from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ..components import emoji_button
from ..db import Gender, StaffMemberRemindersSetting
from ..exceptions import StaffMemberNotFound
from ..registration import ConfirmModal, LaunchButton
from ..utils import emoji_given_pronouns
from ..views import CoordinateBotModal, CoordinateBotView
from .reminders import ReminderDropdown

if TYPE_CHECKING:
    from ..bot import CoordinateBot


class GenderView(CoordinateBotView):
    gender: Gender

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        options=[
            discord.SelectOption(
                value="he",
                label="He/Him",
                description="For the guys...",
                emoji=emoji_given_pronouns("he"),
            ),
            discord.SelectOption(
                value="she",
                label="She/Her",
                description="For the gals...",
                emoji=emoji_given_pronouns("she"),
            ),
            discord.SelectOption(
                value="they",
                label="They/Them",
                description="For the non-binary pals...",
                emoji=emoji_given_pronouns("they"),
            ),
        ],
    )
    async def choose(self, _: discord.Interaction, select: discord.ui.Select):
        if select.values[0] in ("he", "she", "they"):
            genders = {
                "he": Gender.M,
                "she": Gender.F,
                "they": Gender.X,
            }
            self.gender = genders[select.values[0]]
        self.stop()


class StaffMemberValueUpdateView(CoordinateBotView):
    responded_interaction: discord.Interaction | None

    def __init__(self):
        super().__init__(timeout=None)
        self.choice = None
        self.responded_interaction = None

    @discord.ui.select(
        options=[
            discord.SelectOption(
                value="name",
                label="Name",
                description="I want to change my name.",
                emoji="💬",
            ),
            discord.SelectOption(
                value="pronouns",
                label="Pronouns",
                description="I want to update my pronouns.",
                emoji="👫",
            ),
        ],
    )
    async def choose(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.choice = select.values[0]
        self.responded_interaction = interaction
        self.stop()


class StudentIDModal(CoordinateBotModal):
    student_id = discord.ui.TextInput(
        label="Student ID",
        style=discord.TextStyle.short,
        placeholder="12345678",
        min_length=8,
        max_length=8,
    )
    responded_interaction: discord.Interaction | None

    def __init__(self):
        super().__init__(title="Student ID", timeout=None)
        self.responded_interaction = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.responded_interaction = interaction
        self.stop()


class SimpleNameUpdaterModal(CoordinateBotModal):
    name = discord.ui.TextInput(
        label="New Display Name",
        required=True,
        placeholder="Luke Skywalker",
    )

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(title="Update Name", timeout=None)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        async with self.bot.db_factory() as db:
            staff_member = await db.get_staff_member(member=interaction.user)
        old_name = str(staff_member.name)
        staff_member.name = self.name.value
        async with self.bot.db_factory() as db:
            db.add(staff_member)
            await db.commit()
            await interaction.response.send_message(
                f"Your name has been updated from `{old_name}` to `{self.name.value}`!",
                ephemeral=True,
            )


class StaffMemberProfileView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    @emoji_button(
        emoji="📝",
        label="Register/Re-register",
        custom_id="smprofile:register",
        style=discord.ButtonStyle.green,
    )
    async def register(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        # Show user that getting student ID is not dangerous
        view = CoordinateBotView()
        id_modal = StudentIDModal()
        view.add_item(LaunchButton(self.bot, id_modal))
        await interaction.response.send_message(
            "In order to verify your enrollment, you will be asked for **your student ID**. Please note that **these values are not stored anywhere**, and are only provided to **Canvas** to verify your enrollment. If you do not yet have access to the course Canvas page, you may not be able to authorize yourself yet. If you understand this information and would like to proceed, please use the button below.",
            view=view,
            ephemeral=True,
        )

        # Get the user's student ID
        await id_modal.wait()
        student_id = id_modal.student_id.value
        interaction = (
            id_modal.responded_interaction
            if id_modal.responded_interaction
            else interaction
        )
        await interaction.response.send_message(
            f"{self.bot.loading_emoji} Verifying your enrollment with Canvas...",
            ephemeral=True,
        )

        # Check that the user is in the course
        self.bot.get_cog("RegistrationCog")
        course = self.bot.get_course_info()
        if course.canvas_course_code is None:
            raise RuntimeError("No current semester was found.")

        confirm_modal = ConfirmModal(self.bot)
        users = await self.bot.canvas.find_canvas_users(
            student_id,
        )
        confirm_modal.stop()

        if len(users) != 1:
            return await interaction.edit_original_response(
                content="Sorry, you do not appear to be in the Canvas course.",
            )

        user = users[0]
        enrollments = user.get("enrollments") or []
        teacher = False
        for enrollment in enrollments:
            teacher = teacher or enrollment.get("type") == "TeacherEnrollment"
            if enrollment.get("type") == "StudentEnrollment":
                return await interaction.edit_original_response(
                    content="You do not appear to be a TA/PM in the upcoming Canvas course.",
                )

        # Check if user is already in database
        target_role = self.bot.ta_role if not teacher else self.bot.professor_role
        async with self.bot.db_factory() as db:
            try:
                await db.get_staff_member(id=interaction.user.id)
                # If so, just give them TA role again
                assert isinstance(interaction.user, discord.Member)
                if await self.bot.is_staff(interaction.user):
                    await interaction.edit_original_response(
                        content=f"{interaction.user.mention} - you already have the role and are in the database! I can't help you here!",
                    )
                else:
                    if target_role not in interaction.user.roles:
                        await interaction.user.add_roles(target_role)
                    await interaction.edit_original_response(
                        content=f"Alrighty, {interaction.user.mention}, you should be good to go! Your role has been restored. Have a great semester!",
                    )
            except StaffMemberNotFound:
                # Find desired pronouns of user
                view = GenderView()
                await interaction.edit_original_response(
                    content="What are your desired pronouns?",
                    view=view,
                )
                await view.wait()
                gender = view.gender
                assert gender is not None
                # Add user to database
                assert isinstance(interaction.user, discord.Member)
                professor_role = discord.utils.get(
                    interaction.user.roles,
                    name="Professor",
                )
                await db.add_staff_member(
                    name=user["name"],
                    gender=gender,
                    professor=bool(professor_role) or teacher,
                    member=interaction.user,
                )
                if target_role not in interaction.user.roles:
                    await interaction.user.add_roles(target_role)
                await interaction.edit_original_response(
                    content=f"{interaction.user.mention}: You have been added to the database!",
                    view=None,
                )

    @emoji_button(
        emoji="👤",
        label="Update Profile",
        custom_id="smprofile:update",
        style=discord.ButtonStyle.gray,
    )
    async def update(self, interaction: discord.Interaction, _: discord.ui.Button):
        # Find out what the user wants to update (name, pronouns)
        view = StaffMemberValueUpdateView()
        await interaction.response.send_message(
            "What value of your profile would you like to update?",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        choice = view.choice
        interaction = (
            view.responded_interaction if view.responded_interaction else interaction
        )
        assert choice is not None

        if choice == "name":
            await interaction.response.send_modal(SimpleNameUpdaterModal(self.bot))
        elif choice == "pronouns":
            # Find desired pronouns of user
            view = GenderView()
            await interaction.response.send_message(
                "What are your desired pronouns?",
                view=view,
                ephemeral=True,
            )
            await view.wait()
            gender = view.gender
            assert gender is not None

            # Update database
            async with self.bot.db_factory() as db:
                staff_member = await db.get_staff_member(member=interaction.user)
                if staff_member is None:
                    return await interaction.edit_original_response(
                        content="You do not appear to be in the bot database. Are you registered?",
                        view=None,
                    )
                await db.update_staff_member(staff_member, gender=gender)
                await interaction.edit_original_response(
                    content=f"Your gender has been updated to `{staff_member.pronouns}`!",
                    view=None,
                )

    @emoji_button(
        emoji="⏰",
        label="Change Reminders",
        style=discord.ButtonStyle.gray,
        custom_id="smprofile:reminders",
    )
    async def change_reminders(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        view = CoordinateBotView()
        view.add_item(ReminderDropdown(self.bot))
        current_setting = {
            StaffMemberRemindersSetting.ALWAYS: "Always",
            StaffMemberRemindersSetting.ONLY_AWAY: "Only Away",
            StaffMemberRemindersSetting.ONLY_OFFLINE: "Only Offline",
            StaffMemberRemindersSetting.NEVER: "Never",
        }
        async with self.bot.db_factory() as db:
            staff_doc = await db.get_staff_member(id=interaction.user.id)
            await interaction.response.send_message(
                f"Your current status is set to: {current_setting[staff_doc.reminders]}",
                view=view,
                ephemeral=True,
            )


class OfficeHoursStaffCog(commands.Cog):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot


async def setup(bot: CoordinateBot):
    await bot.add_cog(OfficeHoursStaffCog(bot))

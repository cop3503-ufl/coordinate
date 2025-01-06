from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from .. import checks
from ..components import EmojiEmbed
from ..db import StaffMember
from ..env import CANVAS_URL
from ..semesters import semester_given_date
from ..utils import emoji_header
from ..views import Confirm, CoordinateBotModal, CoordinateBotView

if TYPE_CHECKING:
    from ..bot import CoordinateBot


class OfficeHoursStaffMemberQueueDropdown(discord.ui.Select):
    """
    Select containing options of individual staff mmebers hosting office hours
    right now.
    """

    def __init__(
        self,
        bot: CoordinateBot,
        live_staff: list[StaffMember],
    ):
        self.bot = bot
        self.staff = set(live_staff)
        assembled_options = []
        for staff in self.staff:
            assembled_options.append(
                discord.SelectOption(label=staff.name, emoji=staff.emoji),
            )
        super().__init__(
            placeholder="Select which staff member(s)...",
            min_values=1,
            max_values=len(assembled_options),
            options=assembled_options,
        )

    @checks.is_student
    async def callback(self, interaction: discord.Interaction):  # type: ignore
        assert isinstance(interaction.user, discord.Member)
        prefs = [staff for staff in self.staff if staff.name in self.values]
        await interaction.response.send_modal(PurposeModal(self.bot, prefs))


class PurposeModal(CoordinateBotModal):

    name = discord.ui.TextInput(
        label="What do you want help with?",
        placeholder="ex: When attempting to write TGA files, the output is consistently a black square...",
        min_length=30,
        max_length=300,
        style=discord.TextStyle.long,
    )
    tried = discord.ui.TextInput(
        label="What have you tried so far?",
        placeholder="ex: I tried different input images, using unsigned integers, and debugging the pixels...",
        min_length=40,
        max_length=400,
        style=discord.TextStyle.long,
    )

    def __init__(self, bot: CoordinateBot, preferences: list[StaffMember]):
        self.bot = bot
        self.preferences = preferences
        super().__init__(title="What do you need help with?")

    async def on_submit(self, interaction: discord.Interaction):
        #########################
        # User-supported interaction: interaction.user might be a discord.User
        #########################

        # Give the student permission to see join-queue
        role = self.bot.oh_queue_role
        member = await self.bot.get_member(interaction.user.id)
        if role not in member.roles:
            await member.add_roles(role)

        cog = self.bot.office_hours_cog
        cog.queue.set_student_metadata(
            member,
            self.preferences,
            self.name.value,
            self.tried.value,
        )

        if member.voice and member.voice.channel == self.bot.waiting_channel:
            await interaction.response.send_message(
                "Thanks for updating your request!",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Thanks for submitting your request! You now have permission to join the queue. Please click the {self.bot.waiting_channel.mention} channel to join the queue.",
                ephemeral=True,
            )


class OfficeHoursJoinQueueView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot, *, live: bool):
        self.bot = bot
        super().__init__(timeout=None)
        if not live:
            if isinstance(self.children[0], discord.ui.Button):
                self.children[0].style = discord.ButtonStyle.gray
            if isinstance(self.children[1], discord.ui.Button):
                self.children[1].disabled = True
                self.children[1].style = discord.ButtonStyle.gray

    def _notes_embed(self) -> discord.Embed:
        embed = EmojiEmbed(
            title="Important Notes about Office Hours",
            color=discord.Color.dark_red(),
            description="Welcome to office hours! We hope that this will be a productive and helpful experience for you. Please read over the following important notes before proceeding.",
        )
        embed.add_field(
            emoji="1️⃣",
            name="Active Queueing",
            value="You must be active while waiting in the queue. Every 60 minutes, the bot will private message you asking you to re-fill out the form expressing your interest in office hours. It is imperative that you respond within 15 minutes to retain your position in the queue. Consider setting a personal reminder as a precaution. Please be aware that failure to respond will result in your removal from the queue, and we are unable to hold your spot in such cases.",
        )
        embed.add_field(
            emoji="2️⃣",
            name="Comprehensive Form Submission",
            value="We ask that you complete the forthcoming form with as much detail and accuracy as possible. This enables us to assist you more effectively. Please note that our staff may decline assistance for submissions which are insufficiently detailed, allowing them to prioritize students who provide more comprehensive information about their queries.",
        )
        embed.add_field(
            emoji="3️⃣",
            name="Availability",
            value="There is no promise that you will be seen. During periods of high activity, we might not be able to see everyone. We will do our best to help as many students as possible, but we cannot guarantee that you will be seen.",
        )
        embed.add_field(
            emoji="4️⃣",
            name="Feedback",
            value="If you have any feedback, please communicate that with us so we can better assist you.",
        )
        embed.add_field(
            emoji="5️⃣",
            name="Courtesy and Understanding",
            value="Please be respectful and patient. Staff members are here to help you, but they are also juggling several responsibilities and often times, a long queue. Please be patient and respectful of their time and effort.",
        )
        return embed

    @discord.ui.button(
        label="Join OH: First Available",
        style=discord.ButtonStyle.red,
        custom_id="join_queue:next",
    )
    @checks.is_student  # type: ignore
    async def next_available(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        confirm = Confirm(author=interaction.user, defer_interaction=False)
        await interaction.response.send_message(
            "Please read over the following important notes about office hours before proceeding.",
            ephemeral=True,
            view=confirm,
            embed=self._notes_embed(),
        )
        await confirm.wait()
        if confirm.value and confirm.interaction:
            await confirm.interaction.response.send_modal(PurposeModal(self.bot, []))

    @discord.ui.button(
        label="Join OH: Specific Staff Member(s)",
        style=discord.ButtonStyle.red,
        custom_id="join_queue:specific",
    )
    @checks.is_student  # type: ignore
    async def specific(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        async with self.bot.db_factory() as db:
            live_now = await db.live_timeslots()
            if not live_now:
                return await interaction.response.send_message(
                    "There are currently no staff members hosting office hours right now. Please use the First Available button to wait for the next available staff member.",
                    ephemeral=True,
                )

            # Give the student option to select their prefs, the dropdown will
            # then forward their request to the modal
            staff_now = [timeslot.staff for timeslot in live_now]

        view = CoordinateBotView()
        view.add_item(OfficeHoursStaffMemberQueueDropdown(self.bot, staff_now))
        confirm = Confirm(author=interaction.user)
        await interaction.response.send_message(
            "Please read over the following important notes about office hours before proceeding.",
            ephemeral=True,
            view=confirm,
            embed=self._notes_embed(),
        )
        await confirm.wait()
        if confirm.value:
            await interaction.edit_original_response(
                content="Please choose which staff member(s) you would like to work with.",
                view=view,
                embed=None,
            )


class OfficeHoursSchedule(commands.Cog):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot

    async def update_help_message(self):
        schedule_embed = discord.Embed(
            title="Office Hours Schedule",
            description="Below is the schedule of upcoming office hours. For the entire weekly schedule, please see Canvas.",
            color=discord.Color.light_gray(),
        )
        async with self.bot.db_factory() as db:
            now = datetime.datetime.now().astimezone()
            end_of_day = now.replace(hour=23, minute=59, second=59)
            current_entries = list(await db.live_timeslots())
            on_break_entries = list(await db.breaking_timeslots())
            current_entries = [e for e in current_entries if e not in on_break_entries]
            later_today_entries = list(await db.timeslots_during(now, end_of_day))
            tomorrow_entries = list(
                await db.timeslots_during(
                    end_of_day,
                    end_of_day + datetime.timedelta(days=1),
                ),
            )
            two_days_entries = list(
                await db.timeslots_during(
                    end_of_day + datetime.timedelta(days=1),
                    end_of_day + datetime.timedelta(days=2),
                ),
            )

            # Sort entries based on start date
            currently_live = current_entries.copy()

            res = []
            for item in current_entries[:10]:
                res.append(item.schedule_formatted)
            if len(current_entries) > 10:
                res.append("_More entries not listed for space..._")
            current_entries = res

            res = []
            for item in on_break_entries[:10]:
                res.append(item.schedule_formatted)
            on_break_entries = res

            res = []
            for item in later_today_entries[:10]:
                res.append(item.schedule_formatted)
            if len(later_today_entries) > 10:
                res.append("_More entries not listed for space..._")
            later_today_entries = res

            res = []
            for item in tomorrow_entries[:10]:
                res.append(item.schedule_formatted)
            if len(tomorrow_entries) > 10:
                res.append("_More entries not listed for space..._")
            tomorrow_entries = res

            res = []
            for item in two_days_entries[:10]:
                res.append(item.schedule_formatted)
            if len(two_days_entries) > 10:
                res.append("_More entries not listed for space..._")

            two_days_entries = res
            current_str = "\n".join(current_entries)
            on_break_str = "\n".join(on_break_entries)
            later_today_str = "\n".join(later_today_entries)
            tomorrow_str = "\n".join(tomorrow_entries)
            two_days_str = "\n".join(two_days_entries)

        # Break feature
        current_semester = semester_given_date(
            datetime.datetime.now(),
            next_semester=True,
        )

        later_today_val = (
            "_No more office hours are being hosted for the rest of today._"
        )
        if current_semester is not None and any(
            b[0] <= datetime.date.today() <= b[1] for b in current_semester.breaks
        ):
            later_today_val = "🏝️ _No more office hours are being held today because of a school-recognized holiday!_"
        if later_today_str.strip():
            later_today_val = later_today_str.strip()

        schedule_embed.add_field(
            name=emoji_header(
                ":calendar_spiral:",
                f"Later Today ({datetime.datetime.now().strftime('%A, %B %-d')})",
            ),
            value=later_today_val,
            inline=False,
        )

        tomorrow_val = "_No office hours are being held tomorrow._"
        if current_semester is not None and any(
            b[0] <= datetime.date.today() + datetime.timedelta(days=1) <= b[1]
            for b in current_semester.breaks
        ):
            tomorrow_val = "🏝️ _No office hours are being held tomorrow because of a school-recognized holiday!_"
        if tomorrow_str.strip():
            tomorrow_val = tomorrow_str.strip()

        schedule_embed.add_field(
            name=emoji_header(
                ":calendar_spiral:",
                f"Tomorrow ({(datetime.datetime.now() + datetime.timedelta(days = 1)).strftime('%A, %B %-d')})",
            ),
            value=tomorrow_val,
            inline=False,
        )

        two_days_val = "_No office hours are being held in two days._"
        if current_semester is not None and any(
            b[0] <= (datetime.date.today() + datetime.timedelta(days=2)) <= b[1]
            for b in current_semester.breaks
        ):
            two_days_val = "🏝️ _No office hours are being held in two days because of a school-recognized holiday!_"
        if two_days_str.strip():
            two_days_val = two_days_str.strip()

        schedule_embed.add_field(
            name=emoji_header(
                ":calendar_spiral:",
                f"In 2 Days ({(datetime.datetime.now() + datetime.timedelta(days = 2)).strftime('%A, %B %-d')})",
            ),
            value=two_days_val,
            inline=False,
        )

        live_embed = discord.Embed(
            title="Join Office Hours",
            description="Below lists who is currently hosting office hours. These are trained staff members who are here to help you with any question you might have.\n\n**To join the office hours queue, please use the buttons below.** You will be helped in the order that you joined in, indicated by a number next to your name.\n\nIf you would like help from a specific staff member, please feel free to select the staff member from the following list. You will then be placed into the queue for that specific staff member. Please note that queueing for a specific staff member will not accelerate your position in the wait list.",
            color=(
                discord.Color.brand_red()
                if current_str
                else discord.Color.lighter_gray()
            ),
        )
        allotted = self.bot.office_hours_cog.time_control.allotted_time()
        if current_entries:
            live_embed.add_field(
                name=emoji_header("⏳", "Time per Student Limit"),
                value=f"_In times of high activity, the amount of time given to each student is limited in order to ensure all students can equally access office hours. If you are asked to wrap up your session early, please be respectful and mindful that all other students want to access the same resource. This value will adjust as rooms open and close and the room grows and shrinks._\n* **Current limit:** {allotted.total_seconds() / 60:.0f} minutes per student",
                inline=False,
            )
        emoji = self.bot.red_button_emoji if current_str else self.bot.gray_button_emoji
        live_embed.add_field(
            name=emoji_header(emoji, "Currently Live"),
            value=current_str
            or "_No staff members are currently hosting office hours._",
            inline=False,
        )
        if on_break_entries:
            live_embed.add_field(
                name=emoji_header(
                    f"{self.bot.gray_button_emoji}",
                    "Currently On Break",
                ),
                value=on_break_str,
                inline=False,
            )
        channel = self.bot.office_hours_help_ch
        oldest_two = [
            message async for message in channel.history(limit=2, oldest_first=True)
        ]
        schedule_view = CoordinateBotView()
        course_info = self.bot.get_course_info()
        schedule_view.add_item(
            discord.ui.Button(
                label="Full Weekly Schedule",
                url=f"{CANVAS_URL}/courses/{course_info.canvas_course_code}/pages/office-hours-schedule",
            ),
        )
        live_in_person = bool([c for c in currently_live if not c.room])
        if len(oldest_two) < 2:
            for msg in oldest_two:
                await msg.delete()
            await channel.send(embed=schedule_embed, view=schedule_view)
            await channel.send(
                embed=live_embed,
                view=OfficeHoursJoinQueueView(self.bot, live=live_in_person),
            )
        else:
            if oldest_two[0].embeds[0] != schedule_embed:
                await oldest_two[0].edit(embed=schedule_embed, view=schedule_view)
            if (
                live_in_person
                and (
                    live_embed != oldest_two[1].embeds[0]
                    or not oldest_two[1].components
                )
            ) or live_embed != oldest_two[1].embeds[0]:
                await oldest_two[1].edit(
                    embed=live_embed,
                    view=OfficeHoursJoinQueueView(self.bot, live=live_in_person),
                )


async def setup(bot: CoordinateBot):
    await bot.add_cog(OfficeHoursSchedule(bot))

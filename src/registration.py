from __future__ import annotations

import asyncio
import datetime
import logging
import traceback
from typing import TYPE_CHECKING

import discord
from discord.app_commands import NoPrivateMessage
from discord.ext import commands

from .exceptions import StudentsOnly
from .semesters import Semester, semester_given_date
from .views import CoordinateBotView

if TYPE_CHECKING:
    from .bot import CoordinateBot
    from .canvas import Enrollment

logger = logging.getLogger(__name__)


class ConfirmModal(discord.ui.Modal):
    student_sys_id = discord.ui.TextInput(
        label="Student ID",
        placeholder="12345678",
        required=True,
        min_length=1,
        max_length=8,
    )
    name = discord.ui.TextInput(
        label="Name",
        placeholder="Your Name",
        required=True,
        min_length=1,
    )
    lock = asyncio.Lock()

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(title="Confirm Enrollment")

    def roles_given_enrollments(
        self,
        semester: Semester,
        enrollments: list[Enrollment],
    ) -> list[discord.Role]:
        res = []
        current_semester = semester_given_date(
            datetime.datetime.now(),
            next_semester=True,
        )
        assert isinstance(current_semester, Semester)
        semester_is_prev = semester.end < datetime.date.today()
        semester_is_current = current_semester == semester
        course = self.bot.get_course_info()
        if course is None or course.canvas_course_code is None:
            raise RuntimeError("semester config error")
        season, year = current_semester.name.split(" ")
        for enrollment in enrollments:
            if (
                enrollment.get("type")
                in ["TaEnrollment", "DesignerEnrollment", "ObserverEnrollment"]
                and semester_is_current
            ):
                res.append(self.bot.ta_role)
            elif (
                enrollment.get("type")
                in ["TaEnrollment", "DesignerEnrollment", "ObserverEnrollment"]
                and semester_is_prev
            ):
                desired_role = discord.utils.get(
                    self.bot.active_guild.roles,
                    name=f"Emeritus TA/PM ({season} '{year[2:]})",
                )
                if desired_role is not None:
                    res.append(desired_role)
            elif enrollment.get("type") == "StudentEnrollment" and semester_is_current:
                res.append(self.bot.student_role)
            elif enrollment.get("type") == "StudentEnrollment" and semester_is_prev:
                desired_role = discord.utils.get(
                    self.bot.active_guild.roles,
                    name=f"Alumni ({season} '{year[2:]})",
                )
                if desired_role is not None:
                    res.append(desired_role)
        return res

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(
            f"{interaction.user} ({interaction.user.id}) is beginning verification process for server entry.",
        )
        await interaction.response.send_message(
            "<a:loading:1060336216194691092> Attempting to confirm enrollment...",
            ephemeral=True,
        )

        # Check if TA or student in current semester
        course = self.bot.get_course_info()
        if course is None or course.canvas_course_code is None:
            raise RuntimeError("No current semester was found.")

        users = await self.bot.canvas.find_canvas_users(
            self.student_sys_id.value,
        )
        if len(users) > 1:
            await interaction.edit_original_response(
                content="Uh oh! Two or more users were found with that ID. Please contact a bot developer about this situation, or try again with correct information.",
            )
            return
        elif len(users) == 0:
            await interaction.edit_original_response(
                content=f"No user was found with the student ID `{self.student_sys_id.value}`. This may be because you have not yet been given Canvas access to the course. If you believe this is an error, please contact a bot developer or staff member.",
            )
            return

        enrolled = self.bot.canvas.verify_enrollment(
            users,
            self.student_sys_id.value,
            self.name.value,
        )
        if isinstance(interaction.user, discord.User):
            raise NoPrivateMessage

        semester = semester_given_date(datetime.datetime.now(), next_semester=True)
        assert isinstance(semester, Semester)
        if enrolled and users[0]["enrollments"] is not None:
            enrollments = users[0]["enrollments"]
            if enrollments:
                enrollment = enrollments[0]
                sis_id = enrollment.get("sis_section_id")
                if sis_id:
                    section_id = sis_id[-5:]
                    roles = filter(
                        lambda r: r.name.startswith(section_id),
                        self.bot.active_guild.roles,
                    )
                    await interaction.user.add_roles(*roles)
            roles = self.roles_given_enrollments(semester, users[0]["enrollments"])
            await interaction.user.edit(nick=self.name.value.title(), roles=roles)
            await interaction.edit_original_response(
                content=f"Welcome to the server, {self.name.value.split(' ')[0]}!",
            )
        else:
            await interaction.edit_original_response(
                content=f"Sorry, I don't see a `{self.name.value.title()}` with the student ID `{self.student_sys_id.value}` in the course. Please ensure that you didn't make any spelling mistakes!\n\nPlease note that your name must also match your name shown in Canvas:\n\t - Omitting some parts of your name is acceptable (for example, `Laura Cruz` is acceptable if `Laura Cruz Castro` is shown in Canvas)\n\t - Misspelling any part of your name is not acceptable (for example, `Rob Gronk` is not acceptable if `Robert Gronkowski` is listed in Canvas).\n\t - If you would like to request a different name to be shown in Discord than your name listed in Canvas for a personal reason, please verify yourself using the name shown in Canvas, and notify a course staff member after you have been admitted to the server.",
            )

    async def on_error(self, interaction, error: Exception):
        await interaction.edit_original_response(content="Oops! An error has occurred.")
        traceback.print_tb(error.__traceback__)


class LaunchButton(discord.ui.Button):
    def __init__(self, bot: CoordinateBot, modal: discord.ui.Modal):
        self.bot = bot
        self.modal = modal
        super().__init__(
            style=discord.ButtonStyle.green,
            label="I understand, verify enrollment",
        )

    async def callback(self, interaction: discord.Interaction):
        logger.info(
            f"{interaction.user} ({interaction.user.id}) agreed to student ID verification policy.",
        )
        self.disabled = True
        await interaction.response.send_modal(self.modal)
        if interaction.message:
            await interaction.followup.edit_message(
                interaction.message.id,
                view=self.view,
            )


class RegistrationView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Confirm Course Enrollment",
        style=discord.ButtonStyle.green,
        custom_id="registration_view:confirm",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if await self.bot.is_staff(interaction.user):
            raise StudentsOnly
        else:
            view = CoordinateBotView()
            view.add_item(LaunchButton(self.bot, ConfirmModal(self.bot)))
            await interaction.response.send_message(
                "In order to verify your enrollment, you will be asked for **your student ID and name**. Please note that **these values are not stored anywhere**, and are only provided to **Canvas** to verify your enrollment. If you do not yet have access to the course Canvas page, you may not be able to authorize yourself yet. If you understand this information and would like to proceed, please use the button below.",
                view=view,
                ephemeral=True,
            )


class RegistrationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(RegistrationCog(bot))

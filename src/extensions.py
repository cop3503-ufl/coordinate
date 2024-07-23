from __future__ import annotations

import asyncio
import datetime
import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord.app_commands import NoPrivateMessage
from discord.ext import commands, tasks
from gradescope_api.assignment import GradescopeAssignment

from .canvas import AssignmentLiteOverrides
from .env import QUALTRICS_SURVEY_ID, QUALTRICS_URL
from .qualtrics import CompletionStatus, SurveyResponse
from .views import Confirm, CoordinateBotModal, CoordinateBotView

if TYPE_CHECKING:
    from .bot import CoordinateBot
    from .canvas import User


logger = logging.getLogger(__name__)


# Referenced from: https://stackoverflow.com/a/14822210
def convert_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    unit_index = int(math.floor(math.log(size_bytes, 1024)))
    size_in_unit = math.pow(1024, unit_index)
    size_rounded = round(size_bytes / size_in_unit, 2)
    return f"{size_rounded} {size_units[unit_index]}"


@dataclass
class ExtendableAssignment(ABC):

    name: str

    @abstractmethod
    async def extend(
        self,
        new_due_date: datetime.datetime,
    ):
        raise NotImplementedError

    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def due_at(self) -> datetime.datetime | None:
        raise NotImplementedError

    @property
    def id(self) -> str:
        raise NotImplementedError

    @property
    def due_str(self) -> str:
        if not self.due_at:
            return "No due date."
        due_str = None
        days_until_due = (self.due_at - datetime.datetime.now().astimezone()).days
        hours_until_due = (
            self.due_at - datetime.datetime.now().astimezone()
        ).total_seconds() // 3600
        minutes_until_due = (
            self.due_at - datetime.datetime.now().astimezone()
        ).total_seconds() // 60
        if days_until_due >= 1:
            due_str = f"Due in {days_until_due} days..."
        elif hours_until_due > 1:
            due_str = f"Due in {hours_until_due:.0f} hours..."
        elif minutes_until_due > 1:
            due_str = f"Due in {minutes_until_due:.0f} minutes!"
        else:
            due_str = "Due yesterday!"
        return due_str


@dataclass
class ExtendableCanvasAssignment(ExtendableAssignment):

    _id: str
    assignment: AssignmentLiteOverrides
    bot: CoordinateBot
    student_canvas_id: int

    @classmethod
    def from_assignment_override(
        cls,
        assignment: AssignmentLiteOverrides,
        bot: CoordinateBot,
        student_canvas_id: int,
    ):
        return cls(
            name=assignment["name"],
            _id=assignment["_id"],
            assignment=assignment,
            bot=bot,
            student_canvas_id=student_canvas_id,
        )

    @property
    def due_at(self) -> datetime.datetime | None:
        if self.assignment["dueAt"] is None:
            return None
        due_at = datetime.datetime.fromisoformat(self.assignment["dueAt"])
        for override in self.assignment["assignmentOverrides"]:
            for student in override["students"]:
                due_at_str = override.get("dueAt")
                if (
                    int(student["_id"]) == self.student_canvas_id
                    and due_at_str is not None
                ):
                    due_at = datetime.datetime.fromisoformat(due_at_str)
        return due_at

    @property
    def provider_name(self) -> str:
        return "Canvas"

    @property
    def id(self) -> str:
        return self._id

    async def extend(
        self,
        new_due_date: datetime.datetime,
    ):
        existing_override_id: str | None = None
        for override in self.assignment["assignmentOverrides"]:
            override_ids = [s["_id"] for s in override["students"]]
            if str(self.student_canvas_id) in override_ids:
                existing_override_id = override["_id"]
                logger.info(
                    f"Found existing override for {self.assignment['name']}, replacing...",
                )
                break
        course = await self.bot.canvas.get_course()
        logger.info(
            f"Extending Canvas assignment '{self.assignment['name']}' to {new_due_date} for canvas_id={self.student_canvas_id}.",
        )
        await self.bot.canvas.extend_due_date(
            course["id"],
            self.assignment["_id"],
            self.student_canvas_id,
            new_due_date,
            existing_override_id=existing_override_id,
        )


@dataclass
class ExtendableGradescopeAssignment(ExtendableAssignment):

    assignment: GradescopeAssignment
    student_email: str | None

    @classmethod
    def from_assignment(
        cls,
        assignment: GradescopeAssignment,
        student_email: str | None,
    ):
        return cls(
            name=assignment.title or "?",
            assignment=assignment,
            student_email=student_email,
        )

    @property
    def provider_name(self) -> str:
        return "Gradescope"

    @property
    def due_at(self) -> datetime.datetime | None:
        return self.assignment.due_date

    @property
    def id(self) -> str:
        return self.assignment.assignment_id

    async def extend(
        self,
        new_due_date: datetime.datetime,
    ):
        if not self.assignment.due_date:
            logger.info(
                f"Assignment {self.assignment.title} has no due date, skipping extension.",
            )
            raise RuntimeError("Attempted to extend an assignment with no due date.")
        logger.info(
            f"Extending Gradescope assignment '{self.name}' to {new_due_date} for {self.student_email}.",
        )
        amount = new_due_date - self.assignment.due_date
        if not self.student_email:
            raise ValueError("No student email provided.")
        await self.assignment.apply_extension(self.student_email, amount)


class ExtensionReasonModal(CoordinateBotModal, ABC):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder=None,  # filled in in the constructor
        min_length=1,
        max_length=1000,
        style=discord.TextStyle.long,
    )

    def __init__(
        self,
        bot: CoordinateBot,
        response: SurveyResponse,
        original_message: discord.Message,
        verb_present: str,
        verb: str,
        button_color: discord.ButtonStyle,
        completion_status: CompletionStatus,
        date_change_msg: str,
    ):
        super().__init__(title=f"{verb_present} Extension for {response.name}")
        self.bot = bot
        self.response = response
        self.original_message = original_message
        self.verb_present = verb_present
        self.verb = verb
        self.button_color = button_color
        self.completion_status = completion_status
        self.date_change_msg = date_change_msg
        self.reason.placeholder = (
            f"Enter a reason for {verb_present.lower()} the extension request."
        )

    @abstractmethod
    async def confirm(
        self,
        interaction: discord.Interaction,
        response: SurveyResponse,
        reason: str,
    ) -> bool | None:
        raise NotImplementedError

    async def on_submit(self, interaction: discord.Interaction):
        # Send a message to the student
        course = await self.bot.canvas.get_course()
        users = await self.bot.canvas.get_users(course, self.response.student_sys_id)
        # if confirmation cancel, return early
        if not await self.confirm(
            interaction,
            self.response,
            self.reason.value,
        ):
            return

        # Make the button disabled
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label=f"Extension {self.verb}",
                style=self.button_color,
                disabled=True,
            ),
        )
        await self.original_message.edit(view=view)
        # Create a thread with the reason
        thread = await self.bot.message_thread(
            self.original_message,
            f"Extension {self.verb}",
        )
        await thread.send(
            f"**{interaction.user.display_name}** has {self.verb.lower()} the extension request for the following reason:\n\n> {self.reason.value}",
        )

        await self.bot.qualtrics.update_completion_status(
            self.response.id,
            self.completion_status,
        )

        # if interaction not yet responded to, use response.send_message
        # if interaction already responded to, need to use followup.send instead
        response_func = interaction.response.send_message
        if interaction.response.is_done():
            response_func = interaction.followup.send

        # Send notification
        if users:
            await self.bot.canvas.send_message(
                users[0]["id"],
                f"Extension {self.verb}",
                f"Your recent extension request has been {self.verb.lower()} by {interaction.user.display_name} for the following reason:\n\n{self.reason.value}\n\n{self.date_change_msg}",
            )
            await response_func(
                "The student has been notified.",
                ephemeral=True,
            )
        else:
            await response_func(
                "The student could not be notified because no student could be found in Canvas matching that ID.",
                ephemeral=True,
            )


class ExtensionDeclineModal(ExtensionReasonModal):
    def __init__(
        self,
        bot: CoordinateBot,
        response: SurveyResponse,
        original_message: discord.Message,
    ):
        super().__init__(
            bot,
            response,
            original_message,
            "Declining",
            "Declined",
            discord.ButtonStyle.danger,
            CompletionStatus.DECLINED,
            "At this time, your assignment due dates remain unchanged.",
        )

    # No confirmation needed for a decline, just return true
    async def confirm(
        self,
        interaction: discord.Interaction,
        response: SurveyResponse,
        reason: str,
    ) -> bool | None:
        return True


class ExtensionApproveModal(ExtensionReasonModal):
    def __init__(
        self,
        bot: CoordinateBot,
        response: SurveyResponse,
        original_message: discord.Message,
    ):
        combined_date = datetime.datetime.combine(
            response.date,
            datetime.time(23, 59),
        )
        super().__init__(
            bot,
            response,
            original_message,
            "Approving",
            "Approved",
            discord.ButtonStyle.success,
            CompletionStatus.APPROVED,
            f"The due date has been updated to {combined_date.strftime('%A %B %d, %Y %I:%M%p')}.",
        )

    async def get_relevant_assignments(
        self,
        response: SurveyResponse,
    ) -> Sequence[ExtendableAssignment]:
        course_info = self.bot.get_course_info()
        canvas_assignments = await self.bot.canvas.get_assignments_with_overrides(
            course_info.canvas_course_code,
        )
        course = await self.bot.canvas.get_course()
        canvas_student = await self.bot.canvas.get_users(course, response.student_sys_id)
        assignments: list[ExtendableAssignment] = [
            ExtendableCanvasAssignment.from_assignment_override(
                a,
                self.bot,
                canvas_student[0]["id"],
            )
            for a in canvas_assignments
        ]
        gradescope_assignments = await self.bot.gradescope.get_assignments()
        if response.email:
            assignments.extend(
                [
                    ExtendableGradescopeAssignment.from_assignment(
                        a,
                        response.email.lower(),
                    )
                    for a in gradescope_assignments
                ],
            )
        return [
            full
            for full in assignments
            if any(partial in full.name for partial in response.assignments)
        ][:25]

    def confirm_embed(
        self,
        response: SurveyResponse,
        assignment_list: Sequence[ExtendableAssignment],
        approval_reason: str,
    ) -> discord.Embed:
        # Confirm
        embed = discord.Embed(
            title="Confirm Extension",
            description="**Please review this extension carefully to avoid giving students too much/too little time on their assignments.**",
            color=discord.Color.dark_red(),
        )
        embed.add_field(
            name="Student",
            value=response.name,
        )
        combined_date = datetime.datetime.combine(response.date, datetime.time(23, 59))
        embed.add_field(
            name="New Due Date",
            value=f"{discord.utils.format_dt(combined_date, 'F')} ({discord.utils.format_dt(combined_date, 'R')})",
            inline=False,
        )
        embed.add_field(
            name="Assignments to Extend",
            value="\n".join(
                [f"* {a.name} (from {a.provider_name})" for a in assignment_list],
            ),
            inline=False,
        )
        embed.add_field(
            name="Approval Reason",
            value=approval_reason,
            inline=False,
        )
        return embed

    async def confirm(
        self,
        interaction: discord.Interaction,
        response: SurveyResponse,
        reason: str,
    ) -> bool | None:
        await interaction.response.send_message(
            f"{self.bot.loading_emoji} Finding assignments...",
            ephemeral=True,
        )
        # 1. Get a list of all assignments that can be extended for this student
        selected_assignments = await self.get_relevant_assignments(response)

        confirm = Confirm(interaction.user)
        await interaction.edit_original_response(
            content=None,
            embed=self.confirm_embed(response, selected_assignments, self.reason.value),
            view=confirm,
        )
        confirm.message = await interaction.original_response()
        combined_date = datetime.datetime.combine(
            response.date,
            datetime.time(23, 59),
        ).astimezone()
        await confirm.wait()

        if not confirm.value:
            logger.info(f"Extension for {response.name} cancelled.")
            await interaction.edit_original_response(
                content="Extension cancelled.",
            )
        else:
            logger.info(
                f"Approving extension for {response.name} with reason: {reason}",
            )
            # 1. Get the assignment ID from the interaction
            # 2. Get the assignment from the Canvas API
            # 3. Update the due date
            for assignment in selected_assignments:
                # Check for an override
                await assignment.extend(combined_date)
        return confirm.value


class ExtensionRequestView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Grant Extension(s)",
        style=discord.ButtonStyle.success,
        custom_id="extensions:extend",
    )
    async def extend(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.message:
            raise NoPrivateMessage

        response = await self.get_response(interaction.message)
        await interaction.response.send_modal(
            ExtensionApproveModal(
                self.bot,
                response,
                interaction.message,
            ),
        )

    async def get_response(self, message: discord.Message) -> SurveyResponse:
        # Get response
        fields = message.embeds[0].fields
        response_id = None
        for field in fields:
            if field.name == "Response ID":
                response_id = field.value
        assert isinstance(response_id, str)
        return await self.bot.qualtrics.get_response(response_id)

    @discord.ui.button(
        label="Decline",
        style=discord.ButtonStyle.secondary,
        custom_id="extensions:decline",
    )
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        assert isinstance(interaction.message, discord.Message)
        response = await self.get_response(interaction.message)
        await interaction.response.send_modal(
            ExtensionDeclineModal(self.bot, response, interaction.message),
        )


class ExtensionsCog(commands.Cog):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.check_for_responses.start()

    @commands.command()
    async def prepextensions(self, ctx):
        embed = discord.Embed(
            title="Request an Assignment Extension",
            color=discord.Color.brand_red(),
            description="___Please, do not email your professor or peer mentor requesting an extension.___\n\nIf you would like to request an **assignment extension** for any assignment/quiz, please use **the button below**.\n\nTo request an extension, you must:\n* Show proof of supporting documentation\n* Submit the extension request before the assignment due date\n\nPlease note that extensions are only given in cases where the supporting documentation is a valid reason to provide an extension. For more information, please see the policy page of your educational institution.",
        )
        embed.set_image(
            url="https://media.discordapp.net/attachments/1091422466448040047/1165486956470353930/image.png?ex=65470750&is=65349250&hm=a82f2c2a39c17ffee94171f6d20f726c12ac3c2a2a5425821636da65596f407c&=&width=1852&height=1058",
        )
        url_view = CoordinateBotView()
        url_view.add_item(
            discord.ui.Button(
                label="Request an Extension",
                url=f"{QUALTRICS_URL}/jfe/form/{QUALTRICS_SURVEY_ID}",
            ),
        )
        await ctx.send(embed=embed, view=url_view)
        await ctx.message.delete()

    def verify_enrollment(self, users: list[User], student_sys_id: str, name: str):
        """
        Verify that the given student ID and name match.
        """
        return self.bot.canvas.verify_enrollment(users, student_sys_id, name)

    async def request_embed(self, response: SurveyResponse) -> discord.Embed:
        embed = discord.Embed(
            title="New Extension Request",
            description="The following content was submitted by the student.",
            color=discord.Color.brand_green(),
        )
        embed.add_field(name="Request Text", value=response.reason, inline=False)
        embed.add_field(
            name="Assignments Requested",
            value="\n".join([f"* {a}" for a in response.assignments]),
            inline=False,
        )
        combined_date = datetime.datetime.combine(response.date, datetime.time(23, 59))
        embed.add_field(
            name="Requested Extension Date",
            value=f"{discord.utils.format_dt(combined_date, 'D')} ({discord.utils.format_dt(combined_date, 'R')})",
            inline=False,
        )
        embed.add_field(
            name="File Attachment (Auth Required to View)",
            value=(
                f"* [{response.file.name}]({QUALTRICS_URL}/Q/File.php?F={response.file.id}) ({convert_size(response.file.filesize)}, {response.file.mime_type})"
                if response.file
                else "None"
            ),
            inline=False,
        )
        embed.add_field(name="Response ID", value=response.id, inline=True)
        thumbnail = await self.bot.canvas.get_thumbnail(response.student_sys_id)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        return embed

    @tasks.loop(minutes=3)
    async def check_for_responses(self):
        """
        Check for new responses to the Qualtrics survey that need responding to.
        """
        # Wait for bot to start
        await self.bot.wait_until_ready()

        try:
            responses = await asyncio.wait_for(
                self.bot.qualtrics.get_responses(),
                timeout=15,
            )
        except TimeoutError:
            return

        # Perform simple checks on the responses
        # Check 1: Ensure that student's ID and name match
        for resp in responses:
            users = await self.bot.canvas.find_canvas_users(resp.student_sys_id)
            if not self.verify_enrollment(users, resp.student_sys_id, resp.name) and users:
                logger.info(
                    f"Denying response {resp.id} by {resp.name} because your student ID and name do not match.",
                )
                await self.bot.canvas.send_message(
                    users[0]["id"],
                    "Response Denied",
                    "Unfortunately, your response has been denied because your name and student ID do not match. Please try submitting again.",
                )
                await self.bot.qualtrics.update_completion_status(
                    resp.id,
                    CompletionStatus.DECLINED,
                )
                continue

            # Send new view to professor
            embed = await self.request_embed(resp)
            view = ExtensionRequestView(self.bot)
            await self.bot.student_requests_ch.send(
                f"**{resp.name}** has requested an assignment extension. Please review using the buttons below.",
                embed=embed,
                view=view,
            )
            await self.bot.qualtrics.update_completion_status(
                resp.id,
                CompletionStatus.WAITING_FOR_PROF,
            )


async def setup(bot: CoordinateBot):
    await bot.add_cog(ExtensionsCog(bot))

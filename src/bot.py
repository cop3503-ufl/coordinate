from __future__ import annotations

import asyncio
import datetime
import logging
import logging.handlers
import re
import traceback
from typing import TYPE_CHECKING

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from rich.logging import RichHandler
from sqlalchemy.ext.asyncio import create_async_engine

from .canvas import Canvas
from .codio import CodioHelper
from .constants import VC_CLOSING_SUFFIX
from .db import Base, DatabaseFactory, StaffMember
from .env import (
    CANVAS_API_TOKEN,
    CANVAS_URL,
    CODIO_CLIENT_ID,
    CODIO_CLIENT_SECRET,
    CODIO_COURSE_ID,
    DISCORD_TOKEN,
    GITHUB_TOKEN,
    GUILD_ID,
    NVIDIA_NGC_TOKEN,
    POSTGRES_URL,
)
from .exceptions import CoordinateBotErrorHandler
from .extensions import ExtensionRequestView
from .github import GitHub
from .gradescope import Gradescope
from .issues import GitHubIssueView
from .llama import Llama
from .office_hours.alerts import CancelOfficeHoursView, OfficeHoursAlerts
from .office_hours.approvals import OHApprovalView
from .office_hours.breaks import BreakOHView
from .office_hours.events import OfficeHoursEventHandler
from .office_hours.feedback import StarView
from .office_hours.main import (
    OfficeHoursCog,
    OfficeHoursRoutineUpdateView,
    OfficeHoursUpdateView,
)
from .office_hours.schedule import OfficeHoursJoinQueueView, OfficeHoursSchedule
from .office_hours.staff import StaffMemberProfileView
from .qualtrics import Qualtrics
from .registration import RegistrationView
from .sections import AssignSectionView
from .semesters import Course, Semester, semester_given_date
from .student import LatePassView
from .tasks import TaskManager

logger = logging.getLogger(__name__)

intents = discord.Intents.all()

if TYPE_CHECKING:
    from discord.types.threads import ThreadArchiveDuration


class CoordinateBotCommandTree(app_commands.CommandTree):
    def __init__(self, client: CoordinateBot):
        super().__init__(client)
        self.handler = CoordinateBotErrorHandler()

    async def on_error(  # type: ignore
        self,
        interaction: discord.Interaction[CoordinateBot],
        error: app_commands.AppCommandError,
    ) -> None:
        await self.handler.handle_interaction_exception(interaction, error)


class CoordinateBot(commands.Bot):
    """
    The main bot class. Manages all functionality, extensions, and commands.
    """

    active_guild: discord.Guild
    ta_role: discord.Role
    professor_role: discord.Role
    admin_role: discord.Role
    bot_role: discord.Role
    student_role: discord.Role
    inperson_role: discord.Role
    unconfirmed_role: discord.Role
    oh_queue_role: discord.Role
    lead_ta_role: discord.Role

    waiting_channel: discord.VoiceChannel

    office_hours_cat: discord.CategoryChannel

    office_hours_help_ch: discord.TextChannel
    office_hours_approvals_ch: discord.TextChannel
    bot_panel_ch: discord.TextChannel
    bot_log_ch: discord.TextChannel
    random_ch: discord.TextChannel
    admin_ch: discord.TextChannel
    staff_ch: discord.TextChannel
    student_requests_ch: discord.TextChannel
    general_channel: discord.TextChannel
    feedback_channel: discord.TextChannel

    question_channels: list[discord.ForumChannel]

    office_hours_alerts_cog: OfficeHoursAlerts
    office_hours_events_cog: OfficeHoursEventHandler
    office_hours_schedule_cog: OfficeHoursSchedule
    office_hours_cog: OfficeHoursCog

    codio: CodioHelper
    canvas: Canvas
    qualtrics: Qualtrics
    tasks: TaskManager
    github: GitHub
    llama: Llama

    db_factory: DatabaseFactory

    session: aiohttp.ClientSession
    _setup: asyncio.Event

    def __init__(self):
        super().__init__(
            command_prefix="!",
            help_command=None,
            intents=intents,
            tree_cls=CoordinateBotCommandTree,
        )
        self.tasks = TaskManager()
        self._setup = asyncio.Event()

    async def get_staff_oh_role(self, staff_member: StaffMember) -> discord.Role:
        """
        Return the OH role for a staff member that allows the student to share their
        screen in office hours. This is needed because Discord implemented a change
        that requires members in a voice channel to have 'Connect' to share their
        screen. This role is added to the student when they join the OH voice channel
        and removed when they leave.
        """
        role_name = f"{staff_member.id} OH"
        staff_role = discord.utils.get(
            self.active_guild.roles,
            name=role_name,
        )
        if staff_role is None:
            staff_role = await self.active_guild.create_role(
                name=role_name,
                reason=f"Create OH student screen sharing assistant role for {staff_member.name}.",
            )
        return staff_role

    def get_course_info(self) -> Course:
        semester = semester_given_date(
            datetime.datetime.now(),
            next_semester=True,
        )
        assert isinstance(semester, Semester)
        course_name = self.active_guild.name[:7]
        return semester.courses[course_name]

    def fetch_roles(self):
        """
        Fetch all roles on startup.
        """
        # First, get the guild
        guild = self.get_guild(GUILD_ID)
        if guild is None:
            raise RuntimeError("Could not find guild.")
        self.active_guild = guild

        bot_role = self.active_guild.self_role
        if bot_role is None:
            raise RuntimeError("Could not find bot role.")
        self.bot_role = bot_role

        # Then the resources
        roles = {
            "ta_role": "TA/PM",
            "lead_ta_role": "Lead TA/PM",
            "professor_role": "Professor",
            "admin_role": "Admin",
            "student_role": "Student",
            "inperson_role": "In-Person",
            "unconfirmed_role": "Unconfirmed Student",
            "oh_queue_role": "Waiting for OH",
        }
        for k, v in roles.items():
            role = discord.utils.get(self.active_guild.roles, name=v)
            if role is None:
                raise RuntimeError(f"Could not find role {v}.")
            setattr(self, k, role)

        current_semester = semester_given_date(
            datetime.datetime.now(),
            next_semester=True,
        )
        assert isinstance(current_semester, Semester)
        prefix, year = (
            current_semester.name[:2].lower(),
            str(current_semester.start.year)[2:],
        )
        channels = {
            "waiting_channel": "join-queue",
            "office_hours_help_ch": "schedule-and-help",
            "office_hours_approvals_ch": "oh-changes-approvals",
            "office_hours_cat": "Office Hours",
            "bot_panel_ch": "bot-panel",
            "bot_log_ch": "bot-log",
            "random_ch": "random",
            "admin_ch": "admin-chat",
            "staff_ch": f"staff-{prefix}{year}",
            "student_requests_ch": "student-requests",
            "general_channel": "general",
            "feedback_channel": "feedback",
        }
        for k, v in channels.items():
            channel = discord.utils.get(self.active_guild.channels, name=v)
            if channel is None:
                raise RuntimeError(f"Could not find channel {v}.")
            setattr(self, k, channel)

        self.question_channels = [
            c for c in self.active_guild.forums if c.name.endswith("questions")
        ]

        self.red_button_emoji = "<a:redbutton:1060627136345538661>"
        self.gray_button_emoji = "<:graybutton:1060778108191526993>"
        self.loading_emoji = "<a:loading:1060336216194691092>"

        cogs = {
            "office_hours_alerts_cog": "OfficeHoursAlerts",
            "office_hours_events_cog": "OfficeHoursEventHandler",
            "office_hours_schedule_cog": "OfficeHoursSchedule",
            "office_hours_cog": "OfficeHoursCog",
        }
        for k, v in cogs.items():
            cog = self.get_cog(v)
            if cog is None:
                raise RuntimeError(f"Could not find cog {v}.")
            setattr(self, k, cog)

        self._setup.set()

    async def is_course_lead(self, user: discord.Member | discord.User) -> bool:
        """
        Helper check to determine if someone is a course lead or not. Uses roles for
        verification.
        """
        if isinstance(user, discord.User):
            member = self.active_guild.get_member(user.id)
            if not member:
                member = await self.active_guild.fetch_member(user.id)
        else:
            member = user

        return (
            self.professor_role in member.roles
            or self.admin_role in member.roles
            or self.lead_ta_role in member.roles
        )

    async def is_staff(self, user: discord.Member | discord.User) -> bool:
        """
        Helper check to determine if a member is staff or not. Uses roles for
        verification.
        """
        if isinstance(user, discord.User):
            member = self.active_guild.get_member(user.id)
            if not member:
                member = await self.active_guild.fetch_member(user.id)
        else:
            member = user

        return self.ta_role in member.roles or self.professor_role in member.roles

    async def staff_doc_from_vc(self, vc: discord.VoiceChannel) -> StaffMember:
        """
        Gets the document for a staff member from their office hours voice channel.
        """
        staff_name = vc.name.replace(VC_CLOSING_SUFFIX, "").strip()
        async with self.db_factory() as db:
            return await db.get_staff_member(name=staff_name)

    async def get_member(self, user_id: int) -> discord.Member:
        """
        Gets a member from the active guild, fetching them if necessary.
        """
        member = self.active_guild.get_member(user_id)
        if not member:
            member = await self.active_guild.fetch_member(user_id)
        return member

    def is_oh_channel(self, voice_channel: discord.VoiceChannel) -> bool:
        """
        Checks if a voice channel is an office hours channel.
        """
        return (
            voice_channel.category_id == self.office_hours_cat.id
            and voice_channel != self.waiting_channel
            and "." not in voice_channel.name
        )

    def staff_member_channel(self, name: str) -> discord.VoiceChannel | None:
        """
        Attempts to retrieve the staff member office hours voice channel if it exists,
        otherwise returns None.
        """
        voice_channel = discord.utils.get(
            self.active_guild.voice_channels,
            name=f"{name}",
        )
        if not voice_channel:
            voice_channel = discord.utils.get(
                self.active_guild.voice_channels,
                name=f"{name} {VC_CLOSING_SUFFIX}",
            )
        if not voice_channel:
            # in-person OH
            first, last = name.split(" ")[:2]
            pattern = rf"{first} {last[0]}\. \([\w+\s]+\)"
            return discord.utils.find(
                lambda vc: re.match(pattern, vc.name),
                self.active_guild.voice_channels,
            )
        return voice_channel

    async def message_thread(
        self,
        message: discord.Message,
        thread_name: str,
        *,
        reason: str | None = None,
        auto_archive_duration: ThreadArchiveDuration = 60,
    ) -> discord.Thread:
        """
        Returns the existing thread for a message or makes one if it does not exist
        already.
        """
        thread = None
        channel = self.get_channel(message.id)
        if isinstance(channel, discord.Thread):
            thread = channel
        else:
            thread = await message.create_thread(
                name=thread_name,
                auto_archive_duration=auto_archive_duration,
                reason=reason,
            )
        return thread

    async def setup_hook(self):
        extensions = (
            "src.admin",
            "src.registration",
            "src.extensions",
            "src.fun",
            "src.office_hours.main",
            "src.office_hours.alerts",
            "src.office_hours.staff",
            "src.office_hours.schedule",
            "src.office_hours.events",
            "src.questions",
            "src.staff",
            "src.student",
            "src.gpt",
        )
        for i, extension in enumerate(extensions):
            try:
                logger.info(f"Loaded extension {i + 1}/{len(extensions)}: {extension}")
                await self.load_extension(extension)
            except commands.ExtensionError:
                logger.warning(f"Failed to load extension: {extension}")
                traceback.print_exc()

        self.add_view(RegistrationView(self))
        self.add_view(ExtensionRequestView(self))
        self.add_view(OfficeHoursUpdateView(self))
        self.add_view(OHApprovalView(self))
        self.add_view(OfficeHoursRoutineUpdateView(self))
        self.add_view(StaffMemberProfileView(self))
        self.add_view(CancelOfficeHoursView(self))
        self.add_view(OfficeHoursJoinQueueView(self, live=False))
        self.add_view(BreakOHView(self))
        self.add_view(LatePassView(self))
        self.add_view(GitHubIssueView(self))
        self.add_view(StarView(self))
        self.add_view(AssignSectionView(self))

        self.session = aiohttp.ClientSession()
        self.canvas = Canvas(CANVAS_URL, CANVAS_API_TOKEN, self.session, self)
        self.gradescope = Gradescope()
        await self.gradescope.setup()
        self.codio = CodioHelper(
            str(CODIO_CLIENT_ID),
            str(CODIO_CLIENT_SECRET),
            str(CODIO_COURSE_ID),
            self.session,
        )
        self.qualtrics = Qualtrics(self.session)
        self.github = GitHub(session=self.session, auth_token=GITHUB_TOKEN)
        self.llama = Llama(bot=self, api_token=NVIDIA_NGC_TOKEN)
        await self.codio.setup()

    async def on_ready(self):
        logger.info(f" --> Logged in as {self.user}!")

        engine = create_async_engine(POSTGRES_URL)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.db_factory = DatabaseFactory(bot=self, engine=engine)
        self.tasks.start()
        self.fetch_roles()

    async def on_message(self, message: discord.Message):
        # Log direct messages
        if not message.guild and message.author != self.user:
            logger.info(f"DM from {message.author}: {message.content}")
        else:
            logger.info(f"Message from {message.author}: {message.content}")

        await self.process_commands(message)

    async def close(self):
        await self.gradescope.shutdown()
        await self.session.close()
        await self.db_factory.close()
        await self.tasks.shutdown()

        # Cancel all tasks
        for task in asyncio.all_tasks():
            task.cancel()

        await super().close()

    async def on_member_join(self, member: discord.Member):
        await member.add_roles(self.unconfirmed_role)

    async def on_error(self, event, *args, **kwargs):
        self.handler = CoordinateBotErrorHandler()
        await self.handler.handle_event_exception(event, self)

    async def on_command_error(self, ctx, error):
        self.handler = CoordinateBotErrorHandler()
        await self.handler.handle_command_exception(ctx, error)

    async def wait_until_ready(self):
        await super().wait_until_ready()
        await self._setup.wait()


bot = CoordinateBot()


async def main():
    KB = 1024
    MB = 1024 * KB
    handler = logging.handlers.RotatingFileHandler(
        filename="log/coordinate-bot.log",
        encoding="utf-8",
        maxBytes=32 * MB,
        backupCount=5,
    )
    discord.utils.setup_logging(handler=handler)

    logger = logging.getLogger()
    logger.addHandler(RichHandler(rich_tracebacks=True))

    try:
        async with bot:
            await bot.start(token=DISCORD_TOKEN)
    except asyncio.CancelledError:
        logger.warning("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())

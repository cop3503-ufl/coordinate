from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from .. import checks
from ..components import EmojiEmbed
from ..views import CoordinateBotView
from .schedule import PurposeModal

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from ..db import StaffMember


logger = logging.getLogger(__name__)


class OfficeHoursStaffMemberQueueDropdown(discord.ui.Select):
    """
    Select containing options of individual staff members hosting office hours
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
        for staff in live_staff:
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


class UpdateExistingMetadataView(CoordinateBotView):

    message: discord.Message

    def __init__(
        self,
        bot: CoordinateBot,
        timeout_min: int,
        specifics: list[StaffMember],
    ):
        self.bot = bot
        self.specifics = specifics
        super().__init__(timeout=timeout_min * 60)

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
                child.label = "Request Expired"
        await self.message.edit(view=self)

    @discord.ui.button(
        label="Update Background Information",
        style=discord.ButtonStyle.danger,
    )
    async def background_info(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        #########################
        # User-supported interaction: interaction.user is a discord.User
        #########################
        await interaction.response.send_modal(PurposeModal(self.bot, self.specifics))


@dataclass
class StudentMetadata:

    EXPIRY_MINUTES = 60

    student: discord.Member
    specifics: list[StaffMember]
    purpose: str
    tried: str
    expiry: datetime.datetime = field(init=False)

    def _future_expiry(self=None) -> datetime.datetime:
        return datetime.datetime.now().astimezone() + datetime.timedelta(
            minutes=self.EXPIRY_MINUTES,
        )

    def __post_init__(self):
        self.expiry = self._future_expiry()

    @property
    def is_expired(self) -> bool:
        return datetime.datetime.now().astimezone() > self.expiry

    def specifics_string(self) -> str:
        if not self.specifics:
            return "First Available"
        return ", ".join(s.mention for s in self.specifics)

    def purpose_string(self) -> str:
        return (
            self.purpose
            or "_It appears the student's purpose was lost. This can happen if the bot was restarted."
        )

    def tried_string(self) -> str:
        return (
            self.tried
            or "_It appears the student's tried statement was lost. This can happen if the bot was restarted."
        )

    def entry_embed(self, *, moving_to: StaffMember, with_delay: bool) -> discord.Embed:
        moving_at = datetime.datetime.now().astimezone() + datetime.timedelta(
            seconds=moving_to.autoaccept_delay,
        )
        embed = EmojiEmbed(
            title="Details about your upcoming student!",
            description=f"Hi {moving_to.first_name}! Here are some details about the student you're about to meet with. They will be moved into your room {discord.utils.format_dt(moving_at, 'R') if with_delay else 'now'}.",
            color=(
                discord.Color.brand_red()
                if moving_to in self.specifics
                else discord.Color.green()
            ),
        )
        embed.add_field(
            emoji="👤",
            name="Student",
            value=f"**{self.student.display_name.title()}**: {self.student.mention}",
        )
        embed.add_field(
            emoji="🙋",
            name="Staff Member Requested",
            value=self.specifics_string(),
        )
        embed.add_field(emoji="📝", name="Purpose", value=self.purpose_string())
        embed.add_field(
            emoji="🔍",
            name="What They've Tried",
            value=self.tried_string(),
        )
        embed.set_thumbnail(url=self.student.display_avatar.url)
        return embed


class MetadataMapping(dict[discord.Member, StudentMetadata]):
    """
    Enhanced time-based dictionary for keeping track of when students added their
    metadata. Metadata has an expiry time, at which point the manager will re-request
    the student for new metadata.
    """

    REQUIRED_UPDATE_MINUTES = 15

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self._metadata = {}

    def __contains__(self, student: object) -> bool:
        if not isinstance(student, discord.Member):
            return False
        return student in self._metadata

    def __getitem__(self, student: discord.Member) -> StudentMetadata:
        return self._metadata[student]

    def __setitem__(self, student: discord.Member, metadata: StudentMetadata):
        self._metadata[student] = metadata
        task_name = self._expiry_task_name(student)
        self.bot.tasks.run_at(
            metadata.expiry,
            task_name,
            self._request_new_metadata,
            metadata,
        )
        self.bot.tasks.remove_task(self._expiry_task_name(metadata.student))
        self.bot.tasks.remove_task(self._removal_task_name(metadata))

    def __delitem__(self, student: discord.Member):
        metadata = self._metadata[student]
        del self._metadata[student]
        self.bot.tasks.remove_task(self._expiry_task_name(student))
        self.bot.tasks.remove_task(self._removal_task_name(metadata))

    def __iter__(self):
        return iter(self._metadata)

    def __len__(self) -> int:
        return len(self._metadata)

    def get(self, student: discord.Member, default=None) -> StudentMetadata:
        return self._metadata.get(student, default)

    async def _remove_and_disconnect(self, metadata: StudentMetadata):
        logger.info(
            f"Removing {metadata.student} from the queue because they failed to update their metadata in time.",
        )
        await metadata.student.edit(voice_channel=None)
        await metadata.student.send(
            "You have been removed from the queue because you did not submit a new request in time. If you still need help, please submit a new request.",
        )

    def _removal_task_name(self, metadata: StudentMetadata) -> str:
        return f"metadata_removequeue_{metadata.student.id}"

    def _expiry_task_name(self, member: discord.Member) -> str:
        return f"metadata_expiry_{member.id}"

    async def _request_new_metadata(self, metadata: StudentMetadata):
        minutes = (
            datetime.datetime.now().astimezone() - metadata.expiry
        ).total_seconds() / 60
        future_time = datetime.datetime.now().astimezone() + datetime.timedelta(
            minutes=self.REQUIRED_UPDATE_MINUTES,
        )
        embed = EmojiEmbed(
            title="Your request has expired!",
            description=f"It has been {minutes:.0f} minutes since you submitted your request to attend office hours.\n\n**Please submit a new request (using the button below) by {discord.utils.format_dt(future_time, 't')} to remain in the queue.**\n\nIf you do not submit a new request, you will be removed from the queue.",
            color=discord.Color.brand_red(),
        )
        view = UpdateExistingMetadataView(
            self.bot,
            self.REQUIRED_UPDATE_MINUTES,
            metadata.specifics,
        )
        self.bot.tasks.run_in(
            datetime.timedelta(minutes=self.REQUIRED_UPDATE_MINUTES),
            self._removal_task_name(metadata),
            self._remove_and_disconnect,
            metadata,
        )
        logger.info(f"Requesting metadata update for {metadata.student}...")
        try:
            msg = await metadata.student.send(embed=embed, view=view)
            view.message = msg
        except discord.Forbidden:
            msg = await self.bot.general_channel.send(
                f"{metadata.student.mention} Your office hours request has expired, but I cannot send you a DM to update your request. Please enable your direct messages and send me a message to receive the button. If you do not send me a message in {self.REQUIRED_UPDATE_MINUTES} minutes, your request will be deleted and you will be removed from the queue.",
            )

            def check(message: discord.Message) -> bool:
                return (
                    message.author == metadata.student
                    and message.channel == metadata.student.dm_channel
                )

            await self.bot.wait_for(
                "message",
                check=check,
                timeout=self.REQUIRED_UPDATE_MINUTES * 60,
            )
            await msg.delete()
            await metadata.student.send(embed=embed, view=view)

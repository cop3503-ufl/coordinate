from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

import discord

from ..db import TimeslotMethod

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from ..db import StaffMember, Timeslot
    from .main import OfficeHoursCog


logger = logging.getLogger(__name__)


class OfficeHoursChannelManager:
    """
    Performs channel operations for opening/closing office hour channels.
    """

    _voice_client_lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, bot: CoordinateBot, cog: OfficeHoursCog):
        self.bot = bot
        self.cog = cog

    async def create_voice_channel(
        self,
        staff_member: StaffMember,
        timeslot: Timeslot,
    ) -> discord.VoiceChannel:
        """
        Creates a voice channel for the given PM and timeslot.
        """
        in_person = timeslot.method == TimeslotMethod.INPERSON
        staff_oh_role = await self.bot.get_staff_oh_role(staff_member)
        permission_overwrites: dict[
            discord.Role | discord.Member | discord.Object,
            discord.PermissionOverwrite,
        ] = {
            self.bot.active_guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),
            self.bot.bot_role: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
            ),
            self.bot.ta_role: discord.PermissionOverwrite(
                view_channel=True,
                connect=not in_person,
            ),
            self.bot.inperson_role: discord.PermissionOverwrite(
                view_channel=True,
                connect=False,
            ),
        }
        if not in_person:
            permission_overwrites[staff_oh_role] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
            )
        vc_name = staff_member.name
        if in_person:
            first, last = staff_member.name.split(" ")[:2]
            vc_name = f"{first} {last[0]}. ({timeslot.room})"
        return await self.bot.active_guild.create_voice_channel(
            vc_name,
            category=self.bot.office_hours_cat,
            position=9999,
            overwrites=permission_overwrites,
            reason=f"Opening voice channel for {staff_member.name} because they currently have an active office hours timeslot: {timeslot.start}-{timeslot.end}.",
        )

    async def close_voice_channel(
        self,
        staff_member: StaffMember,
        voice_channel: discord.VoiceChannel,
    ):
        """
        Closes the given voice channel for the given PM.
        """
        member = self.bot.active_guild.get_member(staff_member.id)
        if not member:
            member = await self.bot.active_guild.fetch_member(staff_member.id)

        self.bot.office_hours_alerts_cog.cancel_alert(member)

        for member in voice_channel.members:
            with contextlib.suppress(discord.DiscordException):
                logger.info(
                    f"Force kicking {member} from office hours room because no students remain and time is up.",
                )
                if not member.bot:
                    await member.send(
                        f"The voice channel for **{staff_member.name}** has closed, and you have been removed. Have a great day!",
                    )

        with contextlib.suppress(
            discord.NotFound,
        ):  # in case the channel was deleted by another means
            await voice_channel.delete(
                reason=f"Closing channel for {staff_member.name}, as no members are present in the channel and their office hours timeslot has ended.",
            )
            logger.info(f"Closed office hours channel for {staff_member.name}.")

    def open_channels(self) -> list[discord.VoiceChannel]:
        return [
            vc
            for vc in self.bot.active_guild.voice_channels
            if self.bot.is_oh_channel(vc)
        ]

    async def play(self, voice_channel: discord.VoiceChannel, file_path: str):
        async with self._voice_client_lock:
            voice_client = await voice_channel.connect()
            logger.info(f"Connected to {voice_channel.name} for time control reminder.")
            # Time delay to let people quiet down
            await asyncio.sleep(1)
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(file_path),
                volume=0.5,
            )
            if not voice_client.is_playing():
                voice_client.play(
                    source,
                    after=lambda e: print(f"Player error: {e}") if e else None,
                )

            while voice_client.is_playing():
                await asyncio.sleep(1)
            await voice_client.disconnect()
            logger.info(
                f"Disconnected from {voice_channel.name} after time control reminder.",
            )

    def staff_of(self, voice_channel: discord.VoiceChannel) -> discord.Member | None:
        return discord.utils.find(
            lambda m: self.bot.ta_role in m.roles or self.bot.professor_role in m.roles,
            voice_channel.members,
        )

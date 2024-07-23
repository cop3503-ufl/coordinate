from __future__ import annotations

import contextlib
import datetime
import logging
from typing import TYPE_CHECKING

import discord

from ..components import emoji_button
from ..constants import (
    VC_BREAK_SUFFIX,
)
from ..views import CoordinateBotView
from .rooms import RoomState

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from .main import OfficeHoursCog


logger = logging.getLogger(__name__)


class LiveOHTimeSelectorButton(discord.ui.Button):
    def __init__(self, bot: CoordinateBot, minutes: int):
        self.bot = bot
        self.minutes = minutes
        super().__init__(
            label=f"{minutes} min",
            style=(
                discord.ButtonStyle.secondary
                if self.minutes <= 5
                else discord.ButtonStyle.red
            ),
        )

    async def callback(self, interaction: discord.Interaction):
        assert isinstance(interaction.user, discord.Member)

        # 1. Register break with office hours cog
        oh_cog = self.bot.office_hours_cog
        async with self.bot.db_factory() as db:
            doc = await db.get_staff_member(id=interaction.user.id)

        seconds = self.minutes * 60
        until = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
        voice_channel = self.bot.staff_member_channel(doc.name)
        room = oh_cog.room_manager.get_room(interaction.user)

        if not interaction.user.voice or not voice_channel or not room:
            return await interaction.response.send_message(
                "You are not currently hosting office hours right now, or you are not in your office hours room!",
                ephemeral=True,
            )

        # If staff member already on break/desiring break, don't respond
        elif doc.desiring_break:
            return await interaction.response.send_message(
                "You are already waiting to take a break. Once the student you are helping leaves, you will be able to take a break.",
                ephemeral=True,
            )

        elif room.closing:
            return await interaction.response.send_message(
                "You cannot take a break while your room is closing!",
                ephemeral=True,
            )

        elif room.students():
            await interaction.response.send_message(
                "You cannot take a break while students are in your room! Your break will begin when the next student leaves.",
                ephemeral=True,
            )

            await oh_cog.breaks.desire_break(interaction.user, self.minutes)
        else:
            await oh_cog.breaks.start_break(interaction.user, until)

            break_end_time = datetime.datetime.now() + datetime.timedelta(
                seconds=seconds,
            )
            await interaction.response.send_message(
                f"Starting your break! The next student will be accepted into your office hours {discord.utils.format_dt(break_end_time, 'R')}",
                ephemeral=True,
            )


class BreakOHView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    @emoji_button(
        emoji="🌴",
        label="Take a Break",
        custom_id="live_oh:break",
    )
    async def takebreak(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        view = CoordinateBotView()
        for minutes in (3, 5, 10):
            view.add_item(LiveOHTimeSelectorButton(self.bot, minutes))
        await interaction.response.send_message(
            "Ready for a break? Let's do it! How long would you like to take a break for?",
            view=view,
            ephemeral=True,
        )

    @emoji_button(
        emoji="⏩",
        label="End Break Early",
        custom_id="live_oh:end_break",
    )
    async def end_break(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not isinstance(interaction.user, discord.Member):
            return
        oh_cog = self.bot.office_hours_cog
        async with self.bot.db_factory() as db:
            staff_member = await db.get_staff_member(id=interaction.user.id)
            if not staff_member.breaking_until:
                return await interaction.response.send_message(
                    "You are not currently on break!",
                    ephemeral=True,
                )

        await oh_cog.breaks.end_break(interaction.user)
        logger.info(f"{interaction.user} ended their break early.")
        return await interaction.response.send_message(
            "https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExcWg1cWw2aHkxa2RyanZjMzk1bjVlM2QwanRhM3pneGgzd211eDlsZCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/6R2mLi910HL4VXFwOG/giphy.gif",
            ephemeral=True,
        )


class BreakManager:
    """
    Manages breaks for staff members.
    """

    def __init__(self, bot: CoordinateBot, cog: OfficeHoursCog):
        self.bot = bot
        self.cog = cog

    async def load_breaks(self):
        """
        Load breaks from the database.
        """
        logger.warning("Loading breaks from the database.")
        async with self.bot.db_factory() as db:
            docs = await db.get_staff()
            for doc in docs:
                if (
                    doc.breaking_until
                    and doc.breaking_until >= datetime.datetime.now().astimezone()
                ):
                    member = await self.bot.get_member(doc.id)
                    await self.start_break(member, doc.breaking_until)
                elif doc.breaking_until:
                    member = await self.bot.get_member(doc.id)
                    await self.end_break(member)
                elif doc.desiring_break:
                    member = await self.bot.get_member(doc.id)
                    await self.desire_break(member, doc.desiring_break)

    async def start_break(self, staff_member: discord.Member, until: datetime.datetime):
        """
        Start a break for a staff member.
        """
        async with self.bot.db_factory() as db:
            doc = await db.get_staff_member(member=staff_member)
            await db.start_break(doc, until)
        with contextlib.suppress(KeyError):
            self.cog.room_manager.update_state(staff_member, RoomState.CLOSED)
            self.bot.tasks.run_at(
                until,
                f"end_break_{staff_member.id}",
                self.end_break,
                staff_member,
            )
        logger.info(f"{staff_member} started their break (until: {until}).")

    async def desire_break(self, member: discord.Member, minutes: int):
        """
        Desire a break.
        """
        async with self.bot.db_factory() as db:
            staff_member = await db.get_staff_member(member=member)
            vc = self.bot.staff_member_channel(staff_member.name)
            await db.desire_break(staff_member, minutes)

        logger.info(
            f"{staff_member} desired a {minutes} minute break, but could not start because of students inside.",
        )

        def check(
            m: discord.Member,
            before: discord.VoiceState,
            after: discord.VoiceState,
        ) -> bool:
            cond = (
                before.channel == vc
                and before.channel is not None
                and after.channel != vc
                and len(before.channel.members) == 1
            )
            return cond

        if vc and len(vc.members) > 1:
            await self.bot.wait_for("voice_state_update", check=check)

        async with self.bot.db_factory() as db:
            staff_member = await db.merge(staff_member)
            await db.undesire_break(staff_member)

        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        await member.send(
            f"The student has left your room. Your break has begun! Your break ends {discord.utils.format_dt(until, 'R')}.",
        )

        await self.start_break(member, until)

    async def end_break(self, staff_member: discord.Member):
        # Remove from break
        async with self.bot.db_factory() as db:
            doc = await db.get_staff_member(member=staff_member)

        # Ensure that the staff member is still on break (in case two breaks
        # happened at the same time)
        if not doc.breaking_until:
            return

        async with self.bot.db_factory() as db:
            doc = await db.get_staff_member(member=staff_member)
            await db.end_break(doc)
        await self.change_nick(staff_member, starting_break=False)
        with contextlib.suppress(KeyError):
            self.cog.room_manager.update_state(staff_member, RoomState.OPEN)
            await self.cog.queue.allocate()

        await staff_member.send(
            "Your break has ended! You are now accepting students again.",
        )
        logger.info(f"Ended break for {staff_member}.")

    async def change_nick(self, member: discord.Member, *, starting_break: bool):
        name_length = len(VC_BREAK_SUFFIX) + 1
        try:
            if starting_break:
                await member.edit(
                    nick=f"{member.display_name[:32-name_length]} {VC_BREAK_SUFFIX}",
                )
            else:
                nick_without_break = member.display_name.replace(
                    f"{VC_BREAK_SUFFIX}",
                    "",
                ).strip()
                await member.edit(nick=nick_without_break)
        except discord.Forbidden:
            state = "began" if starting_break else "ended"
            await member.send(
                f"I tried to edit your nickname but was unable to because you have higher permissions than I. Rest assured, your break has **{state}**.",
            )

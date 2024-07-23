from __future__ import annotations

import asyncio
import datetime
import logging
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ..components import emoji_button
from ..utils import emoji_header
from ..views import CoordinateBotView

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from ..db import Timeslot


logger = logging.getLogger(__name__)


class CancelOfficeHoursView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    @emoji_button(
        emoji="🛑",
        label="Stop Timeslot",
        style=discord.ButtonStyle.red,
        custom_id="cancel_oh:cancel",
    )
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        # Administrator check
        assert isinstance(interaction.user, discord.Member)
        if not await self.bot.is_course_lead(interaction.user):
            return await interaction.response.send_message(
                f"You must be an {self.bot.professor_role.mention} or {self.bot.lead_ta_role.mention} to cancel office hours.",
                ephemeral=True,
            )

        message = interaction.message
        assert isinstance(message, discord.Message)
        embed = message.embeds[0]
        assert isinstance(embed, discord.Embed)
        for field in embed.fields:
            if field.name and "Staff Member" in field.name and field.value:
                mention = field.value
                # Find the user ID from the mention
                PATTERN = r"<@!?(\d+)>"
                match = re.search(PATTERN, mention)
                assert match is not None
                user_id = int(match.group(1))
                user = self.bot.active_guild.get_member(user_id)
                if not user:
                    await self.bot.active_guild.fetch_member(user_id)
                if user:
                    async with self.bot.db_factory() as db:
                        doc = await db.get_staff_member(member=user)
                    succeeded = False
                    for routine in doc.routines:
                        for timeslot in routine.timeslots:
                            if timeslot.start < discord.utils.utcnow() < timeslot.end:
                                async with self.bot.db_factory() as db:
                                    succeeded = await db.remove_timeslot(
                                        timeslot,
                                    )
                    channel = self.bot.get_channel(message.id)
                    thread = None
                    if isinstance(channel, discord.Thread):
                        thread = channel
                    else:
                        thread = await message.create_thread(name="Request Succeeded")
                    if succeeded:
                        await user.send(
                            f"Your office hours have been cancelled by {interaction.user.mention} because of absence.",
                        )

                        # Post notification message
                        await thread.send(
                            f"{user.mention}: Your office hours have been cancelled by {interaction.user.mention} because of absence.",
                        )
                    else:
                        await thread.send(
                            f"{interaction.user.mention}: Could not find this timeslot anymore. Was it already cancelled?",
                        )

                    return await interaction.response.send_message(
                        f"The request {'succeeded' if succeeded else 'failed'}. Please view {thread.jump_url} for more information.",
                        ephemeral=True,
                    )
        return await interaction.response.send_message("Uh oh! A problem occurred.")


class OfficeHoursAlerts(commands.Cog):
    _alert_tasks: dict[discord.Member, asyncio.Task]
    _last_alerts: dict[discord.Member, datetime.datetime]

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self._alert_tasks = {}
        self._last_alerts = {}

    async def send_missing_alert(
        self,
        staff_member: discord.Member,
        last_seen: datetime.datetime | None,
        timeslot: Timeslot,
    ):
        """
        Sends an alert in the office hours approvals channel about a missing
        staff member.
        """
        # Ensure that we don't send too many alerts
        DELAY_MINUTES = 15
        if staff_member in self._last_alerts and (
            discord.utils.utcnow() - self._last_alerts[staff_member]
        ) < datetime.timedelta(minutes=DELAY_MINUTES):
            logger.info(
                f"Not sending missing staff member alert for {staff_member} because one was sent less than {DELAY_MINUTES} minutes ago.",
            )
            return

        async with self.bot.db_factory() as db:
            doc = await db.get_staff_member(member=staff_member)
        embed = discord.Embed(
            title=":warning: Staff Member Missing",
            description=f"{doc.name} has not been active in {doc.pronouns} office hours for the past 15 minutes.",
            color=discord.Color.brand_red(),
        )
        embed.add_field(
            name=emoji_header("📅", "Last Seen"),
            value=discord.utils.format_dt(last_seen, "F") if last_seen else "Never",
            inline=False,
        )
        embed.add_field(
            name=emoji_header(emoji="🧑‍🏫", title="Staff Member"),
            value=staff_member.mention,
            inline=False,
        )
        embed.add_field(
            name=emoji_header(emoji="🕒", title="Ends"),
            value=f"{discord.utils.format_dt(timeslot.end, 'R')}",
            inline=False,
        )
        thumbnail = await self.bot.canvas.get_thumbnail(doc.name)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        minutes_ago = 15
        message = f"{self.bot.professor_role.mention}: {staff_member.mention} has not been in {doc.pronouns} office hours room for the past {minutes_ago:.0f} minutes. Should their office hours be cancelled to be rescheduled at a later time?"
        await self.bot.office_hours_approvals_ch.send(
            message,
            embed=embed,
            view=CancelOfficeHoursView(self.bot),
        )
        self._last_alerts[staff_member] = discord.utils.utcnow()

    async def prepare_alert(
        self,
        member: discord.Member,
        last_seen: datetime.datetime | None,
    ):
        # Wait 15 minutes
        logger.info(
            f"Preparing to send missing staff member alert because {member} is not in their office hours room.",
        )
        await asyncio.sleep(60 * 15)

        # Before alerting professor, do one final check to ensure that office
        # hours are still running
        async with self.bot.db_factory() as db:
            doc = await db.get_staff_member(member=member)
        slot = None
        for routine in doc.routines:
            for timeslot in routine.timeslots:
                start_time = timeslot.start
                end_time = timeslot.end
                if start_time < discord.utils.utcnow() < end_time and (
                    end_time - discord.utils.utcnow()
                ) > datetime.timedelta(minutes=5):
                    slot = timeslot

        # Also do one final check to make sure PM is still not in their office
        # hours room
        vc = self.bot.staff_member_channel(doc.name)
        not_present = vc and member not in vc.members

        # Alert professor
        if slot and not_present:
            await self.send_missing_alert(
                member,
                last_seen,
                slot,
            )

    def create_alert(self, member: discord.Member, *, overwrite: bool = True) -> None:
        if member in self._alert_tasks:
            if not overwrite:
                return
            self.remove_alert(self._alert_tasks[member])

        task = asyncio.create_task(
            self.prepare_alert(member, discord.utils.utcnow()),
        )
        self._alert_tasks[member] = task
        task.add_done_callback(self.remove_alert)
        logger.info(f"Created missing staff member alert for: {member}")

    def cancel_alert(self, member: discord.Member) -> None:
        if member in self._alert_tasks:
            self.remove_alert(self._alert_tasks[member])
        logger.info(f"Cancelled missing staff member alert for: {member}")

    def remove_alert(self, task: asyncio.Task):
        keys_to_remove = [key for key, t in self._alert_tasks.items() if t == task]

        # Remove the tasks from the dict
        for key in keys_to_remove:
            del self._alert_tasks[key]


async def setup(bot: CoordinateBot):
    await bot.add_cog(OfficeHoursAlerts(bot))

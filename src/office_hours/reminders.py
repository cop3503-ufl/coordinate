from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from ..db import StaffMemberRemindersSetting

if TYPE_CHECKING:
    from ..bot import CoordinateBot


logger = logging.getLogger(__name__)


class ReminderDropdown(discord.ui.Select):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        options = [
            discord.SelectOption(
                label="Always",
                emoji="⏰",
                description="A reminder is sent 10 minutes prior.",
            ),
            discord.SelectOption(
                label="Only Away",
                emoji="🌜",
                description="Only send a reminder if my status is Away.",
            ),
            discord.SelectOption(
                label="Only Offline",
                emoji="🌚",
                description="Only send a reminder if my status is Offline.",
            ),
            discord.SelectOption(
                label="Never",
                emoji="❌",
                description="Never send me reminders prior to my start time.",
            ),
        ]
        super().__init__(placeholder="Change my reminder settings...", options=options)

    async def callback(self, interaction: discord.Interaction):
        # Update setting in database
        responses = {
            "Always": StaffMemberRemindersSetting.ALWAYS,
            "Only Away": StaffMemberRemindersSetting.ONLY_AWAY,
            "Only Offline": StaffMemberRemindersSetting.ONLY_OFFLINE,
            "Never": StaffMemberRemindersSetting.NEVER,
        }
        async with self.bot.db_factory() as db:
            staff_doc = await db.get_staff_member(id=interaction.user.id)
            await db.update_reminder_preference(staff_doc, responses[self.values[0]])
        await interaction.response.send_message(
            "Your reminder settings have been updated!",
            ephemeral=True,
        )


class OfficeHoursReminders:
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.schedule_reminders.start()

    def task_name(self, id: int) -> str:
        return f"oh_reminders:{id}"

    @tasks.loop(minutes=15)  # ugh works for now
    async def schedule_reminders(self):
        await self.bot.wait_until_ready()
        logger.info("Scheduling reminders for all staff members.")
        async with self.bot.db_factory() as db:
            staff = await db.get_staff()
            for staff_member in staff:
                next_timeslot = staff_member.next_timeslot()
                if not next_timeslot:
                    continue
                before = next_timeslot.start - datetime.timedelta(minutes=10)
                if before > datetime.datetime.now().astimezone():
                    self.bot.tasks.run_at(
                        before,
                        self.task_name(staff_member.id),
                        self.send_reminder,
                        staff_member.id,
                        staff_member.reminders,
                        next_timeslot.start,
                    )

    async def send_reminder(
        self,
        member_id: int,
        setting: StaffMemberRemindersSetting,
        start_time: datetime.datetime,
    ):
        """
        Sends a brief reminder to a staff member about their upcoming office hours.
        """
        member = self.bot.active_guild.get_member(member_id)
        if not member:
            member = await self.bot.active_guild.fetch_member(member_id)

        # Check setting
        if (
            (setting == "never")
            or (
                setting == "away"
                and member.status
                not in (
                    discord.Status.idle,
                    discord.Status.offline,
                )
            )
            or (setting == "offline" and member.status not in (discord.Status.offline,))
        ):
            logger.info(
                f"Avoided sending reminder to {member} because their setting is {setting}, and their status is {member.status}.",
            )
            return

        minutes_before = (start_time - discord.utils.utcnow()).total_seconds() / 60
        bot_panel_channel = self.bot.bot_panel_ch
        logger.info(
            f"Sending reminder to {member} about their office hours starting in {minutes_before:.0f} minutes.",
        )
        await member.send(
            f"Hey {member.mention}! Just here to remind you that your office hours start in **{minutes_before:.0f} minutes**. Please make sure you're ready to go! If you need to delay your office hours or change how often you receive these reminders, please use the buttons in {bot_panel_channel.mention}.",
        )

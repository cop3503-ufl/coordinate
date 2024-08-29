from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..db import TimeslotMethod
from ..issues import GitHubIssueView
from .breaks import BreakManager, BreakOHView
from .channels import OfficeHoursChannelManager
from .feedback import OfficeHoursFeedbackSender
from .queue import QueueManager
from .reminders import OfficeHoursReminders
from .rooms import RoomManager
from .routines import (
    OfficeHoursRoutineUpdateView,
)
from .staff import StaffMemberProfileView
from .time_control import TimeControl
from .timeslots import OfficeHoursUpdateView
from .tracker import TimeTracker

if TYPE_CHECKING:
    from ..bot import CoordinateBot


logger = logging.getLogger(__name__)


class OfficeHoursCog(commands.Cog):
    ready_to_close: list[discord.VoiceChannel]

    channel_manager: OfficeHoursChannelManager
    breaks: BreakManager
    queue: QueueManager
    tracker: TimeTracker
    reminders: OfficeHoursReminders
    room_manager: RoomManager
    time_control: TimeControl
    feedback: OfficeHoursFeedbackSender

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.update.start()
        self.ready_to_close = []
        self.channel_manager = OfficeHoursChannelManager(bot, self)
        self.breaks = BreakManager(bot, self)
        self.queue = QueueManager(bot, self)
        self.tracker = TimeTracker(bot)
        self.reminders = OfficeHoursReminders(bot)
        self.room_manager = RoomManager(bot)
        self.time_control = TimeControl(bot)
        self.feedback = OfficeHoursFeedbackSender(bot)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.tracker.load_from_vcs()
        await self.breaks.load_breaks()
        await self.room_manager.load_rooms()
        await self.queue.build_queue()

    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.command(
        name="overwrite",
        description="Overwrite a member's position in the queue. Remember: use zero-based indexing.",
    )
    async def insert_at_index(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        position: int,
    ):
        if member.voice is None or member.voice.channel != self.bot.waiting_channel:
            return await interaction.response.send_message(
                f"{member} is not in the queue! They need to use the join OH buttons to join the queue first.",
                ephemeral=True,
            )

        await self.queue.move_student_position(member, position)
        logger.warn(
            f"Manually inserting {member} into queue at {position}... queue is now: {[str(m) for m in self.queue.queue]}",
        )

        await self.bot.office_hours_approvals_ch.send(
            f"{interaction.user.mention} manually updated the position of {member.mention} in the queue to `{position}`.",
        )
        await interaction.response.send_message(
            f"Success. The position of {member.mention} in the queue is now `{position}`.",
            ephemeral=True,
        )

    @commands.command("botpanel")
    @commands.has_role("Admin")
    async def update_bot_panel(self, ctx):
        bot_panel_history = [
            m async for m in self.bot.bot_panel_ch.history(oldest_first=True)
        ]
        change_embed = discord.Embed(
            title="Update Profile!",
            color=discord.Color.from_rgb(249, 141, 139),
            description=f"Use the following buttons to update your profile with the bot. This profile is the record the bot will use for authenticating your operations on Discord and allowing you to host office hours.\n* **Register/Re-register:** Use this button to register as a staff member with the bot, or re-register for a new semester. Every staff member must use this button at the start of the semester in order to maintain their {self.bot.ta_role.mention} role.\n* **Update Profile:** Use this button to change your chosen name or pronouns with the bot. Please remember to be respectful, appropriate, and authentic with the values you choose.\n* **Change Reminders:** Use this button to update your office hours reminder preferences.",
        )
        update_oh_embed = discord.Embed(
            title="Update Individual Office Hours!",
            color=discord.Color.from_rgb(253, 185, 130),
            description="Use the buttons below to update your office hours schedule. All significant changes (adding, moving, and removing timeslots) need to be approved by the course professor.\n* **Add:** Add a new office hours timeslot into your schedule. You will be asked for the time you want to host them at.\n* **Move:** Update an existing office hours timeslot in your schedule to start at a new time. You can also use this button to change the length of an existing office hours timeslot.\n* **Remove:** Completely remove a timeslot from your office hours schedule. You will not be able to get it back!\n* **Delay:** Delay your office hours by a limited amount of time. Useful if your bus is running late, or you're stuck in traffic, etc. This button does _not_ require professor approval, but you can only delay a timeslot up to 60 minutes.",
        )
        update_routine_embed = discord.Embed(
            title="Update your Routines!",
            color=discord.Color.from_rgb(255, 247, 124),
            description="Use the following buttons to change your weekly office hour routines for the duration of the semester. All routine requests will also require professor approval.\n* **Add Routine:** Add a new weekly routine into your schedule. This will add a timeslot on the provided weekday and time for each week of the semester. This can help you manage lots of timeslots effectively.\n* **Remove Routine:** Removes an existing weekly routine from your existing office hours schedule. This removes all of the timeslots associated with this routine.",
        )
        live_embed = discord.Embed(
            title="Take a Break!",
            color=discord.Color.from_rgb(189, 236, 108),
            description="Use the following buttons to manage your breaks while hosting office hours. Breaks are a way to take a short break from office hours in order to use the bathroom, attend to something at home, or just relax for a second. No students will be moved into your room while on break. Breaks do not require professor approval.\n* **Take a Break:** Start a new break! If you are currently working with a student, your break will start after the student leaves. Otherwise, it will begin immediately.\n* **End Break Early:** If you're ready early to hop back into the action, feel free to use this button to cut the remainder of your break.",
        )
        gissue_embed = discord.Embed(
            title="Report a Bug, Request, or Feedback!",
            color=discord.Color.from_rgb(131, 216, 249),
            description="Use the buttons below to report bugs, request new features, or provide feedback on the bot. The button will automatically add your request into our issue tracker, where we can track its progress and respond formally.\n* **File a Ticket:** Yup, it does what you think it does. Do I need to say more?",
        )
        if len(bot_panel_history) < 5:
            logger.warn(
                "Less than three messages found in the bot-panel. Reposting relevant messages.",
            )
            await self.bot.bot_panel_ch.purge()
            await self.bot.bot_panel_ch.send(
                embed=change_embed,
                view=StaffMemberProfileView(self.bot),
            )
            await self.bot.bot_panel_ch.send(
                embed=update_oh_embed,
                view=OfficeHoursUpdateView(self.bot),
            )
            await self.bot.bot_panel_ch.send(
                embed=update_routine_embed,
                view=OfficeHoursRoutineUpdateView(self.bot),
            )
            await self.bot.bot_panel_ch.send(
                embed=live_embed,
                view=BreakOHView(self.bot),
            )
            await self.bot.bot_panel_ch.send(
                embed=gissue_embed,
                view=GitHubIssueView(self.bot),
            )
        else:
            if (
                not bot_panel_history[0].embeds
                or bot_panel_history[0].embeds[0] != change_embed
            ):
                await bot_panel_history[0].edit(
                    embed=change_embed,
                    view=StaffMemberProfileView(self.bot),
                )
            if (
                not bot_panel_history[1].embeds
                or bot_panel_history[1].embeds[0] != update_oh_embed
            ):
                await bot_panel_history[1].edit(
                    embed=update_oh_embed,
                    view=OfficeHoursUpdateView(self.bot),
                )
            if (
                not bot_panel_history[2].embeds
                or bot_panel_history[2].embeds[0] != update_routine_embed
            ):
                await bot_panel_history[2].edit(
                    embed=update_routine_embed,
                    view=OfficeHoursRoutineUpdateView(self.bot),
                )

    @tasks.loop(seconds=5)
    async def update(self):
        await self.bot.wait_until_ready()
        async with self.bot.db_factory() as db:
            schedule = await db.get_staff()
        await self.bot.office_hours_schedule_cog.update_help_message()

        for staff_member in schedule:
            name = staff_member.name
            voice_channel = self.bot.staff_member_channel(name)
            member = await self.bot.get_member(staff_member.id)
            if (timeslot := staff_member.active_timeslot()) is not None:
                if voice_channel is None or not self.room_manager.get_room(member):
                    # Time to open the room/channel!
                    await self.room_manager.open_room(staff_member, timeslot)
                elif (
                    timeslot.method == TimeslotMethod.DISCORD
                    and not voice_channel.members
                ):
                    member = await self.bot.get_member(staff_member.id)
                    self.bot.office_hours_alerts_cog.create_alert(
                        member,
                        overwrite=False,
                    )
            elif voice_channel:
                # Need to close voice channel!
                await self.room_manager.close_room(staff_member, voice_channel)

    @update.error
    async def update_error(self, _):
        logger.exception("Error has occurred in the update task.")
        await asyncio.sleep(10)
        self.update.restart()


async def setup(bot):
    await bot.add_cog(OfficeHoursCog(bot))

"""
This file contains the view for handling office hours change requests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import discord

from ..components import emoji_button
from ..db import (
    AddOfficeHoursRequest,
    AddRoutineOfficeHoursRequest,
    MoveOfficeHoursRequest,
    OfficeHoursRequest,
    OfficeHoursRequestType,
    RemoveOfficeHoursRequest,
    RemoveRoutineOfficeHoursRequest,
    Routine,
)
from ..views import CoordinateBotView

if TYPE_CHECKING:
    from ..bot import CoordinateBot


logger = logging.getLogger(__name__)


OHRequestDispatcher = Callable[[discord.Interaction, Any], Coroutine[Any, Any, None]]


class OHApprovalView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    async def add_oh(
        self,
        _: discord.Interaction,
        report: AddOfficeHoursRequest,
    ):
        # Add times to database
        discord_uid = report.staff_id
        start_time = report.start
        end_time = report.end

        logger.info(
            f"Handling ADD office hours change request for UID {discord_uid} with timeslot: {start_time} - {end_time}",
        )
        async with self.bot.db_factory() as db:
            user = await db.get_staff_member(id=discord_uid)
            await db.add_timeslot(
                user,
                start_time,
                end_time,
                method=report.method,
                room=report.room,
                meeting_url=report.meeting_url,
            )

    async def move_oh(
        self,
        _: discord.Interaction,
        report: MoveOfficeHoursRequest,
    ):
        timeslot_start = report.timeslot.start  # type: ignore
        logger.info(
            f"Handling MOVE office hours change request for UID {report.staff_id}. Changing timeslots starting at {timeslot_start} to: {report.new_start} - {report.new_end}",
        )
        async with self.bot.db_factory() as db:
            await db.move_timeslot(
                report.timeslot,  # type: ignore
                report.new_start,
                report.new_end,
                report.method,
                report.room,
                report.meeting_url,
            )

    async def remove_oh(
        self,
        _: discord.Interaction,
        report: RemoveOfficeHoursRequest,
    ):
        timeslot_start = report.timeslot.start  # type: ignore
        timeslot_end = report.timeslot.end  # type: ignore
        logger.info(
            f"Handling REMOVE office hours change request for UID {report.staff_id}. Removing timeslots with time: {timeslot_start} - {timeslot_end}",
        )
        async with self.bot.db_factory() as db:
            timeslot = await db.merge(report.timeslot)
            await db.remove_timeslot(timeslot)

    async def add_oh_routine(
        self,
        interaction: discord.Interaction,
        report: AddRoutineOfficeHoursRequest,
    ) -> None:
        del interaction
        routine = Routine(
            report.weekday,
            report.start_time,
            report.length,
            report.staff,
            report.method,
            room=report.room,
            meeting_url=report.meeting_url,
        )
        timeslots, _ = routine.generate_timeslots()
        async with self.bot.db_factory() as db:
            await db.add_routine(routine, timeslots)

    async def remove_oh_routine(
        self,
        _: discord.Interaction,
        report: RemoveRoutineOfficeHoursRequest,
    ):
        async with self.bot.db_factory() as db:
            routine = await db.merge(report.routine)
            await db.remove_routine(routine)

    async def remove_all_active(
        self,
        interaction: discord.Interaction,
        discord_id: int,
        *,
        approved: bool,
    ):
        assert isinstance(interaction.message, discord.Message)
        # Update the message's view to reflect the Approve and Deny button changes
        await interaction.message.edit(view=self)

        # Make a new thread with response
        channel = self.bot.get_channel(interaction.message.id)
        thread = None
        if isinstance(channel, discord.Thread):
            thread = channel
        else:
            thread = await interaction.message.create_thread(
                name=f"Request {'Denied' if not approved else 'Approved'}",
                reason=f"Creating thread to indicate that request was approved={approved}.",
            )
        member = self.bot.active_guild.get_member(discord_id)
        assert isinstance(member, discord.Member)
        await thread.send(
            content=f"{member.mention}: {interaction.user.display_name} {'denied' if not approved else 'approved'} your office hours change request.{' The schedule has been updated.' if approved else ' Your schedule remains the same.'}",
        )
        await interaction.response.send_message(
            "Your response has been posted.",
            ephemeral=True,
        )

    @emoji_button(
        emoji="✅",
        label="Approve",
        style=discord.ButtonStyle.green,
        custom_id="approval:approve",
    )
    async def approve_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        assert isinstance(interaction.user, discord.Member)
        if not await self.bot.is_course_lead(interaction.user):
            logger.info(
                f"{interaction.user} ({interaction.user.display_name}) attempted to approve an OH change request, but only has these roles: {interaction.user.roles}",
            )
            return await interaction.response.send_message(
                f"You need to be a {self.bot.professor_role.mention} or {self.bot.lead_ta_role.mention} to approve/ignore OH change requests!",
                ephemeral=True,
            )

        assert isinstance(interaction.message, discord.Message)
        message_id = interaction.message.id
        async with self.bot.db_factory() as db:
            report = await db.get_oh_request(message_id)
        dispatchers: dict[OfficeHoursRequestType, OHRequestDispatcher] = {
            OfficeHoursRequestType.ADD: self.add_oh,
            OfficeHoursRequestType.MOVE: self.move_oh,
            OfficeHoursRequestType.REMOVE: self.remove_oh,
            OfficeHoursRequestType.ADD_ROUTINE: self.add_oh_routine,
            OfficeHoursRequestType.REMOVE_ROUTINE: self.remove_oh_routine,
        }
        classes: dict[OfficeHoursRequestType, type[OfficeHoursRequest]] = {
            OfficeHoursRequestType.ADD: AddOfficeHoursRequest,
            OfficeHoursRequestType.MOVE: MoveOfficeHoursRequest,
            OfficeHoursRequestType.REMOVE: RemoveOfficeHoursRequest,
            OfficeHoursRequestType.ADD_ROUTINE: AddRoutineOfficeHoursRequest,
            OfficeHoursRequestType.REMOVE_ROUTINE: RemoveRoutineOfficeHoursRequest,
        }
        async with self.bot.db_factory() as db:
            report = await db.get_oh_request(message_id, classes[report.type])
        method = dispatchers.get(report.type)
        if method:
            await method(interaction, report)
        else:
            logger.error(
                f"Encountered unknown office hours change request type: {report.type}",
            )
            await interaction.response.send_message(
                f"An improper change type was requested (`{report.type}`) - this has been logged.",
            )
        # disable Approve button and remove the Deny button
        button.disabled = True
        button.label = "Approved"
        self.remove_item(self.children[1])
        await self.remove_all_active(
            interaction,
            report.staff_id,
            approved=True,
        )

    @emoji_button(
        emoji="🛑",
        label="Deny",
        style=discord.ButtonStyle.red,
        custom_id="approval:ignore",
    )
    async def ignore_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        assert isinstance(interaction.user, discord.Member)
        if not await self.bot.is_course_lead(interaction.user):
            logger.info(
                f"{interaction.user} ({interaction.user.display_name}) attempted to decline an OH change request, but only has these roles: {interaction.user.roles}",
            )
            return await interaction.response.send_message(
                f"You need to be a {self.bot.professor_role.mention} or {self.bot.lead_ta_role.mention} to deny OH change requests!",
                ephemeral=True,
            )

        assert isinstance(interaction.message, discord.Message)
        message_id = interaction.message.id
        async with self.bot.db_factory() as db:
            report = await db.get_oh_request(message_id)
            # disable Deny button and remove the Approve button
            button.disabled = True
            button.label = "Denied"
            self.remove_item(self.children[0])
            if report:
                await self.remove_all_active(
                    interaction,
                    report.staff_id,
                    approved=False,
                )

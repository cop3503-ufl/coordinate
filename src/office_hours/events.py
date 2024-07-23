from __future__ import annotations

import contextlib
import logging
import random
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ..db import OfficeHoursSessionStatus
from .rooms import RoomState

if TYPE_CHECKING:
    from ..bot import CoordinateBot


logger = logging.getLogger(__name__)


class OfficeHoursEventHandler(commands.Cog):
    """
    Handles events related to office hours.
    """

    def __init__(self, bot: CoordinateBot):
        self.bot = bot

    async def staff_enters_voice(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """
        Handles when a staff member enters a voice channel.
        """
        assert after.channel is not None

        # Get info about the PM who joined
        async with self.bot.db_factory() as db:
            relevant_doc = await db.get_staff_member(member=member)

        # If they joined the queue channels, kick them out
        if after.channel == self.bot.waiting_channel:
            await member.edit(
                voice_channel=None,
                reason=f"Removing {member} from {after.channel} (a queue channel), because they are a staff member!",
            )
            await member.send(
                content="Sorry, staff members can't join the queue! This queue is only reserved for our amazing students.",
            )

        # If they joined their own channel, they should be ready to accept students
        if (
            not after.channel
        ):  # if a PM joins the queue, they will be immediately removed
            return
        their_channel = relevant_doc.name in after.channel.name
        member_joined = before.channel is None and after.channel is not None
        only_staff = len(after.channel.members) == 1
        logger.info(
            f"{member} (TA) has {'joined' if member_joined else 'moved to'} {after.channel} {'(their own channel)' if their_channel else ''}",
        )
        students = [
            m for m in after.channel.members if self.bot.student_role in m.roles
        ]
        if their_channel:
            self.bot.office_hours_cog.tracker.start_tracking(
                relevant_doc,
                with_student=bool(students),
            )
            self.bot.office_hours_alerts_cog.cancel_alert(member)

        if their_channel and only_staff:
            assert isinstance(after.channel, discord.VoiceChannel)
            self.bot.office_hours_cog.room_manager.update_state(
                member,
                RoomState.OPEN if not relevant_doc.breaking_until else RoomState.CLOSED,
            )
            await self.bot.office_hours_cog.queue.allocate()

        if not their_channel:
            cog = self.bot.office_hours_alerts_cog
            cog.create_alert(member)

    async def staff_leaves_voice(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        del after
        logger.info(f"{member} (TA) has left {before.channel}")

        async with self.bot.db_factory() as db:
            staff_member = await db.get_staff_member(member=member)
        self.bot.office_hours_cog.tracker.stop_tracking(staff_member)
        self.bot.office_hours_alerts_cog.create_alert(member)
        with contextlib.suppress(
            KeyError,
        ):  # can happen due to race conditions, especially if bot just started
            self.bot.office_hours_cog.room_manager.update_state(
                member,
                RoomState.CLOSED,
            )

    async def student_joins_waiting(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        del before, after
        # Student joins in-person voice queue
        logger.info(
            f"{member} has joined the in-person waiting queue!",
        )

        # Show student we are waiting on preferences
        await self.bot.office_hours_cog.queue.add_student(member)

        # Create new session for student
        async with self.bot.db_factory() as db:
            await db.create_new_session(member.id, [])

    async def student_left_waiting(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        del before
        logger.info(
            f"{member} has left the in-person waiting queue ({after.channel})",
        )

        if self.bot.oh_queue_role in member.roles:
            await member.remove_roles(self.bot.oh_queue_role)

        await self.bot.office_hours_cog.queue.remove_student(member)

        # If student left queue entirely, mark session as LEFT_QUEUE
        if after.channel is None:
            async with self.bot.db_factory() as db:
                await db.abort_session(member.id)

    async def student_left_staff_room(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        del after
        logger.info(f"{member} has left an office hours channel ({before.channel})")
        assert isinstance(before.channel, discord.VoiceChannel)

        doc = await self.bot.staff_doc_from_vc(before.channel)
        staff_member = await self.bot.get_member(doc.id)
        staff_oh_role = await self.bot.get_staff_oh_role(doc)
        if staff_oh_role in member.roles:
            await member.remove_roles(staff_oh_role)

        self.bot.tasks.create_task(
            self.bot.office_hours_cog.room_manager.finish_delay(staff_member),
        )
        assert isinstance(before.channel, discord.VoiceChannel)

        # Update PM object with time they have been helping students
        # if they are the only member left in the channel
        if len(before.channel.members) == 1:
            await self.bot.office_hours_cog.tracker.student_left(doc)

        room = self.bot.office_hours_cog.room_manager.get_room(staff_member)
        if room:
            await self.bot.office_hours_cog.time_control.on_student_leave(member, room)

        # End session for student
        async with self.bot.db_factory() as db:
            await db.end_session(member.id, doc.id, OfficeHoursSessionStatus.COMPLETED)

        if random.random() < 0.2:
            await self.bot.office_hours_cog.feedback.send_feedback_request(member)

    async def student_joins_staff_room(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        del before
        assert isinstance(after.channel, discord.VoiceChannel)

        doc = await self.bot.staff_doc_from_vc(after.channel)
        ta = await self.bot.get_member(doc.id)
        # Add PM OH role
        staff_oh_role = await self.bot.get_staff_oh_role(doc)
        if staff_oh_role not in member.roles:
            logger.info(
                f"{member} was moved into '{after.channel.name}' forcefully: giving them PM-specific OH role.",
            )
            await member.add_roles(staff_oh_role)

        if len(after.channel.members) == 2:
            # TODO Check through the audit log to see when student
            # was accepted

            await self.bot.office_hours_cog.tracker.student_joined(doc)

        room = self.bot.office_hours_cog.room_manager.get_room(ta)
        if room:
            await self.bot.office_hours_cog.time_control.on_student_join(member, room)

        # Start session for student
        async with self.bot.db_factory() as db:
            await db.start_session(member.id, ta.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """
        This function is responsible for the heavy lifting behind ensuring members
        and rooms are accounted for appropriately.

        This function should be used over watching what students/PMs request to happen,
        as this method operates on real-life events, not what is requested. For example,
        this method will account for a student leaving the queue as a result of
        being banned, being kicked out of voice, disconnecting from voice, etc.,
        while other methods may not account for this.

        This function should:
            - Ensure members are removed from their queue when they leave the queue.
            - Ensure members who rejoin the queue channel are placed back into the queue.
            - Ensure that new members are moved into a PM room when other members leave.
            - Ensure that PMs can not join either queue channel.
            - Ensure that PM rooms are closed when no one is left.
        """
        member_joined = before.channel is None and after.channel is not None
        member_left = before.channel is not None and after.channel is None
        member_moved = (
            before.channel is not None
            and after.channel is not None
            and before.channel != after.channel
        )
        is_staff = await self.bot.is_staff(member)

        if is_staff and (member_joined or member_moved):
            await self.staff_enters_voice(member, before, after)

        elif is_staff and member_left:
            await self.staff_leaves_voice(member, before, after)

        elif after.channel == self.bot.waiting_channel and (
            member_joined or member_moved
        ):
            await self.student_joins_waiting(member, before, after)

        elif (
            after.channel != self.bot.waiting_channel
            and before.channel == self.bot.waiting_channel
            and self.bot.student_role in member.roles
        ):
            await self.student_left_waiting(member, before, after)

        # Check if a student has left a PM's office
        if (
            self.bot.student_role in member.roles
            and isinstance(before.channel, discord.VoiceChannel)
            and self.bot.is_oh_channel(before.channel)
            and after.channel != before.channel
        ):
            await self.student_left_staff_room(member, before, after)

        # If student moved into PM room, make sure they have role
        if (
            self.bot.student_role in member.roles
            and isinstance(after.channel, discord.VoiceChannel)
            and self.bot.is_oh_channel(after.channel)
            and after.channel.members
            and before.channel
            and before.channel != after.channel
        ):
            await self.student_joins_staff_room(member, before, after)

        # Check if no one is left and time has expired
        if before.channel:
            students = [
                m for m in before.channel.members if self.bot.student_role in m.roles
            ]
            staff_members = [
                m for m in before.channel.members if await self.bot.is_staff(m)
            ]
            if before.channel in self.bot.office_hours_cog.ready_to_close and (
                not students and len(staff_members) <= 1
            ):
                doc = await self.bot.staff_doc_from_vc(before.channel)
                logger.info(
                    f"Requesting office hours channel close for {doc.name} because everyone has left the channel after time is up.",
                )
                if doc:
                    await self.bot.office_hours_cog.channel_manager.close_voice_channel(
                        doc,
                        before.channel,
                    )


async def setup(bot: CoordinateBot):
    await bot.add_cog(OfficeHoursEventHandler(bot))

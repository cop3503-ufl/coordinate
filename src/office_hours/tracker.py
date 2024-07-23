from __future__ import annotations

import contextlib
import datetime
import logging
from typing import TYPE_CHECKING

from ..exceptions import StaffMemberNotFound

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from ..db import StaffMember


logger = logging.getLogger(__name__)


class TimeTracker:
    """
    Tracks the amount of time spent with/without students for each staff member.

    >>> tracker = TimeTracker()
    >>> staff_member = StaffMember("Person Joe", ...)
    >>> # Person joins their voice channel
    >>> tracker.start_tracking(staff_member, with_student = True)
    >>> # Student is moved to staff_member's room
    >>> tracker.student_joined(staff_member, with_student = True)
    datetime.timedelta(minutes = 10, seconds = 43)
    >>> # (is logged to the database)
    >>> # Student leaves
    >>> tracker.student_left(staff_member)
    datetime.timedelta(minutes = 12, seconds = 4)
    >>> # (is logged to the database)
    """

    spent_without: dict[int, datetime.datetime]
    spent_with: dict[int, datetime.datetime]

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.spent_with = {}
        self.spent_without = {}

    async def student_left(self, staff_member: StaffMember) -> datetime.timedelta:
        """
        Calculates amount of time the staff member spent with the student.
        """
        if self.students_inside(staff_member):
            # Nothing to do for now
            return datetime.timedelta()

        if staff_member.id not in self.spent_with:
            raise RuntimeError(f"{staff_member} is not tracking a student.")

        diff = datetime.datetime.now() - self.spent_with[staff_member.id]

        async with self.bot.db_factory() as db:
            staff_member = await db.merge(staff_member)
            await db.add_seconds_spent(staff_member, diff.total_seconds())

        logger.info(f"Logged time spent with student: {staff_member}, {diff}")
        self.switch(staff_member)
        return diff

    def switch(self, staff_member: StaffMember) -> None:
        """
        Switches the tracking state of the staff member.
        """
        if staff_member.id in self.spent_with:
            self.spent_without[staff_member.id] = datetime.datetime.now()
            del self.spent_with[staff_member.id]
        else:
            self.spent_with[staff_member.id] = datetime.datetime.now()
            del self.spent_without[staff_member.id]

    def students_inside(self, staff_member: StaffMember, limit: int = 0) -> bool:
        vc = self.bot.staff_member_channel(staff_member.name)
        if vc:
            students_inside = [
                m for m in vc.members if self.bot.student_role in m.roles
            ]
            return len(students_inside) > limit
        return False

    async def student_joined(self, staff_member: StaffMember) -> datetime.timedelta:
        if self.students_inside(staff_member, limit=1):
            # Nothing to do for now
            return datetime.timedelta()

        if staff_member.id not in self.spent_without:
            raise RuntimeError(f"{staff_member} is not being tracked.")

        diff = datetime.datetime.now() - self.spent_without[staff_member.id]

        async with self.bot.db_factory() as db:
            staff_member = await db.merge(staff_member)
            await db.add_seconds_spent(staff_member, diff.total_seconds())

        logger.info(f"Logged time spent without student: {staff_member}, {diff}")
        self.switch(staff_member)
        return diff

    def is_tracking(self, staff_member: StaffMember) -> bool:
        return staff_member in self.spent_with or staff_member in self.spent_without

    def start_tracking(
        self,
        staff_member: StaffMember,
        *,
        with_student: bool = False,
    ) -> None:
        if self.is_tracking(staff_member):
            raise RuntimeError(f"{staff_member} is already being tracked.")

        if with_student:
            self.spent_with[staff_member.id] = datetime.datetime.now()
        else:
            self.spent_without[staff_member.id] = datetime.datetime.now()
        logger.info(f"Started tracking {staff_member} (with student: {with_student})")

    def stop_tracking(self, staff_member: StaffMember) -> None:
        if staff_member in self.spent_with:
            del self.spent_with[staff_member.id]
        if staff_member in self.spent_without:
            del self.spent_without[staff_member.id]
        logger.info(f"Stopped tracking {staff_member}")

    async def load_from_vcs(self) -> None:
        await self.bot.wait_until_ready()
        logger.warning("Loading tracker registry from existing voice channels.")
        for channel in self.bot.active_guild.voice_channels:
            with contextlib.suppress(StaffMemberNotFound):
                staff_member = await self.bot.staff_doc_from_vc(channel)
                students = [
                    m for m in channel.members if self.bot.student_role in m.roles
                ]
                self.start_tracking(staff_member, with_student=len(students) > 0)

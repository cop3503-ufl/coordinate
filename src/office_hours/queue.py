from __future__ import annotations

import contextlib
import datetime
import logging
import re
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from .metadata import MetadataMapping, StudentMetadata
from .rooms import Room, RoomState

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from ..db import StaffMember
    from .main import OfficeHoursCog

logger = logging.getLogger(__name__)


@dataclass
class QueueStudent:
    member: discord.Member
    _preemptively_heading_to: Room | None = None

    def __hash__(self) -> int:
        return hash(self.member)

    async def change_suffix(
        self,
        suffix: str | None,
        *,
        only_if_necessary: bool = False,
        **kwargs,
    ):
        """
        Updates the member's suffix in Discord. Keyword arguments passed to this function
        are forwarded to member.edit to change the member object in further ways.
        """
        new_nick = re.sub(
            r" \(\#-?\d+\)",
            "",
            self.member.nick or self.member.display_name,
        )
        new_nick = re.sub(r" \(m\)", "", new_nick)
        new_nick = re.sub(r" \(a\)", "", new_nick)
        with contextlib.suppress(discord.errors.Forbidden):
            new_nick = f"{new_nick} {suffix}"[:32] if suffix else new_nick[:32]
            if self.member.voice and (
                not only_if_necessary or new_nick != self.member.display_name
            ):
                await self.member.edit(
                    nick=new_nick,
                    reason=f"Changing nickname of {self.member} to {new_nick} (using suffix {suffix})...",
                    **kwargs,
                )

    def mark_as_heading_to(self, room: Room | None) -> None:
        self._preemptively_heading_to = room


class AsyncStudentQueue:

    queue: deque[QueueStudent]
    departed_queue_times: dict[discord.Member, tuple[datetime.datetime, int]]

    def __init__(self):
        self.queue = deque()
        self.departed_queue_times = {}

    def __len__(self) -> int:
        return len(self.queue)

    def __getitem__(self, index: int) -> QueueStudent:
        return self.queue[index]

    def __str__(self) -> str:
        member_list = ", ".join(str(qs.member) for qs in self.queue)
        return f"<AsyncStudentQueue queue='{member_list}'>"

    def __iter__(self):
        return iter(self.queue)

    def __contains__(self, student: QueueStudent | discord.Member) -> bool:
        if isinstance(student, discord.Member):
            return student in [qs.member for qs in self.queue]
        return student in self.queue

    def copy(self) -> AsyncStudentQueue:
        new_queue = AsyncStudentQueue()
        new_queue.queue = deque(self.queue)
        new_queue.departed_queue_times = self.departed_queue_times.copy()
        return new_queue

    def get_member(self, member: discord.Member) -> QueueStudent | None:
        for qs in self.queue:
            if qs.member == member:
                return qs
        return None

    async def append(self, queue_student: QueueStudent) -> None:
        self.queue.append(queue_student)
        await self.update_positions()

    async def insert(self, index: int, queue_student: QueueStudent) -> None:
        self.queue.insert(index, queue_student)
        await self.update_positions()

    def recently_left(self, member: discord.Member) -> bool:
        return (
            member in self.departed_queue_times
            and datetime.datetime.now() - self.departed_queue_times[member][0]
            < datetime.timedelta(minutes=2)
        )

    def recent_position(self, member: discord.Member) -> int:
        return self.departed_queue_times[member][1]

    async def remove(self, queue_student: QueueStudent) -> None:
        position = self.queue.index(queue_student)
        self.queue.remove(queue_student)
        await self.update_removed_member(queue_student, position)
        await self.update_positions()

    async def pop(self, index: int | None = None) -> QueueStudent:
        if index is None:
            student = self.queue.pop()
        else:
            student = self.queue[index]
            del self.queue[index]
        await self.update_removed_member(student, 0)
        await self.update_positions()
        return student

    async def popleft(self) -> QueueStudent:
        student = self.queue.popleft()
        await self.update_removed_member(student, 0)
        await self.update_positions()
        return student

    async def update_positions(self) -> None:
        for i, qs in enumerate(self.queue):
            await qs.change_suffix(f"(#{i + 1})", mute=True, only_if_necessary=True)

    async def update_removed_member(
        self,
        qs: QueueStudent,
        position: int,
    ) -> None:
        # Edit member's name and rest of queue
        member = qs.member
        self.departed_queue_times[member] = (datetime.datetime.now(), position)
        if member.voice is not None:
            await qs.change_suffix(None, mute=False)
        else:
            await qs.change_suffix(None)


class QueueManager:

    queue: AsyncStudentQueue
    metadata_mapping: MetadataMapping
    specifics: dict[discord.Member, list[str]]

    def __init__(self, bot: CoordinateBot, cog: OfficeHoursCog):
        self.bot = bot
        self.cog = cog
        self.queue = AsyncStudentQueue()
        self.metadata_mapping = MetadataMapping(self.bot)

    def __len__(self) -> int:
        return len(self.queue)

    def set_student_metadata(
        self,
        member: discord.Member,
        specifics: list[StaffMember],
        purpose: str,
        tried: str,
    ):
        logger.info(f"Setting metadata for {member}... (specifics: {specifics})")
        self.metadata_mapping[member] = StudentMetadata(
            member,
            specifics,
            purpose,
            tried,
        )

    def remove_student_metadata(self, member: discord.Member):
        logger.info(f"Removing metadata for {member}...")
        del self.metadata_mapping[member]

    async def add_student(
        self,
        member: discord.Member,
    ):
        qs = QueueStudent(member)
        if self.queue.recently_left(member):
            position = self.queue.recent_position(member)
            await self.queue.insert(position, qs)
            logger.info(
                f"{member} has been added to queue in position {position} because they recently left the queue.",
            )
        else:
            await self.queue.append(qs)
            logger.info(
                f"{member} has been added to queue... refreshing queue.",
            )

        await self.allocate()

    async def remove_student(self, member: discord.Member):
        qs = self.queue.get_member(member)
        if qs:
            await self.queue.remove(qs)
            with contextlib.suppress(KeyError):
                self.remove_student_metadata(member)
            if self.bot.oh_queue_role in member.roles:
                await member.remove_roles(self.bot.oh_queue_role)
        else:
            raise ValueError(f"Member {member} not found in queue.")

    async def move_student_position(self, member: discord.Member, position: int):
        if member not in self.queue:
            return
        qs = self.queue.get_member(member)
        if qs:
            await self.queue.remove(qs)
            await self.queue.insert(position, qs)
        else:
            raise ValueError(f"Member {member} not found in queue.")

    async def allocate(self) -> None:
        """
        Allocation algorithm, called on events where there might be the possibility
        of moving a student into a better spot.
        """
        open_rooms = self.cog.room_manager.open_rooms()
        logger.info(
            f"Ratios of open rooms: {[f'{room.staff}: {room.staff.ratio}' for room in open_rooms]}",
        )
        if not self.queue:
            return  # No one to allocate!
        mems_to_allocate = [qs for qs in self.queue if not qs._preemptively_heading_to]
        for room in open_rooms:
            print(room)
            for student in list(mems_to_allocate):
                metadata = self.metadata_mapping.get(
                    student.member,
                    StudentMetadata(student.member, [], "", ""),
                )
                print(metadata.specifics, room.staff, room.staff in metadata.specifics)
                if room.staff in metadata.specifics or not metadata.specifics:
                    self.bot.tasks.create_task(room.move_to(student, metadata))
                    mems_to_allocate.remove(student)
                    if room.state == RoomState.OPENING_SOON:
                        student.mark_as_heading_to(room)
                        after_delay = (
                            datetime.datetime.now().astimezone()
                            + datetime.timedelta(seconds=30)
                        )
                        self.bot.tasks.run_at(
                            after_delay,
                            f"unhead_to_{student.member.id}",
                            self.unhead_to,
                            student,
                        )
                    break

    async def unhead_to(self, student: QueueStudent):
        """
        This is used to make sure that the student actually ended up in the room
        they were hoping to go to.
        """
        if student in self.queue:
            student._preemptively_heading_to = None
            await self.allocate()

    async def add_student_to_waiting(
        self,
        member: discord.Member,
    ):
        async with self.bot.db_factory() as db:
            student = await db.get_student(member=member)
            if student is None:
                # hardcoded system ID for testing
                student_sys_id = "99999999"
                users = await self.bot.canvas.find_canvas_users(student_sys_id)
                # add checks to verify users not empty and not multiple users found like in registration.py?
                # add enrollment check like registration.py?
                await db.add_student(
                    member=member,
                    canvas_id=users[0]["id"],
                    student_sys_id=int(student_sys_id),
                    official_name=users[0]["name"],
                    chosen_name=member.nick,
                )

    def _get_member_position_from_name(self, member: discord.Member) -> int:
        positions = re.findall(r"\(\#(-?\d+)\)", member.display_name)
        if not positions:
            return 9999  # Arbitrary large number to put at the end of the queue
        return int(positions[0]) - 1

    async def build_queue(self):
        """
        Rebuilds the internal queue from the queue in Discord.
        """
        logger.warn("In-person queue appears to be lost... will be refreshing now.")
        members = self.bot.waiting_channel.members.copy()
        members.sort(key=self._get_member_position_from_name)

        queue = AsyncStudentQueue()
        for i, member in enumerate(members):
            if member is not None:
                await queue.append(
                    QueueStudent(member=member),
                )
                self.set_student_metadata(member, [], "", "")
                logger.info(
                    f"{member} has been added to in-person queue in position #{i + 1}",
                )
        self.queue = queue
        logger.info(f"Re-built queue: {self.queue}")
        await self.allocate()

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import discord

from ..constants import VC_CLOSING_SUFFIX
from ..db import StaffMember, Timeslot

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from .queue import QueueStudent, StudentMetadata


logger = logging.getLogger(__name__)


class RoomState(Enum):
    OPEN = 0
    OPENING_SOON = 1
    STUDENT_RESERVED_OPENING_SOON = 2
    CLOSED = 3
    INACCESSIBLE = 4

    @classmethod
    def from_voice_channel(cls, voice_channel: discord.VoiceChannel):
        staff = []
        students = []
        for member in voice_channel.members:
            staff_role = discord.utils.get(member.roles, name="TA/PM")
            student_role = discord.utils.get(member.roles, name="Student")
            if staff_role:
                staff.append(member)
            elif student_role:
                students.append(member)
        if len(staff) == 0:
            return cls.INACCESSIBLE
        if staff and len(students) == 0:
            return cls.OPEN
        return cls.CLOSED


def _event_factory() -> asyncio.Event:
    event = asyncio.Event()
    event.set()
    return event


@dataclass
class Room:
    channel: discord.VoiceChannel
    state: RoomState
    staff: StaffMember
    timeslot: Timeslot
    _delay_over: asyncio.Event = field(default_factory=_event_factory)

    def __str__(self) -> str:
        return f"Room<(channel = {self.channel.name}, state = {self.state.name})>"

    __repr__ = __str__

    def __hash__(self) -> int:
        return hash(self.channel.id)

    @property
    def staff_member(self) -> discord.Member:
        staff = [m for m in self.channel.members if self.staff.id == m.id]
        return staff[0]

    async def move_to(self, student: QueueStudent, metadata: StudentMetadata) -> None:
        if self.state == RoomState.OPENING_SOON:
            self.state = RoomState.STUDENT_RESERVED_OPENING_SOON
            logger.info(
                f"Going to move {student.member.name} to {self.staff.name} after waiting for the room's delay...",
            )
            await self.staff_member.send(
                embed=metadata.entry_embed(moving_to=self.staff, with_delay=True),
            )
            await self._delay_over.wait()
        else:
            await self.staff_member.send(
                embed=metadata.entry_embed(moving_to=self.staff, with_delay=False),
            )

        # RoomState.CLOSED would indicate that something happened that closed
        # the room externally and the student could not be moved. Potentially a
        # break as well, which is okay!
        if self.students() and self.state is not RoomState.CLOSED:
            logger.warning(
                "Expected to move a student into a room, but it's not empty.",
            )
            return

        try:
            await student.member.move_to(self.channel)
            self.state = RoomState.CLOSED
        except discord.HTTPException:
            logger.warning(
                f"Failed to move {student.member.name} to {self.channel.name}, they must have left voice.",
            )
            self.state = (
                RoomState.OPEN
            )  # We failed to move the student, let's keep it open

    @property
    def closing(self) -> bool:
        return datetime.datetime.now().astimezone() > self.timeslot.end

    @property
    def ready_for_students(self) -> bool:
        return (
            self.state in (RoomState.OPEN, RoomState.OPENING_SOON) and not self.closing
        )

    async def edit_suffix(self, suffix: str) -> bool:
        ch_name = self.channel.name.removesuffix(suffix).strip()
        ch_name = f"{ch_name} {suffix}"
        if self.channel.name != ch_name:
            await self.channel.edit(name=ch_name)
            return True
        return False

    def students(self) -> list[discord.Member]:
        res = []
        for member in self.channel.members:
            student_role = discord.utils.get(member.roles, name="Student")
            if student_role:
                res.append(member)
        return res


class RoomManager:

    rooms: dict[int, Room]

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.rooms = {}

    def __len__(self) -> int:
        return len(self.rooms)

    async def load_rooms(self) -> None:
        cog = self.bot.office_hours_cog
        for channel in cog.channel_manager.open_channels():
            doc = await self.bot.staff_doc_from_vc(channel)
            timeslot = doc.active_timeslot()
            if timeslot:
                await self.open_room(doc, timeslot)

    async def open_room(self, member: StaffMember, timeslot: Timeslot) -> Room:
        cog = self.bot.office_hours_cog
        vc = self.bot.staff_member_channel(member.name)
        if not vc:
            vc = await cog.channel_manager.create_voice_channel(member, timeslot)
        default_state = RoomState.from_voice_channel(vc)
        if member.breaking_until:
            default_state = RoomState.CLOSED
        self.rooms[member.id] = Room(vc, default_state, member, timeslot)
        await self.bot.office_hours_cog.queue.allocate()
        logger.info(
            f"Opened room for {member.name} at {timeslot.start}: {self.rooms[member.id]}",
        )
        return self.rooms[member.id]

    async def close_room(
        self,
        member: StaffMember,
        voice_channel: discord.VoiceChannel,
    ) -> None:
        logger.info(f"Closing room for {member.name}")
        cog = self.bot.office_hours_cog
        # If there are students inside, we cannot close it yet, let's just edit
        # the name and move on
        if len(voice_channel.members) > 1:
            # We still need to keep this room open if it does not exist
            if member.id not in self.rooms:
                room = await self.open_room(member, member.timeslots[0])
            else:
                room = self.rooms[member.id]
                changed = await room.edit_suffix(f"{VC_CLOSING_SUFFIX}")
                if changed:
                    file_num = random.randint(1, 6)
                    closing_time_name = f"assets/closing{file_num}.mp3"
                    self.bot.tasks.create_task(
                        cog.channel_manager.play(voice_channel, closing_time_name),
                    )
            # logger.info(f"Ready to close channel for {name}, but people are still inside.",)
            return
        else:
            file_num = random.randint(1, 6)
            closing_time_name = f"assets/closing{file_num}.mp3"
            await cog.channel_manager.play(voice_channel, closing_time_name)
            await cog.channel_manager.close_voice_channel(member, voice_channel)
            with contextlib.suppress(KeyError):
                self.rooms.pop(member.id)

    def update_state(self, member: discord.Member, state: RoomState) -> None:
        logger.info(f"Updating state for {member.name} to {state.name}")
        self.rooms[member.id].state = state

    async def finish_delay(self, member: discord.Member):
        self.update_state(member, RoomState.OPENING_SOON)
        cog = self.bot.office_hours_cog
        room = self.get_room(member)
        if room:
            room._delay_over.clear()
        await cog.queue.allocate()
        await asyncio.sleep(30)
        if room:
            if room.state == RoomState.OPENING_SOON:
                self.update_state(member, RoomState.OPEN)
            room._delay_over.set()

    def get_room(self, member: discord.Member) -> Room | None:
        return self.rooms.get(member.id, None)

    def open_rooms(self) -> list[Room]:
        return sorted(
            [room for room in self.rooms.values() if room.ready_for_students],
            key=lambda room: (room.state.value, room.staff.ratio),
        )

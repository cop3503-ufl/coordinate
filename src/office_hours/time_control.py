from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from .rooms import Room


logger = logging.getLogger(__name__)


class TimeControl:
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.joined_at = {}

    def allotted_time(self) -> datetime.timedelta:
        """
        Determines the amount of time each student should receive based on the
        size of the queue.
        """
        queue = self.bot.office_hours_cog.queue
        rooms = self.bot.office_hours_cog.room_manager
        if not rooms:
            return datetime.timedelta()
        ratio = len(queue) / len(rooms) or 1
        return datetime.timedelta(hours=max(1 / ratio, 0.1))

    async def recalculate_times(self) -> None:
        for room, _ in self.joined_at.items():
            self.schedule_reminder(room)

    async def on_queue_add(self) -> None:
        await self.recalculate_times()

    async def on_student_join(self, _: discord.Member, room: Room) -> None:
        if len(room.students()) == 1:
            self.joined_at[room] = datetime.datetime.now()
        self.schedule_reminder(room)

    async def on_student_leave(self, _: discord.Member, room: Room) -> None:
        if len(room.students()) == 0:
            self.joined_at.pop(room, None)
        self.cancel_reminder(room)

    def task_name(self, room: Room) -> str:
        return f"room_reminder:{room.channel.id}"

    def schedule_reminder(self, room: Room):
        self.cancel_reminder(room)
        future = self.joined_at[room] + self.allotted_time()
        self.bot.tasks.run_at(
            future,
            self.task_name(room),
            self.run_reminder,
            room,
        )

    def cancel_reminder(self, room: Room):
        self.bot.tasks.remove_task(self.task_name(room))

    async def run_reminder(self, room: Room):
        await self.bot.office_hours_cog.channel_manager.play(
            room.channel,
            "assets/move_on.mp3",
        )

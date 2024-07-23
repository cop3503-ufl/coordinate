from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..views import CoordinateBotView

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from ..db import StaffMember
    from .components import StaffMemberSelect


class StaffMemberView(CoordinateBotView):
    chosen_value: str | None

    def __init__(
        self,
        bot: CoordinateBot,
        schedule: list[StaffMember],
        select_cls: type[StaffMemberSelect],
    ):
        self.bot = bot
        self.chosen_value = None
        super().__init__()
        schedules = [schedule[i : i + 25] for i in range(0, len(schedule), 25)]
        # Names need to be split up
        for smallschedule in schedules:
            options: list[discord.SelectOption] = []
            for staff_member in smallschedule:
                options.append(
                    discord.SelectOption(
                        label=staff_member.name,
                        emoji=staff_member.emoji,
                        description=f"{len(staff_member.upcoming_timeslots(exc = False))} upcoming timeslots",
                    ),
                )
            self.add_item(select_cls(bot, self, options))

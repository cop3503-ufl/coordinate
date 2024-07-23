from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from .views import StaffMemberView


class StaffMemberSelect(discord.ui.Select):
    def __init__(
        self,
        bot: CoordinateBot,
        view: StaffMemberView,
        options: list[discord.SelectOption],
    ):
        self.bot = bot
        self.parent_view = view
        firstletter = options[0].label[0]
        lastletter = options[-1].label[0]
        placeholder = f"First names from {firstletter}-{lastletter}..."
        super().__init__(options=options, placeholder=placeholder)

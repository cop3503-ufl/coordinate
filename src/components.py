import inspect
from collections.abc import Callable
from typing import TypeVar

import discord
from discord import ButtonStyle, Emoji, PartialEmoji
from discord.ui import Button, View
from discord.ui.item import ItemCallbackType

from .utils import emoji_header, space_prefix

V = TypeVar("V", bound=View, covariant=True)


def emoji_button(
    emoji: str | Emoji | PartialEmoji,
    label: str,
    *,
    style: ButtonStyle = ButtonStyle.secondary,
    custom_id: str | None = None,
    disabled: bool = False,
    row: int | None = None,
) -> Callable[[ItemCallbackType[V, Button[V]]], Button[V]]:
    def decorator(
        func: ItemCallbackType[V, Button[V]],
    ) -> ItemCallbackType[V, Button[V]]:
        # Set some parameters automatically
        if not inspect.iscoroutinefunction(func):
            raise TypeError("button function must be a coroutine function")

        func.__discord_ui_model_type__ = Button  # type: ignore
        func.__discord_ui_model_kwargs__ = {  # type: ignore
            "style": style,
            "custom_id": custom_id,
            "url": None,
            "disabled": disabled,
            "label": space_prefix(label),
            "emoji": emoji,
            "row": row,
        }
        return func

    return decorator  # type: ignore


class EmojiEmbed(discord.Embed):
    def add_field(  # type: ignore
        self,
        emoji: str,
        name: str,
        value: str,
        *,
        inline: bool = False,
    ) -> None:
        super().add_field(name=emoji_header(emoji, name), value=value, inline=inline)

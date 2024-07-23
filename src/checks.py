"""
Decorator checks for individual interaction items. This allows us to raise specific
exceptions when certain interaction criteria are not met. interaction_check(),
the most commonly used approach given by the library, simply causes the interaction
to not be responded to, which is confusing for the user.

Each function is a decorator that should wrap a function taking in an interaction
and returning nothing. The check should simply check some criteria and then run
the function as normal.
"""

import functools
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import discord
from discord.app_commands import MissingAnyRole, NoPrivateMessage
from discord.interactions import Interaction

from .exceptions import StudentsOnly

T = TypeVar("T", bound=discord.ui.Item)
S = TypeVar("S", bound=discord.abc.Snowflake)


# TODO: Clean this up, support any snowflake instead of just MemberInteraction
InteractionOnly = Callable[[Any, Interaction], Coroutine[Any, Any, None]]
MemberInteraction = Callable[
    [Any, Interaction, discord.Member],
    Coroutine[Any, Any, None],
]
ItemInteraction = Callable[
    [Any, Interaction, discord.ui.Item],
    Coroutine[Any, Any, None],
]

InteractionCallback = InteractionOnly | MemberInteraction | ItemInteraction


def is_student(func: InteractionCallback):
    @functools.wraps(func)  # type: ignore
    async def wrapper(
        self,
        interaction: discord.Interaction,
        *args,
    ) -> None:
        if isinstance(interaction.user, discord.User):
            raise NoPrivateMessage

        if discord.utils.get(interaction.user.roles, name="Student") is None:
            raise StudentsOnly

        return await func(self, interaction, *args)

    return wrapper


def is_staff(func: InteractionCallback):
    @functools.wraps(func)  # type: ignore
    async def wrapper(self: Any, interaction: discord.Interaction, *args):
        if isinstance(interaction.user, discord.User):
            raise NoPrivateMessage

        if (
            discord.utils.get(interaction.user.roles, name="TA/PM") is None
            and discord.utils.get(interaction.user.roles, name="Professor") is None
        ):
            raise MissingAnyRole(["TA/PM", "Professor"])

        await func(self, interaction, *args)

    return wrapper

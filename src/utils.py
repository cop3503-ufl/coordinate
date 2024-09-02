from __future__ import annotations

import datetime
from collections.abc import Iterable
from typing import TypeVar

import arrow
import dateutil.parser
import parsedatetime
from discord.ext import commands
from discord.ui import View

from .exceptions import FormValidationError

V = TypeVar("V", bound=View, covariant=True)
T = TypeVar("T")


class DateConverter(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> datetime.date:
        if argument.lower() == "today":
            return datetime.date.today()
        if argument.lower() == "tomorrow":
            return datetime.date.today() + datetime.timedelta(days=1)
        if argument.lower() == "yesterday":
            return datetime.date.today() - datetime.timedelta(days=1)
        try:
            return datetime.datetime.strptime(argument, "%Y-%m-%d").date()
        except ValueError:
            raise commands.BadArgument(
                "Date must be in the format MM/DD/YYYY or today/tomorrow/yesterday",
            )


def parse_datetime(dt: str, allow_past: bool = False) -> datetime.datetime:
    """
    Attempts to parse a datetime string into a datetime. If the conversion cannot be performed, a ValueError is raised.

    Eastern time is assumed if no timezone is provided.

    Example inputs:
        - "2021-08-30T12:00:00Z"
        - "yesterday 2PM"
        - "1/7 4PM"
        - "tomorrow 8:30AM"
        - "4PM tomorrow"
    """
    res = None
    try:
        res = datetime.datetime.fromisoformat(dt).astimezone()
    except (dateutil.parser.ParserError, ValueError):
        try:
            arw = arrow.utcnow()
            res = arw.dehumanize(dt).datetime
        except ValueError:
            time, parse_status = parsedatetime.Calendar(
                version=parsedatetime.VERSION_CONTEXT_STYLE,
            ).parse(dt)
            res = datetime.datetime(*time[:6])
            res = res.astimezone()
            if not res:
                raise FormValidationError(dt, datetime.datetime)
            if not parse_status.hasDate:  # type: ignore
                raise FormValidationError(
                    dt,
                    datetime.datetime,
                    "Found a time component, but no date component. Not assuming which date you are referring to.",
                )
            if not parse_status.hasTime:  # type: ignore
                raise FormValidationError(
                    dt,
                    datetime.datetime,
                    "Found a date component, but no time component. What time are you referring to?",
                )
    if res <= datetime.datetime.now().astimezone() and not allow_past:
        raise FormValidationError(
            dt,
            datetime.datetime,
            "The date and time must be in the future.",
        )
    return res


def parse_time(time: str) -> datetime.time:
    """
    Parse a time string into a datetime.time object.

    Example inputs:
        - "12:00PM"
        - "3:30 AM"
        - "4:00"
    """
    time_parsed, parse_status = parsedatetime.Calendar(
        version=parsedatetime.VERSION_CONTEXT_STYLE,
    ).parse(time)
    res = datetime.datetime(*time_parsed[:6])
    res = res.astimezone()
    if parse_status.hasDate:  # type: ignore
        raise FormValidationError(
            time,
            datetime.time,
            "Found a date component, please only include a time (like 4:00PM).",
        )
    if not parse_status.hasTime:  # type: ignore
        raise FormValidationError(time, datetime.time, "No time component found.")
    return res.time()


def chunks(iterable: Iterable[T], size: int) -> Iterable[list[T]]:
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def space_prefix(string: str) -> str:
    return f"​  {string}"


def emoji_header(emoji: str, title: str) -> str:
    return f"{emoji} __{title}__"


def emoji_given_pronouns(pronouns: str, name: str | None = None) -> str:
    emoji = "🧑‍💻"
    if ("Professor" in pronouns) or (name and "Professor" in name):
        emoji = "👩‍🏫"
    if pronouns == "he":
        emoji = "👨‍💻"
    elif pronouns == "she":
        emoji = "👩‍💻"
    return emoji

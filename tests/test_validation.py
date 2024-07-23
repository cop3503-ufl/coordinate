import datetime

import pytest

from src.exceptions import FormValidationError
from src.utils import parse_datetime, parse_time


def test_parse_dt():
    def assemble(days_offset: int, hour: int, minute: int) -> datetime.datetime:
        return datetime.datetime.combine(
            today + datetime.timedelta(days=days_offset),
            datetime.time(hour, minute),
        ).astimezone()

    def next_instance_of(
        month: int,
        day: int,
        hour: int,
        minute: int,
    ) -> datetime.datetime:
        now = datetime.datetime.now()
        year = now.year
        if now.month > month or (now.month == month and now.day > day):
            year += 1
        return datetime.datetime(year, month, day, hour, minute).astimezone()

    # Natural language
    now = datetime.datetime.now()
    today = now.date()

    # Obvious isoformats
    assert (
        parse_datetime("2029-08-30T12:00:00Z")
        == datetime.datetime(
            2029,
            8,
            30,
            12,
            0,
            tzinfo=datetime.timezone.utc,
        ).astimezone()
    )
    assert (
        parse_datetime("2029-08-30T12:00:00")
        == datetime.datetime(
            2029,
            8,
            30,
            12,
            0,
        ).astimezone()
    )
    assert (
        parse_datetime("04-04-2029 16:00:00")
        == datetime.datetime(
            2029,
            4,
            4,
            16,
            0,
        ).astimezone()
    )

    assert parse_datetime("tomorrow 8:30AM") == assemble(1, 8, 30)
    assert parse_datetime("4PM tomorrow") == assemble(1, 16, 0)
    assert parse_datetime("December 31st 11:59PM") == next_instance_of(12, 31, 23, 59)
    assert parse_datetime("2/11 4PM") == next_instance_of(2, 11, 16, 0)
    assert parse_datetime("2/11 4:30PM") == next_instance_of(2, 11, 16, 30)
    assert parse_datetime("5/14         12:30PM") == next_instance_of(5, 14, 12, 30)
    assert parse_datetime("MAY 15 11AM") == next_instance_of(5, 15, 11, 0)
    assert parse_datetime("April 4th 16:00") == next_instance_of(4, 4, 16, 0)

    # Missing a time component
    with pytest.raises(FormValidationError):
        parse_datetime("yesterday")
        parse_datetime("tomorrow")
        parse_datetime("2/11")
        parse_datetime("5/14")

    # Missing a date component
    with pytest.raises(FormValidationError):
        parse_datetime("4PM")
        parse_datetime("4:30PM")
        parse_datetime("12:30PM")
        parse_datetime("11AM")

    # Not a date
    with pytest.raises(FormValidationError):
        parse_datetime("not a date")
        parse_datetime("1")
        parse_datetime("1243445")

    # Back in time
    with pytest.raises(FormValidationError):
        parse_datetime("2024-01-01T00:00:00Z")
        parse_datetime("April 4th, 2024 11:59PM")
        parse_datetime("yesterday 2PM")


def test_parse_time():
    assert parse_time("12:00PM") == datetime.time(12, 00)
    assert parse_time("3:30 AM") == datetime.time(3, 30)
    assert parse_time("4:00") == datetime.time(4, 0)
    assert parse_time("16:45") == datetime.time(16, 45)
    assert parse_time("4:30PM") == datetime.time(16, 30)
    assert parse_time("4PM") == datetime.time(16, 0)
    assert parse_time("08:30") == datetime.time(8, 30)

    # not a time
    with pytest.raises(FormValidationError):
        parse_time("not a time")
        parse_time("1")
        parse_time("1243445")

    # includes a date
    with pytest.raises(FormValidationError):
        parse_time("September 4th 4:00PM")
        parse_time("4:00PM on September 4th")
        parse_time("4:00PM 9/4")
        parse_time("4:00PM 9/4/2024")
        parse_time("2024-09-04T16:00:00Z")

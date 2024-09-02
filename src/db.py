from __future__ import annotations

import datetime
import logging
from collections.abc import Sequence
from enum import Enum, auto
from typing import TYPE_CHECKING, TypeVar
from zoneinfo import ZoneInfo

import discord
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    String,
    join,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .env import POSTGRES_URL
from .exceptions import (
    NoFutureTimeslots,
    OfficeHoursRequestNotFound,
    SessionNotFound,
    StaffMemberNotFound,
)
from .semesters import semester_given_date

if TYPE_CHECKING:
    from .bot import CoordinateBot


logger = logging.getLogger(__name__)


R = TypeVar("R", bound="OfficeHoursRequest")


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Gender(Enum):
    M = auto()
    F = auto()
    X = auto()


class StaffMemberRemindersSetting(Enum):
    ALWAYS = auto()
    ONLY_AWAY = auto()
    ONLY_OFFLINE = auto()
    NEVER = auto()


class TimeslotMethod(Enum):
    DISCORD = ("Discord", "<:discord:1214076716062867477>")
    INPERSON = ("In Person", "<:inperson:1214076859487363123>")
    ZOOM = ("Zoom", "<:zoom:1214076422180700200>")
    TEAMS = ("Microsoft Teams", "<:teams:1214076600979693628>")

    def __init__(self, display_name: str, emoji: str):
        self.display_name = display_name
        self.emoji = emoji

    def to_option(self) -> discord.SelectOption:
        return discord.SelectOption(
            label=self.display_name,
            emoji=self.emoji,
        )


class OfficeHoursRequestType(Enum):
    ANY = auto()
    ADD = auto()
    MOVE = auto()
    REMOVE = auto()
    ADD_ROUTINE = auto()
    REMOVE_ROUTINE = auto()


class OfficeHoursSessionStatus(Enum):
    WAITING = auto()
    ACTIVE = auto()
    LEFT_QUEUE = auto()
    COMPLETED = auto()
    REMOVED = auto()


class StaffMember(Base):
    __tablename__ = "staff"
    __table_args__ = (
        # Ensure that autoaccept_delay is positive
        CheckConstraint("autoaccept_delay >= 0"),
    )

    id: Mapped[int] = mapped_column("id", BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column()
    gender: Mapped[Gender] = mapped_column()
    professor: Mapped[bool] = mapped_column()
    routines: Mapped[list[Routine]] = relationship(
        back_populates="staff",
        lazy="selectin",
    )
    timeslots: Mapped[list[Timeslot]] = relationship(
        back_populates="staff",
        lazy="selectin",
    )
    autoaccept_delay: Mapped[int] = mapped_column()
    reminders: Mapped[StaffMemberRemindersSetting] = mapped_column()
    breaking_until: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    desiring_break: Mapped[int | None] = mapped_column(
        nullable=True,
    )
    seconds_spent: Mapped[float] = mapped_column(default=0)
    seconds_without: Mapped[float] = mapped_column(default=0)
    oh_requests: Mapped[list[OfficeHoursRequest]] = relationship(
        back_populates="staff",
        lazy="selectin",
    )
    hosted_sessions: Mapped[list[OfficeHoursSession]] = relationship(
        back_populates="staff",
        lazy="selectin",
    )
    llama_invokes: Mapped[list[LlamaResponse]] = relationship(
        back_populates="staff",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<StaffMember(id={self.id}, name={self.name!r})>"

    __str__ = __repr__

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other) -> bool:
        if not isinstance(other, StaffMember):
            return False
        return self.id == other.id

    @property
    def pronouns(self) -> str:
        """
        Returns his, her, or their depending on the staff member's gender.
        """
        d = {
            Gender.M: "his",
            Gender.F: "her",
        }
        return d.get(self.gender, "their")

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def emoji(self) -> str:
        emoji = "🧑‍💻"
        if self.professor:
            emoji = "👩‍🏫"
        if self.gender == Gender.M:
            emoji = "👨‍💻"
        elif self.gender == Gender.F:
            emoji = "👩‍💻"
        return emoji

    @property
    def royal_title(self) -> str:
        d = {
            Gender.M: "King",
            Gender.F: "Queen",
        }
        return d.get(self.gender, "Royalty")

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    @property
    def ratio(self) -> float:
        return self.seconds_spent / (self.seconds_spent + self.seconds_without)

    def active_timeslot(self) -> Timeslot | None:
        for timeslot in self.timeslots:
            if timeslot.start <= datetime.datetime.now().astimezone() <= timeslot.end:
                return timeslot
        return None

    def upcoming_timeslots(self, *, exc: bool = True) -> list[Timeslot]:
        ts = [
            timeslot
            for timeslot in self.timeslots
            if timeslot.end > datetime.datetime.now().astimezone()
        ]
        ts.sort(key=lambda t: t.start)
        if exc and not ts:
            raise NoFutureTimeslots(self)
        return ts

    def next_timeslot(self) -> Timeslot | None:
        try:
            return self.upcoming_timeslots(exc=False)[0]
        except IndexError:
            return None


class Student(Base):
    __tablename__ = "students"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    canvas_id: Mapped[int] = mapped_column()
    student_sys_id: Mapped[int] = mapped_column()
    official_name: Mapped[str] = mapped_column()
    chosen_name: Mapped[str] = mapped_column()
    attended_sessions: Mapped[list[OfficeHoursSession]] = relationship(
        back_populates="student",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Student(discord_id={self.discord_id}, official_name={self.official_name!r})>"


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    ta_name: Mapped[str] = mapped_column(String, unique=True)
    section_names: Mapped[list[str]] = mapped_column(ARRAY(String))

    def __init__(self, ta_name: str, section_names: list[str]):
        self.ta_name = ta_name
        self.section_names = section_names


class Routine(Base):
    __tablename__ = "routines"

    id: Mapped[int] = mapped_column(primary_key=True)
    weekday: Mapped[int] = mapped_column()
    time: Mapped[datetime.time] = mapped_column()
    length: Mapped[float] = mapped_column()  # hours
    method: Mapped[TimeslotMethod] = mapped_column()
    room: Mapped[str | None] = mapped_column()
    meeting_url: Mapped[str | None] = mapped_column()
    staff_id = mapped_column(ForeignKey("staff.id"))
    staff: Mapped[StaffMember] = relationship(back_populates="routines")
    timeslots: Mapped[list[Timeslot]] = relationship(
        back_populates="routine",
        lazy="selectin",
    )
    requests: Mapped[list[RemoveRoutineOfficeHoursRequest]] = relationship(
        back_populates="routine",
        lazy="selectin",
    )

    def __init__(
        self,
        weekday: int,
        time: datetime.time,
        length: float,
        staff: StaffMember | None,
        method: TimeslotMethod,
        *,
        room: str | None = None,
        meeting_url: str | None = None,
    ) -> None:
        self.weekday = weekday
        self.time = time
        if staff is not None:
            self.staff = staff
        self.length = length
        self.method = method
        self.room = room
        self.meeting_url = meeting_url

    def generate_timeslots(
        self,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ) -> tuple[list[Timeslot], list[Timeslot]]:
        current_semester = semester_given_date(
            datetime.datetime.now(),
            next_semester=True,
        )
        if not current_semester:
            raise ValueError("No current semester")

        if not start_date:
            start_date = max(current_semester.start, datetime.date.today())

        if not end_date:
            end_date = current_semester.end

        start_weekday = start_date.weekday()
        diff = self.weekday - start_weekday
        if diff < 0:
            diff += 7
        first_day = start_date + datetime.timedelta(days=diff)

        # Generate list of times
        times: list[Timeslot] = []
        excluded_times: list[Timeslot] = []
        current_date = datetime.datetime.combine(
            first_day,
            self.time,
            tzinfo=ZoneInfo("US/Eastern"),
        )
        while current_date.date() <= end_date:
            in_break = any(
                brk[0] <= current_date.date() <= brk[1]
                for brk in current_semester.breaks
            )
            if in_break:
                excluded_times.append(
                    Timeslot(
                        start=current_date,
                        end=current_date + datetime.timedelta(hours=self.length),
                        method=self.method,
                        staff=self.staff,
                        routine=self,
                        room=self.room,
                        meeting_url=self.meeting_url,
                    ),
                )
            else:
                times.append(
                    Timeslot(
                        start=current_date,
                        end=current_date + datetime.timedelta(hours=self.length),
                        method=self.method,
                        routine=self,
                        staff=self.staff,
                        room=self.room,
                        meeting_url=self.meeting_url,
                    ),
                )
            current_date += datetime.timedelta(days=7)
        return times, excluded_times

    def __repr__(self) -> str:
        return f"<Routine(id={self.id}, name=)>"

    def __len__(self) -> int:
        return len(self.timeslots)


class Timeslot(Base):
    __tablename__ = "timeslots"
    __table_args__ = (
        # Ensure that end time is after start time
        CheckConstraint("end_time > start_time", name="end_after_start"),
        # Ensure that if method is set to INPERSON, room is set to, otherwise room should be null
        CheckConstraint(
            "method = 'INPERSON' AND room IS NOT NULL OR method != 'INPERSON' AND room IS NULL",
            name="room_if_inperson",
        ),
        # Ensure that meeting_url is set if method is not INPERSON or DISCORD
        CheckConstraint(
            "method = 'INPERSON' OR method = 'DISCORD' OR meeting_url IS NOT NULL",
            name="meeting_url_if_needed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    routine_id = mapped_column(None, ForeignKey("routines.id"), nullable=True)
    routine: Mapped[Routine | None] = relationship(
        "Routine",
        back_populates="timeslots",
        lazy="selectin",
    )
    _start: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        name="start_time",
    )
    _end: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        name="end_time",
    )
    method: Mapped[TimeslotMethod] = mapped_column()
    room: Mapped[str | None] = mapped_column(default=None)
    meeting_url: Mapped[str | None] = mapped_column(default=None)
    staff_id = mapped_column(ForeignKey("staff.id"))
    staff: Mapped[StaffMember] = relationship(
        back_populates="timeslots",
        lazy="selectin",
    )
    requests: Mapped[list[OfficeHoursRequest]] = relationship(
        back_populates="timeslot",
        lazy="selectin",
    )

    @property
    def length(self) -> datetime.timedelta:
        return self._end - self._start

    @property
    def end(self) -> datetime.datetime:
        return self._end.replace(tzinfo=datetime.timezone.utc).astimezone()

    @end.setter
    def end(self, value: datetime.datetime) -> None:
        self._end = value.astimezone(datetime.timezone.utc)

    @property
    def start(self) -> datetime.datetime:
        return self._start.replace(tzinfo=datetime.timezone.utc).astimezone()

    @start.setter
    def start(self, value: datetime.datetime) -> None:
        self._start = value.astimezone(datetime.timezone.utc)

    @property
    def schedule_formatted(self) -> str:
        """
        Returns the string representation of the timeslot suited for the
        office hours schedule.
        """
        start = discord.utils.format_dt(self.start, "t")
        end = discord.utils.format_dt(self.end, "t")

        time_string = ""
        if self.staff.breaking_until:
            relative = discord.utils.format_dt(self.staff.breaking_until, "R")
            time_string = f"(break ends {relative})"
        elif self.start <= datetime.datetime.now().astimezone() <= self.end:
            relative = discord.utils.format_dt(self.end, "R")
            time_string = f"(ends {relative})"
        elif (self.start - datetime.datetime.now().astimezone()) < datetime.timedelta(
            hours=24,
        ):
            relative = discord.utils.format_dt(self.start, "R")
            time_string = f"(begins {relative})"

        name_string = self.staff.name
        if self.method == TimeslotMethod.INPERSON:
            name_string += f" (room {self.room})"
        elif self.method != TimeslotMethod.DISCORD:
            name_string += f" (through {self.method.name.title()})"
        return f"{self.staff.emoji} **{name_string}**: {start} - {end} {time_string}"

    def _hour_min_calc(self, future_time: datetime.datetime) -> tuple[float, float]:
        diff = future_time - datetime.datetime.now().astimezone()
        if diff.total_seconds() < 0:
            return 0, 0
        hours, min = diff.total_seconds() // 3600, (diff.total_seconds() % 3600) // 60
        return hours, min

    def _hour_min_str_(self, hour: float, minute: float) -> str:
        if hour > 1:
            return f"in {hour:.0f} hours"
        elif hour == 1:
            return "in 1 hour"
        elif minute > 1:
            return f"in {minute:.0f} minutes"
        elif minute == 1:
            return "in 1 minute"
        else:
            return "now"

    @property
    def relative_start(self) -> str:
        res = ""
        if self.start <= datetime.datetime.now().astimezone() <= self.end:
            hours, min = self._hour_min_calc(self.end)
            res = f"(ends {self._hour_min_str_(hours, min)})"
        elif (self.start - datetime.datetime.now().astimezone()) < datetime.timedelta(
            hours=24,
        ):
            hours, min = self._hour_min_calc(self.start)
            res = f"(starts {self._hour_min_str_(hours, min)})"
        return res

    @property
    def select_option(self) -> discord.SelectOption:
        return discord.SelectOption(
            emoji="⏰",
            value=str(self.id),
            label=f"{self.start.strftime('%a, %B %-d')}",
            description=f"{self.start.strftime('%-I:%M%p')} - {self.end.strftime('%-I:%M%p')} {self.relative_start}",
        )

    def __init__(
        self,
        start: datetime.datetime,
        end: datetime.datetime,
        method: TimeslotMethod,
        staff: StaffMember,
        *,
        routine: Routine | None,
        room: str | None = None,
        meeting_url: str | None = None,
    ):
        self._start = start.astimezone(datetime.timezone.utc)
        self._end = end.astimezone(datetime.timezone.utc)
        self.routine = routine
        self.method = method
        self.staff = staff
        if room:
            self.room = room
        if meeting_url:
            self.meeting_url = meeting_url


class LatePass(Base):
    __tablename__ = "latepass"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[str] = mapped_column()
    assignment_name: Mapped[str] = mapped_column()

    def __init__(self, student_id: int, assignment_id: str, assignment_name: str):
        self.id = student_id
        self.assignment_id = assignment_id
        self.assignment_name = assignment_name


class LlamaResponse(Base):
    __tablename__ = "llama"

    id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    staff_id = mapped_column(ForeignKey("staff.id"))
    date = mapped_column(TIMESTAMP(timezone=True))
    staff: Mapped[StaffMember] = relationship(
        back_populates="llama_invokes",
        lazy="selectin",
    )
    prompt: Mapped[str] = mapped_column()
    response: Mapped[str] = mapped_column()
    accepted: Mapped[bool] = mapped_column()
    reason: Mapped[str | None] = mapped_column()

    def __init__(
        self,
        id: int | None,
        channel_id: int,
        staff_id: int,
        date: datetime.datetime,
        prompt: str,
        response: str,
        accepted: bool,
        reason: str | None = None,
    ):
        self.id = id
        self.channel_id = channel_id
        self.staff_id = staff_id
        self.date = date
        self.prompt = prompt
        self.response = response
        self.accepted = accepted
        self.reason = reason


class OfficeHoursSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    preferences: Mapped[list[str]] = mapped_column(ARRAY(String))
    _entered: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        name="entered",
    )
    _start: Mapped[None | datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        name="start_time",
        nullable=True,
    )
    _end: Mapped[None | datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        name="end_time",
        nullable=True,
    )
    _left_queue: Mapped[None | datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        name="left_queue",
        nullable=True,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.discord_id"),
        nullable=True,
    )
    student: Mapped[Student] = relationship(
        back_populates="attended_sessions",
        lazy="selectin",
    )
    staff_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id"),
        nullable=True,
    )
    staff: Mapped[StaffMember | None] = relationship(
        back_populates="hosted_sessions",
        lazy="selectin",
    )
    status: Mapped[OfficeHoursSessionStatus] = mapped_column()

    def __init__(
        self,
        preferences: list[str],
        entered: datetime.datetime,
        start: datetime.datetime | None,
        end: datetime.datetime | None,
        left_queue: datetime.datetime | None,
        student_id: int,
        staff_member_id: int | None,
        status: OfficeHoursSessionStatus,
    ):
        self.preferences = preferences
        self.entered = entered
        self.start = start
        self.end = end
        self.left_queue = left_queue
        self.student_id = student_id
        self.staff_id = staff_member_id
        self.status = status

    @property
    def entered(self) -> datetime.datetime:
        return self._entered.replace(tzinfo=datetime.timezone.utc).astimezone()

    @entered.setter
    def entered(self, value: datetime.datetime) -> None:
        self._entered = value.astimezone(datetime.timezone.utc)

    @property
    def end(self) -> datetime.datetime | None:
        return (
            self._end.replace(tzinfo=datetime.timezone.utc).astimezone()
            if self._end
            else None
        )

    @end.setter
    def end(self, value: datetime.datetime | None) -> None:
        self._end = value.astimezone(datetime.timezone.utc) if value else None

    @property
    def start(self) -> datetime.datetime | None:
        return (
            self._start.replace(tzinfo=datetime.timezone.utc).astimezone()
            if self._start
            else None
        )

    @start.setter
    def start(self, value: datetime.datetime | None) -> None:
        self._start = value.astimezone(datetime.timezone.utc) if value else None

    @property
    def left_queue(self) -> datetime.datetime | None:
        return (
            self._left_queue.replace(tzinfo=datetime.timezone.utc).astimezone()
            if self._left_queue
            else None
        )

    @left_queue.setter
    def left_queue(self, value: datetime.datetime | None) -> None:
        self._left_queue = value.astimezone(datetime.timezone.utc) if value else None

    @property
    def queue_time(self) -> datetime.timedelta:
        left = self.start or self.left_queue
        if not left:
            raise ValueError("Session has not started or left queue.")
        return left - self.entered

    @property
    def time_with_staff(self) -> datetime.timedelta:
        if not self.end or not self.start:
            raise ValueError("Session has not started or ended.")
        return self.end - self.start


class OfficeHoursRequest(Base):
    __tablename__ = "oh_requests"

    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    staff_id: Mapped[int] = mapped_column(None, ForeignKey("staff.id"))
    staff: Mapped[StaffMember] = relationship(
        back_populates="oh_requests",
        lazy="selectin",
    )
    reason: Mapped[str] = mapped_column()
    type: Mapped[OfficeHoursRequestType] = mapped_column()
    timeslot_id: Mapped[int] = mapped_column(
        None,
        ForeignKey("timeslots.id"),
        nullable=True,
    )
    timeslot: Mapped[Timeslot] = relationship(
        back_populates="requests",
        lazy="selectin",
    )

    __mapper_args__ = {
        "polymorphic_identity": OfficeHoursRequestType.ANY,
        "polymorphic_on": "type",
    }


class AddOfficeHoursRequest(OfficeHoursRequest):
    __mapper_args__ = {
        "polymorphic_identity": OfficeHoursRequestType.ADD,
    }

    _start: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        name="start_timestamp",
        nullable=True,
    )
    _end: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        name="end_timestamp",
        nullable=True,
    )
    method: Mapped[TimeslotMethod] = mapped_column(
        use_existing_column=True,
        nullable=True,
    )
    room: Mapped[str | None] = mapped_column(
        default=None,
        use_existing_column=True,
        nullable=True,
    )
    meeting_url: Mapped[str | None] = mapped_column(
        default=None,
        use_existing_column=True,
        nullable=True,
    )

    def __init__(
        self,
        start: datetime.datetime,
        end: datetime.datetime,
        method: TimeslotMethod,
        room: str | None,
        meeting_url: str | None,
    ):
        self.start = start
        self.end = end
        self.method = method
        self.room = room
        self.meeting_url = meeting_url

    @property
    def end(self) -> datetime.datetime:
        return self._end.replace(tzinfo=datetime.timezone.utc).astimezone()

    @end.setter
    def end(self, value: datetime.datetime) -> None:
        self._end = value.astimezone(datetime.timezone.utc)

    @property
    def start(self) -> datetime.datetime:
        return self._start.replace(tzinfo=datetime.timezone.utc).astimezone()

    @start.setter
    def start(self, value: datetime.datetime) -> None:
        self._start = value.astimezone(datetime.timezone.utc)


class MoveOfficeHoursRequest(OfficeHoursRequest):
    __mapper_args__ = {
        "polymorphic_identity": OfficeHoursRequestType.MOVE,
    }

    new_start: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    new_end: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    method: Mapped[TimeslotMethod] = mapped_column(
        use_existing_column=True,
        nullable=True,
    )
    room: Mapped[str | None] = mapped_column(
        default=None,
        use_existing_column=True,
        nullable=True,
    )
    meeting_url: Mapped[str | None] = mapped_column(
        default=None,
        use_existing_column=True,
        nullable=True,
    )

    def __init__(
        self,
        timeslot: Timeslot,
        new_start: datetime.datetime,
        new_end: datetime.datetime,
        method: TimeslotMethod,
        room: str | None = None,
        meeting_url: str | None = None,
    ):
        self.timeslot = timeslot
        self.new_start = new_start
        self.new_end = new_end
        self.method = method
        self.room = room
        self.meeting_url = meeting_url


class RemoveOfficeHoursRequest(OfficeHoursRequest):
    __mapper_args__ = {
        "polymorphic_identity": OfficeHoursRequestType.REMOVE,
    }

    def __init__(self, timeslot: Timeslot):
        self.timeslot = timeslot


class AddRoutineOfficeHoursRequest(OfficeHoursRequest):
    __mapper_args__ = {
        "polymorphic_identity": OfficeHoursRequestType.ADD_ROUTINE,
    }

    weekday: Mapped[int] = mapped_column(nullable=True)
    start_time: Mapped[datetime.time] = mapped_column(
        use_existing_column=True,
        nullable=True,
    )
    length: Mapped[float] = mapped_column(nullable=True)  # in hours
    start_date: Mapped[datetime.date] = mapped_column(nullable=True)
    end_date: Mapped[datetime.date] = mapped_column(nullable=True)
    method: Mapped[TimeslotMethod] = mapped_column(
        use_existing_column=True,
        nullable=True,
    )
    room: Mapped[str | None] = mapped_column(
        default=None,
        use_existing_column=True,
        nullable=True,
    )
    meeting_url: Mapped[str | None] = mapped_column(
        default=None,
        use_existing_column=True,
        nullable=True,
    )

    def __init__(
        self,
        weekday: int,
        start_time: datetime.time,
        length: float,
        start_date: datetime.date,
        end_date: datetime.date,
        method: TimeslotMethod,
        room: str | None = None,
        meeting_url: str | None = None,
    ):
        self.weekday = weekday
        self.start_time = start_time
        self.length = length
        self.start_date = start_date
        self.end_date = end_date
        self.method = method
        self.room = room
        self.meeting_url = meeting_url


class RemoveRoutineOfficeHoursRequest(OfficeHoursRequest):
    __mapper_args__ = {
        "polymorphic_identity": OfficeHoursRequestType.REMOVE_ROUTINE,
    }

    routine_id: Mapped[int] = mapped_column(
        None,
        ForeignKey("routines.id"),
        nullable=True,
    )
    routine: Mapped[Routine] = relationship(
        back_populates="requests",
        lazy="selectin",
    )

    def __init__(self, routine: Routine):
        self.routine = routine


OfficeHoursRequestDetails = (
    AddOfficeHoursRequest
    | MoveOfficeHoursRequest
    | RemoveOfficeHoursRequest
    | AddRoutineOfficeHoursRequest
    | RemoveRoutineOfficeHoursRequest
)


class DocumentEmbedding(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )
    text: Mapped[str] = mapped_column()
    source: Mapped[str] = mapped_column()
    _added_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
    )
    embedding = mapped_column(Vector(1024))

    def __init__(
        self,
        text: str,
        source: str,
        added_at: datetime.datetime,
        embedding: list[float],
    ):
        self.text = text
        self.source = source
        self.added_at = added_at.astimezone(datetime.timezone.utc)
        self.embedding = embedding

    @property
    def added_at(self) -> datetime.datetime:
        return self._added_at.replace(tzinfo=datetime.timezone.utc).astimezone()

    @added_at.setter
    def added_at(self, value: datetime.datetime) -> None:
        self._added_at = value.astimezone(datetime.timezone.utc)

    def __str__(self) -> str:
        return f"DocumentEmbedding<(id={self.id}, source='{self.source}' text='{self.text[:15]}...')>"

    __repr__ = __str__


class Database(AsyncSession):
    def __init__(self, *, bot: CoordinateBot, engine: AsyncEngine):
        self.bot = bot
        self.engine = engine
        super().__init__(bind=engine, expire_on_commit=False)

    async def __aenter__(self) -> Database:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # Sections
    async def add_staff_section(
        self,
        ta_name: str,
        section_names: list[str],
    ):
        result = await self.execute(
            select(Section).where(Section.ta_name == ta_name),
        )
        existing_selection = result.scalars().first()
        if existing_selection:
            existing_selection.section_names = section_names
        else:
            section_assignment = Section(
                ta_name=ta_name,
                section_names=section_names,
            )
            self.add(section_assignment)
        await self.commit()

    async def get_section(self, staff_name: str) -> Section | None:
        result = await self.execute(
            select(Section).where(Section.ta_name == staff_name),
        )
        return result.scalars().first()

    # Llama
    async def add_llama_response(
        self,
        id: int | None,
        channel_id: int,
        staff_id: int,
        prompt: str,
        response: str,
        accepted: bool,
        reason: str | None,
    ) -> None:
        llama_response = LlamaResponse(
            id=id,
            channel_id=channel_id,
            staff_id=staff_id,
            date=datetime.datetime.now().astimezone(),
            prompt=prompt,
            response=response,
            accepted=accepted,
            reason=reason,
        )
        self.add(llama_response)
        await self.commit()

    async def get_llama_response(self, message_id: int) -> LlamaResponse | None:
        result = await self.execute(
            select(LlamaResponse).where(LlamaResponse.id == message_id),
        )
        response = result.scalars().first()
        return response

    async def get_llama_response_for_thread(
        self,
        thread: discord.Thread,
    ) -> LlamaResponse | None:
        result = await self.execute(
            select(LlamaResponse)
            .where(LlamaResponse.channel_id == thread.id)
            .order_by(LlamaResponse.id.desc())
            .limit(1),
        )
        response = result.scalars().first()
        return response

    async def add_embedding(
        self,
        text: str,
        source: str,
        embedding: list[float],
    ):
        doc = DocumentEmbedding(
            text=text,
            source=source,
            added_at=datetime.datetime.now().astimezone(),
            embedding=embedding,
        )
        self.add(doc)
        await self.commit()

    async def find_similar_documents(
        self,
        embedding: list[float],
        limit: int,
    ) -> list[tuple[DocumentEmbedding, float]]:
        # And return cosine distance for each document
        results = await self.execute(
            select(
                DocumentEmbedding,
                DocumentEmbedding.embedding.cosine_distance(embedding),  # type: ignore
            )
            .order_by(DocumentEmbedding.embedding.cosine_distance(embedding))  # type: ignore
            .filter(DocumentEmbedding.embedding.cosine_distance(embedding) < 1)  # type: ignore
            .limit(limit),
        )
        formatted_results = [(result[0], result[1]) for result in results]
        return formatted_results

    async def get_time_added(self, source: str) -> datetime.datetime | None:
        # Assumes that the added at time is the same for all rows for the
        # same document.
        return (
            await self.execute(
                select(DocumentEmbedding._added_at).filter(
                    DocumentEmbedding.source == source,
                ),
            )
        ).scalar()

    # Student
    async def register_late_pass(
        self,
        student_id: int,
        assignment_id: str,
        assignment_name: str,
    ):
        late_pass = LatePass(
            student_id=student_id,
            assignment_id=assignment_id,
            assignment_name=assignment_name,
        )
        self.add(late_pass)
        await self.commit()

    async def get_late_pass(self, student_id: int) -> LatePass | None:
        return (
            await self.execute(
                select(LatePass).filter(LatePass.id == student_id),
            )
        ).scalar_one_or_none()

    # Staff
    async def get_staff(self) -> Sequence[StaffMember]:
        # Get all staff members into a list
        return (await self.execute(select(StaffMember))).scalars().all()

    async def add_staff_member(
        self,
        *,
        name: str,
        gender: Gender,
        professor: bool,
        member: discord.Member | discord.User,
    ):
        s = StaffMember(
            id=member.id,
            name=name,
            gender=gender,
            professor=professor,
            autoaccept_delay=30,
            reminders=StaffMemberRemindersSetting.ALWAYS,
            breaking_until=None,
            desiring_break=None,
            seconds_spent=1,
            seconds_without=1,
        )
        self.add(s)
        await self.commit()

    async def get_staff_member(
        self,
        *,
        name: str | None = None,
        member: discord.Member | discord.User | None = None,
        id: int | None = None,
    ) -> StaffMember:
        if member:
            id = member.id

        # Find staff member with matching name, member, or discord_uid
        staff_member = (
            (
                await self.execute(
                    select(StaffMember).where(
                        (StaffMember.name == name) | (StaffMember.id == id),
                    ),
                )
            )
            .scalars()
            .first()
        )
        if staff_member:
            return staff_member

        # If no staff member is found
        logger.warn(
            f"Found no staff member matching criteria of (name = {name}, member = {member}, discord_uid = {id})",
        )
        raise StaffMemberNotFound(name, id)

    async def update_reminder_preference(
        self,
        staff: StaffMember,
        preference: StaffMemberRemindersSetting,
    ) -> None:
        staff.reminders = preference
        await self.commit()

    async def add_seconds_without(
        self,
        staff: StaffMember,
        seconds: float,
    ) -> None:
        staff.seconds_without += seconds
        await self.commit()

    async def add_seconds_spent(
        self,
        staff: StaffMember,
        seconds: float,
    ) -> None:
        staff.seconds_spent += seconds
        await self.commit()

    async def update_staff_member(
        self,
        staff: StaffMember,
        *,
        name: str | None = None,
        gender: Gender | None = None,
    ) -> None:
        if name:
            staff.name = name
        if gender:
            staff.gender = gender

        self.add(staff)
        await self.commit()

    # Students
    async def get_student(
        self,
        *,
        official_name: str | None = None,
        member: discord.Member | discord.User | None = None,
        discord_id: int | None = None,
    ) -> Student | None:
        if member:
            discord_id = member.id
        student = (
            (
                await self.execute(
                    select(Student).where(
                        (Student.official_name == official_name)
                        | (Student.discord_id == discord_id),
                    ),
                )
            )
            .scalars()
            .first()
        )
        return student

    async def add_student(
        self,
        *,
        member: discord.Member | discord.User,
        canvas_id: int,
        student_sys_id: int,
        official_name: str | None,
        chosen_name: str | None,
    ):
        student = Student(
            discord_id=member.id,
            canvas_id=canvas_id,
            student_sys_id=student_sys_id,
            official_name=official_name,
            chosen_name=chosen_name,
        )
        logger.info(f"Adding new student {student}")
        self.add(student)
        await self.commit()

    # Sessions
    async def create_new_session(self, student_id: int, preferences: list[str]) -> None:
        if not (await self.get_student(discord_id=student_id)):
            member = await self.bot.get_member(student_id)
            await self.add_student(
                member=member,
                canvas_id=99999999,
                ufid=99999999,
                official_name=member.display_name,
                chosen_name=member.display_name,
            )
        session = OfficeHoursSession(
            student_id=student_id,
            preferences=preferences,
            entered=datetime.datetime.now().astimezone(),
            start=None,
            end=None,
            left_queue=None,
            staff_member_id=None,
            status=OfficeHoursSessionStatus.WAITING,
        )
        self.add(session)
        await self.commit()

    async def get_session(
        self,
        student_id: int,
        *,
        status: OfficeHoursSessionStatus,
    ) -> OfficeHoursSession:
        # Get the most recent session, by entered field
        session = (
            (
                await self.execute(
                    select(OfficeHoursSession)
                    .where(OfficeHoursSession.student_id == student_id)
                    .where(OfficeHoursSession.status == status)
                    .order_by(OfficeHoursSession._entered.desc()),
                )
            )
            .scalars()
            .first()
        )

        if not session:
            raise SessionNotFound

        return session

    async def start_session(
        self,
        student_id: int,
        staff_member_id: int,
    ) -> None:
        session = await self.get_session(
            student_id,
            status=OfficeHoursSessionStatus.WAITING,
        )
        session.staff_id = staff_member_id
        session.start = datetime.datetime.now().astimezone()
        session.status = OfficeHoursSessionStatus.ACTIVE
        session.left_queue = datetime.datetime.now().astimezone()
        self.add(session)
        await self.commit()

    async def abort_session(
        self,
        student_id: int,
    ) -> None:
        session = await self.get_session(
            student_id,
            status=OfficeHoursSessionStatus.WAITING,
        )
        session.left_queue = datetime.datetime.now().astimezone()
        session.status = OfficeHoursSessionStatus.LEFT_QUEUE
        self.add(session)
        await self.commit()

    async def end_session(
        self,
        student_id: int,
        staff_id: int,
        resolution: OfficeHoursSessionStatus,
    ) -> None:
        session = await self.get_session(
            student_id,
            status=OfficeHoursSessionStatus.ACTIVE,
        )
        session.end = datetime.datetime.now().astimezone()
        session.staff_id = staff_id
        session.status = resolution
        self.add(session)
        await self.commit()

    # Timeslots
    async def live_timeslots(self) -> Sequence[Timeslot]:
        """
        Returns all timeslots that are occurring right now.
        """
        return (
            (
                await self.execute(
                    select(Timeslot)
                    .where(
                        (Timeslot._start <= datetime.datetime.now().astimezone())
                        & (Timeslot._end >= datetime.datetime.now().astimezone()),
                    )
                    .order_by(Timeslot._start),
                )
            )
            .scalars()
            .all()
        )

    async def breaking_timeslots(self) -> Sequence[Timeslot]:
        """
        Returns all timeslots that are occurring right now, where the staff member who
        owns the timeslot has a datetime breaking_until value, ordered by start.
        """
        current_time = datetime.datetime.now().astimezone()
        # Assuming Timeslot has a foreign key like `staff_member_id` linking to StaffMember's `id`
        timeslot_staff_join = join(
            Timeslot,
            StaffMember,
            Timeslot.staff_id == StaffMember.id,
        )

        return (
            (
                await self.execute(
                    select(Timeslot)
                    .select_from(
                        timeslot_staff_join,
                    )  # Explicitly specify the join condition
                    .where(
                        (Timeslot._start <= current_time)
                        & (Timeslot._end >= current_time)
                        & (StaffMember.breaking_until >= current_time),
                    )
                    .order_by(Timeslot._start),
                )
            )
            .scalars()
            .all()
        )

    async def timeslots_during(
        self,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> Sequence[Timeslot]:
        """
        Returns all timeslots between a range of timezone-aware start and end
        times ordered by start time.
        """
        return (
            (
                await self.execute(
                    select(Timeslot)
                    .where(
                        (Timeslot._start >= start) & (Timeslot._end <= end),
                    )
                    .order_by(Timeslot._start),
                )
            )
            .scalars()
            .all()
        )

    async def add_timeslot(
        self,
        staff: StaffMember,
        start: datetime.datetime,
        end: datetime.datetime,
        *,
        method: TimeslotMethod = TimeslotMethod.DISCORD,
        room: str | None = None,
        meeting_url: str | None = None,
    ) -> Timeslot:
        timeslot = Timeslot(
            routine=None,
            start=start,
            end=end,
            staff=staff,
            method=method,
            room=room,
            meeting_url=meeting_url,
        )
        self.add(timeslot)
        await self.commit()
        return timeslot

    async def move_timeslot(
        self,
        timeslot: Timeslot,
        start: datetime.datetime,
        end: datetime.datetime,
        method: TimeslotMethod | None = None,
        room: str | None = None,
        meeting_url: str | None = None,
    ) -> None:
        timeslot = await self.merge(timeslot)
        timeslot.start = start
        timeslot.end = end
        # if method is provided, changing method
        if method:
            timeslot.method = method
            timeslot.room = room
            timeslot.meeting_url = meeting_url
        self.add(timeslot)
        await self.commit()

    async def remove_timeslot(
        self,
        timeslot: Timeslot,
    ) -> None:
        # Remove all requests associated with this timeslot
        for request in timeslot.requests:
            if request in self:
                self.expunge(request)
            await self.delete(request)
        await self.delete(timeslot)
        await self.commit()

    # Breaks
    async def desire_break(self, staff: StaffMember, minutes: int) -> None:
        print(minutes)
        staff.desiring_break = minutes
        await self.commit()

    async def undesire_break(self, staff: StaffMember) -> None:
        staff.desiring_break = None
        await self.commit()

    async def start_break(
        self,
        staff: StaffMember,
        breaking_until: datetime.datetime,
    ) -> None:
        staff.breaking_until = breaking_until
        await self.commit()

    async def end_break(self, staff: StaffMember) -> None:
        staff.breaking_until = None
        await self.commit()

    async def remove_routine(self, routine: Routine) -> None:
        for request in routine.requests:
            if request in self:
                self.expunge(request)
            await self.delete(request)
        await self.delete(routine)
        for timeslot in routine.timeslots:
            for request in timeslot.requests:
                if request in self:
                    self.expunge(request)
                await self.delete(request)
            await self.delete(timeslot)
        await self.commit()

    async def add_routine(self, routine: Routine, timeslots: list[Timeslot]) -> None:
        self.add(routine)
        for timeslot in timeslots:
            self.add(timeslot)
        await self.commit()

    # Office Hours Requests
    async def create_oh_request(
        self,
        message_id: int,
        staff: StaffMember,
        reason: str,
        details: OfficeHoursRequestDetails,
    ) -> None:
        details.message_id = message_id
        details.staff = staff
        details.reason = reason
        self.add(details)
        await self.commit()

    async def get_oh_request(
        self,
        message_id: int,
        report_cls: type[R] = OfficeHoursRequest,
        /,
    ) -> R:
        # Get request with message_id
        specific_request = (
            (
                await self.execute(
                    select(report_cls).where(
                        report_cls.message_id == message_id,
                    ),
                )
            )
            .scalars()
            .first()
        )

        if not specific_request:
            logger.warn(f"Found no office hours request with message id {message_id}")
            raise OfficeHoursRequestNotFound(message_id)

        return specific_request


class DatabaseFactory:
    def __init__(self, *, engine: AsyncEngine, bot: CoordinateBot):
        self.engine = engine
        self.bot = bot

    def __call__(self) -> Database:
        return Database(bot=self.bot, engine=self.engine)

    async def close(self):
        await self.engine.dispose()


async def get_session(bot: CoordinateBot) -> Database:
    # Connect to psql database with db name mydb, user abc, pass def
    engine = create_async_engine(
        POSTGRES_URL,
        echo=True,
    )

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.commit()
        await conn.run_sync(Base.metadata.create_all)

    return Database(bot=bot, engine=engine)

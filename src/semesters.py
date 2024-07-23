import dataclasses
import datetime

PeriodT = tuple[datetime.date, datetime.date]


@dataclasses.dataclass()
class CourseSection:
    start_time: datetime.time
    weekday: int
    room: str
    leader: str
    shadower: str | None = None


@dataclasses.dataclass()
class Course:
    canvas_course_code: int
    sections: dict[int, CourseSection]


@dataclasses.dataclass()
class Semester:
    name: str
    start: datetime.date
    end: datetime.date
    courses: dict[str, Course]
    finals_end: datetime.date | None = None
    breaks: list[PeriodT] = dataclasses.field(default_factory=lambda: [])

    def __post_init__(self):
        if not self.finals_end:
            self.finals_end = self.end + datetime.timedelta(days=10)


# Fill out your semester entries like so:
# Semester(
#         "Spring 2023",
#         datetime.date(2023, 1, 1),
#         datetime.date(2023, 4, 26),
#         courses={
#             "EXA1001": Course(470560, {}),
#         },
#     ),
SEMESTERS = []


def semester_given_date(
    date: datetime.datetime,
    *,
    next_semester: bool = False,
) -> Semester | None:
    for semester in SEMESTERS:
        if semester.start <= date.date() <= (semester.finals_end or semester.end):
            return semester
        if next_semester and date.date() < semester.start:
            return semester
    return None

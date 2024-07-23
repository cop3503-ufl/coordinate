import asyncio
import contextlib
import datetime
import re
import tarfile
from dataclasses import dataclass
from typing import Literal, TypedDict

import aiohttp
import pyzstd


class Assignment(TypedDict):
    id: str
    name: str


class Module(TypedDict):
    id: str
    name: str
    assignments: list[Assignment]


class Course(TypedDict):
    id: str
    name: str
    modules: list[Module]
    assignments: list[Assignment]


class StudentProgress(TypedDict):
    student_id: str
    student_email: str
    student_name: str
    seconds_spent: int
    grade: int
    status: Literal["NOT_STARTED", "STARTED", "COMPLETED"]
    completion_date: datetime.datetime
    timeLimitExtension: int | None
    deadlineExtension: int | None


class User(TypedDict):
    id: str
    name: str
    login: str
    email: str


@dataclass
class AuthToken:
    access_token: str
    expires_at: datetime.datetime


class CodioFile:
    name: str
    content: bytes

    def __init__(self, name: str, content: bytes):
        self.name = name
        self.content = content

    def __str__(self) -> str:
        return f"<CodioFile name='{self.name}' content='{self.content[:15]}'>"

    __repr__ = __str__


class CodioHelper:
    client_id: str
    client_secret: str
    course_id: str
    token: AuthToken
    students: list[User]
    assignments: list[Assignment]
    session: aiohttp.ClientSession

    OAUTH_URL = "https://oauth.codio.com/api/v1"
    API_URL = "https://octopus.codio.com/api/v1"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        course_id: str,
        session: aiohttp.ClientSession,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.course_id = course_id
        self.session = session
        self.students = []
        self.assignments = []
        pass

    async def setup(self):
        await self.get_auth_token()
        self.students = await self.get_students()

    async def shutdown(self):
        """
        This method is not needed anymore.
        """
        pass

    async def __aenter__(self):
        await self.setup()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def get_auth_token(self):
        url = f"{self.OAUTH_URL}/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        response = await self.session.post(url, params=data)
        json = await response.json()
        self.token = AuthToken(
            json["access_token"],
            datetime.datetime.now()
            + datetime.timedelta(seconds=int(json["expires_in"])),
        )

    async def get_student(self, name: str) -> User | None:
        if not self.students:
            self.students = await self.get_students()
        return next(
            (student for student in self.students if student["name"] == name),
            None,
        )

    async def fetch(self, url: str) -> aiohttp.ClientResponse:
        # Check if token needs refresh
        if self.token.expires_at < datetime.datetime.now():
            await self.get_auth_token()

        headers = {"Authorization": f"Bearer {self.token.access_token}"}
        return await self.session.get(url, headers=headers)

    async def get_assignment_named(self, name: str) -> Assignment:
        assignments = await self.get_assignments()
        for assignment in assignments:
            assignment_name = assignment["name"]
            assignment_name = re.sub(r"^\d+\.\d+ ", "", assignment_name)
            if assignment_name == name:
                return assignment
        # Lab matching heuristic: attempt to match Canvas lab assignment names
        # with Codio assignment names more clearly
        # TODO - Find a more permanent way to do this
        if "lab" in name.lower():
            with contextlib.suppress(IndexError):
                lab_number = re.search(r"\blab\s*(\d+)\b", name.lower())
                if lab_number:
                    lab_number = int(lab_number.group(1))
                    for assignment in assignments:
                        if f"lab {lab_number}" in assignment["name"].lower():
                            return assignment
        raise ValueError(f"Assignment with name {name} not found")

    async def get_assignments(self) -> list[Assignment]:
        if not self.assignments:
            self.assignments = []
            course = await self.get_course()
            for module in course["modules"]:
                for assignment in module["assignments"]:
                    self.assignments.append(assignment)
        return self.assignments

    async def get_course(self) -> Course:
        url = f"{self.API_URL}/courses/{self.course_id}"
        self.students = []
        self.assignments = []
        response = await self.fetch(url)
        return await response.json()

    async def get_student_progress(self, assignment_id: str) -> list[StudentProgress]:
        url = f"{self.API_URL}/courses/{self.course_id}/assignments/{assignment_id}/students"
        response = await self.fetch(url)
        return await response.json()

    async def download_student_submission(
        self,
        assignment_id: str,
        student_id: str,
    ) -> list[CodioFile]:
        url = f"{self.API_URL}/courses/{self.course_id}/assignments/{assignment_id}/students/{student_id}/download"
        response = await self.fetch(url)
        js = await response.json()
        task_uri = js["taskUri"]
        return await self.wait_download_task(task_uri)

    async def wait_download_task(self, task_url: str) -> list[CodioFile]:
        response = await self.fetch(task_url)
        json = await response.json()
        if json["done"] is True:
            url = json["url"]
            response = await self.fetch(url)
            data = await response.read()
            with open("temp.tar.zst", "wb") as f:
                f.write(data)
            with open("temp.tar.zst", "rb") as f:
                data = f.read()
                data: bytes = pyzstd.decompress(data)
                with open("temp.tar", "wb") as f:
                    f.write(data)
            codio_files: list[CodioFile] = []
            with tarfile.open("temp.tar") as tar:
                for name in tar.getnames():
                    try:
                        file = tar.extractfile(name)
                        if file is not None:
                            content = file.read()
                            codio_files.append(CodioFile(name, content))
                    except tarfile.TarError as e:
                        print(f"Failed to read file {name} due to TarError: {e}")
                    except OSError as e:
                        print(f"Failed to read file {name} due to OSError: {e}")
            return codio_files
        else:
            await asyncio.sleep(0.5)
            return await self.wait_download_task(task_url)

    async def get_progress_for_student(
        self,
        assignment_id: str,
        student_id: str,
    ) -> StudentProgress | None:
        progress = await self.get_student_progress(assignment_id)
        return next((p for p in progress if p["student_id"] == student_id), None)

    async def get_students(self) -> list[User]:
        url = f"{self.API_URL}/courses/{self.course_id}/students"
        response = await self.fetch(url)
        return await response.json()

    def assignment_url_id(self, assignment_name: str) -> str:
        # Replace anything that is not a letter or number with dash, including colons
        return re.sub(r"[^a-zA-Z0-9]", "-", assignment_name.lower())

    def assignment_preview_url(self, login: str, assignment_name: str) -> str:
        return f"https://codio.com/{login}/{self.assignment_url_id(assignment_name)}/preview"

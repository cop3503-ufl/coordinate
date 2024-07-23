import logging

from gradescope_api.assignment import GradescopeAssignment
from gradescope_api.client import GradescopeClient
from gradescope_api.course import GradescopeCourse

from .env import GRADESCOPE_COURSE_ID, GRADESCOPE_EMAIL, GRADESCOPE_PASSWORD

logger = logging.getLogger(__name__)


class Gradescope:
    def __init__(self, course_id: str | None = None):
        self.course_id = course_id or GRADESCOPE_COURSE_ID

    async def setup(self):
        if GRADESCOPE_EMAIL is None or GRADESCOPE_PASSWORD is None:
            return
        self.client = GradescopeClient(GRADESCOPE_EMAIL, GRADESCOPE_PASSWORD)
        await self.client.setup()

    async def shutdown(self):
        await self.client.shutdown()

    async def get_assignments(self) -> list[GradescopeAssignment]:
        if (
            GRADESCOPE_EMAIL is None
            or GRADESCOPE_PASSWORD is None
            or self.course_id is None
        ):
            logger.warning(
                "Gradescope credentials not provided, but an assignment lookup was attempted. If you would like to search through Gradescope assignments, please provide necessary credentials.",
            )
            return []
        course = GradescopeCourse(course_id=self.course_id, _client=self.client)
        return await course.get_assignments()

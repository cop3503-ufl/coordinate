import datetime

import pytest
import pytz

from src.env import GRADESCOPE_TEST_ASSIGNMENT_ID, GRADESCOPE_TEST_COURSE_ID
from src.gradescope import Gradescope


@pytest.fixture
async def gradescope_client():
    # Testing course:
    g = Gradescope(course_id=GRADESCOPE_TEST_COURSE_ID)
    await g.setup()
    yield g
    await g.shutdown()


async def test_gradescope_assignments(gradescope_client: Gradescope):
    # In the testing course, there is one assignment (though this can be changed based on the testing course)
    # Details of the assignment:
    #   - Name: "Testing Assignment 1"
    #   - Released: 2024-06-03 01:23:00AM EST
    #   - Due: 2024-06-04 16:56:00PM EST
    assignments = await gradescope_client.get_assignments()
    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment.title == "Testing Assignment 1"
    assert assignment.due_date is not None
    utc_due_date = assignment.due_date.astimezone(datetime.timezone.utc)
    assert utc_due_date == datetime.datetime(
        2024,
        6,
        4,
        16,
        56,
        tzinfo=pytz.timezone("US/Eastern"),
    ).astimezone(datetime.timezone.utc)
    assert assignment.assignment_id == GRADESCOPE_TEST_ASSIGNMENT_ID

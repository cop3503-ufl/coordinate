import os
from typing import Literal, overload

from dotenv import load_dotenv

load_dotenv()


@overload
def ensure_string(name: str | list[str], *, required: Literal[True] = True) -> str: ...


@overload
def ensure_string(
    name: str | list[str],
    *,
    required: Literal[False] = False,
) -> str | None: ...


@overload
def ensure_string(name: str | list[str], *, required: bool) -> str | None: ...


def ensure_string(name: str | list[str], *, required: bool = True) -> str | None:
    if isinstance(name, str):
        name = [name]
    value = None
    for n in name:
        value = os.getenv(n)
        if value:
            break
    if value is None and required:
        raise ValueError(f"Environment variable {name} is not set.")
    return value


CANVAS_API_TOKEN = ensure_string("CANVAS_API_TOKEN")
CANVAS_URL = ensure_string("CANVAS_URL")
POSTGRES_URL = ensure_string("POSTGRES_URL")
GUILD_ID = int(ensure_string("GUILD_ID"))
DISCORD_TOKEN = ensure_string("DISCORD_TOKEN")
DEV_MODE = ensure_string("DEV_MODE").lower() == "true"
QUALTRICS_URL = ensure_string("QUALTRICS_URL")
QUALTRICS_API_TOKEN = ensure_string("QUALTRICS_API_TOKEN")
QUALTRICS_API_DATACENTER = ensure_string("QUALTRICS_API_DATACENTER")
QUALTRICS_SURVEY_ID = ensure_string("QUALTRICS_SURVEY_ID")
QUALTRICS_FILTER_ID = ensure_string("QUALTRICS_FILTER_ID")
CODIO_CLIENT_ID = ensure_string("CODIO_CLIENT_ID", required=False)
CODIO_CLIENT_SECRET = ensure_string("CODIO_CLIENT_SECRET", required=False)
CODIO_COURSE_ID = ensure_string("COURSE_ID", required=False)
GITHUB_TOKEN = ensure_string(["GITHUB_TOKEN", "TOKEN_FOR_GH"], required=False)
NVIDIA_NGC_TOKEN = ensure_string("NVIDIA_NGC_TOKEN", required=False)
LLAMA_SOURCE_FOLDER = ensure_string("LLAMA_SOURCE_FOLDER", required=False)
GRADESCOPE_EMAIL = ensure_string("GRADESCOPE_EMAIL", required=False)
GRADESCOPE_PASSWORD = ensure_string("GRADESCOPE_PASSWORD", required=False)
GRADESCOPE_COURSE_ID = ensure_string("GRADESCOPE_COURSE_ID", required=False)
GRADESCOPE_TEST_COURSE_ID = ensure_string("GRADESCOPE_TEST_COURSE_ID", required=False)
GRADESCOPE_TEST_ASSIGNMENT_ID = ensure_string(
    "GRADESCOPE_TEST_ASSIGNMENT_ID",
    required=False,
)

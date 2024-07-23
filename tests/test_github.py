import aiohttp
from aioresponses import aioresponses

from src.env import GITHUB_TOKEN
from src.github import GitHub


async def test_create_issue():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.post(
                "https://api.github.com/repos/cbrxyz/coordinate/issues",
                payload={"number": 1},
            )
            g = GitHub(auth_token=GITHUB_TOKEN, session=session)
            resp = await g.create_issue("test issue", "test body")
            assert resp["number"] == 1

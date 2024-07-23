import asyncio
import datetime

import pytest

from src.tasks import TaskManager


async def test_create_task():
    async with TaskManager() as task_manager:
        x = 1

        async def coro(y: int):
            nonlocal x
            x = y

        task_manager.create_task(coro(2))
        task_manager.create_task(coro(3))
        await asyncio.sleep(0.1)
        assert x == 3


async def test_create_same_name():
    async with TaskManager() as task_manager:
        x = 1

        async def coro(sleep: float):
            nonlocal x
            await asyncio.sleep(sleep)
            x = sleep

        task_manager.create_task(coro(1), name="task")
        task_manager.create_task(coro(0.5), name="task")
        await asyncio.sleep(0.6)
        assert x == 0.5


async def test_cancel():
    async with TaskManager() as task_manager:
        x = 1

        async def coro(y: int):
            nonlocal x
            await asyncio.sleep(y)
            x = y

        task_manager.create_task(coro(2), name="task_cancel")
        await asyncio.sleep(0.1)
        task_manager.remove_task("task_cancel")
        await asyncio.sleep(0.1)
        assert x == 1


async def test_create_and_wait():
    async with TaskManager() as task_manager:
        x = 0

        async def coro():
            await asyncio.sleep(0.1)
            nonlocal x
            x += 1

        task = await task_manager.create_task_and_wait(coro())
        task2 = await task_manager.create_task_and_wait(coro())
        await asyncio.sleep(0.3)
        assert task.get_name() != task2.get_name()
        assert x == 2


async def test_get_task():
    async with TaskManager() as task_manager:
        x = 0

        async def coro():
            await asyncio.sleep(0.2)
            nonlocal x
            x += 1

        task = task_manager.create_task(coro(), name="task_get")
        assert task_manager.get_task("task_get") == task
        task_manager.remove_task("doesn't exist")
        task_manager.remove_task("")
        await asyncio.sleep(0.3)
        assert x == 1


async def test_time_based():
    async with TaskManager() as task_manager:
        x = datetime.datetime.now()
        delta = None

        async def coro():
            nonlocal delta
            nonlocal x
            delta = datetime.datetime.now() - x

        task_manager.run_at(
            datetime.datetime.now() + datetime.timedelta(seconds=0.1),
            "task_at",
            coro,
        )
        await asyncio.sleep(0.15)
        assert delta is not None
        assert (
            datetime.timedelta(seconds=0.08)
            <= delta
            <= datetime.timedelta(seconds=0.12)
        )
        x = datetime.datetime.now()
        task_manager.run_in(datetime.timedelta(seconds=0.2), "task_in", coro)
        await asyncio.sleep(0.25)
        assert delta is not None
        assert (
            datetime.timedelta(seconds=0.18)
            <= delta
            <= datetime.timedelta(seconds=0.22)
        )
        task_manager.run_in(datetime.timedelta(seconds=0.2), "please_cancel", coro)
        await asyncio.sleep(0.05)
        task_manager.remove_task("please_cancel")
        await asyncio.sleep(0.05)
        assert task_manager.get_task("please_cancel") is None


async def task_in_past():
    async with TaskManager() as task_manager:

        async def coro():
            pass

        with pytest.raises(ValueError):
            task_manager.run_at(
                datetime.datetime.now() - datetime.timedelta(seconds=0.1),
                "task_at",
                coro,
            )

        with pytest.raises(ValueError):
            task_manager.run_in(
                datetime.timedelta(seconds=-0.1),
                "task_in",
                coro,
            )


async def test_exception():
    async with TaskManager() as task_manager:
        x = 0

        async def problem():
            raise ValueError("This is an error")

        async def coro():
            nonlocal x
            x += 1

        task_manager.create_task(problem(), name="task_exception")
        await asyncio.sleep(0.1)
        assert task_manager.get_task("task_exception") is None
        task_manager.create_task(coro(), name="fine")
        await asyncio.sleep(0.1)
        assert x == 1


async def test_high_volume():
    async with TaskManager() as task_manager:
        x = 0

        async def coro():
            nonlocal x
            x += 1

        for _ in range(10000):
            task_manager.create_task(coro())
        await asyncio.sleep(0.1)
        assert x == 10000


async def test_shutdown():
    task_manager = TaskManager()
    task_manager.start()
    x = 0

    async def coro():
        nonlocal x
        await asyncio.sleep(100)
        x += 1

    task1 = await task_manager.create_task_and_wait(coro())
    task2 = await task_manager.create_task_and_wait(coro())
    await asyncio.sleep(0.1)
    await task_manager.shutdown()
    task_manager.stop()
    assert x == 0
    assert task_manager.get_task(task1.get_name()) is None
    assert task_manager.get_task(task2.get_name()) is None
    assert task1.done()
    assert task2.done()
    assert task1.cancelled()
    assert task2.cancelled()

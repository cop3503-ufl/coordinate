import asyncio
import contextlib
import datetime
import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class TaskManager:

    _meta_task: asyncio.Task
    _tasks: dict[str, asyncio.Task]
    _lock: asyncio.Lock
    _task_queue: asyncio.Queue[
        tuple[Coroutine[Any, Any, Any], str | None, asyncio.Event | None]
    ]

    def __init__(self):
        self._tasks = {}
        self._lock = asyncio.Lock()
        self._task_queue = asyncio.Queue()

    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()
        self.stop()

    def start(self):
        self._meta_task = asyncio.create_task(self._handle_task_creation())

    def stop(self):
        self._meta_task.cancel()

    def unique_id(self) -> str:
        return str(uuid.uuid4())

    def create_task(self, coro: Coroutine, *, name: str | None = None) -> None:
        self._task_queue.put_nowait((coro, name, None))

    async def create_task_and_wait(
        self,
        coro: Coroutine,
        *,
        name: str | None = None,
    ) -> asyncio.Task:
        task_created_event = asyncio.Event()
        if not name:
            name = self.unique_id()
        self._task_queue.put_nowait((coro, name, task_created_event))
        await task_created_event.wait()
        task = self.get_task(name)
        if not task:
            raise ValueError(
                f"Task with name {name} not found. This should not happen, since this function should wait for the task!",
            )
        return task

    async def _handle_task_creation(self):
        while True:
            coro, name, task_event = await self._task_queue.get()
            await self._async_create_task(coro, name=name, event=task_event)

    async def _async_create_task(
        self,
        coro: Coroutine,
        *,
        name: str | None = None,
        event: asyncio.Event | None = None,
    ) -> asyncio.Task:
        async with self._lock:
            task_name = name or self.unique_id()
            if task_name in self._tasks:
                logger.warning(
                    f"Task with name {task_name} already exists, quietly removing it...",
                )
                self.remove_task(task_name)

            task = asyncio.create_task(coro)
            self._tasks[task_name] = task

            task.add_done_callback(
                lambda task_ref: (
                    self._tasks.pop(task_name, None)
                    if self._tasks.get(task_name or "") == task_ref
                    else None
                ),
            )
            if event:
                event.set()
            return task

    def get_task(self, name: str) -> asyncio.Task | None:
        return self._tasks.get(name)

    def remove_task(self, name: str) -> None:
        task = self._tasks.get(name, None)
        self.create_task(self._async_remove_task(task))

    async def _async_remove_task(
        self,
        task: asyncio.Task | None,
    ) -> asyncio.Task | None:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return task

    def run_at(
        self,
        when: datetime.datetime,
        name: str,
        coro: Callable[..., Coroutine[Any, Any, Any]],
        *args,
        **kwargs,
    ) -> None:
        delay = when.astimezone() - datetime.datetime.now().astimezone()
        return self.run_in(delay, name, coro, *args, **kwargs)

    def run_in(
        self,
        delay: datetime.timedelta,
        name: str,
        coro: Callable[..., Coroutine[Any, Any, Any]],
        *args,
        **kwargs,
    ) -> None:
        if delay.total_seconds() <= 0:
            raise ValueError("The specified delay is in the past")

        async def _run_in():
            try:
                await asyncio.sleep(delay.total_seconds())
                await coro(*args, **kwargs)
            except asyncio.CancelledError:
                pass

        return self.create_task(_run_in(), name=name)

    async def shutdown(self):
        tasks = list(self._tasks.values())
        logger.info(f"Shutting down {len(tasks)} tasks in the task manager...")
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

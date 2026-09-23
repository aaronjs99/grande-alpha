from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class TaskSupervisor:
    """Own GUI-created asyncio tasks so failures and shutdown are deterministic."""

    def __init__(self, on_error: Callable[[str, BaseException], None]) -> None:
        self._on_error = on_error
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._closing = False

    @property
    def closing(self) -> bool:
        return self._closing

    def start(self, name: str, awaitable: Awaitable[Any]) -> asyncio.Task[Any] | None:
        existing = self._tasks.get(name)
        if self._closing or (existing is not None and not existing.done()):
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            return existing
        task = asyncio.create_task(awaitable, name=f"grande-alpha-ui:{name}")
        self._tasks[name] = task
        task.add_done_callback(lambda completed, task_name=name: self._finished(task_name, completed))
        return task

    def _finished(self, name: str, task: asyncio.Task[Any]) -> None:
        if self._tasks.get(name) is task:
            self._tasks.pop(name, None)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            self._on_error(name, exception)

    async def shutdown(self) -> None:
        self._closing = True
        current = asyncio.current_task()
        pending = [task for task in self._tasks.values() if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks = {
            name: task for name, task in self._tasks.items() if task is current and not task.done()
        }

"""Bounded retries for idempotent broker reads; never use for order submissions."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

from grande_alpha.broker.base import BrokerError

T = TypeVar("T")


def _transient(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    if isinstance(exc, BrokerError):
        message = str(exc).casefold()
        return "timed out" in message or bool(re.search(r"\b(?:429|502|503|504)\b", message))
    return False


async def read_with_backoff(operation: Callable[[], Awaitable[T]], *, attempts: int = 3) -> T:
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError("Read attempts must be between one and five")
    for index in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            if not _transient(exc) or index == attempts - 1:
                raise
            await asyncio.sleep(.25 * 2 ** index)
    raise AssertionError("Read retry loop exhausted without a result")

"""Small synchronous event hooks shared by the desktop and headless runtimes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

Payload = TypeVar("Payload")


class EventHook(Generic[Payload]):
    """Publish runtime updates without coupling application services to Qt.

    Subscribers run synchronously in the caller's event loop. The desktop binds
    these hooks to Qt widgets, while headless callers can subscribe without
    importing the desktop toolkit.
    """

    def __init__(self) -> None:
        self._subscribers: list[Callable[..., object]] = []

    def connect(self, subscriber: Callable[..., object]) -> None:
        """Register one subscriber once, preserving registration order."""
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def disconnect(self, subscriber: Callable[..., object]) -> None:
        """Remove a subscriber when it no longer owns a presentation surface."""
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def emit(self, *arguments: object) -> None:
        """Deliver an update to a stable snapshot of current subscribers."""
        for subscriber in tuple(self._subscribers):
            subscriber(*arguments)

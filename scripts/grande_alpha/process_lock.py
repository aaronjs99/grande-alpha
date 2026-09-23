"""A small cross-platform process lock for GRANDE Alpha entry points."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class ProcessLock:
    """Own one lock file while a desktop or foreground CLI session is active.

    The operating system releases the byte-range lock if the owning process
    exits unexpectedly. The file itself is intentionally retained, so recovery
    never needs to delete a potentially active lock file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: BinaryIO | None = None

    def acquire(self, timeout_seconds: float = 0.0) -> bool:
        """Try to acquire the lock before ``timeout_seconds`` elapses."""
        if self._handle is not None:
            return True
        if timeout_seconds < 0:
            raise ValueError("Lock timeout must be nonnegative")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_seconds
        while True:
            handle: BinaryIO | None = None
            try:
                handle = self.path.open("a+b")
                handle.seek(0)
                if not handle.read(1):
                    handle.seek(0)
                    handle.write(b"0")
                    handle.flush()
                if self._try_lock(handle):
                    self._handle = handle
                    return True
            except OSError:
                pass
            finally:
                if handle is not None and handle is not self._handle:
                    handle.close()
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.05, deadline - time.monotonic()))

    def release(self) -> None:
        """Release this process's lock; releasing an unlocked instance is safe."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            self._unlock(handle)
        finally:
            handle.close()

    def __enter__(self) -> ProcessLock:
        if not self.acquire():
            raise RuntimeError("Another GRANDE Alpha instance holds the application lock")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    @staticmethod
    def _try_lock(handle: BinaryIO) -> bool:
        if os.name == "nt":
            import msvcrt

            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True

        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

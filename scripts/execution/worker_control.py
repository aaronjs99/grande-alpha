"""Durable control state read by the worker immediately before submissions."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkerState:
    generation: int = 0
    running: bool = False
    account: str = ""
    scope_digest: str = ""
    candidate_path: str = ""
    authorization_path: str = ""
    earnings_database: str = ""
    poll_seconds: float = 5.0


class WorkerControlStore:
    """Keep start and stop state visible across processes and crashes.

    Every transition is a separate SQLite transaction. A second process may
    stop a worker whose event loop is waiting on a broker operation.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA synchronous=FULL")
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS worker_control ("
                "id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL, "
                "running INTEGER NOT NULL CHECK(running IN (0,1)), account TEXT NOT NULL, "
                "scope_digest TEXT NOT NULL, candidate_path TEXT NOT NULL, "
                "authorization_path TEXT NOT NULL, earnings_database TEXT NOT NULL, "
                "poll_seconds REAL NOT NULL CHECK(poll_seconds>0))"
            )
            self._db.execute(
                "INSERT OR IGNORE INTO worker_control VALUES (1,0,0,'','','','','',5.0)"
            )

    def close(self) -> None:
        self._db.close()

    def current(self) -> WorkerState:
        with self._lock:
            row = self._db.execute("SELECT * FROM worker_control WHERE id=1").fetchone()
        return WorkerState(
            generation=row["generation"], running=bool(row["running"]),
            account=row["account"], scope_digest=row["scope_digest"],
            candidate_path=row["candidate_path"],
            authorization_path=row["authorization_path"],
            earnings_database=row["earnings_database"], poll_seconds=row["poll_seconds"],
        )

    def review(
        self, *, account: str, scope_digest: str, candidate_path: str,
        authorization_path: str, earnings_database: str, poll_seconds: float,
    ) -> WorkerState:
        if not all(isinstance(value, str) and value.strip() for value in (
            account, scope_digest, candidate_path, authorization_path, earnings_database,
        )) or not isinstance(poll_seconds, (int, float)) or not 0 < poll_seconds <= 3600:
            raise ValueError("Review requires an account, scope, paths and a positive poll interval")
        with self._lock, self._db:
            row = self._db.execute("SELECT running FROM worker_control WHERE id=1").fetchone()
            if row["running"]:
                raise RuntimeError("Stop the current worker session before changing its review")
            self._db.execute(
                "UPDATE worker_control SET generation=generation+1, running=0, "
                "account=?, scope_digest=?, candidate_path=?, authorization_path=?, "
                "earnings_database=?, poll_seconds=? WHERE id=1",
                (account, scope_digest, candidate_path, authorization_path,
                 earnings_database, poll_seconds),
            )
        return self.current()

    def start(self, scope_digest: str, *, expected_generation: int | None = None) -> WorkerState:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT running,scope_digest,generation FROM worker_control WHERE id=1"
            ).fetchone()
            if row["running"]:
                raise RuntimeError("Worker session is already running")
            if expected_generation is not None and row["generation"] != expected_generation:
                raise RuntimeError("Worker control changed while start was being approved")
            if not row["scope_digest"] or scope_digest != row["scope_digest"]:
                raise RuntimeError("Start scope differs from the reviewed scope")
            self._db.execute(
                "UPDATE worker_control SET generation=generation+1,running=1 WHERE id=1"
            )
        return self.current()

    def stop(self) -> WorkerState:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE worker_control SET generation=generation+1,running=0 WHERE id=1"
            )
        return self.current()

    def may_submit(self, generation: int, scope_digest: str) -> bool:
        state = self.current()
        return state.running and state.generation == generation and state.scope_digest == scope_digest


class WorkerPermission:
    """Combine durable worker intent with the independent user permit."""

    def __init__(
        self, control: WorkerControlStore, generation: int,
        user_permit: Callable[[str], bool],
    ) -> None:
        self.control = control
        self.generation = generation
        self.user_permit = user_permit

    def __call__(self, scope_digest: str) -> bool:
        if not self.control.may_submit(self.generation, scope_digest):
            raise RuntimeError("Worker trading was stopped or its reviewed scope changed")
        if self.user_permit(scope_digest) is not True:
            raise RuntimeError("The user permit does not approve this scope")
        return True

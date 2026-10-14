"""Disconnected worker observations and emergency fencing of existing records.

Recorded intent is never evidence of a reachable worker or verified broker state.
Neither operation initializes or migrates a database.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def _existing_database(data_dir: Path) -> Path:
    path = (data_dir / "grande_alpha.db").resolve(strict=True)
    if not path.is_file():
        raise RuntimeError("The worker control database is not a file")
    return path


def _recorded_intent(connection: sqlite3.Connection) -> tuple[int, bool]:
    row = connection.execute(
        "SELECT generation,running FROM worker_control WHERE id=1"
    ).fetchone()
    if row is None:
        raise RuntimeError("No existing worker control record was found")
    generation, running = row
    if type(generation) is not int or not 0 <= generation < 2**63 - 1:
        raise RuntimeError("Worker control generation is invalid")
    if type(running) is not int or running not in (0, 1):
        raise RuntimeError("Worker control running intent is invalid")
    return generation, bool(running)


def _disconnected_status(
    record: str, intent: tuple[int, bool] | None = None, *, error: str = ""
) -> dict[str, Any]:
    generation, recorded_running = intent if intent is not None else (None, None)
    stop_recorded = recorded_running is False
    if stop_recorded:
        phase = "Local stop recorded"
        guidance = (
            "A local stop fence is recorded. Worker activity and broker orders remain unverified. "
            "Refresh status to retry the worker; verify orders and holdings at the broker."
        )
    elif record == "missing":
        phase = "No worker record"
        guidance = (
            "No local worker control database was found. No stop or broker state is confirmed. "
            "Connect to begin a session, or verify any existing orders at the broker."
        )
    else:
        phase = "Unknown — worker unreachable"
        guidance = (
            "The worker may still be active. Refresh status to retry, or use STOP TRADING to "
            "record a local stop fence. Verify orders and holdings at the broker."
        )
    return {
        "worker_reachable": False,
        "status_source": "read_only_local_control",
        "connected": None,
        "running": None,
        "phase": phase,
        "control_record": record,
        "recorded_running": recorded_running,
        "generation": generation,
        "local_stop_recorded": stop_recorded,
        "broker_verified": False,
        "orders_cancelled": False,
        "guidance": guidance,
        "control_error": error,
    }


def disconnected_worker_status(data_dir: Path) -> dict[str, Any]:
    """Read intent without initializing records, connecting a broker or granting authority.

    SQLite may create WAL sidecars to coordinate with a concurrent writer.
    """
    try:
        path = _existing_database(data_dir)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.25)) as connection:
            intent = _recorded_intent(connection)
    except FileNotFoundError:
        return _disconnected_status("missing")
    except (OSError, sqlite3.Error, RuntimeError) as error:
        return _disconnected_status("unavailable", error=str(error))
    return _disconnected_status("available", intent)


def stop_existing_worker(data_dir: Path) -> dict[str, Any]:
    """Commit an emergency fence only to an existing, valid singleton record."""
    try:
        path = _existing_database(data_dir)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=5)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                generation, _running = _recorded_intent(connection)
                connection.execute(
                    "UPDATE worker_control SET generation=?,running=0 WHERE id=1",
                    (generation + 1,),
                )
        return {
            **_disconnected_status("available", (generation + 1, False)),
            "status_source": "durable_local_stop",
            "stopped": True,
        }
    except (OSError, sqlite3.Error, RuntimeError) as error:
        raise RuntimeError(f"Local stop fence could not be recorded: {error}") from error

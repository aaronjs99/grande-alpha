"""Local, session-scoped IPC for an explicitly enabled research MCP connection.

The stdio process has no broker imports or credentials. Only the live desktop can
serve requests; there is no cached account export or service listening on a port.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

LEASE_SECONDS = 5.0
REQUEST_SECONDS = 10.0
MAX_PENDING = 32
COMMANDS = {"context", "brief", "universe", "start", "stop"}


class BridgeUnavailable(RuntimeError):
    pass


class AgentBridge:
    def __init__(self, path: Path):
        self.path = path
        self.session = ""

    def _connect(self):
        if not self.path.is_file():
            raise BridgeUnavailable("Open GRANDE and enable the research MCP connection first")
        db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=rw", uri=True, timeout=0.1)
        db.row_factory = sqlite3.Row
        return db

    def enable(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        if os.name != "nt":
            self.path.chmod(0o600)
        session = str(uuid.uuid4())
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS session (id INTEGER PRIMARY KEY, token TEXT, heartbeat REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, session TEXT, created REAL, command TEXT, payload TEXT, response TEXT)")
            db.execute("DELETE FROM requests")
            db.execute("INSERT OR REPLACE INTO session VALUES (1, ?, ?)", (session, time.time()))
        self.session = session

    def disable(self) -> None:
        session, self.session = self.session, ""
        if not session:
            return
        try:
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM session WHERE token = ?", (session,))
                db.execute("DELETE FROM requests WHERE session = ?", (session,))
        except (OSError, sqlite3.Error, BridgeUnavailable):
            # No new requests can execute after local revocation, even if locked.
            # An inaccessible lease also expires; queued work cannot survive enable().
            pass

    @staticmethod
    def _active(db):
        row = db.execute("SELECT token, heartbeat FROM session WHERE id = 1").fetchone()
        age = time.time() - row["heartbeat"] if row else float("inf")
        if not row or not 0 <= age <= LEASE_SECONDS:
            raise BridgeUnavailable("Research MCP is off or the desktop is unavailable; enable it in GRANDE")
        return row["token"]

    def poll(self, handler) -> None:
        if not self.session:
            return
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE session SET heartbeat = ? WHERE token = ?", (time.time(), self.session))
            if not result.rowcount:
                raise BridgeUnavailable("The research connection session changed")
            db.execute("DELETE FROM requests WHERE created < ?", (time.time() - REQUEST_SECONDS,))
            rows = db.execute(
                "SELECT * FROM requests WHERE session = ? AND response IS NULL ORDER BY created LIMIT 8", (self.session,)
            ).fetchall()
            for row in rows:
                try:
                    if row["command"] not in COMMANDS:
                        raise ValueError("Unsupported research command")
                    payload = json.loads(row["payload"])
                    if not isinstance(payload, dict):
                        raise ValueError("Expected a research command object")
                    response = {"result": handler(row["command"], payload)}
                except (ValueError, RuntimeError) as exc:
                    response = {"error": str(exc)[:300]}
                except Exception:
                    response = {"error": "Research command failed; inspect the desktop"}
                db.execute("UPDATE requests SET response = ? WHERE id = ?", (json.dumps(response, allow_nan=False), row["id"]))

    async def request(self, command: str, payload: dict | None = None):
        if command not in COMMANDS:
            raise ValueError("Unsupported research command")
        encoded = json.dumps(payload or {}, allow_nan=False)
        if len(encoded) > 12000:
            raise ValueError("Research request is too large")
        request_id = str(uuid.uuid4())
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                session = self._active(db)
                db.execute("DELETE FROM requests WHERE created < ?", (time.time() - REQUEST_SECONDS,))
                if db.execute("SELECT count(*) FROM requests WHERE response IS NULL").fetchone()[0] >= MAX_PENDING:
                    raise BridgeUnavailable("Research command queue is full; try again after it drains")
                db.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?, NULL)",
                           (request_id, session, time.time(), command, encoded))
            deadline = time.monotonic() + REQUEST_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(0.1)
                with closing(self._connect()) as db:
                    if self._active(db) != session:
                        raise BridgeUnavailable("Research MCP permission was revoked; request discarded")
                    row = db.execute("SELECT response FROM requests WHERE id = ?", (request_id,)).fetchone()
                    if row and row[0] is not None:
                        response = json.loads(row[0])
                        if "error" in response:
                            raise ValueError(response["error"])
                        return response["result"]
            raise BridgeUnavailable("Desktop did not respond; request expired. Check its status before retrying")
        except sqlite3.Error as exc:
            raise BridgeUnavailable("Research connection unavailable; check the desktop") from exc
        finally:
            try:
                with closing(self._connect()) as db, db:
                    db.execute("DELETE FROM requests WHERE id = ?", (request_id,))
            except (OSError, sqlite3.Error, BridgeUnavailable):
                pass

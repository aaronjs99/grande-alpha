"""Explicit offline upgrade; original journals and durable backups are retained.

Stop all execution workers before calling this function. Both databases are
write-locked for the snapshot/copy. A failed or interrupted copy rolls back as
one SQLite transaction; retry either completes it or returns its committed
manifest. This never reactivates, rewrites, or invents an authority.
"""

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

TABLES = ("equity_v1_intents", "equity_v1_fills", "equity_v1_dispatch", "equity_v1_engine_lease")


def require_upgraded_legacy(target, source_path: Path):
    """Do not silently start a fresh journal alongside older execution records."""
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        return
    marker = target.execute("SELECT 1 FROM sqlite_master WHERE name='execution_upgrades'").fetchone()
    previous = (
        None
        if not marker
        else target.execute(
            "SELECT source_digest FROM execution_upgrades WHERE source_path=?", (str(source_path),)
        ).fetchone()
    )
    if previous is None:
        raise RuntimeError(
            "Legacy execution records require an explicit offline upgrade before opening the shared ledger"
        )
    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("BEGIN")
        records = {}
        for table in TABLES:
            columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
            records[table] = (columns, sorted(source.execute(f"SELECT * FROM {table}").fetchall(), key=repr))
        digest = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    if previous[0] != digest:
        raise RuntimeError("Legacy execution records changed after upgrade; manual reconciliation required")


def _snapshot(source: Path, destination: Path):
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(destination)) as backup:
            reader.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Execution backup failed integrity check")


def upgrade_execution_store(audit_path: Path, legacy_equity_path: Path, *, backup_dir: Path) -> dict:
    from grande_alpha.execution.equity_ledger import EQUITY_SCHEMA

    audit_path = Path(audit_path).resolve(strict=True)
    legacy_equity_path = Path(legacy_equity_path).resolve(strict=True)
    if audit_path == legacy_equity_path:
        raise ValueError("Upgrade requires two distinct source journals")
    with closing(sqlite3.connect(legacy_equity_path, timeout=0)) as source:
        with closing(sqlite3.connect(audit_path, timeout=0)) as target:
            source.execute("BEGIN IMMEDIATE")
            target.execute("PRAGMA foreign_keys=ON")
            target.execute("BEGIN IMMEDIATE")
            try:
                records = {}
                for table in TABLES:
                    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
                    if not columns:
                        raise ValueError(
                            f"Legacy execution journal is missing {table}; refusing partial import"
                        )
                    records[table] = (
                        columns,
                        sorted(source.execute(f"SELECT * FROM {table}").fetchall(), key=repr),
                    )
                digest = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
                marker_exists = target.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='execution_upgrades'"
                ).fetchone()
                if marker_exists:
                    previous = target.execute(
                        "SELECT source_digest,manifest FROM execution_upgrades WHERE source_path=?",
                        (str(legacy_equity_path),),
                    ).fetchone()
                    if previous:
                        if previous[0] != digest:
                            raise ValueError(
                                "Legacy execution journal changed after upgrade; manual reconciliation required"
                            )
                        target.rollback()
                        return json.loads(previous[1])
                destination = Path(backup_dir).resolve() / ("execution-upgrade-" + uuid.uuid4().hex)
                destination.mkdir(parents=True, exist_ok=False)
                audit_backup, equity_backup = destination / "audit.db", destination / "equity.db"
                _snapshot(audit_path, audit_backup)
                _snapshot(legacy_equity_path, equity_backup)
                for statement in EQUITY_SCHEMA.split(";"):
                    if statement.strip() and not statement.strip().upper().startswith("PRAGMA"):
                        target.execute(statement)
                for table, (columns, rows) in records.items():
                    actual_columns = [row[1] for row in target.execute(f"PRAGMA table_info({table})")]
                    if columns != actual_columns:
                        raise ValueError(f"Unsupported legacy execution schema in {table}")
                    # Refuse even identical pre-existing rows: guessing which journal
                    # is authoritative is not a migration decision.
                    if target.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                        raise ValueError(
                            f"Destination already contains {table}; manual reconciliation required"
                        )
                    placeholders = ",".join("?" for _ in columns)
                    target.executemany(f"INSERT INTO {table} VALUES({placeholders})", rows)
                    copied = sorted(target.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
                    if copied != rows:
                        raise ValueError(f"Execution record verification failed for {table}")
                if target.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Execution import violates journal references")
                result = {
                    "audit_backup": str(audit_backup),
                    "equity_backup": str(equity_backup),
                    "source_digest": digest,
                    "schema_version": 1,
                }
                target.execute(
                    "CREATE TABLE IF NOT EXISTS execution_upgrades (source_path TEXT PRIMARY KEY, source_digest TEXT NOT NULL, manifest TEXT NOT NULL)"
                )
                target.execute(
                    "INSERT INTO execution_upgrades VALUES(?,?,?)",
                    (str(legacy_equity_path), digest, json.dumps(result, sort_keys=True)),
                )
                target.commit()
                return result
            except BaseException:
                target.rollback()
                raise
            finally:
                source.rollback()

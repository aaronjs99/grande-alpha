"""Local record reads and interrupted settings saves, using disposable data only."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from grande_alpha.interfaces.cli.record_commands import (
    command_notifications,
    command_receipts,
    command_runs,
    command_status,
)
from grande_alpha.interfaces.cli.research_commands import command_evidence_show
from grande_alpha.persistence.store import AuditStore
from grande_alpha.research.sandbox import load_sandbox_config, save_sandbox_config
from grande_alpha.research.sandbox_models import SandboxConfig


class RecordReadChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ga-record-check-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "records # folder"
        self.path = self.folder / "grande_alpha.db"
        directory = patch("grande_alpha.persistence.store.data_dir", return_value=self.folder)
        directory.start()
        self.addCleanup(directory.stop)

    def call(self, command, **overrides):
        values = dict(json=True, width=90, limit=30, id=None, after=0, unread=False, ack=None)
        values.update(overrides)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = command(Namespace(**values))
        return result, json.loads(output.getvalue())

    def populate(self):
        store = AuditStore(self.path)
        try:
            store.receipt("fixture", "Disposable receipt", severity="warning")
            store.record_sandbox_run("fixture-run", "synthetic", "2026-01-05", "2026-01-06",
                                     {}, {"final_equity": 100}, [])
            with store._connection:
                store._connection.execute(
                    """INSERT INTO research_promotions(created_at,dataset_hash,strategy_fingerprint,
                       policy_version,status,source,replay_end,gates_json,risk_envelope_json)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    ("2026-01-06", "fixture", "fixture", 1, "SHADOW_ONLY", "synthetic",
                     "2026-01-06", "[]", "{}"),
                )
        finally:
            store.close()

    def test_missing_receipts_are_empty_without_creating_a_directory(self):
        self.assertEqual(self.call(command_receipts), (0, []))
        self.assertFalse(self.folder.exists())

    def test_missing_runs_are_empty_without_creating_a_directory(self):
        self.assertEqual(self.call(command_runs), (0, []))
        self.assertFalse(self.folder.exists())

    def test_missing_notifications_are_empty_without_creating_a_directory(self):
        self.assertEqual(self.call(command_notifications), (0, []))
        self.assertFalse(self.folder.exists())

    def test_missing_status_reports_not_initialized_without_creating_a_directory(self):
        result, payload = self.call(command_status)
        self.assertEqual(result, 0)
        self.assertEqual(payload["Local records"]["value"], "Not initialized")
        self.assertFalse(self.folder.exists())

    def test_missing_specific_run_and_evidence_are_errors_without_creating_records(self):
        for command in (command_runs, command_evidence_show):
            with self.subTest(command=command.__name__), self.assertRaisesRegex(ValueError, "No .*|not initialized"):
                self.call(command, id="missing")
            self.assertFalse(self.folder.exists())

    def test_existing_records_are_read_without_schema_initialization_or_file_changes(self):
        self.populate()
        original = self.path.read_bytes()
        with patch("grande_alpha.persistence.store.SchemaRepository._initialize",
                   side_effect=AssertionError("A record reader must not initialize the schema")):
            _, receipts = self.call(command_receipts)
            self.assertTrue(any(row["summary"] == "Disposable receipt" for row in receipts))
            _, runs = self.call(command_runs)
            self.assertEqual(runs[0]["run_id"], "fixture-run")
            _, run = self.call(command_runs, id="fixture-run")
            self.assertEqual(run["metrics"], {"final_equity": 100})
            _, notifications = self.call(command_notifications)
            self.assertEqual(notifications[0]["summary"], "Disposable receipt")
            self.assertIsNone(notifications[0]["acknowledged_at"])
            _, status = self.call(command_status)
            self.assertEqual(status["Latest historical receipt"]["value"], "SHADOW_ONLY")
            _, evidence = self.call(command_evidence_show)
            self.assertEqual(evidence["status"], "SHADOW_ONLY")
        self.assertEqual(self.path.read_bytes(), original)

    def test_read_only_store_rejects_accidental_record_writes(self):
        self.populate()
        original = self.path.read_bytes()
        store = AuditStore(self.path, read_only=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                store.receipt("fixture", "Must not persist")
        finally:
            store.close()
        self.assertEqual(self.path.read_bytes(), original)

    def test_committed_wal_records_are_read_while_the_writer_remains_open(self):
        self.populate()
        writer = AuditStore(self.path)
        try:
            writer.receipt("fixture", "Committed in the open writer WAL")
            wal = self.path.with_name(self.path.name + "-wal")
            original = self.path.read_bytes(), wal.read_bytes()
            _, rows = self.call(command_receipts)
            self.assertEqual(rows[0]["summary"], "Committed in the open writer WAL")
            self.assertEqual((self.path.read_bytes(), wal.read_bytes()), original)
            writer.receipt("fixture", "Writer still works after the read")
        finally:
            writer.close()

    def test_unrelated_database_is_rejected_and_preserved(self):
        self.folder.mkdir()
        with contextlib.closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated_note (message TEXT)")
            connection.execute("INSERT INTO unrelated_note VALUES ('keep')")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "records.*(incompatible|unreadable)|cannot.*read"):
            self.call(command_receipts)
        self.assertEqual(self.path.read_bytes(), original)
        with contextlib.closing(sqlite3.connect(self.path)) as connection:
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        self.assertEqual(tables, [("unrelated_note",)])

    def test_incompatible_database_is_rejected_without_adding_missing_tables(self):
        self.populate()
        with contextlib.closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DROP TABLE sandbox_execution_events")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "records.*(incompatible|unreadable)|cannot.*read"):
            self.call(command_runs, id="fixture-run")
        self.assertEqual(self.path.read_bytes(), original)

    def test_malformed_database_is_rejected_and_preserved(self):
        self.folder.mkdir()
        original = b"This is not a SQLite database"
        self.path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "records.*(incompatible|unreadable)|cannot.*read"):
            self.call(command_receipts)
        self.assertEqual(self.path.read_bytes(), original)

    def test_incompatible_columns_are_rejected_without_repairing_the_schema(self):
        self.populate()
        with contextlib.closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("ALTER TABLE receipts RENAME COLUMN summary TO old_summary")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "records.*(incompatible|unreadable)|cannot.*read"):
            self.call(command_receipts)
        self.assertEqual(self.path.read_bytes(), original)

    def test_notification_acknowledgment_remains_an_explicit_write(self):
        self.populate()
        _, rows = self.call(command_notifications)
        notification_id = rows[0]["id"]
        _, acknowledged = self.call(command_notifications, ack=notification_id)
        self.assertIsNotNone(acknowledged[0]["acknowledged_at"])
        self.assertEqual(self.call(command_notifications, unread=True), (0, []))


class AtomicSandboxSaveChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ga-settings-check-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "sandbox_config.json"
        self.previous = b'{"lookback_days":9}'
        self.path.write_bytes(self.previous)
        target = patch("grande_alpha.research.sandbox.sandbox_config_path", return_value=self.path)
        target.start()
        self.addCleanup(target.stop)

    def assert_preserved(self):
        self.assertEqual(self.path.read_bytes(), self.previous)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertEqual(load_sandbox_config().lookback_days, 9)

    @contextlib.contextmanager
    def interrupted_write(self):
        original_open = Path.open
        target = self.path

        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream

            def write(self, value):
                self.stream.write(value[:12])
                self.stream.flush()
                raise OSError("simulated disk full")

        @contextlib.contextmanager
        def open_file(path, *args, **kwargs):
            with original_open(path, *args, **kwargs) as stream:
                mode = args[0] if args else kwargs.get("mode", "r")
                yield FailingWriter(stream) if path.parent == target.parent and mode in {"w", "x"} else stream

        with patch.object(Path, "open", open_file):
            yield

    def test_successful_save_roundtrips_and_leaves_no_temporary_file(self):
        settings = SandboxConfig(lookback_days=15, strategy_name="cash", signal_cost_bps=0)
        save_sandbox_config(settings)
        self.assertEqual(asdict(load_sandbox_config()), asdict(settings))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_interrupted_write_preserves_previous_settings_and_cleans_temporary_file(self):
        with self.interrupted_write(), self.assertRaises(OSError):
            save_sandbox_config(SandboxConfig())
        self.assert_preserved()

    def test_fsync_failure_preserves_previous_settings_and_cleans_temporary_file(self):
        with patch("os.fsync", side_effect=OSError("simulated flush failure")), self.assertRaises(OSError):
            save_sandbox_config(SandboxConfig())
        self.assert_preserved()

    def test_replace_failure_preserves_previous_settings_and_cleans_temporary_file(self):
        with patch.object(Path, "replace", side_effect=OSError("simulated replacement failure")), self.assertRaises(OSError):
            save_sandbox_config(SandboxConfig())
        self.assert_preserved()

    def test_interrupted_first_save_does_not_leave_a_truncated_settings_file(self):
        self.path.unlink()
        with self.interrupted_write(), self.assertRaises(OSError):
            save_sandbox_config(SandboxConfig())
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

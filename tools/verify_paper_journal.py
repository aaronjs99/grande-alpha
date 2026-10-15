"""Read-only saved paper pages and CSV exports, using disposable journals only."""

from __future__ import annotations

import contextlib
import copy
import csv
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grande_alpha.research.paper import journal
from grande_alpha.research.paper.journal import read_journal, save_journal_csv
from grande_alpha.research.paper.ledger import PaperLedger

STAMP = "2026-10-14T18:00:00+00:00"


def fill(number: int) -> dict:
    return {"number": number, "key": "equity:QQQ", "side": "buy" if number % 2 else "sell",
            "quantity": "0.1234567890123456789012345678", "price": "100.00000000000000000000001",
            "realized_pnl": "0" if number % 2 else "-0.000000000000000000000001",
            "filled_at": STAMP, "source_ids": ["private-source-not-exported"]}


def state(session_id: str, count: int = 0, source: str = "demo") -> dict:
    return {"session_id": session_id, "source": source, "created_at": STAMP,
            "observed_at": STAMP if count else None, "initial_cash": "1000.00", "cash": "900.000000000000000000000001",
            "trade_cash": "100.00", "realized_pnl": "-0.000000000000000000000001", "fill_count": count,
            "positions": {}, "fills": [fill(n) for n in range(max(1, count - 199), count + 1)],
            "account_number": "PRIVATE-ACCOUNT", "token": "PRIVATE-TOKEN"}


def put_session(path: Path, value: dict) -> None:
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO paper_sessions VALUES (?,?)", (value["session_id"], json.dumps(value)))
        db.executemany("INSERT INTO paper_fills VALUES (?,?,?)",
                       [(value["session_id"], n, json.dumps(fill(n))) for n in range(1, value["fill_count"] + 1)])


class PaperJournalChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="grande-paper-journal-check-")
        self.root = Path(self.scratch.name)
        self.path = self.root / "agent-paper.db"

    def tearDown(self) -> None:
        self.scratch.cleanup()

    def create(self) -> None:
        ledger = PaperLedger(self.path)
        ledger.close()

    def fixture(self, count: int = 3) -> dict:
        self.create()
        value = state("fixture-session", count)
        put_session(self.path, value)
        return value

    def test_missing_database_returns_empty_without_creating_directories_or_files(self) -> None:
        absent = self.root / "absent" / "agent-paper.db"
        result = read_journal(absent)
        self.assertEqual(result["sessions"], [])
        self.assertIsNone(result["session"])
        self.assertEqual(result["fills"], [])
        self.assertEqual(result["fill_count"], 0)
        self.assertIsNone(result["next_before"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_real_ledger_reopens_and_latest_session_matches_its_insertion_order(self) -> None:
        ledger = PaperLedger(self.path)
        ledger.start("demo", "1000.0000000000000000001", "100")
        first = ledger.state["session_id"]
        ledger.start("broker_quotes", "2000", "200")
        second = ledger.state["session_id"]
        ledger.close()
        result = read_journal(self.path)
        self.assertEqual([row["session_id"] for row in result["sessions"]], [second, first])
        self.assertEqual(result["session"]["session_id"], second)
        original = read_journal(self.path, session_id=first)
        self.assertEqual(original["session"]["initial_cash"], "1000.0000000000000000001")
        self.assertIn("synthetic", original["session"]["limitations"].lower())
        self.assertIn("stale", result["session"]["limitations"].lower())
        self.assertIsNone(read_journal(self.path, session_id="missing")["session"])

    def test_all_durable_fills_beyond_the_200_row_state_cache_are_pageable(self) -> None:
        value = self.fixture(305)
        self.assertEqual(len(value["fills"]), 200)
        numbers, before = [], None
        while True:
            page = read_journal(self.path, before=before)
            self.assertEqual(page["fill_count"], 305)
            self.assertEqual(page["session"]["fill_count"], 305)
            self.assertLessEqual(len(page["fills"]), 100)
            numbers.extend(row["number"] for row in page["fills"])
            before = page["next_before"]
            if before is None:
                break
            self.assertEqual(before, min(row["number"] for row in page["fills"]))
        self.assertEqual(numbers, list(range(305, 0, -1)))
        self.assertEqual(len(set(numbers)), 305)
        self.assertEqual(read_journal(self.path, before=1)["fills"], [])

    def test_session_list_and_pages_have_explicit_bounds(self) -> None:
        self.create()
        for number in range(105):
            put_session(self.path, state(f"session-{number}"))
        self.assertEqual(len(read_journal(self.path)["sessions"]), 100)
        self.assertEqual(read_journal(self.path, session_id="session-0")["session"]["session_id"], "session-0")
        for options in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"before": 0},
                        {"before": True}, {"before": 1.5}, {"before": 2**63}, {"session_id": []}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                read_journal(self.path, **options)

    def test_read_never_changes_database_bytes_schema_or_creates_sidecars(self) -> None:
        self.fixture()
        before = self.path.read_bytes()
        files = set(self.root.iterdir())
        statements = []
        real_connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            self.assertTrue(kwargs.get("uri"))
            self.assertIn("mode=ro", args[0])
            db = real_connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with patch.object(journal.sqlite3, "connect", side_effect=tracked_connect):
            read_journal(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), files)
        self.assertTrue(any("query_only" in statement.lower() for statement in statements))
        self.assertTrue(all(not statement.lstrip().upper().startswith(("CREATE", "UPDATE", "INSERT", "DELETE", "ALTER"))
                            for statement in statements))

    def test_metadata_count_and_fills_share_one_snapshot_during_a_writer_commit(self) -> None:
        value = self.fixture(3)
        real_connect = sqlite3.connect
        with contextlib.closing(real_connect(self.path)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
        updated = False

        class SnapshotConnection(sqlite3.Connection):
            def execute(connection, sql, *args, **kwargs):
                nonlocal updated
                cursor = super().execute(sql, *args, **kwargs)
                if "paper_sessions" in sql and "ORDER BY" in sql and not updated:
                    updated = True
                    changed = {**value, "fill_count": 4}
                    with contextlib.closing(real_connect(self.path)) as writer, writer:
                        writer.execute("UPDATE paper_sessions SET payload=? WHERE id=?", (json.dumps(changed), value["session_id"]))
                        writer.execute("INSERT INTO paper_fills VALUES (?,?,?)", (value["session_id"], 4, json.dumps(fill(4))))
                return cursor

        def connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=SnapshotConnection)

        with patch.object(journal.sqlite3, "connect", side_effect=connect):
            result = read_journal(self.path)
        self.assertTrue(updated)
        self.assertEqual(result["fill_count"], 3)
        self.assertEqual([row["number"] for row in result["fills"]], [3, 2, 1])
        self.assertEqual(read_journal(self.path)["fill_count"], 4)

    def test_malformed_session_records_fail_without_changing_the_source(self) -> None:
        value = self.fixture()
        cases = ({"cash": "NaN"}, {"cash": "1e999999999999999999999999999999999999"},
                 {"cash": 100}, {"cash": True}, {"source": "live"},
                 {"created_at": "yesterday"}, {"created_at": "2026-10-14T18:00:00"},
                 {"observed_at": 0}, {"fill_count": True}, {"session_id": "wrong"},
                 {"positions": {"equity:QQQ": {"quantity": "1", "bid": "Infinity"}}})
        for change in cases:
            with self.subTest(change=change):
                with contextlib.closing(sqlite3.connect(self.path)) as db, db:
                    db.execute("UPDATE paper_sessions SET payload=?", (json.dumps({**value, **change}),))
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    read_journal(self.path)
                self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_or_oversized_fills_are_not_silently_skipped(self) -> None:
        self.fixture(1)
        for change in ({"number": 2}, {"number": True}, {"side": "short"}, {"key": []},
                       {"quantity": "0"}, {"price": "Infinity"}, {"realized_pnl": "NaN"},
                       {"filled_at": "invalid"}, {"source_ids": ["x" * 100_000]}):
            with self.subTest(change=list(change)):
                with contextlib.closing(sqlite3.connect(self.path)) as db, db:
                    db.execute("UPDATE paper_fills SET payload=?", (json.dumps({**fill(1), **change}),))
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    read_journal(self.path)
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_json_oversized_sessions_and_wrong_schema_fail_clearly(self) -> None:
        self.fixture()
        for payload in ('{"session_id":"x","session_id":"y"}', '{"value":NaN}',
                        '{"value":1e999999999999999999999999999999999999}',
                        json.dumps({"huge": "x" * 1_100_000}), "[]", "not JSON"):
            with self.subTest(payload=payload[:40]):
                with contextlib.closing(sqlite3.connect(self.path)) as db, db:
                    db.execute("UPDATE paper_sessions SET payload=?", (payload,))
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    read_journal(self.path)
                self.assertEqual(self.path.read_bytes(), before)
        other = self.root / "unrelated.db"
        with contextlib.closing(sqlite3.connect(other)) as db, db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
        before = other.read_bytes()
        with self.assertRaises(ValueError):
            read_journal(other)
        self.assertEqual(other.read_bytes(), before)
        invalid = self.root / "broken.db"
        invalid.write_bytes(b"This is not a SQLite database")
        with self.assertRaises(ValueError):
            read_journal(invalid)
        self.assertEqual(invalid.read_bytes(), b"This is not a SQLite database")

    def test_missing_fill_or_wrong_saved_count_is_not_a_complete_journal(self) -> None:
        value = self.fixture(3)
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM paper_fills WHERE number=2")
        with self.assertRaises(ValueError):
            read_journal(self.path)
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE paper_sessions SET payload=?", (json.dumps({**value, "fill_count": 2}),))
        with self.assertRaises(ValueError):
            read_journal(self.path)

    def test_csv_preserves_decimal_signs_and_protects_text_from_formulas(self) -> None:
        value = self.fixture(2)
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            changed = {**fill(2), "key": " \t=HYPERLINK(\"https://invalid.example\")"}
            db.execute("UPDATE paper_fills SET payload=? WHERE number=2", (json.dumps(changed),))
        snapshot = read_journal(self.path)
        target = self.root / "page.csv"
        before = self.path.read_bytes()
        save_journal_csv(snapshot, target)
        with target.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["key"], "'" + changed["key"])
        self.assertEqual(rows[0]["realized_pnl"], "-0.000000000000000000000001")
        self.assertEqual(rows[0]["quantity"], fill(2)["quantity"])
        self.assertEqual(rows[0]["price"], fill(2)["price"])
        self.assertEqual(rows[0]["session_id"], value["session_id"])
        self.assertNotIn("PRIVATE", target.read_text(encoding="utf-8"))
        self.assertNotIn("private-source", target.read_text(encoding="utf-8"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_export_uses_only_the_immutable_loaded_page_after_database_changes(self) -> None:
        self.fixture(8)
        snapshot = read_journal(self.path, limit=3)
        self.path.unlink()
        target = self.root / "snapshot.csv"
        save_journal_csv(snapshot, target)
        with target.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual([int(row["number"]) for row in rows], [8, 7, 6])
        self.assertFalse(self.path.exists())

    def test_existing_destination_requires_explicit_replacement(self) -> None:
        self.fixture()
        snapshot = read_journal(self.path)
        target = self.root / "page.csv"
        target.write_bytes(b"Preserve existing export")
        with self.assertRaises(ValueError):
            save_journal_csv(snapshot, target)
        self.assertEqual(target.read_bytes(), b"Preserve existing export")
        save_journal_csv(snapshot, target, overwrite=True)
        self.assertIn("quantity", target.read_text(encoding="utf-8"))

    def test_export_refuses_source_database_sidecars_and_hard_link_aliases(self) -> None:
        self.fixture()
        snapshot = read_journal(self.path)
        before = self.path.read_bytes()
        for suffix in ("", "-wal", "-shm", "-journal"):
            target = Path(str(self.path) + suffix)
            if suffix:
                target.write_bytes(b"Preserve sidecar")
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                save_journal_csv(snapshot, target, overwrite=True)
            if suffix:
                self.assertEqual(target.read_bytes(), b"Preserve sidecar")
        alias = self.root / "alias.csv"
        os.link(self.path, alias)
        with self.assertRaises(ValueError):
            save_journal_csv(snapshot, alias, overwrite=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_export_refuses_a_symbolic_link_alias_of_the_database(self) -> None:
        self.fixture()
        snapshot = read_journal(self.path)
        alias = self.root / "linked.csv"
        try:
            alias.symlink_to(self.path)
        except OSError as error:
            self.skipTest(f"Symbolic links are unavailable: {error}")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            save_journal_csv(snapshot, alias, overwrite=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_replace_preserves_existing_target_and_removes_temporary_file(self) -> None:
        self.fixture()
        snapshot = read_journal(self.path)
        target = self.root / "page.csv"
        target.write_bytes(b"Preserve existing export")
        with patch.object(journal.os, "replace", side_effect=OSError("Fixture replacement failed")):
            with self.assertRaises(OSError):
                save_journal_csv(snapshot, target, overwrite=True)
        self.assertEqual(target.read_bytes(), b"Preserve existing export")
        self.assertEqual(list(self.root.glob(".paper-journal-*")), [])

    def test_export_revalidates_snapshot_numbers_instead_of_accepting_formula_text_as_numeric(self) -> None:
        self.fixture()
        snapshot = copy.deepcopy(read_journal(self.path))
        snapshot["fills"][0]["price"] = "=1+1"
        target = self.root / "invalid.csv"
        with self.assertRaises(ValueError):
            save_journal_csv(snapshot, target)
        self.assertFalse(target.exists())

    def test_saved_mark_equity_is_derived_without_float_or_decimal_context_rounding(self) -> None:
        self.create()
        value = {**state("exact-equity"), "cash": "0.1000000000000000000000000001",
                 "positions": {"equity:QQQ": {"quantity": "0.2", "bid": "0.3"}}}
        put_session(self.path, value)
        self.assertEqual(read_journal(self.path)["session"]["equity"], "0.1600000000000000000000000001")

    def test_partial_csv_write_failure_preserves_target_and_removes_temporary_file(self) -> None:
        self.fixture()
        snapshot = read_journal(self.path)
        target = self.root / "page.csv"
        target.write_bytes(b"Preserve existing export")
        real_writer = csv.writer

        class FailedWriter:
            def __init__(self, output):
                self.writer = real_writer(output)

            def writerow(self, row):
                return self.writer.writerow(row)

            def writerows(self, rows):
                self.writer.writerow(rows[0])
                raise OSError("Fixture partial write failed")

        with patch.object(journal.csv, "writer", side_effect=FailedWriter):
            with self.assertRaises(OSError):
                save_journal_csv(snapshot, target, overwrite=True)
        self.assertEqual(target.read_bytes(), b"Preserve existing export")
        self.assertEqual(list(self.root.glob(".paper-journal-*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from grande_alpha.execution.equity_ledger import EquityLedger
from grande_alpha.persistence import store as storage
from grande_alpha.persistence.store import AuditStore


class ExecutionUpgrade(unittest.TestCase):
    def test_shared_open_refuses_unmigrated_legacy_journal(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        legacy = EquityLedger(directory / "equity_v1.db")
        legacy.close()
        audit = AuditStore(directory / "audit.db")
        self.addCleanup(audit.close)
        with self.assertRaisesRegex(RuntimeError, "explicit.*upgrade"):
            EquityLedger(audit)

    def test_interrupted_copy_rolls_back_and_retry_preserves_rows(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        audit_path, legacy_path = directory / "audit.db", directory / "equity.db"
        audit = AuditStore(audit_path)
        EquityLedger(audit)
        audit._connection.execute(
            "CREATE TRIGGER reject_copy BEFORE INSERT ON equity_v1_fills BEGIN SELECT RAISE(ABORT, 'interrupted'); END"
        )
        audit.close()
        legacy = EquityLedger(legacy_path)
        with legacy._db:
            legacy._db.execute(
                "INSERT INTO equity_v1_intents VALUES('ref','account','authority','{}','unknown','order')"
            )
            legacy._db.execute(
                "INSERT INTO equity_v1_fills VALUES('account','execution','ref','AAPL','buy',2,20,0,'2026-09-23T12:00:00+00:00')"
            )
        legacy.close()
        with self.assertRaises(sqlite3.IntegrityError):
            storage.upgrade_execution_store(audit_path, legacy_path, backup_dir=directory / "backups")
        with closing(sqlite3.connect(audit_path)) as check:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM equity_v1_intents").fetchone()[0], 0)
            check.execute("DROP TRIGGER reject_copy")
            check.commit()
        self.assertEqual(len(list((directory / "backups").glob("*/audit.db"))), 1)
        storage.upgrade_execution_store(audit_path, legacy_path, backup_dir=directory / "backups")
        with closing(sqlite3.connect(audit_path)) as check:
            self.assertEqual(
                check.execute("SELECT ref,order_id FROM equity_v1_intents").fetchall(), [("ref", "order")]
            )

    def test_explicit_upgrade_preserves_identity_and_stopped_authority(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        audit_path, legacy_path = directory / "audit.db", directory / "equity.db"
        audit = AuditStore(audit_path)
        audit.register_standing("do-not-reactivate", "exact-scope")
        audit.stop_standing("do-not-reactivate")
        audit.close()
        ledger = EquityLedger(legacy_path)
        with ledger._db:
            ledger._db.execute(
                "INSERT INTO equity_v1_intents VALUES(?,?,?,?,?,?)",
                ("exact-ref", "exact-account", "exact-authority", "{}", "unknown", "exact-order"),
            )
            ledger._db.execute(
                "INSERT INTO equity_v1_fills VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "exact-account",
                    "exact-execution",
                    "exact-ref",
                    "AAPL",
                    "buy",
                    2,
                    20,
                    0,
                    "2026-09-23T12:00:00+00:00",
                ),
            )
        ledger.close()
        self.assertTrue(hasattr(storage, "upgrade_execution_store"), "Upgrade must be explicit")
        result = storage.upgrade_execution_store(audit_path, legacy_path, backup_dir=directory / "backups")
        self.assertTrue(Path(result["audit_backup"]).is_file())
        self.assertTrue(Path(result["equity_backup"]).is_file())
        audit = AuditStore(audit_path)
        self.addCleanup(audit.close)
        merged = EquityLedger(audit, legacy_path=legacy_path)
        self.assertEqual(
            merged.unresolved("exact-account"),
            [{"ref": "exact-ref", "state": "unknown", "order_id": "exact-order"}],
        )
        self.assertEqual(merged.inventory("exact-account"), {"AAPL": 2.0})
        self.assertFalse(audit.standing_active("do-not-reactivate", "exact-scope"))
        with closing(sqlite3.connect(result["audit_backup"])) as backup:
            self.assertIsNone(
                backup.execute("SELECT name FROM sqlite_master WHERE name='equity_v1_intents'").fetchone()
            )
        audit.close()
        again = storage.upgrade_execution_store(audit_path, legacy_path, backup_dir=directory / "backups")
        self.assertEqual(result, again)
        with closing(sqlite3.connect(legacy_path)) as changed:
            changed.execute("UPDATE equity_v1_intents SET state='terminal'")
            changed.commit()
        reopened = AuditStore(audit_path)
        self.addCleanup(reopened.close)
        with self.assertRaisesRegex(RuntimeError, "changed after upgrade"):
            EquityLedger(reopened, legacy_path=legacy_path)


if __name__ == "__main__":
    unittest.main()

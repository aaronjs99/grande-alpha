import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from grande_alpha.execution.equity_ledger import EquityLedger
from grande_alpha.persistence.store import AuditStore


class PersistenceTransactions(unittest.TestCase):
    def test_notifications_only_escape_successful_commit(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        store = AuditStore(Path(directory) / "execution.db")
        self.addCleanup(store.close)
        displayed = []
        store.notification_sink = displayed.append
        with self.assertRaises(ValueError):
            with store.transaction():
                store.receipt("test", "discard", severity="warning")
                self.assertEqual(displayed, [])
                raise ValueError("abort")
        self.assertEqual(displayed, [])
        with store.transaction():
            store.receipt("test", "keep", severity="warning")
            self.assertEqual(displayed, [])
        self.assertEqual(displayed, [{"severity": "warning", "summary": "keep"}])

    def test_cross_repository_failure_rolls_back_authority_lease_and_receipt(self):
        with self.subTest():
            directory = self.enterContext(tempfile.TemporaryDirectory())
            store = AuditStore(Path(directory) / "execution.db")
            self.addCleanup(store.close)
            self.assertTrue(hasattr(store, "transaction"), "Explicit transactions are required")
            ledger = EquityLedger(store)
            now = datetime.now(UTC)
            with self.assertRaisesRegex(ValueError, "abort"):
                with store.transaction():
                    store.register_standing("authority", "scope")
                    ledger.acquire_lease("account", "owner", now=now)
                    store.record_mixed_daily_pnl(
                        "account", "2026-09-23", 10, 25, scope_digest="scope",
                        recovery_delay=None, recovery_unit="manual", observed_at=now,
                    )
                    store.receipt("test", "must roll back")
                    raise ValueError("abort")
            self.assertFalse(store.standing_active("authority", "scope"))
            self.assertFalse(ledger.lease_active("account", "owner", now=now))
            self.assertEqual(store.recent_receipts(), [])
            with self.assertRaisesRegex(ValueError, "history is missing"):
                store.record_mixed_daily_pnl(
                    "account", "2026-09-23", 10, 25, scope_digest="scope",
                    recovery_delay=None, recovery_unit="manual", observed_at=now, require_existing=True,
                )

    def test_nested_failure_is_savepoint_and_outer_can_commit(self):
        with self.subTest():
            directory = self.enterContext(tempfile.TemporaryDirectory())
            store = AuditStore(Path(directory) / "execution.db")
            self.addCleanup(store.close)
            self.assertTrue(hasattr(store, "transaction"), "Explicit transactions are required")
            with store.transaction():
                store.register_standing("keep", "scope")
                with self.assertRaises(ValueError):
                    with store.transaction():
                        store.register_standing("discard", "scope")
                        raise ValueError("abort")
            self.assertTrue(store.standing_active("keep", "scope"))
            self.assertFalse(store.standing_active("discard", "scope"))


if __name__ == "__main__":
    unittest.main()

from pathlib import Path

from momentum_trader.models import OrderIntent
from momentum_trader.storage import AuditStore


def test_receipts_and_idempotent_intents_are_persisted(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    store.receipt("test", "hello", {"value": 1})
    order = OrderIntent("ref-1", "TQQQ", "buy", "test", dollar_amount=10.0)
    store.record_intent(order)
    store.update_intent(order.ref_id, "broker-1", "queued")
    receipts = store.recent_receipts()
    assert receipts[0]["summary"] == "hello"
    store.close()


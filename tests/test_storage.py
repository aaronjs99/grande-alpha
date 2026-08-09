from pathlib import Path

from grande_alpha.models import OrderIntent
from grande_alpha.storage import AuditStore


def test_receipts_and_idempotent_intents_are_persisted(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    store.receipt("test", "hello", {"value": 1})
    order = OrderIntent("ref-1", "TQQQ", "buy", "test", dollar_amount=10.0)
    store.record_intent(order)
    store.update_intent(order.ref_id, "broker-1", "queued")
    receipts = store.recent_receipts()
    assert receipts[0]["summary"] == "hello"
    store.close()


def test_research_fund_requires_plan_then_external_confirmation(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    entry_id = store.plan_research_contribution(
        period="2026-08",
        realized_profit=100.0,
        fees=5.0,
        tax_reserve=25.0,
        contribution_rate=0.25,
        notes="personal realized profit only",
    )

    entry = store.research_fund_entries()[0]
    assert entry["id"] == entry_id
    assert entry["eligible_contribution"] == 17.5
    assert entry["status"] == "planned"
    assert store.confirmed_research_total() == 0.0

    store.confirm_research_contribution(entry_id, "bank-reference-123")
    confirmed = store.research_fund_entries()[0]
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmation_reference"] == "bank-reference-123"
    assert store.confirmed_research_total() == 17.5
    store.close()


def test_research_fund_never_allocates_losses_and_rejects_duplicate_confirmation(
    tmp_path: Path,
) -> None:
    store = AuditStore(tmp_path / "audit.db")
    entry_id = store.plan_research_contribution("2026-07", -10.0, 0.0, 0.0, 1.0)
    assert store.research_fund_entries()[0]["eligible_contribution"] == 0.0
    store.confirm_research_contribution(entry_id, "external-reference")

    try:
        store.confirm_research_contribution(entry_id, "second-reference")
    except ValueError as exc:
        assert "already confirmed" in str(exc)
    else:
        raise AssertionError("Duplicate fund confirmation should fail")
    store.close()

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from grande_alpha.equity_execution import EquityOrderIntent, EquityScope, assess_ticket
from grande_alpha.loss_recovery import recovery_deadline
from grande_alpha.models import Account, Portfolio, Position, Quote
from grande_alpha.storage import AuditStore


def test_calendar_month_clamps_and_elapsed_hour_crosses_dst():
    january = datetime(2026, 1, 31, 15, 0, tzinfo=UTC)
    assert recovery_deadline(january, 1, "months").astimezone(
        ZoneInfo("America/New_York")
    ).date().isoformat() == "2026-02-28"
    start = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)
    assert recovery_deadline(start, 2, "hours") == start + timedelta(hours=2)
    with pytest.raises(ValueError, match="positive integer"):
        recovery_deadline(start, 0, "days")


def test_manual_pause_survives_new_day_without_resetting_daily_loss(tmp_path):
    store = AuditStore(tmp_path / "risk.db")
    first = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
    try:
        store.record_daily_risk("account-1", "2026-09-23", 100, 25,
                                scope_digest="candidate", observed_at=first)
        breached = store.record_daily_risk("account-1", "2026-09-23", 70, 25,
                                           scope_digest="candidate", observed_at=first + timedelta(minutes=5))
        assert breached["loss_latched"] and breached["recovery_blocked"]
        next_day = store.record_daily_risk("account-1", "2026-09-24", 70, 25,
                                           scope_digest="candidate", carry_previous_observation=True,
                                           observed_at=first + timedelta(days=1))
        assert not next_day["loss_latched"] and next_day["recovery_blocked"]
        assert store.acknowledge_loss_recovery("account-1", observed_at=first + timedelta(days=1))
        resumed = store.record_daily_risk("account-1", "2026-09-24", 70, 25,
                                          scope_digest="candidate", observed_at=first + timedelta(days=1))
        assert not resumed["recovery_blocked"]
    finally:
        store.close()


def test_loss_stop_blocks_entry_but_preserves_reconciled_exit():
    now = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)
    scope = EquityScope("account-1", ("TQQQ",), now - timedelta(days=1), None,
                        100, 100, 100, 25, 5, 8, 20)
    account = Account("account-1", "Agentic", "cash", True, "active")
    portfolio = Portfolio(100, 50, 50)
    positions = [Position("TQQQ", 1, 1, 50)]
    quote = Quote("TQQQ", 49.99, 50.01, 50, now, now, now)
    common = dict(account=account, portfolio=portfolio, positions=positions,
                  quotes={"TQQQ": quote}, now=now, reconciled_at=now,
                  tradable=True, fractional=True, daily_notional=100, daily_orders=5,
                  daily_loss_latched=True, has_unresolved_orders=False)
    sell = EquityOrderIntent(str(uuid4()), "TQQQ", "sell", "risk reduction",
                             quantity=1, created_at=now)
    buy = EquityOrderIntent(str(uuid4()), "TQQQ", "buy", "new exposure",
                            dollar_amount=10, created_at=now)
    assert assess_ticket(sell, scope, **common)["allowed"] is True
    assert "DAILY_LOSS_STOP" in assess_ticket(buy, scope, **common)["reasons"]


def test_short_timer_does_not_clear_same_day_loss_latch(tmp_path):
    store = AuditStore(tmp_path / "risk.db")
    first = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
    try:
        store.record_daily_risk("account-1", "2026-09-23", 100, 25,
                                scope_digest="candidate", recovery_delay=5,
                                recovery_unit="minutes", observed_at=first)
        store.record_daily_risk("account-1", "2026-09-23", 70, 25,
                                scope_digest="candidate", recovery_delay=5,
                                recovery_unit="minutes", observed_at=first + timedelta(minutes=1))
        after_timer = store.record_daily_risk("account-1", "2026-09-23", 70, 25,
                                              scope_digest="candidate", recovery_delay=5,
                                              recovery_unit="minutes", observed_at=first + timedelta(minutes=7))
        assert after_timer["loss_latched"] is True
        assert after_timer["recovery_blocked"] is False
    finally:
        store.close()

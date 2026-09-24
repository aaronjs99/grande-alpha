from datetime import UTC, datetime, timedelta

from grande_alpha.domain.models import Quote
from grande_alpha.execution.equity_ledger import EquityLedger
from grande_alpha.persistence.store import AuditStore

NOW = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)


def test_mark_to_market_counts_partial_realized_and_unrealized_without_cash_flows(tmp_path):
    ledger = EquityLedger(tmp_path / "equity.db")
    try:
        for ref in ("buy", "sell"):
            ledger._db.execute(
                "INSERT INTO equity_v1_intents VALUES(?,?,?,?,?,?)",
                (ref, "account-1", "authority", "{}", "terminal", ref),
            )
        ledger._db.executemany(
            "INSERT INTO equity_v1_fills VALUES(?,?,?,?,?,?,?,?,?)",
            [("account-1", "execution-1", "buy", "TQQQ", "buy", 1, 50, .1,
              (NOW - timedelta(minutes=10)).isoformat()),
             ("account-1", "execution-2", "sell", "TQQQ", "sell", .5, 48, .1,
              (NOW - timedelta(minutes=5)).isoformat())],
        )
        ledger._db.commit()
        quote = Quote("TQQQ", 45, 45.02, 45.01, NOW, NOW, NOW)
        pnl = ledger.mark_to_market_pnl("account-1", {"TQQQ": quote})
        assert round(pnl["realized_usd"], 2) == -1.15
        assert round(pnl["unrealized_usd"], 2) == -2.55
        assert round(pnl["total_usd"], 2) == -3.70
    finally:
        ledger.close()


def test_mixed_daily_loss_uses_pnl_and_keeps_prior_day_gap(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    try:
        first = store.record_mixed_daily_pnl("account-1", "2026-09-23", 50, 25,
                                              scope_digest="candidate", recovery_delay=None,
                                              recovery_unit="manual", observed_at=NOW)
        assert not first["loss_latched"]
        loss = store.record_mixed_daily_pnl("account-1", "2026-09-23", 20, 25,
                                             scope_digest="candidate", recovery_delay=None,
                                             recovery_unit="manual", observed_at=NOW + timedelta(minutes=1))
        assert loss["loss_latched"] and loss["recovery_blocked"]
        gap = store.record_mixed_daily_pnl("account-1", "2026-09-24", -10, 25,
                                            scope_digest="candidate", recovery_delay=None,
                                            recovery_unit="manual", carry_previous_observation=True,
                                            observed_at=NOW + timedelta(days=1))
        assert gap["loss_latched"] and gap["recovery_blocked"]
    finally:
        store.close()

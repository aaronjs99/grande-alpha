from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from grande_alpha.domain.models import BrokerExecution, BrokerOrder, Quote, Regime, Signal
from grande_alpha.persistence.store import AuditStore
from grande_alpha.research.sandbox import SandboxConfig
from grande_alpha.strategy.shadow import LiveShadowEngine

NOW = datetime(2026, 8, 11, 15, tzinfo=UTC)
ACCOUNT = "loss-test-account"


def record_fill(
    store, key, side, quantity, price, at, *, fees=0.0, account=ACCOUNT,
    requested_quantity=None, state="filled",
):
    order = BrokerOrder(
        order_id=key, symbol="TQQQ", side=side, state=state,
        quantity=quantity if requested_quantity is None else requested_quantity,
        dollar_amount=None, average_price=price, created_at=at, raw={},
        executions=(BrokerExecution(key + "-execution", quantity, price, fees, at),),
        cumulative_quantity=quantity, last_transaction_at=at, placed_agent="agentic",
    )
    store.record_broker_order_executions(account, order)
    return order


def test_partial_sells_allocate_entry_fees_and_count_each_execution_once(tmp_path):
    path = tmp_path / "losses.db"
    store = AuditStore(path)
    record_fill(store, "buy", "buy", 0.4, 50, NOW, fees=0.04)
    partial = record_fill(
        store, "sell", "sell", 0.1, 50, NOW + timedelta(seconds=1),
        requested_quantity=0.4, state="partially_filled",
    )
    # No price loss, but the allocated entry fee makes this a losing fill.
    assert store.live_loss_streak(ACCOUNT, "2026-08-11") == {
        "consecutive_losses": 1, "peak_consecutive_losses": 1,
    }
    store.record_broker_order_executions(ACCOUNT, partial)
    terminal = replace(
        partial, state="filled", cumulative_quantity=0.4,
        executions=(*partial.executions, BrokerExecution(
            "sell-rest", 0.3, 50, 0, NOW + timedelta(seconds=2),
        )),
        last_transaction_at=NOW + timedelta(seconds=2),
    )
    store.record_broker_order_executions(ACCOUNT, terminal)
    store.record_broker_order_executions(ACCOUNT, terminal)
    expected = {"consecutive_losses": 2, "peak_consecutive_losses": 2}
    assert store.live_loss_streak(ACCOUNT, "2026-08-11") == expected
    store.close()
    reopened = AuditStore(path)
    try:
        assert reopened.live_loss_streak(ACCOUNT, "2026-08-11") == expected
        assert reopened.live_loss_streak("other-account", "2026-08-11") == {
            "consecutive_losses": 0, "peak_consecutive_losses": 0,
        }
        assert reopened.live_loss_streak(ACCOUNT, "2026-08-12") == {
            "consecutive_losses": 0, "peak_consecutive_losses": 0,
        }
    finally:
        reopened.close()


def test_profit_resets_current_streak_but_preserves_session_peak(tmp_path):
    store = AuditStore(tmp_path / "losses.db")
    try:
        for index, price in enumerate((49, 49, 51, 49)):
            at = NOW + timedelta(minutes=index)
            record_fill(store, f"buy-{index}", "buy", 0.1, 50, at)
            record_fill(store, f"sell-{index}", "sell", 0.1, price, at + timedelta(seconds=1))
        assert store.live_loss_streak(ACCOUNT, "2026-08-11") == {
            "consecutive_losses": 1, "peak_consecutive_losses": 2,
        }
    finally:
        store.close()


def test_prior_day_basis_and_sell_fees_follow_eastern_execution_date(tmp_path):
    store = AuditStore(tmp_path / "losses.db")
    try:
        buy_at = datetime(2026, 8, 11, 23, 59, tzinfo=UTC)
        sell_at = datetime(2026, 8, 12, 0, 1, tzinfo=UTC)  # Still August 11 in ET.
        record_fill(store, "buy", "buy", 0.4, 50, buy_at)
        record_fill(store, "sell", "sell", 0.1, 50, sell_at, fees=0.01)
        record_fill(store, "next-day-sell", "sell", 0.3, 49, NOW + timedelta(days=1))
        expected = {"consecutive_losses": 1, "peak_consecutive_losses": 1}
        assert store.live_loss_streak(ACCOUNT, "2026-08-11") == expected
        assert store.live_loss_streak(ACCOUNT, "2026-08-12") == expected
    finally:
        store.close()


def test_missing_entry_cost_history_cannot_be_treated_as_zero_losses(tmp_path):
    store = AuditStore(tmp_path / "losses.db")
    try:
        record_fill(store, "unbacked-sell", "sell", 0.1, 50, NOW)
        with pytest.raises(ValueError, match="entry-cost history"):
            store.live_loss_streak(ACCOUNT, "2026-08-11")
    finally:
        store.close()


@pytest.mark.parametrize("partial_percent", [50.0, 100.0])
def test_provider_ledger_matches_shadow_loss_pause_with_identical_fills(tmp_path, partial_percent):
    config = SandboxConfig(
        initial_cash=100, order_notional=10, decision_stride=1,
        max_consecutive_losses=2, no_trade_open_minutes=0, no_trade_close_minutes=0,
        fill_fraction_pct=partial_percent, slippage_bps=0, base_spread_bps=0,
        spread_volatility_multiplier=0,
    )
    engine = LiveShadowEngine(config)
    store = AuditStore(tmp_path / "losses.db")
    try:
        for index in range(8):
            at = NOW + timedelta(minutes=index)
            quotes = {
                symbol: Quote(symbol, price - 0.01, price + 0.01, price, at, at, at)
                for symbol, price in (("QQQ", 500), ("TQQQ", 50), ("SQQQ", 40))
            }
            signal = Signal(Regime.BULLISH if index % 2 == 0 else Regime.FLAT, 1, "fixture", at)
            fills = engine.on_causal_quote(at, signal, quotes)
            for fill_index, fill in enumerate(fills):
                # Mirror provider execution economics, not its transport or timing.
                record_fill(
                    store, f"fill-{index}-{fill_index}", fill.side, fill.quantity, fill.price,
                    at, fees=fill.commission,
                )
            streak = store.live_loss_streak(ACCOUNT, "2026-08-11")
            assert streak["consecutive_losses"] == engine._consecutive_losses
            if streak["peak_consecutive_losses"] >= config.max_consecutive_losses:
                assert "2026-08-11" in engine._paused_sessions
                assert all(fill.side != "buy" for fill in engine.on_causal_quote(
                    at + timedelta(seconds=5), replace(signal, regime=Regime.BULLISH), quotes,
                ))
                break
        else:
            pytest.fail("Fixture never reached its consecutive-loss limit")
    finally:
        store.close()

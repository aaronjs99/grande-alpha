from datetime import UTC, datetime, timedelta
from pathlib import Path

from grande_alpha.models import Quote, Regime, Signal
from grande_alpha.sandbox import SandboxConfig
from grande_alpha.shadow import LiveShadowEngine


def _quotes(timestamp: datetime) -> dict[str, Quote]:
    return {
        "TQQQ": Quote("TQQQ", 49.99, 50.01, 50.0, timestamp),
        "SQQQ": Quote("SQQQ", 39.99, 40.01, 40.0, timestamp),
    }


def test_shadow_uses_next_bar_virtual_fills_and_is_revocable() -> None:
    config = SandboxConfig(
        initial_cash=100,
        order_notional=50,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
    )
    engine = LiveShadowEngine(config)
    start = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    signal = Signal(Regime.BULLISH, 1, "bullish")

    assert engine.on_bar(start, signal, _quotes(start)) == []
    fills = engine.on_bar(start + timedelta(minutes=1), signal, _quotes(start + timedelta(minutes=1)))

    assert fills[0].symbol == "TQQQS"
    assert fills[0].side == "buy"
    state = engine.stop(_quotes(start + timedelta(minutes=2)))
    assert not state.active
    assert engine.on_bar(start + timedelta(minutes=3), signal, _quotes(start)) == []


def test_shadow_module_cannot_call_broker_order_methods() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "grande_alpha" / "shadow.py").read_text(
        encoding="utf-8"
    )
    assert "place_order" not in source
    assert "review_order" not in source
    assert "cancel_order" not in source
    assert "from grande_alpha.broker" not in source


def test_extended_shadow_uses_the_same_whole_share_limit_profile() -> None:
    config = SandboxConfig(
        initial_cash=250,
        order_notional=200,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        market_hours="extended_hours",
        order_type="limit",
        time_in_force="gfd",
        limit_offset_bps=10,
    )
    engine = LiveShadowEngine(config)
    start = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)  # 08:00 ET
    signal = Signal(Regime.BULLISH, 1, "bullish")

    assert engine.on_bar(start, signal, _quotes(start)) == []
    fills = engine.on_bar(start + timedelta(minutes=1), signal, _quotes(start + timedelta(minutes=1)))

    assert fills
    assert fills[0].quantity == int(fills[0].quantity)
    assert engine.state.position and engine.state.position.quantity == fills[0].quantity


def test_cash_shadow_does_not_recycle_sale_proceeds_until_next_session() -> None:
    config = SandboxConfig(
        initial_cash=50,
        order_notional=50,
        max_exposure_pct=1.0,
        risk_budget_pct=1.0,
        hard_stop_pct=0.5,
        decision_stride=1,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        settlement_model="cash_t1",
    )
    engine = LiveShadowEngine(config)
    monday = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    bullish = Signal(Regime.BULLISH, 1, "bullish")
    bearish = Signal(Regime.BEARISH, 1, "bearish")

    engine.on_bar(monday, bullish, _quotes(monday))
    buy = engine.on_bar(monday + timedelta(minutes=1), bullish, _quotes(monday))[0]
    assert buy.side == "buy"
    engine.on_bar(monday + timedelta(minutes=2), bearish, _quotes(monday))
    sell = engine.on_bar(monday + timedelta(minutes=3), bearish, _quotes(monday))[0]
    assert sell.side == "sell"
    assert engine.state.cash < 1.0
    assert engine.state.unsettled_cash > 49.0

    # The pending reversal cannot buy with Monday's unsettled sale proceeds.
    assert engine.on_bar(monday + timedelta(minutes=4), bearish, _quotes(monday)) == []
    assert engine.state.position is None

    tuesday = monday + timedelta(days=1)
    engine.on_bar(tuesday, bearish, _quotes(tuesday))
    next_session_buy = engine.on_bar(tuesday + timedelta(minutes=1), bearish, _quotes(tuesday))
    assert next_session_buy and next_session_buy[0].side == "buy"
    assert engine.state.unsettled_cash == 0.0

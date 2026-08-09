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
    source = (
        Path(__file__).resolve().parents[1] / "src" / "grande_alpha" / "shadow.py"
    ).read_text(encoding="utf-8")
    assert "place_order" not in source
    assert "review_order" not in source
    assert "cancel_order" not in source
    assert "from grande_alpha.broker" not in source


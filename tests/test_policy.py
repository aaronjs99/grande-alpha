from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from grande_alpha.models import Regime, Signal
from grande_alpha.policy import DecisionPolicy, PolicyConfig, PolicyPosition, regular_session_allowed


def test_live_and_sandbox_symbol_maps_share_identical_decision_logic() -> None:
    timestamp = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    signal = Signal(Regime.BULLISH, 0.8, "trend")
    live = DecisionPolicy(PolicyConfig())
    sandbox = DecisionPolicy(PolicyConfig(bullish_symbol="TQQQS", bearish_symbol="SQQQS"))

    assert live.decide(signal, timestamp).target_symbol == "TQQQ"
    assert sandbox.decide(signal, timestamp).target_symbol == "TQQQS"
    assert live.decide(signal, timestamp).reason == sandbox.decide(signal, timestamp).reason


def test_shared_policy_exits_on_stop_profit_and_time() -> None:
    timestamp = datetime(2026, 8, 3, 15, 0, tzinfo=UTC)
    policy = DecisionPolicy(PolicyConfig(hard_stop_pct=0.01, take_profit_pct=0.02, max_hold_minutes=10))
    signal = Signal(Regime.BULLISH, 1.0, "keep")

    assert policy.decide(signal, timestamp, PolicyPosition("TQQQ", 100, 98)).target_symbol is None
    assert "Hard stop" in policy.decide(signal, timestamp, PolicyPosition("TQQQ", 100, 98)).reason
    assert "Take-profit" in policy.decide(signal, timestamp, PolicyPosition("TQQQ", 100, 103)).reason
    assert "Maximum hold" in policy.decide(
        signal, timestamp, PolicyPosition("TQQQ", 100, 100, 10)
    ).reason


def test_regular_session_gate_is_timezone_aware() -> None:
    eastern = ZoneInfo("America/New_York")
    assert regular_session_allowed(datetime(2026, 8, 3, 10, 0, tzinfo=eastern), 5, 10)
    assert not regular_session_allowed(datetime(2026, 8, 3, 9, 32, tzinfo=eastern), 5, 10)


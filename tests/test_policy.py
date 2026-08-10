from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from grande_alpha.models import Regime, Signal
from grande_alpha.policy import (
    DecisionPolicy,
    PolicyConfig,
    PolicyPosition,
    market_session_allowed,
    regular_session_allowed,
    session_key,
)


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
    assert "Maximum hold" in policy.decide(signal, timestamp, PolicyPosition("TQQQ", 100, 100, 10)).reason


def test_regular_session_gate_is_timezone_aware() -> None:
    eastern = ZoneInfo("America/New_York")
    assert regular_session_allowed(datetime(2026, 8, 3, 10, 0, tzinfo=eastern), 5, 10)
    assert not regular_session_allowed(datetime(2026, 8, 3, 9, 32, tzinfo=eastern), 5, 10)


def test_policy_flattens_during_close_window() -> None:
    eastern = ZoneInfo("America/New_York")
    timestamp = datetime(2026, 8, 3, 15, 52, tzinfo=eastern)
    policy = DecisionPolicy(PolicyConfig(no_trade_close_minutes=10))
    decision = policy.decide(
        Signal(Regime.BULLISH, 1.0, "trend"),
        timestamp,
        PolicyPosition("TQQQ", 100, 101, 5),
    )

    assert decision.target_symbol is None
    assert "flatten" in decision.reason.lower()
    assert not policy.trading_window_allowed(timestamp)
    assert policy.exit_window_allowed(timestamp)


def test_extended_and_all_day_windows_use_the_selected_broker_session() -> None:
    eastern = ZoneInfo("America/New_York")
    premarket = datetime(2026, 8, 3, 8, 0, tzinfo=eastern)
    overnight = datetime(2026, 8, 2, 22, 0, tzinfo=eastern)  # Monday trading session

    assert not market_session_allowed(premarket, 0, 0, "regular_hours")
    assert market_session_allowed(premarket, 0, 0, "extended_hours")
    assert market_session_allowed(overnight, 0, 0, "all_day_hours")
    assert session_key(overnight, "all_day_hours") == "2026-08-03"

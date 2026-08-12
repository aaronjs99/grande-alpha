from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from grande_alpha.candidate_execution import (
    contract_from_config,
    daily_loss_reached,
    decision_due,
    held_minutes,
    runtime_parity_assessment,
)
from grande_alpha.evidence import (
    RUNTIME_SIZING_PARITY_CERTIFIED,
    runtime_parity_manifest,
)
from grande_alpha.evidence import tested_risk_envelope as risk_envelope_for
from grande_alpha.models import LiveGrant, Portfolio, Quote, Regime, Signal
from grande_alpha.risk import RiskEngine
from grande_alpha.sandbox import SandboxConfig
from grande_alpha.shadow import LiveShadowEngine


def test_runtime_parity_manifest_is_machine_readable_and_fail_closed() -> None:
    config = SandboxConfig()
    assessment = runtime_parity_assessment(contract_from_config(config))
    manifest = runtime_parity_manifest(config)

    assert manifest == assessment.as_dict()
    assert json.loads(json.dumps(manifest)) == manifest
    assert not assessment.certified
    assert not RUNTIME_SIZING_PARITY_CERTIFIED
    assert {check.key for check in assessment.blockers} == {
        "market_observation_semantics",
        "filled_entry_count",
        "holding_time_provenance",
        "execution_timing_and_fill_economics",
        "autonomous_exit_lifecycle",
    }
    assert all(check.requirement for check in assessment.blockers)


def test_nonpilot_route_adds_an_explicit_machine_blocker() -> None:
    config = SandboxConfig(
        market_hours="extended_hours",
        order_type="limit",
        time_in_force="gfd",
        latency_bars=1,
    )

    blockers = {
        check.key for check in runtime_parity_assessment(contract_from_config(config)).blockers
    }

    assert "pilot_route" in blockers


def test_peak_loss_definition_matches_runtime_absolute_budget() -> None:
    contract = contract_from_config(SandboxConfig(initial_cash=100, max_daily_loss_pct=0.04))
    assert daily_loss_reached(
        contract,
        session_start_equity=100,
        session_peak_equity=110,
        current_equity=105.5,
    )

    now = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
    grant = LiveGrant(
        account_number="123456789",
        starts_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        max_order_notional=25,
        max_total_exposure=80,
        max_daily_loss=4,
        max_trades=2,
        max_orders_per_minute=2,
        max_spread_bps=20,
        max_quote_age_seconds=8,
        max_daily_notional=50,
        strategy_fingerprint="a" * 64,
    )
    risk = RiskEngine(no_trade_open_minutes=0, no_trade_close_minutes=0)
    risk.arm(grant, Portfolio(100, 100, 100))
    risk.update_portfolio(Portfolio(110, 110, 110))
    risk.update_portfolio(Portfolio(105.5, 105.5, 105.5))

    assert risk.drawdown == pytest.approx(4.5)
    assert risk.session_status(now) == "LOSS LIMIT"


def test_shared_cadence_and_holding_clock_are_completed_bar_based() -> None:
    assert not decision_due(analysis_count=2, last_decision_count=0, decision_stride=3)
    assert decision_due(analysis_count=3, last_decision_count=0, decision_stride=3)
    assert not decision_due(analysis_count=5, last_decision_count=3, decision_stride=3)
    assert decision_due(analysis_count=6, last_decision_count=3, decision_stride=3)

    entered = datetime(2026, 8, 11, 15, 0, 59, tzinfo=UTC)
    observed = entered + timedelta(minutes=4, seconds=59)
    assert held_minutes(entered, observed) == 4


def test_shadow_decision_stride_resets_at_each_broker_session() -> None:
    config = SandboxConfig(
        initial_cash=100,
        order_notional=25,
        decision_stride=2,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
    )
    engine = LiveShadowEngine(config)
    monday = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
    tuesday = monday + timedelta(days=1)
    bullish = Signal(Regime.BULLISH, 1, "bullish")

    def quotes(at: datetime) -> dict[str, Quote]:
        return {
            "TQQQ": Quote("TQQQ", 49.99, 50.01, 50, at),
            "SQQQ": Quote("SQQQ", 39.99, 40.01, 40, at),
        }

    assert engine.on_causal_quote(monday, bullish, quotes(monday)) == []
    assert engine.on_causal_quote(tuesday, bullish, quotes(tuesday)) == []
    fills = engine.on_causal_quote(tuesday + timedelta(minutes=1), bullish, quotes(tuesday))

    assert len(fills) == 1
    assert fills[0].side == "buy"


def test_risk_envelope_reserves_one_buy_and_one_sell_invocation_per_entry() -> None:
    config = SandboxConfig(max_entries_per_day=3, order_notional=20)
    envelope = risk_envelope_for(config)

    assert envelope["max_trades"] == 6
    assert envelope["max_daily_notional"] == pytest.approx(120)

from dataclasses import FrozenInstanceError, replace

import pytest

from grande_alpha.candidate_execution import (
    annualized_volatility,
    contract_from_app_and_sandbox,
    contract_from_config,
    daily_loss_reached,
    effective_spread_bps,
    entry_block_reason,
    execution_price,
    size_entry,
)
from grande_alpha.config import AppConfig
from grande_alpha.sandbox import SandboxConfig


def test_contract_is_frozen_canonical_and_binds_every_field() -> None:
    base = contract_from_config(SandboxConfig(decision_stride=3))
    same = contract_from_config(SandboxConfig(decision_stride=3))
    changed = replace(base, spread_volatility_multiplier=0.25)

    assert base == same
    assert base.canonical_json() == same.canonical_json()
    assert base.fingerprint == same.fingerprint
    assert base.fingerprint != changed.fingerprint
    with pytest.raises(FrozenInstanceError):
        base.order_notional = 10.0  # type: ignore[misc]


def test_app_and_candidate_factory_rejects_runtime_owned_mismatch() -> None:
    app = AppConfig(trade_every_bars=3)
    candidate = SandboxConfig(decision_stride=3)

    assert contract_from_app_and_sandbox(app, candidate) == contract_from_config(candidate)
    with pytest.raises(ValueError, match="decision_stride"):
        contract_from_app_and_sandbox(app, replace(candidate, decision_stride=2))


def test_shared_sizing_applies_risk_volatility_exposure_cash_and_fill_caps() -> None:
    contract = contract_from_config(
        SandboxConfig(
            initial_cash=100,
            order_notional=80,
            hard_stop_pct=0.10,
            risk_budget_pct=0.02,
            max_exposure_pct=0.50,
            volatility_target_pct=0.20,
            fill_fraction_pct=50,
            max_volume_participation_pct=10,
        )
    )
    sizing = size_entry(
        contract,
        equity=100,
        settled_cash=100,
        price=10,
        realized_volatility=0.40,
        available_volume=4,
    )

    # risk notional=20, then 0.5 volatility scaling -> $10 requested; volume cap wins.
    assert sizing.budget == pytest.approx(10)
    assert sizing.requested_quantity == pytest.approx(1)
    assert sizing.fillable_quantity == pytest.approx(0.4)
    assert sizing.volatility_scale == pytest.approx(0.5)


def test_shared_cost_and_lifecycle_helpers_are_explicit_and_fail_closed() -> None:
    contract = contract_from_config(
        SandboxConfig(
            base_spread_bps=2,
            spread_volatility_multiplier=0.5,
            slippage_bps=1,
            max_entries_per_day=2,
            max_consecutive_losses=2,
            max_daily_loss_pct=0.04,
        )
    )

    spread = effective_spread_bps(contract, quoted_spread_bps=4, range_bps=10)
    assert spread == pytest.approx(7)
    assert execution_price(
        contract,
        reference_price=100,
        side="buy",
        spread_bps=spread,
    ) == pytest.approx(100.045)
    assert daily_loss_reached(contract, session_start_equity=100, current_equity=float("nan"))
    assert entry_block_reason(
        contract,
        entries_this_session=2,
        consecutive_losses=0,
        daily_loss_paused=False,
    ) == "Daily entry cap"
    with pytest.raises(ValueError, match="finite"):
        annualized_volatility([0.01] * 9 + [float("nan")], bar_minutes=1, market_hours="regular_hours")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_cash", float("nan")),
        ("risk_budget_pct", float("inf")),
        ("latency_bars", 1.5),
        ("max_hold_minutes", True),
    ],
)
def test_contract_rejects_nonfinite_or_nonintegral_material_values(field: str, value: object) -> None:
    config = SandboxConfig()
    setattr(config, field, value)

    with pytest.raises(ValueError):
        contract_from_config(config)

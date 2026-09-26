from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, replace

from grande_alpha.domain.execution_profile import execution_profile
from grande_alpha.execution.candidate_execution import (
    CandidateExecutionContract,
    contract_from_config,
    runtime_parity_assessment,
)
from grande_alpha.research.sandbox_models import INTERVAL_MINUTES, SandboxConfig
from grande_alpha.strategy.core import StrategyConfig

EVIDENCE_POLICY_VERSION = 15
# This value is derived from the machine-readable mechanics assessment below. It must not
# become true merely because entry sizing shares a helper: exact observations, fill economics,
# exit lifecycle, and the provider order-confirmation contract must all align as well.
RUNTIME_SIZING_PARITY_CERTIFIED = runtime_parity_assessment(
    CandidateExecutionContract()
).certified
MIN_EVIDENCE_SESSIONS = 120
FINAL_HOLDOUT_SESSIONS = 20
FINAL_HOLDOUT_PURGE_SESSIONS = 1
MIN_TOTAL_EVIDENCE_SESSIONS = (
    MIN_EVIDENCE_SESSIONS + FINAL_HOLDOUT_PURGE_SESSIONS + FINAL_HOLDOUT_SESSIONS
)
REQUIRED_LIVE_GATE_NAMES = frozenset(
    {
        "Historical source",
        "Exact runtime observation schema",
        "Trading-session coverage",
        "Data breadth",
        "Data recency",
        "Data integrity",
        "Runtime sizing parity",
        "Parameter stability",
        "Cost stress",
        "Closed-trade sample",
        "After-cost quality",
        "Random-entry control",
        "Trial-adjusted significance",
        "Deflated Sharpe",
        "Profit concentration",
        "Drawdown",
        "Ending flat",
        "Exact candidate identity",
        "Walk-forward",
        "Sealed final holdout",
    }
)
STRATEGY_FINGERPRINT_FIELDS = (
    "strategy_name",
    "warmup_bars",
    "fast_ema",
    "slow_ema",
    "trend_threshold_bps",
    "momentum_bars",
    "hard_stop_pct",
    "take_profit_pct",
    "max_hold_minutes",
    "no_trade_open_minutes",
    "no_trade_close_minutes",
    "trend_short_bars",
    "trend_medium_bars",
    "trend_long_bars",
    "close_momentum_bps",
    "opening_range_minutes",
    "breakout_buffer_bps",
    "ensemble_min_votes",
)
SANDBOX_EXECUTION_FINGERPRINT_FIELDS = tuple(
    field.name for field in fields(CandidateExecutionContract) if field.name != "contract_version"
)


@dataclass(frozen=True)
class ComparisonRow:
    name: str
    return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    round_trips: int
    exposure_pct: float
    total_cost: float


@dataclass(frozen=True)
class SensitivityPoint:
    strategy_name: str
    candidate: str
    fast_ema: int
    slow_ema: int
    threshold_bps: float
    hard_stop_pct: float
    return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    round_trips: int
    sharpe: float


@dataclass(frozen=True)
class WalkForwardFold:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    selected: SandboxConfig
    train_return_pct: float
    test_return_pct: float
    test_drawdown_pct: float
    test_profit_factor: float
    test_round_trips: int
    test_expectancy: float


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[WalkForwardFold]
    positive_fold_pct: float
    average_test_return_pct: float
    median_test_return_pct: float
    worst_test_drawdown_pct: float
    total_test_round_trips: int
    median_test_profit_factor: float
    median_test_expectancy: float




@dataclass(frozen=True)
class PromotionGate:
    name: str
    passed: bool
    observed: str
    requirement: str


@dataclass(frozen=True)
class PromotionReport:
    status: str
    gates: list[PromotionGate]
    dataset_hash: str
    strategy_fingerprint: str
    policy_version: int = EVIDENCE_POLICY_VERSION
    holdout_id: int | None = None

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(gate.passed for gate in self.gates)


def _interval_seconds(config: object, interval: str | None) -> int:
    if interval:
        if interval.endswith("s"):
            return int(interval[:-1])
        return INTERVAL_MINUTES.get(interval, 1) * 60
    return int(getattr(config, "bar_seconds", 60))


def strategy_fingerprint(
    config: object,
    interval: str | None = None,
    execution: object | None = None,
) -> str:
    strategy_defaults = StrategyConfig()
    route = execution_profile(execution or config)
    contract = contract_from_config(config)
    if execution is not None:
        contract = replace(
            contract,
            market_hours=route.market_hours,
            order_type=route.order_type,
            time_in_force=route.time_in_force,
            limit_offset_bps=route.limit_offset_bps,
        )
        contract.validate()

    payload = {
        "policy_version": EVIDENCE_POLICY_VERSION,
        "bar_interval_seconds": _interval_seconds(config, interval),
        "execution_contract": contract.canonical_payload(),
        "execution_contract_fingerprint": contract.fingerprint,
        **{
            field: getattr(config, field, getattr(strategy_defaults, field, None))
            for field in STRATEGY_FINGERPRINT_FIELDS
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def runtime_parity_manifest(config: object | None = None) -> dict[str, object]:
    """Return the current fail-closed replay/shadow/live parity assessment as data."""

    contract = (
        config
        if isinstance(config, CandidateExecutionContract)
        else contract_from_config(config or CandidateExecutionContract())
    )
    return runtime_parity_assessment(contract).as_dict()


def tested_risk_envelope(config: SandboxConfig) -> dict[str, float | int]:
    """Maximum live grant compatible with the replayed sizing and stressed spread model."""
    contract = contract_from_config(config)
    return {
        "max_order_notional": contract.order_notional,
        # Entry plus exit turnover for every permitted entry. Partial-fill retries remain
        # bounded by the same aggregate filled quantity in the shared virtual contract.
        "max_daily_notional": 2.0 * contract.order_notional * contract.max_entries_per_day,
        "max_total_exposure": contract.initial_cash * contract.max_exposure_pct,
        "max_daily_loss": contract.initial_cash * contract.max_daily_loss_pct,
        # RiskEngine counts every irreversible placement invocation. Each replay entry can
        # consume one buy and one sell invocation, so an entry-only count would block the
        # certified exit at exactly the moment it is required.
        "max_trades": 2 * contract.max_entries_per_day,
        "max_orders_per_minute": 2,
        "max_spread_bps": contract.base_spread_bps * 3.0,
    }

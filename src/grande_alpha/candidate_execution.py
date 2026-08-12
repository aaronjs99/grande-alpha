from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from numbers import Real
from typing import Any

from grande_alpha.execution import execution_profile
from grande_alpha.policy import session_minutes

CONTRACT_VERSION = 1
RUNTIME_PARITY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CandidateExecutionContract:
    """Immutable candidate-bound execution, sizing, and lifecycle semantics.

    Market observations (bar ranges, quoted spreads, prices, and volume) remain explicit
    inputs. Replay, live shadow, and a future broker-order controller therefore use the same
    formulas without pretending their market-data sources are identical.
    """

    contract_version: int = CONTRACT_VERSION
    initial_cash: float = 50.0
    order_notional: float = 25.0
    slippage_bps: float = 2.0
    base_spread_bps: float = 2.0
    spread_volatility_multiplier: float = 0.10
    commission_per_order: float = 0.0
    latency_bars: int = 0
    fill_fraction_pct: float = 100.0
    rejection_rate_pct: float = 0.0
    max_volume_participation_pct: float = 1.0
    market_hours: str = "regular_hours"
    order_type: str = "market"
    time_in_force: str = "gfd"
    limit_offset_bps: float = 10.0
    settlement_model: str = "cash_t1"
    random_seed: int = 7007
    decision_stride: int = 1
    hard_stop_pct: float = 0.008
    take_profit_pct: float = 0.015
    max_hold_minutes: int = 45
    max_entries_per_day: int = 6
    no_trade_open_minutes: int = 5
    no_trade_close_minutes: int = 10
    risk_budget_pct: float = 0.01
    max_exposure_pct: float = 0.80
    max_daily_loss_pct: float = 0.04
    max_consecutive_losses: int = 3
    volatility_target_pct: float = 0.30
    force_flat_at_end: bool = True

    def validate(self) -> None:
        execution_profile(self)
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("Unsupported candidate execution contract version")
        integer_fields = (
            "latency_bars",
            "random_seed",
            "decision_stride",
            "max_hold_minutes",
            "max_entries_per_day",
            "no_trade_open_minutes",
            "no_trade_close_minutes",
            "max_consecutive_losses",
        )
        if any(
            isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), int)
            for name in integer_fields
        ):
            raise ValueError("Candidate lifecycle counts must be integers")
        if self.settlement_model not in {"cash_t1", "instant"}:
            raise ValueError("Settlement model must be cash_t1 or instant")
        numeric_fields = (
            "initial_cash",
            "order_notional",
            "slippage_bps",
            "base_spread_bps",
            "spread_volatility_multiplier",
            "commission_per_order",
            "fill_fraction_pct",
            "rejection_rate_pct",
            "max_volume_participation_pct",
            "limit_offset_bps",
            "hard_stop_pct",
            "take_profit_pct",
            "risk_budget_pct",
            "max_exposure_pct",
            "max_daily_loss_pct",
            "volatility_target_pct",
        )
        if any(
            isinstance(getattr(self, name), bool)
            or not isinstance(getattr(self, name), Real)
            or not math.isfinite(float(getattr(self, name)))
            for name in numeric_fields
        ):
            raise ValueError("Candidate execution contract values must be finite")
        if self.initial_cash <= 0 or self.order_notional <= 0:
            raise ValueError("Starting cash and order notional must be positive")
        if self.order_notional > self.initial_cash:
            raise ValueError("Order notional cannot exceed starting virtual cash")
        if any(
            value < 0
            for value in (
                self.slippage_bps,
                self.base_spread_bps,
                self.spread_volatility_multiplier,
                self.commission_per_order,
                self.rejection_rate_pct,
            )
        ):
            raise ValueError("Execution costs and rejection rate cannot be negative")
        if self.latency_bars < 0 or not 0 < self.fill_fraction_pct <= 100:
            raise ValueError("Latency must be nonnegative and fill fraction must be in (0,100]")
        if not 0 < self.max_volume_participation_pct <= 100:
            raise ValueError("Volume participation must be in (0,100]")
        if not 0 <= self.rejection_rate_pct <= 100:
            raise ValueError("Rejection rate must be between 0 and 100")
        if not 1 <= self.decision_stride <= 120:
            raise ValueError("Decision stride must be between 1 and 120 analysis bars")
        if self.hard_stop_pct <= 0 or self.take_profit_pct <= 0:
            raise ValueError("Stop and take-profit percentages must be positive")
        if self.max_hold_minutes < 1 or self.max_entries_per_day < 1:
            raise ValueError("Maximum hold and daily entry cap must be positive")
        if (
            self.no_trade_open_minutes < 0
            or self.no_trade_close_minutes < 0
            or self.no_trade_open_minutes + self.no_trade_close_minutes
            >= session_minutes(self.market_hours)
        ):
            raise ValueError("No-trade windows must leave part of the selected session open")
        for value in (
            self.risk_budget_pct,
            self.max_exposure_pct,
            self.max_daily_loss_pct,
        ):
            if not 0 < value <= 1:
                raise ValueError("Risk and exposure percentages must be in (0,1]")
        if self.max_consecutive_losses < 1 or self.volatility_target_pct < 0:
            raise ValueError("Loss pause must be positive and volatility target cannot be negative")

    @property
    def whole_shares_required(self) -> bool:
        return self.order_type == "limit" or self.market_hours != "regular_hours"

    def canonical_payload(self) -> dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


_CONFIG_ALIASES = {
    "order_notional": "default_max_order_notional",
    "max_entries_per_day": "default_max_trades",
    "decision_stride": "trade_every_bars",
}


def contract_from_config(config: object) -> CandidateExecutionContract:
    """Create one canonical contract from a sandbox config or compatible runtime config."""

    defaults = CandidateExecutionContract()
    values: dict[str, Any] = {}
    for field in fields(defaults):
        if field.name == "contract_version":
            continue
        if hasattr(config, field.name):
            values[field.name] = getattr(config, field.name)
            continue
        alias = _CONFIG_ALIASES.get(field.name)
        values[field.name] = getattr(config, alias) if alias and hasattr(config, alias) else getattr(
            defaults, field.name
        )
    contract = CandidateExecutionContract(**values)
    contract.validate()
    return contract


_RUNTIME_PARITY_FIELDS = {
    "decision_stride": "trade_every_bars",
    "market_hours": "market_hours",
    "order_type": "order_type",
    "time_in_force": "time_in_force",
    "limit_offset_bps": "limit_offset_bps",
    "settlement_model": "settlement_model",
    "hard_stop_pct": "hard_stop_pct",
    "take_profit_pct": "take_profit_pct",
    "max_hold_minutes": "max_hold_minutes",
    "no_trade_open_minutes": "no_trade_open_minutes",
    "no_trade_close_minutes": "no_trade_close_minutes",
}


def contract_from_app_and_sandbox(
    app_config: object,
    sandbox_config: object,
) -> CandidateExecutionContract:
    """Bind a sandbox candidate to runtime-owned fields, rejecting any mismatch."""

    mismatches = []
    for candidate_field, runtime_field in _RUNTIME_PARITY_FIELDS.items():
        if not hasattr(app_config, runtime_field):
            continue
        runtime_value = getattr(app_config, runtime_field)
        candidate_value = getattr(sandbox_config, candidate_field)
        if runtime_value != candidate_value:
            mismatches.append(
                f"{candidate_field}: candidate={candidate_value!r}, runtime={runtime_value!r}"
            )
    if mismatches:
        raise ValueError("Candidate/runtime contract mismatch: " + "; ".join(mismatches))
    return contract_from_config(sandbox_config)


@dataclass(frozen=True)
class EntrySizing:
    budget: float
    requested_quantity: float
    fillable_quantity: float
    volatility_scale: float
    blocked_reason: str = ""


@dataclass(frozen=True)
class RuntimeParityCheck:
    """One machine-readable replay/shadow/live mechanics comparison."""

    key: str
    aligned: bool
    replay: str
    shadow: str
    live: str
    requirement: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeParityAssessment:
    """Fail-closed certification status for the constrained autonomous pilot."""

    schema_version: int
    contract_fingerprint: str
    scope: str
    checks: tuple[RuntimeParityCheck, ...]

    @property
    def certified(self) -> bool:
        return bool(self.checks) and all(check.aligned for check in self.checks)

    @property
    def blockers(self) -> tuple[RuntimeParityCheck, ...]:
        return tuple(check for check in self.checks if not check.aligned)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_fingerprint": self.contract_fingerprint,
            "scope": self.scope,
            "certified": self.certified,
            "checks": [check.as_dict() for check in self.checks],
            "blockers": [check.as_dict() for check in self.blockers],
        }


def fillable_quantity(
    contract: CandidateExecutionContract,
    *,
    requested_quantity: float,
    available_volume: float | None = None,
) -> float:
    if not math.isfinite(requested_quantity) or requested_quantity < 0:
        raise ValueError("Requested quantity must be finite and nonnegative")
    quantity = requested_quantity * contract.fill_fraction_pct / 100.0
    if available_volume is not None:
        if not math.isfinite(available_volume) or available_volume < 0:
            raise ValueError("Available volume must be finite and nonnegative")
    if available_volume is not None and available_volume > 0:
        quantity = min(
            quantity,
            available_volume * contract.max_volume_participation_pct / 100.0,
        )
    if contract.whole_shares_required:
        quantity = float(math.floor(quantity + 1e-9))
    return max(0.0, quantity)


def annualized_volatility(
    returns: tuple[float, ...] | list[float],
    *,
    bar_minutes: float,
    market_hours: str,
) -> float | None:
    if len(returns) < 10:
        return None
    if not math.isfinite(bar_minutes) or bar_minutes <= 0:
        raise ValueError("Bar duration must be finite and positive")
    if not all(math.isfinite(value) for value in returns):
        raise ValueError("Returns must be finite")
    realized = statistics.pstdev(returns)
    return realized * math.sqrt(252 * session_minutes(market_hours) / bar_minutes)


def volatility_scale(
    contract: CandidateExecutionContract,
    realized_volatility: float | None,
) -> float:
    if contract.volatility_target_pct <= 0 or realized_volatility is None:
        return 1.0
    if not math.isfinite(realized_volatility) or realized_volatility < 0:
        raise ValueError("Realized volatility must be finite and nonnegative")
    if realized_volatility <= 1e-9:
        return 1.0
    return min(1.0, contract.volatility_target_pct / realized_volatility)


def size_entry(
    contract: CandidateExecutionContract,
    *,
    equity: float,
    settled_cash: float,
    price: float,
    realized_volatility: float | None,
    available_volume: float | None = None,
) -> EntrySizing:
    """Pure entry sizing shared by replay, virtual shadow, and broker-order preparation."""

    if not all(math.isfinite(value) for value in (equity, settled_cash, price)) or price <= 0:
        raise ValueError("Sizing inputs must be finite and entry price must be positive")
    scale = volatility_scale(contract, realized_volatility)
    risk_notional = equity * contract.risk_budget_pct / contract.hard_stop_pct
    exposure_cap = equity * contract.max_exposure_pct
    budget = min(contract.order_notional, risk_notional, exposure_cap) * scale
    budget = max(0.0, min(budget, settled_cash - contract.commission_per_order))
    if budget <= 0:
        return EntrySizing(0.0, 0.0, 0.0, scale, "No settled cash remains after commission")
    requested = budget / price
    if contract.whole_shares_required:
        requested = float(math.floor(requested + 1e-9))
    if requested <= 0:
        return EntrySizing(budget, 0.0, 0.0, scale, "Order profile requires a whole share")
    fillable = fillable_quantity(
        contract,
        requested_quantity=requested,
        available_volume=available_volume,
    )
    return EntrySizing(budget, requested, max(0.0, fillable), scale)


def observed_range_bps(open_price: float, high: float, low: float) -> float:
    if not all(math.isfinite(value) and value > 0 for value in (open_price, high, low)):
        raise ValueError("Range prices must be finite and positive")
    return max(0.0, high - low) / open_price * 10_000.0


def effective_spread_bps(
    contract: CandidateExecutionContract,
    *,
    quoted_spread_bps: float | None = None,
    range_bps: float = 0.0,
) -> float:
    if not math.isfinite(range_bps) or range_bps < 0:
        raise ValueError("Observed range must be finite and nonnegative")
    modeled = contract.base_spread_bps + range_bps * contract.spread_volatility_multiplier
    if quoted_spread_bps is None:
        return modeled
    if not math.isfinite(quoted_spread_bps) or quoted_spread_bps < 0:
        raise ValueError("Quoted spread must be finite and nonnegative")
    return max(modeled, quoted_spread_bps)


def execution_price(
    contract: CandidateExecutionContract,
    *,
    reference_price: float,
    side: str,
    spread_bps: float,
) -> float:
    if side not in {"buy", "sell"}:
        raise ValueError("Execution side must be buy or sell")
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError("Reference price must be finite and positive")
    if not math.isfinite(spread_bps) or spread_bps < 0:
        raise ValueError("Spread must be finite and nonnegative")
    direction = 1.0 if side == "buy" else -1.0
    total_bps = contract.slippage_bps + spread_bps / 2.0
    return reference_price * (1.0 + direction * total_bps / 10_000.0)


def decision_due(
    *,
    analysis_count: int,
    last_decision_count: int,
    decision_stride: int,
) -> bool:
    """Return whether another completed-bar decision is due.

    Counts are deliberately explicit so replay, live shadow, and the broker controller
    do not mix provider-poll cadence with completed-analysis-bar cadence.
    """

    values = (analysis_count, last_decision_count, decision_stride)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("Decision cadence counts must be integers")
    if analysis_count < 0 or last_decision_count < 0 or decision_stride < 1:
        raise ValueError("Decision cadence counts must be nonnegative and stride positive")
    if last_decision_count > analysis_count:
        raise ValueError("Last decision count cannot exceed the analysis count")
    return analysis_count - last_decision_count >= decision_stride


def held_minutes(entry_time: datetime, observation_time: datetime) -> int:
    """Whole elapsed minutes used by the shared maximum-hold policy."""

    if entry_time.tzinfo is None or observation_time.tzinfo is None:
        raise ValueError("Holding-time inputs must be timezone-aware")
    if observation_time < entry_time:
        raise ValueError("Holding entry time cannot be later than the observation")
    return int((observation_time - entry_time).total_seconds() // 60)


def session_drawdown_amount(*, session_peak_equity: float, current_equity: float) -> float:
    """Observed peak-to-current loss, failing closed on unusable portfolio truth."""

    if not all(math.isfinite(value) for value in (session_peak_equity, current_equity)):
        return math.inf
    if session_peak_equity < 0 or current_equity < 0:
        return math.inf
    return max(0.0, session_peak_equity - current_equity)


def daily_loss_reached(
    contract: CandidateExecutionContract,
    *,
    session_start_equity: float,
    session_peak_equity: float | None = None,
    current_equity: float,
) -> bool:
    peak = session_start_equity if session_peak_equity is None else session_peak_equity
    if not all(math.isfinite(value) for value in (session_start_equity, peak, current_equity)):
        return True
    return bool(
        session_start_equity > 0
        and session_drawdown_amount(session_peak_equity=peak, current_equity=current_equity)
        >= session_start_equity * contract.max_daily_loss_pct
    )


def entry_block_reason(
    contract: CandidateExecutionContract,
    *,
    entries_this_session: int,
    consecutive_losses: int,
    daily_loss_paused: bool,
) -> str:
    if daily_loss_paused:
        return "Daily loss pause"
    if consecutive_losses >= contract.max_consecutive_losses:
        return "Consecutive loss pause"
    if entries_this_session >= contract.max_entries_per_day:
        return "Daily entry cap"
    return ""


def next_consecutive_losses(current: int, realized_pnl: float) -> int:
    if not math.isfinite(realized_pnl):
        raise ValueError("Realized P/L must be finite")
    return current + 1 if realized_pnl < 0 else 0


def runtime_parity_assessment(
    contract: CandidateExecutionContract,
) -> RuntimeParityAssessment:
    """Describe, without granting authority, what still prevents runtime parity.

    Passing items identify shared mechanics already exercised by tests. Failing items are
    architectural differences, not tunable evidence thresholds. The function is intentionally
    data-only so the CLI, GUI, receipts, and tests can render the same blockers without parsing
    prose or changing live-order capability.
    """

    contract.validate()
    pilot_profile = (
        contract.market_hours == "regular_hours"
        and contract.order_type == "market"
        and contract.time_in_force == "gfd"
        and contract.settlement_model == "cash_t1"
        and contract.latency_bars == 0
    )
    checks = (
        RuntimeParityCheck(
            "immutable_contract_identity",
            True,
            "CandidateExecutionContract fingerprint",
            "CandidateExecutionContract fingerprint",
            "CandidateExecutionContract fingerprint bound into evidence and authority",
            "All engines consume the exact candidate-bound contract",
        ),
        RuntimeParityCheck(
            "candidate_entry_sizing",
            True,
            "size_entry(equity, settled cash, modeled price, volatility, volume)",
            "size_entry(equity, settled cash, quoted price, volatility)",
            "size_entry(equity, buying power, ask, volatility), then authority caps",
            "The same risk, volatility, exposure, and settled-cash budget formula runs before entry",
        ),
        RuntimeParityCheck(
            "session_loss_semantics",
            True,
            "Observed session peak loss versus fixed starting-equity budget",
            "Observed session peak loss versus fixed starting-equity budget",
            "Observed session peak loss versus fixed grant-dollar budget",
            "New entries lock on the same peak-to-current loss definition",
        ),
        RuntimeParityCheck(
            "completed_bar_decision_cadence",
            True,
            "decision_due on completed historical bars",
            "decision_due on completed live-shadow bars",
            "decision_due on completed BarBuilder bars; provider polls do not advance it",
            "Decision stride is measured only in completed analysis bars",
        ),
        RuntimeParityCheck(
            "pilot_route",
            pilot_profile,
            execution_profile(contract).label,
            execution_profile(contract).label,
            "Autonomous v1 only accepts Regular market / Market order / GFD / cash T+1 / zero bar latency",
            "Use the one constrained route whose lifecycle is implemented across all engines",
        ),
        RuntimeParityCheck(
            "market_observation_semantics",
            False,
            "Imported OHLCV bars whose trade/quote construction is declared only by dataset "
            "provenance",
            "Completed bars built from sampled live QQQ quote mids plus target-ETF bid/ask quotes",
            "Completed bars built from sampled live QQQ quote mids plus target-ETF bid/ask quotes",
            "Replay input must reproduce the runtime quote-mid bar construction and bind that "
            "provenance to the certified candidate",
        ),
        RuntimeParityCheck(
            "filled_entry_count",
            True,
            "Counts successful virtual buy fills per session",
            "Counts successful virtual buy fills per session",
            "Counts distinct provider execution-backed buy order ids from the durable account ledger",
            "Provider execution identity makes the distinct entry ledger exact across restart",
        ),
        RuntimeParityCheck(
            "holding_time_provenance",
            True,
            "Elapsed whole minutes from deterministic virtual fill time",
            "Elapsed whole minutes from virtual fill time",
            "Elapsed whole minutes from the first provider execution of the active long holding",
            "Actual provider execution time is persisted and reconciled to current inventory",
        ),
        RuntimeParityCheck(
            "execution_timing_and_fill_economics",
            False,
            "Next causal bar open plus modeled spread/slippage, fill fraction, and volume cap",
            "First causal quote plus modeled spread/slippage and fill fraction",
            "Provider execution quantity, price, fees, and timestamp are durably recorded, but broker "
            "fill behavior still differs from replay/shadow modeled fill assumptions",
            "Live provider fills cannot certify replay/shadow fill economics as identical",
        ),
        RuntimeParityCheck(
            "autonomous_exit_lifecycle",
            False,
            "Retries modeled unfilled exits and may continue/reverse after a confirmed exit",
            "Retries modeled unfilled exits and may continue/reverse after a confirmed exit",
            "Blocks behind one exit until terminal order plus inventory reconciliation; cancelled "
            "exits may retry and confirmed exits may continue/reverse",
            "The shared lifecycle must be exercised against provider-observed partial, cancelled, "
            "and filled orders before certification",
        ),
        RuntimeParityCheck(
            "provider_order_confirmation_contract",
            False,
            "No provider order placement",
            "No provider order placement",
            "Current Robinhood review contract requires the exact reviewed ticket and disclosure "
            "to be presented for explicit confirmation before placement",
            "Implement per-order confirmation or obtain written provider confirmation that the "
            "exact bounded session-authority flow satisfies the current order contract",
        ),
    )
    return RuntimeParityAssessment(
        schema_version=RUNTIME_PARITY_SCHEMA_VERSION,
        contract_fingerprint=contract.fingerprint,
        scope="bounded-autonomous-tqqq-sqqq-v1",
        checks=checks,
    )

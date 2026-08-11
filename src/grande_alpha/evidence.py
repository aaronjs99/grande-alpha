from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from grande_alpha.execution import execution_profile
from grande_alpha.historical import HistoricalBundle, assess_quality
from grande_alpha.policy import session_key
from grande_alpha.sandbox import (
    INTERVAL_MINUTES,
    SandboxConfig,
    SandboxReplayEngine,
    SandboxResult,
    interval_minutes,
)
from grande_alpha.strategy import StrategyConfig

EASTERN = ZoneInfo("America/New_York")
EVIDENCE_POLICY_VERSION = 9
# Non-cash replay uses risk-budget and volatility sizing that shadow/live do not yet share.
# Cash can satisfy the report-level parity gate only when it never takes exposure, but it
# cannot satisfy the trade-sample/profit gates. Consequently no current policy-v9 result
# can truthfully create live-review authority.
RUNTIME_SIZING_PARITY_CERTIFIED = False
MIN_EVIDENCE_SESSIONS = 120
REQUIRED_LIVE_GATE_NAMES = frozenset(
    {
        "Historical source",
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
SANDBOX_EXECUTION_FINGERPRINT_FIELDS = (
    "initial_cash",
    "order_notional",
    "slippage_bps",
    "base_spread_bps",
    "spread_volatility_multiplier",
    "commission_per_order",
    "latency_bars",
    "fill_fraction_pct",
    "rejection_rate_pct",
    "max_volume_participation_pct",
    "random_seed",
    "max_entries_per_day",
    "risk_budget_pct",
    "max_exposure_pct",
    "max_daily_loss_pct",
    "max_consecutive_losses",
    "volatility_target_pct",
    "force_flat_at_end",
)
_CONFIG_FIELD_ALIASES = {
    "order_notional": "default_max_order_notional",
    "max_entries_per_day": "default_max_trades",
}
FORCED_FLATTEN_REASONS = frozenset(
    {
        "Session-end forced virtual flatten",
        "End of replay forced virtual flatten",
    }
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
class RandomControl:
    trials: int
    median_return_pct: float
    percentile_10: float
    percentile_90: float
    strategy_percentile: float


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
    sandbox_defaults = SandboxConfig()
    decision_stride = int(getattr(config, "decision_stride", getattr(config, "trade_every_bars", 1)))
    route = execution_profile(execution or config)

    def config_value(field: str) -> object:
        if hasattr(config, field):
            return getattr(config, field)
        alias = _CONFIG_FIELD_ALIASES.get(field)
        if alias and hasattr(config, alias):
            return getattr(config, alias)
        return getattr(sandbox_defaults, field)

    payload = {
        "policy_version": EVIDENCE_POLICY_VERSION,
        "bar_interval_seconds": _interval_seconds(config, interval),
        "decision_stride": decision_stride,
        "market_hours": route.market_hours,
        "order_type": route.order_type,
        "time_in_force": route.time_in_force,
        "limit_offset_bps": route.limit_offset_bps,
        "settlement_model": getattr(config, "settlement_model", "instant"),
        **{
            field: getattr(config, field, getattr(strategy_defaults, field, None))
            for field in STRATEGY_FINGERPRINT_FIELDS
        },
        **{field: config_value(field) for field in SANDBOX_EXECUTION_FINGERPRINT_FIELDS},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def tested_risk_envelope(config: SandboxConfig) -> dict[str, float | int]:
    """Maximum live grant compatible with the replayed sizing and stressed spread model."""
    return {
        "max_order_notional": config.order_notional,
        "max_total_exposure": config.initial_cash * config.max_exposure_pct,
        "max_daily_loss": config.initial_cash * config.max_daily_loss_pct,
        "max_trades": config.max_entries_per_day,
        "max_orders_per_minute": 2,
        "max_spread_bps": config.base_spread_bps * 3.0,
    }


def compare_configs(bundle: HistoricalBundle, configs: dict[str, SandboxConfig]) -> list[ComparisonRow]:
    rows = []
    for name, config in configs.items():
        result = SandboxReplayEngine(config).run(bundle)
        rows.append(
            ComparisonRow(
                name,
                result.return_pct,
                result.max_drawdown_pct,
                result.profit_factor,
                result.round_trips,
                result.exposure_pct,
                result.total_execution_cost,
            )
        )
    return rows


def candidate_grid(base: SandboxConfig, compact: bool = True) -> list[SandboxConfig]:
    if base.strategy_name in {"close_momentum", "first_half_hour_momentum"}:
        return [
            replace(base, close_momentum_bps=value)
            for value in sorted(
                {
                    max(2.0, base.close_momentum_bps * 0.5),
                    base.close_momentum_bps,
                    base.close_momentum_bps * 1.5,
                }
            )
        ]
    if base.strategy_name == "opening_breakout":
        return [
            replace(base, opening_range_minutes=minutes, breakout_buffer_bps=buffer)
            for minutes in sorted({15, base.opening_range_minutes, 60})
            for buffer in sorted(
                {
                    max(0.0, base.breakout_buffer_bps * 0.5),
                    base.breakout_buffer_bps,
                    base.breakout_buffer_bps * 2,
                }
            )
        ]
    if base.strategy_name == "multi_horizon_trend":
        return [
            replace(base, trend_threshold_bps=value)
            for value in sorted(
                {
                    max(0.5, base.trend_threshold_bps * 0.5),
                    base.trend_threshold_bps,
                    base.trend_threshold_bps * 2,
                }
            )
        ]
    if base.strategy_name == "conservative_ensemble":
        return [
            replace(base, ensemble_min_votes=votes, trend_threshold_bps=threshold)
            for votes in sorted({2, base.ensemble_min_votes, 3})
            for threshold in sorted(
                {
                    max(0.5, base.trend_threshold_bps * 0.5),
                    base.trend_threshold_bps,
                    base.trend_threshold_bps * 2,
                }
            )
        ]
    fast_values = sorted({max(2, base.fast_ema - 3), base.fast_ema, base.fast_ema + 3})
    slow_values = sorted({max(5, base.slow_ema - 8), base.slow_ema, base.slow_ema + 8})
    thresholds = sorted(
        {max(0.5, base.trend_threshold_bps / 2), base.trend_threshold_bps, base.trend_threshold_bps * 2}
    )
    stops = (
        [base.hard_stop_pct]
        if compact
        else [base.hard_stop_pct * 0.75, base.hard_stop_pct, base.hard_stop_pct * 1.25]
    )
    configs = []
    for fast in fast_values:
        for slow in slow_values:
            if fast >= slow:
                continue
            for threshold in thresholds:
                for stop in stops:
                    configs.append(
                        replace(
                            base,
                            fast_ema=fast,
                            slow_ema=slow,
                            warmup_bars=max(base.warmup_bars, slow + 2),
                            trend_threshold_bps=threshold,
                            hard_stop_pct=stop,
                        )
                    )
    return configs


def parameter_sweep(bundle: HistoricalBundle, configs: Iterable[SandboxConfig]) -> list[SensitivityPoint]:
    points = []
    for config in configs:
        result = SandboxReplayEngine(config).run(bundle)
        points.append(
            SensitivityPoint(
                config.strategy_name,
                _candidate_label(config),
                config.fast_ema,
                config.slow_ema,
                config.trend_threshold_bps,
                config.hard_stop_pct,
                result.return_pct,
                result.max_drawdown_pct,
                result.profit_factor,
                result.round_trips,
                result.sharpe,
            )
        )
    return points


def _candidate_label(config: SandboxConfig) -> str:
    if config.strategy_name in {"close_momentum", "first_half_hour_momentum"}:
        return f"close threshold {config.close_momentum_bps:.1f} bps"
    if config.strategy_name == "opening_breakout":
        return f"range {config.opening_range_minutes}m; buffer {config.breakout_buffer_bps:.1f} bps"
    if config.strategy_name == "multi_horizon_trend":
        return f"trend threshold {config.trend_threshold_bps:.1f} bps"
    if config.strategy_name == "conservative_ensemble":
        return f"votes {config.ensemble_min_votes}; threshold {config.trend_threshold_bps:.1f} bps"
    return f"EMA {config.fast_ema}/{config.slow_ema}; threshold {config.trend_threshold_bps:.1f} bps"


def _sessions(bundle: HistoricalBundle) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for frame in bundle.frames:
        day = session_key(frame.start, bundle.market_hours)
        grouped.setdefault(day, []).append(frame)
    return grouped


def _subset(bundle: HistoricalBundle, session_names: list[str]) -> HistoricalBundle:
    allowed = set(session_names)
    frames = [frame for frame in bundle.frames if session_key(frame.start, bundle.market_hours) in allowed]
    quality = assess_quality(frames, bundle.interval, bundle.market_hours)
    return HistoricalBundle(
        source=bundle.source,
        downloaded_at=bundle.downloaded_at,
        frames=frames,
        interval=bundle.interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours=bundle.market_hours,
    )


def walk_forward(
    bundle: HistoricalBundle,
    candidates: list[SandboxConfig],
    train_sessions: int = 20,
    test_sessions: int = 5,
    step_sessions: int = 5,
    purge_sessions: int = 1,
) -> WalkForwardResult:
    names = sorted(_sessions(bundle))
    if len(names) < train_sessions + test_sessions:
        raise ValueError(
            f"Walk-forward needs {train_sessions + test_sessions} sessions; dataset has {len(names)}"
        )
    folds = []
    cursor = 0
    if purge_sessions < 0:
        raise ValueError("Purged walk-forward gap cannot be negative")
    while cursor + train_sessions + purge_sessions + test_sessions <= len(names):
        train_names = names[cursor : cursor + train_sessions]
        test_start = cursor + train_sessions + purge_sessions
        test_names = names[test_start : test_start + test_sessions]
        train_bundle = _subset(bundle, train_names)
        test_bundle = _subset(bundle, test_names)
        scored = []
        for config in candidates:
            result = SandboxReplayEngine(config).run(train_bundle)
            score = result.return_pct - 0.5 * result.max_drawdown_pct
            scored.append((score, config, result))
        _, selected, train_result = max(scored, key=lambda item: item[0])
        test_result = SandboxReplayEngine(selected).run(test_bundle)
        folds.append(
            WalkForwardFold(
                train_names[0],
                train_names[-1],
                test_names[0],
                test_names[-1],
                selected,
                train_result.return_pct,
                test_result.return_pct,
                test_result.max_drawdown_pct,
                test_result.profit_factor,
                test_result.round_trips,
                test_result.expectancy,
            )
        )
        cursor += step_sessions
    test_returns = [fold.test_return_pct for fold in folds]
    return WalkForwardResult(
        folds,
        sum(value > 0 for value in test_returns) / len(test_returns) * 100.0,
        statistics.fmean(test_returns),
        statistics.median(test_returns),
        max(fold.test_drawdown_pct for fold in folds),
        sum(fold.test_round_trips for fold in folds),
        statistics.median(fold.test_profit_factor for fold in folds),
        statistics.median(fold.test_expectancy for fold in folds),
    )


def probabilistic_sharpe_ratio(
    returns: list[float], benchmark_annual_sharpe: float = 0.0, periods_per_year: int = 252
) -> float:
    """Probability that Sharpe exceeds a benchmark after skew and kurtosis adjustment."""

    if len(returns) < 3 or periods_per_year < 1:
        return 0.0
    deviation = statistics.stdev(returns)
    if deviation <= 1e-12:
        return 1.0 if statistics.fmean(returns) > 0 else 0.0
    period_sharpe = statistics.fmean(returns) / deviation
    centered = [(value - statistics.fmean(returns)) / deviation for value in returns]
    skew = statistics.fmean(value**3 for value in centered)
    kurtosis = statistics.fmean(value**4 for value in centered)
    benchmark = benchmark_annual_sharpe / math.sqrt(periods_per_year)
    variance_term = 1.0 - skew * period_sharpe + (kurtosis - 1.0) * period_sharpe**2 / 4.0
    statistic = (
        (period_sharpe - benchmark) * math.sqrt(len(returns) - 1) / math.sqrt(max(variance_term, 1e-12))
    )
    return statistics.NormalDist().cdf(statistic)


def expected_maximum_sharpe(
    trial_annual_sharpes: list[float], periods_per_year: int = 252, total_trials: int | None = None
) -> float:
    """Expected maximum Sharpe under multiple independent zero-mean trials."""

    count = max(len(trial_annual_sharpes), total_trials or 0)
    if count < 2 or periods_per_year < 1:
        return 0.0
    period_values = [value / math.sqrt(periods_per_year) for value in trial_annual_sharpes]
    dispersion = statistics.pstdev(period_values)
    if dispersion <= 1e-12:
        return 0.0
    euler_gamma = 0.5772156649015329
    normal = statistics.NormalDist()
    first = normal.inv_cdf(1.0 - 1.0 / count)
    second = normal.inv_cdf(1.0 - 1.0 / (count * math.e))
    expected_period = dispersion * ((1.0 - euler_gamma) * first + euler_gamma * second)
    return expected_period * math.sqrt(periods_per_year)


def deflated_sharpe_ratio(
    returns: list[float],
    trial_annual_sharpes: list[float],
    periods_per_year: int = 252,
    total_trials: int | None = None,
) -> float:
    benchmark = expected_maximum_sharpe(trial_annual_sharpes, periods_per_year, total_trials)
    return probabilistic_sharpe_ratio(returns, benchmark, periods_per_year)


def cost_stress(bundle: HistoricalBundle, base: SandboxConfig) -> dict[float, SandboxResult]:
    return {
        multiplier: SandboxReplayEngine(cost_stressed_config(base, multiplier)).run(bundle)
        for multiplier in (1.0, 2.0, 3.0)
    }


def cost_stressed_config(base: SandboxConfig, multiplier: float) -> SandboxConfig:
    """Scale every monetary execution-cost term, including the dynamic spread term."""

    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("Cost multiplier must be finite and positive")
    return replace(
        base,
        slippage_bps=base.slippage_bps * multiplier,
        base_spread_bps=base.base_spread_bps * multiplier,
        spread_volatility_multiplier=base.spread_volatility_multiplier * multiplier,
        commission_per_order=base.commission_per_order * multiplier,
    )


def forced_flatten_count(result: SandboxResult) -> int:
    """Count executions obtained through the simulator's failure-bypassing EOD path."""

    return sum(event.reason in FORCED_FLATTEN_REASONS for event in result.execution_events)


def random_entry_control(
    bundle: HistoricalBundle,
    config: SandboxConfig,
    strategy_return_pct: float,
    trials: int = 100,
) -> RandomControl:
    rng = random.Random(config.random_seed + 99)
    grouped = _sessions(bundle)
    hold_bars = max(1, round(config.max_hold_minutes / interval_minutes(bundle.interval)))
    returns = []
    for _ in range(trials):
        pnl = 0.0
        for frames in grouped.values():
            if len(frames) < 3:
                continue
            entries = min(config.max_entries_per_day, max(1, len(frames) // hold_bars))
            if config.settlement_model == "cash_t1":
                settled_tranches = max(1, int(config.initial_cash // config.order_notional))
                entries = min(entries, settled_tranches)
            choices = sorted(rng.sample(range(1, len(frames) - 1), min(entries, len(frames) - 2)))
            for entry_index in choices:
                exit_index = min(len(frames) - 1, entry_index + hold_bars)
                alias = "TQQQS" if rng.random() < 0.5 else "SQQQS"
                entry = frames[entry_index].bar_for_alias(alias).open
                exit_price = frames[exit_index].bar_for_alias(alias).close
                gross = config.order_notional * (exit_price / entry - 1.0)
                costs = config.order_notional * (2 * config.slippage_bps + config.base_spread_bps) / 10_000
                costs += 2 * config.commission_per_order
                pnl += gross - costs
        returns.append(pnl / config.initial_cash * 100.0)
    ordered = sorted(returns)
    rank = sum(value <= strategy_return_pct for value in ordered) / len(ordered) * 100.0
    return RandomControl(
        trials,
        statistics.median(ordered),
        ordered[max(0, round(0.10 * (len(ordered) - 1)))],
        ordered[min(len(ordered) - 1, round(0.90 * (len(ordered) - 1)))],
        rank,
    )


def promotion_report(
    bundle: HistoricalBundle,
    base_config: SandboxConfig,
    base_result: SandboxResult,
    sensitivity: list[SensitivityPoint],
    stressed: dict[float, SandboxResult],
    walk: WalkForwardResult | None,
    random_control: RandomControl,
    now: datetime | None = None,
    total_trial_count: int | None = None,
    holdout_result: SandboxResult | None = None,
    holdout_id: int | None = None,
) -> PromotionReport:
    reference = now or datetime.now(UTC)
    sessions = bundle.quality.sessions if bundle.quality else len(_sessions(bundle))
    stable_pct = (
        sum(point.return_pct > 0 for point in sensitivity) / len(sensitivity) * 100.0 if sensitivity else 0.0
    )
    positive_days = [value for value in base_result.daily_pnl.values() if value > 0]
    concentration = max(positive_days) / sum(positive_days) * 100.0 if positive_days else 100.0
    data_age_days = max(0, (reference - bundle.end).total_seconds() / 86_400)
    daily_values = list(base_result.daily_pnl.values())
    if len(daily_values) >= 2 and statistics.stdev(daily_values) > 0:
        t_stat = statistics.fmean(daily_values) / (
            statistics.stdev(daily_values) / math.sqrt(len(daily_values))
        )
        one_sided_p = 0.5 * math.erfc(t_stat / math.sqrt(2.0))
    else:
        one_sided_p = 1.0
    adjusted_p = min(1.0, one_sided_p * max(1, len(sensitivity)))
    daily_returns = base_result.daily_returns
    counted_trials = max(len(sensitivity), total_trial_count or 0)
    dsr = deflated_sharpe_ratio(
        daily_returns,
        [point.sharpe for point in sensitivity],
        total_trials=counted_trials,
    )
    coverage_rank = {"regular_hours": 0, "extended_hours": 1, "all_day_hours": 2}
    session_covered = coverage_rank.get(bundle.market_hours, -1) >= coverage_rank.get(
        base_config.market_hours, 99
    )
    base_forced_flatten_count = forced_flatten_count(base_result)
    cash_candidate = base_config.strategy_name == "cash"
    zero_cash_exposure = (
        cash_candidate
        and not base_result.fills
        and math.isclose(base_result.exposure_pct, 0.0, rel_tol=0.0, abs_tol=1e-12)
    )
    gates = [
        PromotionGate(
            "Historical source",
            "deterministic" not in bundle.source.lower() and "scenario" not in bundle.source.lower(),
            bundle.source,
            "Observed or imported market history; synthetic scenarios are ineligible",
        ),
        PromotionGate(
            "Trading-session coverage",
            session_covered,
            f"dataset={bundle.market_hours}; strategy={base_config.market_hours}",
            "The dataset covers the complete selected broker session",
        ),
        PromotionGate(
            "Data breadth",
            sessions >= MIN_EVIDENCE_SESSIONS,
            f"{sessions} sessions",
            f"At least {MIN_EVIDENCE_SESSIONS} sessions",
        ),
        PromotionGate(
            "Data recency",
            data_age_days <= 30,
            f"{data_age_days:.1f} days old",
            "The final observation is no more than 30 days old",
        ),
        PromotionGate(
            "Data integrity",
            bool(
                bundle.quality
                and bundle.quality.clean
                and bundle.quality.missing_intervals == 0
                and bundle.quality.session_coverage_pct >= 95.0
            ),
            (
                f"{bundle.quality.missing_intervals} missing; "
                f"{bundle.quality.session_coverage_pct:.1f}% complete sessions"
                if bundle.quality
                else "unknown"
            ),
            "Hash-valid, zero duplicate/missing intraday intervals, and 95% complete sessions",
        ),
        PromotionGate(
            "Runtime sizing parity",
            zero_cash_exposure,
            (
                "Cash candidate had zero fills and zero exposure"
                if zero_cash_exposure
                else (
                    f"Cash candidate had {len(base_result.fills)} fills and "
                    f"{base_result.exposure_pct:.2f}% exposure"
                    if cash_candidate
                    else "Replay uses risk-budget/volatility sizing; shadow/live do not share "
                    "the certified sizing contract"
                )
            ),
            "Replay and runtime share the exact certified sizing contract; until then only a "
            "zero-fill, zero-exposure cash candidate can pass this gate",
        ),
        PromotionGate(
            "Parameter stability",
            stable_pct >= 50.0,
            f"{stable_pct:.1f}% profitable neighbors",
            "At least 50% of neighboring configurations profitable",
        ),
        PromotionGate(
            "Cost stress",
            stressed.get(3.0, base_result).net_pnl > 0,
            f"{stressed.get(3.0, base_result).return_pct:+.2f}% at 3x costs",
            "Positive after 3x modeled costs",
        ),
        PromotionGate(
            "Closed-trade sample",
            base_result.round_trips >= 30,
            f"{base_result.round_trips} round trips",
            "At least 30 after-cost closed round trips",
        ),
        PromotionGate(
            "After-cost quality",
            base_result.profit_factor >= 1.20 and base_result.expectancy > 0,
            f"PF {base_result.profit_factor:.2f}; expectancy ${base_result.expectancy:+.4f}",
            "Profit factor at least 1.20 and positive expectancy",
        ),
        PromotionGate(
            "Random-entry control",
            random_control.strategy_percentile >= 75.0,
            f"Strategy at {random_control.strategy_percentile:.1f}th percentile",
            "At or above the 75th percentile of seeded random-entry trials",
        ),
        PromotionGate(
            "Trial-adjusted significance",
            adjusted_p <= 0.05,
            f"one-sided Bonferroni p={adjusted_p:.4f} across {max(1, len(sensitivity))} candidates",
            "Positive daily P/L survives a 5% familywise correction for every tested candidate",
        ),
        PromotionGate(
            "Deflated Sharpe",
            dsr >= 0.95,
            f"DSR probability={dsr:.4f} across {max(1, counted_trials)} registered candidates",
            "At least 95% probability after selection-bias and non-normality adjustment",
        ),
        PromotionGate(
            "Profit concentration",
            concentration <= 50.0,
            f"Best day is {concentration:.1f}% of positive daily P/L",
            "No single day exceeds 50% of positive daily P/L",
        ),
        PromotionGate(
            "Drawdown",
            base_result.max_drawdown_pct <= 5.0,
            f"{base_result.max_drawdown_pct:.2f}%",
            "At most 5% in research configuration",
        ),
        PromotionGate(
            "Ending flat",
            base_result.ending_position is None and base_forced_flatten_count == 0,
            (
                f"No open position; {base_forced_flatten_count} bypassed EOD flatten(s)"
                if base_result.ending_position is None
                else base_result.ending_position
            ),
            "Replay ends flat without a forced, execution-failure-bypassing EOD flatten",
        ),
    ]
    if walk is None:
        gates.append(
            PromotionGate(
                "Walk-forward",
                False,
                "Not run",
                "At least 5 folds, 60% positive, 20 test trades, median PF 1.10, positive expectancy",
            )
        )
    else:
        base_fingerprint = strategy_fingerprint(base_config, bundle.interval)
        matching_folds = sum(
            strategy_fingerprint(fold.selected, bundle.interval) == base_fingerprint for fold in walk.folds
        )
        gates.append(
            PromotionGate(
                "Exact candidate identity",
                bool(walk.folds) and matching_folds == len(walk.folds),
                f"{matching_folds}/{len(walk.folds)} folds selected the certified candidate",
                "Every training fold selects the exact configuration being certified",
            )
        )
        gates.append(
            PromotionGate(
                "Walk-forward",
                len(walk.folds) >= 5
                and walk.positive_fold_pct >= 60.0
                and walk.total_test_round_trips >= 20
                and walk.median_test_profit_factor >= 1.10
                and walk.median_test_expectancy > 0,
                f"{len(walk.folds)} folds; {walk.positive_fold_pct:.1f}% positive; "
                f"{walk.total_test_round_trips} trades; median PF {walk.median_test_profit_factor:.2f}; "
                f"expectancy ${walk.median_test_expectancy:+.4f}",
                "At least 5 folds, 60% positive, 20 test trades, median PF 1.10, positive expectancy",
            )
        )
    if holdout_result is None or holdout_id is None:
        gates.append(
            PromotionGate(
                "Sealed final holdout",
                False,
                "Not reserved and consumed",
                "One frozen candidate must pass one later, purged, sealed holdout at 3x modeled costs",
            )
        )
    else:
        holdout_forced_flatten_count = forced_flatten_count(holdout_result)
        holdout_passed = (
            holdout_result.net_pnl > 0
            and holdout_result.round_trips >= 5
            and holdout_result.profit_factor >= 1.10
            and holdout_result.expectancy > 0
            and holdout_result.max_drawdown_pct <= 5.0
            and holdout_result.ending_position is None
            and holdout_forced_flatten_count == 0
        )
        gates.append(
            PromotionGate(
                "Sealed final holdout",
                holdout_passed,
                f"holdout {holdout_id}; return {holdout_result.return_pct:+.2f}%; "
                f"{holdout_result.round_trips} trades; PF {holdout_result.profit_factor:.2f}; "
                f"expectancy ${holdout_result.expectancy:+.4f}; "
                f"DD {holdout_result.max_drawdown_pct:.2f}%; "
                f"{holdout_forced_flatten_count} bypassed EOD flatten(s)",
                "Positive at 3x costs, at least 5 trades, PF 1.10, positive expectancy, "
                "drawdown at most 5%, and ending flat without a bypassed EOD flatten",
            )
        )
    status = "LIVE_REVIEW_ELIGIBLE" if all(gate.passed for gate in gates) else "SHADOW_ONLY"
    return PromotionReport(
        status,
        gates,
        bundle.dataset_hash,
        strategy_fingerprint(base_config, bundle.interval),
        holdout_id=holdout_id,
    )

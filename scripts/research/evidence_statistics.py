from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, replace

from grande_alpha.domain.market_models import Regime, Signal
from grande_alpha.domain.policy import session_key
from grande_alpha.research.evidence_replay import _run_replay, _sessions
from grande_alpha.research.historical_models import HistoricalBundle
from grande_alpha.research.sandbox import SandboxReplayRunner
from grande_alpha.research.sandbox_models import (
    SandboxConfig,
    SandboxResult,
    interval_minutes,
)

FORCED_FLATTEN_REASONS = frozenset(
    {
        "Session-end forced virtual flatten",
        "End of replay forced virtual flatten",
        "SESSION-END VIRTUAL FLAT at regular-session close",
    }
)


@dataclass(frozen=True)
class RandomControl:
    trials: int
    median_return_pct: float
    percentile_10: float
    percentile_90: float
    strategy_percentile: float


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


def cost_stress(
    bundle: HistoricalBundle,
    base: SandboxConfig,
    replay_runner: SandboxReplayRunner | None = None,
) -> dict[float, SandboxResult]:
    return {
        multiplier: _run_replay(
            bundle,
            cost_stressed_config(base, multiplier),
            replay_runner,
        )
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
    replay_runner: SandboxReplayRunner | None = None,
) -> RandomControl:
    rng = random.Random(config.random_seed + 99)
    if replay_runner is not None and replay_runner.exact_runtime_observation:
        returns = [
            replay_runner.run(
                bundle,
                config,
                signals=_random_runtime_signals(bundle, config, rng),
            ).return_pct
            for _ in range(trials)
        ]
        return _random_control_summary(returns, strategy_return_pct)

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
    return _random_control_summary(returns, strategy_return_pct)


def _random_runtime_signals(
    bundle: HistoricalBundle,
    config: SandboxConfig,
    rng: random.Random,
) -> tuple[Signal, ...]:
    """Build one seeded, feasible random policy for the exact causal execution path."""

    indices_by_session: dict[str, list[int]] = {}
    for index, frame in enumerate(bundle.frames):
        indices_by_session.setdefault(
            session_key(frame.start, bundle.market_hours),
            [],
        ).append(index)
    regimes = [Regime.FLAT] * len(bundle.frames)
    hold_bars = max(1, round(config.max_hold_minutes / interval_minutes(bundle.interval)))
    for indices in indices_by_session.values():
        if len(indices) < 3:
            continue
        entries = min(config.max_entries_per_day, max(1, len(indices) // hold_bars))
        if config.settlement_model == "cash_t1":
            settled_tranches = max(1, int(config.initial_cash // config.order_notional))
            entries = min(entries, settled_tranches)
        local_choices = sorted(
            rng.sample(range(1, len(indices) - 1), min(entries, len(indices) - 2))
        )
        occupied_until = -1
        for local_entry in local_choices:
            if local_entry <= occupied_until:
                continue
            local_exit = min(len(indices) - 1, local_entry + hold_bars)
            regime = Regime.BULLISH if rng.random() < 0.5 else Regime.BEARISH
            for local_index in range(local_entry, local_exit):
                regimes[indices[local_index]] = regime
            occupied_until = local_exit
    return tuple(
        Signal(
            regime,
            0.5 if regime is not Regime.FLAT else 0.0,
            "Seeded random-entry control",
            timestamp=frame.qqq.start,
        )
        for frame, regime in zip(bundle.frames, regimes, strict=True)
    )


def _random_control_summary(
    returns: list[float],
    strategy_return_pct: float,
) -> RandomControl:
    if not returns:
        raise ValueError("Random-entry control requires at least one trial")
    ordered = sorted(returns)
    rank = sum(value <= strategy_return_pct for value in ordered) / len(ordered) * 100.0
    return RandomControl(
        len(ordered),
        statistics.median(ordered),
        ordered[max(0, round(0.10 * (len(ordered) - 1)))],
        ordered[min(len(ordered) - 1, round(0.90 * (len(ordered) - 1)))],
        rank,
    )

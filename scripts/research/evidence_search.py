from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import replace

from grande_alpha.research.evidence_contract import (
    ComparisonRow,
    SensitivityPoint,
    WalkForwardFold,
    WalkForwardResult,
)
from grande_alpha.research.evidence_replay import _run_replay, _sessions, _subset
from grande_alpha.research.historical_models import HistoricalBundle
from grande_alpha.research.sandbox import SandboxReplayRunner
from grande_alpha.research.sandbox_models import SandboxConfig


def compare_configs(
    bundle: HistoricalBundle,
    configs: dict[str, SandboxConfig],
    replay_runner: SandboxReplayRunner | None = None,
) -> list[ComparisonRow]:
    rows = []
    for name, config in configs.items():
        result = _run_replay(bundle, config, replay_runner)
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


def parameter_sweep(
    bundle: HistoricalBundle,
    configs: Iterable[SandboxConfig],
    replay_runner: SandboxReplayRunner | None = None,
) -> list[SensitivityPoint]:
    points = []
    for config in configs:
        result = _run_replay(bundle, config, replay_runner)
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




def walk_forward(
    bundle: HistoricalBundle,
    candidates: list[SandboxConfig],
    train_sessions: int = 20,
    test_sessions: int = 5,
    step_sessions: int = 5,
    purge_sessions: int = 1,
    replay_runner: SandboxReplayRunner | None = None,
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
            result = _run_replay(train_bundle, config, replay_runner)
            score = result.return_pct - 0.5 * result.max_drawdown_pct
            scored.append((score, config, result))
        _, selected, train_result = max(scored, key=lambda item: item[0])
        test_result = _run_replay(test_bundle, selected, replay_runner)
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

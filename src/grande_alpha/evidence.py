from __future__ import annotations

import random
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, replace
from zoneinfo import ZoneInfo

from grande_alpha.historical import HistoricalBundle, assess_quality
from grande_alpha.sandbox import INTERVAL_MINUTES, SandboxConfig, SandboxReplayEngine, SandboxResult

EASTERN = ZoneInfo("America/New_York")


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
    fast_ema: int
    slow_ema: int
    threshold_bps: float
    hard_stop_pct: float
    return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    round_trips: int


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


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[WalkForwardFold]
    positive_fold_pct: float
    average_test_return_pct: float
    median_test_return_pct: float
    worst_test_drawdown_pct: float


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

    @property
    def passed(self) -> bool:
        return all(gate.passed for gate in self.gates)


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
                config.fast_ema,
                config.slow_ema,
                config.trend_threshold_bps,
                config.hard_stop_pct,
                result.return_pct,
                result.max_drawdown_pct,
                result.profit_factor,
                result.round_trips,
            )
        )
    return points


def _sessions(bundle: HistoricalBundle) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for frame in bundle.frames:
        day = frame.start.astimezone(EASTERN).date().isoformat()
        grouped.setdefault(day, []).append(frame)
    return grouped


def _subset(bundle: HistoricalBundle, session_names: list[str]) -> HistoricalBundle:
    allowed = set(session_names)
    frames = [
        frame for frame in bundle.frames if frame.start.astimezone(EASTERN).date().isoformat() in allowed
    ]
    quality = assess_quality(frames, bundle.interval)
    return HistoricalBundle(
        source=bundle.source,
        downloaded_at=bundle.downloaded_at,
        frames=frames,
        interval=bundle.interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
    )


def walk_forward(
    bundle: HistoricalBundle,
    candidates: list[SandboxConfig],
    train_sessions: int = 20,
    test_sessions: int = 5,
    step_sessions: int = 5,
) -> WalkForwardResult:
    names = sorted(_sessions(bundle))
    if len(names) < train_sessions + test_sessions:
        raise ValueError(
            f"Walk-forward needs {train_sessions + test_sessions} sessions; dataset has {len(names)}"
        )
    folds = []
    cursor = 0
    while cursor + train_sessions + test_sessions <= len(names):
        train_names = names[cursor : cursor + train_sessions]
        test_names = names[cursor + train_sessions : cursor + train_sessions + test_sessions]
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
    )


def cost_stress(bundle: HistoricalBundle, base: SandboxConfig) -> dict[float, SandboxResult]:
    return {
        multiplier: SandboxReplayEngine(
            replace(
                base,
                slippage_bps=base.slippage_bps * multiplier,
                base_spread_bps=base.base_spread_bps * multiplier,
                commission_per_order=base.commission_per_order * multiplier,
            )
        ).run(bundle)
        for multiplier in (1.0, 2.0, 3.0)
    }


def random_entry_control(
    bundle: HistoricalBundle,
    config: SandboxConfig,
    strategy_return_pct: float,
    trials: int = 100,
) -> RandomControl:
    rng = random.Random(config.random_seed + 99)
    grouped = _sessions(bundle)
    hold_bars = max(1, round(config.max_hold_minutes / INTERVAL_MINUTES.get(bundle.interval, 1)))
    returns = []
    for _ in range(trials):
        pnl = 0.0
        for frames in grouped.values():
            if len(frames) < 3:
                continue
            entries = min(config.max_entries_per_day, max(1, len(frames) // hold_bars))
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
    base_result: SandboxResult,
    sensitivity: list[SensitivityPoint],
    stressed: dict[float, SandboxResult],
    walk: WalkForwardResult | None,
) -> PromotionReport:
    sessions = bundle.quality.sessions if bundle.quality else len(_sessions(bundle))
    stable_pct = (
        sum(point.return_pct > 0 for point in sensitivity) / len(sensitivity) * 100.0 if sensitivity else 0.0
    )
    positive_days = [value for value in base_result.daily_pnl.values() if value > 0]
    concentration = max(positive_days) / sum(positive_days) * 100.0 if positive_days else 100.0
    gates = [
        PromotionGate(
            "Historical source",
            "deterministic" not in bundle.source.lower() and "scenario" not in bundle.source.lower(),
            bundle.source,
            "Observed or imported market history; synthetic scenarios are ineligible",
        ),
        PromotionGate("Data breadth", sessions >= 20, f"{sessions} sessions", "At least 20 sessions"),
        PromotionGate(
            "Data integrity",
            bool(bundle.quality and bundle.quality.clean and bundle.quality.missing_intervals == 0),
            f"{bundle.quality.missing_intervals if bundle.quality else 'unknown'} missing intervals",
            "Hash-valid with zero duplicate or missing intraday intervals",
        ),
        PromotionGate(
            "Parameter stability",
            stable_pct >= 50.0,
            f"{stable_pct:.1f}% profitable neighbors",
            "At least 50% of neighboring configurations profitable",
        ),
        PromotionGate(
            "Cost stress",
            stressed.get(2.0, base_result).net_pnl > 0,
            f"{stressed.get(2.0, base_result).return_pct:+.2f}% at 2x costs",
            "Positive after 2x modeled costs",
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
    ]
    if walk is None:
        gates.append(PromotionGate("Walk-forward", False, "Not run", "At least 5 folds; 60% positive"))
    else:
        gates.append(
            PromotionGate(
                "Walk-forward",
                len(walk.folds) >= 5 and walk.positive_fold_pct >= 60.0,
                f"{len(walk.folds)} folds; {walk.positive_fold_pct:.1f}% positive",
                "At least 5 folds; 60% positive",
            )
        )
    status = "LIVE_REVIEW_ELIGIBLE" if all(gate.passed for gate in gates) else "SHADOW_ONLY"
    return PromotionReport(status, gates, bundle.dataset_hash)

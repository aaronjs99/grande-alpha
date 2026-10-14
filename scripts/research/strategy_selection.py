"""Small, frozen strategy comparison. No broker, model, credentials or authority.

This is a research shortlist, not an alternative to the Evidence Lab/live gates.
Only the development-selected rule is evaluated on the final chronological block.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.policy import market_session_allowed, session_bounds
from grande_alpha.research.bar_replay import SandboxReplayEngine
from grande_alpha.research.evidence_contract import strategy_fingerprint
from grande_alpha.research.evidence_replay import _sessions, _subset
from grande_alpha.research.evidence_statistics import cost_stressed_config, forced_flatten_count
from grande_alpha.research.historical import assess_quality, dataset_hash
from grande_alpha.research.historical_models import HistoricalBundle
from grande_alpha.research.historical_storage import load_bundle
from grande_alpha.research.sandbox_models import SandboxConfig, interval_minutes
from grande_alpha.strategy.core import STRATEGY_NAMES

SHORTLIST = ("cash", "ema_momentum", "multi_horizon_trend", "opening_breakout", "cost_aware_trend")
COMPARISON_VERSION = 1


def research_defaults() -> SandboxConfig:
    """Illustrative virtual-money assumptions, not personal trading limits."""
    return SandboxConfig(initial_cash=1000, order_notional=100, slippage_bps=5,
                         base_spread_bps=5, max_exposure_pct=.4, risk_budget_pct=.005,
                         max_daily_loss_pct=.02, volatility_target_pct=.15,
                         hard_stop_pct=.01, take_profit_pct=.02)


def load_strategy_dataset(path: Path) -> HistoricalBundle:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Choose a cached historical JSON dataset no larger than 16 MiB")
    return load_bundle(resolved)


def _validate(bundle: HistoricalBundle) -> None:
    if bundle.interval not in {"1m", "5m"} or bundle.market_hours != "regular_hours":
        raise ValueError("This comparison supports regular-session intraday 1m or 5m bars with enough daily warm-up")
    if not bundle.frames or len(bundle.frames) > 100_000:
        raise ValueError("Use between 1 and 100000 observed frames")
    previous = None
    for frame in bundle.frames:
        if frame.start.tzinfo is None or (previous is not None and frame.start <= previous):
            raise ValueError("Dataset timestamps must be aware and strictly chronological")
        opened, closed = session_bounds(frame.start, bundle.market_hours)
        seconds = interval_minutes(bundle.interval) * 60
        if (not market_session_allowed(frame.start, 0, 0, bundle.market_hours)
                or not opened <= frame.start < closed or frame.start + timedelta(seconds=seconds) > closed
                or (frame.start - opened).total_seconds() % seconds != 0):
            raise ValueError("Every bar must lie on the regular-session calendar grid, before the close")
        for name, bar in (("QQQ", frame.qqq), ("TQQQ", frame.tqqq), ("SQQQ", frame.sqqq)):
            prices = (bar.open, bar.high, bar.low, bar.close)
            if (bar.symbol not in {name, name + "S"} or bar.start != frame.start
                    or not all(math.isfinite(p) and p > 0 for p in prices)
                    or bar.high < max(prices) or bar.low > min(prices)
                    or not math.isfinite(bar.volume) or bar.volume < 0):
                raise ValueError("Dataset contains invalid, misaligned or nonfinite OHLCV bars")
        previous = frame.start


def _metrics(result) -> dict:
    return {"return_pct": result.return_pct, "max_drawdown_pct": result.max_drawdown_pct,
            "round_trips": result.round_trips, "total_execution_cost": result.total_execution_cost,
            "ending_position": result.ending_position,
            "forced_flatten_count": forced_flatten_count(result)}


def compare_strategies(bundle: HistoricalBundle, *, base: SandboxConfig | None = None,
                       replay=None, now=None, cancelled=lambda: False) -> dict:
    input_hash = bundle.dataset_hash
    input_frames = bundle.frames
    # Some old chart caches include an empty closing print labelled as a full bar.
    # Remove only that unambiguous zero-volume stamp, recording the original identity.
    frames = [f for f in bundle.frames if not (
        f.start == session_bounds(f.start, bundle.market_hours)[1]
        and all(b.volume == 0 for b in (f.qqq, f.tqqq, f.sqqq)))]
    excluded = len(bundle.frames) - len(frames)
    if excluded:
        quality = assess_quality(frames, bundle.interval, bundle.market_hours)
        bundle = replace(bundle, frames=frames, quality=quality, dataset_hash=quality.dataset_hash, provenance=None)
    _validate(bundle)
    if input_hash != dataset_hash(input_frames):
        raise ValueError("Input dataset hash does not match its observed frames")
    base = base or research_defaults()
    base.validate()
    if base.market_hours != bundle.market_hours or not base.force_flat_at_end:
        raise ValueError("Compare regular-session rules with explicit end-of-session virtual flattening")
    names = sorted(_sessions(bundle))
    quality = assess_quality(bundle.frames, bundle.interval, bundle.market_hours)
    if bundle.dataset_hash != quality.dataset_hash:
        raise ValueError("Dataset hash does not match its observed frames")
    if len(names) < 30:
        raise ValueError(f"A strategy comparison needs at least 30 trading sessions; this dataset has {len(names)}")
    boundary = int(len(names) * .7)
    development_names, purge_names, holdout_names = names[:boundary], names[boundary:boundary + 1], names[boundary + 1:]
    development, holdout = _subset(bundle, development_names), _subset(bundle, holdout_names)
    blocks = [development_names[i * len(development_names) // 3:(i + 1) * len(development_names) // 3]
              for i in range(3)]
    # Freeze the signal cost hurdle before any cost stress. Cost stress changes fills,
    # not the candidate's entry formula. Full config and plan hashes expose reruns.
    base = replace(base, signal_cost_bps=base.strategy_config().round_trip_cost_bps)
    configs = {name: replace(base, strategy_name=name) for name in SHORTLIST}
    plan = {"version": COMPARISON_VERSION, "dataset_hash": bundle.dataset_hash,
            "input_dataset_hash": input_hash, "excluded_empty_close_stamps": excluded,
            "interval": bundle.interval, "development_sessions": development_names,
            "purged_sessions": purge_names, "holdout_sessions": holdout_names,
            "development_blocks": blocks, "cost_multipliers": [1, 2, 3],
            "minimum_development_round_trips": 30, "maximum_drawdown_pct": 3,
            "selection_rule": "All three blocks and 1x/2x/3x development returns positive; then highest worst-block return, lower drawdown, total return",
            "configs": {name: asdict(config) for name, config in configs.items()}}
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True, allow_nan=False).encode()).hexdigest()
    engine = replay or (lambda data, config: SandboxReplayEngine(config, reset_each_session=True).run(data))
    def run(data, config):
        if cancelled():
            raise InterruptedError("Strategy comparison cancelled")
        return engine(data, config)
    rows = []
    for name, config in configs.items():
        result = run(development, config)
        block_returns = [run(_subset(development, block), config).return_pct for block in blocks]
        stressed = {str(m): run(development, cost_stressed_config(config, m)) for m in (2, 3)}
        stress_metrics = {"1": _metrics(result), **{m: _metrics(value) for m, value in stressed.items()}}
        reasons = []
        if min(block_returns) <= 0:
            reasons.append("At least one development block lost money or was flat")
        if any(value["return_pct"] <= 0 for value in stress_metrics.values()):
            reasons.append("Net return did not stay positive at all three cost levels")
        if any(value["max_drawdown_pct"] > 3 for value in stress_metrics.values()):
            reasons.append("Development drawdown exceeded the illustrative 3% screen")
        if result.round_trips < 30:
            reasons.append("Fewer than 30 closed development trades")
        if any(value["ending_position"] for value in stress_metrics.values()):
            reasons.append("Replay ended with an open holding")
        if any(value["forced_flatten_count"] for value in stress_metrics.values()):
            reasons.append("A session-close liquidation attempt affects this result")
        if quality.complete_sessions != len(names) or quality.missing_intervals or quality.missing_sessions:
            reasons.append("Dataset has incomplete sessions or missing bars")
        rows.append({"strategy": name, "label": STRATEGY_NAMES[name], **_metrics(result),
                     "block_returns_pct": block_returns, "worst_block_return_pct": min(block_returns),
                     "cost_stress": stress_metrics, "passes_development": not reasons and name != "cash",
                     "rejection_reasons": reasons if name != "cash" else [],
                     "strategy_fingerprint": strategy_fingerprint(config, bundle.interval)})
    eligible = [row for row in rows if row["passes_development"]]
    chosen = max(eligible, key=lambda row: (row["worst_block_return_pct"], -row["max_drawdown_pct"], row["return_pct"]))["strategy"] if eligible else "cash"
    # Do not open the other candidates' holdouts or reselect after seeing this result.
    selected = configs[chosen]
    final = {str(m): _metrics(run(holdout, cost_stressed_config(selected, m))) for m in (1, 2, 3)}
    cash = _metrics(run(holdout, configs["cash"])) if chosen != "cash" else final["1"]
    passed = chosen != "cash" and all(value["return_pct"] > 0 and value["max_drawdown_pct"] <= 3
                                      and value["round_trips"] >= 10 and not value["ending_position"]
                                      and not value["forced_flatten_count"]
                                      for value in final.values())
    reference = now or utc_now()
    blockers = []
    if not bundle.evidence_provenance_eligible:
        blockers.append("Source provenance is unverified or synthetic")
    if len(names) < 141:
        blockers.append(f"Only {len(names)} sessions; the full evidence workflow needs at least 141")
    if quality.complete_sessions != len(names) or quality.missing_intervals or quality.missing_sessions or quality.zero_volume_bars:
        blockers.append("Coverage, continuity or volume is incomplete")
    if reference - bundle.end > timedelta(days=14) or bundle.end > reference:
        blockers.append("Dataset is stale or future-dated")
    if not passed:
        blockers.append("The frozen candidate did not pass the final after-cost screen")
    blockers.append("Full Evidence Lab, runtime parity and forward paper validation remain required")
    return {"comparison_version": COMPARISON_VERSION, "plan_hash": plan_hash, "plan": plan,
            "dataset": {"hash": bundle.dataset_hash, "source": bundle.source,
                        "input_hash": input_hash, "excluded_empty_close_stamps": excluded,
                        "interval": bundle.interval, "sessions": len(names),
                        "start": bundle.start.isoformat(), "end": bundle.end.isoformat(),
                        "quality": asdict(quality), "provenance_verified": bundle.evidence_provenance_eligible},
            "split": {"development_start": development.start.isoformat(), "development_end": development.end.isoformat(),
                      "purged_sessions": purge_names, "holdout_start": holdout.start.isoformat(), "holdout_end": holdout.end.isoformat()},
            "candidates": rows, "selected_for_holdout": chosen, "selected_config": asdict(selected),
            "holdout": {"base": final["1"], "cost_stress": final, "cash": cash, "passed": passed},
            "recommendation": "cash", "live_approved": False, "blockers": blockers,
            "limitations": ["Repeated runs on the same dates reuse the holdout; this is not a sealed certificate",
                            "Generic OHLCV replay uses modeled spreads and next-bar opens, not executable historical quotes",
                            "Indicators and volatility rewarm each session; no overnight trend state is used by this shortlist",
                            "Session-close liquidation uses modeled fills; residual holdings and special close attempts fail the screen; stopped losses may gap beyond their thresholds",
                            "QQQ signals route to TQQQ/SQQQ virtual positions; daily-reset leverage, concentration and execution risk remain",
                            "Cash earns zero here; tax, interest and unmodeled market impact are excluded" ]}


def command_compare(args) -> int:
    from grande_alpha.interfaces.cli.render import print_json
    report = compare_strategies(load_strategy_dataset(Path(args.dataset)))
    print_json(report)
    return 0

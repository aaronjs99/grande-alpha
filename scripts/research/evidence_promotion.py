from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime

from grande_alpha.execution.candidate_execution import contract_from_config, runtime_parity_assessment
from grande_alpha.research.evidence_contract import (
    MIN_EVIDENCE_SESSIONS,
    RUNTIME_SIZING_PARITY_CERTIFIED,
    PromotionGate,
    PromotionReport,
    SensitivityPoint,
    WalkForwardResult,
    strategy_fingerprint,
)
from grande_alpha.research.evidence_replay import _sessions
from grande_alpha.research.evidence_statistics import (
    RandomControl,
    deflated_sharpe_ratio,
    forced_flatten_count,
)
from grande_alpha.research.historical_models import DataQuality, HistoricalBundle
from grande_alpha.research.sandbox_models import SandboxConfig, SandboxResult


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
    evidence_sessions: int | None = None,
    evidence_quality: DataQuality | None = None,
    holdout_quality: DataQuality | None = None,
) -> PromotionReport:
    reference = now or datetime.now(UTC)
    sessions = (
        evidence_sessions
        if evidence_sessions is not None
        else bundle.quality.sessions
        if bundle.quality
        else len(_sessions(bundle))
    )
    stable_pct = (
        sum(point.return_pct > 0 for point in sensitivity) / len(sensitivity) * 100.0 if sensitivity else 0.0
    )
    positive_days = [value for value in base_result.daily_pnl.values() if value > 0]
    concentration = max(positive_days) / sum(positive_days) * 100.0 if positive_days else 100.0
    data_age_days = (reference - bundle.end).total_seconds() / 86_400
    daily_values = list(base_result.daily_pnl.values())
    if len(daily_values) >= 2 and statistics.stdev(daily_values) > 0:
        t_stat = statistics.fmean(daily_values) / (
            statistics.stdev(daily_values) / math.sqrt(len(daily_values))
        )
        one_sided_p = 0.5 * math.erfc(t_stat / math.sqrt(2.0))
    else:
        one_sided_p = 1.0
    daily_returns = base_result.daily_returns
    counted_trials = max(len(sensitivity), total_trial_count or 0)
    adjusted_p = min(1.0, one_sided_p * max(1, counted_trials))
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
    parity = runtime_parity_assessment(contract_from_config(base_config))
    parity_blockers = ", ".join(check.key for check in parity.blockers)
    development_quality = evidence_quality or bundle.quality
    gates = [
        PromotionGate(
            "Historical source",
            bundle.evidence_provenance_eligible,
            (
                f"kind={bundle.provenance.source_kind}; "
                f"eligible={bundle.evidence_provenance_eligible}; "
                f"provenance={bundle.provenance_hash[:16]}..."
                if bundle.provenance is not None
                else "No machine-readable provenance"
            ),
            "Manifest-bound observed market history with attested research rights; source labels are ignored",
        ),
        PromotionGate(
            "Exact runtime observation schema",
            (
                bundle.runtime_observation_parity_eligible
                and base_result.runtime_observation_replay
            ),
            (
                f"schema={bundle.provenance.observation_schema}; "
                f"analysis={bundle.provenance.analysis_price_semantics}; "
                f"execution={bundle.provenance.execution_price_semantics}; "
                f"volume={bundle.provenance.volume_semantics}; "
                f"schema_exact={bundle.runtime_observation_parity_eligible}; "
                f"evaluated_by_exact_engine={base_result.runtime_observation_replay}"
                if bundle.provenance is not None
                else "No machine-readable runtime quote provenance"
            ),
            "Provenance-bound GRANDE runtime quote trace evaluated by the exact causal replay "
            "engine: QQQ bid/ask-mid bars and the first later synchronized TQQQ/SQQQ venue "
            "bid/ask batch; generic OHLCV or generic bar replay is insufficient",
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
            f"At least {MIN_EVIDENCE_SESSIONS} development sessions after holdout and purge",
        ),
        PromotionGate(
            "Data recency",
            0 <= data_age_days <= 30,
            f"{data_age_days:.1f} days old",
            "The final observation is no more than 30 days old",
        ),
        PromotionGate(
            "Data integrity",
            bool(
                development_quality
                and development_quality.clean
                and development_quality.missing_intervals == 0
                and development_quality.missing_sessions == 0
                and development_quality.session_coverage_pct >= 95.0
            ),
            (
                f"development: {development_quality.missing_intervals} missing bars; "
                f"{development_quality.missing_sessions} missing sessions; "
                f"{development_quality.duplicate_timestamps} duplicate; "
                f"{development_quality.session_coverage_pct:.1f}% complete sessions"
                if development_quality
                else "unknown"
            ),
            "Development partition is hash-valid, has zero duplicate/missing intraday intervals, "
            "and at least 95% complete sessions",
        ),
        PromotionGate(
            "Runtime sizing parity",
            (RUNTIME_SIZING_PARITY_CERTIFIED and parity.certified) or zero_cash_exposure,
            (
                "Certified immutable execution contract shared by replay, shadow, and runtime"
                if RUNTIME_SIZING_PARITY_CERTIFIED and parity.certified
                else "Cash candidate had zero fills and zero exposure"
                if zero_cash_exposure
                else (
                    f"Cash candidate had {len(base_result.fills)} fills and "
                    f"{base_result.exposure_pct:.2f}% exposure"
                    if cash_candidate
                    else "Replay/live do not share the certified sizing contract; blockers: "
                    + parity_blockers
                )
            ),
            "Replay, shadow, and runtime share the exact immutable execution contract; until "
            "certified, only a zero-fill, zero-exposure cash candidate can pass this gate",
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
            f"one-sided Bonferroni p={adjusted_p:.4f} across {max(1, counted_trials)} candidates",
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
        quality_observed = (
            f"Holdout quality blocked evaluation: {holdout_quality.missing_intervals} missing; "
            f"{holdout_quality.duplicate_timestamps} duplicate; "
            f"{holdout_quality.session_coverage_pct:.1f}% complete sessions"
            if holdout_quality is not None
            and (
                not holdout_quality.clean
                or holdout_quality.missing_intervals != 0
                or holdout_quality.session_coverage_pct < 95.0
            )
            else "Not reserved and consumed"
        )
        gates.append(
            PromotionGate(
                "Sealed final holdout",
                False,
                quality_observed,
                "A clean, zero-missing, at least 95%-complete frozen holdout must pass once at 3x modeled costs",
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

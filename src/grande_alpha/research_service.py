from __future__ import annotations

from dataclasses import asdict, dataclass

from grande_alpha.evidence import (
    EVIDENCE_POLICY_VERSION,
    FINAL_HOLDOUT_PURGE_SESSIONS,
    FINAL_HOLDOUT_SESSIONS,
    MIN_TOTAL_EVIDENCE_SESSIONS,
    PromotionReport,
    RandomControl,
    SensitivityPoint,
    WalkForwardResult,
    candidate_grid,
    cost_stress,
    cost_stressed_config,
    forced_flatten_count,
    parameter_sweep,
    promotion_report,
    random_entry_control,
    strategy_fingerprint,
    tested_risk_envelope,
    walk_forward,
)
from grande_alpha.historical import HistoricalBundle, split_final_holdout
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine, SandboxResult
from grande_alpha.storage import AuditStore


@dataclass(frozen=True)
class EvidenceLabResult:
    base: SandboxResult
    sensitivity: list[SensitivityPoint]
    walk_forward: WalkForwardResult | None
    random_control: RandomControl
    report: PromotionReport
    registered_trial_count: int
    promotion_id: int
    holdout: SandboxResult | None = None
    holdout_id: int | None = None


def _quality_allows_holdout_reservation(bundle: HistoricalBundle) -> bool:
    """Require the entire observed chronology to be clean before sealing a subset."""

    quality = bundle.quality
    return bool(
        quality
        and quality.clean
        and quality.sessions >= MIN_TOTAL_EVIDENCE_SESSIONS
        and quality.missing_intervals == 0
        and quality.missing_sessions == 0
        and quality.session_coverage_pct >= 95.0
    )


def run_evidence_lab(
    bundle: HistoricalBundle,
    config: SandboxConfig,
    store: AuditStore,
    *,
    note: str = "",
) -> EvidenceLabResult:
    """Run and record the shared GUI/CLI evidence pipeline without any broker access."""

    config.validate()
    sessions = bundle.quality.sessions if bundle.quality else 0
    development = bundle
    holdout_bundle = None
    holdout_id = None
    holdout_result = None
    holdout_quality = None
    holdout_quality_ok = False
    evidence_development_sessions = (
        max(0, sessions - FINAL_HOLDOUT_SESSIONS - FINAL_HOLDOUT_PURGE_SESSIONS)
        if bundle.evidence_provenance_eligible
        else sessions
    )
    if (
        bundle.evidence_provenance_eligible
        and _quality_allows_holdout_reservation(bundle)
    ):
        split = split_final_holdout(
            bundle,
            FINAL_HOLDOUT_SESSIONS,
            FINAL_HOLDOUT_PURGE_SESSIONS,
        )
        development = split.development
        holdout_bundle = split.holdout
        holdout_quality = holdout_bundle.quality
        holdout_quality_ok = bool(
            holdout_quality
            and holdout_quality.clean
            and holdout_quality.missing_intervals == 0
            and holdout_quality.missing_sessions == 0
            and holdout_quality.session_coverage_pct >= 95.0
        )
        holdout_id = store.reserve_research_holdout(
            dataset_hash=bundle.dataset_hash,
            development_hash=development.dataset_hash,
            holdout_hash=holdout_bundle.dataset_hash,
            holdout_start=holdout_bundle.start.isoformat(),
            holdout_end=holdout_bundle.end.isoformat(),
            policy_version=EVIDENCE_POLICY_VERSION,
            provenance_hash=bundle.provenance_hash,
            development_quality=(
                asdict(development.quality) if development.quality is not None else None
            ),
            holdout_quality=(asdict(holdout_quality) if holdout_quality is not None else None),
        )
        if not holdout_quality_ok:
            store.invalidate_research_holdout(holdout_id)

    try:
        base = SandboxReplayEngine(config).run(development)
        candidates = candidate_grid(config)
        points = parameter_sweep(development, candidates)
        store.record_research_trials(
            development.dataset_hash,
            [
                {
                    "trial_fingerprint": strategy_fingerprint(candidate, bundle.interval),
                    "config": asdict(candidate),
                    "metrics": asdict(point),
                }
                for candidate, point in zip(candidates, points, strict=True)
            ],
        )
        total_trial_count = store.research_trial_count(development.dataset_hash)
        stressed = cost_stress(development, config)
        random_control = random_entry_control(development, config, base.return_pct)
        development_sessions = development.quality.sessions if development.quality else 0
        walk = None
        if development_sessions >= 15:
            test_sessions = max(1, min(5, development_sessions // 7))
            train_sessions = max(5, min(20, development_sessions - 5 * test_sessions))
            if train_sessions + test_sessions <= development_sessions:
                walk = walk_forward(
                    development,
                    candidates,
                    train_sessions,
                    test_sessions,
                    test_sessions,
                )

        selected_fingerprint = strategy_fingerprint(config, bundle.interval)
        development_report = promotion_report(
            bundle,
            config,
            base,
            points,
            stressed,
            walk,
            random_control,
            total_trial_count=total_trial_count,
            evidence_sessions=evidence_development_sessions,
            evidence_quality=development.quality,
            holdout_quality=holdout_quality,
        )
        development_passed = all(
            gate.passed for gate in development_report.gates if gate.name != "Sealed final holdout"
        )
        if (
            holdout_id is not None
            and holdout_bundle is not None
            and holdout_quality_ok
            and development_passed
        ):
            store.freeze_research_holdout(holdout_id, selected_fingerprint)
            store.claim_research_holdout(holdout_id, selected_fingerprint)
            stressed_holdout_config = cost_stressed_config(config, 3.0)
            holdout_result = SandboxReplayEngine(stressed_holdout_config).run(holdout_bundle)
            store.consume_research_holdout(
                holdout_id,
                selected_fingerprint,
                {
                    **holdout_result.metrics(),
                    "cost_multiplier": 3.0,
                    "forced_flatten_count": forced_flatten_count(holdout_result),
                    "holdout_hash": holdout_bundle.dataset_hash,
                    "holdout_start": holdout_bundle.start.isoformat(),
                    "holdout_end": holdout_bundle.end.isoformat(),
                },
            )

        report = promotion_report(
            bundle,
            config,
            base,
            points,
            stressed,
            walk,
            random_control,
            total_trial_count=total_trial_count,
            holdout_result=holdout_result,
            holdout_id=holdout_id if holdout_result is not None else None,
            evidence_sessions=evidence_development_sessions,
            evidence_quality=development.quality,
            holdout_quality=holdout_quality,
        )
    except Exception:
        if holdout_id is not None:
            store.invalidate_research_holdout(holdout_id)
        raise
    promotion_id = store.record_research_promotion(
        dataset_hash=report.dataset_hash,
        strategy_fingerprint=report.strategy_fingerprint,
        policy_version=report.policy_version,
        status=report.status,
        source=bundle.source,
        replay_end=bundle.end.isoformat(),
        gates=[asdict(gate) for gate in report.gates],
        risk_envelope=tested_risk_envelope(config),
        holdout_id=report.holdout_id,
        provenance_hash=bundle.provenance_hash,
        provenance=bundle.provenance.as_dict() if bundle.provenance is not None else None,
    )
    store.receipt(
        "sandbox_evidence",
        f"Evidence lab status: {report.status}",
        {
            "promotion_id": promotion_id,
            "dataset_hash": report.dataset_hash,
            "gates": [asdict(gate) for gate in report.gates],
            "note": note.strip(),
            "registered_trial_count": total_trial_count,
            "provenance_hash": bundle.provenance_hash,
            "development_quality": (
                asdict(development.quality) if development.quality is not None else None
            ),
            "holdout_quality": asdict(holdout_quality) if holdout_quality is not None else None,
        },
        "warning" if not report.passed else "info",
    )
    return EvidenceLabResult(
        base,
        points,
        walk,
        random_control,
        report,
        total_trial_count,
        promotion_id,
        holdout_result,
        holdout_id,
    )

from __future__ import annotations

from dataclasses import asdict, dataclass

from grande_alpha.evidence import (
    PromotionReport,
    RandomControl,
    SensitivityPoint,
    WalkForwardResult,
    candidate_grid,
    cost_stress,
    parameter_sweep,
    promotion_report,
    random_entry_control,
    strategy_fingerprint,
    tested_risk_envelope,
    walk_forward,
)
from grande_alpha.historical import HistoricalBundle
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


def run_evidence_lab(
    bundle: HistoricalBundle,
    config: SandboxConfig,
    store: AuditStore,
    *,
    note: str = "",
) -> EvidenceLabResult:
    """Run and record the shared GUI/CLI evidence pipeline without any broker access."""

    config.validate()
    base = SandboxReplayEngine(config).run(bundle)
    candidates = candidate_grid(config)
    points = parameter_sweep(bundle, candidates)
    store.record_research_trials(
        bundle.dataset_hash,
        [
            {
                "trial_fingerprint": strategy_fingerprint(candidate, bundle.interval),
                "config": asdict(candidate),
                "metrics": asdict(point),
            }
            for candidate, point in zip(candidates, points, strict=True)
        ],
    )
    total_trial_count = store.research_trial_count(bundle.dataset_hash)
    stressed = cost_stress(bundle, config)
    random_control = random_entry_control(bundle, config, base.return_pct)
    sessions = bundle.quality.sessions if bundle.quality else 0
    walk = None
    if sessions >= 15:
        test_sessions = max(1, min(5, sessions // 7))
        train_sessions = max(5, min(20, sessions - 5 * test_sessions))
        if train_sessions + test_sessions <= sessions:
            walk = walk_forward(
                bundle,
                candidates,
                train_sessions,
                test_sessions,
                test_sessions,
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
    )
    promotion_id = store.record_research_promotion(
        dataset_hash=report.dataset_hash,
        strategy_fingerprint=report.strategy_fingerprint,
        policy_version=report.policy_version,
        status=report.status,
        source=bundle.source,
        replay_end=bundle.end.isoformat(),
        gates=[asdict(gate) for gate in report.gates],
        risk_envelope=tested_risk_envelope(config),
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
    )

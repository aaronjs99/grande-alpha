from grande_alpha.evidence import candidate_grid, cost_stress, parameter_sweep, promotion_report
from grande_alpha.historical import deterministic_demo
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine


def test_evidence_pipeline_is_deterministic_and_never_auto_promotes_weak_data() -> None:
    bundle = deterministic_demo(5, seed=77)
    config = SandboxConfig()
    candidates = candidate_grid(config)
    first = parameter_sweep(bundle, candidates)
    second = parameter_sweep(bundle, candidates)

    assert first == second
    assert len(first) >= 9
    result = SandboxReplayEngine(config).run(bundle)
    report = promotion_report(bundle, result, first, cost_stress(bundle, config), None)
    assert report.status == "SHADOW_ONLY"
    assert not report.passed
    assert not next(gate for gate in report.gates if gate.name == "Historical source").passed


def test_execution_rejections_are_audited() -> None:
    bundle = deterministic_demo(2, seed=31)
    config = SandboxConfig(rejection_rate_pct=100, no_trade_open_minutes=0, no_trade_close_minutes=0)
    result = SandboxReplayEngine(config).run(bundle)

    assert not result.fills
    assert result.execution_events
    assert {event.status for event in result.execution_events} == {"rejected"}

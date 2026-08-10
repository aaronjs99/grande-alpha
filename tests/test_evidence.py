from grande_alpha.evidence import (
    candidate_grid,
    cost_stress,
    parameter_sweep,
    promotion_report,
    random_entry_control,
    strategy_fingerprint,
)
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
    control = random_entry_control(bundle, config, result.return_pct, trials=20)
    report = promotion_report(bundle, config, result, first, cost_stress(bundle, config), None, control)
    assert report.status == "SHADOW_ONLY"
    assert not report.passed
    assert not next(gate for gate in report.gates if gate.name == "Historical source").passed
    assert report.strategy_fingerprint == strategy_fingerprint(config)
    assert {gate.name for gate in report.gates} >= {
        "Closed-trade sample",
        "After-cost quality",
        "Random-entry control",
        "Data recency",
    }


def test_execution_rejections_are_audited() -> None:
    bundle = deterministic_demo(2, seed=31)
    config = SandboxConfig(rejection_rate_pct=100, no_trade_open_minutes=0, no_trade_close_minutes=0)
    result = SandboxReplayEngine(config).run(bundle)

    assert not result.fills
    assert result.execution_events
    assert {event.status for event in result.execution_events} == {"rejected"}


def test_fingerprint_binds_strategy_and_bar_interval() -> None:
    base = SandboxConfig(strategy_name="ema_momentum")
    assert strategy_fingerprint(base, "1m") != strategy_fingerprint(base, "5m")
    assert strategy_fingerprint(base, "1m") != strategy_fingerprint(
        SandboxConfig(strategy_name="close_momentum"), "1m"
    )

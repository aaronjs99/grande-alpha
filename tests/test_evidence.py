from grande_alpha.evidence import (
    candidate_grid,
    cost_stress,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    parameter_sweep,
    promotion_report,
    random_entry_control,
    strategy_fingerprint,
    walk_forward,
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
        "Deflated Sharpe",
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


def test_deflated_sharpe_penalizes_trial_search_and_negative_returns() -> None:
    positive = [0.002, -0.0005, 0.0015, 0.0002] * 40
    negative = [-value for value in positive]
    trials = [0.2, 0.5, 0.8, 1.1]

    assert expected_maximum_sharpe(trials) > 0
    assert deflated_sharpe_ratio(positive, trials) > deflated_sharpe_ratio(negative, trials)


def test_walk_forward_inserts_a_purged_session_gap() -> None:
    bundle = deterministic_demo(15, seed=81)
    config = SandboxConfig()
    result = walk_forward(
        bundle,
        [config],
        train_sessions=5,
        test_sessions=2,
        step_sessions=2,
        purge_sessions=1,
    )

    assert result.folds
    assert all(fold.train_end < fold.test_start for fold in result.folds)

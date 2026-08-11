from grande_alpha.config import AppConfig
from grande_alpha.evidence import (
    PromotionReport,
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
from grande_alpha.historical import deterministic_demo, split_final_holdout
from grande_alpha.research_service import run_evidence_lab
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine
from grande_alpha.storage import AuditStore


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


def test_empty_gate_report_never_passes() -> None:
    assert not PromotionReport("SHADOW_ONLY", [], "dataset", "strategy").passed


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
    assert strategy_fingerprint(base, "1m") != strategy_fingerprint(
        SandboxConfig(strategy_name="ema_momentum", decision_stride=3), "1m"
    )


def test_live_and_research_fingerprints_match_only_at_the_same_decision_stride() -> None:
    live = AppConfig(bar_seconds=5, trade_every_bars=3)
    matching = SandboxConfig(decision_stride=3)
    mismatched = SandboxConfig(decision_stride=1)

    assert strategy_fingerprint(live) == strategy_fingerprint(matching, "5s")
    assert strategy_fingerprint(live) != strategy_fingerprint(mismatched, "5s")


def test_fingerprint_binds_execution_session_order_type_and_limit_offset() -> None:
    regular = SandboxConfig()
    extended = SandboxConfig(market_hours="extended_hours", order_type="limit")
    wider_limit = SandboxConfig(
        market_hours="extended_hours",
        order_type="limit",
        limit_offset_bps=25,
    )

    assert strategy_fingerprint(regular) != strategy_fingerprint(extended)
    assert strategy_fingerprint(extended) != strategy_fingerprint(wider_limit)


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


def test_shared_gui_cli_evidence_service_records_one_explainable_result(tmp_path) -> None:
    store = AuditStore(tmp_path / "evidence.db")
    bundle = deterministic_demo(2, seed=88)

    lab = run_evidence_lab(bundle, SandboxConfig(), store, note="shared surface test")
    saved = store.research_promotion(lab.promotion_id)

    assert lab.report.status == "SHADOW_ONLY"
    assert saved is not None
    assert saved["dataset_hash"] == bundle.dataset_hash
    assert saved["gates"] == [
        {
            "name": gate.name,
            "passed": gate.passed,
            "observed": gate.observed,
            "requirement": gate.requirement,
        }
        for gate in lab.report.gates
    ]
    assert not next(gate for gate in lab.report.gates if gate.name == "Sealed final holdout").passed
    store.close()


def test_chronological_final_holdout_is_later_and_purged() -> None:
    bundle = deterministic_demo(40, seed=92)
    split = split_final_holdout(bundle, holdout_sessions=5, purge_sessions=1)

    assert split.development.end < split.holdout.start
    assert len(split.purged_sessions) == 1
    assert split.development.dataset_hash != split.holdout.dataset_hash
    assert split.development.frames[-1].start < split.holdout.frames[0].start


def test_evidence_service_reserves_before_candidate_evaluation(tmp_path, monkeypatch) -> None:
    store = AuditStore(tmp_path / "evidence.db")
    bundle = deterministic_demo(40, seed=93)
    config = SandboxConfig()
    observed_status: list[str] = []
    original_candidate_grid = candidate_grid

    def assert_reserved(value):
        with store._lock:
            row = store._connection.execute(
                "SELECT status FROM research_holdouts ORDER BY id DESC LIMIT 1"
            ).fetchone()
        observed_status.append(row["status"] if row else "missing")
        return original_candidate_grid(value)[:1]

    monkeypatch.setattr("grande_alpha.research_service.candidate_grid", assert_reserved)
    lab = run_evidence_lab(bundle, config, store)

    assert observed_status == ["RESERVED"]
    assert lab.holdout_id is not None
    saved = store.research_holdout(lab.holdout_id)
    assert saved is not None and saved["status"] == "RESERVED"
    assert saved["selected_fingerprint"] is None
    assert saved["metrics"] == {}
    assert lab.holdout is None
    assert lab.report.status == "SHADOW_ONLY"
    store.close()

from dataclasses import replace
from datetime import date, timedelta

import pytest

from grande_alpha.config import AppConfig
from grande_alpha.evidence import (
    SANDBOX_EXECUTION_FINGERPRINT_FIELDS,
    STRATEGY_FINGERPRINT_FIELDS,
    PromotionReport,
    RandomControl,
    SensitivityPoint,
    candidate_grid,
    cost_stress,
    cost_stressed_config,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    parameter_sweep,
    promotion_report,
    random_entry_control,
    strategy_fingerprint,
    walk_forward,
)
from grande_alpha.historical import (
    DataProvenance,
    HistoricalBundle,
    assess_quality,
    deterministic_demo,
    split_final_holdout,
)
from grande_alpha.market_calendar import is_regular_trading_day
from grande_alpha.policy import session_key
from grande_alpha.research_service import run_evidence_lab
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine
from grande_alpha.storage import AuditStore


def _observed_daily_bundle(session_count: int) -> HistoricalBundle:
    scenario = deterministic_demo(240, seed=930 + session_count)
    by_session = {}
    for frame in scenario.frames:
        key = session_key(frame.start)
        if is_regular_trading_day(date.fromisoformat(key)):
            by_session.setdefault(key, frame)
    frames = list(by_session.values())[-session_count:]
    quality = assess_quality(frames, "1d")
    provenance = DataProvenance(
        source_kind="imported_manifest",
        provider="Fixture Provider",
        provider_product="Fixture daily bars",
        acquisition_method="Fixture export",
        license_reference="Fixture research terms",
        license_reviewed_by_user=True,
        research_use_permitted=True,
        automated_strategy_research_permitted=True,
        observed_data=True,
        synthetic_or_interpolated=False,
        construction_method="provider_native",
        source_resolution_seconds=86_400.0,
        bar_interval="1d",
        market_hours="regular_hours",
        manifest_version=1,
        manifest_hash="a" * 64,
        csv_sha256="b" * 64,
        canonical_dataset_hash=quality.dataset_hash,
    )
    return HistoricalBundle(
        source="Fixture manifest-bound observed import",
        downloaded_at=scenario.downloaded_at,
        frames=frames,
        interval="1d",
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours="regular_hours",
        provenance=provenance,
    )


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
    sizing = next(gate for gate in report.gates if gate.name == "Runtime sizing parity")
    assert not sizing.passed
    assert "do not share the certified sizing contract" in sizing.observed
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


def test_promotion_report_rejects_a_future_final_observation() -> None:
    bundle = deterministic_demo(2, seed=771)
    config = SandboxConfig()
    replay = SandboxReplayEngine(config).run(bundle)
    report = promotion_report(
        bundle,
        config,
        replay,
        [],
        {3.0: replay},
        None,
        RandomControl(1, 0.0, 0.0, 0.0, 0.0),
        now=bundle.end - timedelta(seconds=1),
    )

    recency = next(gate for gate in report.gates if gate.name == "Data recency")
    assert not recency.passed
    assert "-0.0 days old" in recency.observed


def test_development_integrity_is_not_lifted_by_full_bundle_quality() -> None:
    bundle = deterministic_demo(2, seed=772)
    assert bundle.quality is not None
    config = SandboxConfig()
    replay = SandboxReplayEngine(config).run(bundle)
    incomplete_development = replace(
        bundle.quality,
        sessions=120,
        complete_sessions=113,
        session_coverage_pct=113 / 120 * 100,
        expected_sessions=120,
        missing_sessions=0,
    )
    report = promotion_report(
        bundle,
        config,
        replay,
        [],
        {3.0: replay},
        None,
        RandomControl(1, 0.0, 0.0, 0.0, 0.0),
        evidence_sessions=120,
        evidence_quality=incomplete_development,
    )

    integrity = next(gate for gate in report.gates if gate.name == "Data integrity")
    assert not integrity.passed
    assert "94.2% complete" in integrity.observed


def test_durable_trial_count_tightens_bonferroni_gate() -> None:
    bundle = deterministic_demo(2, seed=773)
    config = SandboxConfig()
    replay = SandboxReplayEngine(config).run(bundle)
    marginal = replace(
        replay,
        daily_pnl={f"day-{index}": 1.0 if index < 14 else -1.0 for index in range(20)},
    )
    neighbors = [
        SensitivityPoint(
            config.strategy_name,
            "fixture",
            config.fast_ema,
            config.slow_ema,
            config.trend_threshold_bps,
            config.hard_stop_pct,
            1.0,
            1.0,
            1.2,
            30,
            0.5,
        )
    ]

    one_trial = promotion_report(
        bundle,
        config,
        marginal,
        neighbors,
        {3.0: marginal},
        None,
        RandomControl(1, 0.0, 0.0, 0.0, 0.0),
        total_trial_count=1,
    )
    twenty_trials = promotion_report(
        bundle,
        config,
        marginal,
        neighbors,
        {3.0: marginal},
        None,
        RandomControl(1, 0.0, 0.0, 0.0, 0.0),
        total_trial_count=20,
    )
    first = next(gate for gate in one_trial.gates if gate.name == "Trial-adjusted significance")
    durable = next(
        gate for gate in twenty_trials.gates if gate.name == "Trial-adjusted significance"
    )

    assert first.passed
    assert not durable.passed
    assert "across 20 candidates" in durable.observed


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
    live = AppConfig(bar_seconds=5, trade_every_bars=3, strategy_name="ema_momentum")
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


@pytest.mark.parametrize(
    ("field", "mutated"),
    [
        ("initial_cash", 60.0),
        ("order_notional", 20.0),
        ("slippage_bps", 3.0),
        ("base_spread_bps", 3.0),
        ("spread_volatility_multiplier", 0.20),
        ("commission_per_order", 0.10),
        ("latency_bars", 1),
        ("fill_fraction_pct", 80.0),
        ("rejection_rate_pct", 5.0),
        ("max_volume_participation_pct", 2.0),
        ("random_seed", 8008),
        ("max_entries_per_day", 5),
        ("risk_budget_pct", 0.02),
        ("max_exposure_pct", 0.70),
        ("max_daily_loss_pct", 0.03),
        ("max_consecutive_losses", 2),
        ("volatility_target_pct", 0.20),
        ("force_flat_at_end", False),
    ],
)
def test_fingerprint_binds_every_material_sandbox_execution_field(
    field: str, mutated: object
) -> None:
    base = SandboxConfig()

    assert field in SANDBOX_EXECUTION_FINGERPRINT_FIELDS
    assert strategy_fingerprint(base) != strategy_fingerprint(replace(base, **{field: mutated}))


def test_fingerprint_field_registry_covers_sandbox_behavior_fields() -> None:
    separately_bound = {
        "decision_stride",
        "market_hours",
        "order_type",
        "time_in_force",
        "limit_offset_bps",
        "settlement_model",
    }
    uncovered = (
        set(SandboxConfig.__dataclass_fields__)
        - set(SANDBOX_EXECUTION_FINGERPRINT_FIELDS)
        - set(STRATEGY_FINGERPRINT_FIELDS)
        - separately_bound
    )

    assert uncovered == {"lookback_days", "csv_bar_seconds"}


def test_cost_stress_scales_static_and_dynamic_cost_components() -> None:
    base = SandboxConfig(
        slippage_bps=1.0,
        base_spread_bps=2.0,
        spread_volatility_multiplier=0.25,
        commission_per_order=0.50,
        latency_bars=2,
    )

    stressed = cost_stressed_config(base, 3.0)

    assert stressed.slippage_bps == 3.0
    assert stressed.base_spread_bps == 6.0
    assert stressed.spread_volatility_multiplier == 0.75
    assert stressed.commission_per_order == 1.50
    assert stressed.latency_bars == base.latency_bars
    with pytest.raises(ValueError, match="finite and positive"):
        cost_stressed_config(base, float("nan"))


def test_forced_bypassed_flatten_cannot_satisfy_flat_evidence() -> None:
    bundle = deterministic_demo(3, seed=19)
    config = SandboxConfig(
        strategy_name="close_momentum",
        close_momentum_bps=0.1,
        warmup_bars=5,
        fast_ema=1,
        slow_ema=3,
        trend_threshold_bps=0.1,
        momentum_bars=1,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        hard_stop_pct=0.5,
        take_profit_pct=0.5,
        max_hold_minutes=10_000,
        force_flat_at_end=True,
    )
    replay = SandboxReplayEngine(config).run(bundle)
    assert replay.ending_position is None
    assert any("forced virtual flatten" in event.reason.lower() for event in replay.execution_events)
    passing_numbers = replace(
        replay,
        net_pnl=1.0,
        return_pct=2.0,
        round_trips=5,
        profit_factor=1.20,
        expectancy=0.20,
        max_drawdown_pct=1.0,
        ending_position=None,
    )
    report = promotion_report(
        bundle,
        config,
        passing_numbers,
        [],
        {3.0: passing_numbers},
        None,
        RandomControl(1, 0.0, 0.0, 0.0, 0.0),
        holdout_result=passing_numbers,
        holdout_id=1,
    )

    ending = next(gate for gate in report.gates if gate.name == "Ending flat")
    holdout = next(gate for gate in report.gates if gate.name == "Sealed final holdout")
    assert not ending.passed
    assert "bypassed" in ending.observed
    assert not holdout.passed
    assert "bypassed" in holdout.observed


def test_zero_exposure_cash_can_pass_sizing_parity_but_never_promotion() -> None:
    bundle = deterministic_demo(2, seed=23)
    config = SandboxConfig(strategy_name="cash")
    replay = SandboxReplayEngine(config).run(bundle)
    report = promotion_report(
        bundle,
        config,
        replay,
        [],
        {3.0: replay},
        None,
        RandomControl(1, 0.0, 0.0, 0.0, 0.0),
    )

    sizing = next(gate for gate in report.gates if gate.name == "Runtime sizing parity")
    closed_trades = next(gate for gate in report.gates if gate.name == "Closed-trade sample")
    after_cost = next(gate for gate in report.gates if gate.name == "After-cost quality")
    assert not replay.fills
    assert replay.exposure_pct == 0.0
    assert sizing.passed
    assert not closed_trades.passed
    assert not after_cost.passed
    assert report.status == "SHADOW_ONLY"


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
    scenario = deterministic_demo(40, seed=93)
    bundle = replace(
        scenario,
        source="Fixture manifest-bound observed import",
        provenance=DataProvenance(
            source_kind="imported_manifest",
            provider="Fixture Provider",
            provider_product="Fixture Product",
            acquisition_method="Fixture export",
            license_reference="Fixture research terms",
            license_reviewed_by_user=True,
            research_use_permitted=True,
            automated_strategy_research_permitted=True,
            observed_data=True,
            synthetic_or_interpolated=False,
            construction_method="provider_native",
            source_resolution_seconds=60.0,
            bar_interval="1m",
            market_hours="regular_hours",
            manifest_version=1,
            manifest_hash="a" * 64,
            csv_sha256="b" * 64,
            canonical_dataset_hash=scenario.dataset_hash,
        ),
    )
    bundle = _observed_daily_bundle(141)
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
    monkeypatch.setattr("grande_alpha.research_service.walk_forward", lambda *args: None)
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


def test_source_label_cannot_reserve_holdout_without_bound_provenance(tmp_path) -> None:
    store = AuditStore(tmp_path / "evidence.db")
    bundle = replace(
        deterministic_demo(40, seed=94),
        source="Licensed observed imported market history",
    )

    lab = run_evidence_lab(bundle, SandboxConfig(), store)

    assert lab.holdout_id is None
    assert store.research_promotion(lab.promotion_id) is not None
    with store._lock:
        count = store._connection.execute(
            "SELECT COUNT(*) AS n FROM research_holdouts"
        ).fetchone()["n"]
    assert count == 0
    source_gate = next(gate for gate in lab.report.gates if gate.name == "Historical source")
    assert not source_gate.passed
    store.close()


@pytest.mark.parametrize(("total_sessions", "development_sessions"), [(120, 99), (140, 119)])
def test_total_sessions_below_141_fail_development_breadth_without_reserving_holdout(
    tmp_path, monkeypatch, total_sessions, development_sessions
) -> None:
    store = AuditStore(tmp_path / "evidence.db")
    bundle = _observed_daily_bundle(total_sessions)
    monkeypatch.setattr(
        "grande_alpha.research_service.candidate_grid",
        lambda config: [config],
    )
    monkeypatch.setattr("grande_alpha.research_service.walk_forward", lambda *args: None)

    lab = run_evidence_lab(bundle, SandboxConfig(strategy_name="cash"), store)

    breadth = next(gate for gate in lab.report.gates if gate.name == "Data breadth")
    assert not breadth.passed
    assert breadth.observed == f"{development_sessions} sessions"
    assert lab.holdout_id is None
    with store._lock:
        assert store._connection.execute(
            "SELECT COUNT(*) AS n FROM research_holdouts"
        ).fetchone()["n"] == 0
    store.close()


def test_141_total_sessions_reserve_20_holdout_and_leave_120_development(
    tmp_path, monkeypatch
) -> None:
    store = AuditStore(tmp_path / "evidence.db")
    bundle = _observed_daily_bundle(141)
    monkeypatch.setattr(
        "grande_alpha.research_service.candidate_grid",
        lambda config: [config],
    )
    monkeypatch.setattr("grande_alpha.research_service.walk_forward", lambda *args: None)

    lab = run_evidence_lab(bundle, SandboxConfig(strategy_name="cash"), store)

    breadth = next(gate for gate in lab.report.gates if gate.name == "Data breadth")
    assert breadth.passed
    assert breadth.observed == "120 sessions"
    assert lab.holdout_id is not None
    holdout = store.research_holdout(lab.holdout_id)
    assert holdout is not None and holdout["status"] == "RESERVED"
    store.close()


def test_full_bundle_session_gap_before_purge_cannot_reserve_holdout(
    tmp_path, monkeypatch
) -> None:
    store = AuditStore(tmp_path / "evidence.db")
    complete = _observed_daily_bundle(142)
    frames = complete.frames[:120] + complete.frames[121:]
    quality = assess_quality(frames, "1d")
    assert quality.sessions == 141
    assert quality.missing_sessions == 1
    bundle = replace(
        complete,
        frames=frames,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        provenance=replace(
            complete.provenance,
            canonical_dataset_hash=quality.dataset_hash,
        ),
    )
    monkeypatch.setattr("grande_alpha.research_service.candidate_grid", lambda config: [config])
    monkeypatch.setattr("grande_alpha.research_service.walk_forward", lambda *args: None)

    lab = run_evidence_lab(bundle, SandboxConfig(strategy_name="cash"), store)

    integrity = next(gate for gate in lab.report.gates if gate.name == "Data integrity")
    assert not integrity.passed
    assert lab.holdout_id is None
    with store._lock:
        assert store._connection.execute(
            "SELECT COUNT(*) AS n FROM research_holdouts"
        ).fetchone()["n"] == 0
    store.close()


def test_incomplete_holdout_partition_is_invalidated_without_evaluation(
    tmp_path, monkeypatch
) -> None:
    store = AuditStore(tmp_path / "evidence.db")
    bundle = _observed_daily_bundle(141)
    actual_split = split_final_holdout(bundle, holdout_sessions=20, purge_sessions=1)
    assert actual_split.holdout.quality is not None
    bad_quality = replace(
        actual_split.holdout.quality,
        complete_sessions=18,
        session_coverage_pct=90.0,
    )
    bad_split = replace(
        actual_split,
        holdout=replace(actual_split.holdout, quality=bad_quality),
    )
    monkeypatch.setattr("grande_alpha.research_service.split_final_holdout", lambda *args: bad_split)
    monkeypatch.setattr("grande_alpha.research_service.candidate_grid", lambda config: [config])
    monkeypatch.setattr("grande_alpha.research_service.walk_forward", lambda *args: None)

    lab = run_evidence_lab(bundle, SandboxConfig(strategy_name="cash"), store)
    saved = store.research_holdout(lab.holdout_id)
    final_gate = next(gate for gate in lab.report.gates if gate.name == "Sealed final holdout")

    assert lab.holdout is None
    assert saved is not None and saved["status"] == "INVALID"
    assert "Holdout quality blocked evaluation" in final_gate.observed
    store.close()

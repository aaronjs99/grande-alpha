import sqlite3
from pathlib import Path

import pytest

from grande_alpha.config import AppConfig
from grande_alpha.evidence import (
    EVIDENCE_POLICY_VERSION,
    REQUIRED_LIVE_GATE_NAMES,
    strategy_fingerprint,
)
from grande_alpha.historical import deterministic_demo
from grande_alpha.models import OrderIntent
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine
from grande_alpha.storage import AuditStore


def _passing_gates() -> list[dict[str, object]]:
    return [{"name": name, "passed": True} for name in sorted(REQUIRED_LIVE_GATE_NAMES)]


def _passing_holdout_metrics(
    holdout_hash: str,
    holdout_start: str,
    holdout_end: str,
) -> dict[str, object]:
    return {
        "net_pnl": 1.0,
        "return_pct": 2.0,
        "round_trips": 5,
        "profit_factor": 1.2,
        "expectancy": 0.2,
        "max_drawdown_pct": 1.0,
        "ending_position": None,
        "cost_multiplier": 3.0,
        "forced_flatten_count": 0,
        "holdout_hash": holdout_hash,
        "holdout_start": holdout_start,
        "holdout_end": holdout_end,
    }


def test_receipts_and_idempotent_intents_are_persisted(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    store.receipt("test", "hello", {"value": 1})
    order = OrderIntent("ref-1", "TQQQ", "buy", "test", dollar_amount=10.0)
    store.record_intent(order)
    store.update_intent(order.ref_id, "broker-1", "queued")
    receipts = store.recent_receipts()
    assert receipts[0]["summary"] == "hello"
    store.close()


def test_order_intent_submission_provenance_migrates_and_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE order_intents(
        ref_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,symbol TEXT NOT NULL,
        side TEXT NOT NULL,reason TEXT NOT NULL,payload_json TEXT NOT NULL,
        broker_order_id TEXT,broker_state TEXT)"""
    )
    connection.commit()
    connection.close()

    store = AuditStore(path)
    columns = {
        row["name"] for row in store._connection.execute("PRAGMA table_info(order_intents)")
    }
    assert {
        "account_number",
        "authority_id",
        "strategy_fingerprint",
        "authorized_notional",
        "submission_started_at",
    } <= columns
    intent = OrderIntent("ref-submit", "TQQQ", "buy", "test", dollar_amount=10.0)
    store.record_intent(intent)
    store.mark_intent_submitting(
        intent.ref_id,
        account_number="acct-1",
        authority_id="authority-1",
        strategy_fingerprint="fingerprint-1",
        authorized_notional=10.0,
    )

    unresolved = store.unresolved_order_intents("acct-1")
    assert [row["ref_id"] for row in unresolved] == [intent.ref_id]
    assert unresolved[0]["broker_state"] == "submitting"
    with pytest.raises(ValueError, match="already invoked"):
        store.mark_intent_submitting(
            intent.ref_id,
            account_number="acct-1",
            authority_id="authority-1",
            strategy_fingerprint="fingerprint-1",
            authorized_notional=10.0,
        )
    with pytest.raises(ValueError, match="finite and nonnegative"):
        store.mark_intent_submitting(
            "missing",
            account_number="acct-1",
            authority_id="authority-1",
            strategy_fingerprint="fingerprint-1",
            authorized_notional=float("nan"),
        )
    store.update_intent(intent.ref_id, None, "submission_uncertain")
    assert store.unresolved_order_intents("acct-1")
    store.update_intent(intent.ref_id, "broker-1", "filled")
    assert store.unresolved_order_intents("acct-1") == []
    store.close()


def test_live_daily_usage_counts_placement_invocations_by_eastern_date(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    for ref_id, amount in (("late-et", 10.0), ("next-et", 20.0), ("other-account", 99.0)):
        intent = OrderIntent(ref_id, "TQQQ", "buy", "test", dollar_amount=amount)
        store.record_intent(intent)
        store.mark_intent_submitting(
            ref_id,
            account_number="acct-2" if ref_id == "other-account" else "acct-1",
            authority_id="authority-1",
            strategy_fingerprint="fingerprint-1",
            authorized_notional=amount,
        )
    with store._connection:
        store._connection.execute(
            "UPDATE order_intents SET submission_started_at=? WHERE ref_id='late-et'",
            ("2026-08-12T03:30:00+00:00",),  # Aug 11 at 11:30 PM ET
        )
        store._connection.execute(
            "UPDATE order_intents SET submission_started_at=? WHERE ref_id='next-et'",
            ("2026-08-12T04:30:00+00:00",),  # Aug 12 at 12:30 AM ET
        )
    store.receipt("authority_action", "chain", {"receipt_digest": "digest-2"})

    first = store.live_daily_usage("acct-1", "2026-08-11")
    second = store.live_daily_usage("acct-1", "2026-08-12")

    assert first == {
        "daily_notional": 10.0,
        "submitted_orders": 1,
        "last_receipt_digest": "digest-2",
    }
    assert second["daily_notional"] == 20.0
    assert second["submitted_orders"] == 1
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        store.live_daily_usage("acct-1", "08/12/2026")
    store.close()


def test_research_fund_requires_plan_then_external_confirmation(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    entry_id = store.plan_research_contribution(
        period="2026-08",
        realized_profit=100.0,
        fees=5.0,
        tax_reserve=25.0,
        contribution_rate=0.25,
        notes="personal realized profit only",
    )

    entry = store.research_fund_entries()[0]
    assert entry["id"] == entry_id
    assert entry["eligible_contribution"] == 17.5
    assert entry["status"] == "planned"
    assert store.confirmed_research_total() == 0.0

    store.confirm_research_contribution(entry_id, "bank-reference-123")
    confirmed = store.research_fund_entries()[0]
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmation_reference"] == "bank-reference-123"
    assert store.confirmed_research_total() == 17.5
    store.close()


def test_research_fund_never_allocates_losses_and_rejects_duplicate_confirmation(
    tmp_path: Path,
) -> None:
    store = AuditStore(tmp_path / "audit.db")
    entry_id = store.plan_research_contribution("2026-07", -10.0, 0.0, 0.0, 1.0)
    assert store.research_fund_entries()[0]["eligible_contribution"] == 0.0
    store.confirm_research_contribution(entry_id, "external-reference")

    try:
        store.confirm_research_contribution(entry_id, "second-reference")
    except ValueError as exc:
        assert "already confirmed" in str(exc)
    else:
        raise AssertionError("Duplicate fund confirmation should fail")
    store.close()


def test_sandbox_fill_ledger_persists_unsettled_cash(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    bundle = deterministic_demo(2, seed=9)
    config = SandboxConfig(
        warmup_bars=5,
        fast_ema=1,
        slow_ema=3,
        trend_threshold_bps=0.1,
        momentum_bars=1,
        hard_stop_pct=0.5,
        take_profit_pct=0.5,
        max_hold_minutes=100,
        max_entries_per_day=10,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        force_flat_at_end=True,
        settlement_model="cash_t1",
    )
    result = SandboxReplayEngine(config).run(bundle)
    store.record_sandbox_run(
        result.run_id,
        result.source,
        result.start.isoformat(),
        result.end.isoformat(),
        config.__dict__,
        result.metrics(),
        [fill.as_dict() for fill in result.fills],
    )

    saved = store.sandbox_run(result.run_id)
    assert saved is not None
    assert saved["fills"][-1]["unsettled_cash_after"] > 0
    store.close()


def test_live_evidence_is_exact_strategy_scoped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("grande_alpha.evidence.RUNTIME_SIZING_PARITY_CERTIFIED", True)
    store = AuditStore(tmp_path / "audit.db")
    fingerprint = strategy_fingerprint(AppConfig())
    holdout_id = store.reserve_research_holdout(
        dataset_hash="hash-1",
        development_hash="development-1",
        holdout_hash="holdout-1",
        holdout_start="2026-07-01T13:30:00+00:00",
        holdout_end="2026-08-09T16:00:00+00:00",
        policy_version=EVIDENCE_POLICY_VERSION,
    )
    store.freeze_research_holdout(holdout_id, fingerprint)
    store.claim_research_holdout(holdout_id, fingerprint)
    store.consume_research_holdout(
        holdout_id,
        fingerprint,
        _passing_holdout_metrics(
            "holdout-1",
            "2026-07-01T13:30:00+00:00",
            "2026-08-09T16:00:00+00:00",
        ),
    )
    store.record_research_promotion(
        dataset_hash="hash-1",
        strategy_fingerprint=fingerprint,
        policy_version=EVIDENCE_POLICY_VERSION,
        status="LIVE_REVIEW_ELIGIBLE",
        source="licensed CSV",
        replay_end="2026-08-09T16:00:00+00:00",
        gates=_passing_gates(),
        risk_envelope={
            "max_order_notional": 25.0,
            "max_daily_notional": 300.0,
            "max_total_exposure": 40.0,
            "max_daily_loss": 2.0,
            "max_trades": 6,
            "max_orders_per_minute": 2,
            "max_spread_bps": 6.0,
        },
        holdout_id=holdout_id,
    )
    assert store.current_live_evidence(fingerprint) is not None
    assert store.current_live_evidence("different-strategy") is None
    assert (
        store.current_live_evidence(
            fingerprint,
            requested_envelope={
                "max_order_notional": 25.0,
                "max_daily_notional": 300.0,
                "max_total_exposure": 40.0,
                "max_daily_loss": 2.0,
                "max_trades": 6,
                "max_orders_per_minute": 2,
                "max_spread_bps": 7.0,
            },
        )
        is None
    )
    assert (
        store.current_live_evidence(
            fingerprint,
            requested_envelope={
                "max_order_notional": float("nan"),
                "max_daily_notional": 300.0,
                "max_total_exposure": 40.0,
                "max_daily_loss": 2.0,
                "max_trades": 6,
                "max_orders_per_minute": 2,
                "max_spread_bps": 6.0,
            },
        )
        is None
    )
    with store._connection:
        store._connection.execute(
            "UPDATE research_promotions SET policy_version=?",
            (EVIDENCE_POLICY_VERSION - 1,),
        )
        store._connection.execute(
            "UPDATE research_holdouts SET policy_version=?",
            (EVIDENCE_POLICY_VERSION - 1,),
        )
    assert store.current_live_evidence(fingerprint) is None
    store.close()


def test_final_holdout_is_one_use_and_exact_strategy_scoped(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    holdout_id = store.reserve_research_holdout(
        dataset_hash="dataset-a",
        development_hash="development-a",
        holdout_hash="holdout-a",
        holdout_start="2026-07-01T13:30:00+00:00",
        holdout_end="2026-08-01T20:00:00+00:00",
        policy_version=EVIDENCE_POLICY_VERSION,
    )
    assert (
        store.reserve_research_holdout(
            dataset_hash="dataset-a",
            development_hash="development-a",
            holdout_hash="holdout-a",
            holdout_start="2026-07-01T13:30:00+00:00",
            holdout_end="2026-08-01T20:00:00+00:00",
            policy_version=EVIDENCE_POLICY_VERSION,
        )
        == holdout_id
    )
    with pytest.raises(ValueError, match="already reserved"):
        store.reserve_research_holdout(
            dataset_hash="different-development-dataset",
            development_hash="different-development",
            holdout_hash="holdout-a",
            holdout_start="2026-07-01T13:30:00+00:00",
            holdout_end="2026-08-01T20:00:00+00:00",
            policy_version=EVIDENCE_POLICY_VERSION,
        )
    store.freeze_research_holdout(holdout_id, "candidate-a")
    with pytest.raises(ValueError, match="exact strategy"):
        store.claim_research_holdout(holdout_id, "candidate-b")
    store.claim_research_holdout(holdout_id, "candidate-a")
    store.consume_research_holdout(holdout_id, "candidate-a", {"return_pct": 2.0})

    with pytest.raises(ValueError, match="already consumed"):
        store.consume_research_holdout(holdout_id, "candidate-a", {"return_pct": 3.0})
    with pytest.raises(ValueError, match="already reserved"):
        store.reserve_research_holdout(
            dataset_hash="dataset-a",
            development_hash="development-a",
            holdout_hash="holdout-a",
            holdout_start="2026-07-01T13:30:00+00:00",
            holdout_end="2026-08-01T20:00:00+00:00",
            policy_version=EVIDENCE_POLICY_VERSION,
        )
    with pytest.raises(ValueError, match="already reserved"):
        store.reserve_research_holdout(
            dataset_hash="dataset-a",
            development_hash="development-a",
            holdout_hash="holdout-a",
            holdout_start="2026-07-01T13:30:00+00:00",
            holdout_end="2026-08-01T20:00:00+00:00",
            policy_version=EVIDENCE_POLICY_VERSION + 1,
        )
    saved = store.research_holdout(holdout_id)
    assert saved is not None
    assert saved["status"] == "CONSUMED"
    assert saved["selected_fingerprint"] == "candidate-a"
    assert saved["metrics"]["return_pct"] == 2.0
    store.close()


def test_live_evidence_cannot_be_recorded_without_consumed_holdout(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    with pytest.raises(ValueError, match="passing final holdout"):
        store.record_research_promotion(
            dataset_hash="dataset-a",
            strategy_fingerprint="candidate-a",
            policy_version=EVIDENCE_POLICY_VERSION,
            status="LIVE_REVIEW_ELIGIBLE",
            source="fixture",
            replay_end="2026-08-10T20:00:00+00:00",
            gates=_passing_gates(),
            risk_envelope={},
        )
    store.close()


def test_live_evidence_receipt_fails_closed_when_any_gate_failed(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    failed = _passing_gates()
    failed[0]["passed"] = False
    with pytest.raises(ValueError, match="canonical evidence gate"):
        store.record_research_promotion(
            dataset_hash="hash-1",
            strategy_fingerprint="candidate-1",
            policy_version=EVIDENCE_POLICY_VERSION,
            status="LIVE_REVIEW_ELIGIBLE",
            source="fixture",
            replay_end="2026-08-10T20:00:00+00:00",
            gates=failed,
            risk_envelope={},
        )
    store.close()


def test_live_certificate_rejects_unrelated_or_losing_holdout(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    fingerprint = "candidate-a"
    holdout_id = store.reserve_research_holdout(
        dataset_hash="dataset-a",
        development_hash="development-a",
        holdout_hash="holdout-a",
        holdout_start="2026-07-01T13:30:00+00:00",
        holdout_end="2026-08-01T20:00:00+00:00",
        policy_version=EVIDENCE_POLICY_VERSION,
    )
    store.freeze_research_holdout(holdout_id, fingerprint)
    store.claim_research_holdout(holdout_id, fingerprint)
    losing = _passing_holdout_metrics(
        "holdout-a",
        "2026-07-01T13:30:00+00:00",
        "2026-08-01T20:00:00+00:00",
    )
    losing["net_pnl"] = -99.0
    store.consume_research_holdout(holdout_id, fingerprint, losing)

    for dataset_hash in ("DIFFERENT-DATASET", "dataset-a"):
        with pytest.raises(ValueError, match="passing final holdout"):
            store.record_research_promotion(
                dataset_hash=dataset_hash,
                strategy_fingerprint=fingerprint,
                policy_version=EVIDENCE_POLICY_VERSION,
                status="LIVE_REVIEW_ELIGIBLE",
                source="licensed CSV",
                replay_end="2026-08-01T20:00:00+00:00",
                gates=_passing_gates(),
                risk_envelope={},
                holdout_id=holdout_id,
            )
    assert store.current_live_evidence(fingerprint) is None
    store.close()


def test_noncash_certificate_cannot_omit_or_forge_runtime_sizing_parity(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    fingerprint = strategy_fingerprint(SandboxConfig(strategy_name="ema_momentum"))
    holdout_id = store.reserve_research_holdout(
        dataset_hash="sizing-dataset",
        development_hash="sizing-development",
        holdout_hash="sizing-holdout",
        holdout_start="2026-07-01T13:30:00+00:00",
        holdout_end="2026-08-01T20:00:00+00:00",
        policy_version=EVIDENCE_POLICY_VERSION,
    )
    store.freeze_research_holdout(holdout_id, fingerprint)
    store.claim_research_holdout(holdout_id, fingerprint)
    store.consume_research_holdout(
        holdout_id,
        fingerprint,
        _passing_holdout_metrics(
            "sizing-holdout",
            "2026-07-01T13:30:00+00:00",
            "2026-08-01T20:00:00+00:00",
        ),
    )
    envelope = {
        "max_order_notional": 25.0,
        "max_daily_notional": 300.0,
        "max_total_exposure": 40.0,
        "max_daily_loss": 2.0,
        "max_trades": 6,
        "max_orders_per_minute": 2,
        "max_spread_bps": 6.0,
    }
    omitted = [gate for gate in _passing_gates() if gate["name"] != "Runtime sizing parity"]

    with pytest.raises(ValueError, match="canonical evidence gate"):
        store.record_research_promotion(
            dataset_hash="sizing-dataset",
            strategy_fingerprint=fingerprint,
            policy_version=EVIDENCE_POLICY_VERSION,
            status="LIVE_REVIEW_ELIGIBLE",
            source="licensed CSV",
            replay_end="2026-08-01T20:00:00+00:00",
            gates=omitted,
            risk_envelope=envelope,
            holdout_id=holdout_id,
        )

    with pytest.raises(ValueError, match="certified sizing contract"):
        store.record_research_promotion(
            dataset_hash="sizing-dataset",
            strategy_fingerprint=fingerprint,
            policy_version=EVIDENCE_POLICY_VERSION,
            status="LIVE_REVIEW_ELIGIBLE",
            source="licensed CSV",
            replay_end="2026-08-01T20:00:00+00:00",
            gates=_passing_gates(),
            risk_envelope=envelope,
            holdout_id=holdout_id,
        )
    assert store.current_live_evidence(fingerprint) is None
    store.close()


def test_live_certificate_rejects_holdout_that_needed_bypassed_flatten(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    fingerprint = "candidate-forced-flat"
    holdout_id = store.reserve_research_holdout(
        dataset_hash="dataset-forced-flat",
        development_hash="development-forced-flat",
        holdout_hash="holdout-forced-flat",
        holdout_start="2026-07-01T13:30:00+00:00",
        holdout_end="2026-08-01T20:00:00+00:00",
        policy_version=EVIDENCE_POLICY_VERSION,
    )
    store.freeze_research_holdout(holdout_id, fingerprint)
    store.claim_research_holdout(holdout_id, fingerprint)
    metrics = _passing_holdout_metrics(
        "holdout-forced-flat",
        "2026-07-01T13:30:00+00:00",
        "2026-08-01T20:00:00+00:00",
    )
    metrics["forced_flatten_count"] = 1
    store.consume_research_holdout(holdout_id, fingerprint, metrics)

    with pytest.raises(ValueError, match="passing final holdout"):
        store.record_research_promotion(
            dataset_hash="dataset-forced-flat",
            strategy_fingerprint=fingerprint,
            policy_version=EVIDENCE_POLICY_VERSION,
            status="LIVE_REVIEW_ELIGIBLE",
            source="licensed CSV",
            replay_end="2026-08-01T20:00:00+00:00",
            gates=_passing_gates(),
            risk_envelope={
                "max_order_notional": 25.0,
                "max_daily_notional": 300.0,
                "max_total_exposure": 40.0,
                "max_daily_loss": 2.0,
                "max_trades": 6,
                "max_orders_per_minute": 2,
                "max_spread_bps": 6.0,
            },
            holdout_id=holdout_id,
        )
    assert store.current_live_evidence(fingerprint) is None
    store.close()


def test_research_trial_ledger_counts_unique_candidates_per_dataset(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    trial = {
        "trial_fingerprint": "candidate-a",
        "config": {"strategy_name": "first_half_hour_momentum"},
        "metrics": {"return_pct": -0.5},
    }

    assert store.record_research_trials("dataset-a", [trial]) == 1
    assert store.record_research_trials("dataset-a", [trial]) == 0
    assert store.record_research_trials("dataset-b", [trial]) == 1
    assert store.research_trial_count("dataset-a") == 1
    assert store.research_trial_count("dataset-b") == 1
    store.close()


def test_research_promotions_and_sandbox_runs_have_decoded_cli_views(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    promotion_id = store.record_research_promotion(
        dataset_hash="dataset-a",
        strategy_fingerprint="candidate-a",
        policy_version=EVIDENCE_POLICY_VERSION,
        status="SHADOW_ONLY",
        source="fixture",
        replay_end="2026-08-10T20:00:00+00:00",
        gates=[{"name": "fixture", "passed": False, "observed": "no", "requirement": "yes"}],
        risk_envelope={"max_order_notional": 10},
    )
    store.record_sandbox_run(
        "run-a",
        "fixture",
        "2026-08-10T19:00:00+00:00",
        "2026-08-10T20:00:00+00:00",
        {"strategy_name": "ema_momentum"},
        {"return_pct": 1.0},
        [
            {
                "timestamp": "2026-08-10T19:30:00+00:00",
                "symbol": "TQQQS",
                "side": "buy",
                "quantity": 1,
                "price": 10,
                "commission": 0,
                "realized_pnl": None,
                "reason": "fixture",
                "cash_after": 40,
            }
        ],
    )

    promotion = store.research_promotion(promotion_id)
    run = store.sandbox_run("run-a")

    assert promotion is not None and promotion["gates"][0]["name"] == "fixture"
    assert promotion["risk_envelope"]["max_order_notional"] == 10
    assert store.recent_research_promotions()[0]["id"] == promotion_id
    assert run is not None and run["metrics"]["return_pct"] == 1.0
    assert run["fills"][0]["symbol"] == "TQQQS"
    store.close()

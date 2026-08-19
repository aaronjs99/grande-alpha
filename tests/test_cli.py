import json

import pytest

from grande_alpha import cli
from grande_alpha.config import AppConfig
from grande_alpha.evidence import EVIDENCE_POLICY_VERSION
from grande_alpha.sandbox import SandboxConfig
from grande_alpha.storage import AuditStore


def test_cli_table_wraps_without_hiding_long_evidence_text() -> None:
    rendered = cli.format_table(
        ["Gate", "Status", "Observed", "Requirement"],
        [["Historical source", "FAIL", "Deterministic offline scenario", "Observed market history is required"]],
        width=72,
    )

    assert "Historical" in rendered
    assert "Deterministic" in rendered
    assert "market" in rendered and "history" in rendered
    assert all(len(line) <= 72 for line in rendered.splitlines())


def test_cli_table_uses_readable_ascii_and_keeps_status_on_one_line() -> None:
    rendered = cli.format_table(
        ["Condition", "Status", "Current result"],
        [["Scheduled auto-shadow", "READ-ONLY", "evidence—not authority…"]],
        width=100,
    )

    assert "READ-ONLY" in rendered
    assert "evidence-not authority..." in rendered
    assert "—" not in rendered and "…" not in rendered


def test_cli_reads_the_same_evidence_receipt_and_explains_blockers(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "cli.db"
    store = AuditStore(database)
    promotion_id = store.record_research_promotion(
        dataset_hash="dataset-123",
        strategy_fingerprint="strategy-123",
        policy_version=EVIDENCE_POLICY_VERSION,
        status="SHADOW_ONLY",
        source="Deterministic offline scenario",
        replay_end="2026-08-10T20:00:00+00:00",
        gates=[
            {
                "name": "Historical source",
                "passed": False,
                "observed": "Deterministic offline scenario",
                "requirement": "Observed market history",
            },
            {
                "name": "Data integrity",
                "passed": True,
                "observed": "0 missing",
                "requirement": "0 missing",
            },
        ],
        risk_envelope={},
    )
    store.close()
    monkeypatch.setattr(cli, "AuditStore", lambda: AuditStore(database))

    result = cli.main(["evidence", "show", "--id", str(promotion_id), "--width", "100"])
    output = capsys.readouterr().out

    assert result == 0
    assert "1/2 independent gates passed" in output
    assert "not a progress score" in output
    assert "Historical source — FAIL" in output
    assert "can never pass this gate" in output


def test_cli_glossary_includes_evidence_statistics(capsys) -> None:
    result = cli.main(["glossary", "Deflated Sharpe", "--width", "100"])
    output = capsys.readouterr().out

    assert result == 0
    assert "Deflated Sharpe" in output
    assert "registered strategy trials" in output


def test_cli_activation_is_offline_owner_labeled_and_has_no_authority_command(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "activation.db"
    store = AuditStore(database)
    store.record_research_promotion(
        dataset_hash="dataset-123",
        strategy_fingerprint="strategy-123",
        policy_version=EVIDENCE_POLICY_VERSION,
        status="SHADOW_ONLY",
        source="Deterministic offline scenario",
        replay_end="2026-08-10T20:00:00+00:00",
        gates=[
            {
                "name": "Historical source",
                "passed": False,
                "observed": "Deterministic offline scenario",
                "requirement": "Observed market history",
            }
        ],
        risk_envelope={},
    )
    store.close()
    monkeypatch.setattr(cli, "AuditStore", lambda: AuditStore(database))

    result = cli.main(["activation", "--width", "120"])
    output = capsys.readouterr().out

    assert result == 0
    assert "local inspection only" in output
    assert "structurally read-only" in output
    assert "APP GATE" in output
    assert "Outside-app responsibility" in output
    assert "does not collect or certify jurisdiction" in output
    assert "Positive exact evidence" in output
    assert "Historical source" not in output
    assert "never pass this gate" not in output.replace("\n", " ")
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["authorize"])


def test_cli_activation_marks_old_policy_receipt_stale_not_current(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "stale-activation.db"
    store = AuditStore(database)
    store.record_research_promotion(
        dataset_hash="dataset-old",
        strategy_fingerprint="strategy-old",
        policy_version=EVIDENCE_POLICY_VERSION - 1,
        status="SHADOW_ONLY",
        source="Licensed CSV",
        replay_end="2026-08-10T20:00:00+00:00",
        gates=[
            {
                "name": "Historical source",
                "passed": True,
                "observed": "Licensed CSV",
                "requirement": "Observed market history",
            }
        ],
        risk_envelope={},
    )
    store.close()
    monkeypatch.setattr(cli, "AuditStore", lambda: AuditStore(database))

    result = cli.main(["activation", "--width", "150"])
    output = capsys.readouterr().out
    flattened = " ".join(output.split())

    assert result == 0
    assert "STALE / INELIGIBLE" in output
    assert f"v{EVIDENCE_POLICY_VERSION}" in output
    assert "historical" in flattened and "1/1" in flattened
    assert "1/1 current-policy gates" not in flattened


def test_cli_activation_flags_nonpilot_persisted_route(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "route-activation.db"
    AuditStore(database).close()
    monkeypatch.setattr(cli, "AuditStore", lambda: AuditStore(database))
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: AppConfig(
            market_hours="all_day_hours",
            order_type="limit",
            time_in_force="gtc",
            settlement_model="cash_t1",
        ),
    )

    result = cli.main(["activation", "--json"])
    payload = json.loads(capsys.readouterr().out)
    route = next(
        condition
        for condition in payload["conditions"]
        if condition["gate"] == "Supported real-order route"
    )

    assert result == 0
    assert route["status"] == "BLOCKED"
    assert "24 Hour Market" in route["observed"]
    assert "modeled latency" in route["observed"]
    assert "Apply bounded pilot settings" in route["action"]


def test_cli_activation_pilot_route_uses_contract_latency_check(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "latency-activation.db"
    AuditStore(database).close()
    config = AppConfig()
    candidate = SandboxConfig(
        latency_bars=1,
        decision_stride=config.trade_every_bars,
        strategy_name=config.strategy_name,
    )
    monkeypatch.setattr(cli, "AuditStore", lambda: AuditStore(database))
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "load_sandbox_config", lambda: candidate)

    result = cli.main(["activation", "--json"])
    payload = json.loads(capsys.readouterr().out)
    route = next(
        condition
        for condition in payload["conditions"]
        if condition["gate"] == "Supported real-order route"
    )

    assert result == 0
    assert route["status"] == "BLOCKED"
    assert "modeled latency 1 bars" in route["observed"]
    assert "Extra latency at 0 bars" in route["action"]


def test_cli_activation_requires_current_exact_fingerprint_lookup(monkeypatch, capsys) -> None:
    config = AppConfig()
    looked_up: list[str] = []

    class FakeStore:
        def research_promotion(self):
            return {
                "id": 9,
                "status": "LIVE_REVIEW_ELIGIBLE",
                "policy_version": EVIDENCE_POLICY_VERSION,
                "strategy_fingerprint": "f" * 64,
                "gates": [{"name": "Historical source", "passed": True}],
            }

        def current_live_evidence(self, fingerprint):
            looked_up.append(fingerprint)
            return None

        def close(self):
            return None

    monkeypatch.setattr(cli, "AuditStore", FakeStore)
    monkeypatch.setattr(cli, "load_config", lambda: config)

    result = cli.main(["activation", "--json"])
    payload = json.loads(capsys.readouterr().out)
    evidence = next(
        condition
        for condition in payload["conditions"]
        if condition["gate"] == "Positive exact evidence"
    )

    assert result == 0
    assert looked_up == [cli._current_runtime_fingerprint(config)]
    assert evidence["status"] == "BLOCKED"
    assert "INELIGIBLE FOR CURRENT RUNTIME" in evidence["observed"]
    assert not payload["current_exact_evidence"]
    assert payload["latest_receipt_uses_current_policy"]
    assert not payload["latest_receipt_matches_current_fingerprint"]
    assert payload["evidence_failures"] == []
    assert "latest_receipt_is_current" not in payload

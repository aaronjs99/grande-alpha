from grande_alpha import cli
from grande_alpha.evidence import EVIDENCE_POLICY_VERSION
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

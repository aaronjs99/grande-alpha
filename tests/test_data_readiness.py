from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grande_alpha import cli
from grande_alpha.data_readiness import (
    EXPECTED_SYMBOLS,
    MANIFEST_REQUIRED_FIELDS,
    CsvInspection,
    audit_bundle,
    audit_csv_dataset,
    audit_evidence_ledger,
    inspect_csv,
    load_audited_csv_dataset,
    manifest_template,
)
from grande_alpha.historical import (
    DataQuality,
    HistoricalBundle,
    ReplayFrame,
    assess_quality,
    dataset_hash,
)
from grande_alpha.market_calendar import is_regular_trading_day
from grande_alpha.models import Bar
from grande_alpha.storage import AuditStore


def _bar(symbol: str, timestamp: datetime, price: float = 100.0) -> Bar:
    return Bar(symbol, timestamp, price, price + 0.1, price - 0.1, price, 1, 1000.0)


def _write_csv(path: Path, *, seconds: int = 5, timestamps: int = 30) -> None:
    start = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    rows = ["timestamp,symbol,open,high,low,close,volume,market_hours"]
    for index in range(timestamps):
        timestamp = start + timedelta(seconds=index * seconds)
        for symbol in EXPECTED_SYMBOLS:
            rows.append(
                f"{timestamp.isoformat()},{symbol},100,100.1,99.9,100,1000,regular_hours"
            )
    path.write_text("\n".join(rows), encoding="utf-8")


def _manifest_for(
    bundle: HistoricalBundle,
    inspection: CsvInspection,
    *,
    source_resolution_seconds: float = 5.0,
) -> dict:
    result = manifest_template("5s")
    result.update(
        dataset_id="unit-observed-5s",
        created_at="2026-08-03T21:00:00+00:00",
        provider="Unit Market Data LLC",
        provider_product="Observed test export",
        acquisition_method="authenticated test export",
        license_reference="unit-test research agreement",
        license_reviewed_by_user=True,
        research_use_permitted=True,
        automated_strategy_research_permitted=True,
        observed_data=True,
        synthetic_or_interpolated=False,
        symbols=list(EXPECTED_SYMBOLS),
        bar_interval="5s",
        source_resolution_seconds=source_resolution_seconds,
        construction_method="provider_native",
        contains_upsampled_rows=False,
        timestamp_timezone="UTC",
        timestamp_semantics="bar_start",
        market_hours=bundle.market_hours,
        start=bundle.start.isoformat(),
        end=bundle.end.isoformat(),
        price_adjustment="split_adjusted",
        corporate_action_policy="Provider applies split factors to OHLCV consistently.",
        csv_sha256=inspection.file_sha256,
        dataset_hash=bundle.dataset_hash,
        row_count=inspection.row_count,
    )
    return result


def test_csv_audit_rejects_one_minute_rows_relabelled_as_five_seconds(tmp_path: Path) -> None:
    csv_path = tmp_path / "coarse.csv"
    manifest_path = tmp_path / "coarse.manifest.json"
    _write_csv(csv_path, seconds=60)
    inspection = inspect_csv(csv_path)

    from grande_alpha.historical import load_csv_history

    bundle = load_csv_history(csv_path, "5s")
    manifest = _manifest_for(bundle, inspection, source_resolution_seconds=60.0)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit_csv_dataset(
        csv_path,
        "5s",
        target_interval="5s",
        manifest_path=manifest_path,
        now=bundle.end,
    )

    assert not report.input_ready
    cadence = next(check for check in report.checks if check.name == "Exact native cadence")
    masquerade = next(check for check in report.checks if check.name == "No coarse-data masquerade")
    assert not cadence.passed
    assert "60s" in cadence.observed
    assert not masquerade.passed


def test_csv_audit_rejects_file_mutation_between_inspection_and_load(
    tmp_path: Path, monkeypatch
) -> None:
    csv_path = tmp_path / "changing.csv"
    _write_csv(csv_path)
    from grande_alpha import data_readiness

    original_loader = data_readiness.load_csv_history_bytes

    def mutating_loader(raw_csv: bytes, source_name: str, interval: str):
        bundle = original_loader(raw_csv, source_name, interval)
        csv_path.write_text(csv_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return bundle

    monkeypatch.setattr(data_readiness, "load_csv_history_bytes", mutating_loader)

    import pytest

    with pytest.raises(ValueError, match="changed during readiness audit"):
        load_audited_csv_dataset(csv_path, "5s")


def test_csv_audit_rejects_mutation_during_inspection_before_hash(
    tmp_path: Path, monkeypatch
) -> None:
    csv_path = tmp_path / "changing-during-inspection.csv"
    _write_csv(csv_path)
    from grande_alpha import data_readiness

    original_inspector = data_readiness.inspect_csv_bytes

    def mutating_inspector(raw_csv: bytes):
        inspection = original_inspector(raw_csv)
        csv_path.write_text(csv_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return inspection

    monkeypatch.setattr(data_readiness, "inspect_csv_bytes", mutating_inspector)

    import pytest

    with pytest.raises(ValueError, match="changed during readiness audit"):
        load_audited_csv_dataset(csv_path, "5s")


def test_bound_manifest_can_qualify_an_otherwise_complete_input() -> None:
    start = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    frames = [
        ReplayFrame(start, _bar("QQQ", start), _bar("TQQQ", start), _bar("SQQQ", start)),
        ReplayFrame(
            start + timedelta(seconds=5),
            _bar("QQQ", start + timedelta(seconds=5)),
            _bar("TQQQ", start + timedelta(seconds=5)),
            _bar("SQQQ", start + timedelta(seconds=5)),
        ),
    ]
    digest = dataset_hash(frames)
    quality = DataQuality(
        aligned_bars=2,
        sessions=141,
        missing_intervals=0,
        zero_volume_bars=0,
        duplicate_timestamps=0,
        invalid_session_bars=0,
        interval="5s",
        dataset_hash=digest,
        complete_sessions=141,
        session_coverage_pct=100.0,
        expected_sessions=141,
        missing_sessions=0,
    )
    bundle = HistoricalBundle(
        "Imported observed CSV",
        start,
        frames,
        "5s",
        digest,
        quality,
        "regular_hours",
    )
    inspection = CsvInspection(
        file_sha256="a" * 64,
        row_count=6,
        invalid_rows=0,
        duplicate_keys=0,
        incomplete_timestamps=0,
        out_of_session_rows=0,
        headers=("timestamp", "symbol", "open", "high", "low", "close", "volume", "market_hours"),
        symbols=EXPECTED_SYMBOLS,
        market_hours=("regular_hours",),
        timezone_aware_timestamps=True,
    )
    manifest = _manifest_for(bundle, inspection)

    report = audit_bundle(
        bundle,
        label="observed.csv",
        target_interval="5s",
        manifest=manifest,
        inspection=inspection,
        now=bundle.end,
    )

    assert report.input_ready
    assert all(check.passed for check in report.checks)


def test_readiness_rejects_an_entire_omitted_exchange_session() -> None:
    cursor = datetime(2025, 10, 1, 15, 0, tzinfo=UTC)
    frames: list[ReplayFrame] = []
    while len(frames) < 142:
        if is_regular_trading_day(cursor.date()):
            frames.append(
                ReplayFrame(
                    cursor,
                    _bar("QQQ", cursor),
                    _bar("TQQQ", cursor),
                    _bar("SQQQ", cursor),
                )
            )
        cursor += timedelta(days=1)
    del frames[70]
    quality = assess_quality(frames, "1d")
    bundle = HistoricalBundle(
        "Observed-with-gap fixture",
        frames[-1].start,
        frames,
        "1d",
        quality.dataset_hash,
        quality,
        "regular_hours",
    )

    report = audit_bundle(bundle, label="gap.csv", target_interval="1d", now=bundle.end)
    integrity = next(check for check in report.checks if check.name == "Data integrity")

    assert quality.sessions == 141
    assert quality.missing_sessions == 1
    assert not integrity.passed
    assert "1 missing sessions" in integrity.observed


def test_manifest_template_is_exact_and_defaults_to_no_rights_attestation() -> None:
    template = manifest_template("5s")

    assert set(template) == MANIFEST_REQUIRED_FIELDS
    assert template["bar_interval"] == "5s"
    assert template["source_resolution_seconds"] == 5
    assert template["license_reviewed_by_user"] is False
    assert template["research_use_permitted"] is False
    assert template["automated_strategy_research_permitted"] is False
    assert template["redistribution_permitted"] is False


def test_ledger_inventory_is_query_only_and_does_not_reserve_a_holdout(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    store = AuditStore(database)
    store.record_research_promotion(
        dataset_hash="dataset",
        strategy_fingerprint="fingerprint",
        policy_version=9,
        status="SHADOW_ONLY",
        source="Deterministic offline scenario",
        replay_end="2026-08-03T20:00:00+00:00",
        gates=[
            {
                "name": "Historical source",
                "passed": False,
                "observed": "scenario",
                "requirement": "observed",
            }
        ],
        risk_envelope={},
    )
    store.close()
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    inventory = audit_evidence_ledger(database)

    after = hashlib.sha256(database.read_bytes()).hexdigest()
    assert inventory["read_only"] is True
    assert inventory["promotions"] == 1
    assert inventory["holdouts"] == 0
    assert inventory["holdout_statuses"] == {}
    assert before == after


def test_cli_data_audit_of_empty_locations_is_explicitly_not_ready(
    tmp_path: Path, capsys
) -> None:
    result = cli.main(
        [
            "data",
            "audit",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--database",
            str(tmp_path / "missing.db"),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["operation"] == "read_only_data_audit"
    assert payload["broker_calls"] == 0
    assert payload["holdout_reserved_or_evaluated"] is False
    assert payload["datasets"] == []
    assert payload["ledger"]["exists"] is False

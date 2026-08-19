from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from grande_alpha import cli
from grande_alpha.historical import (
    load_csv_history,
    load_runtime_quote_trace,
    load_runtime_quote_trace_with_row_count,
    runtime_trace_manifest_template,
)
from grande_alpha.models import Quote
from grande_alpha.storage import EXACT_QUOTE_VALIDATOR_VERSION, AuditStore


def _batch(timestamp: datetime, index: int) -> dict[str, Quote]:
    values = {
        "QQQ": 100.0 + index * 0.12,
        "TQQQ": 80.0 + index * 0.20,
        "SQQQ": 40.0 - index * 0.08,
    }
    return {
        symbol: Quote(
            symbol,
            value - 0.01,
            value + 0.01,
            value,
            timestamp,
            timestamp,
            timestamp,
        )
        for symbol, value in values.items()
    }


def _session_trace(start: datetime, minutes: int = 3) -> list[dict[str, Quote]]:
    return [
        _batch(start + timedelta(seconds=5), 0),
        *[
            _batch(start + timedelta(minutes=minute, seconds=2), minute)
            for minute in range(1, minutes + 1)
        ],
    ]


def _write_quote_trace(path: Path, batches: list[dict[str, Quote]]) -> None:
    store = AuditStore(path)
    try:
        for batch in batches:
            observed_at = max(
                quote.latest_book_timestamp or quote.timestamp for quote in batch.values()
            ) + timedelta(milliseconds=100)
            with patch("grande_alpha.storage.utc_now", return_value=observed_at):
                store.record_quote_batch(
                    batch,
                    stream_id="fixture-runtime-stream",
                    validation_profile="exact_execution_quotes",
                    validation_version=EXACT_QUOTE_VALIDATOR_VERSION,
                    max_age_seconds=8.0,
                    max_skew_seconds=5.0,
                )
    finally:
        store.close()


def _start_new_stream_at(path: Path, timestamp: datetime) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """UPDATE quote_batches SET stream_id='fixture-runtime-stream-2'
            WHERE batch_id IN (
                SELECT batch_id FROM quotes
                WHERE symbol='QQQ' AND venue_timestamp>=?
            )""",
            (timestamp.isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()


def _attested_manifest(
    bundle,
    row_count: int,
    *,
    range_start=None,
    range_end=None,
) -> dict:
    manifest = runtime_trace_manifest_template(
        bundle,
        row_count,
        range_start=range_start,
        range_end=range_end,
    )
    manifest.update(
        dataset_id="unit-runtime-trace",
        provider="Fixture venue provider",
        provider_product="Fixture exact quotes",
        license_reference="Fixture research terms",
        license_reviewed_by_user=True,
        research_use_permitted=True,
        automated_strategy_research_permitted=True,
    )
    return manifest


def test_runtime_trace_range_is_inclusive_and_bound_into_manifest_and_hashes(
    tmp_path: Path,
) -> None:
    first = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    second = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    first_batches = _session_trace(first)
    second_batches = _session_trace(second)
    database = tmp_path / "range.db"
    _write_quote_trace(database, [*first_batches, *second_batches])
    _start_new_stream_at(database, second)

    full, full_rows = load_runtime_quote_trace_with_row_count(database)
    selected, selected_rows = load_runtime_quote_trace_with_row_count(
        database,
        start=first.date(),
        end=first.date(),
    )

    assert full.quality is not None and full.quality.sessions == 2
    assert selected.quality is not None and selected.quality.sessions == 1
    assert selected_rows == len(first_batches) * 3
    assert full_rows == (len(first_batches) + len(second_batches)) * 3
    assert selected.dataset_hash != full.dataset_hash
    assert selected.provenance is not None and full.provenance is not None
    assert selected.provenance.source_trace_sha256 != full.provenance.source_trace_sha256

    manifest = _attested_manifest(
        selected,
        selected_rows,
        range_start=first.date(),
        range_end=first.date(),
    )
    attested = load_runtime_quote_trace(
        database,
        manifest=manifest,
        start=first.date(),
        end=first.date(),
    )
    assert attested.runtime_observation_parity_eligible

    with pytest.raises(ValueError, match="manifest does not match"):
        load_runtime_quote_trace(
            database,
            manifest=manifest,
            start=second.date(),
            end=second.date(),
        )


def test_runtime_trace_rejects_malformed_schema_manifest(tmp_path: Path) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "malformed-manifest.db"
    _write_quote_trace(database, _session_trace(start))
    bundle, row_count = load_runtime_quote_trace_with_row_count(database)
    manifest = _attested_manifest(bundle, row_count)
    manifest["observation_schema"] = "generic_ohlcv_v1"

    with pytest.raises(ValueError, match="manifest does not match: observation_schema"):
        load_runtime_quote_trace(database, manifest=manifest)


def test_cli_runtime_trace_audit_and_template_are_query_only(
    tmp_path: Path,
    capsys,
) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "cli-trace.db"
    _write_quote_trace(database, _session_trace(start))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    common = [
        "--database",
        str(database),
        "--bar-seconds",
        "60",
        "--start",
        start.date().isoformat(),
        "--end",
        start.date().isoformat(),
    ]

    result = cli.main(["data", "runtime-trace", "audit", *common, "--json"])
    audit = json.loads(capsys.readouterr().out)
    assert result == 1
    assert audit["operation"] == "read_only_runtime_trace_audit"
    assert audit["broker_calls"] == 0
    assert audit["holdout_reserved_or_evaluated"] is False
    assert audit["database_open_mode"] == "ro"
    assert not audit["input_ready"]

    result = cli.main(["data", "runtime-trace", "manifest-template", *common])
    template = json.loads(capsys.readouterr().out)
    assert result == 0
    assert template["range_start"] == start.date().isoformat()
    assert template["range_end"] == start.date().isoformat()
    assert template["license_reviewed_by_user"] is False
    assert template["research_use_permitted"] is False
    assert template["automated_strategy_research_permitted"] is False
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before

    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM research_holdouts").fetchone()[0] == 0
    finally:
        connection.close()


def test_runtime_trace_manifest_paths_require_explicit_bounds(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "unused.db"
    manifest = tmp_path / "unused.manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cli.build_parser().parse_args(
            [
                "data",
                "runtime-trace",
                "manifest-template",
                "--database",
                str(database),
            ]
        )
    assert exc_info.value.code == 2
    parser_error = capsys.readouterr().err
    assert "--start" in parser_error
    assert "--end" in parser_error

    result = cli.main(
        [
            "data",
            "runtime-trace",
            "audit",
            "--database",
            str(database),
            "--manifest",
            str(manifest),
        ]
    )
    error = capsys.readouterr().err
    assert result == 2
    assert "manifest-backed runtime-trace audit requires --start and --end" in error


def test_runtime_trace_evidence_source_rejects_incomplete_input_before_store(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "incomplete-evidence.db"
    manifest_path = tmp_path / "incomplete.manifest.json"
    _write_quote_trace(database, _session_trace(start))
    bundle, row_count = load_runtime_quote_trace_with_row_count(
        database,
        start=start.date(),
        end=start.date(),
    )
    manifest_path.write_text(
        json.dumps(
            _attested_manifest(
                bundle,
                row_count,
                range_start=start.date(),
                range_end=start.date(),
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "AuditStore",
        lambda: pytest.fail("Evidence store must not open before runtime input readiness passes"),
    )

    result = cli.main(
        [
            "evidence",
            "run",
            "--source",
            "runtime-trace",
            "--database",
            str(database),
            "--manifest",
            str(manifest_path),
            "--bar-seconds",
            "60",
            "--start",
            start.date().isoformat(),
            "--end",
            start.date().isoformat(),
        ]
    )
    error = capsys.readouterr().err

    assert result == 2
    assert "Runtime-trace evidence input is not ready" in error
    assert "no final holdout was reserved or evaluated" in error


def test_generic_csv_remains_generic_ohlcv(tmp_path: Path) -> None:
    csv_path = tmp_path / "generic.csv"
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    rows = ["timestamp,symbol,open,high,low,close,volume,market_hours"]
    for offset in range(0, 150, 5):
        timestamp = start + timedelta(seconds=offset)
        for symbol in ("QQQ", "TQQQ", "SQQQ"):
            rows.append(
                f"{timestamp.isoformat()},{symbol},100,100.1,99.9,100,1000,regular_hours"
            )
    csv_path.write_text("\n".join(rows), encoding="utf-8")

    bundle = load_csv_history(csv_path, "5s")

    assert bundle.provenance is not None
    assert bundle.provenance.source_kind == "import_unverified"
    assert bundle.provenance.observation_schema == "generic_ohlcv_v1"
    assert not bundle.runtime_observation_parity_eligible
    assert all(not frame.has_exact_runtime_observation for frame in bundle.frames)

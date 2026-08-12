from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from grande_alpha.historical import (
    RUNTIME_OBSERVATION_SCHEMA,
    RUNTIME_PROVENANCE_FIELDS,
    load_bundle,
    load_runtime_quote_trace,
    runtime_trace_manifest_template,
    save_bundle,
)
from grande_alpha.models import Quote
from grande_alpha.sandbox import RuntimeObservationReplayEngine, SandboxConfig
from grande_alpha.shadow import LiveShadowEngine
from grande_alpha.storage import EXACT_QUOTE_VALIDATOR_VERSION, AuditStore
from grande_alpha.strategy import BarBuilder, build_strategy


def _write_quote_trace(path: Path, batches: list[dict[str, Quote]]) -> None:
    store = AuditStore(path)
    try:
        for batch in batches:
            observed_at = max(
                quote.latest_book_timestamp or quote.timestamp
                for quote in batch.values()
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


def _batch(timestamp: datetime, index: int) -> dict[str, Quote]:
    qqq = 100.0 + index * 0.12
    tqqq = 80.0 + index * 0.20
    sqqq = 40.0 - index * 0.08
    values = {"QQQ": qqq, "TQQQ": tqqq, "SQQQ": sqqq}
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


def _session_trace(start: datetime, minutes: int = 28) -> list[dict[str, Quote]]:
    batches = [_batch(start + timedelta(seconds=5), 0)]
    for minute in range(1, minutes + 1):
        batches.append(_batch(start + timedelta(minutes=minute, seconds=2), minute))
    return batches


def _attest_manifest(bundle, row_count: int) -> dict:
    manifest = runtime_trace_manifest_template(bundle, row_count)
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


def test_runtime_trace_replays_exact_bar_signal_and_causal_fill_clocks(tmp_path: Path) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    batches = _session_trace(start)
    database = tmp_path / "trace.db"
    _write_quote_trace(database, batches)
    unattested = load_runtime_quote_trace(database)
    bundle = load_runtime_quote_trace(
        database,
        manifest=_attest_manifest(unattested, len(batches) * 3),
    )
    config = SandboxConfig(
        warmup_bars=23,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        decision_stride=1,
        rejection_rate_pct=0,
        fill_fraction_pct=100,
    )

    replay = RuntimeObservationReplayEngine(config).run(bundle)

    direct_builder = BarBuilder("QQQ", 60)
    direct_strategy = build_strategy(config.strategy_config())
    direct_shadow = LiveShadowEngine(config, bar_minutes=1.0)
    direct_bars = []
    direct_signals = []
    direct_clocks = []
    for batch in batches:
        bar = direct_builder.update(batch["QQQ"])
        if bar is None:
            continue
        signal = direct_strategy.on_bar(bar)
        causal = max(
            quote.latest_book_timestamp or quote.timestamp for quote in batch.values()
        )
        direct_shadow.on_causal_quote(causal, signal, batch)
        direct_bars.append(bar)
        direct_signals.append(signal)
        direct_clocks.append(causal)

    assert bundle.runtime_observation_parity_eligible
    assert bundle.provenance is not None
    assert bundle.provenance.observation_schema == RUNTIME_OBSERVATION_SCHEMA
    assert replay.bars == tuple(direct_bars)
    assert replay.signals == tuple(direct_signals)
    assert replay.causal_timestamps == tuple(direct_clocks)
    assert replay.fills == tuple(direct_shadow.state.fills)
    assert replay.fills
    assert replay.fills[0].timestamp in replay.causal_timestamps
    assert replay.fills[0].timestamp > replay.signals[22].timestamp
    assert replay.fills[0].price == pytest.approx(
        bundle.frames[22].tqqq_quote.ask * (1.0 + config.slippage_bps / 10_000)
    )
    assert all(frame.tqqq.volume == frame.sqqq.volume == 0 for frame in bundle.frames)
    cache = tmp_path / "runtime-bundle.json"
    save_bundle(bundle, cache)
    restored = load_bundle(cache)
    assert restored.dataset_hash == bundle.dataset_hash
    assert restored.runtime_observation_parity_eligible
    assert restored.frames == bundle.frames


def test_runtime_trace_filters_off_session_and_resets_bar_builder_by_session(
    tmp_path: Path,
) -> None:
    first_open = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    second_open = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    batches = [
        _batch(first_open - timedelta(minutes=30), 0),
        _batch(first_open + timedelta(seconds=5), 1),
        _batch(first_open + timedelta(minutes=1, seconds=2), 2),
        _batch(first_open + timedelta(hours=6, minutes=31), 3),
        _batch(second_open + timedelta(seconds=5), 4),
        _batch(second_open + timedelta(minutes=1, seconds=2), 5),
    ]
    database = tmp_path / "sessions.db"
    _write_quote_trace(database, batches)
    _start_new_stream_at(database, second_open)

    bundle = load_runtime_quote_trace(database)
    assert [frame.start for frame in bundle.frames] == [first_open, second_open]
    assert [frame.qqq.samples for frame in bundle.frames] == [1, 1]
    assert bundle.frames[0].causal_timestamp == first_open + timedelta(minutes=1, seconds=2)
    assert bundle.frames[1].causal_timestamp == second_open + timedelta(minutes=1, seconds=2)
    assert not bundle.runtime_observation_parity_eligible  # rights manifest is intentionally absent


def test_runtime_trace_requires_both_book_sides_inside_selected_session(
    tmp_path: Path,
) -> None:
    market_open = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    boundary = _batch(market_open, 0)
    boundary["QQQ"] = replace(
        boundary["QQQ"],
        bid_timestamp=market_open - timedelta(microseconds=1),
        ask_timestamp=market_open,
    )
    database = tmp_path / "two-sided-session-boundary.db"
    _write_quote_trace(
        database,
        [
            boundary,
            _batch(market_open + timedelta(seconds=5), 1),
            _batch(market_open + timedelta(minutes=1, seconds=2), 2),
        ],
    )

    bundle = load_runtime_quote_trace(database)

    assert len(bundle.frames) == 1
    assert bundle.frames[0].qqq.samples == 1
    assert bundle.frames[0].start == market_open


def test_generic_ohlcv_bundle_cannot_enter_runtime_observation_replay(tmp_path: Path) -> None:
    from grande_alpha.historical import deterministic_demo

    generic = deterministic_demo(1)

    assert not generic.runtime_observation_parity_eligible
    with pytest.raises(ValueError, match="generic OHLCV is not runtime parity"):
        RuntimeObservationReplayEngine(SandboxConfig()).run(generic)


def test_pre_v12_cached_provenance_digest_remains_loadable(tmp_path: Path) -> None:
    from grande_alpha.historical import deterministic_demo

    bundle = deterministic_demo(1)
    cache = tmp_path / "legacy.json"
    save_bundle(bundle, cache)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    provenance = payload["provenance"]
    for field in RUNTIME_PROVENANCE_FIELDS:
        provenance.pop(field)
    legacy_fields = {
        key: value
        for key, value in provenance.items()
        if key not in {"digest", "evidence_eligible", "runtime_observation_eligible"}
    }
    provenance["digest"] = hashlib.sha256(
        json.dumps(legacy_fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    cache.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_bundle(cache)

    assert restored.dataset_hash == bundle.dataset_hash
    assert not restored.runtime_observation_parity_eligible


def test_runtime_trace_rejects_interrupted_atomic_batch(tmp_path: Path) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "interrupted.db"
    _write_quote_trace(database, _session_trace(start, minutes=2))
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "DELETE FROM quotes WHERE id=(SELECT MAX(id) FROM quotes)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="interrupted or incomplete"):
        load_runtime_quote_trace(database)


def test_runtime_trace_rejects_interleaved_children_even_when_each_batch_has_three(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "interleaved.db"
    _write_quote_trace(database, _session_trace(start, minutes=2))
    connection = sqlite3.connect(database)
    try:
        batches = [
            row[0]
            for row in connection.execute(
                "SELECT batch_id FROM quote_batches ORDER BY observed_at,batch_id LIMIT 2"
            )
        ]
        first_id = connection.execute(
            "SELECT id FROM quotes WHERE batch_id=? AND symbol='TQQQ'",
            (batches[0],),
        ).fetchone()[0]
        second_id = connection.execute(
            "SELECT id FROM quotes WHERE batch_id=? AND symbol='TQQQ'",
            (batches[1],),
        ).fetchone()[0]
        connection.execute("UPDATE quotes SET batch_id='swap' WHERE id=?", (first_id,))
        connection.execute("UPDATE quotes SET batch_id=? WHERE id=?", (batches[0], second_id))
        connection.execute("UPDATE quotes SET batch_id=? WHERE id=?", (batches[1], first_id))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="atomic batch timestamp"):
        load_runtime_quote_trace(database)


def test_runtime_trace_excludes_legacy_unbound_rows_from_exact_content(tmp_path: Path) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "legacy-unbound.db"
    bound = _session_trace(start, minutes=2)
    _write_quote_trace(database, bound)
    before = load_runtime_quote_trace(database)
    store = AuditStore(database)
    try:
        legacy = _batch(start + timedelta(seconds=20), 99)
        for quote in legacy.values():
            store.record_quote(quote)
    finally:
        store.close()

    after = load_runtime_quote_trace(database)

    assert after.frames == before.frames
    assert after.dataset_hash == before.dataset_hash
    assert after.provenance is not None
    assert after.provenance.excluded_legacy_quote_rows == 3
    assert after.provenance.source_trace_sha256 == before.provenance.source_trace_sha256


def test_runtime_trace_ignores_stale_qqq_batch_exactly_like_live_controller(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    fresh_zero = _batch(start + timedelta(seconds=5), 0)
    stale = _batch(start + timedelta(seconds=4), 99)
    fresh_one = _batch(start + timedelta(minutes=1, seconds=2), 1)
    database = tmp_path / "stale.db"
    _write_quote_trace(database, [fresh_zero, stale, fresh_one])

    bundle = load_runtime_quote_trace(database)

    assert len(bundle.frames) == 1
    assert bundle.frames[0].qqq.open == fresh_zero["QQQ"].mid
    assert bundle.frames[0].qqq.close == fresh_zero["QQQ"].mid
    assert bundle.frames[0].qqq.samples == 1
    assert bundle.frames[0].causal_timestamp == fresh_one["QQQ"].timestamp


def test_runtime_replay_resets_strategy_on_each_session_boundary(tmp_path: Path) -> None:
    first = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    second = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    batches = [*_session_trace(first, minutes=25), *_session_trace(second, minutes=25)]
    database = tmp_path / "two-sessions.db"
    _write_quote_trace(database, batches)
    _start_new_stream_at(database, second)
    bundle = load_runtime_quote_trace(database)
    first_stream = bundle.frames[0].stream_id
    bundle = replace(
        bundle,
        frames=[replace(frame, stream_id=first_stream) for frame in bundle.frames],
    )

    replay = RuntimeObservationReplayEngine(
        SandboxConfig(warmup_bars=23, no_trade_open_minutes=0, no_trade_close_minutes=0)
    ).run(bundle)
    second_index = next(
        index for index, bar in enumerate(replay.bars) if bar.start.date() == second.date()
    )

    assert "Warm-up 1/23" in replay.signals[0].reason
    assert "Warm-up 1/23" in replay.signals[second_index].reason
    assert replay.signals[second_index].regime.value == "flat"
    assert all(
        signal.regime.value == "flat"
        for signal in replay.signals[second_index : second_index + 22]
    )


def test_runtime_trace_rejects_one_stream_spanning_sessions(tmp_path: Path) -> None:
    first = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    second = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "unreset-stream.db"
    _write_quote_trace(
        database,
        [*_session_trace(first, minutes=1), *_session_trace(second, minutes=1)],
    )

    with pytest.raises(ValueError, match="spans multiple sessions"):
        load_runtime_quote_trace(database)


def test_stale_passive_atomic_batches_cannot_become_exact_with_rights_manifest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "passive-stale.db"
    store = AuditStore(database)
    stale_start = datetime.now(UTC) - timedelta(hours=20)
    try:
        for index in range(2):
            store.record_quote_batch(
                _batch(stale_start + timedelta(minutes=index), index),
                stream_id="passive-connected-stream",
            )
    finally:
        store.close()

    with pytest.raises(ValueError, match="at least two synchronized quote batches"):
        load_runtime_quote_trace(
            database,
            manifest={
                "license_reviewed_by_user": True,
                "research_use_permitted": True,
                "automated_strategy_research_permitted": True,
            },
        )


def test_pre_book_clock_validator_batches_are_stale_for_runtime_eligibility(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "stale-validator-v1.db"
    _write_quote_trace(database, _session_trace(start, minutes=2))
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE quote_batches SET schema_version=1,validation_version=1"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="at least two synchronized quote batches"):
        load_runtime_quote_trace(database)


def test_importer_recomputes_bound_exact_book_age_after_database_tampering(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    database = tmp_path / "tampered-age.db"
    _write_quote_trace(database, _session_trace(start, minutes=2))
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE quotes SET bid_timestamp=?",
            ((start - timedelta(hours=20)).isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="bound validator age envelope"):
        load_runtime_quote_trace(database)

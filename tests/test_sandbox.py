from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grande_alpha.historical import (
    HistoricalBundle,
    ReplayFrame,
    align_bars,
    assess_quality,
    deterministic_demo,
    load_bundle,
    parse_yahoo_chart,
    save_bundle,
)
from grande_alpha.models import Bar
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine
from grande_alpha.storage import AuditStore


def _bar(symbol: str, start: datetime, price: float) -> Bar:
    return Bar(symbol, start, price, price, price, price, 1)


def test_yahoo_parser_skips_incomplete_candles() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1_700_000_000, 1_700_000_060],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, None],
                                "high": [101.0, None],
                                "low": [99.0, None],
                                "close": [100.5, None],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }

    bars = parse_yahoo_chart(payload, "QQQ")

    assert len(bars) == 1
    assert bars[0].symbol == "QQQ"
    assert bars[0].close == 100.5


def test_alignment_uses_only_common_timestamps() -> None:
    start = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    qqq = [_bar("QQQ", start, 100), _bar("QQQ", start + timedelta(minutes=1), 101)]
    tqqq = [_bar("TQQQ", start, 50), _bar("TQQQ", start + timedelta(minutes=1), 51)]
    sqqq = [_bar("SQQQ", start + timedelta(minutes=1), 40)]

    frames = align_bars(qqq, tqqq, sqqq)

    assert len(frames) == 1
    assert frames[0].start == start + timedelta(minutes=1)


def test_replay_uses_sandbox_aliases_and_next_bar_fills() -> None:
    start = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    frames = []
    for index in range(30):
        timestamp = start + timedelta(minutes=index)
        frames.append(
            ReplayFrame(
                timestamp,
                _bar("QQQ", timestamp, 100.0 + index),
                _bar("TQQQ", timestamp, 50.0 + index),
                _bar("SQQQ", timestamp, 50.0 - index * 0.2),
            )
        )
    bundle = HistoricalBundle("unit test", start, frames)
    config = SandboxConfig(
        initial_cash=100.0,
        order_notional=50.0,
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
    )

    result = SandboxReplayEngine(config).run(bundle)

    assert result.round_trips == 1
    assert result.net_pnl > 0
    assert {fill.symbol for fill in result.fills} == {"TQQQS"}
    assert result.fills[0].side == "buy"
    assert result.fills[0].timestamp == frames[5].start
    assert result.fills[-1].side == "sell"


def test_demo_is_repeatable_and_config_rejects_lookahead_prone_values() -> None:
    first = deterministic_demo(2, seed=9)
    second = deterministic_demo(2, seed=9)
    assert [frame.qqq.close for frame in first.frames] == [frame.qqq.close for frame in second.frames]

    with pytest.raises(ValueError, match="Fast EMA"):
        SandboxConfig(fast_ema=21, slow_ema=8).validate()


def test_historical_cache_round_trip_verifies_content_hash(tmp_path: Path) -> None:
    bundle = deterministic_demo(2, seed=14)
    path = tmp_path / "bundle.json"
    save_bundle(bundle, path)

    restored = load_bundle(path)

    assert restored.dataset_hash == bundle.dataset_hash
    assert restored.frames == bundle.frames
    assert restored.quality and restored.quality.clean


def test_data_quality_counts_intraday_gaps() -> None:
    start = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    frames = [
        ReplayFrame(start, _bar("QQQ", start, 1), _bar("TQQQ", start, 1), _bar("SQQQ", start, 1)),
        ReplayFrame(
            start + timedelta(minutes=3),
            _bar("QQQ", start + timedelta(minutes=3), 1),
            _bar("TQQQ", start + timedelta(minutes=3), 1),
            _bar("SQQQ", start + timedelta(minutes=3), 1),
        ),
    ]
    quality = assess_quality(frames, "1m")
    assert quality.missing_intervals == 2


def test_sandbox_run_persists_separately_from_live_orders(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    result = SandboxReplayEngine(SandboxConfig()).run(deterministic_demo(2))
    store.record_sandbox_run(
        result.run_id,
        result.source,
        result.start.isoformat(),
        result.end.isoformat(),
        asdict(SandboxConfig()),
        result.metrics(),
        [fill.as_dict() for fill in result.fills],
        [event.as_dict() for event in result.execution_events],
    )

    runs = store.recent_sandbox_runs()
    assert runs[0]["run_id"] == result.run_id
    assert "sandbox" in store.recent_receipts()[0]["category"]
    with store._lock:
        event_count = store._connection.execute(
            "SELECT COUNT(*) AS count FROM sandbox_execution_events WHERE run_id=?", (result.run_id,)
        ).fetchone()["count"]
    assert event_count == len(result.execution_events)
    store.close()


def test_sandbox_engine_has_no_broker_submission_dependency() -> None:
    path = Path(__file__).resolve().parents[1] / "src" / "grande_alpha" / "sandbox.py"
    source = path.read_text(encoding="utf-8")
    assert "place_order" not in source
    assert "review_order" not in source
    assert "RobinhoodMCPBroker" not in source

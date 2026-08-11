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
    full_history_calendar_days,
    load_bundle,
    load_csv_history,
    parse_yahoo_chart,
    save_bundle,
)
from grande_alpha.models import Bar
from grande_alpha.policy import session_key
from grande_alpha.sandbox import EquityPoint, SandboxConfig, SandboxReplayEngine
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


def test_full_history_window_covers_shared_2010_inception() -> None:
    reference = datetime(2026, 8, 9, tzinfo=UTC)

    assert full_history_calendar_days(reference) > 6_000


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
    assert result.fills[-1].unsettled_cash_after > 0
    assert result.final_unsettled_cash > 0
    assert result.final_equity == pytest.approx(
        result.equity_curve[-1].cash + result.equity_curve[-1].unsettled_cash
    )
    assert len(result.daily_returns) == 1


def test_opening_fill_does_not_use_that_bars_future_range_or_volume() -> None:
    start = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    normal = []
    shocked = []
    for index in range(30):
        timestamp = start + timedelta(minutes=index)
        qqq = _bar("QQQ", timestamp, 100 + index)
        ordinary_tqqq = Bar("TQQQ", timestamp, 50, 50, 50, 50, 1, volume=100)
        future_tqqq = ordinary_tqqq
        if index == 5:
            future_tqqq = Bar("TQQQ", timestamp, 50, 100, 1, 80, 1, volume=1)
        sqqq = _bar("SQQQ", timestamp, 40)
        normal.append(ReplayFrame(timestamp, qqq, ordinary_tqqq, sqqq))
        shocked.append(ReplayFrame(timestamp, qqq, future_tqqq, sqqq))
    config = SandboxConfig(
        initial_cash=100,
        order_notional=50,
        warmup_bars=5,
        fast_ema=1,
        slow_ema=3,
        trend_threshold_bps=0.1,
        momentum_bars=1,
        hard_stop_pct=0.5,
        take_profit_pct=0.5,
        max_hold_minutes=100,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        spread_volatility_multiplier=1.0,
        max_volume_participation_pct=1.0,
    )

    ordinary_fill = SandboxReplayEngine(config).run(
        HistoricalBundle("normal", start, normal)
    ).fills[0]
    shocked_fill = SandboxReplayEngine(config).run(
        HistoricalBundle("shocked", start, shocked)
    ).fills[0]

    assert ordinary_fill.timestamp == shocked_fill.timestamp == normal[5].start
    assert shocked_fill.price == pytest.approx(ordinary_fill.price)
    assert shocked_fill.quantity == pytest.approx(ordinary_fill.quantity)


def test_all_day_statistics_group_by_trading_session_not_calendar_midnight() -> None:
    curve = [
        EquityPoint(datetime(2026, 8, 4, 1, 0, tzinfo=UTC), 100.0, 100.0, None),
        EquityPoint(datetime(2026, 8, 4, 14, 0, tzinfo=UTC), 110.0, 110.0, None),
    ]

    assert {session_key(point.timestamp, "all_day_hours") for point in curve} == {"2026-08-04"}
    assert SandboxReplayEngine._daily_pnl(curve, "all_day_hours") == {"2026-08-04": 10.0}
    assert SandboxReplayEngine._daily_returns(curve, 100.0, "all_day_hours") == pytest.approx([0.1])


def test_negative_unsettled_sale_debit_posts_on_next_session() -> None:
    bundle = deterministic_demo(4, seed=9)
    config = SandboxConfig(
        initial_cash=100,
        order_notional=10,
        commission_per_order=30,
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
    first_sell = next(fill for fill in result.fills if fill.side == "sell")
    assert first_sell.unsettled_cash_after < 0
    first_day = session_key(first_sell.timestamp, config.market_hours)
    next_session_point = next(
        point
        for point in result.equity_curve
        if session_key(point.timestamp, config.market_hours) != first_day
    )
    assert next_session_point.unsettled_cash == 0.0
    assert next_session_point.cash == pytest.approx(first_sell.cash_after + first_sell.unsettled_cash_after)


def test_force_flat_closes_each_session_without_overnight_exposure() -> None:
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

    result = SandboxReplayEngine(config).run(bundle)
    open_fill = None
    for fill in result.fills:
        if fill.side == "buy":
            open_fill = fill
        elif fill.side == "sell" and open_fill is not None:
            assert fill.timestamp.date() == open_fill.timestamp.date()
            open_fill = None

    assert open_fill is None
    assert any(event.reason == "Session-end forced virtual flatten" for event in result.execution_events)


def test_demo_is_repeatable_and_config_rejects_lookahead_prone_values() -> None:
    first = deterministic_demo(2, seed=9)
    second = deterministic_demo(2, seed=9)
    assert [frame.qqq.close for frame in first.frames] == [frame.qqq.close for frame in second.frames]
    assert first.quality and first.quality.session_coverage_pct == 100

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


def test_all_day_csv_coverage_must_be_explicit_and_crosses_the_trading_date(tmp_path: Path) -> None:
    path = tmp_path / "all-day.csv"
    rows = ["timestamp,symbol,open,high,low,close,volume,market_hours"]
    evening = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)  # Sunday 8 PM ET
    overnight = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)  # Monday 6 AM ET
    timestamps = [evening + timedelta(minutes=index) for index in range(15)] + [
        overnight + timedelta(minutes=index) for index in range(15)
    ]
    for timestamp in timestamps:
        for symbol in ("QQQ", "TQQQ", "SQQQ"):
            rows.append(f"{timestamp.isoformat()},{symbol},100,101,99,100,1000,all_day_hours")
    path.write_text("\n".join(rows), encoding="utf-8")

    bundle = load_csv_history(path)

    assert bundle.market_hours == "all_day_hours"
    assert bundle.quality and bundle.quality.sessions == 1
    assert bundle.quality.missing_intervals > 0
    assert bundle.quality.session_coverage_pct == 0


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

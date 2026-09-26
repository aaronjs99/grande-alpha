"""Historical-data transformations shared by import, replay, and quality reporting."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.domain.market_models import Bar
from grande_alpha.domain.policy import session_bounds, session_key, trading_date

from .historical_models import (
    INTERVAL_SECONDS,
    ChronologicalHoldoutSplit,
    DataQuality,
    HistoricalBundle,
    ReplayFrame,
)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
INTERVAL_LIMITS = {"1m": 7, "5m": 60, "15m": 60, "60m": 730, "1d": 10_000}
SHARED_LEVERAGED_HISTORY_START = datetime(2010, 2, 9, tzinfo=UTC)


def _bundle_for_sessions(bundle: HistoricalBundle, sessions: list[str]) -> HistoricalBundle:
    allowed = set(sessions)
    frames = [frame for frame in bundle.frames if session_key(frame.start, bundle.market_hours) in allowed]
    if not frames:
        raise ValueError("Historical subset cannot be empty")
    quality = assess_quality(frames, bundle.interval, bundle.market_hours)
    return HistoricalBundle(
        source=bundle.source,
        downloaded_at=bundle.downloaded_at,
        frames=frames,
        interval=bundle.interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours=bundle.market_hours,
        provenance=bundle.provenance,
    )


def split_final_holdout(
    bundle: HistoricalBundle,
    holdout_sessions: int = 20,
    purge_sessions: int = 1,
) -> ChronologicalHoldoutSplit:
    """Freeze a later session block while keeping an embargo after development data."""

    if holdout_sessions < 1 or purge_sessions < 0:
        raise ValueError("Holdout sessions must be positive and purge sessions nonnegative")
    names = sorted({session_key(frame.start, bundle.market_hours) for frame in bundle.frames})
    required = holdout_sessions + purge_sessions + 1
    if len(names) < required:
        raise ValueError(f"Final holdout needs at least {required} sessions; dataset has {len(names)}")
    holdout_start = len(names) - holdout_sessions
    purge_start = holdout_start - purge_sessions
    development_names = names[:purge_start]
    purged_names = tuple(names[purge_start:holdout_start])
    holdout_names = names[holdout_start:]
    return ChronologicalHoldoutSplit(
        development=_bundle_for_sessions(bundle, development_names),
        holdout=_bundle_for_sessions(bundle, holdout_names),
        purged_sessions=purged_names,
    )


def parse_yahoo_chart(payload: dict[str, Any], expected_symbol: str) -> list[Bar]:
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"Historical data error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"No historical data returned for {expected_symbol}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quotes = indicators.get("quote") or []
    if not quotes:
        raise ValueError(f"No candles returned for {expected_symbol}")
    quote = quotes[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    bars: list[Bar] = []
    for index, timestamp in enumerate(timestamps):
        try:
            values = tuple(float(series[index]) for series in (opens, highs, lows, closes))
        except (IndexError, TypeError, ValueError):
            continue
        if any(not math.isfinite(value) or value <= 0 for value in values):
            continue
        bars.append(
            Bar(
                symbol=expected_symbol,
                start=datetime.fromtimestamp(int(timestamp), tz=UTC),
                open=values[0],
                high=values[1],
                low=values[2],
                close=values[3],
                samples=1,
                volume=(
                    float(volumes[index]) if index < len(volumes) and volumes[index] is not None else 0.0
                ),
            )
        )
    if not bars:
        raise ValueError(f"Historical candles for {expected_symbol} were all incomplete")
    return bars


def align_bars(qqq: list[Bar], tqqq: list[Bar], sqqq: list[Bar]) -> list[ReplayFrame]:
    maps = [{bar.start: bar for bar in series} for series in (qqq, tqqq, sqqq)]
    timestamps = sorted(set(maps[0]).intersection(maps[1], maps[2]))
    return [ReplayFrame(start, maps[0][start], maps[1][start], maps[2][start]) for start in timestamps]


def dataset_hash(frames: list[ReplayFrame]) -> str:
    digest = hashlib.sha256()
    for frame in frames:
        digest.update(frame.start.isoformat().encode())
        for bar in (frame.qqq, frame.tqqq, frame.sqqq):
            digest.update(
                f"{bar.symbol}|{bar.open:.8f}|{bar.high:.8f}|{bar.low:.8f}|"
                f"{bar.close:.8f}|{bar.volume:.4f}".encode()
            )
        if frame.has_exact_runtime_observation:
            assert frame.causal_timestamp is not None
            digest.update(f"|causal|{frame.causal_timestamp.isoformat()}".encode())
            digest.update(f"|stream|{frame.stream_id}".encode())
            for quote in frame.runtime_quotes().values():
                digest.update(
                    f"|{quote.symbol}|{quote.bid:.8f}|{quote.ask:.8f}|{quote.last:.8f}|"
                    f"{quote.timestamp.isoformat()}|{quote.bid_timestamp.isoformat()}|"
                    f"{quote.ask_timestamp.isoformat()}".encode()
                )
    return digest.hexdigest()


def assess_quality(
    frames: list[ReplayFrame], interval: str, market_hours: str = "regular_hours"
) -> DataQuality:
    seconds = (
        int(interval[:-1])
        if interval.endswith("s") and interval[:-1].isdigit()
        else INTERVAL_SECONDS.get(interval, 60)
    )
    grouped: dict[str, list[ReplayFrame]] = {}
    valid_frames: list[ReplayFrame] = []
    invalid_session_bars = 0
    for frame in frames:
        trade_date = trading_date(frame.start, market_hours)
        if regular_session_times(trade_date) is None:
            invalid_session_bars += 1
            continue
        valid_frames.append(frame)
        grouped.setdefault(session_key(frame.start, market_hours), []).append(frame)
    sessions = set(grouped)
    expected_session_dates: set[str] = set()
    if sessions:
        first_date = datetime.fromisoformat(min(sessions)).date()
        last_date = datetime.fromisoformat(max(sessions)).date()
        cursor = first_date
        while cursor <= last_date:
            if regular_session_times(cursor) is not None:
                expected_session_dates.add(cursor.isoformat())
            cursor += timedelta(days=1)
    missing_sessions = len(expected_session_dates - sessions)
    missing = 0
    duplicates = 0
    previous: ReplayFrame | None = None
    seen: set[datetime] = set()
    for frame in valid_frames:
        if frame.start in seen:
            duplicates += 1
        seen.add(frame.start)
        if previous is not None:
            previous_day = session_key(previous.start, market_hours)
            current_day = session_key(frame.start, market_hours)
            gap = (frame.start - previous.start).total_seconds()
            if previous_day == current_day and gap > seconds * 1.5:
                missing += max(0, round(gap / seconds) - 1)
        previous = frame
    zero_volume = sum(
        1 for frame in frames if frame.qqq.volume <= 0 or frame.tqqq.volume <= 0 or frame.sqqq.volume <= 0
    )
    if interval == "1d":
        complete_sessions = len(grouped)
    else:
        complete_sessions = 0
        tolerance = seconds * 1.5
        for session_frames in grouped.values():
            first, last = session_frames[0].start, session_frames[-1].start
            opened, closed = session_bounds(first, market_hours)
            starts_near_open = first.timestamp() <= opened.timestamp() + tolerance
            ends_near_close = last.timestamp() >= closed.timestamp() - seconds - tolerance
            complete_sessions += int(starts_near_open and ends_near_close)
    coverage_pct = complete_sessions / len(grouped) * 100 if grouped else 0.0
    return DataQuality(
        aligned_bars=len(frames),
        sessions=len(sessions),
        missing_intervals=missing,
        zero_volume_bars=zero_volume,
        duplicate_timestamps=duplicates,
        invalid_session_bars=invalid_session_bars,
        interval=interval,
        dataset_hash=dataset_hash(frames),
        complete_sessions=complete_sessions,
        session_coverage_pct=coverage_pct,
        expected_sessions=len(expected_session_dates),
        missing_sessions=missing_sessions,
    )

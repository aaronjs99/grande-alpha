from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from grande_alpha.config import data_dir
from grande_alpha.models import Bar, utc_now
from grande_alpha.policy import session_bounds, session_key

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
INTERVAL_LIMITS = {"1m": 7, "5m": 60, "15m": 60, "60m": 730, "1d": 10_000}
INTERVAL_SECONDS = {"5s": 5, "1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": 86400}
SHARED_LEVERAGED_HISTORY_START = datetime(2010, 2, 9, tzinfo=UTC)


@dataclass(frozen=True)
class DataQuality:
    aligned_bars: int
    sessions: int
    missing_intervals: int
    zero_volume_bars: int
    duplicate_timestamps: int
    interval: str
    dataset_hash: str
    complete_sessions: int = 0
    session_coverage_pct: float = 0.0

    @property
    def clean(self) -> bool:
        return self.aligned_bars > 0 and self.duplicate_timestamps == 0


@dataclass(frozen=True)
class ReplayFrame:
    start: datetime
    qqq: Bar
    tqqq: Bar
    sqqq: Bar

    def bar_for_alias(self, alias: str) -> Bar:
        if alias == "TQQQS":
            return self.tqqq
        if alias == "SQQQS":
            return self.sqqq
        raise ValueError(f"Unknown sandbox alias: {alias}")


@dataclass(frozen=True)
class HistoricalBundle:
    source: str
    downloaded_at: datetime
    frames: list[ReplayFrame]
    interval: str = "1m"
    dataset_hash: str = ""
    quality: DataQuality | None = None
    market_hours: str = "regular_hours"

    @property
    def start(self) -> datetime:
        return self.frames[0].start

    @property
    def end(self) -> datetime:
        return self.frames[-1].start


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
    for frame in frames:
        grouped.setdefault(session_key(frame.start, market_hours), []).append(frame)
    sessions = set(grouped)
    missing = 0
    duplicates = 0
    previous: ReplayFrame | None = None
    seen: set[datetime] = set()
    for frame in frames:
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
        interval=interval,
        dataset_hash=dataset_hash(frames),
        complete_sessions=complete_sessions,
        session_coverage_pct=coverage_pct,
    )


def _bar_dict(bar: Bar) -> dict[str, Any]:
    return {
        "symbol": bar.symbol,
        "start": bar.start.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "samples": bar.samples,
        "volume": bar.volume,
    }


def _bar_from_dict(raw: dict[str, Any]) -> Bar:
    return Bar(
        symbol=str(raw["symbol"]),
        start=datetime.fromisoformat(str(raw["start"])).astimezone(UTC),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        samples=int(raw.get("samples", 1)),
        volume=float(raw.get("volume", 0.0)),
    )


def save_bundle(bundle: HistoricalBundle, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": bundle.source,
        "downloaded_at": bundle.downloaded_at.isoformat(),
        "interval": bundle.interval,
        "dataset_hash": bundle.dataset_hash,
        "market_hours": bundle.market_hours,
        "frames": [
            {
                "start": frame.start.isoformat(),
                "qqq": _bar_dict(frame.qqq),
                "tqqq": _bar_dict(frame.tqqq),
                "sqqq": _bar_dict(frame.sqqq),
            }
            for frame in bundle.frames
        ],
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def load_bundle(path: Path) -> HistoricalBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    frames = [
        ReplayFrame(
            datetime.fromisoformat(row["start"]).astimezone(UTC),
            _bar_from_dict(row["qqq"]),
            _bar_from_dict(row["tqqq"]),
            _bar_from_dict(row["sqqq"]),
        )
        for row in payload["frames"]
    ]
    interval = str(payload.get("interval", "1m"))
    market_hours = str(payload.get("market_hours", "regular_hours"))
    quality = assess_quality(frames, interval, market_hours)
    expected_hash = str(payload.get("dataset_hash") or quality.dataset_hash)
    if expected_hash != quality.dataset_hash:
        raise ValueError("Cached historical dataset hash does not match its contents")
    return HistoricalBundle(
        source=str(payload["source"]),
        downloaded_at=datetime.fromisoformat(payload["downloaded_at"]).astimezone(UTC),
        frames=frames,
        interval=interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours=market_hours,
    )


class HistoricalDataProvider:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def _fetch_symbol(
        self,
        client: httpx.AsyncClient,
        symbol: str,
        days: int,
        interval: str,
        include_pre_post: bool,
    ) -> list[Bar]:
        period2 = int(utc_now().timestamp()) + 60
        period1 = period2 - days * 24 * 60 * 60
        response = await client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={
                "period1": period1,
                "period2": period2,
                "interval": interval,
                "includePrePost": "true" if include_pre_post else "false",
                "events": "div,splits",
            },
        )
        response.raise_for_status()
        return parse_yahoo_chart(response.json(), symbol)

    async def fetch(
        self,
        days: int = 7,
        interval: str = "1m",
        use_cache: bool = True,
        market_hours: str = "regular_hours",
    ) -> HistoricalBundle:
        maximum = INTERVAL_LIMITS.get(interval)
        if maximum is None:
            raise ValueError(f"Unsupported historical interval: {interval}")
        if not 1 <= days <= maximum:
            raise ValueError(f"{interval} historical lookback must be between 1 and {maximum} days")
        if market_hours not in {"regular_hours", "extended_hours", "all_day_hours"}:
            raise ValueError(f"Unsupported trading session: {market_hours}")
        if market_hours == "all_day_hours":
            raise ValueError(
                "The community Yahoo adapter does not provide complete Robinhood overnight coverage; "
                "import a lawfully sourced 24-hour CSV for all-day evidence"
            )
        cache_path = (
            data_dir()
            / "sandbox_cache"
            / f"yahoo_{market_hours}_{interval}_{days}d_{utc_now().date().isoformat()}.json"
        )
        if use_cache and cache_path.exists():
            return load_bundle(cache_path)
        headers = {"User-Agent": "GRANDE-Alpha/0.7 research client"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers) as client:
            qqq, tqqq, sqqq = await asyncio.gather(
                self._fetch_symbol(client, "QQQ", days, interval, market_hours != "regular_hours"),
                self._fetch_symbol(client, "TQQQ", days, interval, market_hours != "regular_hours"),
                self._fetch_symbol(client, "SQQQ", days, interval, market_hours != "regular_hours"),
            )
        frames = align_bars(qqq, tqqq, sqqq)
        if len(frames) < 30:
            raise ValueError(f"Only {len(frames)} aligned {interval} candles were available")
        quality = assess_quality(frames, interval, market_hours)
        bundle = HistoricalBundle(
            source=f"Yahoo Finance chart data ({interval}) — unsupported research source",
            downloaded_at=utc_now(),
            frames=frames,
            interval=interval,
            dataset_hash=quality.dataset_hash,
            quality=quality,
            market_hours=market_hours,
        )
        if use_cache:
            save_bundle(bundle, cache_path)
        return bundle

    async def fetch_full_daily(self, use_cache: bool = True) -> HistoricalBundle:
        days = full_history_calendar_days()
        return await self.fetch(days, "1d", use_cache)


def full_history_calendar_days(reference: datetime | None = None) -> int:
    end = reference or utc_now()
    return max(1, math.ceil((end - SHARED_LEVERAGED_HISTORY_START).total_seconds() / 86_400) + 2)


def load_csv_history(path: Path, interval: str = "1m") -> HistoricalBundle:
    """Load long-history rows: timestamp,symbol,open,high,low,close,volume."""
    required = {"timestamp", "symbol", "open", "high", "low", "close"}
    series: dict[str, list[Bar]] = {"QQQ": [], "TQQQ": [], "SQQQ": []}
    declared_coverage: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required <= {name.lower() for name in reader.fieldnames}:
            raise ValueError("CSV needs timestamp,symbol,open,high,low,close and optional volume columns")
        for row in reader:
            normalized = {str(key).lower(): value for key, value in row.items()}
            declared = str(normalized.get("market_hours") or "").strip()
            if declared:
                declared_coverage.add(declared)
            symbol = str(normalized.get("symbol", "")).upper()
            if symbol not in series:
                continue
            raw_time = str(normalized["timestamp"])
            timestamp = (
                datetime.fromtimestamp(float(raw_time), tz=UTC)
                if raw_time.replace(".", "", 1).isdigit()
                else datetime.fromisoformat(raw_time.replace("Z", "+00:00")).astimezone(UTC)
            )
            values = [float(normalized[name]) for name in ("open", "high", "low", "close")]
            if any(not math.isfinite(value) or value <= 0 for value in values):
                continue
            series[symbol].append(
                Bar(
                    symbol,
                    timestamp,
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    1,
                    float(normalized.get("volume") or 0.0),
                )
            )
    frames = align_bars(series["QQQ"], series["TQQQ"], series["SQQQ"])
    if len(frames) < 30:
        raise ValueError(f"CSV produced only {len(frames)} aligned candles")
    eastern = ZoneInfo("America/New_York")
    local_times = [frame.start.astimezone(eastern).time() for frame in frames]
    has_extended = any(value < time(9, 30) or value >= time(16, 0) for value in local_times)
    if len(declared_coverage) > 1:
        raise ValueError("CSV market_hours must declare one consistent coverage value")
    coverage = next(iter(declared_coverage), "")
    if coverage and coverage not in {"regular_hours", "extended_hours", "all_day_hours"}:
        raise ValueError("CSV market_hours must be regular_hours, extended_hours, or all_day_hours")
    if not coverage:
        coverage = "extended_hours" if has_extended else "regular_hours"
    if coverage == "all_day_hours":
        has_evening = any(value >= time(20, 0) for value in local_times)
        has_early = any(value < time(7, 0) for value in local_times)
        if not has_evening or not has_early:
            raise ValueError(
                "CSV declaring all_day_hours must contain both evening and overnight timestamps"
            )
    quality = assess_quality(frames, interval, coverage)
    return HistoricalBundle(
        source=f"Imported CSV: {path.name}",
        downloaded_at=utc_now(),
        frames=frames,
        interval=interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours=coverage,
    )


def _demo_market_days(days: int, end: datetime) -> list[datetime]:
    eastern = ZoneInfo("America/New_York")
    local_end = end.astimezone(eastern).date()
    start = local_end - timedelta(days=max(1, days) - 1)
    result: list[datetime] = []
    cursor = start
    while cursor <= local_end:
        if cursor.weekday() < 5:
            result.append(datetime.combine(cursor, time(9, 30), eastern).astimezone(UTC))
        cursor += timedelta(days=1)
    if not result:
        cursor = local_end
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        result.append(datetime.combine(cursor, time(9, 30), eastern).astimezone(UTC))
    return result


def deterministic_demo(days: int = 7, seed: int = 7007) -> HistoricalBundle:
    """Create repeatable offline candles; these are scenarios, never claimed as market history."""
    rng = random.Random(seed)
    qqq_price, tqqq_price, sqqq_price = 600.0, 55.0, 45.0
    frames: list[ReplayFrame] = []
    index = 0
    for market_open in _demo_market_days(days, utc_now()):
        for minute in range(390):
            timestamp = market_open + timedelta(minutes=minute)
            cycle = math.sin(index / 67.0) * 0.00032
            regime = 0.00018 if (index // 260) % 2 == 0 else -0.00016
            qqq_return = regime + cycle + rng.gauss(0.0, 0.00055)
            tqqq_return = 3.0 * qqq_return - 0.000012
            sqqq_return = -3.0 * qqq_return - 0.000012
            qqq_bar = _synthetic_bar("QQQ", timestamp, qqq_price, qqq_return, rng)
            tqqq_bar = _synthetic_bar("TQQQ", timestamp, tqqq_price, tqqq_return, rng)
            sqqq_bar = _synthetic_bar("SQQQ", timestamp, sqqq_price, sqqq_return, rng)
            frames.append(ReplayFrame(timestamp, qqq_bar, tqqq_bar, sqqq_bar))
            qqq_price, tqqq_price, sqqq_price = qqq_bar.close, tqqq_bar.close, sqqq_bar.close
            index += 1
    return HistoricalBundle(
        source=f"Deterministic offline scenario (seed {seed}) — not historical market data",
        downloaded_at=utc_now(),
        frames=frames,
        interval="1m",
        dataset_hash=dataset_hash(frames),
        quality=assess_quality(frames, "1m"),
    )


def _synthetic_bar(symbol: str, start: datetime, opening: float, change: float, rng: random.Random) -> Bar:
    close = max(0.01, opening * (1.0 + change))
    wick = abs(rng.gauss(0.0, 0.00025)) * opening
    return Bar(
        symbol=symbol,
        start=start,
        open=opening,
        high=max(opening, close) + wick,
        low=max(0.01, min(opening, close) - wick),
        close=close,
        samples=1,
        volume=max(1.0, rng.lognormvariate(10.0, 0.45)),
    )

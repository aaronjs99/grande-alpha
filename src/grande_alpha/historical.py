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

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
INTERVAL_LIMITS = {"1m": 7, "5m": 60, "15m": 60, "60m": 730, "1d": 3650}
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": 86400}


@dataclass(frozen=True)
class DataQuality:
    aligned_bars: int
    sessions: int
    missing_intervals: int
    zero_volume_bars: int
    duplicate_timestamps: int
    interval: str
    dataset_hash: str

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
    if (result.get("events") or {}).get("splits"):
        raise ValueError(
            f"{expected_symbol} had a split in the replay window; choose a shorter window to avoid false P/L"
        )
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
                    float(volumes[index])
                    if index < len(volumes) and volumes[index] is not None
                    else 0.0
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


def assess_quality(frames: list[ReplayFrame], interval: str) -> DataQuality:
    seconds = INTERVAL_SECONDS.get(interval, 60)
    eastern = ZoneInfo("America/New_York")
    sessions = {frame.start.astimezone(eastern).date().isoformat() for frame in frames}
    missing = 0
    duplicates = 0
    previous: ReplayFrame | None = None
    seen: set[datetime] = set()
    for frame in frames:
        if frame.start in seen:
            duplicates += 1
        seen.add(frame.start)
        if previous is not None:
            previous_day = previous.start.astimezone(eastern).date()
            current_day = frame.start.astimezone(eastern).date()
            gap = (frame.start - previous.start).total_seconds()
            if previous_day == current_day and gap > seconds * 1.5:
                missing += max(0, round(gap / seconds) - 1)
        previous = frame
    zero_volume = sum(
        1
        for frame in frames
        if frame.qqq.volume <= 0 or frame.tqqq.volume <= 0 or frame.sqqq.volume <= 0
    )
    return DataQuality(
        aligned_bars=len(frames),
        sessions=len(sessions),
        missing_intervals=missing,
        zero_volume_bars=zero_volume,
        duplicate_timestamps=duplicates,
        interval=interval,
        dataset_hash=dataset_hash(frames),
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
    quality = assess_quality(frames, interval)
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
    )


class HistoricalDataProvider:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def _fetch_symbol(
        self, client: httpx.AsyncClient, symbol: str, days: int, interval: str
    ) -> list[Bar]:
        period2 = int(utc_now().timestamp()) + 60
        period1 = period2 - days * 24 * 60 * 60
        response = await client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={
                "period1": period1,
                "period2": period2,
                "interval": interval,
                "includePrePost": "false",
                "events": "div,splits",
            },
        )
        response.raise_for_status()
        return parse_yahoo_chart(response.json(), symbol)

    async def fetch(
        self, days: int = 7, interval: str = "1m", use_cache: bool = True
    ) -> HistoricalBundle:
        maximum = INTERVAL_LIMITS.get(interval)
        if maximum is None:
            raise ValueError(f"Unsupported historical interval: {interval}")
        if not 1 <= days <= maximum:
            raise ValueError(f"{interval} historical lookback must be between 1 and {maximum} days")
        cache_path = (
            data_dir()
            / "sandbox_cache"
            / f"yahoo_{interval}_{days}d_{utc_now().date().isoformat()}.json"
        )
        if use_cache and cache_path.exists():
            return load_bundle(cache_path)
        headers = {"User-Agent": "GRANDE-Alpha/0.3 evidence research client"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers) as client:
            qqq, tqqq, sqqq = await asyncio.gather(
                self._fetch_symbol(client, "QQQ", days, interval),
                self._fetch_symbol(client, "TQQQ", days, interval),
                self._fetch_symbol(client, "SQQQ", days, interval),
            )
        frames = align_bars(qqq, tqqq, sqqq)
        if len(frames) < 30:
            raise ValueError(f"Only {len(frames)} aligned {interval} candles were available")
        quality = assess_quality(frames, interval)
        bundle = HistoricalBundle(
            source=f"Yahoo Finance chart data ({interval}) — cached research source",
            downloaded_at=utc_now(),
            frames=frames,
            interval=interval,
            dataset_hash=quality.dataset_hash,
            quality=quality,
        )
        if use_cache:
            save_bundle(bundle, cache_path)
        return bundle


def load_csv_history(path: Path, interval: str = "1m") -> HistoricalBundle:
    """Load long-history rows: timestamp,symbol,open,high,low,close,volume."""
    required = {"timestamp", "symbol", "open", "high", "low", "close"}
    series: dict[str, list[Bar]] = {"QQQ": [], "TQQQ": [], "SQQQ": []}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required <= {name.lower() for name in reader.fieldnames}:
            raise ValueError("CSV needs timestamp,symbol,open,high,low,close and optional volume columns")
        for row in reader:
            normalized = {str(key).lower(): value for key, value in row.items()}
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
    quality = assess_quality(frames, interval)
    return HistoricalBundle(
        source=f"Imported CSV: {path.name}",
        downloaded_at=utc_now(),
        frames=frames,
        interval=interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
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

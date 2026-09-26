"""Historical data acquisition, import, and offline scenario generation."""

from __future__ import annotations

import asyncio
import csv
import io
import math
import random
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from grande_alpha import __version__
from grande_alpha.configuration.config import data_dir
from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.market_models import Bar
from grande_alpha.research.historical import (
    INTERVAL_LIMITS,
    INTERVAL_SECONDS,
    SHARED_LEVERAGED_HISTORY_START,
    YAHOO_CHART_URL,
    DataProvenance,
    HistoricalBundle,
    ReplayFrame,
    align_bars,
    assess_quality,
    parse_yahoo_chart,
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
            from grande_alpha.research.historical_storage import load_bundle

            return load_bundle(cache_path)
        headers = {"User-Agent": f"GRANDE-Alpha/{__version__} research client"}
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
            provenance=DataProvenance(
                source_kind="community_unattested",
                provider="Yahoo Finance",
                provider_product="Unsupported chart endpoint",
                acquisition_method="Public chart request",
                observed_data=True,
                synthetic_or_interpolated=False,
                construction_method="provider_native",
                source_resolution_seconds=float(INTERVAL_SECONDS[interval]),
                bar_interval=interval,
                market_hours=market_hours,
                canonical_dataset_hash=quality.dataset_hash,
            ),
        )
        if use_cache:
            from grande_alpha.research.historical_storage import save_bundle

            save_bundle(bundle, cache_path)
        return bundle

    async def fetch_full_daily(self, use_cache: bool = True) -> HistoricalBundle:
        days = full_history_calendar_days()
        return await self.fetch(days, "1d", use_cache)


def full_history_calendar_days(reference: datetime | None = None) -> int:
    end = reference or utc_now()
    return max(1, math.ceil((end - SHARED_LEVERAGED_HISTORY_START).total_seconds() / 86_400) + 2)


def load_csv_history_bytes(
    raw_csv: bytes,
    source_name: str,
    interval: str = "1m",
) -> HistoricalBundle:
    """Load one immutable CSV byte snapshot into an aligned historical bundle."""

    required = {"timestamp", "symbol", "open", "high", "low", "close"}
    series: dict[str, list[Bar]] = {"QQQ": [], "TQQQ": [], "SQQQ": []}
    declared_coverage: set[str] = set()
    with io.StringIO(raw_csv.decode("utf-8-sig"), newline="") as handle:
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
            raise ValueError("CSV declaring all_day_hours must contain both evening and overnight timestamps")
    quality = assess_quality(frames, interval, coverage)
    return HistoricalBundle(
        source=f"Imported CSV: {source_name}",
        downloaded_at=utc_now(),
        frames=frames,
        interval=interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours=coverage,
        provenance=DataProvenance(
            source_kind="import_unverified",
            observed_data=False,
            synthetic_or_interpolated=True,
            bar_interval=interval,
            market_hours=coverage,
            canonical_dataset_hash=quality.dataset_hash,
        ),
    )


def load_csv_history(path: Path, interval: str = "1m") -> HistoricalBundle:
    """Load long-history rows from one file read.

    Evidence qualification uses :func:`load_csv_history_bytes` directly so inspection,
    hashing, and parsing are all bound to the same immutable byte snapshot.
    """

    return load_csv_history_bytes(path.read_bytes(), path.name, interval)


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
    quality = assess_quality(frames, "1m")
    return HistoricalBundle(
        source=f"Deterministic offline scenario (seed {seed}) — not historical market data",
        downloaded_at=utc_now(),
        frames=frames,
        interval="1m",
        dataset_hash=quality.dataset_hash,
        quality=quality,
        provenance=DataProvenance(
            source_kind="deterministic_scenario",
            provider="GRANDE Alpha",
            provider_product="Deterministic scenario generator",
            acquisition_method="Local seeded generation",
            observed_data=False,
            synthetic_or_interpolated=True,
            construction_method="synthetic",
            source_resolution_seconds=60.0,
            bar_interval="1m",
            market_hours="regular_hours",
            canonical_dataset_hash=quality.dataset_hash,
        ),
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

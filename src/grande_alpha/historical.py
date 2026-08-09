from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from grande_alpha.models import Bar, utc_now

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


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
        raise ValueError(f"No one-minute candles returned for {expected_symbol}")
    quote = quotes[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
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
            )
        )
    if not bars:
        raise ValueError(f"Historical candles for {expected_symbol} were all incomplete")
    return bars


def align_bars(qqq: list[Bar], tqqq: list[Bar], sqqq: list[Bar]) -> list[ReplayFrame]:
    maps = [{bar.start: bar for bar in series} for series in (qqq, tqqq, sqqq)]
    timestamps = sorted(set(maps[0]).intersection(maps[1], maps[2]))
    return [ReplayFrame(start, maps[0][start], maps[1][start], maps[2][start]) for start in timestamps]


class HistoricalDataProvider:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def _fetch_symbol(self, client: httpx.AsyncClient, symbol: str, days: int) -> list[Bar]:
        period2 = int(utc_now().timestamp()) + 60
        period1 = period2 - days * 24 * 60 * 60
        response = await client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={
                "period1": period1,
                "period2": period2,
                "interval": "1m",
                "includePrePost": "false",
                "events": "div,splits",
            },
        )
        response.raise_for_status()
        return parse_yahoo_chart(response.json(), symbol)

    async def fetch(self, days: int = 7) -> HistoricalBundle:
        if not 1 <= days <= 7:
            raise ValueError("One-minute historical lookback must be between 1 and 7 calendar days")
        headers = {"User-Agent": "GRANDE-Alpha/0.2 sandbox research client"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers) as client:
            qqq, tqqq, sqqq = await asyncio.gather(
                self._fetch_symbol(client, "QQQ", days),
                self._fetch_symbol(client, "TQQQ", days),
                self._fetch_symbol(client, "SQQQ", days),
            )
        frames = align_bars(qqq, tqqq, sqqq)
        if len(frames) < 30:
            raise ValueError(f"Only {len(frames)} aligned one-minute candles were available")
        return HistoricalBundle(
            source="Yahoo Finance chart data — TQQQ/SQQQ relabeled as sandbox-only aliases",
            downloaded_at=utc_now(),
            frames=frames,
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
    )

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from grande_alpha.config import data_dir
from grande_alpha.market_calendar import regular_session_times
from grande_alpha.models import Bar, utc_now
from grande_alpha.policy import session_bounds, session_key, trading_date

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
INTERVAL_LIMITS = {"1m": 7, "5m": 60, "15m": 60, "60m": 730, "1d": 10_000}
INTERVAL_SECONDS = {"5s": 5, "1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": 86400}
SHARED_LEVERAGED_HISTORY_START = datetime(2010, 2, 9, tzinfo=UTC)


def _is_sha256(value: str) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True)
class DataProvenance:
    """Machine-readable source/rights claims; a human-readable label is never sufficient."""

    source_kind: str
    provider: str = ""
    provider_product: str = ""
    acquisition_method: str = ""
    license_reference: str = ""
    license_reviewed_by_user: bool = False
    research_use_permitted: bool = False
    automated_strategy_research_permitted: bool = False
    redistribution_permitted: bool = False
    observed_data: bool = False
    synthetic_or_interpolated: bool = True
    contains_upsampled_rows: bool = False
    construction_method: str = "unknown"
    source_resolution_seconds: float | None = None
    bar_interval: str = ""
    market_hours: str = ""
    manifest_version: int = 0
    manifest_hash: str = ""
    csv_sha256: str = ""
    canonical_dataset_hash: str = ""

    @property
    def evidence_eligible(self) -> bool:
        try:
            resolution = float(self.source_resolution_seconds or 0)
            output_seconds = INTERVAL_SECONDS.get(self.bar_interval)
            if output_seconds is None and self.bar_interval.endswith("s"):
                output_seconds = int(self.bar_interval[:-1])
        except (TypeError, ValueError):
            return False
        return bool(
            self.source_kind == "imported_manifest"
            and self.manifest_version == 1
            and self.observed_data
            and not self.synthetic_or_interpolated
            and not self.contains_upsampled_rows
            and self.license_reviewed_by_user
            and self.research_use_permitted
            and self.automated_strategy_research_permitted
            and self.provider.strip()
            and self.provider_product.strip()
            and self.acquisition_method.strip()
            and self.license_reference.strip()
            and self.construction_method
            in {
                "provider_native",
                "aggregated_from_trades",
                "aggregated_from_quotes",
                "aggregated_from_nbbo",
            }
            and output_seconds is not None
            and 0 < resolution <= output_seconds
            and self.market_hours in {"regular_hours", "extended_hours", "all_day_hours"}
            and _is_sha256(self.manifest_hash)
            and _is_sha256(self.csv_sha256)
            and _is_sha256(self.canonical_dataset_hash)
        )

    @property
    def digest(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "evidence_eligible": self.evidence_eligible, "digest": self.digest}


@dataclass(frozen=True)
class DataQuality:
    aligned_bars: int
    sessions: int
    missing_intervals: int
    zero_volume_bars: int
    duplicate_timestamps: int
    invalid_session_bars: int
    interval: str
    dataset_hash: str
    complete_sessions: int = 0
    session_coverage_pct: float = 0.0
    expected_sessions: int = 0
    missing_sessions: int = 0

    @property
    def clean(self) -> bool:
        return (
            self.aligned_bars > 0
            and self.duplicate_timestamps == 0
            and self.invalid_session_bars == 0
            and self.missing_sessions == 0
        )


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
    provenance: DataProvenance | None = None

    @property
    def start(self) -> datetime:
        return self.frames[0].start

    @property
    def end(self) -> datetime:
        return self.frames[-1].start

    @property
    def provenance_hash(self) -> str:
        return self.provenance.digest if self.provenance is not None else ""

    @property
    def evidence_provenance_eligible(self) -> bool:
        return bool(
            self.provenance is not None
            and self.provenance.evidence_eligible
            and self.provenance.canonical_dataset_hash == self.dataset_hash
            and self.provenance.bar_interval == self.interval
            and self.provenance.market_hours == self.market_hours
        )


@dataclass(frozen=True)
class ChronologicalHoldoutSplit:
    development: HistoricalBundle
    holdout: HistoricalBundle
    purged_sessions: tuple[str, ...]


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
        "provenance": bundle.provenance.as_dict() if bundle.provenance is not None else None,
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
    raw_provenance = payload.get("provenance")
    provenance = None
    if isinstance(raw_provenance, dict):
        stored_digest = str(raw_provenance.get("digest") or "")
        if not stored_digest:
            raise ValueError("Cached historical provenance is missing its required digest")
        allowed = DataProvenance.__dataclass_fields__.keys()
        provenance = DataProvenance(
            **{key: value for key, value in raw_provenance.items() if key in allowed}
        )
        if stored_digest != provenance.digest:
            raise ValueError("Cached historical provenance hash does not match its contents")
    return HistoricalBundle(
        source=str(payload["source"]),
        downloaded_at=datetime.fromisoformat(payload["downloaded_at"]).astimezone(UTC),
        frames=frames,
        interval=interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours=market_hours,
        provenance=provenance,
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

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import math
import random
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from grande_alpha import __version__
from grande_alpha.config import data_dir
from grande_alpha.market_calendar import regular_session_times
from grande_alpha.models import Bar, Quote, utc_now
from grande_alpha.policy import market_session_allowed, session_bounds, session_key, trading_date
from grande_alpha.storage import EXACT_QUOTE_VALIDATOR_VERSION, QUOTE_BATCH_SCHEMA_VERSION
from grande_alpha.strategy import BarBuilder

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
INTERVAL_LIMITS = {"1m": 7, "5m": 60, "15m": 60, "60m": 730, "1d": 10_000}
INTERVAL_SECONDS = {"5s": 5, "1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": 86400}
SHARED_LEVERAGED_HISTORY_START = datetime(2010, 2, 9, tzinfo=UTC)
RUNTIME_OBSERVATION_SCHEMA = "grande_runtime_quote_v2"
RUNTIME_ANALYSIS_PRICE_SEMANTICS = "qqq_bid_ask_mid_ohlc"
RUNTIME_EXECUTION_PRICE_SEMANTICS = "causal_target_bid_ask"
RUNTIME_VOLUME_SEMANTICS = "absent"
RUNTIME_REQUIRED_SYMBOLS = ("QQQ", "TQQQ", "SQQQ")
EASTERN = ZoneInfo("America/New_York")
RUNTIME_PROVENANCE_FIELDS = frozenset(
    {
        "observation_schema",
        "analysis_price_semantics",
        "execution_price_semantics",
        "volume_semantics",
        "source_trace_sha256",
        "excluded_legacy_quote_rows",
        "validator_profile",
        "validator_version",
        "validator_max_age_seconds",
        "validator_max_skew_seconds",
        "excluded_nonexact_quote_batches",
    }
)


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
    observation_schema: str = "generic_ohlcv_v1"
    analysis_price_semantics: str = "bar_ohlc"
    execution_price_semantics: str = "next_bar_modeled_spread"
    volume_semantics: str = "provider_or_zero"
    source_trace_sha256: str = ""
    excluded_legacy_quote_rows: int = 0
    validator_profile: str = ""
    validator_version: int = 0
    validator_max_age_seconds: float | None = None
    validator_max_skew_seconds: float | None = None
    excluded_nonexact_quote_batches: int = 0

    @property
    def evidence_eligible(self) -> bool:
        try:
            resolution = float(self.source_resolution_seconds or 0)
            output_seconds = INTERVAL_SECONDS.get(self.bar_interval)
            if output_seconds is None and self.bar_interval.endswith("s"):
                output_seconds = int(self.bar_interval[:-1])
        except (TypeError, ValueError):
            return False
        source_content_bound = (
            self.source_kind == "imported_manifest"
            and _is_sha256(self.csv_sha256)
        ) or (
            self.source_kind == "grande_runtime_quote_trace"
            and _is_sha256(self.source_trace_sha256)
        )
        return bool(
            source_content_bound
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
            and _is_sha256(self.canonical_dataset_hash)
        )

    @property
    def runtime_observation_eligible(self) -> bool:
        """Whether source identity and semantics match the live causal observation path."""

        return bool(
            self.evidence_eligible
            and self.source_kind == "grande_runtime_quote_trace"
            and self.observation_schema == RUNTIME_OBSERVATION_SCHEMA
            and self.analysis_price_semantics == RUNTIME_ANALYSIS_PRICE_SEMANTICS
            and self.execution_price_semantics == RUNTIME_EXECUTION_PRICE_SEMANTICS
            and self.volume_semantics == RUNTIME_VOLUME_SEMANTICS
            and self.validator_profile == "exact_execution_quotes"
            and self.validator_version == EXACT_QUOTE_VALIDATOR_VERSION
            and self.validator_max_age_seconds is not None
            and 0 < self.validator_max_age_seconds <= 8.0
            and self.validator_max_skew_seconds is not None
            and 0 < self.validator_max_skew_seconds
            <= min(5.0, self.validator_max_age_seconds)
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
    causal_timestamp: datetime | None = None
    qqq_quote: Quote | None = None
    tqqq_quote: Quote | None = None
    sqqq_quote: Quote | None = None
    stream_id: str = ""

    def bar_for_alias(self, alias: str) -> Bar:
        if alias == "TQQQS":
            return self.tqqq
        if alias == "SQQQS":
            return self.sqqq
        raise ValueError(f"Unknown sandbox alias: {alias}")

    @property
    def has_exact_runtime_observation(self) -> bool:
        quotes = (self.qqq_quote, self.tqqq_quote, self.sqqq_quote)
        if self.causal_timestamp is None or any(quote is None for quote in quotes):
            return False
        assert all(quote is not None for quote in quotes)
        return bool(
            tuple(quote.symbol for quote in quotes if quote is not None) == RUNTIME_REQUIRED_SYMBOLS
            and all(quote.book_timestamp is not None for quote in quotes if quote is not None)
            and self.causal_timestamp
            == max(
                quote.latest_book_timestamp
                for quote in quotes
                if quote is not None and quote.latest_book_timestamp is not None
            )
            and self.causal_timestamp > self.qqq.start
            and bool(self.stream_id.strip())
        )

    def runtime_quotes(self) -> dict[str, Quote]:
        if not self.has_exact_runtime_observation:
            raise ValueError("Replay frame has no exact runtime quote observation")
        assert self.qqq_quote is not None
        assert self.tqqq_quote is not None
        assert self.sqqq_quote is not None
        return {
            "QQQ": self.qqq_quote,
            "TQQQ": self.tqqq_quote,
            "SQQQ": self.sqqq_quote,
        }


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

    @property
    def runtime_observation_parity_eligible(self) -> bool:
        return bool(
            self.provenance is not None
            and self.provenance.runtime_observation_eligible
            and self.provenance.canonical_dataset_hash == self.dataset_hash
            and self.provenance.bar_interval == self.interval
            and self.provenance.market_hours == self.market_hours
            and self.frames
            and all(frame.has_exact_runtime_observation for frame in self.frames)
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


def _quote_dict(quote: Quote | None) -> dict[str, Any] | None:
    if quote is None:
        return None
    return {
        "symbol": quote.symbol,
        "bid": quote.bid,
        "ask": quote.ask,
        "last": quote.last,
        "timestamp": quote.timestamp.isoformat(),
        "bid_timestamp": (
            quote.bid_timestamp.isoformat() if quote.bid_timestamp is not None else None
        ),
        "ask_timestamp": (
            quote.ask_timestamp.isoformat() if quote.ask_timestamp is not None else None
        ),
    }


def _quote_from_dict(raw: dict[str, Any] | None) -> Quote | None:
    if raw is None:
        return None
    quote = Quote(
        symbol=str(raw["symbol"]),
        bid=float(raw["bid"]),
        ask=float(raw["ask"]),
        last=float(raw["last"]),
        timestamp=datetime.fromisoformat(str(raw["timestamp"])).astimezone(UTC),
        bid_timestamp=(
            datetime.fromisoformat(str(raw["bid_timestamp"])).astimezone(UTC)
            if raw.get("bid_timestamp") is not None
            else None
        ),
        ask_timestamp=(
            datetime.fromisoformat(str(raw["ask_timestamp"])).astimezone(UTC)
            if raw.get("ask_timestamp") is not None
            else None
        ),
    )
    quote.validate()
    return quote


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
                "causal_timestamp": (
                    frame.causal_timestamp.isoformat() if frame.causal_timestamp is not None else None
                ),
                "qqq_quote": _quote_dict(frame.qqq_quote),
                "tqqq_quote": _quote_dict(frame.tqqq_quote),
                "sqqq_quote": _quote_dict(frame.sqqq_quote),
                "stream_id": frame.stream_id,
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
            (
                datetime.fromisoformat(row["causal_timestamp"]).astimezone(UTC)
                if row.get("causal_timestamp")
                else None
            ),
            _quote_from_dict(row.get("qqq_quote")),
            _quote_from_dict(row.get("tqqq_quote")),
            _quote_from_dict(row.get("sqqq_quote")),
            str(row.get("stream_id") or ""),
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
            legacy_payload = {
                key: raw_provenance[key]
                for key in allowed
                if key not in RUNTIME_PROVENANCE_FIELDS and key in raw_provenance
            }
            legacy_digest = hashlib.sha256(
                json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if RUNTIME_PROVENANCE_FIELDS.intersection(raw_provenance) or stored_digest != legacy_digest:
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


def runtime_trace_manifest_template(
    bundle: HistoricalBundle,
    quote_row_count: int,
    *,
    range_start: date | None = None,
    range_end: date | None = None,
) -> dict[str, Any]:
    """Return a fail-closed manifest template bound to one imported runtime trace."""

    provenance = bundle.provenance
    if provenance is None or not _is_sha256(provenance.source_trace_sha256):
        raise ValueError("Runtime trace bundle is missing its source trace digest")
    return {
        "manifest_version": 1,
        "dataset_id": "replace-with-stable-runtime-trace-id",
        "created_at": utc_now().isoformat(),
        "provider": "",
        "provider_product": "",
        "acquisition_method": "GRANDE Alpha synchronized venue quote recorder",
        "license_reference": "",
        "license_reviewed_by_user": False,
        "research_use_permitted": False,
        "automated_strategy_research_permitted": False,
        "redistribution_permitted": False,
        "observed_data": True,
        "synthetic_or_interpolated": False,
        "contains_upsampled_rows": False,
        "symbols": list(RUNTIME_REQUIRED_SYMBOLS),
        "bar_interval": bundle.interval,
        "source_resolution_seconds": provenance.source_resolution_seconds,
        "construction_method": "aggregated_from_quotes",
        "observation_schema": RUNTIME_OBSERVATION_SCHEMA,
        "analysis_price_semantics": RUNTIME_ANALYSIS_PRICE_SEMANTICS,
        "execution_price_semantics": RUNTIME_EXECUTION_PRICE_SEMANTICS,
        "volume_semantics": RUNTIME_VOLUME_SEMANTICS,
        "timestamp_timezone": "UTC",
        "timestamp_semantics": "venue_quote_time",
        "market_hours": bundle.market_hours,
        "start": bundle.start.isoformat(),
        "end": bundle.end.isoformat(),
        "range_start": range_start.isoformat() if range_start is not None else None,
        "range_end": range_end.isoformat() if range_end is not None else None,
        "source_trace_sha256": provenance.source_trace_sha256,
        "dataset_hash": bundle.dataset_hash,
        "quote_row_count": quote_row_count,
        "excluded_legacy_quote_rows": provenance.excluded_legacy_quote_rows,
        "validator_profile": provenance.validator_profile,
        "validator_version": provenance.validator_version,
        "validator_max_age_seconds": provenance.validator_max_age_seconds,
        "validator_max_skew_seconds": provenance.validator_max_skew_seconds,
        "excluded_nonexact_quote_batches": provenance.excluded_nonexact_quote_batches,
    }


def _runtime_trace_provenance(
    manifest: dict[str, Any] | None,
    *,
    source_trace_sha256: str,
    dataset_hash_value: str,
    interval: str,
    market_hours: str,
    quote_row_count: int,
    excluded_legacy_quote_rows: int,
    validator_max_age_seconds: float,
    validator_max_skew_seconds: float,
    excluded_nonexact_quote_batches: int,
    source_resolution_seconds: float,
    start: datetime,
    end: datetime,
    range_start: date | None,
    range_end: date | None,
) -> DataProvenance:
    if manifest is None:
        return DataProvenance(
            source_kind="grande_runtime_quote_trace_unattested",
            acquisition_method="GRANDE Alpha synchronized venue quote recorder",
            observed_data=True,
            synthetic_or_interpolated=False,
            contains_upsampled_rows=False,
            construction_method="aggregated_from_quotes",
            source_resolution_seconds=source_resolution_seconds,
            bar_interval=interval,
            market_hours=market_hours,
            canonical_dataset_hash=dataset_hash_value,
            observation_schema=RUNTIME_OBSERVATION_SCHEMA,
            analysis_price_semantics=RUNTIME_ANALYSIS_PRICE_SEMANTICS,
            execution_price_semantics=RUNTIME_EXECUTION_PRICE_SEMANTICS,
            volume_semantics=RUNTIME_VOLUME_SEMANTICS,
            source_trace_sha256=source_trace_sha256,
            excluded_legacy_quote_rows=excluded_legacy_quote_rows,
            validator_profile="exact_execution_quotes",
            validator_version=EXACT_QUOTE_VALIDATOR_VERSION,
            validator_max_age_seconds=validator_max_age_seconds,
            validator_max_skew_seconds=validator_max_skew_seconds,
            excluded_nonexact_quote_batches=excluded_nonexact_quote_batches,
        )
    required_exact = {
        "manifest_version": 1,
        "observed_data": True,
        "synthetic_or_interpolated": False,
        "contains_upsampled_rows": False,
        "symbols": list(RUNTIME_REQUIRED_SYMBOLS),
        "bar_interval": interval,
        "construction_method": "aggregated_from_quotes",
        "observation_schema": RUNTIME_OBSERVATION_SCHEMA,
        "analysis_price_semantics": RUNTIME_ANALYSIS_PRICE_SEMANTICS,
        "execution_price_semantics": RUNTIME_EXECUTION_PRICE_SEMANTICS,
        "volume_semantics": RUNTIME_VOLUME_SEMANTICS,
        "timestamp_timezone": "UTC",
        "timestamp_semantics": "venue_quote_time",
        "market_hours": market_hours,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "range_start": range_start.isoformat() if range_start is not None else None,
        "range_end": range_end.isoformat() if range_end is not None else None,
        "source_trace_sha256": source_trace_sha256,
        "dataset_hash": dataset_hash_value,
        "quote_row_count": quote_row_count,
        "excluded_legacy_quote_rows": excluded_legacy_quote_rows,
        "validator_profile": "exact_execution_quotes",
        "validator_version": EXACT_QUOTE_VALIDATOR_VERSION,
        "validator_max_age_seconds": validator_max_age_seconds,
        "validator_max_skew_seconds": validator_max_skew_seconds,
        "excluded_nonexact_quote_batches": excluded_nonexact_quote_batches,
        "source_resolution_seconds": source_resolution_seconds,
    }
    mismatches = [key for key, value in required_exact.items() if manifest.get(key) != value]
    if mismatches:
        raise ValueError("Runtime trace manifest does not match: " + ", ".join(mismatches))
    try:
        source_resolution = float(manifest.get("source_resolution_seconds"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Runtime trace source resolution must be numeric") from exc
    output_seconds = float(INTERVAL_SECONDS.get(interval, 60))
    if not 0 < source_resolution <= output_seconds:
        raise ValueError("Runtime trace source resolution must be positive and no coarser than bars")
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DataProvenance(
        source_kind="grande_runtime_quote_trace",
        provider=str(manifest.get("provider") or ""),
        provider_product=str(manifest.get("provider_product") or ""),
        acquisition_method=str(manifest.get("acquisition_method") or ""),
        license_reference=str(manifest.get("license_reference") or ""),
        license_reviewed_by_user=manifest.get("license_reviewed_by_user") is True,
        research_use_permitted=manifest.get("research_use_permitted") is True,
        automated_strategy_research_permitted=(
            manifest.get("automated_strategy_research_permitted") is True
        ),
        redistribution_permitted=manifest.get("redistribution_permitted") is True,
        observed_data=True,
        synthetic_or_interpolated=False,
        contains_upsampled_rows=False,
        construction_method="aggregated_from_quotes",
        source_resolution_seconds=source_resolution,
        bar_interval=interval,
        market_hours=market_hours,
        manifest_version=1,
        manifest_hash=manifest_hash,
        canonical_dataset_hash=dataset_hash_value,
        observation_schema=RUNTIME_OBSERVATION_SCHEMA,
        analysis_price_semantics=RUNTIME_ANALYSIS_PRICE_SEMANTICS,
        execution_price_semantics=RUNTIME_EXECUTION_PRICE_SEMANTICS,
        volume_semantics=RUNTIME_VOLUME_SEMANTICS,
        source_trace_sha256=source_trace_sha256,
        excluded_legacy_quote_rows=excluded_legacy_quote_rows,
        validator_profile="exact_execution_quotes",
        validator_version=EXACT_QUOTE_VALIDATOR_VERSION,
        validator_max_age_seconds=validator_max_age_seconds,
        validator_max_skew_seconds=validator_max_skew_seconds,
        excluded_nonexact_quote_batches=excluded_nonexact_quote_batches,
    )


def _aware_trace_timestamp(raw: object, field: str) -> datetime:
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Runtime quote trace {field} must include a UTC offset")
    return parsed.astimezone(UTC)


def _runtime_trace_observed_bounds(
    range_start: date | None,
    range_end: date | None,
    market_hours: str,
) -> tuple[datetime | None, datetime | None]:
    if range_start is not None and range_end is not None and range_start > range_end:
        raise ValueError("Runtime trace --start must be on or before --end")

    def bounds_for(trading_day: date) -> tuple[datetime, datetime]:
        anchor = datetime.combine(trading_day, time(12, 0), tzinfo=EASTERN)
        return session_bounds(anchor, market_hours)

    started = bounds_for(range_start)[0].astimezone(UTC) if range_start is not None else None
    # Exact batches permit book age up to eight seconds and a two-second future-clock tolerance.
    # The observed recorder clock can therefore fall just after the venue session boundary.
    ended = (
        bounds_for(range_end)[1].astimezone(UTC) + timedelta(seconds=10)
        if range_end is not None
        else None
    )
    return started, ended


def _load_runtime_quote_trace(
    database_path: Path,
    *,
    bar_seconds: int = 60,
    market_hours: str = "regular_hours",
    manifest: dict[str, Any] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> tuple[HistoricalBundle, int]:
    """Read GRANDE Alpha's quote ledger in SQLite read-only mode into causal frames.

    Every accepted response must have a current durable batch record and exact
    QQQ/TQQQ/SQQQ children with two-sided book clocks.
    Legacy adjacency is never inferred. QQQ bid/ask mids are fed through the runtime
    :class:`BarBuilder`; the batch that emits a completed bar supplies the later target bid/ask
    quotes and causal execution timestamp. Quote data has no volume, so every derived bar records
    zero volume and volume-based evidence remains unavailable.
    """

    if bar_seconds < 1 or bar_seconds > 300:
        raise ValueError("Runtime trace bar duration must be between 1 and 300 seconds")
    if market_hours not in {"regular_hours", "extended_hours", "all_day_hours"}:
        raise ValueError("Unsupported runtime trace market hours")
    observed_start, observed_end = _runtime_trace_observed_bounds(start, end, market_hours)
    range_sql = ""
    range_parameters: list[str] = []
    if observed_start is not None:
        range_sql += " AND observed_at>=?"
        range_parameters.append(observed_start.isoformat())
    if observed_end is not None:
        range_sql += " AND observed_at<=?"
        range_parameters.append(observed_end.isoformat())
    resolved = database_path.resolve(strict=True)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(quotes)").fetchall()
        }
        required = {
            "id",
            "observed_at",
            "symbol",
            "bid",
            "ask",
            "last",
            "venue_timestamp",
            "bid_timestamp",
            "ask_timestamp",
        }
        required.update({"batch_id", "batch_position"})
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not required <= columns or "quote_batches" not in tables:
            raise ValueError("Database has no compatible GRANDE Alpha quote ledger")
        legacy_count = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM quotes "
                f"WHERE (batch_id IS NULL OR batch_position IS NULL){range_sql}",
                range_parameters,
            ).fetchone()["n"]
        )
        excluded_nonexact = int(
            connection.execute(
                """SELECT COUNT(*) AS n FROM quote_batches
                WHERE (validation_profile!='exact_execution_quotes'
                   OR schema_version!=?
                   OR validation_version!=?
                   OR max_age_seconds IS NULL OR max_skew_seconds IS NULL)"""
                + range_sql,
                (
                    QUOTE_BATCH_SCHEMA_VERSION,
                    EXACT_QUOTE_VALIDATOR_VERSION,
                    *range_parameters,
                ),
            ).fetchone()["n"]
        )
        batch_rows = connection.execute(
            """SELECT rowid AS batch_sequence,batch_id,stream_id,observed_at,
            schema_version,symbol_count,validation_profile,validation_version,
            max_age_seconds,max_skew_seconds FROM quote_batches
            WHERE validation_profile='exact_execution_quotes' AND schema_version=?
              AND validation_version=?
              AND max_age_seconds IS NOT NULL AND max_skew_seconds IS NOT NULL
            """
            + range_sql
            + " ORDER BY rowid",
            (
                QUOTE_BATCH_SCHEMA_VERSION,
                EXACT_QUOTE_VALIDATOR_VERSION,
                *range_parameters,
            ),
        ).fetchall()
        rows = connection.execute(
            """SELECT id,observed_at,symbol,bid,ask,last,venue_timestamp,
            bid_timestamp,ask_timestamp,batch_id,batch_position
            FROM quotes WHERE batch_id IN (
                SELECT batch_id FROM quote_batches
                WHERE validation_profile='exact_execution_quotes' AND schema_version=?
                   AND validation_version=?
                   AND max_age_seconds IS NOT NULL AND max_skew_seconds IS NOT NULL
            """
            + range_sql
            + """
            ) AND batch_position IS NOT NULL
            ORDER BY batch_id,batch_position""",
            (
                QUOTE_BATCH_SCHEMA_VERSION,
                EXACT_QUOTE_VALIDATOR_VERSION,
                *range_parameters,
            ),
        ).fetchall()
    finally:
        connection.close()
    if len(batch_rows) < 2 or len(rows) < 6:
        raise ValueError("Runtime quote trace needs at least two synchronized quote batches")

    trace_digest = hashlib.sha256()
    batches: list[dict[str, Quote]] = []
    rows_by_batch: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        rows_by_batch.setdefault(str(row["batch_id"]), []).append(row)
    known_batch_ids = {str(row["batch_id"]) for row in batch_rows}
    if set(rows_by_batch) != known_batch_ids:
        raise ValueError("Runtime quote rows reference a missing or childless provider batch")
    envelopes = {
        (float(row["max_age_seconds"]), float(row["max_skew_seconds"]))
        for row in batch_rows
    }
    if len(envelopes) != 1:
        raise ValueError("Exact runtime trace must use one validator age/skew envelope")
    validator_max_age_seconds, validator_max_skew_seconds = next(iter(envelopes))
    if (
        not 0 < validator_max_age_seconds <= 8.0
        or not 0 < validator_max_skew_seconds
        <= min(5.0, validator_max_age_seconds)
    ):
        raise ValueError("Exact runtime trace validator envelope is unsupported")
    for batch_record in batch_rows:
        batch_id = str(batch_record["batch_id"])
        batch_sequence = int(batch_record["batch_sequence"])
        stream_id = str(batch_record["stream_id"]).strip()
        schema_version = int(batch_record["schema_version"])
        symbol_count = int(batch_record["symbol_count"])
        validation_profile = str(batch_record["validation_profile"])
        validation_version = int(batch_record["validation_version"])
        if not stream_id:
            raise ValueError("Runtime quote batch lacks a bound signal-pipeline stream ID")
        if (
            schema_version != QUOTE_BATCH_SCHEMA_VERSION
            or validation_profile != "exact_execution_quotes"
            or validation_version != EXACT_QUOTE_VALIDATOR_VERSION
            or symbol_count != len(RUNTIME_REQUIRED_SYMBOLS)
        ):
            raise ValueError("Runtime quote batch has an unsupported or inconsistent schema")
        batch_observed_at = _aware_trace_timestamp(
            batch_record["observed_at"], "batch observed_at"
        )
        chunk = rows_by_batch[batch_id]
        if len(chunk) != len(RUNTIME_REQUIRED_SYMBOLS):
            raise ValueError("Runtime quote batch is interrupted or incomplete")
        if sorted(int(row["batch_position"]) for row in chunk) != [0, 1, 2]:
            raise ValueError("Runtime quote batch positions must be exactly 0, 1, and 2")
        observed_times: list[datetime] = []
        quotes: dict[str, Quote] = {}
        for row in sorted(chunk, key=lambda item: int(item["batch_position"])):
            observed_at = _aware_trace_timestamp(row["observed_at"], "observed_at")
            if observed_at != batch_observed_at:
                raise ValueError("Runtime quote child does not match its atomic batch timestamp")
            venue_timestamp = _aware_trace_timestamp(row["venue_timestamp"], "venue_timestamp")
            bid_timestamp = _aware_trace_timestamp(row["bid_timestamp"], "bid_timestamp")
            ask_timestamp = _aware_trace_timestamp(row["ask_timestamp"], "ask_timestamp")
            symbol = str(row["symbol"]).upper()
            quote = Quote(
                symbol,
                float(row["bid"]),
                float(row["ask"]),
                float(row["last"]),
                venue_timestamp,
                bid_timestamp,
                ask_timestamp,
            )
            quote.validate()
            if symbol in quotes:
                raise ValueError("Runtime quote batch contains a duplicate symbol")
            quotes[symbol] = quote
            observed_times.append(observed_at)
            trace_digest.update(
                f"{batch_sequence}|{stream_id}|{batch_id}|{schema_version}|{symbol_count}|"
                f"{validation_profile}|{validation_version}|"
                f"{validator_max_age_seconds:.6f}|{validator_max_skew_seconds:.6f}|"
                f"{row['batch_position']}|{row['id']}|"
                f"{observed_at.isoformat()}|{symbol}|{quote.bid:.8f}|"
                f"{quote.ask:.8f}|{quote.last:.8f}|{venue_timestamp.isoformat()}|"
                f"{bid_timestamp.isoformat()}|{ask_timestamp.isoformat()}\n".encode()
            )
        if tuple(sorted(quotes)) != tuple(sorted(RUNTIME_REQUIRED_SYMBOLS)):
            raise ValueError("Runtime quote batch must contain exactly QQQ, TQQQ, and SQQQ")
        if (max(observed_times) - min(observed_times)).total_seconds() > 2.0:
            raise ValueError("Runtime quote rows are not one synchronized recorder batch")
        book_times = [
            timestamp
            for quote in quotes.values()
            for timestamp in (quote.bid_timestamp, quote.ask_timestamp)
            if timestamp is not None
        ]
        ages = [(batch_observed_at - timestamp).total_seconds() for timestamp in book_times]
        if any(age < -2.0 or age > validator_max_age_seconds for age in ages):
            raise ValueError("Runtime quote batch violates its bound validator age envelope")
        if (
            max(book_times) - min(book_times)
        ).total_seconds() > validator_max_skew_seconds:
            raise ValueError("Runtime quote batch violates its bound validator skew envelope")
        batches.append({"__stream_id__": stream_id, **quotes})

    eligible_batches = [
        quotes
        for quotes in batches
        if all(
            market_session_allowed(timestamp, 0, 0, market_hours)
            for symbol, quote in quotes.items()
            if symbol != "__stream_id__"
            for timestamp in (quote.bid_timestamp, quote.ask_timestamp)
        )
    ]
    sessions_by_stream: dict[str, set[str]] = {}
    for quotes in eligible_batches:
        sessions_by_stream.setdefault(str(quotes["__stream_id__"]), set()).add(
            session_key(quotes["QQQ"].latest_book_timestamp, market_hours)
        )
    if any(len(sessions) > 1 for sessions in sessions_by_stream.values()):
        raise ValueError(
            "Runtime quote stream spans multiple sessions without a signal-pipeline reset"
        )
    within_session_deltas = [
        (
            current["QQQ"].latest_book_timestamp
            - previous["QQQ"].latest_book_timestamp
        ).total_seconds()
        for previous, current in zip(eligible_batches, eligible_batches[1:], strict=False)
        if session_key(previous["QQQ"].latest_book_timestamp, market_hours)
        == session_key(current["QQQ"].latest_book_timestamp, market_hours)
        and current["QQQ"].latest_book_timestamp > previous["QQQ"].latest_book_timestamp
        and previous["__stream_id__"] == current["__stream_id__"]
    ]
    source_resolution_seconds = (
        max(within_session_deltas) if within_session_deltas else float(bar_seconds)
    )
    builder = BarBuilder("QQQ", bar_seconds)
    frames: list[ReplayFrame] = []
    active_stream: str | None = None
    last_qqq_timestamp: datetime | None = None
    for quotes in eligible_batches:
        stream_id = str(quotes["__stream_id__"])
        if active_stream != stream_id:
            builder = BarBuilder("QQQ", bar_seconds)
            active_stream = stream_id
            last_qqq_timestamp = None
        qqq_observed_at = quotes["QQQ"].latest_book_timestamp
        if qqq_observed_at is None:
            raise ValueError("Runtime QQQ quote lacks exact book observation time")
        if last_qqq_timestamp is not None and qqq_observed_at <= last_qqq_timestamp:
            continue
        last_qqq_timestamp = qqq_observed_at
        completed = builder.update(replace(quotes["QQQ"], timestamp=qqq_observed_at))
        if completed is None:
            continue
        causal_timestamp = max(
            quotes[symbol].latest_book_timestamp for symbol in RUNTIME_REQUIRED_SYMBOLS
        )
        if causal_timestamp <= completed.start:
            raise ValueError("Runtime causal quote must be later than its completed analysis bar")

        def point_bar(symbol: str, quote: Quote, start: datetime) -> Bar:
            price = quote.mid
            return Bar(symbol, start, price, price, price, price, 1, 0.0)

        frames.append(
            ReplayFrame(
                completed.start,
                completed,
                point_bar("TQQQ", quotes["TQQQ"], completed.start),
                point_bar("SQQQ", quotes["SQQQ"], completed.start),
                causal_timestamp,
                quotes["QQQ"],
                quotes["TQQQ"],
                quotes["SQQQ"],
                stream_id,
            )
        )
    if not frames:
        raise ValueError("Runtime quote trace did not complete an analysis bar")
    interval = f"{bar_seconds}s" if bar_seconds != 60 else "1m"
    quality = assess_quality(frames, interval, market_hours)
    provenance = _runtime_trace_provenance(
        manifest,
        source_trace_sha256=trace_digest.hexdigest(),
        dataset_hash_value=quality.dataset_hash,
        interval=interval,
        market_hours=market_hours,
        quote_row_count=len(rows),
        excluded_legacy_quote_rows=legacy_count,
        validator_max_age_seconds=validator_max_age_seconds,
        validator_max_skew_seconds=validator_max_skew_seconds,
        excluded_nonexact_quote_batches=excluded_nonexact,
        source_resolution_seconds=source_resolution_seconds,
        start=frames[0].start,
        end=frames[-1].start,
        range_start=start,
        range_end=end,
    )
    return (
        HistoricalBundle(
            source="GRANDE Alpha synchronized runtime venue quote trace",
            downloaded_at=utc_now(),
            frames=frames,
            interval=interval,
            dataset_hash=quality.dataset_hash,
            quality=quality,
            market_hours=market_hours,
            provenance=provenance,
        ),
        len(rows),
    )


def load_runtime_quote_trace(
    database_path: Path,
    *,
    bar_seconds: int = 60,
    market_hours: str = "regular_hours",
    manifest: dict[str, Any] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> HistoricalBundle:
    """Load a range-bound exact quote trace without opening SQLite for writes."""

    bundle, _ = _load_runtime_quote_trace(
        database_path,
        bar_seconds=bar_seconds,
        market_hours=market_hours,
        manifest=manifest,
        start=start,
        end=end,
    )
    return bundle


def load_runtime_quote_trace_with_row_count(
    database_path: Path,
    *,
    bar_seconds: int = 60,
    market_hours: str = "regular_hours",
    manifest: dict[str, Any] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> tuple[HistoricalBundle, int]:
    """Return the range-bound bundle and exact selected source-row count."""

    return _load_runtime_quote_trace(
        database_path,
        bar_seconds=bar_seconds,
        market_hours=market_hours,
        manifest=manifest,
        start=start,
        end=end,
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

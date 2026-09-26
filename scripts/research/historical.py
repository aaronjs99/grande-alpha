from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.domain.market_models import Bar, Quote
from grande_alpha.domain.policy import session_bounds, session_key, trading_date
from grande_alpha.persistence.store import EXACT_QUOTE_VALIDATOR_VERSION

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

"""Shared records and data-contract constants for historical research."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from grande_alpha.domain.market_models import EXACT_QUOTE_VALIDATOR_VERSION, Bar, Quote

INTERVAL_SECONDS = {"5s": 5, "1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": 86400}
RUNTIME_OBSERVATION_SCHEMA = "grande_runtime_quote_v2"
RUNTIME_ANALYSIS_PRICE_SEMANTICS = "qqq_bid_ask_mid_ohlc"
RUNTIME_EXECUTION_PRICE_SEMANTICS = "causal_target_bid_ask"
RUNTIME_VOLUME_SEMANTICS = "absent"
RUNTIME_REQUIRED_SYMBOLS = ("QQQ", "TQQQ", "SQQQ")
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
    """Machine-readable source and rights claims for a dataset."""

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
            self.source_kind == "imported_manifest" and _is_sha256(self.csv_sha256)
        ) or (
            self.source_kind == "grande_runtime_quote_trace" and _is_sha256(self.source_trace_sha256)
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

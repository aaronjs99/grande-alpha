from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from grande_alpha.domain.clock import utc_now
from grande_alpha.persistence.store import EXACT_QUOTE_VALIDATOR_VERSION
from grande_alpha.research.historical import (
    INTERVAL_SECONDS,
    RUNTIME_ANALYSIS_PRICE_SEMANTICS,
    RUNTIME_EXECUTION_PRICE_SEMANTICS,
    RUNTIME_OBSERVATION_SCHEMA,
    RUNTIME_REQUIRED_SYMBOLS,
    RUNTIME_VOLUME_SEMANTICS,
    DataProvenance,
    HistoricalBundle,
    _is_sha256,
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

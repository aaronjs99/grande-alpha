"""Versioned provenance metadata for imported historical market data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from grande_alpha.research.historical_models import (
    INTERVAL_SECONDS,
    RUNTIME_REQUIRED_SYMBOLS,
    DataProvenance,
)

MANIFEST_VERSION = 1
MANIFEST_REQUIRED_FIELDS = frozenset(
    {
        "manifest_version",
        "dataset_id",
        "created_at",
        "provider",
        "provider_product",
        "acquisition_method",
        "license_reference",
        "license_reviewed_by_user",
        "research_use_permitted",
        "automated_strategy_research_permitted",
        "redistribution_permitted",
        "observed_data",
        "synthetic_or_interpolated",
        "symbols",
        "bar_interval",
        "source_resolution_seconds",
        "construction_method",
        "contains_upsampled_rows",
        "timestamp_timezone",
        "timestamp_semantics",
        "market_hours",
        "start",
        "end",
        "price_adjustment",
        "corporate_action_policy",
        "csv_sha256",
        "dataset_hash",
        "row_count",
    }
)
CONSTRUCTION_METHODS = frozenset(
    {
        "provider_native",
        "aggregated_from_trades",
        "aggregated_from_quotes",
        "aggregated_from_nbbo",
    }
)
PRICE_ADJUSTMENTS = frozenset(
    {"unadjusted", "split_adjusted", "split_and_dividend_adjusted"}
)


def interval_seconds(interval: str) -> int:
    if interval in INTERVAL_SECONDS:
        return INTERVAL_SECONDS[interval]
    if interval.endswith("s") and interval[:-1].isdigit() and int(interval[:-1]) > 0:
        return int(interval[:-1])
    raise ValueError(f"Unsupported interval: {interval}")


def manifest_template(target_interval: str = "5s") -> dict[str, Any]:
    seconds = interval_seconds(target_interval)
    return {
        "manifest_version": MANIFEST_VERSION,
        "dataset_id": "replace-with-stable-dataset-id",
        "created_at": "YYYY-MM-DDTHH:MM:SS+00:00",
        "provider": "provider legal name",
        "provider_product": "licensed product or export name",
        "acquisition_method": "API or export method; never include a token or account number",
        "license_reference": "public terms URL or local contract name; never include credentials",
        "license_reviewed_by_user": False,
        "research_use_permitted": False,
        "automated_strategy_research_permitted": False,
        "redistribution_permitted": False,
        "observed_data": True,
        "synthetic_or_interpolated": False,
        "symbols": list(RUNTIME_REQUIRED_SYMBOLS),
        "bar_interval": target_interval,
        "source_resolution_seconds": seconds,
        "construction_method": "provider_native",
        "contains_upsampled_rows": False,
        "timestamp_timezone": "UTC",
        "timestamp_semantics": "bar_start",
        "market_hours": "regular_hours",
        "start": "YYYY-MM-DDTHH:MM:SS+00:00",
        "end": "YYYY-MM-DDTHH:MM:SS+00:00",
        "price_adjustment": "split_adjusted",
        "corporate_action_policy": "Describe split/dividend handling and volume adjustment",
        "csv_sha256": "64 lowercase hexadecimal characters",
        "dataset_hash": "GRANDE Alpha canonical aligned-bar SHA-256",
        "row_count": 0,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Dataset manifest must be one JSON object")
    return payload


def manifest_hash(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def provenance_from_manifest(manifest: dict[str, Any]) -> DataProvenance:
    try:
        source_resolution = float(manifest.get("source_resolution_seconds"))
    except (TypeError, ValueError):
        source_resolution = None
    try:
        manifest_version = int(manifest.get("manifest_version", 0))
    except (TypeError, ValueError):
        manifest_version = 0
    return DataProvenance(
        source_kind="imported_manifest",
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
        observed_data=manifest.get("observed_data") is True,
        synthetic_or_interpolated=manifest.get("synthetic_or_interpolated") is not False,
        contains_upsampled_rows=manifest.get("contains_upsampled_rows") is not False,
        construction_method=str(manifest.get("construction_method") or ""),
        source_resolution_seconds=source_resolution,
        bar_interval=str(manifest.get("bar_interval") or ""),
        market_hours=str(manifest.get("market_hours") or ""),
        manifest_version=manifest_version,
        manifest_hash=manifest_hash(manifest),
        csv_sha256=str(manifest.get("csv_sha256") or ""),
        canonical_dataset_hash=str(manifest.get("dataset_hash") or ""),
    )

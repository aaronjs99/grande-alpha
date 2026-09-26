from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from grande_alpha.data.csv_inspection import (
    CsvInspection,
    file_sha256,
    inspect_csv_bytes,
)
from grande_alpha.data.dataset_manifest import (
    CONSTRUCTION_METHODS,
    MANIFEST_REQUIRED_FIELDS,
    MANIFEST_VERSION,
    PRICE_ADJUSTMENTS,
    interval_seconds,
    load_manifest,
    provenance_from_manifest,
)
from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.policy import session_key
from grande_alpha.research.evidence import MIN_EVIDENCE_SESSIONS, MIN_TOTAL_EVIDENCE_SESSIONS
from grande_alpha.research.historical import (
    RUNTIME_REQUIRED_SYMBOLS,
    HistoricalBundle,
)
from grande_alpha.research.historical_source import load_csv_history_bytes
from grande_alpha.research.historical_storage import load_bundle


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    observed: str
    requirement: str




@dataclass(frozen=True)
class DatasetReadinessReport:
    label: str
    source: str
    dataset_hash: str
    interval: str
    market_hours: str
    start: str
    end: str
    aligned_bars: int
    sessions: int
    complete_sessions: int
    session_coverage_pct: float
    missing_intervals: int
    duplicate_timestamps: int
    invalid_session_bars: int
    missing_sessions: int
    zero_volume_bars: int
    observed_cadence_seconds: float | None
    target_interval: str
    checks: tuple[ReadinessCheck, ...]
    load_error: str = ""

    @property
    def input_ready(self) -> bool:
        return not self.load_error and bool(self.checks) and all(check.passed for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "input_ready": self.input_ready,
            "checks": [asdict(check) for check in self.checks],
        }


def _observed_cadence(bundle: HistoricalBundle) -> tuple[float | None, int, int]:
    deltas: list[int] = []
    for previous, current in zip(bundle.frames, bundle.frames[1:], strict=False):
        if session_key(previous.start, bundle.market_hours) != session_key(
            current.start, bundle.market_hours
        ):
            continue
        delta = round((current.start - previous.start).total_seconds())
        if delta > 0:
            deltas.append(delta)
    if not deltas:
        return None, 0, 0
    cadence, count = Counter(deltas).most_common(1)[0]
    return float(cadence), count, len(deltas)


def _manifest_checks(
    manifest: dict[str, Any] | None,
    bundle: HistoricalBundle,
    target_interval: str,
    inspection: CsvInspection | None,
) -> list[ReadinessCheck]:
    if manifest is None:
        return [
            ReadinessCheck(
                "Provenance manifest",
                False,
                "Not supplied",
                "A complete v1 manifest binds source, rights, native cadence, coverage, and hashes",
            )
        ]
    missing = sorted(MANIFEST_REQUIRED_FIELDS - set(manifest))
    try:
        created_at = datetime.fromisoformat(str(manifest.get("created_at", "")).replace("Z", "+00:00"))
        created_at_valid = created_at.tzinfo is not None and created_at.utcoffset() is not None
    except ValueError:
        created_at_valid = False
    schema_values_valid = (
        manifest.get("manifest_version") == MANIFEST_VERSION
        and isinstance(manifest.get("dataset_id"), str)
        and bool(str(manifest.get("dataset_id")).strip())
        and created_at_valid
        and isinstance(manifest.get("redistribution_permitted"), bool)
    )
    checks = [
        ReadinessCheck(
            "Manifest schema",
            not missing and schema_values_valid,
            (
                f"version {manifest.get('manifest_version')}; missing {', '.join(missing)}"
                if missing
                else f"version {manifest.get('manifest_version')}"
            ),
            f"Manifest version {MANIFEST_VERSION} with every required field",
        )
    ]
    rights_ok = all(
        manifest.get(name) is True
        for name in (
            "license_reviewed_by_user",
            "research_use_permitted",
            "automated_strategy_research_permitted",
        )
    ) and all(
        isinstance(manifest.get(name), str) and bool(str(manifest.get(name)).strip())
        for name in ("provider", "provider_product", "acquisition_method", "license_reference")
    )
    checks.append(
        ReadinessCheck(
            "Declared source rights",
            rights_ok,
            (
                "User attested research and automated-strategy-research rights"
                if rights_ok
                else "Required source/right attestations are incomplete"
            ),
            "User-reviewed license permits this research use; the app cannot independently give legal clearance",
        )
    )
    target_seconds = interval_seconds(target_interval)
    try:
        source_seconds = float(manifest.get("source_resolution_seconds"))
    except (TypeError, ValueError):
        source_seconds = math.inf
    observed_ok = (
        manifest.get("observed_data") is True
        and manifest.get("synthetic_or_interpolated") is False
        and manifest.get("contains_upsampled_rows") is False
        and manifest.get("construction_method") in CONSTRUCTION_METHODS
        and math.isfinite(source_seconds)
        and 0 < source_seconds <= target_seconds
    )
    checks.append(
        ReadinessCheck(
            "No coarse-data masquerade",
            observed_ok,
            (
                f"method={manifest.get('construction_method')}; source resolution="
                f"{manifest.get('source_resolution_seconds')}s; upsampled="
                f"{manifest.get('contains_upsampled_rows')}"
            ),
            f"Observed source resolution is at most {target_seconds}s with no interpolation or upsampling",
        )
    )
    scope_ok = (
        manifest.get("symbols") == list(RUNTIME_REQUIRED_SYMBOLS)
        and manifest.get("bar_interval") == bundle.interval == target_interval
        and manifest.get("timestamp_timezone") == "UTC"
        and manifest.get("timestamp_semantics") == "bar_start"
        and manifest.get("market_hours") == bundle.market_hours
        and manifest.get("start") == bundle.start.isoformat()
        and manifest.get("end") == bundle.end.isoformat()
        and manifest.get("price_adjustment") in PRICE_ADJUSTMENTS
        and isinstance(manifest.get("corporate_action_policy"), str)
        and bool(str(manifest.get("corporate_action_policy")).strip())
    )
    checks.append(
        ReadinessCheck(
            "Manifest scope binding",
            scope_ok,
            (
                f"symbols={manifest.get('symbols')}; interval={manifest.get('bar_interval')}; "
                f"hours={manifest.get('market_hours')}"
            ),
            "Manifest exactly matches symbols, interval, UTC bar-start timestamps, coverage, dates, and adjustments",
        )
    )
    expected_file_hash = inspection.file_sha256 if inspection else None
    expected_rows = inspection.row_count if inspection else None
    hashes_ok = (
        manifest.get("dataset_hash") == bundle.dataset_hash
        and (expected_file_hash is None or manifest.get("csv_sha256") == expected_file_hash)
        and (expected_rows is None or manifest.get("row_count") == expected_rows)
    )
    checks.append(
        ReadinessCheck(
            "Manifest content binding",
            hashes_ok,
            (
                f"dataset={str(manifest.get('dataset_hash', ''))[:16]}…; "
                f"file={str(manifest.get('csv_sha256', ''))[:16]}…; rows={manifest.get('row_count')}"
            ),
            "Canonical dataset hash, raw CSV SHA-256, and exact source-row count all match",
        )
    )
    return checks


def audit_bundle(
    bundle: HistoricalBundle,
    *,
    label: str,
    target_interval: str = "5s",
    manifest: dict[str, Any] | None = None,
    inspection: CsvInspection | None = None,
    now: datetime | None = None,
) -> DatasetReadinessReport:
    if manifest is not None:
        bundle = replace(bundle, provenance=provenance_from_manifest(manifest))
    quality = bundle.quality
    if quality is None:
        raise ValueError("Historical bundle has no data-quality assessment")
    target_seconds = interval_seconds(target_interval)
    observed_cadence, cadence_count, cadence_total = _observed_cadence(bundle)
    if target_interval == "1d":
        exact_cadence = bundle.interval == "1d" and quality.sessions == len(bundle.frames)
    else:
        exact_cadence = (
            bundle.interval == target_interval
            and observed_cadence == target_seconds
            and cadence_total > 0
            and cadence_count == cadence_total
        )
    reference = now or utc_now()
    age_days = (reference - bundle.end).total_seconds() / 86_400
    aligned_symbols = all(
        (frame.qqq.symbol, frame.tqqq.symbol, frame.sqqq.symbol) == RUNTIME_REQUIRED_SYMBOLS
        for frame in bundle.frames
    )
    provenance = bundle.provenance
    source_observed = bundle.evidence_provenance_eligible
    checks = [
        ReadinessCheck(
            "Observed source classification",
            source_observed,
            (
                f"kind={provenance.source_kind}; eligible={source_observed}; "
                f"provenance={bundle.provenance_hash[:16]}..."
                if provenance is not None
                else "No machine-readable provenance"
            ),
            "Manifest-bound observed market data with user-attested research rights; labels are ignored",
        ),
        ReadinessCheck(
            "Exact native cadence",
            exact_cadence,
            (
                f"declared {bundle.interval}; observed mode "
                f"{observed_cadence:g}s across {cadence_total} within-session gaps"
                if observed_cadence is not None
                else f"declared {bundle.interval}; no intraday cadence available"
            ),
            f"Native or finer observed input produces exact {target_interval} bars; no 1m/daily relabeling",
        ),
        ReadinessCheck(
            "Aligned symbol triples",
            aligned_symbols and quality.aligned_bars == len(bundle.frames),
            f"{quality.aligned_bars} aligned QQQ/TQQQ/SQQQ timestamps",
            "Exactly one valid bar for every required symbol at each timestamp",
        ),
        ReadinessCheck(
            "Data breadth",
            quality.sessions >= MIN_TOTAL_EVIDENCE_SESSIONS,
            f"{quality.sessions} sessions",
            f"At least {MIN_TOTAL_EVIDENCE_SESSIONS} total: {MIN_EVIDENCE_SESSIONS} development, one purge, and 20 final holdout",
        ),
        ReadinessCheck(
            "Data recency",
            0 <= age_days <= 30,
            f"{age_days:.1f} days old",
            "Final observation no more than 30 days old",
        ),
        ReadinessCheck(
            "Data integrity",
            (
                quality.clean
                and quality.missing_intervals == 0
                and quality.missing_sessions == 0
                and quality.session_coverage_pct >= 95.0
            ),
            (
                f"{quality.missing_intervals} missing bars; "
                f"{quality.missing_sessions} missing sessions; "
                f"{quality.duplicate_timestamps} duplicate; "
                f"{quality.invalid_session_bars} closed-session bars; "
                f"{quality.session_coverage_pct:.1f}% complete sessions"
            ),
            "Hash-valid, zero omitted exchange sessions or missing/duplicate intervals, and at least 95% complete selected sessions",
        ),
    ]
    if inspection is not None:
        raw_ok = (
            inspection.invalid_rows == 0
            and inspection.duplicate_keys == 0
            and inspection.incomplete_timestamps == 0
            and inspection.out_of_session_rows == 0
            and inspection.timezone_aware_timestamps
            and set(inspection.headers)
            == {"timestamp", "symbol", "open", "high", "low", "close", "volume", "market_hours"}
            and len(inspection.headers) == 8
            and set(inspection.symbols) == set(RUNTIME_REQUIRED_SYMBOLS)
            and inspection.market_hours == (bundle.market_hours,)
        )
        checks.append(
            ReadinessCheck(
                "Raw CSV row integrity",
                raw_ok,
                (
                    f"{inspection.row_count} rows; {inspection.invalid_rows} invalid; "
                    f"{inspection.duplicate_keys} duplicate keys; "
                    f"{inspection.incomplete_timestamps} incomplete timestamps; "
                    f"{inspection.out_of_session_rows} outside declared session"
                ),
                "Every row has aware time, valid OHLCV, one symbol key, and every timestamp has all three symbols",
            )
        )
    checks.extend(_manifest_checks(manifest, bundle, target_interval, inspection))
    return DatasetReadinessReport(
        label=label,
        source=bundle.source,
        dataset_hash=bundle.dataset_hash,
        interval=bundle.interval,
        market_hours=bundle.market_hours,
        start=bundle.start.isoformat(),
        end=bundle.end.isoformat(),
        aligned_bars=quality.aligned_bars,
        sessions=quality.sessions,
        complete_sessions=quality.complete_sessions,
        session_coverage_pct=quality.session_coverage_pct,
        missing_intervals=quality.missing_intervals,
        duplicate_timestamps=quality.duplicate_timestamps,
        invalid_session_bars=quality.invalid_session_bars,
        missing_sessions=quality.missing_sessions,
        zero_volume_bars=quality.zero_volume_bars,
        observed_cadence_seconds=observed_cadence,
        target_interval=target_interval,
        checks=tuple(checks),
    )


def audit_csv_dataset(
    csv_path: Path,
    interval: str,
    *,
    target_interval: str = "5s",
    manifest_path: Path | None = None,
    now: datetime | None = None,
) -> DatasetReadinessReport:
    _, report = load_audited_csv_dataset(
        csv_path,
        interval,
        target_interval=target_interval,
        manifest_path=manifest_path,
        now=now,
    )
    return report


def load_audited_csv_dataset(
    csv_path: Path,
    interval: str,
    *,
    target_interval: str = "5s",
    manifest_path: Path | None = None,
    now: datetime | None = None,
) -> tuple[HistoricalBundle, DatasetReadinessReport]:
    raw_csv = csv_path.read_bytes()
    inspection = inspect_csv_bytes(raw_csv)
    bundle = load_csv_history_bytes(raw_csv, csv_path.name, interval)
    final_file_hash = file_sha256(csv_path)
    if final_file_hash != inspection.file_sha256:
        raise ValueError(
            "CSV changed during readiness audit; retry with one immutable source snapshot"
        )
    manifest = load_manifest(manifest_path) if manifest_path else None
    if manifest is not None:
        bundle = replace(bundle, provenance=provenance_from_manifest(manifest))
    report = audit_bundle(
        bundle,
        label=csv_path.name,
        target_interval=target_interval,
        manifest=manifest,
        inspection=inspection,
        now=now,
    )
    return bundle, report


def audit_cache_directory(
    cache_dir: Path,
    *,
    target_interval: str = "5s",
    now: datetime | None = None,
) -> list[DatasetReadinessReport]:
    reports: list[DatasetReadinessReport] = []
    if not cache_dir.exists():
        return reports
    for path in sorted(cache_dir.glob("*.json")):
        if path.name.endswith(".manifest.json"):
            continue
        try:
            bundle = load_bundle(path)
            reports.append(
                audit_bundle(bundle, label=path.name, target_interval=target_interval, now=now)
            )
        except Exception as exc:
            reports.append(
                DatasetReadinessReport(
                    label=path.name,
                    source="Unreadable cache",
                    dataset_hash="",
                    interval="",
                    market_hours="",
                    start="",
                    end="",
                    aligned_bars=0,
                    sessions=0,
                    complete_sessions=0,
                    session_coverage_pct=0.0,
                    missing_intervals=0,
                    duplicate_timestamps=0,
                    invalid_session_bars=0,
                    missing_sessions=0,
                    zero_volume_bars=0,
                    observed_cadence_seconds=None,
                    target_interval=target_interval,
                    checks=(),
                    load_error=f"{type(exc).__name__}: {exc}",
                )
            )
    return reports

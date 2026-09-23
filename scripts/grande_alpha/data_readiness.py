from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grande_alpha.evidence import (
    EVIDENCE_POLICY_VERSION,
    MIN_EVIDENCE_SESSIONS,
    MIN_TOTAL_EVIDENCE_SESSIONS,
)
from grande_alpha.historical import (
    INTERVAL_SECONDS,
    DataProvenance,
    HistoricalBundle,
    load_bundle,
    load_csv_history_bytes,
)
from grande_alpha.models import utc_now
from grande_alpha.policy import session_bounds, session_key

EXPECTED_SYMBOLS = ("QQQ", "TQQQ", "SQQQ")
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


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    observed: str
    requirement: str


@dataclass(frozen=True)
class CsvInspection:
    file_sha256: str
    row_count: int
    invalid_rows: int
    duplicate_keys: int
    incomplete_timestamps: int
    out_of_session_rows: int
    headers: tuple[str, ...]
    symbols: tuple[str, ...]
    market_hours: tuple[str, ...]
    timezone_aware_timestamps: bool


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


def manifest_template(target_interval: str = "5s") -> dict[str, Any]:
    seconds = _interval_seconds(target_interval)
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
        "symbols": list(EXPECTED_SYMBOLS),
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


def _interval_seconds(interval: str) -> int:
    if interval in INTERVAL_SECONDS:
        return INTERVAL_SECONDS[interval]
    if interval.endswith("s") and interval[:-1].isdigit() and int(interval[:-1]) > 0:
        return int(interval[:-1])
    raise ValueError(f"Unsupported interval: {interval}")


def _parse_aware_timestamp(raw: str) -> datetime:
    value = raw.strip()
    if value.replace(".", "", 1).isdigit():
        raise ValueError("readiness CSV timestamps must be ISO 8601, not epoch numbers")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp has no UTC offset")
    if timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp is aware but is not expressed in UTC")
    return timestamp.astimezone(UTC)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bytes_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def inspect_csv_bytes(raw_csv: bytes) -> CsvInspection:
    """Inspect one immutable byte snapshot without silently discarding bad rows."""

    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume", "market_hours"}
    row_count = 0
    invalid_rows = 0
    duplicate_keys = 0
    out_of_session_rows = 0
    timezone_aware = True
    seen: set[tuple[datetime, str]] = set()
    symbols: set[str] = set()
    coverages: set[str] = set()
    timestamp_symbols: dict[datetime, set[str]] = defaultdict(set)
    with io.StringIO(raw_csv.decode("utf-8-sig"), newline="") as handle:
        reader = csv.DictReader(handle)
        headers = tuple(str(value).strip().lower() for value in (reader.fieldnames or ()))
        if not required <= set(headers):
            missing = ", ".join(sorted(required - set(headers)))
            raise ValueError(f"CSV readiness schema is missing: {missing}")
        for row in reader:
            row_count += 1
            normalized = {str(key).strip().lower(): str(value or "").strip() for key, value in row.items()}
            symbol = normalized["symbol"].upper()
            coverage = normalized["market_hours"]
            symbols.add(symbol)
            coverages.add(coverage)
            try:
                timestamp = _parse_aware_timestamp(normalized["timestamp"])
            except (OSError, OverflowError, ValueError):
                invalid_rows += 1
                timezone_aware = False
                continue
            try:
                opened, high, low, close, volume = (
                    float(normalized[name])
                    for name in ("open", "high", "low", "close", "volume")
                )
            except ValueError:
                invalid_rows += 1
                continue
            numeric = (opened, high, low, close, volume)
            valid_ohlcv = (
                all(math.isfinite(value) for value in numeric)
                and min(opened, high, low, close) > 0
                and volume >= 0
                and low <= min(opened, close)
                and high >= max(opened, close)
                and low <= high
            )
            if (
                symbol not in EXPECTED_SYMBOLS
                or coverage not in {"regular_hours", "extended_hours", "all_day_hours"}
                or not valid_ohlcv
            ):
                invalid_rows += 1
                continue
            opened_at, closed_at = session_bounds(timestamp, coverage)
            if not opened_at <= timestamp.astimezone(opened_at.tzinfo) < closed_at:
                out_of_session_rows += 1
            key = (timestamp, symbol)
            if key in seen:
                duplicate_keys += 1
            seen.add(key)
            timestamp_symbols[timestamp].add(symbol)
    incomplete = sum(set(EXPECTED_SYMBOLS) != values for values in timestamp_symbols.values())
    if row_count == 0:
        raise ValueError("CSV readiness audit requires at least one data row")
    return CsvInspection(
        file_sha256=_bytes_sha256(raw_csv),
        row_count=row_count,
        invalid_rows=invalid_rows,
        duplicate_keys=duplicate_keys,
        incomplete_timestamps=incomplete,
        out_of_session_rows=out_of_session_rows,
        headers=headers,
        symbols=tuple(sorted(symbols)),
        market_hours=tuple(sorted(coverages)),
        timezone_aware_timestamps=timezone_aware,
    )


def inspect_csv(path: Path) -> CsvInspection:
    """Inspect a CSV from one file read.

    This convenience wrapper is suitable for manifest preparation. Evidence qualification
    calls :func:`inspect_csv_bytes` and the historical parser on the same byte snapshot.
    """

    return inspect_csv_bytes(path.read_bytes())


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Dataset manifest must be one JSON object")
    return payload


def _manifest_hash(manifest: dict[str, Any]) -> str:
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
        manifest_hash=_manifest_hash(manifest),
        csv_sha256=str(manifest.get("csv_sha256") or ""),
        canonical_dataset_hash=str(manifest.get("dataset_hash") or ""),
    )


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
    target_seconds = _interval_seconds(target_interval)
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
        manifest.get("symbols") == list(EXPECTED_SYMBOLS)
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
    target_seconds = _interval_seconds(target_interval)
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
        (frame.qqq.symbol, frame.tqqq.symbol, frame.sqqq.symbol) == EXPECTED_SYMBOLS
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
            and set(inspection.symbols) == set(EXPECTED_SYMBOLS)
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
    final_file_hash = _file_sha256(csv_path)
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


def audit_evidence_ledger(database_path: Path) -> dict[str, Any]:
    """Return a query-only ledger inventory without running migrations or reserving a holdout."""

    result: dict[str, Any] = {
        "database": str(database_path),
        "exists": database_path.is_file(),
        "read_only": True,
        "policy_version": EVIDENCE_POLICY_VERSION,
        "trials": 0,
        "trial_datasets": 0,
        "promotions": 0,
        "promotion_statuses": {},
        "promotion_policy_versions": {},
        "holdouts": 0,
        "holdout_statuses": {},
        "latest_promotion": None,
        "runtime_trace": {
            "quotes": 0,
            "quote_symbols": {},
            "quote_start": None,
            "quote_end": None,
            "balanced_required_symbols": False,
            "bars": 0,
            "bar_symbols": {},
            "bar_start": None,
            "bar_end": None,
            "eligible_historical_bundle": False,
            "reason": (
                "Runtime trace is collection progress only: aligned OHLCV bars for QQQ/TQQQ/SQQQ, "
                "complete sessions, exact construction, and manifest-bound provenance are not established"
            ),
        },
    }
    if not database_path.is_file():
        return result
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "research_trials" in tables:
            row = connection.execute(
                "SELECT COUNT(*) AS n,COUNT(DISTINCT dataset_hash) AS datasets FROM research_trials"
            ).fetchone()
            result["trials"] = int(row["n"])
            result["trial_datasets"] = int(row["datasets"])
        if "research_promotions" in tables:
            result["promotions"] = int(
                connection.execute("SELECT COUNT(*) AS n FROM research_promotions").fetchone()["n"]
            )
            result["promotion_statuses"] = {
                row["status"]: int(row["n"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS n FROM research_promotions GROUP BY status"
                )
            }
            result["promotion_policy_versions"] = {
                str(row["policy_version"]): int(row["n"])
                for row in connection.execute(
                    "SELECT policy_version,COUNT(*) AS n FROM research_promotions GROUP BY policy_version"
                )
            }
            latest = connection.execute(
                """SELECT id,created_at,dataset_hash,policy_version,status,source,replay_end,holdout_id
                FROM research_promotions ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            result["latest_promotion"] = dict(latest) if latest is not None else None
        if "research_holdouts" in tables:
            result["holdouts"] = int(
                connection.execute("SELECT COUNT(*) AS n FROM research_holdouts").fetchone()["n"]
            )
            result["holdout_statuses"] = {
                row["status"]: int(row["n"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS n FROM research_holdouts GROUP BY status"
                )
            }
        trace = result["runtime_trace"]
        if "quotes" in tables:
            quote_summary = connection.execute(
                """SELECT COUNT(*) AS n,MIN(venue_timestamp) AS started,
                MAX(venue_timestamp) AS ended FROM quotes"""
            ).fetchone()
            trace["quotes"] = int(quote_summary["n"])
            trace["quote_start"] = quote_summary["started"]
            trace["quote_end"] = quote_summary["ended"]
            trace["quote_symbols"] = {
                row["symbol"]: int(row["n"])
                for row in connection.execute(
                    "SELECT symbol,COUNT(*) AS n FROM quotes GROUP BY symbol ORDER BY symbol"
                )
            }
            required_counts = [trace["quote_symbols"].get(symbol, 0) for symbol in EXPECTED_SYMBOLS]
            trace["balanced_required_symbols"] = bool(
                required_counts and min(required_counts) > 0 and len(set(required_counts)) == 1
            )
        if "bars" in tables:
            bar_summary = connection.execute(
                "SELECT COUNT(*) AS n,MIN(start_at) AS started,MAX(start_at) AS ended FROM bars"
            ).fetchone()
            trace["bars"] = int(bar_summary["n"])
            trace["bar_start"] = bar_summary["started"]
            trace["bar_end"] = bar_summary["ended"]
            trace["bar_symbols"] = {
                row["symbol"]: int(row["n"])
                for row in connection.execute(
                    "SELECT symbol,COUNT(*) AS n FROM bars GROUP BY symbol ORDER BY symbol"
                )
            }
        return result
    finally:
        connection.close()

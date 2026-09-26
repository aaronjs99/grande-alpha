from __future__ import annotations

import csv
import hashlib
import io
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from grande_alpha.domain.policy import session_bounds
from grande_alpha.research.historical import RUNTIME_REQUIRED_SYMBOLS as EXPECTED_SYMBOLS


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


def file_sha256(path: Path) -> str:
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

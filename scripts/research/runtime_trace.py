from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.market_models import EXACT_QUOTE_VALIDATOR_VERSION, Bar, Quote
from grande_alpha.domain.policy import EASTERN, market_session_allowed, session_bounds, session_key
from grande_alpha.persistence.market import QUOTE_BATCH_SCHEMA_VERSION
from grande_alpha.research.historical import assess_quality
from grande_alpha.research.historical_models import (
    RUNTIME_REQUIRED_SYMBOLS,
    HistoricalBundle,
    ReplayFrame,
)
from grande_alpha.research.runtime_trace_provenance import _runtime_trace_provenance
from grande_alpha.strategy.core import BarBuilder


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
        exact_batch_count = int(
            connection.execute(
                """SELECT COUNT(*) AS n FROM quote_batches b
                WHERE validation_profile='exact_execution_quotes' AND schema_version=?
                  AND validation_version=? AND max_age_seconds IS NOT NULL
                  AND max_skew_seconds IS NOT NULL"""
                + range_sql.replace("observed_at", "b.observed_at"),
                (QUOTE_BATCH_SCHEMA_VERSION, EXACT_QUOTE_VALIDATOR_VERSION, *range_parameters),
            ).fetchone()["n"]
        )
        cursor = connection.execute(
            """SELECT b.rowid AS batch_sequence,b.batch_id,b.stream_id,
            b.observed_at AS batch_observed_at,b.schema_version,b.symbol_count,
            b.validation_profile,b.validation_version,b.max_age_seconds,b.max_skew_seconds,
            q.id,q.observed_at,q.symbol,q.bid,q.ask,q.last,q.venue_timestamp,
            q.bid_timestamp,q.ask_timestamp,q.batch_position
            FROM quote_batches b JOIN quotes q ON q.batch_id=b.batch_id
            WHERE b.validation_profile='exact_execution_quotes' AND b.schema_version=?
              AND b.validation_version=? AND b.max_age_seconds IS NOT NULL
              AND b.max_skew_seconds IS NOT NULL AND q.batch_position IS NOT NULL"""
            + range_sql.replace("observed_at", "b.observed_at")
            + " ORDER BY b.rowid,q.batch_position",
            (QUOTE_BATCH_SCHEMA_VERSION, EXACT_QUOTE_VALIDATOR_VERSION, *range_parameters),
        )

        trace_digest = hashlib.sha256()
        frames: list[ReplayFrame] = []
        sessions_by_stream: dict[str, set[str]] = {}
        builder = BarBuilder("QQQ", bar_seconds)
        active_stream: str | None = None
        last_qqq_timestamp: datetime | None = None
        previous_eligible: tuple[str, str, datetime] | None = None
        source_resolution_seconds: float | None = None
        validator_envelope: tuple[float, float] | None = None
        current_batch_id: str | None = None
        chunk: list[sqlite3.Row] = []
        processed_batches = 0
        quote_row_count = 0

        def point_bar(symbol: str, quote: Quote, start_at: datetime) -> Bar:
            price = quote.mid
            return Bar(symbol, start_at, price, price, price, price, 1, 0.0)

        def process_chunk(batch_rows: list[sqlite3.Row]) -> None:
            nonlocal active_stream, builder, last_qqq_timestamp, previous_eligible
            nonlocal source_resolution_seconds, validator_envelope, processed_batches
            if len(batch_rows) != len(RUNTIME_REQUIRED_SYMBOLS):
                raise ValueError("Runtime quote batch is interrupted or incomplete")
            record = batch_rows[0]
            batch_id = str(record["batch_id"])
            batch_sequence = int(record["batch_sequence"])
            stream_id = str(record["stream_id"]).strip()
            schema_version = int(record["schema_version"])
            symbol_count = int(record["symbol_count"])
            validation_profile = str(record["validation_profile"])
            validation_version = int(record["validation_version"])
            envelope = (float(record["max_age_seconds"]), float(record["max_skew_seconds"]))
            if validator_envelope is None:
                validator_envelope = envelope
            elif validator_envelope != envelope:
                raise ValueError("Exact runtime trace must use one validator age/skew envelope")
            max_age, max_skew = envelope
            if not 0 < max_age <= 8.0 or not 0 < max_skew <= min(5.0, max_age):
                raise ValueError("Exact runtime trace validator envelope is unsupported")
            if not stream_id:
                raise ValueError("Runtime quote batch lacks a bound signal-pipeline stream ID")
            if (
                schema_version != QUOTE_BATCH_SCHEMA_VERSION
                or validation_profile != "exact_execution_quotes"
                or validation_version != EXACT_QUOTE_VALIDATOR_VERSION
                or symbol_count != len(RUNTIME_REQUIRED_SYMBOLS)
            ):
                raise ValueError("Runtime quote batch has an unsupported or inconsistent schema")
            if [int(row["batch_position"]) for row in batch_rows] != [0, 1, 2]:
                raise ValueError("Runtime quote batch positions must be exactly 0, 1, and 2")
            batch_observed_at = _aware_trace_timestamp(record["batch_observed_at"], "batch observed_at")
            observed_times: list[datetime] = []
            quotes: dict[str, Quote] = {}
            for row in batch_rows:
                observed_at = _aware_trace_timestamp(row["observed_at"], "observed_at")
                if observed_at != batch_observed_at:
                    raise ValueError("Runtime quote child does not match its atomic batch timestamp")
                venue_timestamp = _aware_trace_timestamp(row["venue_timestamp"], "venue_timestamp")
                bid_timestamp = _aware_trace_timestamp(row["bid_timestamp"], "bid_timestamp")
                ask_timestamp = _aware_trace_timestamp(row["ask_timestamp"], "ask_timestamp")
                symbol = str(row["symbol"]).upper()
                quote = Quote(symbol, float(row["bid"]), float(row["ask"]), float(row["last"]), venue_timestamp, bid_timestamp, ask_timestamp)
                quote.validate()
                if symbol in quotes:
                    raise ValueError("Runtime quote batch contains a duplicate symbol")
                quotes[symbol] = quote
                observed_times.append(observed_at)
                trace_digest.update(
                    f"{batch_sequence}|{stream_id}|{batch_id}|{schema_version}|{symbol_count}|"
                    f"{validation_profile}|{validation_version}|{max_age:.6f}|{max_skew:.6f}|"
                    f"{row['batch_position']}|{row['id']}|{observed_at.isoformat()}|{symbol}|"
                    f"{quote.bid:.8f}|{quote.ask:.8f}|{quote.last:.8f}|{venue_timestamp.isoformat()}|"
                    f"{bid_timestamp.isoformat()}|{ask_timestamp.isoformat()}\n".encode()
                )
            if tuple(sorted(quotes)) != tuple(sorted(RUNTIME_REQUIRED_SYMBOLS)):
                raise ValueError("Runtime quote batch must contain exactly QQQ, TQQQ, and SQQQ")
            if (max(observed_times) - min(observed_times)).total_seconds() > 2.0:
                raise ValueError("Runtime quote rows are not one synchronized recorder batch")
            book_times = [timestamp for quote in quotes.values() for timestamp in (quote.bid_timestamp, quote.ask_timestamp) if timestamp is not None]
            ages = [(batch_observed_at - timestamp).total_seconds() for timestamp in book_times]
            if any(age < -2.0 or age > max_age for age in ages):
                raise ValueError("Runtime quote batch violates its bound validator age envelope")
            if (max(book_times) - min(book_times)).total_seconds() > max_skew:
                raise ValueError("Runtime quote batch violates its bound validator skew envelope")
            processed_batches += 1
            if not all(
                market_session_allowed(timestamp, 0, 0, market_hours)
                for quote in quotes.values()
                for timestamp in (quote.bid_timestamp, quote.ask_timestamp)
            ):
                return
            qqq_observed_at = quotes["QQQ"].latest_book_timestamp
            if qqq_observed_at is None:
                raise ValueError("Runtime QQQ quote lacks exact book observation time")
            current_session = session_key(qqq_observed_at, market_hours)
            sessions_by_stream.setdefault(stream_id, set()).add(current_session)
            if previous_eligible is not None:
                previous_stream, previous_session, previous_time = previous_eligible
                if stream_id == previous_stream and current_session == previous_session and qqq_observed_at > previous_time:
                    delta_seconds = (qqq_observed_at - previous_time).total_seconds()
                    source_resolution_seconds = max(source_resolution_seconds or 0.0, delta_seconds)
            previous_eligible = (stream_id, current_session, qqq_observed_at)
            if active_stream != stream_id:
                builder = BarBuilder("QQQ", bar_seconds)
                active_stream = stream_id
                last_qqq_timestamp = None
            if last_qqq_timestamp is not None and qqq_observed_at <= last_qqq_timestamp:
                return
            last_qqq_timestamp = qqq_observed_at
            completed = builder.update(replace(quotes["QQQ"], timestamp=qqq_observed_at))
            if completed is None:
                return
            causal_timestamp = max(quotes[symbol].latest_book_timestamp for symbol in RUNTIME_REQUIRED_SYMBOLS)
            if causal_timestamp <= completed.start:
                raise ValueError("Runtime causal quote must be later than its completed analysis bar")
            frames.append(ReplayFrame(completed.start, completed, point_bar("TQQQ", quotes["TQQQ"], completed.start), point_bar("SQQQ", quotes["SQQQ"], completed.start), causal_timestamp, quotes["QQQ"], quotes["TQQQ"], quotes["SQQQ"], stream_id))

        for row in cursor:
            quote_row_count += 1
            row_batch_id = str(row["batch_id"])
            if current_batch_id is not None and row_batch_id != current_batch_id:
                process_chunk(chunk)
                chunk = []
            current_batch_id = row_batch_id
            chunk.append(row)
        if chunk:
            process_chunk(chunk)
        if exact_batch_count < 2 or quote_row_count < 6:
            raise ValueError("Runtime quote trace needs at least two synchronized quote batches")
        if processed_batches != exact_batch_count:
            raise ValueError("Runtime quote rows reference a missing or childless provider batch")
        if any(len(sessions) > 1 for sessions in sessions_by_stream.values()):
            raise ValueError("Runtime quote stream spans multiple sessions without a signal-pipeline reset")
        if validator_envelope is None:
            raise ValueError("Runtime quote trace has no validator envelope")
        validator_max_age_seconds, validator_max_skew_seconds = validator_envelope
        source_resolution_seconds = source_resolution_seconds or float(bar_seconds)
    finally:
        connection.close()
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
        quote_row_count=quote_row_count,
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
        quote_row_count,
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

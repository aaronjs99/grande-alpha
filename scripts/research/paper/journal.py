"""Read saved virtual fills without opening or resuming the writable paper ledger."""

from __future__ import annotations

import contextlib
import csv
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from decimal import Context, Decimal, DecimalException, localcontext
from pathlib import Path

MAX_SESSIONS = 100
MAX_PAGE = 100
MAX_SESSION_BYTES = 1_048_576
MAX_FILL_BYTES = 16_384
MAX_INTEGER = 2**63 - 1
DECIMAL_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
LIMITATIONS = {
    "demo": "Saved synthetic demo; not historical market data or performance evidence. Marks may be stale. Virtual fills omit fees and liquidity.",
    "broker_quotes": "Saved forward paper observations; marks may be stale. Virtual fills omit fees and liquidity and do not validate a trading edge or broker execution.",
}
CSV_COLUMNS = ("session_id", "source", "session_created_at", "session_fill_count", "number", "key", "side",
               "quantity", "price", "realized_pnl", "filled_at")


def _text(value, name: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"Paper journal {name} must be bounded, nonempty text")
    return value


def _integer(value, name: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValueError(f"Paper journal {name} must be an integer from {minimum} to {MAX_INTEGER}")
    return value


def _decimal(value, name: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str) or len(value) > 128 or not DECIMAL_PATTERN.fullmatch(value):
        raise ValueError(f"Paper journal {name} must be a bounded finite decimal string")
    try:
        number = Decimal(value)
    except DecimalException as error:
        raise ValueError(f"Paper journal {name} must be a bounded finite decimal string") from error
    if (not number.is_finite() or abs(number.adjusted()) > 128 or abs(number.as_tuple().exponent) > 128
            or (positive and number <= 0) or (nonnegative and number < 0)):
        raise ValueError(f"Paper journal {name} is outside the supported finite decimal range")
    return number


def _timestamp(value, name: str) -> str:
    value = _text(value, name, 64)
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Paper journal {name} must be an ISO timestamp with a timezone") from error
    if len(value) < 11 or value[10] not in {"T", " "} or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"Paper journal {name} must be an ISO timestamp with a timezone")
    return value


def _unique_object(pairs) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Paper journal JSON contains duplicate fields")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Paper journal JSON contains a nonfinite number")


def _payload(raw, size, maximum: int) -> dict:
    if type(size) is not int or size > maximum:
        raise ValueError("Paper journal record is missing or exceeds its payload size limit")
    if not isinstance(raw, str):
        raise ValueError("Paper journal record must contain JSON text")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant,
                           parse_float=Decimal)
    except (ValueError, RecursionError, DecimalException) as error:
        raise ValueError("Paper journal record contains malformed or ambiguous JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Paper journal record must be a JSON object")
    return value


def _payload_sql(maximum: int) -> str:
    # Do not transfer an oversized SQLite value into Python before checking it.
    return (f"CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<={maximum} "
            "THEN payload END, length(CAST(payload AS BLOB))")


def _summary(raw: dict, session_id: str) -> dict:
    if _text(raw.get("session_id"), "session ID", 128) != session_id:
        raise ValueError("Paper journal session ID does not match its saved row")
    source = raw.get("source")
    if not isinstance(source, str) or source not in LIMITATIONS:
        raise ValueError("Paper journal source must be demo or broker_quotes")
    created = _timestamp(raw.get("created_at"), "creation time")
    observed = raw.get("observed_at")
    if observed is not None:
        _timestamp(observed, "observation time")
    initial = _decimal(raw.get("initial_cash"), "initial cash", positive=True)
    allocation = _decimal(raw.get("trade_cash"), "trade cash", positive=True)
    cash = _decimal(raw.get("cash"), "cash", nonnegative=True)
    _decimal(raw.get("realized_pnl"), "realized P/L")
    if not Decimal(1) <= initial <= Decimal(1_000_000) or not Decimal(1) <= allocation <= initial:
        raise ValueError("Paper journal virtual cash limits are inconsistent")
    result = {"session_id": session_id, "source": source, "created_at": created, "observed_at": observed,
              **{name: raw[name] for name in ("initial_cash", "cash", "trade_cash", "realized_pnl")},
              "fill_count": _integer(raw.get("fill_count"), "fill count"), "limitations": LIMITATIONS[source]}
    if "positions" in raw:
        positions = raw["positions"]
        if not isinstance(positions, dict) or len(positions) > 1000:
            raise ValueError("Paper journal saved positions must be a bounded object")
        # Bounded operands need fewer than 600 digits for an exact sum/product.
        # This context cannot round the ledger's saved decimal values to floats.
        with localcontext(Context(prec=1024)):
            equity = cash
            for holding in positions.values():
                if not isinstance(holding, dict):
                    raise ValueError("Paper journal saved position is malformed")
                quantity = _decimal(holding.get("quantity"), "position quantity", positive=True)
                bid = _decimal(holding.get("bid"), "saved bid", positive=True)
                equity += quantity * bid
        result["equity"] = raw["cash"] if not positions else str(equity)
    return result


def _fill(raw: dict, number: int) -> dict:
    if _integer(raw.get("number"), "fill number", 1) != number:
        raise ValueError("Paper journal fill number does not match its saved row")
    side = raw.get("side")
    if not isinstance(side, str) or side not in {"buy", "sell"}:
        raise ValueError("Paper journal fill side must be buy or sell")
    _decimal(raw.get("quantity"), "fill quantity", positive=True)
    _decimal(raw.get("price"), "fill price", positive=True)
    _decimal(raw.get("realized_pnl"), "fill realized P/L")
    return {"number": number, "key": _text(raw.get("key"), "instrument"), "side": side,
            **{name: raw[name] for name in ("quantity", "price", "realized_pnl")},
            "filled_at": _timestamp(raw.get("filled_at"), "fill time")}


def read_journal(path: Path, *, session_id: str | None = None, before: int | None = None,
                 limit: int = MAX_PAGE) -> dict:
    """Return one immutable-in-time page; never create, migrate, or resume a ledger.

    Session order follows ledger insertion order. Fill numbers descend, and
    ``before`` is exclusive. Unknown sessions have no selected page. Private
    ``_journal_path`` provenance must travel with the snapshot to CSV export.
    """
    if session_id is not None:
        _text(session_id, "selected session ID", 128)
    if before is not None:
        _integer(before, "page cursor", 1)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError("Paper journal page size must be between 1 and 100")
    source = Path(path).resolve()
    result = {"sessions": [], "session": None, "fills": [], "fill_count": 0, "next_before": None,
              "_journal_path": str(source)}
    try:
        source.stat()
    except FileNotFoundError:
        return result
    try:
        with contextlib.closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=.1)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            summaries = {}
            for saved_id, raw, size in db.execute(
                    f"SELECT id, {_payload_sql(MAX_SESSION_BYTES)} FROM paper_sessions ORDER BY rowid DESC LIMIT ?",
                    (MAX_SESSIONS,)):
                saved_id = _text(saved_id, "saved session ID", 128)
                if saved_id in summaries:
                    raise ValueError("Paper journal contains duplicate session IDs")
                summary = _summary(_payload(raw, size, MAX_SESSION_BYTES), saved_id)
                summaries[saved_id] = summary
                result["sessions"].append({name: summary[name] for name in ("session_id", "source", "created_at")})
            selected = session_id or (result["sessions"][0]["session_id"] if result["sessions"] else None)
            summary = summaries.get(selected)
            if selected is not None and summary is None:
                row = db.execute(f"SELECT {_payload_sql(MAX_SESSION_BYTES)} FROM paper_sessions WHERE id=?",
                                 (selected,)).fetchone()
                if row is not None:
                    summary = _summary(_payload(row[0], row[1], MAX_SESSION_BYTES), selected)
            if summary is None:
                sql = "SELECT 1 FROM paper_fills" + (" WHERE session_id=?" if selected is not None else "") + " LIMIT 1"
                if db.execute(sql, (selected,) if selected is not None else ()).fetchone() is not None:
                    raise ValueError("Paper journal contains fills without their session")
                return result
            count, distinct, first, last, invalid = db.execute(
                "SELECT COUNT(*),COUNT(DISTINCT number),MIN(number),MAX(number),"
                "COALESCE(SUM(CASE WHEN typeof(number)!='integer' OR number<1 THEN 1 ELSE 0 END),0) "
                "FROM paper_fills WHERE session_id=?", (selected,),
            ).fetchone()
            if (invalid or distinct != count or count != summary["fill_count"]
                    or (count and (first != 1 or last != count))):
                raise ValueError("Paper journal fill count or numbering is inconsistent; the journal is incomplete")
            sql = f"SELECT number, {_payload_sql(MAX_FILL_BYTES)} FROM paper_fills WHERE session_id=?"
            arguments = [selected]
            if before is not None:
                sql += " AND number<?"
                arguments.append(before)
            sql += " ORDER BY number DESC LIMIT ?"
            arguments.append(limit)
            fills = [_fill(_payload(raw, size, MAX_FILL_BYTES), _integer(number, "saved fill number", 1))
                     for number, raw, size in db.execute(sql, arguments)]
            result.update(session=summary, fills=fills, fill_count=count,
                          next_before=fills[-1]["number"] if fills and fills[-1]["number"] > 1 else None)
            return result
    except sqlite3.Error as error:
        raise ValueError("Saved paper journal is unreadable or has an incompatible database schema") from error


def _destination(source: Path, target: Path, overwrite: bool) -> Path:
    target = Path(target)
    if target.is_symlink():
        raise ValueError("Choose an export destination without a symbolic link")
    destination = target.resolve()
    for protected in (source, *(Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
        if (destination == protected.resolve()
                or (destination.exists() and protected.exists() and os.path.samefile(destination, protected))):
            raise ValueError("Choose a CSV destination that preserves the paper database and its sidecars")
    if destination.exists() and (not overwrite or not destination.is_file()):
        raise ValueError("Confirm replacement of the existing destination or choose a new file")
    if not destination.parent.is_dir():
        raise ValueError("Choose an existing folder for the CSV export")
    return destination


def _csv_text(value: str) -> str:
    if value.lstrip(" \t\r\n\v\f\ufeff").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def save_journal_csv(snapshot: dict, target: Path, *, overwrite: bool = False) -> None:
    """Atomically export precisely the supplied page, without reopening SQLite."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("session"), dict):
        raise ValueError("Choose a saved paper session before exporting its page")
    if type(overwrite) is not bool:
        raise ValueError("CSV replacement approval must be true or false")
    source = Path(_text(snapshot.get("_journal_path"), "source provenance", 32_768)).resolve()
    session = _summary(snapshot["session"], _text(snapshot["session"].get("session_id"), "session ID", 128))
    count = _integer(snapshot.get("fill_count"), "snapshot fill count")
    if count != session["fill_count"]:
        raise ValueError("Paper journal snapshot counts are inconsistent")
    raw_fills = snapshot.get("fills")
    if not isinstance(raw_fills, list) or len(raw_fills) > MAX_PAGE:
        raise ValueError("Export one loaded paper journal page of at most 100 fills")
    fills = [_fill(raw, _integer(raw.get("number"), "fill number", 1)) if isinstance(raw, dict) else None
             for raw in raw_fills]
    previous = None
    rows = []
    for item in fills:
        if item is None or item["number"] > count or (previous is not None and item["number"] != previous - 1):
            raise ValueError("Paper journal snapshot fill order or numbering is inconsistent")
        previous = item["number"]
        rows.append([_csv_text(session["session_id"]), _csv_text(session["source"]), _csv_text(session["created_at"]),
                     count, item["number"], _csv_text(item["key"]), _csv_text(item["side"]),
                     item["quantity"], item["price"], item["realized_pnl"], _csv_text(item["filled_at"])])
    destination = _destination(source, target, overwrite)
    descriptor, temporary = tempfile.mkstemp(prefix=".paper-journal-", dir=destination.parent)
    try:
        try:
            output = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        except BaseException:
            os.close(descriptor)
            raise
        with output:
            writer = csv.writer(output)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(rows)
            output.flush()
            os.fsync(output.fileno())
        _destination(source, destination, overwrite)
        if overwrite:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError as error:
                raise ValueError("Confirm replacement of the existing destination or choose a new file") from error
    finally:
        Path(temporary).unlink(missing_ok=True)

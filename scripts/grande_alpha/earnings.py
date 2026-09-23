"""Research-only PEAD candidate screening; intentionally has no broker/controller imports."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grande_alpha.json_inputs import load_json

SCHEMA_VERSION = 1
LIMITS = {
    "min_surprise_bps",
    "min_momentum_bps",
    "max_spread_bps",
    "max_event_age_days",
    "max_quote_age_seconds",
}
FIELDS = {
    "event_id",
    "symbol",
    "announced_at",
    "actual_available_at",
    "consensus_available_at",
    "actual_eps",
    "consensus_eps",
    "actual_basis",
    "consensus_basis",
    "actual_period",
    "consensus_period",
    "actual_currency",
    "consensus_currency",
    "price_currency",
    "reference_price",
    "reference_at",
    "reference_available_at",
    "post_price",
    "post_at",
    "post_available_at",
    "bid",
    "ask",
    "quote_at",
    "quote_available_at",
    "source_id",
}


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: finite number required")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name}: finite number required") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name}: invalid numeric value")
    return number


def _time(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name}: timezone-aware timestamp required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name}: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name}: timezone required")
    return parsed.astimezone(UTC)


def template() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": None,
        "thresholds": {key: None for key in sorted(LIMITS)},
        "events": [{key: None for key in sorted(FIELDS)}],
    }


def _screen(event: Any, as_of: datetime, thresholds: dict[str, float]) -> dict[str, Any]:
    if not isinstance(event, dict) or set(event) != FIELDS:
        raise ValueError("Every event requires the exact documented fields")
    for key in (
        "event_id",
        "source_id",
        "symbol",
        "actual_basis",
        "consensus_basis",
        "actual_period",
        "consensus_period",
    ):
        if not isinstance(event[key], str) or not event[key].strip():
            raise ValueError(f"{key}: nonempty text required")
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", event["symbol"]):
        raise ValueError("Invalid research symbol")
    for key in ("actual_currency", "consensus_currency", "price_currency"):
        if event[key] != "USD":
            raise ValueError("Research v1 requires EPS and prices in USD")
    if (
        event["actual_basis"] != event["consensus_basis"]
        or event["actual_period"] != event["consensus_period"]
    ):
        raise ValueError("Actual and consensus EPS accounting basis and fiscal period must match")
    t = {key: _time(event[key], key) for key in FIELDS if key.endswith("_at")}
    if not (t["consensus_available_at"] < t["announced_at"] <= t["actual_available_at"] <= as_of):
        raise ValueError(
            "Earnings/consensus chronology contains unavailable or post-announcement information"
        )
    if not (t["reference_at"] < t["announced_at"] <= t["post_at"] < t["quote_at"] <= as_of):
        raise ValueError(
            "Price chronology must separate pre-announcement reference and post-announcement drift"
        )
    if not (t["reference_at"] <= t["reference_available_at"] < t["announced_at"]):
        raise ValueError("Reference price was not available before the announcement")
    if not (t["actual_available_at"] <= t["post_at"] <= t["post_available_at"] <= as_of):
        raise ValueError("Post-announcement baseline was not available causally")
    if not (t["quote_at"] <= t["quote_available_at"] <= as_of):
        raise ValueError("Latest quote was not available by decision time")
    # A post-price observed before the actual result became available is not a causal entry baseline.
    if t["post_available_at"] > t["quote_at"]:
        raise ValueError("Baseline was delivered after the latest quote")
    actual = _number(event["actual_eps"], "actual_eps")
    consensus = _number(event["consensus_eps"], "consensus_eps")
    ref = _number(event["reference_price"], "reference_price", positive=True)
    post = _number(event["post_price"], "post_price", positive=True)
    bid = _number(event["bid"], "bid", positive=True)
    ask = _number(event["ask"], "ask", positive=True)
    if bid > ask:
        raise ValueError("Crossed quote")
    mid = bid / 2 + ask / 2
    metrics = {
        "price_scaled_surprise_bps": (actual - consensus) / ref * 10_000,
        "post_announcement_momentum_bps": (mid / post - 1) * 10_000,
        "spread_bps": (ask - bid) / mid * 10_000,
        "event_age_days": (as_of - t["announced_at"]).total_seconds() / 86_400,
        "quote_age_seconds": (as_of - t["quote_at"]).total_seconds(),
    }
    if not all(math.isfinite(value) for value in metrics.values()):
        raise ValueError("Derived metrics overflowed")
    failures = []
    for metric, threshold, direction in [
        ("price_scaled_surprise_bps", "min_surprise_bps", "min"),
        ("post_announcement_momentum_bps", "min_momentum_bps", "min"),
        ("spread_bps", "max_spread_bps", "max"),
        ("event_age_days", "max_event_age_days", "max"),
        ("quote_age_seconds", "max_quote_age_seconds", "max"),
    ]:
        if (direction == "min" and metrics[metric] < thresholds[threshold]) or (
            direction == "max" and metrics[metric] > thresholds[threshold]
        ):
            failures.append(threshold)
    return {
        "event_id": event["event_id"],
        "symbol": event["symbol"],
        "source_id": event["source_id"],
        "status": "RESEARCH_CANDIDATE" if not failures else "NO_CANDIDATE",
        "metrics": metrics,
        "failed_thresholds": failures,
    }


def screen(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "as_of", "thresholds", "events"}:
        raise ValueError("Expected exact research request schema")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported earnings schema")
    as_of = _time(payload["as_of"], "as_of")
    limits = payload["thresholds"]
    if not isinstance(limits, dict) or set(limits) != LIMITS:
        raise ValueError("All research thresholds must be explicit")
    thresholds = {key: _number(limits[key], key, positive=True) for key in LIMITS}
    events = payload["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= 10_000:
        raise ValueError("Supply between 1 and 10000 events")
    seen: set[str] = set()
    results = []
    for index, event in enumerate(events):
        if isinstance(event, dict) and isinstance(event.get("event_id"), str):
            if event["event_id"] in seen:
                raise ValueError("Duplicate event IDs would double-count a candidate")
            seen.add(event["event_id"])
        try:
            results.append(_screen(event, as_of, thresholds))
        except (ValueError, TypeError, OverflowError) as exc:
            results.append({"row": index + 1, "status": "INVALID_INPUT", "reason": str(exc)})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "input_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "provenance": "USER_SUPPLIED_UNVERIFIED",
        "authority_granted": False,
        "live_eligible": False,
        "backtest_performed": False,
        "results": results,
    }


def command_screen(args: Any) -> int:
    result = screen(load_json(Path(args.input), max_bytes=5_000_000))
    print(json.dumps(result, indent=2, allow_nan=False))
    return 2 if any(row["status"] == "INVALID_INPUT" for row in result["results"]) else 0


def command_template(args: Any) -> int:
    print(json.dumps(template(), indent=2))
    return 0

"""Bounded local research export ingestion; no vendor, credentials, or authority."""

import hashlib
import json
from pathlib import Path

from grande_alpha.earnings import _time
from grande_alpha.json_inputs import load_json
from grande_alpha.mixed_portfolio import plan


def load_snapshot(path: Path, *, now, max_age_seconds: float = 8) -> dict:
    from grande_alpha.earnings import _number

    _number(max_age_seconds, "max_age_seconds", positive=True)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Snapshot observation time must be timezone-aware")
    data = load_json(path, max_bytes=8_000_000)
    if not isinstance(data, dict) or set(data) != {"request", "theses"}:
        raise ValueError("Research export requires exactly request and theses")
    if not isinstance(data["theses"], dict) or any(type(v) is not bool for v in data["theses"].values()):
        raise ValueError("Research theses require explicit boolean values")
    result = plan(data["request"])
    age = (now-_time(result["as_of"], "as_of")).total_seconds()
    if not 0 <= age <= max_age_seconds:
        raise ValueError("Research export is stale or future-dated")
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {**data, "input_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "provenance": "LOCAL_EXPORT_UNVERIFIED", "authority_granted": False}

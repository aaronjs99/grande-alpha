from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any


def _parse_aware_utc(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp with a UTC offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


def _valid_provenance_record(
    provenance_hash: str,
    provenance: dict[str, Any] | None,
    dataset_hash: str,
    *,
    require_runtime_observation: bool = False,
) -> bool:
    if not isinstance(provenance, dict):
        return False
    try:
        from grande_alpha.research.historical import DataProvenance

        allowed = DataProvenance.__dataclass_fields__.keys()
        record = DataProvenance(**{key: value for key, value in provenance.items() if key in allowed})
    except (TypeError, ValueError):
        return False
    return bool(
        record.evidence_eligible
        and (not require_runtime_observation or record.runtime_observation_eligible)
        and record.digest == provenance_hash
        and record.canonical_dataset_hash == dataset_hash
    )


def _valid_quality_record(
    raw_quality: Any,
    expected_hash: str,
    *,
    minimum_sessions: int | None = None,
    exact_sessions: int | None = None,
) -> bool:
    if isinstance(raw_quality, str):
        try:
            raw_quality = json.loads(raw_quality)
        except (TypeError, json.JSONDecodeError):
            return False
    if not isinstance(raw_quality, dict):
        return False
    integer_fields = (
        "aligned_bars",
        "sessions",
        "missing_intervals",
        "zero_volume_bars",
        "duplicate_timestamps",
        "invalid_session_bars",
        "expected_sessions",
        "missing_sessions",
        "complete_sessions",
    )
    if any(type(raw_quality.get(name)) is not int for name in integer_fields):
        return False
    aligned_bars = raw_quality["aligned_bars"]
    sessions = raw_quality["sessions"]
    missing = raw_quality["missing_intervals"]
    duplicates = raw_quality["duplicate_timestamps"]
    complete = raw_quality["complete_sessions"]
    raw_coverage = raw_quality.get("session_coverage_pct")
    if isinstance(raw_coverage, bool) or not isinstance(raw_coverage, (int, float)):
        return False
    coverage = float(raw_coverage)
    expected_coverage = complete / sessions * 100.0 if sessions > 0 else math.nan
    return bool(
        isinstance(raw_quality.get("interval"), str)
        and bool(raw_quality["interval"])
        and isinstance(raw_quality.get("dataset_hash"), str)
        and aligned_bars >= sessions > 0
        and sessions > 0
        and missing == 0
        and duplicates == 0
        and raw_quality["invalid_session_bars"] == 0
        and raw_quality["missing_sessions"] == 0
        and raw_quality["expected_sessions"] == sessions
        and raw_quality["zero_volume_bars"] >= 0
        and 0 <= complete <= sessions
        and math.isfinite(coverage)
        and math.isclose(coverage, expected_coverage, rel_tol=0.0, abs_tol=1e-9)
        and coverage >= 95.0
        and (minimum_sessions is None or sessions >= minimum_sessions)
        and (exact_sessions is None or sessions == exact_sessions)
        and raw_quality.get("dataset_hash") == expected_hash
    )


def _passing_holdout_metrics(metrics: Any, holdout: Any) -> bool:
    """Recompute the immutable final-holdout pass at the storage trust boundary."""

    if not isinstance(metrics, dict):
        return False
    try:
        net_pnl = float(metrics["net_pnl"])
        round_trips = float(metrics["round_trips"])
        profit_factor = float(metrics["profit_factor"])
        expectancy = float(metrics["expectancy"])
        max_drawdown = float(metrics["max_drawdown_pct"])
        cost_multiplier = float(metrics["cost_multiplier"])
        forced_flatten_count = float(metrics["forced_flatten_count"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    finite_values = (
        net_pnl,
        round_trips,
        expectancy,
        max_drawdown,
        cost_multiplier,
        forced_flatten_count,
    )
    if not all(math.isfinite(value) for value in finite_values):
        return False
    if math.isnan(profit_factor) or profit_factor == -math.inf:
        return False
    return bool(
        metrics.get("holdout_hash") == holdout["holdout_hash"]
        and metrics.get("holdout_start") == holdout["holdout_start"]
        and metrics.get("holdout_end") == holdout["holdout_end"]
        and math.isclose(cost_multiplier, 3.0, rel_tol=0.0, abs_tol=1e-12)
        and net_pnl > 0
        and round_trips >= 5
        and profit_factor >= 1.10
        and expectancy > 0
        and 0 <= max_drawdown <= 5.0
        and metrics.get("ending_position") is None
        and forced_flatten_count == 0
    )


_RISK_ENVELOPE_FIELDS = {
    "max_order_notional",
    "max_daily_notional",
    "max_total_exposure",
    "max_daily_loss",
    "max_trades",
    "max_orders_per_minute",
    "max_spread_bps",
}


def _valid_risk_envelope(envelope: Any) -> bool:
    if not isinstance(envelope, dict) or not _RISK_ENVELOPE_FIELDS <= envelope.keys():
        return False
    try:
        values = {name: float(envelope[name]) for name in _RISK_ENVELOPE_FIELDS}
    except (TypeError, ValueError, OverflowError):
        return False
    if not all(math.isfinite(value) and value > 0 for value in values.values()):
        return False
    return all(values[name].is_integer() for name in ("max_trades", "max_orders_per_minute"))

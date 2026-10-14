"""Read-only qualification of recorded runtime quote traces, independent of the CLI."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.domain.policy import session_key
from grande_alpha.research.historical_models import (
    RUNTIME_ANALYSIS_PRICE_SEMANTICS,
    RUNTIME_EXECUTION_PRICE_SEMANTICS,
    RUNTIME_OBSERVATION_SCHEMA,
    RUNTIME_VOLUME_SEMANTICS,
    HistoricalBundle,
)


def runtime_trace_readiness(
    bundle: HistoricalBundle,
    *,
    quote_row_count: int,
    range_start: date | None,
    range_end: date | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    quality = bundle.quality
    provenance = bundle.provenance
    if quality is None or provenance is None:
        raise ValueError("Runtime trace has no quality or provenance record")
    reference = now or datetime.now(UTC)
    age_days = (reference - bundle.end).total_seconds() / 86_400
    actual_sessions = {session_key(frame.start, bundle.market_hours) for frame in bundle.frames}
    actual_start = date.fromisoformat(min(actual_sessions))
    actual_end = date.fromisoformat(max(actual_sessions))
    expected_start = range_start or actual_start
    expected_end = range_end or actual_end
    expected_sessions: list[str] = []
    cursor = expected_start
    while cursor <= expected_end:
        if regular_session_times(cursor) is not None:
            expected_sessions.append(cursor.isoformat())
        cursor += timedelta(days=1)
    missing_range_sessions = sorted(set(expected_sessions) - actual_sessions)
    interval_seconds = int(bundle.interval[:-1]) if bundle.interval.endswith("s") else 60
    exact_schema = bool(
        bundle.frames
        and all(frame.has_exact_runtime_observation for frame in bundle.frames)
        and provenance.observation_schema == RUNTIME_OBSERVATION_SCHEMA
        and provenance.analysis_price_semantics == RUNTIME_ANALYSIS_PRICE_SEMANTICS
        and provenance.execution_price_semantics == RUNTIME_EXECUTION_PRICE_SEMANTICS
        and provenance.volume_semantics == RUNTIME_VOLUME_SEMANTICS
        and provenance.validator_profile == "exact_execution_quotes"
    )
    exact_cadence = bool(
        provenance.source_resolution_seconds is not None
        and 0 < provenance.source_resolution_seconds <= interval_seconds
    )
    checks = [
        {
            "name": "Exact runtime observation schema",
            "passed": exact_schema,
            "observed": (
                f"{len(bundle.frames)} causal frames; schema={provenance.observation_schema}; "
                f"validator={provenance.validator_profile} v{provenance.validator_version}"
            ),
            "requirement": "Every frame is one synchronized QQQ/TQQQ/SQQQ causal runtime observation",
        },
        {
            "name": "Provenance and source rights",
            "passed": bundle.evidence_provenance_eligible,
            "observed": (
                f"kind={provenance.source_kind}; manifest v{provenance.manifest_version}; "
                f"eligible={bundle.evidence_provenance_eligible}"
            ),
            "requirement": "Content-bound manifest v1 with user-reviewed research and automated-research rights",
        },
        {
            "name": "Exact native cadence",
            "passed": exact_cadence,
            "observed": f"maximum accepted within-stream delta {provenance.source_resolution_seconds}s",
            "requirement": f"Positive source cadence no coarser than {interval_seconds}s",
        },
        {
            "name": "Requested range coverage",
            "passed": not missing_range_sessions,
            "observed": (
                f"{len(actual_sessions)} observed sessions; {len(expected_sessions)} expected; "
                f"{len(missing_range_sessions)} absent"
            ),
            "requirement": "Every scheduled equity session in the inclusive requested range is present",
        },
        {
            "name": "Data breadth",
            "passed": quality.sessions >= 141,
            "observed": f"{quality.sessions} sessions",
            "requirement": "At least 141 sessions: 120 development, one purge, and 20 final holdout",
        },
        {
            "name": "Data recency",
            "passed": 0 <= age_days <= 30,
            "observed": f"{age_days:.1f} days old",
            "requirement": "Final observation no more than 30 days old",
        },
        {
            "name": "Data integrity",
            "passed": bool(
                quality.clean
                and quality.missing_intervals == 0
                and quality.missing_sessions == 0
                and quality.session_coverage_pct >= 95.0
            ),
            "observed": (
                f"{quality.missing_intervals} missing intervals; "
                f"{quality.missing_sessions} omitted sessions; "
                f"{quality.duplicate_timestamps} duplicates; "
                f"{quality.session_coverage_pct:.1f}% complete"
            ),
            "requirement": "Zero missing/duplicate intervals or sessions and at least 95% complete sessions",
        },
    ]
    return {
        "operation": "read_only_runtime_trace_audit",
        "broker_calls": 0,
        "holdout_reserved_or_evaluated": False,
        "database_open_mode": "ro",
        "source": bundle.source,
        "dataset_hash": bundle.dataset_hash,
        "provenance_hash": bundle.provenance_hash,
        "source_trace_sha256": provenance.source_trace_sha256,
        "interval": bundle.interval,
        "market_hours": bundle.market_hours,
        "range_start": range_start.isoformat() if range_start is not None else None,
        "range_end": range_end.isoformat() if range_end is not None else None,
        "observed_start": bundle.start.isoformat(),
        "observed_end": bundle.end.isoformat(),
        "quote_row_count": quote_row_count,
        "frames": len(bundle.frames),
        "sessions": quality.sessions,
        "complete_sessions": quality.complete_sessions,
        "checks": checks,
        "input_ready": all(check["passed"] for check in checks),
    }

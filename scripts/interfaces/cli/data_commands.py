from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from grande_alpha.configuration.config import APP_NAME
from grande_alpha.data.data_readiness import (
    DatasetReadinessReport,
    audit_cache_directory,
    audit_csv_dataset,
)
from grande_alpha.data.dataset_manifest import manifest_template
from grande_alpha.data.evidence_ledger import audit_evidence_ledger
from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.domain.policy import session_key
from grande_alpha.interfaces.cli.cli_table import format_table
from grande_alpha.interfaces.cli.render import print_json as _json
from grande_alpha.research.historical import (
    RUNTIME_ANALYSIS_PRICE_SEMANTICS,
    RUNTIME_EXECUTION_PRICE_SEMANTICS,
    RUNTIME_OBSERVATION_SCHEMA,
    RUNTIME_VOLUME_SEMANTICS,
    HistoricalBundle,
)
from grande_alpha.research.runtime_trace import (
    load_runtime_quote_trace_with_row_count,
    runtime_trace_manifest_template,
)


def _load_json_object(path: Path | None, label: str) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label.capitalize()} must contain one JSON object")
    return payload


def _runtime_trace_readiness(
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


def _print_data_report(report: DatasetReadinessReport, width: int | None) -> None:
    state = "INPUT READY" if report.input_ready else "NOT READY"
    digest = report.dataset_hash[:16] + "..." if report.dataset_hash else "unavailable"
    cadence = (
        "not applicable"
        if report.observed_cadence_seconds is None
        else f"{report.observed_cadence_seconds:g}s"
    )
    print(f"{report.label} | {state} for exact {report.target_interval} research input | dataset {digest}")
    if report.load_error:
        print(f"Load failure: {report.load_error}")
        return
    print(
        format_table(
            ["Item", "Value"],
            [
                ["Source", report.source],
                ["Coverage", f"{report.start} through {report.end}"],
                [
                    "Interval",
                    f"{report.interval}; observed mode {cadence}",
                ],
                [
                    "Sessions",
                    f"{report.sessions} total; {report.complete_sessions} complete "
                    f"({report.session_coverage_pct:.1f}%)",
                ],
                [
                    "Integrity",
                    f"{report.missing_intervals} missing; "
                    f"{report.duplicate_timestamps} duplicate; "
                    f"{report.zero_volume_bars} zero-volume aligned bars",
                ],
            ],
            width,
        )
    )
    print()
    print(
        format_table(
            ["Gate", "Status", "Observed", "Requirement"],
            [
                [
                    check.name,
                    "PASS" if check.passed else "FAIL",
                    check.observed,
                    check.requirement,
                ]
                for check in report.checks
            ],
            width,
        )
    )


def command_data_audit(args: argparse.Namespace) -> int:
    """Qualify data and inventory the ledger without reserving or revealing a holdout."""

    if args.csv is not None:
        if args.interval is None:
            raise ValueError("--interval is required for a CSV; never infer or relabel its cadence")
        reports = [
            audit_csv_dataset(
                args.csv,
                args.interval,
                target_interval=args.target_interval,
                manifest_path=args.manifest,
            )
        ]
    else:
        if args.manifest is not None or args.interval is not None:
            raise ValueError("--manifest and --interval apply only with --csv PATH")
        local_root = Path(user_data_path(APP_NAME, appauthor=False))
        cache_dir = args.cache_dir or local_root / "sandbox_cache"
        reports = audit_cache_directory(cache_dir, target_interval=args.target_interval)

    local_root = Path(user_data_path(APP_NAME, appauthor=False))
    database_path = args.database or local_root / "grande_alpha.db"
    ledger = audit_evidence_ledger(database_path)
    payload = {
        "operation": "read_only_data_audit",
        "broker_calls": 0,
        "holdout_reserved_or_evaluated": False,
        "target_interval": args.target_interval,
        "datasets": [report.as_dict() for report in reports],
        "ledger": ledger,
    }
    if args.json:
        _json(payload)
    else:
        print(
            "READ-ONLY DATA AUDIT - no broker call, evidence trial, holdout reservation, "
            "or holdout evaluation was performed."
        )
        if not reports:
            print("No cached dataset was found. Supply --csv PATH --interval INTERVAL to audit an import.")
        for index, report in enumerate(reports):
            if index:
                print()
            _print_data_report(report, args.width)
        latest = ledger.get("latest_promotion") or {}
        print("\nEvidence ledger inventory")
        print(
            format_table(
                ["Item", "Value", "Explanation"],
                [
                    ["Database", ledger["database"], "Opened with SQLite mode=ro and query_only"],
                    [
                        "Registered trials",
                        ledger["trials"],
                        f"Across {ledger['trial_datasets']} dataset hash(es)",
                    ],
                    [
                        "Promotion receipts",
                        ledger["promotions"],
                        f"Statuses {ledger['promotion_statuses']}; policy versions "
                        f"{ledger['promotion_policy_versions']}",
                    ],
                    [
                        "Latest promotion",
                        latest.get("status", "none"),
                        (
                            f"Receipt {latest.get('id')}; policy {latest.get('policy_version')}; "
                            f"holdout {latest.get('holdout_id') or 'none'}"
                            if latest
                            else "No saved evidence receipt"
                        ),
                    ],
                    [
                        "Final holdouts",
                        ledger["holdouts"],
                        f"Statuses {ledger['holdout_statuses']}; this audit did not reserve or read one",
                    ],
                ],
                args.width,
            )
        )
        trace = ledger["runtime_trace"]
        print("\nLocal runtime-trace progress")
        print(
            format_table(
                ["Item", "Observed", "Eligibility"],
                [
                    [
                        "Quotes",
                        f"{trace['quotes']} • {trace['quote_symbols']} • "
                        f"{trace['quote_start'] or 'none'} to {trace['quote_end'] or 'none'}",
                        (
                            "balanced QQQ/TQQQ/SQQQ counts"
                            if trace["balanced_required_symbols"]
                            else "required-symbol counts are absent or unbalanced"
                        ),
                    ],
                    [
                        "Constructed bars",
                        f"{trace['bars']} • {trace['bar_symbols']} • "
                        f"{trace['bar_start'] or 'none'} to {trace['bar_end'] or 'none'}",
                        "NOT an evidence HistoricalBundle",
                    ],
                    ["Why not ready", trace["reason"], "Collection progress only"],
                ],
                args.width,
            )
        )
        print(
            "\nINPUT READY means only that a dataset is suitable to enter development/final-evidence "
            "governance. It is not a passing strategy certificate or trading authorization."
        )
    return 0 if reports and all(report.input_ready for report in reports) else 1


def command_data_manifest_template(args: argparse.Namespace) -> int:
    _json(manifest_template(args.target_interval))
    return 0


def _load_runtime_trace_from_data_args(
    args: argparse.Namespace,
    *,
    include_manifest: bool,
) -> tuple[HistoricalBundle, int]:
    if include_manifest and args.manifest is not None and (args.start is None or args.end is None):
        raise ValueError("A manifest-backed runtime-trace audit requires --start and --end")
    manifest = _load_json_object(args.manifest, "runtime-trace manifest") if include_manifest else None
    return load_runtime_quote_trace_with_row_count(
        args.database,
        bar_seconds=args.bar_seconds,
        market_hours=args.session,
        manifest=manifest,
        start=args.start,
        end=args.end,
    )


def command_data_runtime_trace_audit(args: argparse.Namespace) -> int:
    """Audit one exact trace range without opening the evidence ledger for writes."""

    bundle, row_count = _load_runtime_trace_from_data_args(args, include_manifest=True)
    payload = _runtime_trace_readiness(
        bundle,
        quote_row_count=row_count,
        range_start=args.start,
        range_end=args.end,
    )
    if args.json:
        _json(payload)
    else:
        print(
            "READ-ONLY RUNTIME-TRACE AUDIT - SQLite mode=ro; no broker call, trial, "
            "holdout reservation, or holdout evaluation was performed."
        )
        print(
            format_table(
                ["Item", "Value"],
                [
                    [
                        "Range",
                        f"{payload['range_start'] or 'first row'} through {payload['range_end'] or 'last row'}",
                    ],
                    ["Observed", f"{payload['observed_start']} through {payload['observed_end']}"],
                    ["Source rows", payload["quote_row_count"]],
                    ["Causal frames", payload["frames"]],
                    ["Sessions", f"{payload['sessions']} total; {payload['complete_sessions']} complete"],
                    ["Dataset hash", payload["dataset_hash"]],
                ],
                args.width,
            )
        )
        print()
        print(
            format_table(
                ["Gate", "Status", "Observed", "Requirement"],
                [
                    [
                        check["name"],
                        "PASS" if check["passed"] else "FAIL",
                        check["observed"],
                        check["requirement"],
                    ]
                    for check in payload["checks"]
                ],
                args.width,
            )
        )
        print(
            "\nINPUT READY"
            if payload["input_ready"]
            else "\nINPUT NOT READY - do not run Evidence Lab on this range."
        )
    return 0 if payload["input_ready"] else 1


def command_data_runtime_trace_manifest_template(args: argparse.Namespace) -> int:
    """Print a range-bound template; never save it or touch the holdout ledger."""

    bundle, row_count = _load_runtime_trace_from_data_args(args, include_manifest=False)
    _json(
        runtime_trace_manifest_template(
            bundle,
            row_count,
            range_start=args.start,
            range_end=args.end,
        )
    )
    return 0

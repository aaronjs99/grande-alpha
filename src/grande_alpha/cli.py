from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import textwrap
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from grande_alpha import __version__
from grande_alpha.activation_guidance import decorate_readiness
from grande_alpha.candidate_execution import contract_from_app_and_sandbox, runtime_parity_assessment
from grande_alpha.config import APP_NAME, load_config
from grande_alpha.data_readiness import (
    DatasetReadinessReport,
    audit_cache_directory,
    audit_csv_dataset,
    audit_evidence_ledger,
    load_audited_csv_dataset,
    manifest_template,
)
from grande_alpha.evidence import (
    EVIDENCE_POLICY_VERSION,
    RUNTIME_SIZING_PARITY_CERTIFIED,
    STRATEGY_FINGERPRINT_FIELDS,
    strategy_fingerprint,
)
from grande_alpha.gate_guidance import GATE_GUIDANCE, gate_detail, promotion_overview
from grande_alpha.historical import (
    RUNTIME_ANALYSIS_PRICE_SEMANTICS,
    RUNTIME_EXECUTION_PRICE_SEMANTICS,
    RUNTIME_OBSERVATION_SCHEMA,
    RUNTIME_VOLUME_SEMANTICS,
    HistoricalBundle,
    HistoricalDataProvider,
    deterministic_demo,
    load_csv_history,
    load_runtime_quote_trace_with_row_count,
    runtime_trace_manifest_template,
)
from grande_alpha.market_calendar import regular_session_times
from grande_alpha.policy import session_key
from grande_alpha.product import PRODUCT_PLANS, configured_upgrade_url, current_entitlement
from grande_alpha.research_service import run_evidence_lab
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine, load_sandbox_config
from grande_alpha.storage import AuditStore
from grande_alpha.strategy import STRATEGY_NAMES
from grande_alpha.ui.glossary import TERM_HELP

CLI_WIDTHS: dict[str, int] = {
    "Gate": 22,
    "Status": 11,
    "Observed": 31,
    "Requirement": 43,
    "Time": 25,
    "Severity": 9,
    "Category": 20,
    "Summary": 55,
    "Run": 12,
    "Source": 32,
    "Metric": 24,
    "Condition": 25,
    "Owner": 16,
    "Current result": 28,
    "Exact next action": 58,
    "Value": 28,
}


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    return (
        str(value)
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("—", "-")
        .replace("–", "-")
        .replace("…", "...")
        .replace("•", " / ")
        .replace("·", " / ")
        .replace("×", "x")
        .replace("≥", ">=")
        .replace("≤", "<=")
    )


def format_table(headers: list[str], rows: list[list[Any]], width: int | None = None) -> str:
    """Render a wrapping terminal table; --width is the CLI equivalent of dragging columns."""

    if not headers:
        return ""
    available = width or shutil.get_terminal_size((120, 30)).columns
    available = max(54, available)
    string_rows = [[_cell(value) for value in row] for row in rows]
    minimums = [max(5, len(header)) for header in headers]
    preferred = []
    for column, header in enumerate(headers):
        content = max([len(header), *(len(row[column]) for row in string_rows)] or [len(header)])
        preferred.append(max(minimums[column], min(CLI_WIDTHS.get(header, 28), content)))
    separators = 3 * (len(headers) - 1)
    while sum(preferred) + separators > available:
        candidates = [index for index, value in enumerate(preferred) if value > minimums[index]]
        if not candidates:
            break
        largest = max(candidates, key=lambda index: preferred[index] - minimums[index])
        preferred[largest] -= 1

    def rule(character: str = "-") -> str:
        return "+".join(character * value for value in preferred)

    def wrapped(values: list[str]) -> list[str]:
        cells = [
            textwrap.wrap(value, width=preferred[index], break_long_words=True, break_on_hyphens=False)
            or [""]
            for index, value in enumerate(values)
        ]
        height = max(len(value) for value in cells)
        return [
            " | ".join(
                cells[column][line].ljust(preferred[column])
                if line < len(cells[column])
                else " " * preferred[column]
                for column in range(len(headers))
            ).rstrip()
            for line in range(height)
        ]

    lines = [*wrapped(headers), rule("=")]
    for index, row in enumerate(string_rows):
        lines.extend(wrapped(row))
        if index != len(string_rows) - 1:
            lines.append(rule())
    return "\n".join(lines)


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str, sort_keys=True))


def _sandbox_metric_rows(metrics: dict[str, Any]) -> list[list[str]]:
    return [
        ["Final equity", f"${float(metrics.get('final_equity', 0)):,.2f}"],
        ["Net P/L", f"${float(metrics.get('net_pnl', 0)):+,.2f}"],
        ["Return", f"{float(metrics.get('return_pct', 0)):+.2f}%"],
        ["Max drawdown", f"{float(metrics.get('max_drawdown_pct', 0)):.2f}%"],
        ["Round trips", str(metrics.get("round_trips", 0))],
        ["Win rate", f"{float(metrics.get('win_rate', 0)):.1f}%"],
        ["Profit factor", f"{float(metrics.get('profit_factor', 0)):.2f}"],
        ["Expectancy", f"${float(metrics.get('expectancy', 0)):+.4f}"],
        ["Sharpe", f"{float(metrics.get('sharpe', 0)):+.2f}"],
        ["Sortino", f"{float(metrics.get('sortino', 0)):+.2f}"],
        ["Exposure", f"{float(metrics.get('exposure_pct', 0)):.1f}%"],
        ["Execution cost", f"${float(metrics.get('total_execution_cost', 0)):,.4f}"],
        ["Ending position", str(metrics.get("ending_position") or "cash")],
    ]


def _config_from_args(args: argparse.Namespace) -> SandboxConfig:
    config = load_sandbox_config()
    updates = {
        "lookback_days": args.days,
        "strategy_name": args.strategy,
        "market_hours": args.session,
        "order_type": args.order_type,
        "time_in_force": args.time_in_force,
        "initial_cash": args.starting_cash,
        "order_notional": args.order_notional,
    }
    config = replace(config, **{name: value for name, value in updates.items() if value is not None})
    config.validate()
    return config


def _current_runtime_candidate(config: object) -> SandboxConfig:
    """Bind the saved candidate to the runtime-owned fields without a broker controller."""
    updates = {
        field: getattr(config, field)
        for field in STRATEGY_FINGERPRINT_FIELDS
        if hasattr(config, field)
    }
    updates.update(
        decision_stride=config.trade_every_bars,
        market_hours=config.market_hours,
        order_type=config.order_type,
        time_in_force=config.time_in_force,
        limit_offset_bps=config.limit_offset_bps,
        settlement_model=config.settlement_model,
    )
    return replace(load_sandbox_config(), **updates)


def _current_runtime_fingerprint(config: object) -> str:
    candidate = _current_runtime_candidate(config)
    return strategy_fingerprint(candidate, f"{config.bar_seconds}s")


async def _bundle_from_args(
    args: argparse.Namespace,
    config: SandboxConfig,
    *,
    require_evidence_ready: bool = False,
) -> HistoricalBundle:
    if args.source == "demo":
        return await asyncio.to_thread(deterministic_demo, config.lookback_days)
    if args.source == "runtime-trace":
        if args.database is None:
            raise ValueError("--database PATH is required when --source runtime-trace is selected")
        if require_evidence_ready and (args.start is None or args.end is None):
            raise ValueError(
                "Runtime-trace evidence requires explicit inclusive --start and --end trading dates"
            )
        if require_evidence_ready and args.manifest is None:
            raise ValueError(
                "Runtime-trace evidence requires --manifest PATH and a passing read-only audit"
            )
        manifest = _load_json_object(args.manifest, "runtime-trace manifest")
        bundle, row_count = await asyncio.to_thread(
            load_runtime_quote_trace_with_row_count,
            args.database,
            bar_seconds=args.bar_seconds,
            market_hours=config.market_hours,
            manifest=manifest,
            start=args.start,
            end=args.end,
        )
        readiness = _runtime_trace_readiness(
            bundle,
            quote_row_count=row_count,
            range_start=args.start,
            range_end=args.end,
        )
        if require_evidence_ready and not readiness["input_ready"]:
            failures = ", ".join(
                check["name"] for check in readiness["checks"] if not check["passed"]
            )
            raise ValueError(
                f"Runtime-trace evidence input is not ready: {failures}. "
                "Run `data runtime-trace audit` for details; no final holdout was reserved or evaluated"
            )
        return bundle
    if args.source == "csv":
        if args.csv is None:
            raise ValueError("--csv PATH is required when --source csv is selected")
        manifest_path = getattr(args, "manifest", None)
        if require_evidence_ready and manifest_path is None:
            raise ValueError(
                "CSV evidence requires --manifest PATH and a passing read-only data audit; "
                "sandbox replay remains available without one"
            )
        if manifest_path is not None:
            bundle, report = await asyncio.to_thread(
                load_audited_csv_dataset,
                args.csv,
                args.interval,
                target_interval=args.interval,
                manifest_path=manifest_path,
            )
            if require_evidence_ready and not report.input_ready:
                failures = ", ".join(
                    check.name for check in report.checks if not check.passed
                )
                raise ValueError(
                    f"CSV evidence input is not ready: {failures}. Run `data audit` for details; "
                    "no final holdout was reserved or evaluated"
                )
            return bundle
        return await asyncio.to_thread(load_csv_history, args.csv, args.interval)
    app_config = load_config()
    if not app_config.remote_market_data_enabled:
        raise RuntimeError("Community remote market data is disabled in Settings & Permissions")
    if not args.acknowledge_community_data:
        raise RuntimeError(
            "Remote research requires --acknowledge-community-data; no broker or account data is sent"
        )
    provider = HistoricalDataProvider()
    if args.source == "full-daily":
        return await provider.fetch_full_daily()
    if config.market_hours == "all_day_hours":
        raise ValueError("24-hour evidence requires an appropriate imported CSV; community data is incomplete")
    maximum_days = {"1m": 7, "5m": 60, "60m": 730}
    if args.interval not in maximum_days:
        raise ValueError("Remote intervals are 1m, 5m, or 60m; custom second bars require --source csv")
    if config.lookback_days > maximum_days[args.interval]:
        raise ValueError(
            f"Remote {args.interval} history is capped at {maximum_days[args.interval]} calendar days"
        )
    return await provider.fetch(config.lookback_days, args.interval, market_hours=config.market_hours)


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
    actual_sessions = {
        session_key(frame.start, bundle.market_hours) for frame in bundle.frames
    }
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


def _record_sandbox(store: AuditStore, config: SandboxConfig, bundle: HistoricalBundle, result, note: str) -> None:
    store.record_sandbox_run(
        result.run_id,
        result.source,
        result.start.isoformat(),
        result.end.isoformat(),
        {**asdict(config), "note": note.strip(), "dataset_hash": bundle.dataset_hash},
        result.metrics(),
        [fill.as_dict() for fill in result.fills],
        [event.as_dict() for event in result.execution_events],
    )


def _print_evidence(promotion: dict[str, Any], args: argparse.Namespace) -> None:
    gates = promotion["gates"]
    if getattr(args, "failures_only", False):
        gates = [gate for gate in gates if not gate.get("passed", False)]
    if getattr(args, "json", False):
        _json({**promotion, "gates": gates})
        return
    all_gates = promotion["gates"]
    print(
        f"Evidence receipt #{promotion['id']} | {promotion['status']} | "
        f"dataset {promotion['dataset_hash'][:16]}…"
    )
    print(f"Source: {promotion['source']}")
    print(promotion_overview(all_gates))
    print()
    print(
        format_table(
            ["Gate", "Status", "Observed", "Requirement"],
            [
                [
                    gate.get("name", "Unknown gate"),
                    "PASS" if gate.get("passed", False) else "FAIL",
                    gate.get("observed", "Not recorded"),
                    gate.get("requirement", "Not recorded"),
                ]
                for gate in gates
            ],
            args.width,
        )
    )
    failures = [gate for gate in all_gates if not gate.get("passed", False)]
    if failures and not getattr(args, "compact", False):
        print("\nBlocking-gate guidance")
        for gate in failures:
            print("\n" + gate_detail(gate))


def command_status(args: argparse.Namespace) -> int:
    config = load_config()
    store = AuditStore()
    try:
        latest = store.research_promotion()
        try:
            current_fingerprint = _current_runtime_fingerprint(config)
            current_evidence = store.current_live_evidence(current_fingerprint)
        except Exception:
            current_evidence = None
        passes = (
            f"{sum(bool(gate.get('passed', False)) for gate in latest['gates'])}/{len(latest['gates'])}"
            if latest
            else "none"
        )
        rows = [
            ["Version", __version__, "Installed GRANDE Alpha Python package"],
            ["Mode", "RESEARCH / LOCKED", "This CLI never grants standing live-order authority"],
            ["Broker-data permission", "ENABLED" if config.broker_connection_enabled else "DISABLED", "Local setting only; no broker call was made"],
            [
                "Real-order setting",
                "ENABLED" if config.live_trading_enabled else "DISABLED",
                "The GUI still requires an attended bounded session and fresh per-order confirmation; autonomy also requires exact evidence",
            ],
            ["Remote-data permission", "ENABLED" if config.remote_market_data_enabled else "DISABLED", "CLI downloads also require an explicit acknowledgement flag"],
            [
                "Latest historical receipt",
                latest["status"] if latest else "NONE",
                f"{passes} stored gates; not current eligibility",
            ],
            [
                "Current exact eligibility",
                "ELIGIBLE" if current_evidence is not None else "BLOCKED",
                "Revalidated for current policy, fingerprint, provenance, holdout, replay age, and envelope",
            ],
            ["Local audit database", str(store.path), "Receipts, virtual runs, and evidence only"],
        ]
        if args.json:
            _json({row[0]: {"value": row[1], "explanation": row[2]} for row in rows})
        else:
            print(format_table(["Item", "Value", "Explanation"], rows, args.width))
        return 0
    finally:
        store.close()


def command_activation(args: argparse.Namespace) -> int:
    """Explain the complete fail-closed activation path without touching a broker."""

    config = load_config()
    store = AuditStore()
    try:
        latest = store.research_promotion()
        fingerprint_error = ""
        route_ready = False
        route_observed = "Runtime candidate contract unavailable"
        try:
            current_candidate = _current_runtime_candidate(config)
            current_fingerprint = strategy_fingerprint(
                current_candidate, f"{config.bar_seconds}s"
            )
            current_evidence = store.current_live_evidence(current_fingerprint)
            contract = contract_from_app_and_sandbox(config, current_candidate)
            pilot_route = next(
                check
                for check in runtime_parity_assessment(contract).checks
                if check.key == "pilot_route"
            )
            route_ready = pilot_route.aligned
            route_observed = (
                f"{pilot_route.replay.replace(' · ', ' / ')}; "
                f"modeled latency {contract.latency_bars} bars"
            )
        except Exception as exc:
            current_fingerprint = ""
            current_evidence = None
            fingerprint_error = str(exc)
        latest_receipt_uses_current_policy = bool(
            latest and int(latest.get("policy_version", -1)) == EVIDENCE_POLICY_VERSION
        )
        latest_receipt_matches_fingerprint = bool(
            latest
            and current_fingerprint
            and latest.get("strategy_fingerprint") == current_fingerprint
        )
        evidence_ready = current_evidence is not None
        historical_passed = (
            sum(bool(gate.get("passed", False)) for gate in latest["gates"])
            if latest
            else 0
        )
        historical_total = len(latest["gates"]) if latest else 0
        evidence_observed = (
            f"Current runtime fingerprint unavailable: {fingerprint_error}"
            if fingerprint_error
            else "No evidence receipt"
        )
        if current_evidence is not None:
            evidence_observed = (
                f"Current exact LIVE_REVIEW_ELIGIBLE certificate "
                f"#{current_evidence.get('id', '?')}"
            )
        elif latest and latest_receipt_uses_current_policy and latest_receipt_matches_fingerprint:
            evidence_observed = (
                f"INELIGIBLE FOR LIVE: {latest['status']} "
                f"({historical_passed}/{historical_total} current-policy gates); exact receipt still "
                "fails current holdout, age, parity, or envelope requirements"
            )
        elif latest and latest_receipt_uses_current_policy:
            evidence_observed = (
                f"INELIGIBLE FOR CURRENT RUNTIME: latest fingerprint "
                f"{str(latest.get('strategy_fingerprint', 'missing'))[:12]}... does not match current "
                f"{current_fingerprint[:12]}..."
            )
        elif latest:
            evidence_observed = (
                f"STALE / INELIGIBLE: policy v{latest.get('policy_version', '?')} receipt "
                f"({historical_passed}/{historical_total} historical gates); current policy is "
                f"v{EVIDENCE_POLICY_VERSION}"
            )
        raw_rows = [
            {
                "gate": "Scheduled auto-shadow",
                "status": "READ-ONLY",
                "observed": "Order review/place/cancel are structurally blocked",
            },
            {
                "gate": "Broker capability",
                "status": "PASS" if config.broker_connection_enabled else "BLOCKED",
                "observed": "Enabled" if config.broker_connection_enabled else "Disabled",
            },
            {
                "gate": "Supported real-order route",
                "status": "PASS" if route_ready else "BLOCKED",
                "observed": route_observed,
            },
            {
                "gate": "Runtime execution parity",
                "status": "PASS" if RUNTIME_SIZING_PARITY_CERTIFIED else "BLOCKED",
                "observed": "Certified" if RUNTIME_SIZING_PARITY_CERTIFIED else "Not certified",
            },
            {
                "gate": "Positive exact evidence",
                "status": "PASS" if evidence_ready else "BLOCKED",
                "observed": evidence_observed,
            },
            {
                "gate": "Real-order capability",
                "status": "PASS" if config.live_trading_enabled else "BLOCKED",
                "observed": "Enabled" if config.live_trading_enabled else "Disabled",
            },
            {
                "gate": "Live broker preflight",
                "status": "USER ACTION",
                "observed": "Not evaluated by this offline CLI command",
            },
            {
                "gate": "Bounded same-day authority",
                "status": "USER ACTION",
                "observed": "Never stored; required in the normal GUI each live day",
            },
        ]
        rows = decorate_readiness(raw_rows)
        failures = (
            [gate for gate in latest["gates"] if not gate.get("passed", False)]
            if latest
            and latest_receipt_uses_current_policy
            and latest_receipt_matches_fingerprint
            else []
        )
        evidence_failures = [
            {
                "gate": str(gate.get("name", "Unknown gate")),
                "observed": str(gate.get("observed", "Not recorded")),
                "next_action": GATE_GUIDANCE.get(
                    str(gate.get("name", "")),
                    ("", "Compare the observed result with the requirement and rerun unchanged."),
                )[1],
            }
            for gate in failures
        ]
        if args.json:
            _json(
                {
                    "authority": "This command cannot grant, schedule, review, place, or cancel orders.",
                    "current_evidence_policy": EVIDENCE_POLICY_VERSION,
                    "current_strategy_fingerprint": current_fingerprint,
                    "latest_receipt_uses_current_policy": latest_receipt_uses_current_policy,
                    "latest_receipt_matches_current_fingerprint": latest_receipt_matches_fingerprint,
                    "current_exact_evidence": evidence_ready,
                    "conditions": rows,
                    "external_responsibility": (
                        "The app does not collect or certify jurisdiction, account eligibility, "
                        "legal, tax, employment, residency, or business status."
                    ),
                    "evidence_failures": evidence_failures,
                }
            )
            return 0

        print("GRANDE Alpha activation assistant (local inspection only)")
        print(
            "This command cannot grant, schedule, review, place, or cancel orders. Scheduled auto-shadow "
            "is structurally read-only."
        )
        print()
        print(
            format_table(
                ["Condition", "Owner", "Status", "Current result", "Exact next action"],
                [
                    [
                        row["gate"],
                        row["owner"],
                        row["status"],
                        row["observed"],
                        row["action"],
                    ]
                    for row in rows
                ],
                args.width,
            )
        )
        print(
            "\nOutside-app responsibility: GRANDE Alpha does not collect or certify jurisdiction, "
            "account eligibility, legal, tax, employment, residency, or business status. Review "
            "applicable requirements with the broker and appropriately qualified professionals."
        )
        if evidence_failures:
            print("\nExact failed-evidence actions")
            print(
                format_table(
                    ["Gate", "Observed", "Exact next action"],
                    [
                        [failure["gate"], failure["observed"], failure["next_action"]]
                        for failure in evidence_failures
                    ],
                    args.width,
                )
            )
        print("\nNext command: .\\Morning Check.cmd (read-only), then use Live Readiness in the normal GUI.")
        return 0
    finally:
        store.close()


def command_evidence_show(args: argparse.Namespace) -> int:
    store = AuditStore()
    try:
        promotion = store.research_promotion(args.id)
        if promotion is None:
            raise ValueError("No matching evidence receipt exists; run the Evidence Lab first")
        _print_evidence(promotion, args)
        return 0
    finally:
        store.close()


def command_evidence_run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    bundle = asyncio.run(_bundle_from_args(args, config, require_evidence_ready=True))
    store = AuditStore()
    try:
        lab = run_evidence_lab(bundle, config, store, note=args.note)
        promotion = store.research_promotion(lab.promotion_id)
        if promotion is None:
            raise RuntimeError("Evidence completed but its local receipt could not be reloaded")
        _print_evidence(promotion, args)
        return 0
    finally:
        store.close()


def _print_data_report(report: DatasetReadinessReport, width: int | None) -> None:
    state = "INPUT READY" if report.input_ready else "NOT READY"
    digest = report.dataset_hash[:16] + "..." if report.dataset_hash else "unavailable"
    cadence = (
        "not applicable"
        if report.observed_cadence_seconds is None
        else f"{report.observed_cadence_seconds:g}s"
    )
    print(
        f"{report.label} | {state} for exact {report.target_interval} research input | "
        f"dataset {digest}"
    )
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
                    ["Range", f"{payload['range_start'] or 'first row'} through {payload['range_end'] or 'last row'}"],
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
            "\nINPUT READY" if payload["input_ready"] else "\nINPUT NOT READY - do not run Evidence Lab on this range."
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


def command_sandbox_run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    bundle = asyncio.run(_bundle_from_args(args, config))
    result = SandboxReplayEngine(config).run(bundle)
    store = AuditStore()
    try:
        if not args.no_save:
            _record_sandbox(store, config, bundle, result, args.note)
        payload = {
            "run_id": result.run_id,
            "source": result.source,
            "dataset_hash": bundle.dataset_hash,
            "metrics": result.metrics(),
            "fills": [fill.as_dict() for fill in result.fills],
            "saved": not args.no_save,
        }
        if args.json:
            _json(payload)
            return 0
        print(f"Sandbox run {result.run_id} | {'saved' if not args.no_save else 'not saved'}")
        print(f"Source: {result.source}")
        print(
            format_table(
                ["Metric", "Value"],
                _sandbox_metric_rows(result.metrics()),
                args.width,
            )
        )
        if args.fills:
            rows = result.fills[-args.fills :]
            print("\nVirtual fills")
            print(
                format_table(
                    ["Time", "Symbol", "Side", "Quantity", "Fill", "Realized P/L", "Reason"],
                    [
                        [
                            fill.timestamp.isoformat(),
                            fill.symbol,
                            fill.side.upper(),
                            f"{fill.quantity:.6f}",
                            f"${fill.price:,.2f}",
                            f"${fill.realized_pnl:+,.2f}" if fill.realized_pnl is not None else "—",
                            fill.reason,
                        ]
                        for fill in rows
                    ],
                    args.width,
                )
            )
        return 0
    finally:
        store.close()


def command_runs(args: argparse.Namespace) -> int:
    store = AuditStore()
    try:
        if args.id:
            run = store.sandbox_run(args.id)
            if run is None:
                raise ValueError("No sandbox run matches that complete run ID")
            if args.json:
                _json(run)
            else:
                print(format_table(["Metric", "Value"], _sandbox_metric_rows(run["metrics"]), args.width))
                print("\nVirtual fills")
                print(
                    format_table(
                        ["Time", "Symbol", "Side", "Quantity", "Fill", "Realized P/L", "Reason"],
                        [
                            [
                                fill["filled_at"],
                                fill["symbol"],
                                fill["side"].upper(),
                                fill["quantity"],
                                fill["price"],
                                fill["realized_pnl"],
                                fill["reason"],
                            ]
                            for fill in run["fills"]
                        ],
                        args.width,
                    )
                )
            return 0
        runs = store.recent_sandbox_runs(args.limit)
        if args.json:
            _json(runs)
            return 0
        rows = []
        for run in runs:
            metrics = json.loads(run["metrics_json"])
            rows.append(
                [
                    run["run_id"],
                    run["created_at"],
                    run["data_source"],
                    f"{float(metrics.get('return_pct', 0)):+.2f}%",
                    metrics.get("round_trips", 0),
                ]
            )
        print(format_table(["Run", "Time", "Source", "Return", "Trades"], rows, args.width))
        return 0
    finally:
        store.close()


def command_receipts(args: argparse.Namespace) -> int:
    store = AuditStore()
    try:
        receipts = store.recent_receipts(args.limit)
        if args.json:
            _json(receipts)
        else:
            print(
                format_table(
                    ["Time", "Severity", "Category", "Summary"],
                    [
                        [value["created_at"], value["severity"], value["category"], value["summary"]]
                        for value in receipts
                    ],
                    args.width,
                )
            )
        return 0
    finally:
        store.close()


def command_glossary(args: argparse.Namespace) -> int:
    query = (args.query or "").casefold()
    values = [
        [term, explanation]
        for term, explanation in sorted(TERM_HELP.items(), key=lambda item: item[0].casefold())
        if not query or query in f"{term} {explanation}".casefold()
    ]
    if args.json:
        _json({term: explanation for term, explanation in values})
    elif values:
        print(format_table(["Term", "Explanation"], values, args.width))
    else:
        print("No glossary terms matched that search.")
        return 1
    return 0


def command_plans(args: argparse.Namespace) -> int:
    entitlement = current_entitlement()
    upgrade_url = configured_upgrade_url()
    plans = [
        {
            **asdict(plan),
            "features": [asdict(feature) for feature in plan.features],
        }
        for plan in PRODUCT_PLANS
    ]
    payload = {
        "current_entitlement": asdict(entitlement),
        "plans": plans,
        "upgrade_information": {
            "configured": bool(upgrade_url),
            "url": upgrade_url,
            "checkout": False,
        },
        "never_paywalled": (
            "Evidence, provenance, risk, stop, privacy, and per-order consent controls"
        ),
    }
    if args.json:
        _json(payload)
        return 0

    rows = []
    for plan in PRODUCT_PLANS:
        feature_summary = "; ".join(
            f"{feature.status.value}: {feature.label}" for feature in plan.features
        )
        rows.append(
            [
                plan.name,
                plan.price_label,
                "CURRENT" if plan.plan_id == entitlement.plan_id else plan.availability_label,
                feature_summary,
            ]
        )
    print("GRANDE Alpha plans")
    print(
        f"Current plan: {entitlement.plan_name} ($0) via {entitlement.source}; "
        "no checkout or paid entitlement backend."
    )
    print()
    print(format_table(["Plan", "Price", "Status", "Features"], rows, args.width))
    print()
    print(
        "Never paywalled: evidence, provenance, risk, stop, privacy, and per-order consent controls."
    )
    print(
        f"Pro information: {upgrade_url} (information only; not checkout)"
        if upgrade_url
        else "Pro information: no URL configured; Pro remains coming soon."
    )
    return 0


def _output_options(parser: argparse.ArgumentParser, *, compact: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--width", type=int, help="Wrap the table to this terminal width")
    if compact:
        parser.add_argument("--compact", action="store_true", help="Hide per-failure guidance")


def _trading_date_arg(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO trading date in YYYY-MM-DD form") from exc


def _runtime_trace_options(
    parser: argparse.ArgumentParser,
    *,
    allow_manifest: bool,
    require_range: bool = False,
) -> None:
    parser.add_argument("--database", type=Path, required=True, help="GRANDE Alpha SQLite quote ledger")
    parser.add_argument("--bar-seconds", type=int, choices=range(1, 301), default=5)
    parser.add_argument(
        "--session",
        choices=("regular_hours", "extended_hours", "all_day_hours"),
        default="regular_hours",
    )
    parser.add_argument(
        "--start",
        type=_trading_date_arg,
        required=require_range,
        help="Inclusive first trading date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=_trading_date_arg,
        required=require_range,
        help="Inclusive last trading date (YYYY-MM-DD)",
    )
    if allow_manifest:
        parser.add_argument("--manifest", type=Path, help="Completed range-bound runtime-trace manifest")


def _source_options(
    parser: argparse.ArgumentParser,
    *,
    allow_runtime_trace: bool = False,
) -> None:
    source_choices = ["demo", "csv", "remote", "full-daily"]
    if allow_runtime_trace:
        source_choices.insert(2, "runtime-trace")
    parser.add_argument(
        "--source",
        choices=tuple(source_choices),
        default="demo",
        help="Research dataset source; demo is deterministic and never promotion-eligible",
    )
    parser.add_argument("--days", type=int, help="Calendar lookback")
    parser.add_argument("--csv", type=Path, help="Aligned QQQ/TQQQ/SQQQ CSV")
    if allow_runtime_trace:
        parser.add_argument("--database", type=Path, help="SQLite quote ledger for --source runtime-trace")
        parser.add_argument("--bar-seconds", type=int, choices=range(1, 301), default=5)
        parser.add_argument("--start", type=_trading_date_arg, help="Inclusive runtime-trace start date")
        parser.add_argument("--end", type=_trading_date_arg, help="Inclusive runtime-trace end date")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Provenance manifest; mandatory for CSV and runtime-trace Evidence Lab runs",
    )
    parser.add_argument("--interval", default="1m", help="CSV or remote interval, such as 5s, 1m, 5m, 60m")
    parser.add_argument("--acknowledge-community-data", action="store_true")
    parser.add_argument("--strategy", choices=sorted(STRATEGY_NAMES))
    parser.add_argument(
        "--session", choices=("regular_hours", "extended_hours", "all_day_hours")
    )
    parser.add_argument("--order-type", choices=("market", "limit"))
    parser.add_argument("--time-in-force", choices=("gfd", "gtc"))
    parser.add_argument("--starting-cash", type=float)
    parser.add_argument("--order-notional", type=float)
    parser.add_argument("--note", default="", help="Audit note saved with the research receipt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grande-alpha-cli",
        description=(
            "Local GRANDE Alpha research, evidence, receipt, and glossary companion. "
            "It has no command that bypasses GUI live-session consent."
        ),
    )
    parser.add_argument("--version", action="version", version=f"GRANDE Alpha {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="Show local permissions and evidence state")
    _output_options(status)
    status.set_defaults(func=command_status)

    activation = commands.add_parser(
        "activation",
        help="Show who owns every activation condition and the exact next action",
    )
    _output_options(activation)
    activation.set_defaults(func=command_activation)

    evidence = commands.add_parser("evidence", help="Show or run the exact Evidence Lab gate table")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_show = evidence_commands.add_parser("show", help="Show a saved evidence receipt")
    evidence_show.add_argument("--id", type=int, help="Promotion receipt ID; default is latest")
    evidence_show.add_argument("--failures-only", action="store_true")
    _output_options(evidence_show, compact=True)
    evidence_show.set_defaults(func=command_evidence_show)
    evidence_run = evidence_commands.add_parser("run", help="Run and record the shared evidence pipeline")
    _source_options(evidence_run, allow_runtime_trace=True)
    evidence_run.add_argument("--failures-only", action="store_true")
    _output_options(evidence_run, compact=True)
    evidence_run.set_defaults(func=command_evidence_run)

    data = commands.add_parser(
        "data", help="Audit historical-data readiness without running or reserving evidence"
    )
    data_commands = data.add_subparsers(dest="data_command", required=True)
    data_audit = data_commands.add_parser(
        "audit", help="Read-only audit of local caches, a CSV import, and the evidence ledger"
    )
    data_audit.add_argument("--csv", type=Path, help="Aligned QQQ/TQQQ/SQQQ source CSV")
    data_audit.add_argument(
        "--interval", help="Actual CSV bar interval; required with --csv and never inferred"
    )
    data_audit.add_argument("--manifest", type=Path, help="Dataset provenance manifest JSON")
    data_audit.add_argument(
        "--target-interval",
        default="5s",
        help="Exact runtime evidence interval to qualify against; default 5s",
    )
    data_audit.add_argument(
        "--cache-dir", type=Path, help="Cache directory to inspect when --csv is omitted"
    )
    data_audit.add_argument(
        "--database", type=Path, help="Evidence SQLite database to inventory read-only"
    )
    _output_options(data_audit)
    data_audit.set_defaults(func=command_data_audit)
    data_template = data_commands.add_parser(
        "manifest-template", help="Print the exact provenance-manifest template without writing a file"
    )
    data_template.add_argument("--target-interval", default="5s")
    data_template.set_defaults(func=command_data_manifest_template)

    runtime_trace = data_commands.add_parser(
        "runtime-trace",
        help="Audit or describe a synchronized runtime quote trace without broker access",
    )
    runtime_trace_commands = runtime_trace.add_subparsers(
        dest="runtime_trace_command",
        required=True,
    )
    runtime_trace_audit = runtime_trace_commands.add_parser(
        "audit",
        help="Audit one inclusive trace range read-only",
    )
    _runtime_trace_options(runtime_trace_audit, allow_manifest=True)
    _output_options(runtime_trace_audit)
    runtime_trace_audit.set_defaults(func=command_data_runtime_trace_audit)
    runtime_trace_template = runtime_trace_commands.add_parser(
        "manifest-template",
        help="Print a range-bound rights/provenance template without writing a file",
    )
    _runtime_trace_options(
        runtime_trace_template,
        allow_manifest=False,
        require_range=True,
    )
    runtime_trace_template.set_defaults(func=command_data_runtime_trace_manifest_template)

    sandbox = commands.add_parser("sandbox", help="Run a broker-isolated virtual replay")
    sandbox_commands = sandbox.add_subparsers(dest="sandbox_command", required=True)
    sandbox_run = sandbox_commands.add_parser("run", help="Run a virtual sandbox replay")
    _source_options(sandbox_run)
    sandbox_run.add_argument("--fills", type=int, default=20, help="Number of latest virtual fills to print")
    sandbox_run.add_argument("--no-save", action="store_true")
    _output_options(sandbox_run)
    sandbox_run.set_defaults(func=command_sandbox_run)

    runs = commands.add_parser("runs", help="List saved sandbox runs or inspect one")
    runs.add_argument("--id", help="Complete sandbox run ID")
    runs.add_argument("--limit", type=int, default=20)
    _output_options(runs)
    runs.set_defaults(func=command_runs)

    receipts = commands.add_parser("receipts", help="Show local audit receipts")
    receipts.add_argument("--limit", type=int, default=30)
    _output_options(receipts)
    receipts.set_defaults(func=command_receipts)

    glossary = commands.add_parser("glossary", help="Search the same definitions used by the GUI")
    glossary.add_argument("query", nargs="?")
    _output_options(glossary)
    glossary.set_defaults(func=command_glossary)

    plans = commands.add_parser(
        "plans", help="Show the free Community entitlement and truthful Pro roadmap"
    )
    _output_options(plans)
    plans.set_defaults(func=command_plans)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted; no broker order was submitted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            f"Local data directory: {user_data_path(APP_NAME, appauthor=False)}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

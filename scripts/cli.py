from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from grande_alpha import __version__
from grande_alpha.application.activation_guidance import decorate_readiness
from grande_alpha.application.gate_guidance import GATE_GUIDANCE, gate_detail, promotion_overview
from grande_alpha.configuration.config import APP_NAME, load_config
from grande_alpha.data.data_readiness import (
    DatasetReadinessReport,
    audit_cache_directory,
    audit_csv_dataset,
    audit_evidence_ledger,
    load_audited_csv_dataset,
    manifest_template,
)
from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.domain.policy import session_key
from grande_alpha.domain.product import PRODUCT_PLANS, configured_upgrade_url, current_entitlement
from grande_alpha.domain.terminology import TERM_HELP
from grande_alpha.execution.candidate_execution import (
    contract_from_app_and_sandbox,
    runtime_parity_assessment,
)
from grande_alpha.interfaces.cli.cli_table import format_table
from grande_alpha.interfaces.cli.config_cli import (
    command_config_import_legacy,
    command_config_show,
    command_config_upgrade,
    command_execution_store_upgrade,
)
from grande_alpha.persistence.store import AuditStore
from grande_alpha.research.evidence import (
    EVIDENCE_POLICY_VERSION,
    RUNTIME_SIZING_PARITY_CERTIFIED,
    STRATEGY_FINGERPRINT_FIELDS,
    strategy_fingerprint,
)
from grande_alpha.research.historical import (
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
from grande_alpha.research.research_service import run_evidence_lab
from grande_alpha.research.sandbox import SandboxConfig, SandboxReplayEngine, load_sandbox_config
from grande_alpha.strategy.core import STRATEGY_NAMES


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
            ["Mode", "OFFLINE STATUS", "This command grants no authority; run-live requires explicit terminal approvals"],
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
                    "authority": "This command cannot grant, review, place, or cancel orders.",
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
        print("This command cannot grant, review, place, or cancel orders.")
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
        print("\nNext step: open Live Readiness in the desktop application and run safe checks.")
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


def command_engine_readiness(args: argparse.Namespace) -> int:
    """Offline inventory: local evidence is not broker verification or live authority."""
    from grande_alpha.application.readiness import missing_requirements, recorded_preferences
    from grande_alpha.interfaces.cli.live_cli import load_policy

    workflow = getattr(args, "workflow", "current")
    setup_path = getattr(args, "setup", None)
    preferences = recorded_preferences(Path(setup_path) if setup_path else None)
    config = load_config()
    candidate = _current_runtime_candidate(config)
    contract = contract_from_app_and_sandbox(config, candidate)
    fingerprint = _current_runtime_fingerprint(config)
    policy_path = getattr(args, "policy", None)
    policy_issues = ["Complete the remaining account, asset, route, expiry and order-limit policy; recorded planning amounts alone are not a live grant"]
    if policy_path:
        _mode, grant = load_policy(Path(policy_path))
        policy_issues = []
        if grant.strategy_fingerprint != fingerprint:
            policy_issues.append("Policy fingerprint does not match the current strategy")
        for field in ("market_hours", "order_type", "time_in_force", "limit_offset_bps"):
            if getattr(grant, field) != getattr(config, field):
                policy_issues.append(f"Policy {field} does not match current configuration")
        capital = preferences["trading_capital_usd"]
        loss = preferences["daily_loss_threshold_usd"]
        if capital is not None and grant.max_total_exposure > capital:
            policy_issues.append("Policy exposure exceeds recorded trading capital")
        if loss is not None and grant.max_daily_loss > loss:
            policy_issues.append("Policy loss threshold exceeds the recorded loss threshold")
    store = AuditStore()
    try:
        evidence = store.current_live_evidence(fingerprint)
    finally:
        store.close()
    parity = runtime_parity_assessment(contract).as_dict()
    missing, optional = missing_requirements(
        workflow=workflow,
        evidence_present=evidence is not None,
        parity=parity,
        preferences=preferences,
        policy_issues=policy_issues,
    )
    report = {
        "workflow": workflow,
        "unattended_live_ready": False,
        "authority_granted": False,
        "broker_contacted": False,
        "strategy_fingerprint": fingerprint,
        "current_strategy_name": config.strategy_name,
        "exact_local_evidence_present": evidence is not None,
        "recorded_preferences": preferences,
        "requested_limits_checked": policy_path is not None and not policy_issues,
        "policy_issues": policy_issues,
        "runtime_parity": parity,
        "implemented": [
            "headless_shadow",
            "attended_live_cli",
            "explicit_session_policy",
            "mcp_contract_inspection",
            "research_only_pead_screen",
        ],
        "missing": missing,
        "optional_enhancements": optional,
    }
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


def command_session_run(args: argparse.Namespace) -> int:
    """Dispatch one explicit session mode without guessing a strategy or limit file."""
    from grande_alpha.interfaces.cli.headless import command_engine_run
    from grande_alpha.interfaces.cli.live_cli import command_live
    from grande_alpha.interfaces.cli.worker_cli import command_worker_run

    files = {"policy": args.policy, "candidate": args.candidate,
             "authorization": args.authorization, "earnings_database": args.earnings_database}
    if args.mode == "shadow":
        if args.strategy != "etf" or any(files.values()) or args.poll_seconds is not None:
            raise ValueError("Shadow mode supports the ETF strategy and no live policy or candidate files")
        args.duration = args.duration or 0
        return command_engine_run(args)
    if args.mode == "attended":
        if (args.strategy != "etf" or not args.policy or
                any(files[key] for key in ("candidate", "authorization", "earnings_database")) or
                args.duration is not None or args.poll_seconds is not None):
            raise ValueError("Attended ETF mode requires only --policy")
        args.unattended = False
        return command_live(args)
    if (args.strategy != "mixed" or not all(files[key] for key in
            ("candidate", "authorization", "earnings_database")) or args.policy or
            args.duration is not None):
        raise ValueError("Autonomous mixed mode requires --candidate, --authorization and --earnings-database")
    args.poll_seconds = args.poll_seconds or 5.0
    return command_worker_run(args)


def build_parser() -> argparse.ArgumentParser:
    from grande_alpha.data.earnings import command_screen, command_template
    from grande_alpha.data.earnings_feed import (
        command_fetch,
        command_import_event,
        command_key_delete,
        command_key_set,
        command_key_status,
        command_normalize_fact,
        command_record_fact,
        command_verify_event,
    )
    from grande_alpha.desktop.device_notifications import command_notifications
    from grande_alpha.execution.authorization import (
        command_authorization_check,
        command_authorization_revoke,
        command_authorization_template,
    )
    from grande_alpha.interfaces.cli.autonomous_cli import (
        command_autonomous_readiness,
        command_candidate_template,
        command_loss_recovery_ack,
    )
    from grande_alpha.interfaces.cli.headless import command_engine_inspect
    from grande_alpha.interfaces.cli.live_cli import (
        command_policy_check,
        command_policy_template,
    )
    from grande_alpha.interfaces.cli.worker_cli import (
        command_research_mcp,
        command_worker_control,
        command_worker_stop,
    )
    from grande_alpha.research.portfolio_replay import command_replay as command_portfolio_replay
    from grande_alpha.research.qualification import command_qualification_check
    from grande_alpha.research.qualification_evidence import (
        command_forward_append,
        command_forward_report,
        command_replay_report,
    )
    from grande_alpha.strategy.mixed_portfolio import command_plan
    from grande_alpha.strategy.mixed_portfolio import command_template as mixed_template

    parser = argparse.ArgumentParser(
        prog="grande-alpha-cli",
        description=(
            "GRANDE Alpha research and headless runtime. "
            "Live sessions require explicit limits and attended or standing authorization."
        ),
    )
    parser.add_argument("--version", action="version", version=f"GRANDE Alpha {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config", help="Inspect or explicitly upgrade local configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    broker = commands.add_parser("broker", help="Inspect the connected broker contract")
    broker_commands = broker.add_subparsers(dest="broker_command", required=True)
    data = commands.add_parser("data", help="Earnings and historical-data observations")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    research = commands.add_parser("research", help="Replay and evidence tools; no broker orders")
    research_commands = research.add_subparsers(dest="research_command", required=True)
    research_mcp = research_commands.add_parser(
        "mcp", help="Opt into the current worker's separate research-only MCP connection"
    )
    research_mcp_commands = research_mcp.add_subparsers(dest="research_mcp_command", required=True)
    for name in ("status", "enable", "disable"):
        research_mcp_commands.add_parser(name).set_defaults(func=command_research_mcp)
    session = commands.add_parser("session", help="Run or stop an explicit trading session")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    records = commands.add_parser("records", help="Read local activity, notifications and reports")
    records_commands = records.add_subparsers(dest="records_command", required=True)
    execution_upgrade = records_commands.add_parser(
        "upgrade-execution-store",
        help="Offline, backed-up upgrade of existing legacy execution records; stop the worker first",
    )
    execution_upgrade.add_argument("--audit", type=Path, required=True, help="Existing audit database")
    execution_upgrade.add_argument("--legacy-equity", type=Path, required=True,
                                   help="Existing equity_v1 execution database")
    execution_upgrade.add_argument("--backup-dir", type=Path, required=True,
                                   help="Directory to receive durable database snapshots")
    execution_upgrade.add_argument("--json", action="store_true")
    execution_upgrade.set_defaults(func=command_execution_store_upgrade)
    config_show = config_commands.add_parser("show", help="Read validated settings without writing files")
    config_show.add_argument("--path", type=Path, help="Configuration file to inspect")
    config_show.add_argument("--json", action="store_true")
    config_show.add_argument("--width", type=int)
    config_show.set_defaults(func=command_config_show)
    config_upgrade = config_commands.add_parser("upgrade", help="Back up and upgrade one existing configuration")
    config_upgrade.add_argument("--path", type=Path, help="Configuration file to upgrade")
    config_upgrade.add_argument("--json", action="store_true")
    config_upgrade.set_defaults(func=command_config_upgrade)
    config_import_legacy = config_commands.add_parser(
        "import-legacy", help="Explicitly copy recoverable legacy data without deleting its source"
    )
    config_import_legacy.add_argument("--source", type=Path, required=True, help="Legacy data directory")
    config_import_legacy.add_argument("--destination", type=Path, help="Target data directory")
    config_import_legacy.add_argument("--json", action="store_true")
    config_import_legacy.set_defaults(func=command_config_import_legacy)
    portfolio = research_commands.add_parser("portfolio", help="Broker-isolated mixed-allocation research")
    portfolio_commands = portfolio.add_subparsers(dest="portfolio_command", required=True)
    portfolio_template = portfolio_commands.add_parser("template", help="Mixed-portfolio research input template")
    portfolio_template.set_defaults(func=mixed_template)
    portfolio_plan = portfolio_commands.add_parser("plan", help="Compute research targets; never orders")
    portfolio_plan.add_argument("--input", required=True)
    portfolio_plan.set_defaults(func=command_plan)
    portfolio_replay = portfolio_commands.add_parser("replay", help="Causal multi-day paper accounting; no broker orders")
    portfolio_replay.add_argument("--input", required=True)
    portfolio_replay.set_defaults(func=command_portfolio_replay)
    replay_report = portfolio_commands.add_parser("replay-report", help="Compact cost-inclusive evidence report")
    replay_report.add_argument("--input", required=True)
    replay_report.set_defaults(func=command_replay_report)
    forward_append = portfolio_commands.add_parser("forward-append", help="Append a near-real-time paper frame")
    forward_append.add_argument("--database", required=True)
    forward_append.add_argument("--input", required=True)
    forward_append.set_defaults(func=command_forward_append)
    forward_report = portfolio_commands.add_parser("forward-report", help="Replay append-only forward frames")
    forward_report.add_argument("--database", required=True)
    forward_report.add_argument("--settings", required=True)
    forward_report.set_defaults(func=command_forward_report)
    notifications = records_commands.add_parser("notifications", help="On-device persistent notification inbox; no email")
    notifications.add_argument("--after", type=int, default=0)
    notifications.add_argument("--limit", type=int, default=100)
    notifications.add_argument("--unread", action="store_true")
    notifications.add_argument("--ack", type=int, help="Acknowledge one notification; never resumes trading")
    notifications.set_defaults(func=command_notifications)

    earnings = data_commands.add_parser("earnings", help="Point-in-time earnings observations and screening")
    earnings_commands = earnings.add_subparsers(dest="earnings_command", required=True)
    earnings_template = earnings_commands.add_parser("template", help="Print the unfilled earnings research schema")
    earnings_template.set_defaults(func=command_template)
    earnings_screen = earnings_commands.add_parser("screen", help="Screen supplied events; not a backtest or trading signal")
    earnings_screen.add_argument("--input", required=True)
    earnings_screen.set_defaults(func=command_screen)
    earnings_fetch = earnings_commands.add_parser("fetch", help="Capture one raw Alpha Vantage earnings response")
    earnings_fetch.add_argument("--database", required=True)
    earnings_fetch.add_argument("--symbol", required=True)
    earnings_fetch.add_argument("--dataset", choices=("EARNINGS", "EARNINGS_ESTIMATES"), required=True)
    earnings_fetch.add_argument("--cache-hours", type=float, default=12.0)
    earnings_fetch.add_argument("--max-requests-24h", type=int, default=25)
    earnings_fetch.set_defaults(func=command_fetch)
    earnings_key_set = earnings_commands.add_parser("key-set", help="Store the API key with a hidden prompt")
    earnings_key_set.set_defaults(func=command_key_set)
    earnings_key_status = earnings_commands.add_parser("key-status", help="Report key presence without printing it")
    earnings_key_status.set_defaults(func=command_key_status)
    earnings_key_delete = earnings_commands.add_parser("key-delete", help="Remove the stored API key")
    earnings_key_delete.set_defaults(func=command_key_delete)
    earnings_fact = earnings_commands.add_parser("record-fact", help="Bind one normalized fact to a raw response")
    earnings_fact.add_argument("--database", required=True)
    earnings_fact.add_argument("--input", required=True)
    earnings_fact.set_defaults(func=command_record_fact)
    earnings_normalize = earnings_commands.add_parser(
        "normalize", help="Extract one exact quarterly EPS fact from a stored raw response"
    )
    earnings_normalize.add_argument("--database", required=True)
    earnings_normalize.add_argument("--source-sha", required=True)
    earnings_normalize.add_argument("--kind", choices=("actual", "consensus"), required=True)
    earnings_normalize.add_argument("--period", required=True, help="Exact fiscal ending date from the provider")
    earnings_normalize.add_argument("--basis", required=True, help="Explicit accounting/estimate basis label")
    earnings_normalize.add_argument("--currency", required=True)
    earnings_normalize.set_defaults(func=command_normalize_fact)
    earnings_verify = earnings_commands.add_parser("verify-event", help="Verify an event against stored provider facts")
    earnings_verify.add_argument("--database", required=True)
    earnings_verify.add_argument("--input", required=True)
    earnings_verify.set_defaults(func=command_verify_event)
    earnings_import = earnings_commands.add_parser("import-event", help="Store a provider-verified earnings event")
    earnings_import.add_argument("--database", required=True)
    earnings_import.add_argument("--input", required=True)
    earnings_import.set_defaults(func=command_import_event)

    engine_commands = session_commands
    readiness = engine_commands.add_parser("readiness", help="Offline JSON inventory of unattended-live blockers")
    readiness.add_argument("--workflow", choices=("current", "earnings"), default="current")
    readiness.add_argument("--setup", type=Path, help="Optional planning amounts; never grants authority")
    readiness.add_argument("--policy", type=Path, help="Validate a complete policy against current configuration offline")
    readiness.set_defaults(func=command_engine_readiness)
    qualification = research_commands.add_parser("qualification-check", help="Check a historical certificate")
    qualification.add_argument("--certificate", required=True)
    qualification.add_argument("--candidate-digest", required=True)
    qualification.set_defaults(func=command_qualification_check)
    candidate_template = engine_commands.add_parser(
        "autonomous-template", help="Print the exact mixed live-candidate schema; grants nothing"
    )
    candidate_template.set_defaults(func=command_candidate_template)
    autonomous_readiness = engine_commands.add_parser(
        "autonomous-readiness", help="Validate mixed live artifacts offline; no broker contact"
    )
    for option in ("candidate", "authorization", "earnings-database"):
        autonomous_readiness.add_argument(f"--{option}", required=True)
    autonomous_readiness.set_defaults(func=command_autonomous_readiness)
    recovery_ack = engine_commands.add_parser(
        "loss-recovery-ack", help="Explicitly clear a manual loss pause; does not reset daily loss"
    )
    recovery_ack.add_argument("--account", required=True)
    recovery_ack.add_argument("--database", required=True)
    recovery_ack.set_defaults(func=command_loss_recovery_ack)
    authorization_template = engine_commands.add_parser(
        "authorization-template", help="Print an until-revoked mixed-engine permit schema"
    )
    authorization_template.set_defaults(func=command_authorization_template)
    authorization_check = engine_commands.add_parser(
        "authorization-check", help="Validate an exact account-bound permit; submits no order"
    )
    authorization_check.add_argument("--permit", required=True)
    authorization_check.add_argument("--account", required=True)
    authorization_check.add_argument("--scope-digest", required=True)
    authorization_check.set_defaults(func=command_authorization_check)
    authorization_revoke = engine_commands.add_parser(
        "authorization-revoke", help="Revoke one persistent mixed authorization; does not cancel orders"
    )
    authorization_revoke.add_argument("--permit", required=True)
    authorization_revoke.add_argument("--account", required=True)
    authorization_revoke.set_defaults(func=command_authorization_revoke)
    policy_template = engine_commands.add_parser("policy-template", help="Print an unarmed live-policy template")
    policy_template.set_defaults(func=command_policy_template)
    policy_check = engine_commands.add_parser("policy-check", help="Validate explicit limits offline; grants no authority")
    policy_check.add_argument("--policy", required=True)
    policy_check.set_defaults(func=command_policy_check)
    stop_run = engine_commands.add_parser("stop", help="Durably block new worker orders; does not claim broker cancellation")
    stop_run.set_defaults(func=command_worker_stop)
    worker_status = engine_commands.add_parser("status", help="Read the shared worker's actual status")
    worker_status.set_defaults(func=command_worker_control)
    worker_connect = engine_commands.add_parser("connect", help="Start the hidden worker and connect its broker session")
    worker_connect.add_argument("--authenticate", action="store_true", help="Permit interactive broker login")
    worker_connect.set_defaults(func=command_worker_control)
    worker_review = engine_commands.add_parser("review", help="Review exact autonomous candidate and broker account")
    worker_review.add_argument("--candidate", required=True)
    worker_review.add_argument("--authorization", required=True)
    worker_review.add_argument("--earnings-database", required=True)
    worker_review.add_argument("--poll-seconds", type=float, default=5.0)
    worker_review.set_defaults(func=command_worker_control)
    for name, description in (
        ("authorize", "Interactively approve the exact reviewed scope"),
        ("start", "Start an already reviewed and approved worker session"),
        ("revoke", "Interactively revoke exact account authorization"),
        ("recovery-ack", "Acknowledge manual loss recovery without resetting daily losses"),
        ("shutdown", "Stop trading and close the hidden worker"),
    ):
        worker_command = engine_commands.add_parser(name, help=description)
        worker_command.set_defaults(func=command_worker_control)
    engine_inspect = broker_commands.add_parser(
        "inspect", help="Inspect MCP tool schemas and descriptions; no orders or account reads"
    )
    engine_inspect.add_argument("--connect", action="store_true")
    engine_inspect.add_argument("--authenticate", action="store_true")
    engine_inspect.add_argument("--tool", action="append", help="Include full metadata for this tool; repeatable")
    engine_inspect.add_argument("--full", action="store_true", help="Include all metadata (potentially very large)")
    engine_inspect.set_defaults(func=command_engine_inspect)
    session_run = engine_commands.add_parser("run", help="Run one explicit mode and strategy")
    session_run.add_argument("--mode", choices=("shadow", "attended", "autonomous"), required=True)
    session_run.add_argument("--strategy", choices=("etf", "mixed"), required=True)
    session_run.add_argument("--connect", action="store_true")
    session_run.add_argument("--authenticate", action="store_true")
    session_run.add_argument("--policy", help="Attended ETF policy")
    session_run.add_argument("--candidate", help="Mixed candidate JSON")
    session_run.add_argument("--authorization", help="Persistent mixed authorization path")
    session_run.add_argument("--earnings-database", help="Earnings observation database")
    session_run.add_argument("--duration", type=float, help="Shadow duration in seconds; omitted runs until stopped")
    session_run.add_argument("--poll-seconds", type=float, help="Mixed engine polling interval")
    session_run.set_defaults(func=command_session_run)

    status = records_commands.add_parser("status", help="Show local permissions and evidence state")
    _output_options(status)
    status.set_defaults(func=command_status)

    activation = engine_commands.add_parser(
        "activation",
        help="Show who owns every activation condition and the exact next action",
    )
    _output_options(activation)
    activation.set_defaults(func=command_activation)

    evidence = research_commands.add_parser("evidence", help="Show or run the exact Evidence Lab gate table")
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

    sandbox = research_commands.add_parser("sandbox", help="Run a broker-isolated virtual replay")
    sandbox_commands = sandbox.add_subparsers(dest="sandbox_command", required=True)
    sandbox_run = sandbox_commands.add_parser("run", help="Run a virtual sandbox replay")
    _source_options(sandbox_run)
    sandbox_run.add_argument("--fills", type=int, default=20, help="Number of latest virtual fills to print")
    sandbox_run.add_argument("--no-save", action="store_true")
    _output_options(sandbox_run)
    sandbox_run.set_defaults(func=command_sandbox_run)

    runs = records_commands.add_parser("runs", help="List saved sandbox runs or inspect one")
    runs.add_argument("--id", help="Complete sandbox run ID")
    runs.add_argument("--limit", type=int, default=20)
    _output_options(runs)
    runs.set_defaults(func=command_runs)

    receipts = records_commands.add_parser("receipts", help="Show local audit receipts")
    receipts.add_argument("--limit", type=int, default=30)
    _output_options(receipts)
    receipts.set_defaults(func=command_receipts)

    glossary = config_commands.add_parser("glossary", help="Search the same definitions used by the GUI")
    glossary.add_argument("query", nargs="?")
    _output_options(glossary)
    glossary.set_defaults(func=command_glossary)

    plans = config_commands.add_parser(
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
        print("Interrupted. Check Robinhood for existing orders and positions; interruption is not cancellation.",
              file=sys.stderr)
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

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
from typing import Any

from grande_alpha.application.gate_guidance import gate_detail, promotion_overview
from grande_alpha.configuration.config import load_config
from grande_alpha.data.data_readiness import (
    load_audited_csv_dataset,
)
from grande_alpha.domain.product import PRODUCT_PLANS, configured_upgrade_url, current_entitlement
from grande_alpha.domain.terminology import TERM_HELP
from grande_alpha.interfaces.cli.cli_table import format_table
from grande_alpha.interfaces.cli.data_commands import _load_json_object, _runtime_trace_readiness
from grande_alpha.interfaces.cli.parser_common import output_options, source_options
from grande_alpha.interfaces.cli.render import print_json as _json
from grande_alpha.interfaces.cli.render import sandbox_metric_rows as _sandbox_metric_rows
from grande_alpha.persistence.store import AuditStore
from grande_alpha.research.bar_replay import SandboxReplayEngine
from grande_alpha.research.historical_models import HistoricalBundle
from grande_alpha.research.historical_source import (
    HistoricalDataProvider,
    deterministic_demo,
    load_csv_history,
)
from grande_alpha.research.research_service import run_evidence_lab
from grande_alpha.research.runtime_trace import load_runtime_quote_trace_with_row_count
from grande_alpha.research.sandbox import load_sandbox_config
from grande_alpha.research.sandbox_models import SandboxConfig


def add_research_commands(root_commands) -> None:
    from grande_alpha.interfaces.cli.worker_cli import add_research_mcp_command
    from grande_alpha.research.portfolio_replay import command_replay as command_portfolio_replay
    from grande_alpha.research.qualification import command_qualification_check
    from grande_alpha.research.qualification_evidence import (
        command_forward_append,
        command_forward_report,
        command_replay_report,
    )
    from grande_alpha.strategy.mixed_portfolio import command_plan
    from grande_alpha.strategy.mixed_portfolio import command_template as mixed_template

    research = root_commands.add_parser("research", help="Replay and evidence tools; no broker orders")
    commands = research.add_subparsers(dest="research_command", required=True)
    add_research_mcp_command(commands)

    portfolio = commands.add_parser("portfolio", help="Broker-isolated mixed-allocation research")
    portfolio_commands = portfolio.add_subparsers(dest="portfolio_command", required=True)
    template = portfolio_commands.add_parser("template", help="Mixed-portfolio research input template")
    template.set_defaults(func=mixed_template)
    plan = portfolio_commands.add_parser("plan", help="Compute research targets; never orders")
    plan.add_argument("--input", required=True)
    plan.set_defaults(func=command_plan)
    replay = portfolio_commands.add_parser("replay", help="Causal multi-day paper accounting; no broker orders")
    replay.add_argument("--input", required=True)
    replay.set_defaults(func=command_portfolio_replay)
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

    qualification = commands.add_parser("qualification-check", help="Check a historical certificate")
    qualification.add_argument("--certificate", required=True)
    qualification.add_argument("--candidate-digest", required=True)
    qualification.set_defaults(func=command_qualification_check)

    evidence = commands.add_parser("evidence", help="Show or run the exact Evidence Lab gate table")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_show = evidence_commands.add_parser("show", help="Show a saved evidence receipt")
    evidence_show.add_argument("--id", type=int, help="Promotion receipt ID; default is latest")
    evidence_show.add_argument("--failures-only", action="store_true")
    output_options(evidence_show, compact=True)
    evidence_show.set_defaults(func=command_evidence_show)

    evidence_run = evidence_commands.add_parser("run", help="Run and record the shared evidence pipeline")
    source_options(evidence_run, allow_runtime_trace=True)
    evidence_run.add_argument("--failures-only", action="store_true")
    output_options(evidence_run, compact=True)
    evidence_run.set_defaults(func=command_evidence_run)

    sandbox = commands.add_parser("sandbox", help="Run a broker-isolated virtual replay")
    sandbox_commands = sandbox.add_subparsers(dest="sandbox_command", required=True)
    sandbox_run = sandbox_commands.add_parser("run", help="Run a virtual sandbox replay")
    source_options(sandbox_run)
    sandbox_run.add_argument("--fills", type=int, default=20, help="Number of latest virtual fills to print")
    sandbox_run.add_argument("--no-save", action="store_true")
    output_options(sandbox_run)
    sandbox_run.set_defaults(func=command_sandbox_run)


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
            raise ValueError("Runtime-trace evidence requires --manifest PATH and a passing read-only audit")
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
            failures = ", ".join(check["name"] for check in readiness["checks"] if not check["passed"])
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
                failures = ", ".join(check.name for check in report.checks if not check.passed)
                raise ValueError(
                    f"CSV evidence input is not ready: {failures}. Run `data audit` for details; "
                    "no final holdout was reserved or evaluated"
                )
            return bundle
        return await asyncio.to_thread(load_csv_history, args.csv, args.interval)
    app_config = load_config()
    if not app_config.data.remote_market_data_enabled:
        raise RuntimeError("Community remote market data is disabled in Settings & Permissions")
    if not args.acknowledge_community_data:
        raise RuntimeError(
            "Remote research requires --acknowledge-community-data; no broker or account data is sent"
        )
    provider = HistoricalDataProvider()
    if args.source == "full-daily":
        return await provider.fetch_full_daily()
    if config.market_hours == "all_day_hours":
        raise ValueError(
            "24-hour evidence requires an appropriate imported CSV; community data is incomplete"
        )
    maximum_days = {"1m": 7, "5m": 60, "60m": 730}
    if args.interval not in maximum_days:
        raise ValueError("Remote intervals are 1m, 5m, or 60m; custom second bars require --source csv")
    if config.lookback_days > maximum_days[args.interval]:
        raise ValueError(
            f"Remote {args.interval} history is capped at {maximum_days[args.interval]} calendar days"
        )
    return await provider.fetch(config.lookback_days, args.interval, market_hours=config.market_hours)


def _record_sandbox(
    store: AuditStore, config: SandboxConfig, bundle: HistoricalBundle, result, note: str
) -> None:
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
        "never_paywalled": ("Evidence, provenance, risk, stop, privacy, and per-order consent controls"),
    }
    if args.json:
        _json(payload)
        return 0

    rows = []
    for plan in PRODUCT_PLANS:
        feature_summary = "; ".join(f"{feature.status.value}: {feature.label}" for feature in plan.features)
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
    print("Never paywalled: evidence, provenance, risk, stop, privacy, and per-order consent controls.")
    print(
        f"Pro information: {upgrade_url} (information only; not checkout)"
        if upgrade_url
        else "Pro information: no URL configured; Pro remains coming soon."
    )
    return 0

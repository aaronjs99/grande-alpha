from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import textwrap
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from grande_alpha import __version__
from grande_alpha.config import data_dir, load_config
from grande_alpha.gate_guidance import gate_detail, promotion_overview
from grande_alpha.historical import (
    HistoricalBundle,
    HistoricalDataProvider,
    deterministic_demo,
    load_csv_history,
)
from grande_alpha.research_service import run_evidence_lab
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine, load_sandbox_config
from grande_alpha.storage import AuditStore
from grande_alpha.strategy import STRATEGY_NAMES
from grande_alpha.ui.glossary import TERM_HELP

CLI_WIDTHS: dict[str, int] = {
    "Gate": 22,
    "Status": 6,
    "Observed": 31,
    "Requirement": 43,
    "Time": 25,
    "Severity": 9,
    "Category": 20,
    "Summary": 55,
    "Run": 12,
    "Source": 32,
    "Metric": 24,
    "Value": 28,
}


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("\r", " ").replace("\n", " ")


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


async def _bundle_from_args(args: argparse.Namespace, config: SandboxConfig) -> HistoricalBundle:
    if args.source == "demo":
        return await asyncio.to_thread(deterministic_demo, config.lookback_days)
    if args.source == "csv":
        if args.csv is None:
            raise ValueError("--csv PATH is required when --source csv is selected")
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
        passes = (
            f"{sum(bool(gate.get('passed', False)) for gate in latest['gates'])}/{len(latest['gates'])}"
            if latest
            else "none"
        )
        rows = [
            ["Version", __version__, "Installed GRANDE Alpha Python package"],
            ["Mode", "RESEARCH / LOCKED", "This CLI never grants standing live-order authority"],
            ["Broker-data permission", "ENABLED" if config.broker_connection_enabled else "DISABLED", "Local setting only; no broker call was made"],
            ["Real-order setting", "ENABLED" if config.live_trading_enabled else "DISABLED", "The GUI still requires matching evidence and a bounded live grant"],
            ["Remote-data permission", "ENABLED" if config.remote_market_data_enabled else "DISABLED", "CLI downloads also require an explicit acknowledgement flag"],
            ["Latest evidence", latest["status"] if latest else "NONE", f"{passes} gates passed"],
            ["Local audit database", str(store.path), "Receipts, virtual runs, and evidence only"],
        ]
        if args.json:
            _json({row[0]: {"value": row[1], "explanation": row[2]} for row in rows})
        else:
            print(format_table(["Item", "Value", "Explanation"], rows, args.width))
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
    bundle = asyncio.run(_bundle_from_args(args, config))
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


def _output_options(parser: argparse.ArgumentParser, *, compact: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--width", type=int, help="Wrap the table to this terminal width")
    if compact:
        parser.add_argument("--compact", action="store_true", help="Hide per-failure guidance")


def _source_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        choices=("demo", "csv", "remote", "full-daily"),
        default="demo",
        help="Research dataset source; demo is deterministic and never promotion-eligible",
    )
    parser.add_argument("--days", type=int, help="Calendar lookback")
    parser.add_argument("--csv", type=Path, help="Aligned QQQ/TQQQ/SQQQ CSV")
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

    evidence = commands.add_parser("evidence", help="Show or run the exact Evidence Lab gate table")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_show = evidence_commands.add_parser("show", help="Show a saved evidence receipt")
    evidence_show.add_argument("--id", type=int, help="Promotion receipt ID; default is latest")
    evidence_show.add_argument("--failures-only", action="store_true")
    _output_options(evidence_show, compact=True)
    evidence_show.set_defaults(func=command_evidence_show)
    evidence_run = evidence_commands.add_parser("run", help="Run and record the shared evidence pipeline")
    _source_options(evidence_run)
    evidence_run.add_argument("--failures-only", action="store_true")
    _output_options(evidence_run, compact=True)
    evidence_run.set_defaults(func=command_evidence_run)

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
        print(f"Local data directory: {data_dir()}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

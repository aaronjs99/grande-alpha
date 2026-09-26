from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from platformdirs import user_data_path

from grande_alpha import __version__
from grande_alpha.configuration.config import APP_NAME
from grande_alpha.interfaces.cli.config_cli import (
    command_config_import_legacy,
    command_config_show,
    command_config_upgrade,
    command_execution_store_upgrade,
)
from grande_alpha.interfaces.cli.data_commands import (
    command_data_audit,
    command_data_manifest_template,
    command_data_runtime_trace_audit,
    command_data_runtime_trace_manifest_template,
)
from grande_alpha.interfaces.cli.record_commands import (
    command_notifications,
    command_receipts,
    command_runs,
    command_status,
)
from grande_alpha.interfaces.cli.research_commands import (
    command_evidence_run,
    command_evidence_show,
    command_glossary,
    command_plans,
    command_sandbox_run,
)
from grande_alpha.strategy.core import STRATEGY_NAMES


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
    parser.add_argument("--session", choices=("regular_hours", "extended_hours", "all_day_hours"))
    parser.add_argument("--order-type", choices=("market", "limit"))
    parser.add_argument("--time-in-force", choices=("gfd", "gtc"))
    parser.add_argument("--starting-cash", type=float)
    parser.add_argument("--order-notional", type=float)
    parser.add_argument("--note", default="", help="Audit note saved with the research receipt")


def command_session_run(args: argparse.Namespace) -> int:
    """Run the sole supported live route through the shared worker."""
    from grande_alpha.interfaces.cli.worker_cli import command_worker_run

    return command_worker_run(args)


def build_parser() -> argparse.ArgumentParser:
    from grande_alpha.interfaces.cli.broker_cli import command_engine_inspect
    from grande_alpha.interfaces.cli.earnings_commands import (
        command_earnings_fetch,
        command_earnings_import_event,
        command_earnings_key_delete,
        command_earnings_key_set,
        command_earnings_key_status,
        command_earnings_normalize_fact,
        command_earnings_record_fact,
        command_earnings_screen,
        command_earnings_template,
        command_earnings_verify_event,
    )
    from grande_alpha.interfaces.cli.worker_cli import (
        command_candidate_template,
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
            "Live sessions require explicit limits and exact-scope authorization."
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
    session = commands.add_parser("session", help="Control the mixed stock/ETF trading worker")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    records = commands.add_parser("records", help="Read local activity, notifications and reports")
    records_commands = records.add_subparsers(dest="records_command", required=True)
    execution_upgrade = records_commands.add_parser(
        "upgrade-execution-store",
        help="Offline, backed-up upgrade of existing legacy execution records; stop the worker first",
    )
    execution_upgrade.add_argument("--audit", type=Path, required=True, help="Existing audit database")
    execution_upgrade.add_argument(
        "--legacy-equity", type=Path, required=True, help="Existing equity_v1 execution database"
    )
    execution_upgrade.add_argument(
        "--backup-dir", type=Path, required=True, help="Directory to receive durable database snapshots"
    )
    execution_upgrade.add_argument("--json", action="store_true")
    execution_upgrade.set_defaults(func=command_execution_store_upgrade)
    config_show = config_commands.add_parser("show", help="Read validated settings without writing files")
    config_show.add_argument("--path", type=Path, help="Configuration file to inspect")
    config_show.add_argument("--json", action="store_true")
    config_show.add_argument("--width", type=int)
    config_show.set_defaults(func=command_config_show)
    config_upgrade = config_commands.add_parser(
        "upgrade", help="Back up and upgrade one existing configuration"
    )
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
    portfolio_template = portfolio_commands.add_parser(
        "template", help="Mixed-portfolio research input template"
    )
    portfolio_template.set_defaults(func=mixed_template)
    portfolio_plan = portfolio_commands.add_parser("plan", help="Compute research targets; never orders")
    portfolio_plan.add_argument("--input", required=True)
    portfolio_plan.set_defaults(func=command_plan)
    portfolio_replay = portfolio_commands.add_parser(
        "replay", help="Causal multi-day paper accounting; no broker orders"
    )
    portfolio_replay.add_argument("--input", required=True)
    portfolio_replay.set_defaults(func=command_portfolio_replay)
    replay_report = portfolio_commands.add_parser(
        "replay-report", help="Compact cost-inclusive evidence report"
    )
    replay_report.add_argument("--input", required=True)
    replay_report.set_defaults(func=command_replay_report)
    forward_append = portfolio_commands.add_parser(
        "forward-append", help="Append a near-real-time paper frame"
    )
    forward_append.add_argument("--database", required=True)
    forward_append.add_argument("--input", required=True)
    forward_append.set_defaults(func=command_forward_append)
    forward_report = portfolio_commands.add_parser("forward-report", help="Replay append-only forward frames")
    forward_report.add_argument("--database", required=True)
    forward_report.add_argument("--settings", required=True)
    forward_report.set_defaults(func=command_forward_report)
    notifications = records_commands.add_parser(
        "notifications", help="On-device persistent notification inbox; no email"
    )
    notifications.add_argument("--after", type=int, default=0)
    notifications.add_argument("--limit", type=int, default=100)
    notifications.add_argument("--unread", action="store_true")
    notifications.add_argument("--ack", type=int, help="Acknowledge one notification; never resumes trading")
    notifications.set_defaults(func=command_notifications)

    earnings = data_commands.add_parser("earnings", help="Point-in-time earnings observations and screening")
    earnings_commands = earnings.add_subparsers(dest="earnings_command", required=True)
    earnings_template = earnings_commands.add_parser(
        "template", help="Print the unfilled earnings research schema"
    )
    earnings_template.set_defaults(func=command_earnings_template)
    earnings_screen = earnings_commands.add_parser(
        "screen", help="Screen supplied events; not a backtest or trading signal"
    )
    earnings_screen.add_argument("--input", required=True)
    earnings_screen.set_defaults(func=command_earnings_screen)
    earnings_fetch = earnings_commands.add_parser(
        "fetch", help="Capture one raw Alpha Vantage earnings response"
    )
    earnings_fetch.add_argument("--database", required=True)
    earnings_fetch.add_argument("--symbol", required=True)
    earnings_fetch.add_argument("--dataset", choices=("EARNINGS", "EARNINGS_ESTIMATES"), required=True)
    earnings_fetch.add_argument("--cache-hours", type=float, default=12.0)
    earnings_fetch.add_argument("--max-requests-24h", type=int, default=25)
    earnings_fetch.set_defaults(func=command_earnings_fetch)
    earnings_key_set = earnings_commands.add_parser("key-set", help="Store the API key with a hidden prompt")
    earnings_key_set.set_defaults(func=command_earnings_key_set)
    earnings_key_status = earnings_commands.add_parser(
        "key-status", help="Report key presence without printing it"
    )
    earnings_key_status.set_defaults(func=command_earnings_key_status)
    earnings_key_delete = earnings_commands.add_parser("key-delete", help="Remove the stored API key")
    earnings_key_delete.set_defaults(func=command_earnings_key_delete)
    earnings_fact = earnings_commands.add_parser(
        "record-fact", help="Bind one normalized fact to a raw response"
    )
    earnings_fact.add_argument("--database", required=True)
    earnings_fact.add_argument("--input", required=True)
    earnings_fact.set_defaults(func=command_earnings_record_fact)
    earnings_normalize = earnings_commands.add_parser(
        "normalize", help="Extract one exact quarterly EPS fact from a stored raw response"
    )
    earnings_normalize.add_argument("--database", required=True)
    earnings_normalize.add_argument("--source-sha", required=True)
    earnings_normalize.add_argument("--kind", choices=("actual", "consensus"), required=True)
    earnings_normalize.add_argument(
        "--period", required=True, help="Exact fiscal ending date from the provider"
    )
    earnings_normalize.add_argument("--basis", required=True, help="Explicit accounting/estimate basis label")
    earnings_normalize.add_argument("--currency", required=True)
    earnings_normalize.set_defaults(func=command_earnings_normalize_fact)
    earnings_verify = earnings_commands.add_parser(
        "verify-event", help="Verify an event against stored provider facts"
    )
    earnings_verify.add_argument("--database", required=True)
    earnings_verify.add_argument("--input", required=True)
    earnings_verify.set_defaults(func=command_earnings_verify_event)
    earnings_import = earnings_commands.add_parser(
        "import-event", help="Store a provider-verified earnings event"
    )
    earnings_import.add_argument("--database", required=True)
    earnings_import.add_argument("--input", required=True)
    earnings_import.set_defaults(func=command_earnings_import_event)

    engine_commands = session_commands
    qualification = research_commands.add_parser("qualification-check", help="Check a historical certificate")
    qualification.add_argument("--certificate", required=True)
    qualification.add_argument("--candidate-digest", required=True)
    qualification.set_defaults(func=command_qualification_check)
    candidate = engine_commands.add_parser(
        "candidate-template", help="Print an unfilled mixed stock/ETF scope; grants nothing"
    )
    candidate.set_defaults(func=command_candidate_template)
    stop_run = engine_commands.add_parser(
        "stop", help="Durably block new worker orders; does not claim broker cancellation"
    )
    stop_run.set_defaults(func=command_worker_stop)
    worker_status = engine_commands.add_parser("status", help="Read the shared worker's actual status")
    worker_status.set_defaults(func=command_worker_control)
    worker_connect = engine_commands.add_parser(
        "connect", help="Start the hidden worker and connect its broker session"
    )
    worker_connect.add_argument("--authenticate", action="store_true", help="Permit interactive broker login")
    worker_connect.set_defaults(func=command_worker_control)
    worker_review = engine_commands.add_parser(
        "review", help="Review exact autonomous candidate and broker account"
    )
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
    engine_inspect.add_argument(
        "--tool", action="append", help="Include full metadata for this tool; repeatable"
    )
    engine_inspect.add_argument(
        "--full", action="store_true", help="Include all metadata (potentially very large)"
    )
    engine_inspect.set_defaults(func=command_engine_inspect)
    session_run = engine_commands.add_parser(
        "run", help="Interactively review, authorize, and start the shared worker"
    )
    session_run.add_argument("--candidate", required=True, help="Mixed stock/ETF candidate JSON")
    session_run.add_argument("--authorization", required=True, help="Exact-scope authorization path")
    session_run.add_argument("--earnings-database", required=True, help="Earnings observation database")
    session_run.add_argument("--poll-seconds", type=float, default=5.0)
    session_run.set_defaults(func=command_session_run)

    status = records_commands.add_parser("status", help="Show local permissions and evidence state")
    _output_options(status)
    status.set_defaults(func=command_status)

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
    data_audit.add_argument("--cache-dir", type=Path, help="Cache directory to inspect when --csv is omitted")
    data_audit.add_argument("--database", type=Path, help="Evidence SQLite database to inventory read-only")
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
        print(
            "Interrupted. Check Robinhood for existing orders and positions; interruption is not cancellation.",
            file=sys.stderr,
        )
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

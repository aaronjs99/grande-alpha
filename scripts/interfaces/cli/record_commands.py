from __future__ import annotations

import argparse
import json
from pathlib import Path

from grande_alpha import __version__
from grande_alpha.interfaces.cli.cli_table import format_table
from grande_alpha.interfaces.cli.parser_common import output_options
from grande_alpha.interfaces.cli.render import print_json as _json
from grande_alpha.interfaces.cli.render import sandbox_metric_rows as _sandbox_metric_rows
from grande_alpha.persistence.store import AuditStore


def add_record_commands(root_commands) -> None:
    from grande_alpha.interfaces.cli.config_cli import command_execution_store_upgrade

    records = root_commands.add_parser("records", help="Read local activity, notifications and reports")
    commands = records.add_subparsers(dest="records_command", required=True)
    upgrade = commands.add_parser(
        "upgrade-execution-store",
        help="Offline, backed-up upgrade of existing legacy execution records; stop the worker first",
    )
    upgrade.add_argument("--audit", type=Path, required=True, help="Existing audit database")
    upgrade.add_argument(
        "--legacy-equity", type=Path, required=True, help="Existing equity_v1 execution database"
    )
    upgrade.add_argument(
        "--backup-dir", type=Path, required=True, help="Directory to receive durable database snapshots"
    )
    upgrade.add_argument("--json", action="store_true")
    upgrade.set_defaults(func=command_execution_store_upgrade)

    notifications = commands.add_parser(
        "notifications", help="On-device persistent notification inbox; no email"
    )
    notifications.add_argument("--after", type=int, default=0)
    notifications.add_argument("--limit", type=int, default=100)
    notifications.add_argument("--unread", action="store_true")
    notifications.add_argument("--ack", type=int, help="Acknowledge one notification; never resumes trading")
    notifications.set_defaults(func=command_notifications)

    status = commands.add_parser("status", help="Show local permissions and evidence state")
    output_options(status)
    status.set_defaults(func=command_status)

    runs = commands.add_parser("runs", help="List saved sandbox runs or inspect one")
    runs.add_argument("--id", help="Complete sandbox run ID")
    runs.add_argument("--limit", type=int, default=20)
    output_options(runs)
    runs.set_defaults(func=command_runs)

    receipts = commands.add_parser("receipts", help="Show local audit receipts")
    receipts.add_argument("--limit", type=int, default=30)
    output_options(receipts)
    receipts.set_defaults(func=command_receipts)


def command_status(args: argparse.Namespace) -> int:
    """Summarize historical local records; session status comes from the worker."""
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
            [
                "Latest historical receipt",
                latest["status"] if latest else "NONE",
                f"{passes} research checks; does not authorize trading",
            ],
            ["Local records", str(store.path), "Use session status for the active worker"],
        ]
        if args.json:
            _json({row[0]: {"value": row[1], "explanation": row[2]} for row in rows})
        else:
            print(format_table(["Item", "Value", "Explanation"], rows, args.width))
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


def command_notifications(args: argparse.Namespace) -> int:
    """Read or acknowledge the local notification inbox."""
    store = AuditStore()
    try:
        if args.ack is not None:
            store.acknowledge_notification(args.ack)
        notifications = store.device_notifications(
            after_id=args.after,
            limit=args.limit,
            unread_only=args.unread,
        )
        print(json.dumps(notifications, indent=2))
        return 0
    finally:
        store.close()

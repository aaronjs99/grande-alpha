"""General dataset commands; runtime-trace commands register through their own module."""

from __future__ import annotations

import argparse
from pathlib import Path

from platformdirs import user_data_path

from grande_alpha.configuration.config import APP_NAME
from grande_alpha.data.data_readiness import (
    DatasetReadinessReport,
    audit_cache_directory,
    audit_csv_dataset,
)
from grande_alpha.data.dataset_manifest import manifest_template
from grande_alpha.data.evidence_ledger import audit_evidence_ledger
from grande_alpha.interfaces.cli.cli_table import format_table
from grande_alpha.interfaces.cli.parser_common import output_options
from grande_alpha.interfaces.cli.render import print_json as _json

# Keep the existing import paths available while the runtime-trace implementation
# lives with its own command group and reusable data qualification function.
from grande_alpha.interfaces.cli.runtime_trace_commands import (
    _load_json_object as _load_json_object,
)
from grande_alpha.interfaces.cli.runtime_trace_commands import (
    _load_runtime_trace_from_data_args as _load_runtime_trace_from_data_args,
)
from grande_alpha.interfaces.cli.runtime_trace_commands import (
    _runtime_trace_readiness as _runtime_trace_readiness,
)
from grande_alpha.interfaces.cli.runtime_trace_commands import (
    add_runtime_trace_commands,
)
from grande_alpha.interfaces.cli.runtime_trace_commands import (
    command_data_runtime_trace_audit as command_data_runtime_trace_audit,
)
from grande_alpha.interfaces.cli.runtime_trace_commands import (
    command_data_runtime_trace_manifest_template as command_data_runtime_trace_manifest_template,
)


def add_data_commands(root_commands) -> None:
    data = root_commands.add_parser("data", help="Earnings and historical-data observations")
    commands = data.add_subparsers(dest="data_command", required=True)
    from grande_alpha.interfaces.cli.earnings_commands import add_earnings_commands

    add_earnings_commands(commands)

    audit = commands.add_parser(
        "audit", help="Read-only audit of local caches, a CSV import, and the evidence ledger"
    )
    audit.add_argument("--csv", type=Path, help="Aligned QQQ/TQQQ/SQQQ source CSV")
    audit.add_argument(
        "--interval", help="Actual CSV bar interval; required with --csv and never inferred"
    )
    audit.add_argument("--manifest", type=Path, help="Dataset provenance manifest JSON")
    audit.add_argument(
        "--target-interval",
        default="5s",
        help="Exact runtime evidence interval to qualify against; default 5s",
    )
    audit.add_argument("--cache-dir", type=Path, help="Cache directory to inspect when --csv is omitted")
    audit.add_argument("--database", type=Path, help="Evidence SQLite database to inventory read-only")
    output_options(audit)
    audit.set_defaults(func=command_data_audit)

    template = commands.add_parser(
        "manifest-template", help="Print the exact provenance-manifest template without writing a file"
    )
    template.add_argument("--target-interval", default="5s")
    template.set_defaults(func=command_data_manifest_template)

    add_runtime_trace_commands(commands)





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

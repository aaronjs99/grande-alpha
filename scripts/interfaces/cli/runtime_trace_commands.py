"""CLI registration and presentation for read-only runtime-trace commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from grande_alpha.data.runtime_trace_readiness import runtime_trace_readiness as _runtime_trace_readiness
from grande_alpha.interfaces.cli.cli_table import format_table
from grande_alpha.interfaces.cli.parser_common import output_options, runtime_trace_options
from grande_alpha.interfaces.cli.render import print_json as _json
from grande_alpha.research.historical_models import HistoricalBundle
from grande_alpha.research.runtime_trace import load_runtime_quote_trace_with_row_count
from grande_alpha.research.runtime_trace_provenance import runtime_trace_manifest_template


def add_runtime_trace_commands(commands) -> None:
    trace = commands.add_parser(
        "runtime-trace",
        help="Audit or describe a synchronized runtime quote trace without broker access",
    )
    trace_commands = trace.add_subparsers(dest="runtime_trace_command", required=True)
    trace_audit = trace_commands.add_parser("audit", help="Audit one inclusive trace range read-only")
    runtime_trace_options(trace_audit, allow_manifest=True)
    output_options(trace_audit)
    trace_audit.set_defaults(func=command_data_runtime_trace_audit)

    trace_template = trace_commands.add_parser(
        "manifest-template",
        help="Print a range-bound rights/provenance template without writing a file",
    )
    runtime_trace_options(trace_template, allow_manifest=False, require_range=True)
    trace_template.set_defaults(func=command_data_runtime_trace_manifest_template)


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

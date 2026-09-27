"""Argument helpers shared by CLI command groups."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from grande_alpha.strategy.core import STRATEGY_NAMES


def output_options(parser: argparse.ArgumentParser, *, compact: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--width", type=int, help="Wrap the table to this terminal width")
    if compact:
        parser.add_argument("--compact", action="store_true", help="Hide per-failure guidance")


def trading_date_arg(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO trading date in YYYY-MM-DD form") from exc


def runtime_trace_options(
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
        type=trading_date_arg,
        required=require_range,
        help="Inclusive first trading date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=trading_date_arg,
        required=require_range,
        help="Inclusive last trading date (YYYY-MM-DD)",
    )
    if allow_manifest:
        parser.add_argument("--manifest", type=Path, help="Completed range-bound runtime-trace manifest")


def source_options(
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
        parser.add_argument("--start", type=trading_date_arg, help="Inclusive runtime-trace start date")
        parser.add_argument("--end", type=trading_date_arg, help="Inclusive runtime-trace end date")
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

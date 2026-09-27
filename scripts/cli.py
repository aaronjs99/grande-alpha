"""Command-line entry point for research and shared worker controls."""

from __future__ import annotations

import argparse
import sys

from platformdirs import user_data_path

from grande_alpha import __version__
from grande_alpha.configuration.config import APP_NAME
from grande_alpha.interfaces.cli.broker_cli import add_broker_commands
from grande_alpha.interfaces.cli.config_cli import add_config_commands
from grande_alpha.interfaces.cli.data_commands import add_data_commands
from grande_alpha.interfaces.cli.record_commands import add_record_commands
from grande_alpha.interfaces.cli.research_commands import add_research_commands
from grande_alpha.interfaces.cli.worker_cli import add_session_commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grande-alpha-cli",
        description=(
            "GRANDE Alpha research and headless runtime. "
            "Live sessions require explicit limits and exact-scope authorization."
        ),
    )
    parser.add_argument("--version", action="version", version=f"GRANDE Alpha {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    add_config_commands(commands)
    add_broker_commands(commands)
    add_data_commands(commands)
    add_research_commands(commands)
    add_session_commands(commands)
    add_record_commands(commands)
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

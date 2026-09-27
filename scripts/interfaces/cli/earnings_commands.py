"""Terminal commands for earnings data collection and research screening."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from grande_alpha.configuration.json_inputs import load_json
from grande_alpha.data.earnings import screen, template
from grande_alpha.data.earnings_feed import (
    AlphaVantageEarningsClient,
    EarningsObservationStore,
    delete_api_key,
    load_api_key,
    store_api_key,
)


def add_earnings_commands(data_commands) -> None:
    earnings = data_commands.add_parser("earnings", help="Point-in-time earnings observations and screening")
    commands = earnings.add_subparsers(dest="earnings_command", required=True)
    template = commands.add_parser("template", help="Print the unfilled earnings research schema")
    template.set_defaults(func=command_earnings_template)

    screen = commands.add_parser("screen", help="Screen supplied events; not a backtest or trading signal")
    screen.add_argument("--input", required=True)
    screen.set_defaults(func=command_earnings_screen)

    fetch = commands.add_parser("fetch", help="Capture one raw Alpha Vantage earnings response")
    fetch.add_argument("--database", required=True)
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--dataset", choices=("EARNINGS", "EARNINGS_ESTIMATES"), required=True)
    fetch.add_argument("--cache-hours", type=float, default=12.0)
    fetch.add_argument("--max-requests-24h", type=int, default=25)
    fetch.set_defaults(func=command_earnings_fetch)

    key_set = commands.add_parser("key-set", help="Store the API key with a hidden prompt")
    key_set.set_defaults(func=command_earnings_key_set)
    key_status = commands.add_parser("key-status", help="Report key presence without printing it")
    key_status.set_defaults(func=command_earnings_key_status)
    key_delete = commands.add_parser("key-delete", help="Remove the stored API key")
    key_delete.set_defaults(func=command_earnings_key_delete)

    fact = commands.add_parser("record-fact", help="Bind one normalized fact to a raw response")
    fact.add_argument("--database", required=True)
    fact.add_argument("--input", required=True)
    fact.set_defaults(func=command_earnings_record_fact)

    normalize = commands.add_parser(
        "normalize", help="Extract one exact quarterly EPS fact from a stored raw response"
    )
    normalize.add_argument("--database", required=True)
    normalize.add_argument("--source-sha", required=True)
    normalize.add_argument("--kind", choices=("actual", "consensus"), required=True)
    normalize.add_argument("--period", required=True, help="Exact fiscal ending date from the provider")
    normalize.add_argument("--basis", required=True, help="Explicit accounting/estimate basis label")
    normalize.add_argument("--currency", required=True)
    normalize.set_defaults(func=command_earnings_normalize_fact)

    verify = commands.add_parser("verify-event", help="Verify an event against stored provider facts")
    verify.add_argument("--database", required=True)
    verify.add_argument("--input", required=True)
    verify.set_defaults(func=command_earnings_verify_event)

    import_event = commands.add_parser("import-event", help="Store a provider-verified earnings event")
    import_event.add_argument("--database", required=True)
    import_event.add_argument("--input", required=True)
    import_event.set_defaults(func=command_earnings_import_event)


def command_earnings_screen(args: argparse.Namespace) -> int:
    result = screen(load_json(Path(args.input), max_bytes=5_000_000))
    print(json.dumps(result, indent=2, allow_nan=False))
    return 2 if any(row["status"] == "INVALID_INPUT" for row in result["results"]) else 0


def command_earnings_template(args: argparse.Namespace) -> int:
    print(json.dumps(template(), indent=2))
    return 0


def command_earnings_fetch(args: argparse.Namespace) -> int:
    api_key, _source = load_api_key()
    with closing(EarningsObservationStore(Path(args.database))) as store:
        observation, cached, remaining = asyncio.run(
            store.fetch_cached(
                AlphaVantageEarningsClient(api_key),
                args.symbol,
                args.dataset,
                cache_age=timedelta(hours=args.cache_hours),
                max_requests_per_24h=args.max_requests_24h,
            )
        )
    summary = {key: value for key, value in observation.items() if key != "payload"}
    print(
        json.dumps(
            {
                **summary,
                "cached": cached,
                "remaining_local_requests_24h": remaining,
            },
            indent=2,
        )
    )
    return 0


def command_earnings_key_set(args: argparse.Namespace) -> int:
    store_api_key(getpass.getpass("Alpha Vantage API key (hidden): "))
    print(json.dumps({"configured": True, "storage": "windows_credential_manager"}, indent=2))
    return 0


def command_earnings_key_status(args: argparse.Namespace) -> int:
    try:
        _value, source = load_api_key()
    except RuntimeError:
        print(json.dumps({"configured": False}, indent=2))
        return 2
    print(json.dumps({"configured": True, "source": source}, indent=2))
    return 0


def command_earnings_key_delete(args: argparse.Namespace) -> int:
    delete_api_key()
    print(json.dumps({"configured_in_credential_manager": False}, indent=2))
    return 0


def command_earnings_record_fact(args: argparse.Namespace) -> int:
    fact = load_json(Path(args.input), max_bytes=64_000)
    with closing(EarningsObservationStore(Path(args.database))) as store:
        digest = store.record_fact(fact)
    print(json.dumps({"recorded": True, "fact_sha256": digest}, indent=2))
    return 0


def command_earnings_normalize_fact(args: argparse.Namespace) -> int:
    with closing(EarningsObservationStore(Path(args.database))) as store:
        digest = store.normalize_fact(
            args.source_sha,
            kind=args.kind,
            fiscal_period=args.period,
            basis=args.basis,
            currency=args.currency,
        )
    print(json.dumps({"recorded": True, "fact_sha256": digest}, indent=2))
    return 0


def command_earnings_verify_event(args: argparse.Namespace) -> int:
    event = load_json(Path(args.input), max_bytes=128_000)
    with closing(EarningsObservationStore(Path(args.database))) as store:
        verified = store.verify_event(event)
    print(json.dumps({"verified": verified, "authority_granted": False}, indent=2))
    return 0 if verified else 2


def command_earnings_import_event(args: argparse.Namespace) -> int:
    event = load_json(Path(args.input), max_bytes=128_000)
    with closing(EarningsObservationStore(Path(args.database))) as store:
        store.record_event(event)
    print(json.dumps({"stored": True, "event_id": event["event_id"]}, indent=2))
    return 0

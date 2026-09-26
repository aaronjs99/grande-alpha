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

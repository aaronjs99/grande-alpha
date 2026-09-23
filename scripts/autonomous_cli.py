"""Explicit foreground entry point for the mixed live engine.

The runner can be authorized while markets are closed. It remains alive and
abstains until the regular session is open; it never installs a scheduler.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import sys
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from pathlib import Path

from grande_alpha.authorization import UserAuthorizationGate, create_authorization
from grande_alpha.earnings import LIMITS
from grande_alpha.equity_execution import EquityScope
from grande_alpha.json_inputs import load_json
from grande_alpha.mixed_engine import mixed_candidate_digest, run_cycles
from grande_alpha.mixed_portfolio import AllocationPolicy
from grande_alpha.models import utc_now


def candidate_template() -> dict:
    scope = {item.name: None for item in fields(EquityScope)}
    scope["allowed_symbols"] = []
    scope["loss_recovery_unit"] = "manual"
    return {
        "schema_version": 1,
        "scope": scope,
        "allocation_policy": asdict(AllocationPolicy()),
        "earnings_thresholds": {key: None for key in sorted(LIMITS)},
    }


def load_candidate(path: Path) -> tuple[EquityScope, AllocationPolicy, dict]:
    value = load_json(path, max_bytes=65_536)
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "scope", "allocation_policy", "earnings_thresholds"
    } or value["schema_version"] != 1:
        raise ValueError("Exact mixed candidate schema version 1 is required")
    raw_scope = value["scope"]
    scope_fields = {item.name for item in fields(EquityScope)}
    if not isinstance(raw_scope, dict) or set(raw_scope) != scope_fields:
        raise ValueError("Every mixed execution scope field must be supplied")
    try:
        starts_at = datetime.fromisoformat(raw_scope["starts_at"])
        expires_at = (datetime.fromisoformat(raw_scope["expires_at"])
                      if raw_scope["expires_at"] is not None else None)
    except (TypeError, ValueError) as exc:
        raise ValueError("Scope start and expiry must be ISO 8601 timestamps") from exc
    symbols = raw_scope["allowed_symbols"]
    if not isinstance(symbols, list):
        raise ValueError("allowed_symbols must be an explicit JSON list")
    scope = EquityScope(**{
        **raw_scope,
        "allowed_symbols": tuple(symbols),
        "starts_at": starts_at,
        "expires_at": expires_at,
    })
    scope.validate()
    raw_policy = value["allocation_policy"]
    policy_fields = {item.name for item in fields(AllocationPolicy)}
    if not isinstance(raw_policy, dict) or set(raw_policy) != policy_fields:
        raise ValueError("Every allocation policy field must be supplied")
    policy = AllocationPolicy(**raw_policy)
    policy.validate()
    thresholds = value["earnings_thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != LIMITS:
        raise ValueError("Every earnings threshold must be supplied")
    # The research parser owns exact threshold numeric semantics.
    from grande_alpha.earnings import _number
    for name, threshold in thresholds.items():
        _number(threshold, name, positive=True)
    return scope, policy, thresholds


def command_candidate_template(_args) -> int:
    print(json.dumps(candidate_template(), indent=2))
    return 0


def _readiness(candidate: Path, authorization: Path, earnings_db: Path) -> dict:
    scope, policy, thresholds = load_candidate(candidate)
    digest = mixed_candidate_digest(scope, policy, thresholds)
    checks: dict[str, object] = {
        "candidate_valid": True,
        "candidate_digest": digest,
        "authorization_can_begin_while_market_closed": True,
        "execution_session": "regular_hours",
        "scheduler_installed": False,
        "broker_contacted": False,
    }
    blockers: list[str] = []
    for label, gate in (("authorization", UserAuthorizationGate(authorization, scope.account_number)),):
        try:
            checks[label] = gate(digest) is True
        except Exception as exc:
            checks[label] = False
            blockers.append(f"{label}: {exc}")
    try:
        # Readiness must not manufacture an empty database that then appears ready.
        with contextlib.closing(sqlite3.connect(earnings_db.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            observations = db.execute("SELECT COUNT(*) FROM earnings_observations").fetchone()[0]
            facts = db.execute("SELECT COUNT(*) FROM earnings_facts").fetchone()[0]
        checks["earnings_observations"] = observations
        checks["earnings_facts"] = facts
        checks["earnings_store_opened"] = True
    except Exception as exc:
        checks["earnings_store_opened"] = False
        blockers.append(f"earnings_store: {exc}")
    checks["research_inputs"] = "built internally from broker quotes and stored observations"
    checks["locally_ready"] = not blockers
    checks["live_ready"] = False
    checks["blockers"] = blockers + [
        "broker permissions and the pinned current provider contract must be verified at connection time"
    ]
    return checks


def command_autonomous_readiness(args) -> int:
    report = _readiness(Path(args.candidate), Path(args.authorization), Path(args.earnings_database))
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


def command_loss_recovery_ack(args) -> int:
    """Clear a manual recovery pause, never the current day's recorded loss."""
    from grande_alpha.storage import AuditStore

    path = Path(args.database)
    if not path.is_file() or not sys.stdin.isatty():
        raise RuntimeError("Manual recovery needs the existing local database and an interactive terminal")
    phrase = f"RESTART {args.account}"
    print("This does not reset today's loss budget or authorize a new strategy.")
    if input(f"Type {phrase!r} to clear the recovery pause: ").strip() != phrase:
        raise RuntimeError("Manual recovery declined")
    store = AuditStore(path)
    try:
        changed = store.acknowledge_loss_recovery(args.account)
    finally:
        store.close()
    print(json.dumps({"recovery_pause_cleared": changed, "daily_loss_reset": False}, indent=2))
    return 0


def command_run_autonomous(args) -> int:
    """Run the account-bound mixed engine in this foreground process."""
    from grande_alpha.broker import RobinhoodMCPBroker
    from grande_alpha.config import data_dir
    from grande_alpha.earnings_feed import EarningsObservationStore
    from grande_alpha.equity_ledger import EquityLedger
    from grande_alpha.live_cli import terminal_line
    from grande_alpha.live_data import LiveDataService
    from grande_alpha.process_lock import ProcessLock
    from grande_alpha.production import build_production_engine
    from grande_alpha.read_retry import read_with_backoff
    from grande_alpha.standing import validate_contract
    from grande_alpha.storage import AuditStore

    if not args.connect:
        raise ValueError("Pass --connect to perform the broker checks required for live operation")
    from grande_alpha.earnings import _number
    _number(args.poll_seconds, "poll_seconds", positive=True)
    scope, policy, thresholds = load_candidate(Path(args.candidate))
    lock = ProcessLock(data_dir() / "app.lock")
    if not lock.acquire(timeout_seconds=0.1):
        raise RuntimeError("Another GRANDE Alpha instance holds the application lock")
    resources = contextlib.ExitStack()
    resources.callback(lock.release)
    engine = None
    try:
        audit = AuditStore()
        resources.callback(audit.close)
        ledger = EquityLedger(data_dir() / "equity_v1.db")
        resources.callback(ledger.close)
        earnings = EarningsObservationStore(Path(args.earnings_database))
        resources.callback(earnings.close)
        broker = RobinhoodMCPBroker(allow_interactive_auth=args.authenticate)
        source_service = LiveDataService(data_dir() / "market_v1.db", broker, earnings,
                                         scope, policy, thresholds)
        resources.callback(source_service.close)
        engine = build_production_engine(
            broker, ledger, audit, scope, policy, thresholds,
            authorization_permit=Path(args.authorization),
            earnings_store=earnings,
        )
        digest = engine.digest()

        async def session() -> None:
            try:
                async with asyncio.timeout(300 if args.authenticate else 30):
                    await broker.connect()
                validate_contract(broker)
                accounts = [item for item in await read_with_backoff(broker.get_accounts)
                            if item.account_number == scope.account_number]
                if len(accounts) != 1 or accounts[0].agentic_allowed is not True:
                    raise RuntimeError("The exact authorized Agentic account was not verified")
                active = audit.active_standing_for_scope(digest)
                if active is None:
                    try:
                        engine.authorization_gate(digest)
                    except (FileNotFoundError, RuntimeError, ValueError):
                        if not sys.stdin.isatty() or not sys.stdout.isatty():
                            raise RuntimeError("Initial live authorization requires an interactive terminal") from None
                        phrase = f"AUTHORIZE {digest}"
                        print("Review the account, strategy and limits. Approval is valid until revoked.")
                        print(json.dumps({"account": accounts[0].masked,
                                          "symbols": list(scope.allowed_symbols),
                                          "starts_at": scope.starts_at.isoformat(),
                                          "expires_at": scope.expires_at.isoformat() if scope.expires_at else None,
                                          "max_order_usd": scope.max_order_usd,
                                          "max_exposure_usd": scope.max_exposure_usd,
                                          "max_daily_notional_usd": scope.max_daily_notional_usd,
                                          "max_daily_loss_usd": scope.max_daily_loss_usd,
                                          "max_orders": scope.max_orders}, indent=2))
                        print(f"Type exactly {phrase!r} within 60 seconds; Escape declines:")
                        response = await terminal_line(expires_at=utc_now() + timedelta(seconds=60))
                        if response != phrase:
                            raise RuntimeError("Autonomous authorization declined; no authority was created") from None
                        create_authorization(Path(args.authorization), scope.account_number, digest)
                    engine.arm()
                else:
                    engine.recover(active)
                    print(f"Recovered the same unchanged authorization {active}; no new grant was created.")

                print("Autonomous runner active. It waits while the supported market session is closed. Ctrl+C revokes new orders.")
                await run_cycles(engine, source_service.snapshot, poll_seconds=args.poll_seconds)
            finally:
                # Local revocation must precede any potentially stalled network cleanup.
                try:
                    if engine.authority_id is not None:
                        engine.stop()
                finally:
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(15):
                            await broker.disconnect()

        try:
            asyncio.run(session())
        except KeyboardInterrupt:
            print("Stop requested; new-order authority was revoked.")
    finally:
        try:
            if engine is not None and engine.authority_id is not None:
                engine.stop()
        finally:
            resources.close()
    return 0

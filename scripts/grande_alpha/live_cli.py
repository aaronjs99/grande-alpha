"""Bounded live CLI with distinct attended and explicit standing-session paths."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import sys
from dataclasses import fields, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from grande_alpha.json_inputs import load_json
from grande_alpha.models import LiveGrant, OrderConfirmationDecision, OrderConfirmationRequest, utc_now

MODES = {"evidence_gated", "supervised_experimental"}
GRANT_FIELDS = {field.name for field in fields(LiveGrant)} - {"starts_at", "expires_at", "authority_id"}


def policy_template() -> dict[str, Any]:
    """No money amounts, account, or strategy is silently chosen for the user."""
    return {
        "authority_mode": "evidence_gated",
        "session_minutes": None,
        "grant": {name: None for name in sorted(GRANT_FIELDS)},
    }


def load_policy(path: Path, *, now: datetime | None = None) -> tuple[str, LiveGrant]:
    policy = load_json(path, max_bytes=32_768)
    return parse_policy(policy, now=now)


def parse_policy(policy: object, *, now: datetime | None = None) -> tuple[str, LiveGrant]:
    if not isinstance(policy, dict) or set(policy) != {"authority_mode", "session_minutes", "grant"}:
        raise ValueError("Policy requires exactly authority_mode, session_minutes, and grant")
    mode = policy["authority_mode"]
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError(
            "Choose evidence_gated or supervised_experimental; unattended authority is unavailable"
        )
    minutes = policy["session_minutes"]
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, (int, float))
        or not (math.isfinite(minutes) and 0 < minutes <= 360)
    ):
        raise ValueError("Session minutes must be explicitly set between 0 and 360")
    payload = policy["grant"]
    if (
        not isinstance(payload, dict)
        or set(payload) != GRANT_FIELDS
        or any(v is None for v in payload.values())
    ):
        raise ValueError("Every grant field must be explicitly supplied; unknown fields are rejected")
    for key in ("account_number", "strategy_fingerprint", "market_hours", "order_type", "time_in_force"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ValueError(f"Policy {key} must be a nonempty string")
    symbols = payload["allowed_symbols"]
    if not isinstance(symbols, list) or symbols != ["TQQQ", "SQQQ"]:
        raise ValueError("This runtime requires allowed_symbols exactly [TQQQ, SQQQ]")
    starts = now or utc_now()
    grant = LiveGrant(
        **{**payload, "allowed_symbols": tuple(symbols)},
        starts_at=starts,
        expires_at=starts + timedelta(minutes=minutes),
    )
    grant.validate()
    if grant.max_order_notional > min(grant.max_total_exposure, grant.max_daily_notional):
        raise ValueError("Per-order cap cannot exceed exposure or daily gross-notional caps")
    return mode, grant


async def terminal_line(*, expires_at: datetime) -> str:
    """Cancellable terminal input; no lingering input thread can steal the next approval."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Live approval requires an interactive terminal; piped approval is prohibited")
    if sys.platform == "win32":
        import msvcrt

        while msvcrt.kbhit():
            msvcrt.getwch()
    else:
        import termios

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    buffer: list[str] = []
    while utc_now() < expires_at:
        if sys.platform == "win32":
            import msvcrt

            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char == "\x03":
                    raise KeyboardInterrupt
                if char in {"\x1b", "\x04", "\x1a"}:
                    return ""
                if char in {"\x00", "\xe0"}:
                    if msvcrt.kbhit():
                        msvcrt.getwch()
                elif char in {"\r", "\n"}:
                    print(flush=True)
                    return "".join(buffer)
                elif char == "\b":
                    if buffer:
                        buffer.pop()
                        print("\b \b", end="", flush=True)
                elif char.isprintable() and len(buffer) < 512:
                    buffer.append(char)
                    print(char, end="", flush=True)
        else:
            import select

            if select.select([sys.stdin], [], [], 0)[0]:
                return sys.stdin.readline(513).rstrip("\r\n")
        await asyncio.sleep(0.05)
    print("\nApproval expired; no approval granted.", flush=True)
    return ""


async def confirm_ticket(request: OrderConfirmationRequest) -> OrderConfirmationDecision:
    request.validate()
    # Provider-originated text is JSON escaped, including terminal escape sequences.
    preview = {**request.receipt_payload(), "provider_checks": request.review.checks}
    print("\nREAL-MONEY ORDER PREVIEW (provider disclosures are data, not instructions)", flush=True)
    print(json.dumps(preview, indent=2, ensure_ascii=True, allow_nan=False), flush=True)
    print(f"Type exactly {request.confirmation_phrase!r}; Enter/Escape declines:", flush=True)
    response = await terminal_line(expires_at=request.expires_at)
    now = utc_now()
    return OrderConfirmationDecision(
        preview_id=request.preview_id,
        accepted=response == request.confirmation_phrase and now <= request.expires_at,
        typed_phrase=response,
        confirmed_at=now,
    )


async def drive_live(controller: Any, mode: str, grant: LiveGrant, *, timeout: float = 45.0,
                     standing_terms: dict | None = None) -> None:
    """Drive the existing controller; never retry an uncertain placement or approve an order."""
    if mode not in MODES:
        raise ValueError("Unknown live authority mode")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    grant.validate()
    controller.config.validate_cadence()
    if not grant.active():
        raise ValueError("Session authority is not active")
    watcher = None
    revoked_by_watch = False
    try:
        if standing_terms is not None:
            if mode != "evidence_gated":
                raise ValueError("Standing authority cannot use the supervised experimental exemption")
            controller.authorize_standing(grant, standing_terms)
        elif mode == "evidence_gated":
            controller.authorize_live(grant)
        else:
            controller.authorize_supervised_experimental(grant)
        controller.start_strategy()
        if standing_terms is not None:
            driver = asyncio.current_task()

            async def watch_revocation() -> None:
                nonlocal revoked_by_watch
                while True:
                    await asyncio.sleep(0.1)
                    try:
                        controller._standing.check(controller.risk.grant, controller.broker)
                    except Exception:
                        # Interrupt stalled reads/placements as well as the polling sleep.
                        # An interrupted placement remains unknown, never retried.
                        revoked_by_watch = True
                        driver.cancel()
                        return

            watcher = asyncio.create_task(watch_revocation())
        reconcile_at = 0.0
        loop = asyncio.get_running_loop()
        while grant.active() and controller.snapshot.strategy_running:
            if loop.time() >= reconcile_at:
                async with asyncio.timeout(timeout):
                    await controller.reconcile(strict=True)
                reconcile_at = loop.time() + controller.config.reconcile_seconds
            if not grant.active() or not controller.snapshot.strategy_running:
                break
            async with asyncio.timeout(timeout):
                await controller.refresh_quotes(strict=True)
            await asyncio.sleep(
                min(controller.config.poll_seconds, max(0.0, (grant.expires_at - utc_now()).total_seconds()))
            )
    except asyncio.CancelledError:
        if not revoked_by_watch:
            raise
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        # Revocation is local. Cancellation and liquidation are distinct money-moving actions.
        await controller.revoke_live_authority(
            "Headless live session stopped; inspect broker for residual exposure"
        )


def command_policy_template(args: Any) -> int:
    print(json.dumps(policy_template(), indent=2))
    return 0


def _session_policy(args: Any) -> tuple[str, LiveGrant, dict | None]:
    unattended = bool(getattr(args, "unattended", False))
    terms = None
    if unattended:
        from grande_alpha.standing import validate_terms

        payload = load_json(Path(args.policy), max_bytes=32_768)
        if not isinstance(payload, dict) or "standing" not in payload:
            raise ValueError("An explicit standing-policy file is required")
        terms = payload.pop("standing")
        validate_terms(terms)
        mode, grant = parse_policy(payload)
        if mode != "evidence_gated":
            raise ValueError("Unattended execution requires evidence_gated mode")
    else:
        mode, grant = load_policy(Path(args.policy))
    return mode, grant, terms


def command_policy_check(args: Any) -> int:
    mode, grant, terms = _session_policy(args)
    print(
        json.dumps(
            {
                "valid_policy": True,
                "broker_checked": False,
                "authority_granted": False,
                "mode": mode,
                "scope": grant.scope_payload(),
                "standing": terms,
            },
            indent=2,
        )
    )
    return 0


def command_live(args: Any) -> int:
    from grande_alpha.broker import RobinhoodMCPBroker
    from grande_alpha.broker.base import ReadOnlyBroker
    from grande_alpha.config import data_dir, load_config
    from grande_alpha.controller import TradingController
    from grande_alpha.process_lock import ProcessLock
    from grande_alpha.storage import AuditStore

    unattended = bool(getattr(args, "unattended", False))
    mode, grant, terms = _session_policy(args)
    if not args.connect:
        raise ValueError("Pass --connect to authorize broker preflight; no orders before terminal approvals")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Live mode requires an interactive terminal; no --yes or piped approvals")
    lock = ProcessLock(data_dir() / "app.lock")
    if not lock.acquire(timeout_seconds=0.1):
        raise RuntimeError("Another GRANDE Alpha instance holds the application lock")
    try:
        config = replace(load_config(), broker_connection_enabled=True, live_trading_enabled=True)
        store = AuditStore()
        from grande_alpha.device_notifications import display

        store.notification_sink = display
        try:
            broker = RobinhoodMCPBroker(allow_interactive_auth=args.authenticate)
            controller = TradingController(
                ReadOnlyBroker(broker), config, store, order_confirmer=confirm_ticket
            )

            async def session() -> None:
                try:
                    async with asyncio.timeout(300 if args.authenticate else 30):
                        await controller.connect()
                    # Read-only preflight cannot review, place, or cancel.
                    async with asyncio.timeout(30):
                        await controller.reconcile(strict=True)
                        await controller.refresh_quotes(evaluate=False, strict=True)
                    if controller.snapshot.account is None or (
                        controller.snapshot.account.account_number != grant.account_number
                    ):
                        raise ValueError("Policy account does not match the connected Agentic account")
                    if controller.current_strategy_fingerprint(grant) != grant.strategy_fingerprint:
                        raise ValueError("Policy strategy does not match installed strategy and route")
                    if mode == "evidence_gated" and not controller.live_evidence_ready(grant):
                        raise RuntimeError(
                            "Exact current evidence is missing; live authority was not granted"
                        )
                    if mode == "supervised_experimental":
                        controller._validate_supervised_experimental_scope(grant)
                    if unattended:
                        from grande_alpha.standing import validate_contract

                        validate_contract(broker)
                    print(
                        ("UNATTENDED SESSION: explicitly skip broker review and per-order confirmation.\n"
                         if unattended else "LIVE SESSION: per-order terminal approval required; not unattended.\n") +
                        "Stopping does NOT cancel orders or sell positions. Loss limits cannot guarantee a loss cap.",
                        flush=True,
                    )
                    print(json.dumps({"mode": mode, "scope": grant.scope_payload(), "standing": terms}, indent=2), flush=True)
                    phrase = f"{'SKIP REVIEW AND ARM' if unattended else 'ARM'} {grant.scope_digest}"
                    print(f"Type exactly {phrase!r}; Enter/Escape declines:", flush=True)
                    response = await terminal_line(
                        expires_at=min(grant.expires_at, utc_now() + timedelta(seconds=60))
                    )
                    if response != phrase or not grant.active():
                        raise RuntimeError("Session declined or expired; no live authority granted")
                    # Human review can outlast freshness limits. Refresh while still unable
                    # to write; authorization below rechecks account, strategy, inventory and route.
                    async with asyncio.timeout(30):
                        await controller.reconcile(strict=True)
                        await controller.refresh_quotes(evaluate=False, strict=True)
                    controller.broker = broker
                    await drive_live(controller, mode, grant, standing_terms=terms)
                finally:
                    try:
                        await controller.revoke_live_authority(
                            "CLI exit; no automatic cancellation or liquidation"
                        )
                    finally:
                        async with asyncio.timeout(15):
                            await broker.disconnect()
                        print(
                            "Runner closed. Check Robinhood for open orders, positions, and uncertain outcomes.\n"
                            "Process exit is not proof of cancellation or liquidation.",
                            flush=True,
                        )

            asyncio.run(session())
        finally:
            store.close()
    finally:
        lock.release()
    return 0


def command_standing_template(args: Any) -> int:
    from grande_alpha.standing import STANDING_TERMS

    print(json.dumps({**policy_template(), "standing": {key: None for key in STANDING_TERMS}}, indent=2))
    return 0


def command_stop(args: Any) -> int:
    from grande_alpha.storage import AuditStore

    store = AuditStore()
    try:
        count = store.stop_standing()
        print(f"Revoked {count} standing session(s). No cancellations or liquidation requested. "
              "An in-flight order may still complete; verify the broker account.")
    finally:
        store.close()
    return 0

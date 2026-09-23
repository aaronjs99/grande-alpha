"""Explicit foreground operation without a desktop window or broker write authority."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import replace
from typing import Any


async def inspect_broker_contract(broker: Any, *, timeout: float = 30.0) -> dict[str, Any]:
    """Only initialize/list tools, then close. Never read accounts or invoke trading tools."""
    try:
        async with asyncio.timeout(timeout):
            await broker.connect()
            return broker.tool_contract_snapshot()
    finally:
        async with asyncio.timeout(timeout):
            await broker.disconnect()


def command_engine_inspect(args: Any) -> int:
    from grande_alpha.broker import RobinhoodMCPBroker

    if not args.connect:
        raise ValueError("Pass --connect to authorize MCP metadata inspection")
    broker = RobinhoodMCPBroker(allow_interactive_auth=args.authenticate)
    snapshot = asyncio.run(inspect_broker_contract(broker, timeout=300 if args.authenticate else 30))
    snapshot["total_tools"] = len(snapshot["tools"])
    if args.tool:
        wanted = set(args.tool)
        unknown = wanted - {tool["name"] for tool in snapshot["tools"]}
        if unknown:
            raise ValueError(f"Unknown MCP tools: {', '.join(sorted(unknown))}")
        snapshot["tools"] = [tool for tool in snapshot["tools"] if tool["name"] in wanted]
    elif not args.full:
        snapshot["tools"] = [{"name": tool["name"]} for tool in snapshot["tools"]]
    print(json.dumps(snapshot, indent=2, allow_nan=False))
    return 0


async def drive_shadow(controller: Any, *, duration: float, timeout: float = 30.0) -> None:
    """Run serial ticks; cancellation always checkpoints and closes the read-only transport.

    Zero duration means until interrupted. Failures stop the process, never blindly restart it.
    Disconnect the transport directly: desktop disconnect may require cancelling existing orders,
    which this process does not own and is structurally unable to do.
    """
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("Duration must be finite and nonnegative")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    controller.config.validate_cadence()
    loop = asyncio.get_running_loop()
    try:
        # MCP transports own task-local cancellation scopes: connect, reads and close
        # must run in this same task, not the child tasks created by wait_for().
        async with asyncio.timeout(timeout):
            await controller.connect()
        controller.start_shadow()
        started = loop.time()
        reconcile_at = started
        while duration == 0 or loop.time() - started < duration:
            if loop.time() >= reconcile_at:
                async with asyncio.timeout(timeout):
                    await controller.reconcile(strict=True)
                reconcile_at = loop.time() + controller.config.reconcile_seconds
            async with asyncio.timeout(timeout):
                await controller.refresh_quotes(strict=True)
            delay = controller.config.poll_seconds
            if duration:
                delay = min(delay, max(0.0, duration - (loop.time() - started)))
            await asyncio.sleep(delay)
    finally:
        try:
            controller.stop_shadow("Headless runner stopped; no real-order authority")
        finally:
            async with asyncio.timeout(timeout):
                await controller.broker.disconnect()


def command_engine_run(args: Any) -> int:
    # Lazy imports keep offline CLI operations independent of a runtime instance.
    from grande_alpha.broker import RobinhoodMCPBroker
    from grande_alpha.broker.base import ReadOnlyBroker
    from grande_alpha.config import data_dir, load_config
    from grande_alpha.controller import TradingController
    from grande_alpha.process_lock import ProcessLock
    from grande_alpha.storage import AuditStore

    if not args.connect:
        raise ValueError("Pass --connect to authorize read-only Robinhood access")
    if not math.isfinite(args.duration) or args.duration < 0:
        raise ValueError("Duration must be finite and nonnegative")
    lock = ProcessLock(data_dir() / "app.lock")
    if not lock.acquire(timeout_seconds=0.1):
        raise RuntimeError("Another GRANDE Alpha instance holds the application lock")
    try:
        config = replace(load_config(), broker_connection_enabled=True, live_trading_enabled=False)
        store = AuditStore()
        try:
            broker = ReadOnlyBroker(RobinhoodMCPBroker(allow_interactive_auth=args.authenticate))
            controller = TradingController(broker, config, store)
            print("HEADLESS SHADOW: virtual trades only; broker writes blocked. Ctrl+C stops this process.",
                  flush=True)
            asyncio.run(drive_shadow(controller, duration=args.duration))
        finally:
            store.close()
    finally:
        lock.release()
    return 0

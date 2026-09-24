"""Read-only broker contract inspection from the command line."""

from __future__ import annotations

import asyncio
import json
from typing import Any


async def inspect_broker_contract(broker: Any, *, timeout: float = 30.0) -> dict[str, Any]:
    """List provider tools without reading accounts or invoking order operations."""
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

"""Read-only broker contract inspection from the command line."""

from __future__ import annotations

import asyncio
import json
from typing import Any


def add_broker_commands(root_commands) -> None:
    broker = root_commands.add_parser("broker", help="Inspect the connected broker contract")
    commands = broker.add_subparsers(dest="broker_command", required=True)
    inspect = commands.add_parser(
        "inspect", help="Inspect MCP tool schemas and descriptions; no orders or account reads"
    )
    inspect.add_argument("--connect", action="store_true")
    inspect.add_argument("--authenticate", action="store_true")
    inspect.add_argument("--tool", action="append", help="Include full metadata for this tool; repeatable")
    inspect.add_argument("--full", action="store_true", help="Include all metadata (potentially very large)")
    inspect.set_defaults(func=command_engine_inspect)


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

"""Validate the broker capabilities used by delegated execution."""

from __future__ import annotations

ROBINHOOD_URL = "https://agent.robinhood.com/mcp/trading"
REQUIRED_TOOL_ARGUMENTS = {
    "get_accounts": frozenset(),
    "get_portfolio": frozenset({"account_number"}),
    "get_equity_quotes": frozenset({"symbols"}),
    "get_equity_tradability": frozenset({"account_number", "symbols"}),
    "get_equity_positions": frozenset({"account_number"}),
    "get_equity_orders": frozenset({"account_number"}),
    "review_equity_order": frozenset({"account_number", "symbol", "side", "type", "market_hours", "time_in_force"}),
    "place_equity_order": frozenset({"account_number", "symbol", "side", "type", "market_hours", "time_in_force", "ref_id"}),
    "cancel_equity_order": frozenset({"account_number", "order_id"}),
}
def validate_contract(broker: object) -> None:
    snapshot = broker.tool_contract_snapshot()
    if snapshot.get("server_url") != ROBINHOOD_URL:
        raise RuntimeError("Delegated trading requires the supported Robinhood MCP endpoint")
    tools = snapshot.get("tools")
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise RuntimeError("Broker tool catalog is unavailable or malformed")
    by_name = {tool.get("name"): tool for tool in tools}
    if len(by_name) != len(tools):
        raise RuntimeError("Broker tool catalog contains duplicate names")
    for name, arguments in REQUIRED_TOOL_ARGUMENTS.items():
        tool = by_name.get(name)
        schema = tool.get("inputSchema") if tool else None
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if (not isinstance(properties, dict) or not arguments <= properties.keys()
                or schema.get("type", "object") != "object"):
            raise RuntimeError(f"Broker capability {name} has a missing or incompatible input schema")
        required = schema.get("required", [])
        possible = set(arguments)
        if name in {"review_equity_order", "place_equity_order"}:
            possible.update({"dollar_amount", "quantity", "limit_price"})
        if name in {"get_equity_orders", "get_equity_positions"}:
            possible.add("cursor")
        if not isinstance(required, list) or not set(required) <= possible:
            raise RuntimeError(f"Broker capability {name} requires unsupported arguments")
        for argument in arguments:
            definition = properties[argument]
            expected_type = "array" if argument == "symbols" else "string"
            if (not isinstance(definition, dict)
                    or definition.get("type", expected_type) != expected_type):
                raise RuntimeError(f"Broker capability {name} changed the {argument} argument type")

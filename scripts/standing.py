"""Broker capability checks for account-bound delegated execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from grande_alpha.models import LiveGrant, OrderIntent, utc_now

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
STANDING_TERMS = {
    "skip_broker_review_and_per_order_confirmation": True,
    "sizing": "exact_candidate_within_grant_limits",
    "allow_strategy_sells_and_loss_exits": True,
    "automatic_cancellation": False,
    "restart": "recover_same_unexpired_unchanged_authorization",
    "stop": "revoke_new_orders_residual_exposure_may_remain",
}


def validate_terms(value: object) -> None:
    # JSON equality alone would accept 1 in place of True.
    if not isinstance(value, dict) or set(value) != set(STANDING_TERMS):
        raise ValueError("Supply every standing authorization term explicitly")
    for key, expected in STANDING_TERMS.items():
        if type(value[key]) is not type(expected) or value[key] != expected:
            raise ValueError(f"Unsupported standing term: {key}")


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


@dataclass(frozen=True)
class StandingPermit:
    account_number: str
    authority_id: str
    strategy_fingerprint: str
    expires_at: datetime
    preview_id: str  # Ticket digest, NOT a human approval or broker review.


class StandingAuthority:
    def __init__(self, grant: LiveGrant, store: object, broker: object) -> None:
        grant.validate()
        if not grant.active():
            raise RuntimeError("Standing authority must be active")
        validate_contract(broker)
        self.grant = grant
        self.store = store
        self.stopped = False
        store.register_standing(grant.authority_id, grant.scope_digest)

    def check(self, grant: LiveGrant | None, broker: object) -> None:
        if (self.stopped or grant is None or grant.scope_digest != self.grant.scope_digest
                or not grant.active() or not self.store.standing_active(grant.authority_id, grant.scope_digest)):
            raise RuntimeError("Standing authority stopped, expired, changed, or missing")
        validate_contract(broker)

    def permit(self, intent: OrderIntent, grant: LiveGrant, broker: object) -> StandingPermit:
        self.check(grant, broker)
        intent.validate()
        # RiskEngine separately checks current sizing, inventory, freshness and budgets.
        if (intent.symbol not in grant.allowed_symbols or intent.order_type != grant.order_type
                or intent.market_hours != grant.market_hours or intent.time_in_force != grant.time_in_force):
            raise RuntimeError("Ticket is outside standing scope")
        digest = hashlib.sha256(json.dumps(intent.as_dict(), sort_keys=True, default=str).encode()).hexdigest()
        return StandingPermit(grant.account_number, grant.authority_id, grant.strategy_fingerprint,
                              min(grant.expires_at, utc_now() + timedelta(seconds=30)), digest)

    def revoke(self) -> None:
        self.stopped = True
        self.store.stop_standing(self.grant.authority_id)

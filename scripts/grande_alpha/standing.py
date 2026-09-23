"""Explicit, session-only delegation; never a fabricated human confirmation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from grande_alpha.models import LiveGrant, OrderIntent, utc_now

# Full tool inventory observed read-only on 2026-09-07. A provider change requires
# a fresh engineering review, not a keyword match or a user-editable allowlist.
ROBINHOOD_CONTRACT_SHA256 = "15f945f6287aecf6d89140c57e0fe0f6a0efff89ef53268a6f6afbcdbae8bc1f"
ROBINHOOD_URL = "https://agent.robinhood.com/mcp/trading"
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
    if (snapshot.get("server_url") != ROBINHOOD_URL
            or snapshot.get("sha256") != ROBINHOOD_CONTRACT_SHA256):
        raise RuntimeError("Provider contract changed or is unqualified; unattended placement is locked")


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

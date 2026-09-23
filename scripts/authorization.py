"""Durable, exact-scope user authorization validation; never creates consent implicitly."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grande_alpha.json_inputs import load_json

AUTHORIZATION_TERMS = {
    "real_money_orders": True,
    "unattended_within_limits": True,
    "strategy_managed_sells": True,
    "broker_cancellation_is_separate": True,
    "loss_limit_cannot_guarantee_realized_loss": True,
    "no_automatic_renewal": True,
}


class UserAuthorizationGate:
    def __init__(self, path: Path, account_number: str, *, now=lambda: datetime.now(UTC)):
        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Authorization gate requires the exact account")
        self.path, self.account_number, self.now = path, account_number, now

    def __call__(self, scope_digest: str) -> bool:
        value = load_json(self.path, max_bytes=32_768)
        required = {"schema_version", "scope_digest", "account_number", "issued_at", "expires_at", "terms"}
        if not isinstance(value, dict) or set(value) != required or value["schema_version"] != 1:
            raise RuntimeError("Authorization permit schema is invalid")
        if value["scope_digest"] != scope_digest or value["account_number"] != self.account_number:
            raise RuntimeError("Authorization permit does not match the exact scope and account")
        terms = value["terms"]
        if (not isinstance(terms, dict) or set(terms) != set(AUTHORIZATION_TERMS)
                or any(type(terms[key]) is not bool or terms[key] != expected
                       for key, expected in AUTHORIZATION_TERMS.items())):
            raise RuntimeError("Authorization terms are incomplete or changed")
        try:
            issued = datetime.fromisoformat(value["issued_at"])
            expires = datetime.fromisoformat(value["expires_at"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Authorization permit times are invalid") from exc
        current = self.now()
        if any(item.tzinfo is None or item.utcoffset() is None for item in (issued, expires, current)):
            raise RuntimeError("Authorization permit times must include offsets")
        if not issued <= current < expires or not timedelta(0) < expires-issued <= timedelta(days=7):
            raise RuntimeError("Authorization permit is expired, future-dated, or longer than seven days")
        json.dumps(value, sort_keys=True, allow_nan=False)
        return True


def authorization_template() -> dict:
    return {"schema_version": 1, "scope_digest": None, "account_number": None,
            "issued_at": None, "expires_at": None, "terms": dict(AUTHORIZATION_TERMS)}


def command_authorization_template(args) -> int:
    print(json.dumps(authorization_template(), indent=2))
    return 0


def command_authorization_check(args) -> int:
    passed = UserAuthorizationGate(Path(args.permit), args.account)(args.scope_digest)
    print(json.dumps({"authorized": passed, "orders_submitted": False}, indent=2))
    return 0

"""Exact-account, exact-strategy authorization kept until explicit revocation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
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
PERSISTENT_TERMS = {**AUTHORIZATION_TERMS, "no_automatic_renewal": False,
                    "until_revoked": True}


def _aware(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"{label} must include an offset")
    return parsed


def _replace_permit(path: Path, value: dict) -> None:
    """Replace one local permit atomically; never leave a half-written grant."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def create_authorization(path: Path, account_number: str, scope_digest: str,
                         *, now: datetime | None = None) -> None:
    """Call only after a separate, exact-scope user approval has completed."""
    issued = now or datetime.now(UTC)
    if issued.tzinfo is None or issued.utcoffset() is None:
        raise ValueError("Authorization time must be aware")
    if not account_number.strip() or not scope_digest.strip():
        raise ValueError("Exact account and scope identity are required")
    if path.exists():
        existing = load_json(path, max_bytes=32_768)
        if existing.get("schema_version") == 2 and existing.get("revoked_at") is None:
            raise RuntimeError("Revoke the current authorization before approving another scope")
        backup = path.with_name(f"{path.name}.{issued.strftime('%Y%m%dT%H%M%SZ')}.bak")
        if backup.exists():
            raise RuntimeError("Authorization backup already exists; choose a different time")
        os.replace(path, backup)
    _replace_permit(path, {"schema_version": 2, "scope_digest": scope_digest,
                           "account_number": account_number, "issued_at": issued.isoformat(),
                           "revoked_at": None, "terms": dict(PERSISTENT_TERMS)})


def revoke_authorization(path: Path, *, now: datetime | None = None) -> None:
    value = load_json(path, max_bytes=32_768)
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise RuntimeError("Only a version 2 authorization can be explicitly revoked here")
    if value.get("revoked_at") is not None:
        return
    issued = _aware(value.get("issued_at"), "Authorization issue time")
    revoked = now or datetime.now(UTC)
    if revoked.tzinfo is None or revoked.utcoffset() is None or revoked < issued:
        raise ValueError("Revocation time must be aware and after approval")
    value["revoked_at"] = revoked.isoformat()
    _replace_permit(path, value)


class UserAuthorizationGate:
    def __init__(self, path: Path, account_number: str, *, now=lambda: datetime.now(UTC)):
        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Authorization gate requires the exact account")
        self.path, self.account_number, self.now = path, account_number, now

    def __call__(self, scope_digest: str) -> bool:
        value = load_json(self.path, max_bytes=32_768)
        if not isinstance(value, dict) or value.get("schema_version") not in {1, 2}:
            raise RuntimeError("Authorization permit schema is invalid")
        version = value["schema_version"]
        required = {"schema_version", "scope_digest", "account_number", "issued_at", "terms"}
        required.add("expires_at" if version == 1 else "revoked_at")
        if set(value) != required:
            raise RuntimeError("Authorization permit fields are invalid")
        if value["scope_digest"] != scope_digest or value["account_number"] != self.account_number:
            raise RuntimeError("Authorization permit does not match the exact scope and account")
        terms = value["terms"]
        expected_terms = AUTHORIZATION_TERMS if version == 1 else PERSISTENT_TERMS
        if (not isinstance(terms, dict) or set(terms) != set(expected_terms)
                or any(type(terms[key]) is not bool or terms[key] != expected
                       for key, expected in expected_terms.items())):
            raise RuntimeError("Authorization terms are incomplete or changed")
        issued = _aware(value["issued_at"], "Authorization issue time")
        current = self.now()
        if current.tzinfo is None or current.utcoffset() is None or issued > current:
            raise RuntimeError("Authorization permit is future-dated or clock is invalid")
        if version == 1:
            expires = _aware(value["expires_at"], "Authorization expiry")
            if not current < expires or not timedelta(0) < expires-issued <= timedelta(days=7):
                raise RuntimeError("Legacy authorization is expired or wider than seven days")
        elif value["revoked_at"] is not None:
            _aware(value["revoked_at"], "Authorization revocation time")
            raise RuntimeError("Authorization has been revoked")
        return True


def authorization_template() -> dict:
    return {"schema_version": 2, "scope_digest": None, "account_number": None,
            "issued_at": None, "revoked_at": None, "terms": dict(PERSISTENT_TERMS)}


def command_authorization_template(args) -> int:
    print(json.dumps(authorization_template(), indent=2))
    return 0


def command_authorization_check(args) -> int:
    passed = UserAuthorizationGate(Path(args.permit), args.account)(args.scope_digest)
    print(json.dumps({"authorized": passed, "orders_submitted": False}, indent=2))
    return 0


def command_authorization_revoke(args) -> int:
    path = Path(args.permit)
    value = load_json(path, max_bytes=32_768)
    if not isinstance(value, dict) or value.get("account_number") != args.account:
        raise RuntimeError("Authorization does not belong to the exact requested account")
    if not sys.stdin.isatty():
        raise RuntimeError("Revocation requires an interactive terminal")
    phrase = f"REVOKE {args.account}"
    if input(f"Type {phrase!r} to revoke new-order authority: ").strip() != phrase:
        raise RuntimeError("Revocation declined")
    revoke_authorization(path)
    print(json.dumps({"revoked": True, "orders_cancelled": False,
                      "positions_closed": False}, indent=2))
    return 0

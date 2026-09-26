from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

ALIASES = {"TQQQ": "TQQQS", "SQQQ": "SQQQS"}
UNDERLYING = {value: key for key, value in ALIASES.items()}
SHADOW_CHECKPOINT_SCHEMA_VERSION = 1


def _aware_datetime(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a nonnegative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a nonnegative integer") from exc
    if result < 0 or result != value:
        raise ValueError(f"{field} must be a nonnegative integer")
    return result


def _tuple_tree(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tuple_tree(item) for item in value)
    return value


def _list_tree(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return [_list_tree(item) for item in value]
    return value


def shadow_checkpoint_digest(checkpoint: dict[str, Any]) -> str:
    """Return the canonical digest for a checkpoint, excluding its digest field."""

    unsigned = {key: value for key, value in checkpoint.items() if key != "digest"}
    try:
        encoded = json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Shadow checkpoint must be canonical JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


def validate_shadow_checkpoint(checkpoint: object) -> dict[str, Any]:
    """Validate structural integrity and return the original checkpoint mapping."""

    if not isinstance(checkpoint, dict):
        raise ValueError("Shadow checkpoint must be an object")
    if checkpoint.get("schema_version") != SHADOW_CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("Unsupported shadow checkpoint schema version")
    for field_name in (
        "run_id",
        "session_key",
        "account_fingerprint",
        "strategy_fingerprint",
        "contract_fingerprint",
        "event",
        "digest",
    ):
        value = checkpoint.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Shadow checkpoint {field_name} must be nonempty")
    for field_name in ("account_fingerprint", "strategy_fingerprint", "contract_fingerprint", "digest"):
        value = str(checkpoint[field_name])
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"Shadow checkpoint {field_name} must be a lowercase SHA-256 digest")
    sequence = _nonnegative_int(checkpoint.get("sequence"), field="checkpoint sequence")
    if sequence < 1:
        raise ValueError("Shadow checkpoint sequence must start at one")
    previous_digest = checkpoint.get("previous_digest")
    if previous_digest is not None and (
        not isinstance(previous_digest, str)
        or len(previous_digest) != 64
        or any(character not in "0123456789abcdef" for character in previous_digest)
    ):
        raise ValueError("Shadow checkpoint previous_digest must be null or a SHA-256 digest")
    if sequence == 1 and previous_digest is not None:
        raise ValueError("First shadow checkpoint cannot name a previous digest")
    if sequence > 1 and previous_digest is None:
        raise ValueError("Later shadow checkpoint must name the previous digest")
    _aware_datetime(checkpoint.get("recorded_at"), field="checkpoint recorded_at")
    state = checkpoint.get("state")
    if not isinstance(state, dict):
        raise ValueError("Shadow checkpoint state must be an object")
    if state.get("run_id") != checkpoint["run_id"]:
        raise ValueError("Shadow checkpoint run identity does not match its state")
    if not isinstance(state.get("active"), bool):
        raise ValueError("Shadow checkpoint active state must be boolean")
    expected_digest = shadow_checkpoint_digest(checkpoint)
    if checkpoint["digest"] != expected_digest:
        raise ValueError("Shadow checkpoint digest mismatch")
    return checkpoint


def shadow_checkpoint_requires_continuity(checkpoint: object) -> bool:
    """Return whether an inactive checkpoint still owns unresolved virtual state.

    A stopped, flat checkpoint with fully settled cash is a completed run. Any
    position, pending transition, or material unsettled cash still belongs to
    that run and must not be replaced by a fresh virtual ledger in the same
    market session.
    """

    validated = validate_shadow_checkpoint(checkpoint)
    state = validated["state"]
    unsettled_cash = _finite(state.get("unsettled_cash"), field="unsettled_cash")
    if unsettled_cash < -1e-6:
        raise ValueError("Shadow checkpoint ledger contains impossible unsettled cash")
    return bool(
        state.get("position") is not None
        or state.get("pending") is not None
        or unsettled_cash > 1e-6
    )


@dataclass(frozen=True)
class ShadowFill:
    timestamp: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    realized_pnl: float | None
    reason: str
    cash_after: float
    unsettled_cash_after: float = 0.0
    commission: float = 0.0
    requested_quantity: float = 0.0
    fill_fraction: float = 1.0
    execution_cost: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["timestamp"] = self.timestamp.isoformat()
        return values


@dataclass
class ShadowPosition:
    symbol: str
    quantity: float
    entry_price: float
    entry_time: datetime
    entry_cost: float


@dataclass
class ShadowState:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    active: bool = True
    starting_cash: float = 0.0
    cash: float = 0.0
    unsettled_cash: float = 0.0
    equity: float = 0.0
    pnl: float = 0.0
    position: ShadowPosition | None = None
    fills: list[ShadowFill] = field(default_factory=list)


@dataclass(frozen=True)
class _PendingTransition:
    target: str | None
    reason: str
    due_analysis_count: int
    session: str

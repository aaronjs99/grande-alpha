"""Validate the reviewed mixed portfolio profile shared by desktop and CLI."""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from grande_alpha.configuration.json_inputs import load_json
from grande_alpha.data.earnings import LIMITS, _number
from grande_alpha.execution.equity_execution import EquityScope
from grande_alpha.strategy.mixed_portfolio import AllocationPolicy


def candidate_template() -> dict:
    scope = {item.name: None for item in fields(EquityScope)}
    scope["allowed_symbols"] = []
    scope["loss_recovery_unit"] = "manual"
    return {
        "schema_version": 1,
        "scope": scope,
        "allocation_policy": asdict(AllocationPolicy()),
        "earnings_thresholds": {key: None for key in sorted(LIMITS)},
    }


def load_candidate(path: Path) -> tuple[EquityScope, AllocationPolicy, dict]:
    value = load_json(path, max_bytes=65_536)
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "scope", "allocation_policy", "earnings_thresholds"
    } or value["schema_version"] != 1:
        raise ValueError("Exact mixed candidate schema version 1 is required")
    raw_scope = value["scope"]
    scope_fields = {item.name for item in fields(EquityScope)}
    if not isinstance(raw_scope, dict) or set(raw_scope) != scope_fields:
        raise ValueError("Every mixed execution scope field must be supplied")
    try:
        starts_at = datetime.fromisoformat(raw_scope["starts_at"])
        expires_at = (datetime.fromisoformat(raw_scope["expires_at"])
                      if raw_scope["expires_at"] is not None else None)
    except (TypeError, ValueError) as exc:
        raise ValueError("Scope start and expiry must be ISO 8601 timestamps") from exc
    symbols = raw_scope["allowed_symbols"]
    if not isinstance(symbols, list):
        raise ValueError("allowed_symbols must be an explicit JSON list")
    scope = EquityScope(**{
        **raw_scope,
        "allowed_symbols": tuple(symbols),
        "starts_at": starts_at,
        "expires_at": expires_at,
    })
    scope.validate()
    raw_policy = value["allocation_policy"]
    policy_fields = {item.name for item in fields(AllocationPolicy)}
    if not isinstance(raw_policy, dict) or set(raw_policy) != policy_fields:
        raise ValueError("Every allocation policy field must be supplied")
    policy = AllocationPolicy(**raw_policy)
    policy.validate()
    thresholds = value["earnings_thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != LIMITS:
        raise ValueError("Every earnings threshold must be supplied")
    for name, threshold in thresholds.items():
        _number(threshold, name, positive=True)
    return scope, policy, thresholds

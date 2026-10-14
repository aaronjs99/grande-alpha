"""Validate the reviewed mixed portfolio profile shared by desktop and CLI."""

from __future__ import annotations

import json
import os
import tempfile
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
    return validate_candidate(load_json(path, max_bytes=65_536))


def check_candidate_shape(value: dict) -> None:
    """Drafts use the same exact schema, with unfilled fields left as null."""
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "scope", "allocation_policy", "earnings_thresholds"
    } or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Exact mixed candidate schema version 1 is required")
    scope_fields = {item.name for item in fields(EquityScope)}
    if not isinstance(value["scope"], dict) or set(value["scope"]) != scope_fields:
        raise ValueError("Every mixed execution scope field must be supplied")
    policy_fields = {item.name for item in fields(AllocationPolicy)}
    if not isinstance(value["allocation_policy"], dict) or set(value["allocation_policy"]) != policy_fields:
        raise ValueError("Every allocation policy field must be supplied")
    if not isinstance(value["earnings_thresholds"], dict) or set(value["earnings_thresholds"]) != LIMITS:
        raise ValueError("Every earnings threshold must be supplied")


def validate_candidate(value: dict) -> tuple[EquityScope, AllocationPolicy, dict]:
    check_candidate_shape(value)
    raw_scope = value["scope"]
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
    policy = AllocationPolicy(**raw_policy)
    policy.validate()
    thresholds = value["earnings_thresholds"]
    for name, threshold in thresholds.items():
        _number(threshold, name, positive=True)
    return scope, policy, thresholds


def save_candidate_document(target: Path, document: dict, *, source_path: Path | None = None,
                            overwrite: bool = False) -> Path:
    """Atomically save a draft or candidate without replacing its loaded source."""
    check_candidate_shape(document)
    target = target.resolve()
    if source_path is not None and target == source_path.resolve():
        raise ValueError("Choose a new file; the loaded source is preserved")
    if target.exists() and (not overwrite or not target.is_file()):
        raise ValueError("Confirm replacement of the existing destination or choose a new file")
    encoded = (json.dumps(document, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > 65_536:
        raise ValueError("Candidate exceeds 64 KiB")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".candidate-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
        if overwrite:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target

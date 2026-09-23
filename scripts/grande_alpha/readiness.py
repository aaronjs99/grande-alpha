"""Workflow-specific readiness reporting, never execution authority."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from grande_alpha.json_inputs import load_json


def recorded_preferences(path: Path | None) -> dict[str, Any]:
    """Read only two planning amounts; ignore any purported permissions in the file."""
    raw = load_json(path, max_bytes=32_768) if path is not None else {}
    if not isinstance(raw, dict):
        raise ValueError("Setup preferences must be a JSON object")
    amounts = {}
    for name in ("trading_capital_usd", "daily_loss_threshold_usd"):
        value = raw.get(name)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"Setup {name} must be null or a finite positive number")
        amounts[name] = value
    return {**amounts, "runtime_enforced": False, "authority_granted": False}


def missing_requirements(
    *,
    workflow: str,
    evidence_present: bool,
    parity: dict[str, Any],
    preferences: dict[str, Any],
    policy_issues: list[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if workflow not in {"current", "earnings"}:
        raise ValueError("Unknown readiness workflow")
    missing = []
    if policy_issues:
        missing.append(
            {"key": "session_policy", "owner": "user_and_engineering", "item": "; ".join(policy_issues)}
        )
    if preferences["trading_capital_usd"] is None:
        missing.append(
            {
                "key": "capital_allocation",
                "owner": "user",
                "item": "Choose trading capital separately from operating expenses",
            }
        )
    if not evidence_present:
        missing.append(
            {
                "key": "exact_strategy_evidence",
                "owner": "engineering_and_research",
                "item": "Qualify the exact current strategy; no matching local evidence is present",
            }
        )
    for blocker in parity["blockers"]:
        missing.append(
            {
                "key": blocker["key"],
                "owner": "provider_and_engineering"
                if blocker["key"] == "provider_order_confirmation_contract"
                else "engineering",
                "item": blocker["requirement"],
            }
        )
    missing.append(
        {
            "key": "operational_recovery",
            "owner": "user_and_engineering",
            "item": "Verify an always-on execution host, alerts and restart/outage recovery; no particular cloud vendor is required",
        }
    )
    earnings = [
        {
            "key": "earnings_data",
            "owner": "user_and_engineering",
            "item": "Point-in-time earnings/consensus/price data with appropriate usage rights",
        },
        {
            "key": "earnings_execution",
            "owner": "engineering",
            "item": "PEAD out-of-sample evaluation, costs, individual-stock execution and risk qualification",
        },
    ]
    optional = [
        {
            "key": "ai_advisor",
            "owner": "user_and_engineering",
            "item": "Optional non-authoritative AI advisor; deterministic strategies do not require an AI subscription",
        }
    ]
    if workflow == "earnings":
        missing.extend(earnings)
    else:
        optional.extend(earnings)
    return missing, optional

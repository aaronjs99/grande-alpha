from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActivationGuidance:
    owner: str
    destination: str
    next_action: str
    explanation: str


_DEFAULT = ActivationGuidance(
    owner="APP CHECK",
    destination="inspect",
    next_action="Select this row and inspect the recorded result before continuing.",
    explanation="This is an independent, fail-closed activation condition.",
)


ACTIVATION_GUIDANCE: dict[str, ActivationGuidance] = {
    "Scheduled auto-shadow": ActivationGuidance(
        owner="APP GATE",
        destination="inspect",
        next_action=(
            "Use scheduled auto-shadow only for observations and virtual fills. It cannot become live; "
            "launch normal GRANDE Alpha for attended supervised review after its shared checks, or for "
            "autonomous review only after every evidence and runtime gate passes."
        ),
        explanation="The scheduled process is wrapped in a broker facade that blocks every order method.",
    ),
    "Broker capability": ActivationGuidance(
        owner="YOU",
        destination="settings",
        next_action=(
            "Open Settings & Permissions, check Connect Robinhood broker data, then Save. "
            "This enables account reads only; it does not authorize an order."
        ),
        explanation="Connecting broker data is an optional capability that requires your consent.",
    ),
    "Real-order capability": ActivationGuidance(
        owner="YOU",
        destination="settings",
        next_action=(
            "Open Settings & Permissions only if you deliberately want attended supervised tickets. "
            "Enabling the capability grants no session authority: the hard-capped session is reviewed "
            "separately and every broker preview requires a fresh typed confirmation. Autonomous use "
            "still requires exact evidence and runtime parity."
        ),
        explanation=(
            "This user-controlled capability exposes only the supervised per-order path; it cannot "
            "override autonomous evidence or create standing authority."
        ),
    ),
    "Exact Agentic account": ActivationGuidance(
        owner="APP + YOU",
        destination="connect",
        next_action=(
            "Click Connect Robinhood and complete browser consent. GRANDE Alpha will accept exactly one "
            "active Agentic account; it will not select the regular investing account for app orders."
        ),
        explanation="You complete provider consent; the app validates and selects the exact account.",
    ),
    "Fresh account truth": ActivationGuidance(
        owner="APP CHECK",
        destination="refresh",
        next_action=(
            "After connecting, click Run safe checks. The app refreshes balances, positions, and orders "
            "without reviewing, placing, or cancelling an order."
        ),
        explanation="The app can automate this read-only preflight once broker data is connected.",
    ),
    "Flat leveraged inventory": ActivationGuidance(
        owner="YOU",
        destination="manual_review",
        next_action=(
            "Review TQQQ/SQQQ inventory in Robinhood. If exposure exists, decide whether to keep it or use "
            "the separately reviewed Flatten Position flow; then refresh and verify flat."
        ),
        explanation="Selling real inventory is a financial decision and is never part of safe auto-checks.",
    ),
    "No working Agentic orders": ActivationGuidance(
        owner="APP + YOU",
        destination="manual_review",
        next_action=(
            "Use STOP + CANCEL, then verify every affected order is terminal in Robinhood. A cancellation "
            "request alone is not proof that an order cannot fill."
        ),
        explanation="The app can request cancellation only after your action; broker truth remains authoritative.",
    ),
    "No ambiguous placements": ActivationGuidance(
        owner="YOU",
        destination="manual_review",
        next_action=(
            "Match each quarantined client reference to Robinhood order history. Do not retry an unknown "
            "outcome or create a replacement order until reconciliation is authoritative."
        ),
        explanation="An unknown acknowledgement may already represent a live order.",
    ),
    "Fresh exact venue quotes": ActivationGuidance(
        owner="APP CHECK",
        destination="refresh",
        next_action=(
            "During the selected market session, click Run safe checks and wait for one complete, fresh, "
            "low-skew QQQ/TQQQ/SQQQ provider batch."
        ),
        explanation="The app can repeat this read-only quote check; it cannot manufacture freshness.",
    ),
    "Supported real-order route": ActivationGuidance(
        owner="YOU",
        destination="settings",
        next_action=(
            "Open Settings and click Apply bounded pilot settings to preview Regular market, Market order, "
            "GFD, and cash T+1. Review and explicitly Save. Evidence-gated autonomy must also keep Research "
            "Sandbox Extra latency at 0 bars and rerun Evidence Lab because route or latency changes alter "
            "the exact candidate fingerprint."
        ),
        explanation=(
            "Both supervised tickets and evidence-gated autonomy use the bounded regular-hours/GFD route."
        ),
    ),
    "Immutable runtime contract": ActivationGuidance(
        owner="RESEARCH",
        destination="evidence",
        next_action=(
            "Open Research Sandbox, load the saved candidate that matches current Settings, and rerun the "
            "complete Evidence Lab without editing the final result afterward."
        ),
        explanation="Research and runtime sizing, cadence, settlement, route, and strategy must be identical.",
    ),
    "Runtime execution parity": ActivationGuidance(
        owner="APP GATE",
        destination="evidence",
        next_action=(
            "Complete and test exact replay/runtime sizing and execution parity. Do not flip a constant or "
            "edit a receipt; the resulting candidate must be reevaluated from unchanged source data."
        ),
        explanation="A backtest cannot authorize a different live execution path.",
    ),
    "Positive exact evidence": ActivationGuidance(
        owner="RESEARCH",
        destination="evidence",
        next_action=(
            "Open Research Sandbox, import lawful observed QQQ/TQQQ/SQQQ history for the exact interval, run "
            "Evidence Lab, and resolve every failed gate with new evidence—not lower thresholds."
        ),
        explanation="A synthetic or partially passing result cannot authorize real-money automation.",
    ),
    "Live broker preflight": ActivationGuidance(
        owner="APP + YOU",
        destination="connect",
        next_action=(
            "Run Morning Check, then open normal GRANDE Alpha, connect with browser consent, and use Run "
            "safe checks during regular hours. Verify the Agentic account and Robinhood views yourself."
        ),
        explanation="The app automates read-only validation; you verify provider consent and broker truth.",
    ),
    "Bounded same-day authority": ActivationGuidance(
        owner="YOU",
        destination="manual_review",
        next_action=(
            "In normal GRANDE Alpha, review the exact account, symbols, route, expiry, and dollar limits. "
            "A supervised session additionally requires fresh confirmation for each reviewed order; the "
            "autonomous path requires every evidence and parity condition first."
        ),
        explanation="Authority is never stored, scheduled, or inferred from a previous session.",
    ),
}


def activation_guidance(gate_name: str) -> ActivationGuidance:
    return ACTIVATION_GUIDANCE.get(str(gate_name), _DEFAULT)


def decorate_readiness(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    decorated: list[dict[str, str]] = []
    for row in rows:
        gate = str(row.get("gate", "Unknown condition"))
        guidance = activation_guidance(gate)
        status = str(row.get("status", "BLOCKED"))
        passed = status == "PASS"
        decorated.append(
            {
                "gate": gate,
                "owner": guidance.owner,
                "status": status,
                "observed": str(row.get("observed", "Not checked")),
                "action": "Keep verified; no action now." if passed else guidance.next_action,
                "destination": guidance.destination,
                "explanation": guidance.explanation,
            }
        )
    return decorated


def activation_summary(rows: Iterable[Mapping[str, Any]], *, shadow_only: bool = False) -> str:
    values = list(rows)
    passed = sum(str(row.get("status", "")) == "PASS" for row in values)
    blocked = len(values) - passed
    mode = (
        "This scheduled auto-shadow process is structurally read-only and has no live-order path. "
        if shadow_only
        else "Scheduled auto-shadow is structurally read-only and cannot become a live session. "
    )
    return (
        f"{passed}/{len(values)} platform conditions currently pass; {blocked} are blocked. {mode}"
        "Normal GRANDE Alpha may separately offer an attended, hard-capped supervised session when its "
        "account, route, and capability checks pass; every order still requires fresh confirmation. "
        "Runtime parity and evidence govern only evidence-gated autonomous eligibility. Neither path "
        "guarantees profit or grants standing authority."
    )

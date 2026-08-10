from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

GATE_GUIDANCE: dict[str, tuple[str, str]] = {
    "Historical source": (
        "Synthetic scenarios are useful for testing software behavior, but they are not observations of a market.",
        "Import a lawful QQQ/TQQQ/SQQQ history file or deliberately enable community market data. A deterministic scenario can never pass this gate.",
    ),
    "Trading-session coverage": (
        "The evidence must cover every hour in which the selected route is allowed to trade.",
        "Choose a route matching the dataset. Extended and 24-hour routes require corresponding complete-session data; 24-hour evidence requires an appropriate imported CSV.",
    ),
    "Data breadth": (
        "A few sessions cannot represent enough market regimes or support the statistical tests below.",
        "Use at least 120 complete market sessions. More independent sessions are preferable to denser samples from only a few days.",
    ),
    "Data recency": (
        "Old data may not represent current liquidity, volatility, spread, or market structure.",
        "Refresh or extend the dataset so its final observation is no more than 30 days old.",
    ),
    "Data integrity": (
        "Missing, duplicated, misaligned, or incomplete bars can invent returns and execution opportunities.",
        "Repair the source rather than interpolating performance: remove duplicates, align all three symbols, and provide at least 95% complete sessions with no missing expected intervals.",
    ),
    "Parameter stability": (
        "A result that works at only one precise setting is likely tuned to noise.",
        "Inspect neighboring configurations and seek a broad profitable region. Do not promote a single lucky parameter point.",
    ),
    "Cost stress": (
        "A small apparent edge can disappear when spreads, slippage, or fees are worse than expected.",
        "Use defensible execution costs, reduce unnecessary turnover, and require positive results even at three times those costs. Do not pass this by entering unrealistically low costs.",
    ),
    "Closed-trade sample": (
        "Too few completed trades makes win rate, profit factor, and expectancy unstable.",
        "Collect more eligible history or wait for more genuine signals until there are at least 30 after-cost round trips.",
    ),
    "After-cost quality": (
        "Gross gains are irrelevant if modeled execution costs consume the edge.",
        "The unchanged hypothesis must reach profit factor 1.20 with positive after-cost expectancy on eligible data.",
    ),
    "Random-entry control": (
        "A strategy should outperform chance entries under comparable holding and sizing assumptions.",
        "Improve the causal signal or reject the hypothesis; it must rank at or above the 75th percentile of the seeded random-entry control.",
    ),
    "Trial-adjusted significance": (
        "Trying many candidates increases the chance that one looks profitable by accident.",
        "Use more independent profitable days or a stronger pre-specified effect. The complete registered trial count remains part of the correction and must not be erased.",
    ),
    "Deflated Sharpe": (
        "Ordinary Sharpe can be overstated by non-normal returns and selection from many trials.",
        "Require more stable daily returns and a stronger result relative to every registered candidate; the DSR probability must reach 95%.",
    ),
    "Profit concentration": (
        "If one day creates most gains, the result may depend on a single event rather than a repeatable process.",
        "Gather additional independent profitable sessions until no day contributes more than half of total positive daily P/L.",
    ),
    "Drawdown": (
        "A strategy that breaches its research loss envelope is not compatible with the tested risk plan.",
        "Reduce defensible exposure or improve exits and rerun unchanged out-of-sample evaluation; maximum drawdown must remain at or below 5%.",
    ),
    "Ending flat": (
        "An unclosed position makes final equity and realized performance incomplete.",
        "Enable force-flat handling or extend the replay through a valid modeled exit so it ends in cash.",
    ),
    "Exact candidate identity": (
        "A certificate cannot cover settings that the chronological training folds would not actually select.",
        "Treat this as instability: every fold must independently select the exact configuration being reviewed.",
    ),
    "Walk-forward": (
        "Chronological train/test folds estimate whether selection survives later unseen periods.",
        "Provide enough sessions to run at least five purged folds, then require 60% positive folds, 20 test trades, median profit factor 1.10, and positive expectancy.",
    ),
}


def _value(gate: object, name: str, default: Any = "") -> Any:
    if isinstance(gate, Mapping):
        return gate.get(name, default)
    return getattr(gate, name, default)


def gate_detail(gate: object) -> str:
    name = str(_value(gate, "name", "Unknown gate"))
    passed = bool(_value(gate, "passed", False))
    observed = str(_value(gate, "observed", "Unknown"))
    requirement = str(_value(gate, "requirement", "Unknown"))
    why, next_step = GATE_GUIDANCE.get(
        name,
        (
            "This is an independent evidence-policy condition.",
            "Compare the observed value with the requirement and rerun without changing or hiding prior trials.",
        ),
    )
    return (
        f"{name} — {'PASS' if passed else 'FAIL'}\n\n"
        f"Observed: {observed}\n"
        f"Required: {requirement}\n\n"
        f"Why it matters: {why}\n\n"
        f"{'Keep verified' if passed else 'Next step'}: {next_step}"
    )


def promotion_overview(gates: Iterable[object]) -> str:
    values = list(gates)
    passed = sum(bool(_value(gate, "passed", False)) for gate in values)
    failed = [str(_value(gate, "name", "Unknown")) for gate in values if not _value(gate, "passed", False)]
    prefix = (
        f"{passed}/{len(values)} independent gates passed. This is not a progress score: every gate must pass "
        "before GRANDE Alpha can create a live-review certificate."
    )
    if not failed:
        return prefix + " A certificate still grants no standing order authority."
    preview = ", ".join(failed[:5])
    if len(failed) > 5:
        preview += f", and {len(failed) - 5} more"
    synthetic = any(
        str(_value(gate, "name", "")) == "Historical source"
        and "deterministic" in str(_value(gate, "observed", "")).lower()
        for gate in values
    )
    source_note = (
        " This run is synthetic, so it can test the app but can never qualify as market evidence."
        if synthetic
        else ""
    )
    return f"{prefix}{source_note} Current blockers: {preview}. Select a row for the exact reason and next step."

"""Experimental price-led paper strategy. No broker or model calls."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from statistics import pstdev

from grande_alpha.research.agent_models import AgentDecision, AgentSettings

ADAPTIVE_POLICY = "adaptive-trend-v1"
HISTORY_SECONDS = 600
STOP_BPS = 100.0
TRAIL_BPS = 75.0
TARGET_BPS = 200.0
MAX_HOLD_SECONDS = 1800
COOLDOWN_SECONDS = 60
MAX_DRAWDOWN_PCT = 3.0


def _ema(history, seconds):
    value = history[0][1]
    for (before, _), (at, mid) in zip(history, history[1:], strict=False):
        weight = 1 - math.exp(-(at - before).total_seconds() / seconds)
        value += weight * (mid - value)
    return value


def adaptive_decision(item: AgentDecision, history, state: dict, now: datetime) -> AgentDecision:
    """Use only already-validated quotes; exits do not wait for entry warm-up."""
    if item.quote is None or item.risk_status not in {"Data checks passed", "Warming up"}:
        return item
    quote, key = item.quote, item.instrument.key
    history = list(history)
    slippage = float(state["slippage_bps"]) / 10_000
    fee = float(state.get("costs", {}).get(f"{item.instrument.asset_class.value}_fee_bps", 0)) / 10_000
    cost_bps = (quote.ask * (1 + slippage) * (1 + fee) / (quote.bid * (1 - slippage) * (1 - fee)) - 1) * 10_000
    fast = _ema(history, 30) if history else quote.mid
    slow = _ema(history, 120) if history else quote.mid
    returns = [(b[1] / a[1] - 1) * 10_000 for a, b in zip(history, history[1:], strict=False)]
    noise = pstdev(returns) * math.sqrt(len(returns)) if len(returns) >= 2 else 0.0
    movement = (quote.mid / history[0][1] - 1) * 10_000 if history else 0.0
    threshold = max(cost_bps * 1.5, noise * 2)
    metrics = {"policy": ADAPTIVE_POLICY, "round_trip_cost_bps": cost_bps,
               "entry_threshold_bps": threshold, "movement_bps": movement,
               "noise_bps": noise, "fast_ema": fast, "slow_ema": slow}

    def result(action, reason):
        return replace(item, action=action, reason=reason, buy_allowed=action == "buy",
                       risk_status="Data checks passed" if action == "exit" or key in state["positions"] else item.risk_status,
                       strategy_context=metrics)

    holding = state["positions"].get(key)
    if holding:
        entry = float(holding["cost"]) / float(holding["quantity"])
        net_bps = (quote.bid * (1 - slippage) * (1 - fee) / entry - 1) * 10_000
        peak = max(float(holding.get("peak_bid", holding["bid"])), quote.bid)
        retracement = (1 - quote.bid / peak) * 10_000
        opened = datetime.fromisoformat(holding.get("opened_at", holding["marked_at"]))
        age = (now - opened).total_seconds()
        metrics.update(net_exit_bps=net_bps, retracement_bps=retracement, holding_seconds=age)
        if net_bps <= -STOP_BPS:
            return result("exit", f"Paper stop: net exit {net_bps / 100:+.2f}% reached -{STOP_BPS / 100:.2f}%")
        if net_bps >= TARGET_BPS:
            return result("exit", f"Paper profit target: net exit {net_bps / 100:+.2f}% reached {TARGET_BPS / 100:.2f}%")
        if retracement >= TRAIL_BPS:
            return result("exit", f"Paper trailing exit: price fell {retracement / 100:.2f}% from its observed high")
        if age >= MAX_HOLD_SECONDS:
            return result("exit", "Paper time exit: holding reached 30 minutes")
        if item.risk_status == "Data checks passed" and fast < slow and quote.mid < fast:
            return result("exit", "Paper trend exit: fast trend crossed below the slow trend")
        return result("hold", f"Managing virtual holding · net exit {net_bps / 100:+.2f}% · "
                      f"stop -{STOP_BPS / 100:.2f}% / target +{TARGET_BPS / 100:.2f}%")
    if item.risk_status == "Warming up":
        return result("hold", item.reason)
    if cost_bps >= STOP_BPS:
        return result("hold", f"Round-trip cost {cost_bps / 100:.3f}% consumes the 1% paper stop budget")
    for fill in reversed(state["fills"]):
        if fill["key"] == key and fill["side"] == "sell":
            remaining = COOLDOWN_SECONDS - (now - datetime.fromisoformat(fill["filled_at"])).total_seconds()
            if remaining > 0:
                return result("hold", f"Paper re-entry cooldown: {math.ceil(remaining)}s remaining")
            break
    occupied = set(state["positions"]) | set(state["pending"])
    if key in state["pending"] and state["pending"][key]["side"] == "sell":
        return result("hold", "Waiting for the pending virtual exit")
    # Require a breakout of the preceding 30 seconds, not just any positive tick.
    recent = [mid for at, mid in history[:-1] if (history[-1][0] - at).total_seconds() <= 30]
    breakout = bool(recent and quote.mid > max(recent))
    metrics["breakout"] = breakout
    if not recent:
        gap = (history[-1][0] - history[-2][0]).total_seconds() if len(history) >= 2 else None
        return result("hold", "No preceding quote inside the 30s breakout window" +
                      (f" · last observation gap {gap:.1f}s" if gap is not None else "") +
                      "; check quote cadence and provider delays")
    if movement > threshold and fast > slow and quote.mid > fast and breakout:
        return result("buy", f"Paper trend breakout · move {movement / 100:+.3f}% > "
                      f"cost/noise threshold {threshold / 100:.3f}% · round-trip estimate {cost_bps / 100:.3f}%")
    return result("hold", f"Watching trend · move {movement / 100:+.3f}% / "
                  f"required >{threshold / 100:.3f}% · breakout {'yes' if breakout else 'no'}" +
                  (" · virtual entry awaiting confirmation" if key in occupied else ""))


def limit_entries(
    decisions: list[AgentDecision], state: dict, settings: AgentSettings
) -> list[AgentDecision]:
    """Reserve capacity within a batch as well as across both market workers."""
    occupied = set(state["positions"]) | {k for k, v in state["pending"].items() if v["side"] == "buy"}
    costs = {k: float(p["cost"]) for k, p in state["positions"].items()}
    trade_cash = float(state["trade_cash"])
    costs.update({k: trade_cash for k in occupied if k not in costs})
    family = {"equity:QQQ", "equity:TQQQ", "equity:SQQQ"}
    result = []
    max_positions = settings.paper_max_positions
    max_exposure = settings.paper_max_exposure_pct / 100
    for item in decisions:
        key, reason = item.instrument.key, ""
        if item.action == "buy" and item.buy_allowed:
            others = occupied - {key}
            if float(state["max_drawdown_pct"]) >= MAX_DRAWDOWN_PCT:
                reason = "Paper session drawdown reached 3%; new entries paused until a new session"
            elif len(others) >= max_positions:
                reason = f"Paper position limit: {max_positions} held or pending entries"
            elif key in family and others & family:
                reason = "Paper exposure limit: only one of QQQ, TQQQ or SQQQ at a time"
            elif sum(costs[k] for k in others) + trade_cash > float(state["initial_cash"]) * max_exposure:
                reason = f"Paper exposure limit: entries use at most {max_exposure:.0%} of starting virtual cash"
            else:
                occupied.add(key)
                costs[key] = trade_cash
            if reason:
                item = replace(item, action="hold", buy_allowed=False, reason=reason)
        result.append(item)
    return result


def pause_entries(decisions, paused):
    """Also revoke eligibility on HOLD so an earlier pending buy cannot fill."""
    if not paused:
        return decisions
    return [replace(item, action="hold" if item.action == "buy" else item.action, buy_allowed=False,
                    reason="New paper buys paused by your directions" if item.action == "buy" else item.reason)
            for item in decisions]

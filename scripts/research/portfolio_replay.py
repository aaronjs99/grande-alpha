"""Causal, long-only multi-asset paper replay. No broker imports or authority."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import timedelta
from pathlib import Path

from grande_alpha.configuration.json_inputs import load_json
from grande_alpha.data.earnings import _number, _time
from grande_alpha.domain.market_calendar import is_regular_trading_day, regular_session_times
from grande_alpha.domain.policy import EASTERN
from grande_alpha.strategy.mixed_portfolio import plan


def _nav(cash, unsettled, holdings, quotes):
    return cash + sum(row[1] for row in unsettled) + sum(
        row["quantity"] * (quotes[s][0]/2 + quotes[s][1]/2) for s, row in holdings.items())


def replay(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != {
        "initial_cash", "daily_loss_usd", "slippage_bps", "fee_bps", "max_quote_age_seconds",
        "max_execution_delay_seconds", "frames",
    }:
        raise ValueError("Exact portfolio replay schema required")
    cash = _number(payload["initial_cash"], "initial_cash", positive=True)
    initial = cash
    loss_limit = _number(payload["daily_loss_usd"], "daily_loss_usd", positive=True)
    max_age = _number(payload["max_quote_age_seconds"], "max_quote_age_seconds", positive=True)
    max_delay = _number(payload["max_execution_delay_seconds"], "max_execution_delay_seconds", positive=True)
    slip = _number(payload["slippage_bps"], "slippage_bps") / 10000
    fee = _number(payload["fee_bps"], "fee_bps") / 10000
    if not 0 <= slip < 1 or not 0 <= fee < 1:
        raise ValueError("Costs must be nonnegative and less than 10000 bps")
    frames = payload["frames"]
    if not isinstance(frames, list) or not 2 <= len(frames) <= 10000:
        raise ValueError("Replay requires 2 to 10000 chronological frames")
    holdings, unsettled, fills, curve = {}, [], [], []
    pending = None
    previous_time = None
    previous_nav = initial
    active_day = None
    day_peak = initial
    loss_latched = False
    last_policy = None
    skipped = []
    for frame_index, frame in enumerate(frames):
        if not isinstance(frame, dict) or set(frame) != {"request", "quotes", "theses", "corporate_actions"}:
            raise ValueError("Each frame requires request, quotes, theses and corporate_actions")
        if frame["corporate_actions"] != []:
            raise ValueError("Corporate actions require a qualified adjustment model; replay rejected")
        request = copy.deepcopy(frame["request"])
        now = _time(request["earnings"]["as_of"], "as_of")
        if previous_time is not None and now <= previous_time:
            raise ValueError("Frames must be strictly chronological")
        # Freeze candidate policy; selecting tomorrow's best parameters is not replay.
        candidate_policy = {"allocation": request["policy"], "earnings": request["earnings"]["thresholds"]}
        if last_policy is not None and candidate_policy != last_policy:
            raise ValueError("Allocation policy must remain fixed throughout replay")
        last_policy = copy.deepcopy(candidate_policy)
        if request["holdings"]:
            raise ValueError("Replay starts in cash and constructs holdings internally; input holdings must be empty")
        local = now.astimezone(EASTERN)
        session = regular_session_times(local.date())
        if session is None or not session[0] <= local.time().replace(tzinfo=None) < session[1]:
            raise ValueError("Replay frames must be inside scheduled regular trading hours")
        raw_quotes = frame["quotes"]
        if not isinstance(raw_quotes, dict) or not raw_quotes or len(raw_quotes) > 10000:
            raise ValueError("A bounded symbol quote map is required")
        quotes = {}
        for symbol, quote in raw_quotes.items():
            if not isinstance(quote, dict) or set(quote) != {"bid", "ask", "observed_at", "available_at"}:
                raise ValueError("Exact quote fields required")
            bid = _number(quote["bid"], "bid", positive=True)
            ask = _number(quote["ask"], "ask", positive=True)
            observed = _time(quote["observed_at"], "observed_at")
            available = _time(quote["available_at"], "available_at")
            if bid > ask or not observed <= available <= now or (now-observed).total_seconds() > max_age:
                raise ValueError("Crossed, stale or future replay quote")
            quotes[symbol] = (bid, ask, observed)
        for event in request["earnings"]["events"]:
            symbol = event["symbol"]
            if symbol not in quotes or quotes[symbol] != (
                event["bid"], event["ask"], _time(event["quote_at"], "earnings quote_at")
            ):
                raise ValueError("Earnings quotes must match the replay quote tape")
        needed = set(holdings) | (set(pending["targets_usd"]) if pending else set())
        if not needed <= set(quotes):
            raise ValueError("Missing quote for held or pending asset; cannot invent a mark or fill")
        if not isinstance(frame["theses"], dict) or any(type(v) is not bool for v in frame["theses"].values()):
            raise ValueError("Explicit boolean thesis map required")
        if not needed <= set(frame["theses"]):
            raise ValueError("Each held or pending asset requires current thesis status")
        ready = [row for row in unsettled if row[0] <= local.date()]
        cash += sum(row[1] for row in ready)
        unsettled = [row for row in unsettled if row[0] > local.date()]

        before = _nav(cash, unsettled, holdings, quotes)
        if active_day != local.date():
            active_day = local.date()
            # Carry previous observed NAV so a gap cannot reset away the loss.
            day_peak = max(previous_nav, before)
            loss_latched = False
        day_peak = max(day_peak, before)
        loss_latched = loss_latched or day_peak-before >= loss_limit
        if pending:
            decision_time = _time(pending["as_of"], "decision time")
            due = (now-decision_time).total_seconds() <= max_delay
            causal = all(quotes[s][2] > decision_time for s in needed)
            if not due or not causal:
                skipped.append({"frame": frame_index, "reason": "EXPIRED_OR_NONCAUSAL_TARGETS"})
            else:
                review = {row["symbol"]: row["review_rebalance"] for row in pending["rebalance_reviews"]}
                # Sell first, but proceeds enter unsettled cash, not buying power.
                for side in ("sell", "buy"):
                    for symbol in sorted(needed):
                        target = pending["targets_usd"].get(symbol, 0)
                        row = holdings.get(symbol)
                        quantity = row["quantity"] if row else 0
                        bid, ask, _ = quotes[symbol]
                        mid = bid/2 + ask/2
                        delta = target-quantity*mid
                        if not review.get(symbol, target == 0):
                            continue
                        if side == "buy" and (delta <= 0 or loss_latched or not frame["theses"][symbol]):
                            continue
                        if side == "sell" and delta >= 0:
                            continue
                        price = ask*(1+slip) if side == "buy" else bid*(1-slip)
                        amount = min(delta, cash/(1+fee)) if side == "buy" else -delta
                        shares = math.floor(max(0, amount/price)*1e6)/1e6
                        if side == "sell":
                            shares = min(quantity, shares)
                            if target == 0:
                                shares = quantity
                        if shares <= 0:
                            continue
                        notional = shares*price
                        fees = notional*fee
                        if side == "buy":
                            cash -= notional+fees
                            holdings[symbol] = {"quantity": quantity+shares,
                                                "opened_at": row["opened_at"] if row else now.isoformat()}
                        else:
                            settle = local.date()+timedelta(days=1)
                            while not is_regular_trading_day(settle):
                                settle += timedelta(days=1)
                            unsettled.append((settle, notional-fees))
                            if quantity-shares <= 1e-10:
                                holdings.pop(symbol)
                            else:
                                holdings[symbol]["quantity"] = quantity-shares
                        fills.append({"at": now.isoformat(), "decision_at": pending["as_of"], "symbol": symbol,
                                      "side": side, "quantity": shares, "price": price, "fee": fees})
                        value = _nav(cash, unsettled, holdings, quotes)
                        day_peak = max(day_peak, value)
                        loss_latched = loss_latched or day_peak-value >= loss_limit
        value = _nav(cash, unsettled, holdings, quotes)
        if not math.isfinite(value) or value <= 0 or cash < -1e-7:
            raise ValueError("Invalid or exhausted virtual portfolio")
        request["capital_usd"] = value
        request["holdings"] = [{"symbol": s, "market_value": row["quantity"]*(quotes[s][0]/2+quotes[s][1]/2),
                                "opened_at": row["opened_at"], "thesis_valid": frame["theses"][s]}
                               for s, row in holdings.items()]
        pending = plan(request)
        curve.append({"at": now.isoformat(), "nav": value, "settled_cash": cash,
                      "unsettled_cash": sum(row[1] for row in unsettled), "loss_latched": loss_latched,
                      "holdings": copy.deepcopy(holdings)})
        previous_nav, previous_time = value, now
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"status": "PAPER_REPLAY_ONLY", "authority_granted": False, "live_eligible": False,
            "input_sha256": hashlib.sha256(canonical.encode()).hexdigest(), "fills": fills,
            "curve": curve, "skipped": skipped, "final_nav": previous_nav,
            "cash_benchmark_nav": initial, "net_change": previous_nav-initial,
            "unexecuted_final_targets": pending, "residual_holdings": holdings,
            "limitations": ["Full virtual fills, no queue/liquidity model", "No dividends, splits or taxes",
                            "Calendar omits unscheduled closures", "User-supplied data is unverified",
                            "No forced terminal liquidation; residual holdings are marked, not sold"]}


def command_replay(args) -> int:
    result = replay(load_json(Path(args.input), max_bytes=32_000_000))
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0

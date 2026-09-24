"""A single costed $100 paper experiment. No real account or order access."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

from grande_alpha.agent_models import AgentSettings, AssetClass
from grande_alpha.agent_paper import PaperLedger, money
from grande_alpha.market_calendar import regular_session_times

EASTERN = ZoneInfo("America/New_York")
POLICY = "budget-paper-v1"


def fee_rate(state, key):
    return money(state["costs"][key.split(":")[0] + "_fee_bps"]) / 10000


def settlement_time(at):
    day = at.astimezone(EASTERN).date() + timedelta(days=1)
    while not (hours := regular_session_times(day)):
        day += timedelta(days=1)
    return datetime.combine(day, hours[0], EASTERN).astimezone(UTC).isoformat()


class ExperimentLedger(PaperLedger):
    """One experiment per database, resumed explicitly; no reset or budget-renewal route."""

    def initialize(self, *, equity_fee_bps=1, crypto_fee_bps=25, slippage_bps=5):
        if self.state:
            if self.state.get("experiment_policy") != POLICY:
                raise ValueError("This database is not a background paper experiment")
            return
        rates = {"equity_fee_bps": money(equity_fee_bps), "crypto_fee_bps": money(crypto_fee_bps)}
        slip = money(slippage_bps)
        if any(not 0 <= n <= 1000 for n in (*rates.values(), slip)):
            raise ValueError("Fee/slippage assumptions must be between 0 and 1,000 basis points")
        # Initialize in one save so a crash cannot leave a partly configured experiment.
        seed = PaperLedger()
        try:
            seed.start("broker_quotes", 100, 10)
            state = deepcopy(seed.state)
        finally:
            seed.close()
        settings = AgentSettings(equity_symbols=("SPY", "QQQ"), crypto_symbols=("BTC-USD", "ETH-USD"),
                                 paper_strategy="adaptive", news_enabled=True, paper_max_positions=2,
                                 paper_max_exposure_pct=20)
        state.update(experiment_policy=POLICY, costs={k: str(v) for k, v in rates.items()},
                     slippage_bps=str(slip), fees_paid="0", operating_expenses="0", expenses=[],
                     unsettled=[], unavailable_marks=[], loss_limit="10", loss_locked=False, paused=False,
                     settings=asdict(settings), strategy={"policy": "adaptive-trend-v1", "experiment": POLICY,
                     "ai_role": "off", "news_role": "context and headline-risk checks"})
        self._save(state)

    @staticmethod
    def mark_value(state, key, holding):
        return (money(holding["quantity"]) * money(holding["bid"])
                * (1 - money(state["slippage_bps"]) / 10000) * (1 - fee_rate(state, key)))

    @staticmethod
    def unsettled_value(state):
        return sum((money(v["amount"]) for v in state.get("unsettled", [])), Decimal(0))

    @classmethod
    def check_loss(cls, state):
        if money(state["initial_cash"]) - cls.equity_value(state) >= money(state["loss_limit"]):
            state["loss_locked"] = True
        if state["loss_locked"] or state["paused"]:
            state["pending"] = {k: v for k, v in state["pending"].items() if v["side"] == "sell"}

    def _save(self, state):
        if state.get("experiment_policy") == POLICY:
            self.check_loss(state)
            equity = self.equity_value(state)
            peak = max(money(state["equity_peak"]), equity)
            state["equity_peak"] = str(peak)
            state["max_drawdown_pct"] = str(max(money(state["max_drawdown_pct"]), 100 * (peak - equity) / peak))
        super()._save(state)

    def set_paused(self, paused):
        if type(paused) is not bool:
            raise ValueError("Pause must be true or false")
        state = deepcopy(self.state)
        state["paused"] = paused
        self._save(state)

    def add_expense(self, amount, note, receipt_id):
        amount = money(amount)
        if not 0 < amount <= 10000 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Enter an expense from $0.01 to $10,000 in whole cents")
        if not isinstance(note, str) or not note.strip() or len(note) > 160:
            raise ValueError("Give the expense a short description")
        if not isinstance(receipt_id, str) or not 1 <= len(receipt_id) <= 80:
            raise ValueError("Expense needs a receipt ID")
        state = deepcopy(self.state)
        for entry in state["expenses"]:
            if entry["id"] == receipt_id:
                if money(entry["amount"]) != amount or entry["note"] != note.strip():
                    raise ValueError("Receipt ID already belongs to a different expense")
                return  # Retrying the same receipt must not double-charge it.
        state["cash"] = str(money(state["cash"]) - amount)
        state["operating_expenses"] = str(money(state["operating_expenses"]) + amount)
        state["expenses"].append({"id": receipt_id, "amount": str(amount), "note": note.strip(),
                                  "at": datetime.now(UTC).isoformat()})
        state["equity_history"].append({"at": datetime.now(UTC).isoformat(), "equity": str(self.equity_value(state))})
        state["equity_history"] = state["equity_history"][-500:]
        self._save(state)

    def consume(self, decisions, now, max_age):
        state = deepcopy(self.state)
        released = [v for v in state["unsettled"] if datetime.fromisoformat(v["available_at"]) <= now]
        state["cash"] = str(money(state["cash"]) + sum((money(v["amount"]) for v in released), Decimal(0)))
        state["unsettled"] = [v for v in state["unsettled"] if v not in released]
        # Mark all valid books in the batch before assessing entry capacity or loss.
        unavailable = set(state.get("unavailable_marks", []))
        for item in decisions:
            key, quote = item.instrument.key, item.quote
            if key not in state["positions"]:
                continue
            unavailable.add(key)
            if quote is None or item.risk_status not in {"Data checks passed", "Warming up"}:
                continue
            try:
                quote.validate()
                stamp = self._timestamp(item)
                latest = quote.latest_book_timestamp or quote.timestamp
                if (quote.symbol != item.instrument.symbol or (now - stamp).total_seconds() > max_age
                        or (latest - now).total_seconds() > 2
                        or stamp < datetime.fromisoformat(state["positions"][key]["marked_at"])):
                    continue
                state["positions"][key].update(bid=str(quote.bid), marked_at=stamp.isoformat())
                unavailable.discard(key)
            except (ValueError, TypeError, OverflowError):
                continue
        state["unavailable_marks"] = sorted(unavailable & state["positions"].keys())
        self._save(state)
        self._decision_now, self._max_age = now, max_age
        stale = bool(state["unavailable_marks"]) or any((now - datetime.fromisoformat(p["marked_at"])).total_seconds() > max_age
                    for p in self.state["positions"].values())
        if self.state["loss_locked"]:
            decisions = [replace(d, action="exit" if d.instrument.key in self.state["positions"] else "hold",
                                 buy_allowed=False, reason="$10 experiment loss budget reached",
                                 risk_status="Data checks passed" if d.risk_status == "Warming up" else d.risk_status)
                         for d in decisions]
        elif self.state["paused"] or stale:
            decisions = [replace(d, action="hold" if d.action == "buy" else d.action, buy_allowed=False) for d in decisions]
            self.cancel_pending_buys()
        super().consume(decisions, now, max_age)

    def _fill(self, state, item, side, at, source_ids):
        self.check_loss(state)
        key, quote = item.instrument.key, item.quote
        rate, slip = fee_rate(state, key), money(state["slippage_bps"]) / 10000
        positions = state["positions"]
        pnl = Decimal(0)
        if side == "buy":
            stale = bool(set(state.get("unavailable_marks", [])) & positions.keys()) or any((self._decision_now - datetime.fromisoformat(p["marked_at"])).total_seconds() > self._max_age
                        for p in positions.values())
            if state["loss_locked"] or state["paused"] or stale or key in positions:
                return
            allocation = money(state["trade_cash"])
            if money(state["cash"]) < allocation:
                return
            price = money(quote.ask) * (1 + slip)
            quantity = (allocation / (price * (1 + rate))).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
            if quantity <= 0:
                return
            notional = quantity * price
            fee = notional * rate
            state["cash"] = str(money(state["cash"]) - notional - fee)
            positions[key] = {"quantity": str(quantity), "cost": str(notional + fee), "entry_fee": str(fee),
                              "bid": str(quote.bid), "marked_at": at.isoformat(), "opened_at": at.isoformat(),
                              "peak_bid": str(quote.bid), "entry_sources": list(source_ids)}
        else:
            holding = positions.pop(key, None)
            if holding is None:
                return
            source_ids = holding.get("entry_sources", [])
            quantity = money(holding["quantity"])
            price = money(quote.bid) * (1 - slip)
            notional = quantity * price
            fee = notional * rate
            proceeds = notional - fee
            pnl = proceeds - money(holding["cost"])
            if item.instrument.asset_class == AssetClass.EQUITY:
                state["unsettled"].append({"amount": str(proceeds), "available_at": settlement_time(at)})
            else:
                state["cash"] = str(money(state["cash"]) + proceeds)
            state["realized_pnl"] = str(money(state["realized_pnl"]) + pnl)
            state["wins" if pnl > 0 else "losses" if pnl < 0 else "breakeven"] += 1
            metric = "gross_profit" if pnl > 0 else "gross_loss"
            state[metric] = str(money(state[metric]) + abs(pnl))
        state["fees_paid"] = str(money(state["fees_paid"]) + fee)
        state["fill_count"] += 1
        state["fills"].append({"number": state["fill_count"], "key": key, "side": side,
                               "quantity": str(quantity), "price": str(price), "fee": str(fee),
                               "realized_pnl": str(pnl), "filled_at": at.isoformat(), "source_ids": list(source_ids)})
        state["fills"] = state["fills"][-200:]
        self.check_loss(state)

    def summary(self, **kwargs):
        result = super().summary(**kwargs)
        if result is None:
            return None
        state = self.state
        for position in result["positions"]:
            if position["key"] in state.get("unavailable_marks", []):
                position["stale"] = True
        expenses = money(state["operating_expenses"])
        net = money(result["total_pnl"])
        result.update(net_profit=str(net), trading_profit=str(net + expenses), fees_paid=state["fees_paid"],
                      operating_expenses=str(expenses), loss_limit=state["loss_limit"],
                      loss_remaining="0" if state["loss_locked"] else str(max(Decimal(0), money(state["loss_limit"]) + min(Decimal(0), net))),
                      loss_locked=state["loss_locked"], paused=state["paused"], costs=deepcopy(state["costs"]),
                      unsettled_cash=str(self.unsettled_value(state)), expenses=deepcopy(state["expenses"][-30:]),
                      model="Paper only. Bid/ask + adverse slippage + modeled fees. Equity proceeds settle next scheduled session. "
                      "Crypto proceeds available immediately. No depth/queue/partial-fill model. "
                      "Fees are stress assumptions, not a verified broker fee schedule. "
                      "Operating costs include only expenses you record; electricity is not measured automatically.")
        return result

"""Virtual portfolios only: no broker, account, authority, or order callbacks.

Signals fill on the next distinct eligible quote, at ask/bid plus adverse
slippage. Fractional quantities, immediate settlement, no fees or liquidity
model: this is a software simulation, not an execution forecast.
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from grande_alpha.agent_models import AgentDecision, AssetClass, Instrument
from grande_alpha.models import Quote

DEMO_CYCLES = 24
DEMO_EPOCH = datetime(2026, 9, 23, 15, tzinfo=UTC)


def money(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Use a finite positive virtual dollar amount")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("Use a finite virtual dollar amount") from exc
    if not result.is_finite():
        raise ValueError("Use a finite virtual dollar amount")
    return result


def validate_paper_settings(source: str, initial_cash: object, trade_cash: object) -> tuple[Decimal, Decimal]:
    if source not in {"demo", "broker_quotes"}:
        raise ValueError("Choose demo or broker_quotes for paper trading")
    capital, allocation = money(initial_cash), money(trade_cash)
    if not Decimal("1") <= capital <= Decimal("1000000") or not Decimal("1") <= allocation <= capital:
        raise ValueError("Virtual cash must be $1–$1,000,000; each buy must be $1 up to virtual cash")
    return capital, allocation


def demo_time(cycle: int) -> datetime:
    return DEMO_EPOCH + timedelta(seconds=30 * cycle)


def demo_market(market: AssetClass, cycle: int) -> tuple[list[Instrument], dict[str, Quote]]:
    """A fixed up/down path exercises the real research rules without any I/O."""
    symbol = "DEMO-STOCK" if market == AssetClass.EQUITY else "DEMO-USD"
    step = cycle if cycle <= 10 else 20 - cycle
    price = (100 if market == AssetClass.EQUITY else 200) * (1 + step * 0.003)
    quote = Quote(symbol, price, price * 1.0002, price, demo_time(cycle))
    return [Instrument(market, symbol, source="synthetic_demo")], {symbol: quote}


class PaperLedger:
    """Separate durable sessions; reopening never starts or resumes simulation."""

    def __init__(self, path: Path | None = None) -> None:
        self._db = sqlite3.connect(str(path) if path else ":memory:", timeout=0.1)
        self._db.execute("CREATE TABLE IF NOT EXISTS paper_sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS paper_fills (session_id TEXT, number INTEGER, payload TEXT NOT NULL, PRIMARY KEY(session_id, number))")
        row = self._db.execute("SELECT payload FROM paper_sessions ORDER BY rowid DESC LIMIT 1").fetchone()
        self.state = json.loads(row[0]) if row else None

    def close(self) -> None:
        self._db.close()

    def _save(self, state: dict) -> None:
        payload = json.dumps(state, allow_nan=False)
        with self._db:
            self._db.execute("INSERT INTO paper_sessions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                             (state["session_id"], payload))
            old_count = self.state["fill_count"] if self.state and self.state["session_id"] == state["session_id"] else 0
            for fill in state["fills"]:
                if fill["number"] > old_count:
                    self._db.execute("INSERT INTO paper_fills VALUES (?,?,?)",
                                     (state["session_id"], fill["number"], json.dumps(fill, allow_nan=False)))
        self.state = state

    def start(self, source: str, initial_cash: object, trade_cash: object) -> None:
        capital, allocation = validate_paper_settings(source, initial_cash, trade_cash)
        self._save({
            "session_id": str(uuid.uuid4()), "source": source,
            "initial_cash": str(capital), "cash": str(capital), "trade_cash": str(allocation),
            "slippage_bps": "5", "realized_pnl": "0", "positions": {}, "pending": {},
            "last_quotes": {}, "fills": [], "fill_count": 0, "observed_at": None,
            "created_at": datetime.now(UTC).isoformat(),
        })

    def cancel_pending(self) -> None:
        if self.state and self.state["pending"]:
            state = deepcopy(self.state)
            state["pending"] = {}
            # Stopping takes effect in memory even if the journal is unavailable.
            self.state = state
            self._save(state)

    @staticmethod
    def _timestamp(item: AgentDecision) -> datetime:
        quote = item.quote
        if item.instrument.asset_class == AssetClass.CRYPTO:
            return min(t for t in (quote.timestamp, quote.bid_timestamp, quote.ask_timestamp) if t is not None)
        return quote.book_timestamp or quote.timestamp

    def consume(self, decisions: list[AgentDecision], now: datetime, max_age: float) -> None:
        if not self.state:
            return
        state = deepcopy(self.state)
        positions, pending = state["positions"], state["pending"]
        for item in decisions:
            key, quote = item.instrument.key, item.quote
            # Blocked decisions cannot create orders, fills, or fresh valuations.
            if quote is None or item.risk_status not in {"Data checks passed", "Warming up"}:
                pending.pop(key, None)
                continue
            try:
                quote.validate()
                timestamp = self._timestamp(item)
                newest = max(t for t in (quote.timestamp, quote.bid_timestamp, quote.ask_timestamp) if t is not None)
                if (quote.symbol != item.instrument.symbol or not all(math.isfinite(n) for n in (quote.bid, quote.ask))
                        or (now - timestamp).total_seconds() > max_age or (newest - now).total_seconds() > 2):
                    raise ValueError("Invalid paper quote")
            except (ValueError, TypeError, OverflowError):
                pending.pop(key, None)
                continue
            previous = state["last_quotes"].get(key)
            if previous and timestamp <= datetime.fromisoformat(previous):
                continue
            state["last_quotes"][key] = timestamp.isoformat()
            if key in positions:
                positions[key].update(bid=str(quote.bid), marked_at=timestamp.isoformat())
            intent = pending.pop(key, None)
            if intent and item.risk_status == "Data checks passed":
                signal_at = datetime.fromisoformat(intent["signal_at"])
                if signal_at < timestamp and (now - signal_at).total_seconds() <= 600:
                    self._fill(state, item, intent["side"], timestamp)
            # One holding per symbol, no repeated accumulation or short positions.
            if item.risk_status == "Data checks passed":
                side = "buy" if item.action == "buy" and key not in positions else (
                    "sell" if item.action == "exit" and key in positions else None)
                if side and (side == "sell" or money(state["cash"]) >= money(state["trade_cash"])):
                    pending[key] = {"side": side, "signal_at": timestamp.isoformat()}
        state["observed_at"] = now.isoformat()
        self._save(state)

    @staticmethod
    def _fill(state: dict, item: AgentDecision, side: str, at: datetime) -> None:
        key, quote = item.instrument.key, item.quote
        positions = state["positions"]
        cash = money(state["cash"])
        slippage = money(state["slippage_bps"]) / 10000
        pnl = Decimal(0)
        if side == "buy":
            allocation = money(state["trade_cash"])
            if key in positions or cash < allocation:
                return
            price = money(quote.ask) * (1 + slippage)
            quantity = (allocation / price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
            if quantity <= 0:
                return
            cost = quantity * price
            cash -= cost
            positions[key] = {"quantity": str(quantity), "cost": str(cost),
                              "bid": str(quote.bid), "marked_at": at.isoformat()}
        else:
            holding = positions.pop(key, None)
            if holding is None:
                return
            quantity = money(holding["quantity"])
            price = money(quote.bid) * (1 - slippage)
            proceeds = quantity * price
            pnl = proceeds - money(holding["cost"])
            cash += proceeds
            state["realized_pnl"] = str(money(state["realized_pnl"]) + pnl)
        state["cash"] = str(cash)
        state["fill_count"] += 1
        state["fills"].append({"number": state["fill_count"], "key": key, "side": side,
                               "quantity": str(quantity), "price": str(price),
                               "realized_pnl": str(pnl), "filled_at": at.isoformat()})
        state["fills"] = state["fills"][-200:]

    def summary(self, *, active: bool = False, now: datetime | None = None, max_age: float = 15) -> dict | None:
        if not self.state:
            return None
        state = self.state
        positions, value, cost = [], Decimal(0), Decimal(0)
        for key, holding in state["positions"].items():
            market_value = money(holding["quantity"]) * money(holding["bid"])
            basis = money(holding["cost"])
            age = (now - datetime.fromisoformat(holding["marked_at"])).total_seconds() if now else None
            positions.append({"key": key, **holding, "value": str(market_value),
                              "unrealized_pnl": str(market_value - basis),
                              "stale": age is None or age > max_age or age < -2})
            value += market_value
            cost += basis
        equity = money(state["cash"]) + value
        return {
            "session_id": state["session_id"], "source": state["source"], "active": active,
            "initial_cash": state["initial_cash"], "cash": state["cash"], "equity": str(equity),
            "trade_cash": state["trade_cash"], "slippage_bps": state["slippage_bps"],
            "realized_pnl": state["realized_pnl"], "unrealized_pnl": str(value - cost),
            "total_pnl": str(equity - money(state["initial_cash"])),
            "positions": positions, "pending_count": len(state["pending"]) if active else 0,
            "fill_count": state["fill_count"], "fills": deepcopy(state["fills"]),
            "observed_at": state["observed_at"],
            "model": "Virtual only; next eligible quote; fractional units; 5 bps adverse slippage; no fees; immediate settlement; no liquidity model",
        }

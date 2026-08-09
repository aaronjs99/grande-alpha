from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from grande_alpha.models import Quote, Signal
from grande_alpha.policy import DecisionPolicy, PolicyConfig, PolicyPosition
from grande_alpha.sandbox import SandboxConfig

ALIASES = {"TQQQ": "TQQQS", "SQQQ": "SQQQS"}
UNDERLYING = {value: key for key, value in ALIASES.items()}


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
    equity: float = 0.0
    pnl: float = 0.0
    position: ShadowPosition | None = None
    fills: list[ShadowFill] = field(default_factory=list)


class LiveShadowEngine:
    """Live-quote virtual executor. This module has no broker dependency by design."""

    def __init__(self, config: SandboxConfig) -> None:
        config.validate()
        self.config = config
        self.policy = DecisionPolicy(
            PolicyConfig(
                bullish_symbol="TQQQS",
                bearish_symbol="SQQQS",
                hard_stop_pct=config.hard_stop_pct,
                take_profit_pct=config.take_profit_pct,
                max_hold_minutes=config.max_hold_minutes,
                no_trade_open_minutes=config.no_trade_open_minutes,
                no_trade_close_minutes=config.no_trade_close_minutes,
            )
        )
        self.state = ShadowState(
            starting_cash=config.initial_cash,
            cash=config.initial_cash,
            equity=config.initial_cash,
        )
        self._pending: tuple[str | None, str] | None = None

    def on_bar(self, timestamp: datetime, signal: Signal, quotes: dict[str, Quote]) -> list[ShadowFill]:
        if not self.state.active:
            return []
        fills: list[ShadowFill] = []
        window_allowed = self.policy.trading_window_allowed(timestamp)
        if self._pending is not None and window_allowed:
            target, reason = self._pending
            complete, fill = self._transition(timestamp, target, reason, quotes)
            if fill:
                fills.append(fill)
                self.state.fills.append(fill)
            if complete:
                self._pending = None

        position = self.state.position
        marked = None
        if position:
            quote = quotes.get(UNDERLYING[position.symbol])
            marked = quote.mid if quote else None
        policy_position = (
            PolicyPosition(
                position.symbol,
                position.entry_price,
                marked,
                max(0.0, (timestamp - position.entry_time).total_seconds() / 60.0),
            )
            if position
            else None
        )
        decision = self.policy.decide(signal, timestamp, policy_position)
        current = position.symbol if position else None
        if window_allowed and decision.target_symbol != current and self._pending is None:
            self._pending = (decision.target_symbol, decision.reason)
        self._mark(quotes)
        return fills

    def stop(self, quotes: dict[str, Quote] | None = None) -> ShadowState:
        self.state.active = False
        self._pending = None
        if quotes:
            self._mark(quotes)
        return self.state

    def _transition(
        self,
        timestamp: datetime,
        target: str | None,
        reason: str,
        quotes: dict[str, Quote],
    ) -> tuple[bool, ShadowFill | None]:
        position = self.state.position
        if position:
            if target == position.symbol:
                return True, None
            quote = quotes.get(UNDERLYING[position.symbol])
            if not quote:
                return False, None
            price = self._price(quote, "sell")
            proceeds = position.quantity * price - self.config.commission_per_order
            realized = proceeds - position.entry_cost
            self.state.cash += proceeds
            self.state.position = None
            fill = ShadowFill(
                timestamp, position.symbol, "sell", position.quantity, price, realized,
                reason or "Shadow exit", self.state.cash,
            )
            return target is None, fill
        if target is None:
            return True, None
        quote = quotes.get(UNDERLYING[target])
        if not quote:
            return False, None
        price = self._price(quote, "buy")
        budget = min(
            self.config.order_notional,
            self.state.cash * self.config.max_exposure_pct,
            max(0.0, self.state.cash - self.config.commission_per_order),
        )
        quantity = budget / price if price > 0 else 0.0
        if quantity <= 0:
            return True, None
        cost = quantity * price + self.config.commission_per_order
        self.state.cash -= cost
        self.state.position = ShadowPosition(target, quantity, price, timestamp, cost)
        return True, ShadowFill(
            timestamp, target, "buy", quantity, price, None, reason or "Shadow entry", self.state.cash
        )

    def _price(self, quote: Quote, side: str) -> float:
        modeled_bps = self.config.slippage_bps + self.config.base_spread_bps / 2.0
        direction = 1.0 if side == "buy" else -1.0
        return quote.mid * (1.0 + direction * modeled_bps / 10_000.0)

    def _mark(self, quotes: dict[str, Quote]) -> None:
        value = self.state.cash
        if self.state.position:
            quote = quotes.get(UNDERLYING[self.state.position.symbol])
            if quote:
                value += self.state.position.quantity * quote.mid
        self.state.equity = value
        self.state.pnl = value - self.state.starting_cash

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from grande_alpha.models import Quote, Signal
from grande_alpha.policy import DecisionPolicy, PolicyConfig, PolicyPosition, session_key
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
    unsettled_cash_after: float = 0.0

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
    unsettled_cash: float = 0.0
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
                market_hours=config.market_hours,
            )
        )
        self.state = ShadowState(
            starting_cash=config.initial_cash,
            cash=config.initial_cash,
            equity=config.initial_cash,
        )
        self._pending: tuple[str | None, str] | None = None
        self._pending_session: str | None = None
        self._current_session: str | None = None
        self._analysis_count = 0

    def on_bar(self, timestamp: datetime, signal: Signal, quotes: dict[str, Quote]) -> list[ShadowFill]:
        """Advance a synthetic bar trace whose decision fills on the next call."""

        return self._advance(timestamp, signal, quotes, execute_current_decision=False)

    def on_causal_quote(
        self,
        timestamp: datetime,
        signal: Signal,
        quotes: dict[str, Quote],
    ) -> list[ShadowFill]:
        """Execute a completed-bar decision at its first causally available quote.

        The controller calls this only after the analysis bar has completed. ``timestamp``
        and ``quotes`` therefore describe the first quote/open of the following bar, never
        a price from inside the bar that produced ``signal``.
        """

        return self._advance(timestamp, signal, quotes, execute_current_decision=True)

    def _advance(
        self,
        timestamp: datetime,
        signal: Signal,
        quotes: dict[str, Quote],
        *,
        execute_current_decision: bool,
    ) -> list[ShadowFill]:
        if not self.state.active:
            return []
        self._analysis_count += 1
        fills: list[ShadowFill] = []
        current_session = session_key(timestamp, self.config.market_hours)
        if self._current_session is None:
            self._current_session = current_session
        elif current_session != self._current_session:
            if self.config.settlement_model == "cash_t1":
                self.state.cash += self.state.unsettled_cash
                self.state.unsettled_cash = 0.0
            self._current_session = current_session
        if (
            self._pending is not None
            and self.config.time_in_force == "gfd"
            and self._pending_session != current_session
        ):
            self._pending = None
            self._pending_session = None
        window_allowed = self.policy.trading_window_allowed(timestamp)
        pending_is_exit = (
            self.state.position is not None
            and self._pending
            and (self._pending[0] != self.state.position.symbol)
        )
        pending_window = self.policy.exit_window_allowed(timestamp) if pending_is_exit else window_allowed
        if self._pending is not None and pending_window:
            target, reason = self._pending
            complete, fill = self._transition(timestamp, target, reason, quotes)
            if fill:
                fills.append(fill)
                self.state.fills.append(fill)
            if complete:
                self._pending = None
                self._pending_session = None

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
        if self._analysis_count % self.config.decision_stride == 0:
            decision = self.policy.decide(signal, timestamp, policy_position)
            current = position.symbol if position else None
            decision_window = (
                self.policy.exit_window_allowed(timestamp) if current is not None else window_allowed
            )
            if decision_window and decision.target_symbol != current and self._pending is None:
                if execute_current_decision:
                    complete, fill = self._transition(
                        timestamp,
                        decision.target_symbol,
                        decision.reason,
                        quotes,
                    )
                    if fill:
                        fills.append(fill)
                        self.state.fills.append(fill)
                    if not complete:
                        self._pending = (decision.target_symbol, decision.reason)
                        self._pending_session = current_session
                else:
                    self._pending = (decision.target_symbol, decision.reason)
                    self._pending_session = current_session
        self._mark(quotes)
        return fills

    def stop(
        self,
        quotes: dict[str, Quote] | None = None,
        *,
        flatten_at: datetime | None = None,
        flatten_reason: str = "Virtual end-of-day flatten",
    ) -> ShadowState:
        if flatten_at is not None and self.state.position is not None:
            _complete, fill = self._transition(
                flatten_at,
                None,
                flatten_reason,
                quotes or {},
                force_fill=True,
            )
            if fill is not None:
                self.state.fills.append(fill)
        self.state.active = False
        self._pending = None
        self._pending_session = None
        if quotes:
            self._mark(quotes)
        return self.state

    def _transition(
        self,
        timestamp: datetime,
        target: str | None,
        reason: str,
        quotes: dict[str, Quote],
        *,
        force_fill: bool = False,
    ) -> tuple[bool, ShadowFill | None]:
        position = self.state.position
        if position:
            if target == position.symbol:
                return True, None
            quote = quotes.get(UNDERLYING[position.symbol])
            if not quote:
                return False, None
            price = self._price(quote, "sell")
            if self.config.order_type == "limit" and not force_fill:
                limit_price = quote.bid * (1 - self.config.limit_offset_bps / 10_000)
                if price < limit_price:
                    return False, None
            proceeds = position.quantity * price - self.config.commission_per_order
            realized = proceeds - position.entry_cost
            if self.config.settlement_model == "cash_t1":
                self.state.unsettled_cash += proceeds
            else:
                self.state.cash += proceeds
            self.state.position = None
            fill = ShadowFill(
                timestamp,
                position.symbol,
                "sell",
                position.quantity,
                price,
                realized,
                reason or "Shadow exit",
                self.state.cash,
                self.state.unsettled_cash,
            )
            return target is None, fill
        if target is None:
            return True, None
        quote = quotes.get(UNDERLYING[target])
        if not quote:
            return False, None
        price = self._price(quote, "buy")
        if self.config.order_type == "limit":
            limit_price = quote.ask * (1 + self.config.limit_offset_bps / 10_000)
            if price > limit_price:
                return False, None
        budget = min(
            self.config.order_notional,
            self.state.cash * self.config.max_exposure_pct,
            max(0.0, self.state.cash - self.config.commission_per_order),
        )
        quantity = budget / price if price > 0 else 0.0
        if self.config.order_type == "limit":
            quantity = float(int(quantity))
        if quantity <= 0:
            return True, None
        cost = quantity * price + self.config.commission_per_order
        self.state.cash -= cost
        self.state.position = ShadowPosition(target, quantity, price, timestamp, cost)
        return True, ShadowFill(
            timestamp,
            target,
            "buy",
            quantity,
            price,
            None,
            reason or "Shadow entry",
            self.state.cash,
            self.state.unsettled_cash,
        )

    def _price(self, quote: Quote, side: str) -> float:
        reference = (
            (quote.ask if quote.ask > 0 else quote.mid)
            if side == "buy"
            else (quote.bid if quote.bid > 0 else quote.mid)
        )
        direction = 1.0 if side == "buy" else -1.0
        return reference * (1.0 + direction * self.config.slippage_bps / 10_000.0)

    def _mark(self, quotes: dict[str, Quote]) -> None:
        value = self.state.cash + self.state.unsettled_cash
        if self.state.position:
            quote = quotes.get(UNDERLYING[self.state.position.symbol])
            if quote:
                value += self.state.position.quantity * quote.mid
        self.state.equity = value
        self.state.pnl = value - self.state.starting_cash

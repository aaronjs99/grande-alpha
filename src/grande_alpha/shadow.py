from __future__ import annotations

import random
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from grande_alpha.candidate_execution import (
    annualized_volatility,
    contract_from_config,
    daily_loss_reached,
    decision_due,
    effective_spread_bps,
    entry_block_reason,
    execution_price,
    fillable_quantity,
    held_minutes,
    next_consecutive_losses,
    size_entry,
)
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
    commission: float = 0.0
    requested_quantity: float = 0.0
    fill_fraction: float = 1.0
    execution_cost: float = 0.0

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


@dataclass(frozen=True)
class _PendingTransition:
    target: str | None
    reason: str
    due_analysis_count: int
    session: str


class LiveShadowEngine:
    """Live-quote virtual executor. This module has no broker dependency by design."""

    def __init__(self, config: SandboxConfig, *, bar_minutes: float | None = None) -> None:
        config.validate()
        self.config = config
        self.contract = contract_from_config(config)
        self.policy = DecisionPolicy(
            PolicyConfig(
                bullish_symbol="TQQQS",
                bearish_symbol="SQQQS",
                hard_stop_pct=self.contract.hard_stop_pct,
                take_profit_pct=self.contract.take_profit_pct,
                max_hold_minutes=self.contract.max_hold_minutes,
                no_trade_open_minutes=self.contract.no_trade_open_minutes,
                no_trade_close_minutes=self.contract.no_trade_close_minutes,
                market_hours=self.contract.market_hours,
            )
        )
        self.state = ShadowState(
            starting_cash=self.contract.initial_cash,
            cash=self.contract.initial_cash,
            equity=self.contract.initial_cash,
        )
        self._pending: _PendingTransition | None = None
        self._current_session: str | None = None
        self._analysis_count = 0
        self._session_analysis_count = 0
        self._last_decision_count = 0
        self._bar_minutes = (
            float(bar_minutes)
            if bar_minutes is not None
            else max(float(config.csv_bar_seconds) / 60.0, 1.0 / 60.0)
        )
        if self._bar_minutes <= 0:
            raise ValueError("Analysis bar duration must be positive")
        self._rng = random.Random(self.contract.random_seed)
        self._entries_by_session: dict[str, int] = {}
        self._session_start_equity: dict[str, float] = {}
        self._session_peak_equity: dict[str, float] = {}
        self._paused_sessions: set[str] = set()
        self._consecutive_losses = 0
        self._recent_returns = {"TQQQS": deque(maxlen=30), "SQQQS": deque(maxlen=30)}
        self._previous_prices: dict[str, float] = {}

    def on_bar(self, timestamp: datetime, signal: Signal, quotes: dict[str, Quote]) -> list[ShadowFill]:
        """Advance a synthetic bar trace whose decision fills on the next call."""

        return self._advance(timestamp, signal, quotes, execute_current_decision=False)

    def on_causal_quote(
        self,
        timestamp: datetime,
        signal: Signal,
        quotes: dict[str, Quote],
    ) -> list[ShadowFill]:
        """Execute a completed-bar decision at its first causally available quote."""

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
        fills: list[ShadowFill] = []
        current_session = session_key(timestamp, self.contract.market_hours)
        if self._current_session is None:
            self._current_session = current_session
            self._session_analysis_count = 0
            self._last_decision_count = 0
        elif current_session != self._current_session:
            if self.contract.settlement_model == "cash_t1":
                self.state.cash += self.state.unsettled_cash
                self.state.unsettled_cash = 0.0
            self._current_session = current_session
            self._consecutive_losses = 0
            self._session_analysis_count = 0
            self._last_decision_count = 0
        self._analysis_count += 1
        self._session_analysis_count += 1
        if (
            self._pending is not None
            and self.contract.time_in_force == "gfd"
            and self._pending.session != current_session
        ):
            self._pending = None

        self._update_returns(quotes)
        self._mark(quotes)
        self._session_start_equity.setdefault(current_session, self.state.equity)
        self._session_peak_equity[current_session] = max(
            self._session_peak_equity.get(current_session, self.state.equity),
            self.state.equity,
        )
        if daily_loss_reached(
            self.contract,
            session_start_equity=self._session_start_equity[current_session],
            session_peak_equity=self._session_peak_equity[current_session],
            current_equity=self.state.equity,
        ):
            self._paused_sessions.add(current_session)
            if self.state.position is not None and self._pending is None:
                self._pending = _PendingTransition(
                    None,
                    "Daily loss pause",
                    self._analysis_count + 1 + self.contract.latency_bars,
                    current_session,
                )

        window_allowed = self.policy.trading_window_allowed(timestamp)
        pending_is_exit = bool(
            self.state.position is not None
            and self._pending is not None
            and self._pending.target != self.state.position.symbol
        )
        pending_window = self.policy.exit_window_allowed(timestamp) if pending_is_exit else window_allowed
        if (
            self._pending is not None
            and self._analysis_count >= self._pending.due_analysis_count
            and pending_window
        ):
            complete, fill = self._transition(
                timestamp,
                self._pending.target,
                self._pending.reason,
                quotes,
            )
            if fill:
                fills.append(fill)
                self.state.fills.append(fill)
                self._record_realized(fill, current_session)
            if complete:
                self._pending = None
            elif self._pending is not None:
                self._pending = _PendingTransition(
                    self._pending.target,
                    self._pending.reason,
                    self._analysis_count + 1,
                    current_session,
                )

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
                held_minutes(position.entry_time, timestamp),
            )
            if position
            else None
        )
        if decision_due(
            analysis_count=self._session_analysis_count,
            last_decision_count=self._last_decision_count,
            decision_stride=self.contract.decision_stride,
        ):
            self._last_decision_count = self._session_analysis_count
            decision = self.policy.decide(signal, timestamp, policy_position)
            current = position.symbol if position else None
            decision_window = (
                self.policy.exit_window_allowed(timestamp) if current is not None else window_allowed
            )
            entry_blocked = bool(
                current is None
                and decision.target_symbol is not None
                and entry_block_reason(
                    self.contract,
                    entries_this_session=self._entries_by_session.get(current_session, 0),
                    consecutive_losses=self._consecutive_losses,
                    daily_loss_paused=current_session in self._paused_sessions,
                )
            )
            if (
                decision_window
                and not entry_blocked
                and decision.target_symbol != current
                and self._pending is None
            ):
                due = self._analysis_count + self.contract.latency_bars
                if not execute_current_decision:
                    due += 1
                pending = _PendingTransition(
                    decision.target_symbol,
                    decision.reason,
                    due,
                    current_session,
                )
                if due <= self._analysis_count:
                    complete, fill = self._transition(
                        timestamp,
                        pending.target,
                        pending.reason,
                        quotes,
                    )
                    if fill:
                        fills.append(fill)
                        self.state.fills.append(fill)
                        self._record_realized(fill, current_session)
                    if not complete:
                        self._pending = _PendingTransition(
                            pending.target,
                            pending.reason,
                            self._analysis_count + 1,
                            current_session,
                        )
                else:
                    self._pending = pending
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
            if not force_fill and self._rng.random() < self.contract.rejection_rate_pct / 100.0:
                return False, None
            spread = effective_spread_bps(self.contract, quoted_spread_bps=quote.spread_bps)
            price = execution_price(
                self.contract,
                reference_price=quote.mid,
                side="sell",
                spread_bps=spread,
            )
            if self.contract.order_type == "limit" and not force_fill:
                modeled_bid = quote.mid * (1 - spread / 20_000)
                limit_price = modeled_bid * (1 - self.contract.limit_offset_bps / 10_000)
                if price < limit_price:
                    return False, None
            requested = position.quantity
            quantity = (
                requested
                if force_fill
                else fillable_quantity(self.contract, requested_quantity=requested)
            )
            if quantity <= 0:
                return False, None
            cost_share = position.entry_cost * (quantity / position.quantity)
            proceeds = quantity * price - self.contract.commission_per_order
            realized = proceeds - cost_share
            if self.contract.settlement_model == "cash_t1":
                self.state.unsettled_cash += proceeds
            else:
                self.state.cash += proceeds
            remaining = position.quantity - quantity
            self.state.position = None
            if remaining > 1e-9:
                self.state.position = ShadowPosition(
                    position.symbol,
                    remaining,
                    position.entry_price,
                    position.entry_time,
                    position.entry_cost - cost_share,
                )
            execution_cost = max(0.0, (quote.mid - price) * quantity) + self.contract.commission_per_order
            fill = ShadowFill(
                timestamp,
                position.symbol,
                "sell",
                quantity,
                price,
                realized,
                reason or "Shadow exit",
                self.state.cash,
                self.state.unsettled_cash,
                self.contract.commission_per_order,
                requested,
                quantity / requested,
                execution_cost,
            )
            return target is None and self.state.position is None, fill
        if target is None:
            return True, None
        current_session = session_key(timestamp, self.contract.market_hours)
        if entry_block_reason(
            self.contract,
            entries_this_session=self._entries_by_session.get(current_session, 0),
            consecutive_losses=self._consecutive_losses,
            daily_loss_paused=current_session in self._paused_sessions,
        ):
            return True, None
        quote = quotes.get(UNDERLYING[target])
        if not quote:
            return False, None
        spread = effective_spread_bps(self.contract, quoted_spread_bps=quote.spread_bps)
        price = execution_price(
            self.contract,
            reference_price=quote.mid,
            side="buy",
            spread_bps=spread,
        )
        if self.contract.order_type == "limit":
            modeled_ask = quote.mid * (1 + spread / 20_000)
            limit_price = modeled_ask * (1 + self.contract.limit_offset_bps / 10_000)
            if price > limit_price:
                return False, None
        realized = annualized_volatility(
            tuple(self._recent_returns[target]),
            bar_minutes=self._bar_minutes,
            market_hours=self.contract.market_hours,
        )
        sizing = size_entry(
            self.contract,
            equity=self.state.equity,
            settled_cash=self.state.cash,
            price=price,
            realized_volatility=realized,
        )
        requested = sizing.requested_quantity
        if requested <= 0:
            return True, None
        if self._rng.random() < self.contract.rejection_rate_pct / 100.0:
            return False, None
        quantity = sizing.fillable_quantity
        if quantity <= 0:
            return False, None
        cost = quantity * price + self.contract.commission_per_order
        self.state.cash -= cost
        self.state.position = ShadowPosition(target, quantity, price, timestamp, cost)
        self._entries_by_session[current_session] = self._entries_by_session.get(current_session, 0) + 1
        execution_cost = max(0.0, (price - quote.mid) * quantity) + self.contract.commission_per_order
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
            self.contract.commission_per_order,
            requested,
            quantity / requested,
            execution_cost,
        )

    def _record_realized(self, fill: ShadowFill, current_session: str) -> None:
        if fill.realized_pnl is None:
            return
        self._consecutive_losses = next_consecutive_losses(
            self._consecutive_losses,
            fill.realized_pnl,
        )
        if self._consecutive_losses >= self.contract.max_consecutive_losses:
            self._paused_sessions.add(current_session)

    def _update_returns(self, quotes: dict[str, Quote]) -> None:
        for alias, underlying in UNDERLYING.items():
            quote = quotes.get(underlying)
            if quote is None:
                continue
            mark = quote.mid
            previous = self._previous_prices.get(alias)
            if previous is not None and previous > 0:
                self._recent_returns[alias].append(mark / previous - 1.0)
            self._previous_prices[alias] = mark

    def _mark(self, quotes: dict[str, Quote]) -> None:
        value = self.state.cash + self.state.unsettled_cash
        if self.state.position:
            quote = quotes.get(UNDERLYING[self.state.position.symbol])
            if quote:
                value += self.state.position.quantity * quote.mid
        self.state.equity = value
        self.state.pnl = value - self.state.starting_cash

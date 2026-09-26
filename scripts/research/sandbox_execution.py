from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from grande_alpha.domain.policy import session_key
from grande_alpha.execution.candidate_execution import (
    CandidateExecutionContract,
    annualized_volatility,
    effective_spread_bps,
    execution_price,
    fillable_quantity,
    observed_range_bps,
    size_entry,
)
from grande_alpha.research.historical_models import ReplayFrame
from grande_alpha.research.sandbox_models import ExecutionEvent, SandboxFill


@dataclass
class _VirtualPosition:
    symbol: str
    quantity: float
    entry_price: float
    cost_total: float
    entry_index: int
    entry_time: datetime


class SandboxExecutionModel:
    """Apply deterministic fills, partial-fill rules, and cash settlement in replay."""

    def __init__(self, contract: CandidateExecutionContract, rng: random.Random, bar_minutes: float) -> None:
        self.contract = contract
        self.rng = rng
        self._bar_minutes = bar_minutes

    def transition(
        self,
        frame: ReplayFrame,
        index: int,
        cash: float,
        unsettled_cash: float,
        position: _VirtualPosition | None,
        target: str | None,
        reason: str,
        entries_by_day: dict[str, int],
        paused_days: set[str],
        recent_returns: dict[str, deque[float]],
        previous_range_bps: dict[str, float],
        previous_volume: dict[str, float | None],
        use_close: bool = False,
        bypass_execution_failures: bool = False,
    ) -> tuple[
        float,
        float,
        _VirtualPosition | None,
        list[SandboxFill],
        list[ExecutionEvent],
        bool,
    ]:
        fills: list[SandboxFill] = []
        events: list[ExecutionEvent] = []
        if position is not None and position.symbol != target:
            cash, unsettled_cash, position, fill, event, complete = self._sell(
                frame,
                cash,
                unsettled_cash,
                position,
                reason,
                use_close,
                bypass_execution_failures,
                previous_range_bps[position.symbol],
                previous_volume[position.symbol],
            )
            events.append(event)
            if fill:
                fills.append(fill)
            if not complete:
                return cash, unsettled_cash, position, fills, events, False
        if target is not None and position is None:
            day = session_key(frame.start, self.contract.market_hours)
            if day in paused_days or entries_by_day.get(day, 0) >= self.contract.max_entries_per_day:
                events.append(ExecutionEvent(frame.start, target, "buy", "risk_blocked", 0, 0, reason))
                return cash, unsettled_cash, None, fills, events, True
            cash, position, fill, event = self._buy(
                frame,
                index,
                cash,
                unsettled_cash,
                target,
                reason,
                recent_returns[target],
                previous_range_bps[target],
                previous_volume[target],
                use_close,
            )
            events.append(event)
            if fill:
                fills.append(fill)
                entries_by_day[day] = entries_by_day.get(day, 0) + 1
            return (
                cash,
                unsettled_cash,
                position,
                fills,
                events,
                event.status not in {"rejected", "limit_unfilled"},
            )
        return cash, unsettled_cash, position, fills, events, True

    def _sell(
        self,
        frame: ReplayFrame,
        cash: float,
        unsettled_cash: float,
        position: _VirtualPosition,
        reason: str,
        use_close: bool,
        bypass: bool,
        prior_range_bps: float,
        prior_volume: float | None,
    ) -> tuple[
        float,
        float,
        _VirtualPosition | None,
        SandboxFill | None,
        ExecutionEvent,
        bool,
    ]:
        if not bypass and self.rng.random() < self.contract.rejection_rate_pct / 100.0:
            return (
                cash,
                unsettled_cash,
                position,
                None,
                ExecutionEvent(
                    frame.start, position.symbol, "sell", "rejected", position.quantity, 0, reason
                ),
                False,
            )
        bar = frame.bar_for_alias(position.symbol)
        requested = position.quantity
        available_volume = bar.volume if use_close and bar.volume > 0 else prior_volume
        quantity = self._fillable_quantity(available_volume, requested, bypass)
        if self.contract.whole_shares_required and not bypass:
            quantity = float(math.floor(quantity + 1e-9))
        raw = bar.close if use_close else bar.open
        spread = self._dynamic_spread_bps(bar, prior_range_bps, use_close)
        price = self._execution_price(raw, "sell", spread)
        if self.contract.order_type == "limit" and not bypass:
            modeled_bid = raw * (1 - spread / 20_000)
            limit_price = modeled_bid * (1 - self.contract.limit_offset_bps / 10_000)
            if price < limit_price:
                return (
                    cash,
                    unsettled_cash,
                    position,
                    None,
                    ExecutionEvent(
                        frame.start,
                        position.symbol,
                        "sell",
                        "limit_unfilled",
                        requested,
                        0,
                        reason,
                    ),
                    False,
                )
        if quantity <= 0:
            return (
                cash,
                unsettled_cash,
                position,
                None,
                ExecutionEvent(frame.start, position.symbol, "sell", "limit_unfilled", requested, 0, reason),
                False,
            )
        cost_share = position.cost_total * (quantity / position.quantity)
        proceeds = quantity * price - self.contract.commission_per_order
        if self.contract.settlement_model == "cash_t1":
            unsettled_cash += proceeds
        else:
            cash += proceeds
        realized = proceeds - cost_share
        remaining = position.quantity - quantity
        next_position = None
        if remaining > 1e-9:
            next_position = _VirtualPosition(
                position.symbol,
                remaining,
                position.entry_price,
                position.cost_total - cost_share,
                position.entry_index,
                position.entry_time,
            )
        execution_cost = max(0.0, (raw - price) * quantity) + self.contract.commission_per_order
        fraction = quantity / requested
        status = "filled" if next_position is None else "partially_filled"
        fill = SandboxFill(
            frame.start,
            position.symbol,
            "sell",
            quantity,
            price,
            self.contract.commission_per_order,
            realized,
            reason,
            cash,
            requested,
            fraction,
            execution_cost,
            unsettled_cash,
        )
        event = ExecutionEvent(frame.start, position.symbol, "sell", status, requested, quantity, reason)
        return cash, unsettled_cash, next_position, fill, event, next_position is None

    def _buy(
        self,
        frame: ReplayFrame,
        index: int,
        cash: float,
        unsettled_cash: float,
        target: str,
        reason: str,
        recent_returns: deque[float],
        prior_range_bps: float,
        prior_volume: float | None,
        use_close: bool,
    ) -> tuple[float, _VirtualPosition | None, SandboxFill | None, ExecutionEvent]:
        bar = frame.bar_for_alias(target)
        raw = bar.close if use_close else bar.open
        equity = cash + unsettled_cash
        spread = self._dynamic_spread_bps(bar, prior_range_bps, use_close)
        price = self._execution_price(raw, "buy", spread)
        realized = annualized_volatility(
            tuple(recent_returns),
            bar_minutes=self._bar_minutes,
            market_hours=self.contract.market_hours,
        )
        sizing = size_entry(
            self.contract,
            equity=equity,
            settled_cash=cash,
            price=price,
            realized_volatility=realized,
            available_volume=(bar.volume if use_close and bar.volume > 0 else prior_volume),
        )
        requested = sizing.requested_quantity
        if self.contract.order_type == "limit":
            modeled_ask = raw * (1 + spread / 20_000)
            limit_price = modeled_ask * (1 + self.contract.limit_offset_bps / 10_000)
            if price > limit_price:
                return (
                    cash,
                    None,
                    None,
                    ExecutionEvent(frame.start, target, "buy", "limit_unfilled", requested, 0, reason),
                )
        if requested <= 0:
            return cash, None, None, ExecutionEvent(frame.start, target, "buy", "risk_blocked", 0, 0, reason)
        if self.rng.random() < self.contract.rejection_rate_pct / 100.0:
            return (
                cash,
                None,
                None,
                ExecutionEvent(frame.start, target, "buy", "rejected", requested, 0, reason),
            )
        quantity = sizing.fillable_quantity
        if quantity <= 0:
            return (
                cash,
                None,
                None,
                ExecutionEvent(frame.start, target, "buy", "limit_unfilled", requested, 0, reason),
            )
        cost = quantity * price + self.contract.commission_per_order
        cash -= cost
        execution_cost = max(0.0, (price - raw) * quantity) + self.contract.commission_per_order
        position = _VirtualPosition(target, quantity, price, cost, index, frame.start)
        fraction = quantity / requested
        status = "filled" if fraction >= 0.999999 else "partially_filled"
        fill = SandboxFill(
            frame.start,
            target,
            "buy",
            quantity,
            price,
            self.contract.commission_per_order,
            None,
            reason,
            cash,
            requested,
            fraction,
            execution_cost,
            unsettled_cash,
        )
        return (
            cash,
            position,
            fill,
            ExecutionEvent(frame.start, target, "buy", status, requested, quantity, reason),
        )

    def _fillable_quantity(
        self,
        available_volume: float | None,
        requested: float,
        bypass: bool,
    ) -> float:
        if bypass:
            return requested
        return fillable_quantity(
            self.contract,
            requested_quantity=requested,
            available_volume=available_volume,
        )

    def _dynamic_spread_bps(self, bar, prior_range_bps: float, use_close: bool) -> float:
        range_bps = observed_range_bps(bar.open, bar.high, bar.low) if use_close else prior_range_bps
        return effective_spread_bps(
            self.contract,
            range_bps=range_bps,
        )

    def _execution_price(self, price: float, side: str, spread_bps: float) -> float:
        return execution_price(
            self.contract,
            reference_price=price,
            side=side,
            spread_bps=spread_bps,
        )

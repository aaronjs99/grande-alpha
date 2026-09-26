from __future__ import annotations

import math
import random
import statistics
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from grande_alpha.domain.policy import (
    DecisionPolicy,
    PolicyConfig,
    PolicyPosition,
    session_key,
    session_minutes,
)
from grande_alpha.execution.candidate_execution import (
    annualized_volatility,
    contract_from_config,
    daily_loss_reached,
    decision_due,
    effective_spread_bps,
    execution_price,
    fillable_quantity,
    held_minutes,
    next_consecutive_losses,
    observed_range_bps,
    size_entry,
)
from grande_alpha.research.historical import HistoricalBundle, ReplayFrame
from grande_alpha.research.sandbox_models import (
    EquityPoint,
    ExecutionEvent,
    SandboxConfig,
    SandboxFill,
    SandboxResult,
    interval_minutes,
)
from grande_alpha.strategy.core import build_strategy


@dataclass
class _VirtualPosition:
    symbol: str
    quantity: float
    entry_price: float
    cost_total: float
    entry_index: int
    entry_time: datetime


class SandboxReplayEngine:
    """Deterministic virtual execution with no broker or order-submission dependency."""

    def __init__(self, config: SandboxConfig) -> None:
        config.validate()
        self.config = config
        self.contract = contract_from_config(config)
        self.rng = random.Random(self.contract.random_seed)
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

    def run(self, bundle: HistoricalBundle) -> SandboxResult:
        if len(bundle.frames) < self.config.warmup_bars + 3:
            raise ValueError("The dataset is too short for the selected warm-up")
        strategy = build_strategy(self.config.strategy_config())
        cash = self.contract.initial_cash
        unsettled_cash = 0.0
        position: _VirtualPosition | None = None
        scheduled: tuple[int, str | None, str] | None = None
        entries_by_day: dict[str, int] = {}
        day_start_equity: dict[str, float] = {}
        day_peak_equity: dict[str, float] = {}
        paused_days: set[str] = set()
        fills: list[SandboxFill] = []
        events: list[ExecutionEvent] = []
        closed_pnl: list[float] = []
        curve: list[EquityPoint] = []
        recent_returns = {"TQQQS": deque(maxlen=30), "SQQQS": deque(maxlen=30)}
        previous_prices: dict[str, float] = {}
        previous_range_bps = {"TQQQS": 0.0, "SQQQS": 0.0}
        previous_volume: dict[str, float | None] = {"TQQQS": None, "SQQQS": None}
        consecutive_losses = 0
        session_bar_counts: dict[str, int] = {}
        last_decision_counts: dict[str, int] = {}
        bar_minutes = interval_minutes(bundle.interval)
        self._bar_minutes = bar_minutes
        session_last_indices: dict[str, int] = {}
        for frame_index, replay_frame in enumerate(bundle.frames):
            session_day = session_key(replay_frame.start, self.contract.market_hours)
            session_last_indices[session_day] = frame_index

        def window_allowed(frame: ReplayFrame) -> bool:
            return bundle.interval == "1d" or self.policy.trading_window_allowed(frame.start)

        def exit_window_allowed(frame: ReplayFrame) -> bool:
            return bundle.interval == "1d" or self.policy.exit_window_allowed(frame.start)

        previous_session: str | None = None
        for index, frame in enumerate(bundle.frames):
            day = session_key(frame.start, self.contract.market_hours)
            if previous_session is not None and day != previous_session:
                consecutive_losses = 0
            if (
                self.contract.settlement_model == "cash_t1"
                and previous_session is not None
                and day != previous_session
                and abs(unsettled_cash) > 1e-12
            ):
                cash += unsettled_cash
                unsettled_cash = 0.0
            if (
                previous_session is not None
                and day != previous_session
                and self.contract.time_in_force == "gfd"
            ):
                scheduled = None
            previous_session = day
            session_bar_counts[day] = session_bar_counts.get(day, 0) + 1
            starting_equity = self._equity(cash, unsettled_cash, position, frame)
            day_start_equity.setdefault(day, starting_equity)
            day_peak_equity[day] = max(day_peak_equity.get(day, starting_equity), starting_equity)
            if daily_loss_reached(
                self.contract,
                session_start_equity=day_start_equity[day],
                session_peak_equity=day_peak_equity[day],
                current_equity=starting_equity,
            ):
                paused_days.add(day)
                if position is not None and window_allowed(frame):
                    scheduled = (index + 1 + self.contract.latency_bars, None, "Daily loss pause")

            scheduled_is_exit = position is not None and scheduled and scheduled[1] != position.symbol
            if (
                scheduled
                and index >= scheduled[0]
                and (exit_window_allowed(frame) if scheduled_is_exit else window_allowed(frame))
            ):
                cash, unsettled_cash, position, new_fills, new_events, complete = self._transition(
                    frame,
                    index,
                    cash,
                    unsettled_cash,
                    position,
                    scheduled[1],
                    scheduled[2],
                    entries_by_day,
                    paused_days,
                    recent_returns,
                    previous_range_bps,
                    previous_volume,
                )
                fills.extend(new_fills)
                events.extend(new_events)
                for fill in new_fills:
                    if fill.realized_pnl is not None:
                        closed_pnl.append(fill.realized_pnl)
                        consecutive_losses = next_consecutive_losses(
                            consecutive_losses, fill.realized_pnl
                        )
                        if consecutive_losses >= self.contract.max_consecutive_losses:
                            paused_days.add(day)
                scheduled = None if complete else (index + 1, scheduled[1], scheduled[2])

            signal = strategy.on_bar(frame.qqq)
            policy_position = None
            if position is not None:
                policy_position = PolicyPosition(
                    position.symbol,
                    position.entry_price,
                    frame.bar_for_alias(position.symbol).close,
                    held_minutes(position.entry_time, frame.start),
                )
            is_decision_due = decision_due(
                analysis_count=session_bar_counts[day],
                last_decision_count=last_decision_counts.get(day, 0),
                decision_stride=self.contract.decision_stride,
            )
            if is_decision_due:
                last_decision_counts[day] = session_bar_counts[day]
                decision = self.policy.decide(signal, frame.start, policy_position)
                current = position.symbol if position else None
                decision_window = exit_window_allowed(frame) if current is not None else window_allowed(frame)
                if decision_window and decision.target_symbol != current:
                    if scheduled is None or scheduled[1] != decision.target_symbol:
                        scheduled = (
                            index + 1 + self.contract.latency_bars,
                            decision.target_symbol,
                            decision.reason,
                        )

            if position is not None and self.contract.force_flat_at_end and index == session_last_indices[day]:
                cash, unsettled_cash, position, new_fills, new_events, _ = self._transition(
                    frame,
                    index,
                    cash,
                    unsettled_cash,
                    position,
                    None,
                    "Session-end forced virtual flatten",
                    entries_by_day,
                    paused_days,
                    recent_returns,
                    previous_range_bps,
                    previous_volume,
                    use_close=True,
                    bypass_execution_failures=True,
                )
                fills.extend(new_fills)
                events.extend(new_events)
                for fill in new_fills:
                    if fill.realized_pnl is not None:
                        closed_pnl.append(fill.realized_pnl)
                        consecutive_losses = next_consecutive_losses(
                            consecutive_losses, fill.realized_pnl
                        )
                scheduled = None

            # Opening fills may use only observations from a previously completed frame.
            for alias in ("TQQQS", "SQQQS"):
                completed_bar = frame.bar_for_alias(alias)
                mark = completed_bar.close
                if alias in previous_prices and previous_prices[alias] > 0:
                    recent_returns[alias].append(mark / previous_prices[alias] - 1.0)
                previous_prices[alias] = mark
                previous_range_bps[alias] = observed_range_bps(
                    completed_bar.open,
                    completed_bar.high,
                    completed_bar.low,
                )
                previous_volume[alias] = completed_bar.volume if completed_bar.volume > 0 else None

            equity = self._equity(cash, unsettled_cash, position, frame)
            curve.append(
                EquityPoint(
                    frame.start,
                    equity,
                    cash,
                    position.symbol if position else None,
                    unsettled_cash,
                )
            )

        ending_position = position.symbol if position else None
        if position is not None and self.contract.force_flat_at_end:
            final_frame = bundle.frames[-1]
            cash, unsettled_cash, position, new_fills, new_events, _ = self._transition(
                final_frame,
                len(bundle.frames),
                cash,
                unsettled_cash,
                position,
                None,
                "End of replay forced virtual flatten",
                entries_by_day,
                paused_days,
                recent_returns,
                previous_range_bps,
                previous_volume,
                use_close=True,
                bypass_execution_failures=True,
            )
            fills.extend(new_fills)
            events.extend(new_events)
            closed_pnl.extend(fill.realized_pnl for fill in new_fills if fill.realized_pnl is not None)
            ending_position = None
            curve[-1] = EquityPoint(
                final_frame.start,
                cash + unsettled_cash,
                cash,
                None,
                unsettled_cash,
            )

        final_equity = curve[-1].equity
        max_drawdown, max_drawdown_bars = self._drawdown(curve)
        wins = [value for value in closed_pnl if value > 0]
        losses = [value for value in closed_pnl if value < 0]
        win_rate = len(wins) / len(closed_pnl) if closed_pnl else 0.0
        gross_profit, gross_loss = sum(wins), abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit else 0.0)
        expectancy = statistics.fmean(closed_pnl) if closed_pnl else 0.0
        first, last = bundle.frames[0], bundle.frames[-1]
        returns = [
            curve[index].equity / curve[index - 1].equity - 1.0
            for index in range(1, len(curve))
            if curve[index - 1].equity > 0
        ]
        annualization = math.sqrt(max(1.0, 252 * session_minutes(self.contract.market_hours) / bar_minutes))
        sharpe = self._risk_adjusted(returns, annualization, downside_only=False)
        sortino = self._risk_adjusted(returns, annualization, downside_only=True)
        turnover_dollars = sum(fill.quantity * fill.price for fill in fills)
        execution_cost = sum(fill.execution_cost for fill in fills)
        daily_pnl = self._daily_pnl(curve, self.contract.market_hours)
        daily_returns = self._daily_returns(
            curve,
            self.contract.initial_cash,
            self.contract.market_hours,
        )
        warnings = ["Historical replay is not evidence of future profitability"]
        if not closed_pnl:
            warnings.insert(0, "No complete virtual round trips occurred with these settings")
        if ending_position:
            warnings.append(f"Replay ended holding {ending_position}; final P/L includes unrealized value")
        if bundle.quality and bundle.quality.missing_intervals:
            warnings.append(f"Dataset has {bundle.quality.missing_intervals} missing intraday intervals")
        if self.contract.settlement_model == "cash_t1":
            warnings.append(
                "Cash-account model: sale proceeds become spendable at the next observed trading session"
            )
        return SandboxResult(
            run_id=str(uuid.uuid4()),
            source=bundle.source,
            start=bundle.start,
            end=bundle.end,
            initial_cash=self.contract.initial_cash,
            final_equity=final_equity,
            net_pnl=final_equity - self.contract.initial_cash,
            return_pct=(final_equity / self.contract.initial_cash - 1.0) * 100.0,
            max_drawdown_pct=max_drawdown * 100.0,
            round_trips=len(closed_pnl),
            win_rate=win_rate * 100.0,
            tqqqs_buy_hold_pct=(last.tqqq.close / first.tqqq.open - 1.0) * 100.0,
            sqqqs_buy_hold_pct=(last.sqqq.close / first.sqqq.open - 1.0) * 100.0,
            fills=fills,
            equity_curve=curve,
            warnings=warnings,
            execution_events=events,
            profit_factor=profit_factor,
            expectancy=expectancy,
            average_win=statistics.fmean(wins) if wins else 0.0,
            average_loss=statistics.fmean(losses) if losses else 0.0,
            turnover=turnover_dollars / self.contract.initial_cash,
            exposure_pct=sum(point.position_symbol is not None for point in curve) / len(curve) * 100.0,
            max_drawdown_bars=max_drawdown_bars,
            sharpe=sharpe,
            sortino=sortino,
            total_execution_cost=execution_cost,
            ending_position=ending_position,
            daily_pnl=daily_pnl,
            daily_returns=daily_returns,
            final_unsettled_cash=unsettled_cash,
        )

    def _transition(
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
        range_bps = (
            observed_range_bps(bar.open, bar.high, bar.low) if use_close else prior_range_bps
        )
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

    @staticmethod
    def _equity(
        cash: float,
        unsettled_cash: float,
        position: _VirtualPosition | None,
        frame: ReplayFrame,
    ) -> float:
        if position is None:
            return cash + unsettled_cash
        return cash + unsettled_cash + position.quantity * frame.bar_for_alias(position.symbol).close

    @staticmethod
    def _drawdown(curve: list[EquityPoint]) -> tuple[float, int]:
        peak = curve[0].equity
        peak_index = 0
        max_drawdown = 0.0
        max_duration = 0
        for index, point in enumerate(curve):
            if point.equity >= peak:
                peak, peak_index = point.equity, index
            elif peak > 0:
                drawdown = (peak - point.equity) / peak
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
                    max_duration = index - peak_index
        return max_drawdown, max_duration

    @staticmethod
    def _risk_adjusted(returns: list[float], annualization: float, downside_only: bool) -> float:
        if len(returns) < 2:
            return 0.0
        sample = [min(0.0, value) for value in returns] if downside_only else returns
        deviation = statistics.pstdev(sample)
        return statistics.fmean(returns) / deviation * annualization if deviation > 1e-12 else 0.0

    @staticmethod
    def _daily_pnl(curve: list[EquityPoint], market_hours: str) -> dict[str, float]:
        grouped: dict[str, list[float]] = {}
        for point in curve:
            grouped.setdefault(session_key(point.timestamp, market_hours), []).append(point.equity)
        return {day: values[-1] - values[0] for day, values in grouped.items() if values}

    @staticmethod
    def _daily_returns(
        curve: list[EquityPoint],
        initial_equity: float,
        market_hours: str,
    ) -> list[float]:
        ending_equity: dict[str, float] = {}
        for point in curve:
            day = session_key(point.timestamp, market_hours)
            ending_equity[day] = point.equity
        previous = initial_equity
        returns = []
        for equity in ending_equity.values():
            if previous > 0:
                returns.append(equity / previous - 1.0)
            previous = equity
        return returns

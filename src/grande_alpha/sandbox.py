from __future__ import annotations

import json
import math
import random
import statistics
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from grande_alpha.config import data_dir
from grande_alpha.execution import execution_profile
from grande_alpha.historical import HistoricalBundle, ReplayFrame
from grande_alpha.policy import DecisionPolicy, PolicyConfig, PolicyPosition, session_key, session_minutes
from grande_alpha.strategy import StrategyConfig, build_strategy

INTERVAL_MINUTES = {"5s": 5 / 60, "1m": 1, "5m": 5, "15m": 15, "60m": 60, "1d": 390}


def interval_minutes(interval: str) -> float:
    if interval.endswith("s") and interval[:-1].isdigit():
        return int(interval[:-1]) / 60
    return float(INTERVAL_MINUTES.get(interval, 1))


@dataclass
class SandboxConfig:
    lookback_days: int = 7
    csv_bar_seconds: int = 5
    initial_cash: float = 50.0
    order_notional: float = 25.0
    slippage_bps: float = 2.0
    base_spread_bps: float = 2.0
    spread_volatility_multiplier: float = 0.10
    commission_per_order: float = 0.0
    latency_bars: int = 0
    fill_fraction_pct: float = 100.0
    rejection_rate_pct: float = 0.0
    max_volume_participation_pct: float = 1.0
    market_hours: str = "regular_hours"
    order_type: str = "market"
    time_in_force: str = "gfd"
    limit_offset_bps: float = 10.0
    settlement_model: str = "cash_t1"
    random_seed: int = 7007
    strategy_name: str = "ema_momentum"
    warmup_bars: int = 24
    fast_ema: int = 8
    slow_ema: int = 21
    trend_threshold_bps: float = 4.0
    momentum_bars: int = 3
    # Number of completed analysis bars between policy action selections.
    # Keep 1 for legacy experiments; use the live value when seeking an exact certificate match.
    decision_stride: int = 1
    trend_short_bars: int = 3
    trend_medium_bars: int = 12
    trend_long_bars: int = 36
    close_momentum_bps: float = 15.0
    opening_range_minutes: int = 30
    breakout_buffer_bps: float = 3.0
    ensemble_min_votes: int = 2
    hard_stop_pct: float = 0.008
    take_profit_pct: float = 0.015
    max_hold_minutes: int = 45
    max_entries_per_day: int = 6
    no_trade_open_minutes: int = 5
    no_trade_close_minutes: int = 10
    risk_budget_pct: float = 0.01
    max_exposure_pct: float = 0.80
    max_daily_loss_pct: float = 0.04
    max_consecutive_losses: int = 3
    volatility_target_pct: float = 0.30
    force_flat_at_end: bool = True

    def validate(self) -> None:
        execution_profile(self)
        if self.settlement_model not in {"cash_t1", "instant"}:
            raise ValueError("Settlement model must be cash_t1 or instant")
        if not 1 <= self.lookback_days <= 10_000:
            raise ValueError("Lookback must be between 1 and 10000 calendar days")
        if not 1 <= self.csv_bar_seconds <= 300:
            raise ValueError("CSV bar interval must be between 1 and 300 seconds")
        if self.initial_cash <= 0 or self.order_notional <= 0:
            raise ValueError("Starting cash and order notional must be positive")
        if self.order_notional > self.initial_cash:
            raise ValueError("Order notional cannot exceed starting virtual cash")
        if self.fast_ema < 1 or self.slow_ema < 2 or self.fast_ema >= self.slow_ema:
            raise ValueError("Fast EMA must be positive and smaller than slow EMA")
        if self.momentum_bars < 1 or self.warmup_bars < self.slow_ema + 2:
            raise ValueError("Warm-up must be at least slow EMA + 2; momentum must be positive")
        if not 1 <= self.decision_stride <= 120:
            raise ValueError("Decision stride must be between 1 and 120 analysis bars")
        if self.trend_threshold_bps <= 0:
            raise ValueError("Trend threshold must be positive")
        nonnegative = (
            self.slippage_bps,
            self.base_spread_bps,
            self.spread_volatility_multiplier,
            self.commission_per_order,
            self.rejection_rate_pct,
        )
        if any(value < 0 for value in nonnegative):
            raise ValueError("Execution costs and rejection rate cannot be negative")
        if self.latency_bars < 0 or not 0 < self.fill_fraction_pct <= 100:
            raise ValueError("Latency must be nonnegative and fill fraction must be in (0,100]")
        if not 0 < self.max_volume_participation_pct <= 100:
            raise ValueError("Volume participation must be in (0,100]")
        if not 0 <= self.rejection_rate_pct <= 100:
            raise ValueError("Rejection rate must be between 0 and 100")
        if self.hard_stop_pct <= 0 or self.take_profit_pct <= 0:
            raise ValueError("Stop and take-profit percentages must be positive")
        if self.max_hold_minutes < 1 or self.max_entries_per_day < 1:
            raise ValueError("Maximum hold and daily entry cap must be positive")
        if (
            self.no_trade_open_minutes < 0
            or self.no_trade_close_minutes < 0
            or self.no_trade_open_minutes + self.no_trade_close_minutes >= 390
        ):
            raise ValueError("No-trade windows must be nonnegative and leave part of the session open")
        for name, value in (
            ("risk budget", self.risk_budget_pct),
            ("maximum exposure", self.max_exposure_pct),
            ("daily loss", self.max_daily_loss_pct),
        ):
            if not 0 < value <= 1:
                raise ValueError(f"{name.title()} percentage must be in (0,1]")
        if self.max_consecutive_losses < 1 or self.volatility_target_pct < 0:
            raise ValueError("Loss pause must be positive and volatility target cannot be negative")
        self.strategy_config().validate()

    def strategy_config(self) -> StrategyConfig:
        return StrategyConfig(
            strategy_name=self.strategy_name,
            warmup_bars=self.warmup_bars,
            fast_ema=self.fast_ema,
            slow_ema=self.slow_ema,
            trend_threshold_bps=self.trend_threshold_bps,
            momentum_bars=self.momentum_bars,
            trend_short_bars=self.trend_short_bars,
            trend_medium_bars=self.trend_medium_bars,
            trend_long_bars=self.trend_long_bars,
            close_momentum_bps=self.close_momentum_bps,
            opening_range_minutes=self.opening_range_minutes,
            breakout_buffer_bps=self.breakout_buffer_bps,
            ensemble_min_votes=self.ensemble_min_votes,
        )


@dataclass(frozen=True)
class SandboxFill:
    timestamp: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    realized_pnl: float | None
    reason: str
    cash_after: float
    requested_quantity: float = 0.0
    fill_fraction: float = 1.0
    execution_cost: float = 0.0
    unsettled_cash_after: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["timestamp"] = self.timestamp.isoformat()
        return values


@dataclass(frozen=True)
class ExecutionEvent:
    timestamp: datetime
    symbol: str
    side: str
    status: str
    requested_quantity: float
    filled_quantity: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["timestamp"] = self.timestamp.isoformat()
        return values


@dataclass(frozen=True)
class EquityPoint:
    timestamp: datetime
    equity: float
    cash: float
    position_symbol: str | None
    unsettled_cash: float = 0.0


@dataclass(frozen=True)
class SandboxResult:
    run_id: str
    source: str
    start: datetime
    end: datetime
    initial_cash: float
    final_equity: float
    net_pnl: float
    return_pct: float
    max_drawdown_pct: float
    round_trips: int
    win_rate: float
    tqqqs_buy_hold_pct: float
    sqqqs_buy_hold_pct: float
    fills: list[SandboxFill]
    equity_curve: list[EquityPoint]
    warnings: list[str] = field(default_factory=list)
    execution_events: list[ExecutionEvent] = field(default_factory=list)
    profit_factor: float = 0.0
    expectancy: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    turnover: float = 0.0
    exposure_pct: float = 0.0
    max_drawdown_bars: int = 0
    sharpe: float = 0.0
    sortino: float = 0.0
    total_execution_cost: float = 0.0
    ending_position: str | None = None
    daily_pnl: dict[str, float] = field(default_factory=dict)
    daily_returns: list[float] = field(default_factory=list)
    final_unsettled_cash: float = 0.0

    def metrics(self) -> dict[str, Any]:
        return {
            "initial_cash": self.initial_cash,
            "final_equity": self.final_equity,
            "net_pnl": self.net_pnl,
            "return_pct": self.return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_drawdown_bars": self.max_drawdown_bars,
            "round_trips": self.round_trips,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "turnover": self.turnover,
            "exposure_pct": self.exposure_pct,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "total_execution_cost": self.total_execution_cost,
            "ending_position": self.ending_position,
            "tqqqs_buy_hold_pct": self.tqqqs_buy_hold_pct,
            "sqqqs_buy_hold_pct": self.sqqqs_buy_hold_pct,
            "daily_pnl": self.daily_pnl,
            "daily_returns": self.daily_returns,
            "final_unsettled_cash": self.final_unsettled_cash,
            "warnings": self.warnings,
        }


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
        self.rng = random.Random(config.random_seed)
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

    def run(self, bundle: HistoricalBundle) -> SandboxResult:
        if len(bundle.frames) < self.config.warmup_bars + 3:
            raise ValueError("The dataset is too short for the selected warm-up")
        strategy = build_strategy(self.config.strategy_config())
        cash = self.config.initial_cash
        unsettled_cash = 0.0
        position: _VirtualPosition | None = None
        scheduled: tuple[int, str | None, str] | None = None
        entries_by_day: dict[str, int] = {}
        day_start_equity: dict[str, float] = {}
        paused_days: set[str] = set()
        fills: list[SandboxFill] = []
        events: list[ExecutionEvent] = []
        closed_pnl: list[float] = []
        curve: list[EquityPoint] = []
        recent_returns = {"TQQQS": deque(maxlen=30), "SQQQS": deque(maxlen=30)}
        previous_prices: dict[str, float] = {}
        consecutive_losses = 0
        session_bar_counts: dict[str, int] = {}
        bar_minutes = interval_minutes(bundle.interval)
        self._bar_minutes = bar_minutes
        session_last_indices: dict[str, int] = {}
        for frame_index, replay_frame in enumerate(bundle.frames):
            session_day = session_key(replay_frame.start, self.config.market_hours)
            session_last_indices[session_day] = frame_index

        def window_allowed(frame: ReplayFrame) -> bool:
            return bundle.interval == "1d" or self.policy.trading_window_allowed(frame.start)

        def exit_window_allowed(frame: ReplayFrame) -> bool:
            return bundle.interval == "1d" or self.policy.exit_window_allowed(frame.start)

        previous_session: str | None = None
        for index, frame in enumerate(bundle.frames):
            day = session_key(frame.start, self.config.market_hours)
            if (
                self.config.settlement_model == "cash_t1"
                and previous_session is not None
                and day != previous_session
                and abs(unsettled_cash) > 1e-12
            ):
                cash += unsettled_cash
                unsettled_cash = 0.0
            if (
                previous_session is not None
                and day != previous_session
                and self.config.time_in_force == "gfd"
            ):
                scheduled = None
            previous_session = day
            session_bar_counts[day] = session_bar_counts.get(day, 0) + 1
            starting_equity = self._equity(cash, unsettled_cash, position, frame)
            day_start_equity.setdefault(day, starting_equity)
            for alias in ("TQQQS", "SQQQS"):
                mark = frame.bar_for_alias(alias).close
                if alias in previous_prices and previous_prices[alias] > 0:
                    recent_returns[alias].append(mark / previous_prices[alias] - 1.0)
                previous_prices[alias] = mark

            if (
                day_start_equity[day] > 0
                and (day_start_equity[day] - starting_equity) / day_start_equity[day]
                >= self.config.max_daily_loss_pct
            ):
                paused_days.add(day)
                if position is not None and window_allowed(frame):
                    scheduled = (index + 1 + self.config.latency_bars, None, "Daily loss pause")

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
                )
                fills.extend(new_fills)
                events.extend(new_events)
                for fill in new_fills:
                    if fill.realized_pnl is not None:
                        closed_pnl.append(fill.realized_pnl)
                        consecutive_losses = consecutive_losses + 1 if fill.realized_pnl < 0 else 0
                        if consecutive_losses >= self.config.max_consecutive_losses:
                            paused_days.add(day)
                scheduled = None if complete else (index + 1, scheduled[1], scheduled[2])

            signal = strategy.on_bar(frame.qqq)
            policy_position = None
            if position is not None:
                policy_position = PolicyPosition(
                    position.symbol,
                    position.entry_price,
                    frame.bar_for_alias(position.symbol).close,
                    max(0, int((frame.start - position.entry_time).total_seconds() // 60)),
                )
            decision_due = session_bar_counts[day] % self.config.decision_stride == 0
            if decision_due:
                decision = self.policy.decide(signal, frame.start, policy_position)
                current = position.symbol if position else None
                decision_window = exit_window_allowed(frame) if current is not None else window_allowed(frame)
                if decision_window and decision.target_symbol != current:
                    if scheduled is None or scheduled[1] != decision.target_symbol:
                        scheduled = (
                            index + 1 + self.config.latency_bars,
                            decision.target_symbol,
                            decision.reason,
                        )

            if position is not None and self.config.force_flat_at_end and index == session_last_indices[day]:
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
                    use_close=True,
                    bypass_execution_failures=True,
                )
                fills.extend(new_fills)
                events.extend(new_events)
                for fill in new_fills:
                    if fill.realized_pnl is not None:
                        closed_pnl.append(fill.realized_pnl)
                        consecutive_losses = consecutive_losses + 1 if fill.realized_pnl < 0 else 0
                scheduled = None

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
        if position is not None and self.config.force_flat_at_end:
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
        annualization = math.sqrt(max(1.0, 252 * session_minutes(self.config.market_hours) / bar_minutes))
        sharpe = self._risk_adjusted(returns, annualization, downside_only=False)
        sortino = self._risk_adjusted(returns, annualization, downside_only=True)
        turnover_dollars = sum(fill.quantity * fill.price for fill in fills)
        execution_cost = sum(fill.execution_cost for fill in fills)
        daily_pnl = self._daily_pnl(curve, self.config.market_hours)
        daily_returns = self._daily_returns(
            curve,
            self.config.initial_cash,
            self.config.market_hours,
        )
        warnings = ["Historical replay is not evidence of future profitability"]
        if not closed_pnl:
            warnings.insert(0, "No complete virtual round trips occurred with these settings")
        if ending_position:
            warnings.append(f"Replay ended holding {ending_position}; final P/L includes unrealized value")
        if bundle.quality and bundle.quality.missing_intervals:
            warnings.append(f"Dataset has {bundle.quality.missing_intervals} missing intraday intervals")
        if self.config.settlement_model == "cash_t1":
            warnings.append(
                "Cash-account model: sale proceeds become spendable at the next observed trading session"
            )
        return SandboxResult(
            run_id=str(uuid.uuid4()),
            source=bundle.source,
            start=bundle.start,
            end=bundle.end,
            initial_cash=self.config.initial_cash,
            final_equity=final_equity,
            net_pnl=final_equity - self.config.initial_cash,
            return_pct=(final_equity / self.config.initial_cash - 1.0) * 100.0,
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
            turnover=turnover_dollars / self.config.initial_cash,
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
            )
            events.append(event)
            if fill:
                fills.append(fill)
            if not complete:
                return cash, unsettled_cash, position, fills, events, False
        if target is not None and position is None:
            day = session_key(frame.start, self.config.market_hours)
            if day in paused_days or entries_by_day.get(day, 0) >= self.config.max_entries_per_day:
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
    ) -> tuple[
        float,
        float,
        _VirtualPosition | None,
        SandboxFill | None,
        ExecutionEvent,
        bool,
    ]:
        if not bypass and self.rng.random() < self.config.rejection_rate_pct / 100.0:
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
        quantity = self._fillable_quantity(bar, requested, bypass)
        if self.config.order_type == "limit" and not bypass:
            quantity = float(math.floor(quantity + 1e-9))
        raw = bar.close if use_close else bar.open
        spread = self._dynamic_spread_bps(bar)
        price = self._execution_price(raw, "sell", spread)
        if self.config.order_type == "limit" and not bypass:
            modeled_bid = raw * (1 - spread / 20_000)
            limit_price = modeled_bid * (1 - self.config.limit_offset_bps / 10_000)
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
        proceeds = quantity * price - self.config.commission_per_order
        if self.config.settlement_model == "cash_t1":
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
        execution_cost = max(0.0, (raw - price) * quantity) + self.config.commission_per_order
        fraction = quantity / requested
        status = "filled" if next_position is None else "partially_filled"
        fill = SandboxFill(
            frame.start,
            position.symbol,
            "sell",
            quantity,
            price,
            self.config.commission_per_order,
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
        use_close: bool,
    ) -> tuple[float, _VirtualPosition | None, SandboxFill | None, ExecutionEvent]:
        bar = frame.bar_for_alias(target)
        raw = bar.close if use_close else bar.open
        equity = cash + unsettled_cash
        risk_notional = equity * self.config.risk_budget_pct / self.config.hard_stop_pct
        exposure_cap = equity * self.config.max_exposure_pct
        volatility_scale = self._volatility_scale(recent_returns)
        budget = min(self.config.order_notional, risk_notional, exposure_cap) * volatility_scale
        budget = min(budget, cash - self.config.commission_per_order)
        spread = self._dynamic_spread_bps(bar)
        price = self._execution_price(raw, "buy", spread)
        requested = max(0.0, budget / price)
        if self.config.order_type == "limit":
            modeled_ask = raw * (1 + spread / 20_000)
            limit_price = modeled_ask * (1 + self.config.limit_offset_bps / 10_000)
            if price > limit_price:
                return (
                    cash,
                    None,
                    None,
                    ExecutionEvent(frame.start, target, "buy", "limit_unfilled", requested, 0, reason),
                )
            requested = float(math.floor(requested))
        if requested <= 0:
            return cash, None, None, ExecutionEvent(frame.start, target, "buy", "risk_blocked", 0, 0, reason)
        if self.rng.random() < self.config.rejection_rate_pct / 100.0:
            return (
                cash,
                None,
                None,
                ExecutionEvent(frame.start, target, "buy", "rejected", requested, 0, reason),
            )
        quantity = self._fillable_quantity(bar, requested, False)
        if self.config.order_type == "limit":
            quantity = float(math.floor(quantity + 1e-9))
        if quantity <= 0:
            return (
                cash,
                None,
                None,
                ExecutionEvent(frame.start, target, "buy", "limit_unfilled", requested, 0, reason),
            )
        cost = quantity * price + self.config.commission_per_order
        cash -= cost
        execution_cost = max(0.0, (price - raw) * quantity) + self.config.commission_per_order
        position = _VirtualPosition(target, quantity, price, cost, index, frame.start)
        fraction = quantity / requested
        status = "filled" if fraction >= 0.999999 else "partially_filled"
        fill = SandboxFill(
            frame.start,
            target,
            "buy",
            quantity,
            price,
            self.config.commission_per_order,
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

    def _fillable_quantity(self, bar, requested: float, bypass: bool) -> float:
        if bypass:
            return requested
        fraction_cap = requested * self.config.fill_fraction_pct / 100.0
        volume_cap = (
            bar.volume * self.config.max_volume_participation_pct / 100.0 if bar.volume > 0 else requested
        )
        return max(0.0, min(requested, fraction_cap, volume_cap))

    def _dynamic_spread_bps(self, bar) -> float:
        range_bps = (bar.high - bar.low) / max(bar.open, 1e-9) * 10_000.0
        return self.config.base_spread_bps + range_bps * self.config.spread_volatility_multiplier

    def _execution_price(self, price: float, side: str, spread_bps: float) -> float:
        direction = 1.0 if side == "buy" else -1.0
        total_bps = self.config.slippage_bps + spread_bps / 2.0
        return price * (1.0 + direction * total_bps / 10_000.0)

    def _volatility_scale(self, returns: deque[float]) -> float:
        if self.config.volatility_target_pct <= 0 or len(returns) < 10:
            return 1.0
        realized = statistics.pstdev(returns) * math.sqrt(252 * 390 / self._bar_minutes)
        if realized <= 1e-9:
            return 1.0
        return min(1.0, self.config.volatility_target_pct / realized)

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


def sandbox_config_path() -> Path:
    return data_dir() / "sandbox_config.json"


def load_sandbox_config() -> SandboxConfig:
    path = sandbox_config_path()
    if not path.exists():
        return SandboxConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = SandboxConfig.__dataclass_fields__.keys()
        config = SandboxConfig(**{key: value for key, value in raw.items() if key in allowed})
        config.validate()
        return config
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return SandboxConfig()


def save_sandbox_config(config: SandboxConfig) -> None:
    config.validate()
    sandbox_config_path().write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")

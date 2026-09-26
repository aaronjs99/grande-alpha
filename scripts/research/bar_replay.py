from __future__ import annotations

import math
import random
import statistics
import uuid
from collections import deque

from grande_alpha.domain.policy import (
    DecisionPolicy,
    PolicyConfig,
    PolicyPosition,
    session_key,
    session_minutes,
)
from grande_alpha.execution.candidate_execution import (
    contract_from_config,
    daily_loss_reached,
    decision_due,
    held_minutes,
    next_consecutive_losses,
    observed_range_bps,
)
from grande_alpha.research.historical import HistoricalBundle, ReplayFrame
from grande_alpha.research.sandbox_execution import SandboxExecutionModel, _VirtualPosition
from grande_alpha.research.sandbox_models import (
    EquityPoint,
    ExecutionEvent,
    SandboxConfig,
    SandboxFill,
    SandboxResult,
    interval_minutes,
)
from grande_alpha.strategy.core import build_strategy


class SandboxReplayEngine:
    """Deterministic virtual execution with no broker or order-submission dependency."""

    def __init__(self, config: SandboxConfig) -> None:
        config.validate()
        self.config = config
        self.contract = contract_from_config(config)
        self._rng = random.Random(self.contract.random_seed)
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
        execution_model = SandboxExecutionModel(self.contract, self._rng, bar_minutes)
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
                cash, unsettled_cash, position, new_fills, new_events, complete = execution_model.transition(
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
                cash, unsettled_cash, position, new_fills, new_events, _ = execution_model.transition(
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
            cash, unsettled_cash, position, new_fills, new_events, _ = execution_model.transition(
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

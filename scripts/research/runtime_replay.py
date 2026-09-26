from __future__ import annotations

import math
import statistics
import uuid
from datetime import datetime
from typing import Any

from grande_alpha.domain.market_models import Bar, Signal
from grande_alpha.domain.policy import session_bounds, session_key, session_minutes
from grande_alpha.research.bar_replay import SandboxReplayEngine
from grande_alpha.research.historical import HistoricalBundle, ReplayFrame
from grande_alpha.research.sandbox_models import (
    EquityPoint,
    ExecutionEvent,
    RuntimeObservationReplayResult,
    SandboxConfig,
    SandboxFill,
    SandboxResult,
    interval_minutes,
)
from grande_alpha.strategy.core import build_strategy
from grande_alpha.strategy.shadow import QuoteSimulationExecutor


class RuntimeObservationReplayEngine:
    """Replay exact runtime observations with no broker dependency or write capability."""

    def __init__(self, config: SandboxConfig) -> None:
        config.validate()
        self.config = config

    def run(
        self,
        bundle: HistoricalBundle,
        *,
        signals: tuple[Signal, ...] | list[Signal] | None = None,
    ) -> RuntimeObservationReplayResult:
        if not bundle.frames or not all(
            frame.has_exact_runtime_observation for frame in bundle.frames
        ):
            raise ValueError(
                "Runtime observation replay requires causal QQQ/TQQQ/SQQQ quote frames; "
                "generic OHLCV is not runtime parity"
            )
        if signals is not None and len(signals) != len(bundle.frames):
            raise ValueError("Exact runtime replay needs one causal signal per observation frame")
        strategy = build_strategy(self.config.strategy_config())
        simulator = QuoteSimulationExecutor(self.config, bar_minutes=interval_minutes(bundle.interval))
        active_session: str | None = None
        active_stream: str | None = None
        last_frame: ReplayFrame | None = None
        bars: list[Bar] = []
        causal_timestamps: list[datetime] = []
        fills: list[Any] = []
        curve: list[EquityPoint] = []
        session_states: list[Any] = []
        completed_session_pnl = 0.0
        supplied_signals = signals
        emitted_signals: list[Signal] = []

        def session_reached_close(frame: ReplayFrame) -> bool:
            assert frame.causal_timestamp is not None
            _opened, closed = session_bounds(
                frame.causal_timestamp,
                self.config.market_hours,
            )
            return frame.causal_timestamp >= closed

        def finish_session(frame: ReplayFrame) -> None:
            nonlocal completed_session_pnl
            prior_fill_count = len(simulator.state.fills)
            if self.config.force_flat_at_end and session_reached_close(frame):
                simulator.stop(
                    frame.runtime_quotes(),
                    flatten_at=frame.causal_timestamp,
                    flatten_reason="SESSION-END VIRTUAL FLAT at regular-session close",
                )
            extra_fills = simulator.state.fills[prior_fill_count:]
            fills.extend(extra_fills)
            if curve:
                curve[-1] = EquityPoint(
                    curve[-1].timestamp,
                    self.config.initial_cash + completed_session_pnl + simulator.state.pnl,
                    completed_session_pnl + simulator.state.cash,
                    simulator.state.position.symbol if simulator.state.position else None,
                    simulator.state.unsettled_cash,
                )
            session_states.append(simulator.state)
            completed_session_pnl += simulator.state.pnl

        for index, frame in enumerate(bundle.frames):
            assert frame.causal_timestamp is not None
            current_session = session_key(frame.causal_timestamp, bundle.market_hours)
            if active_session is not None and current_session != active_session:
                assert last_frame is not None
                finish_session(last_frame)
                strategy = build_strategy(self.config.strategy_config())
                simulator = QuoteSimulationExecutor(
                    self.config,
                    bar_minutes=interval_minutes(bundle.interval),
                )
            elif active_stream is not None and frame.stream_id != active_stream:
                # A restart resets partial bars and indicators. Preserve the execution
                # ledger and risk state while rewarming only the forecasting strategy.
                strategy = build_strategy(self.config.strategy_config())
            active_session = current_session
            active_stream = frame.stream_id
            signal = (
                supplied_signals[index]
                if supplied_signals is not None
                else strategy.on_bar(frame.qqq)
            )
            fills.extend(
                simulator.on_causal_quote(
                    frame.causal_timestamp,
                    signal,
                    frame.runtime_quotes(),
                )
            )
            bars.append(frame.qqq)
            emitted_signals.append(signal)
            causal_timestamps.append(frame.causal_timestamp)
            curve.append(
                EquityPoint(
                    frame.causal_timestamp,
                    self.config.initial_cash + completed_session_pnl + simulator.state.pnl,
                    completed_session_pnl + simulator.state.cash,
                    simulator.state.position.symbol if simulator.state.position else None,
                    simulator.state.unsettled_cash,
                )
            )
            last_frame = frame
        assert last_frame is not None
        finish_session(last_frame)
        return RuntimeObservationReplayResult(
            tuple(bars),
            tuple(emitted_signals),
            tuple(causal_timestamps),
            tuple(fills),
            simulator.state,
            tuple(curve),
            tuple(session_states),
        )

    def run_result(
        self,
        bundle: HistoricalBundle,
        *,
        signals: tuple[Signal, ...] | list[Signal] | None = None,
    ) -> SandboxResult:
        """Return canonical evidence metrics from the exact causal quote engine."""

        replay = self.run(bundle, signals=signals)
        curve = list(replay.equity_curve)
        if not curve:
            raise ValueError("Exact runtime replay produced no equity observations")

        converted_fills = [
            SandboxFill(
                timestamp=fill.timestamp,
                symbol=fill.symbol,
                side=fill.side,
                quantity=fill.quantity,
                price=fill.price,
                commission=fill.commission,
                realized_pnl=fill.realized_pnl,
                reason=fill.reason,
                cash_after=fill.cash_after,
                requested_quantity=fill.requested_quantity,
                fill_fraction=fill.fill_fraction,
                execution_cost=fill.execution_cost,
                unsettled_cash_after=fill.unsettled_cash_after,
            )
            for fill in replay.fills
        ]
        execution_events = [
            ExecutionEvent(
                timestamp=fill.timestamp,
                symbol=fill.symbol,
                side=fill.side,
                status="filled" if fill.fill_fraction >= 0.999999 else "partially_filled",
                requested_quantity=fill.requested_quantity,
                filled_quantity=fill.quantity,
                reason=fill.reason,
            )
            for fill in replay.fills
        ]
        closed_pnl = [
            fill.realized_pnl
            for fill in converted_fills
            if fill.realized_pnl is not None
        ]
        wins = [value for value in closed_pnl if value > 0]
        losses = [value for value in closed_pnl if value < 0]
        gross_profit, gross_loss = sum(wins), abs(sum(losses))
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else math.inf
            if gross_profit
            else 0.0
        )
        returns = [
            curve[index].equity / curve[index - 1].equity - 1.0
            for index in range(1, len(curve))
            if curve[index - 1].equity > 0
        ]
        bar_minutes = interval_minutes(bundle.interval)
        annualization = math.sqrt(
            max(1.0, 252 * session_minutes(self.config.market_hours) / bar_minutes)
        )
        max_drawdown, max_drawdown_bars = SandboxReplayEngine._drawdown(curve)
        first, last = bundle.frames[0], bundle.frames[-1]
        state = replay.final_state
        ending_position = next(
            (
                session_state.position.symbol
                for session_state in replay.session_states
                if session_state.position is not None
            ),
            None,
        )
        final_equity = curve[-1].equity
        warnings = [
            "Historical replay is not evidence of future profitability",
            "Exact runtime-observation replay uses recorded venue quotes and has no broker write path",
        ]
        if not closed_pnl:
            warnings.insert(0, "No complete virtual round trips occurred with these settings")
        if ending_position:
            warnings.append(
                f"Replay ended holding {ending_position}; final P/L includes unrealized value"
            )
        if bundle.quality and bundle.quality.missing_intervals:
            warnings.append(
                f"Dataset has {bundle.quality.missing_intervals} missing intraday intervals"
            )
        if self.config.settlement_model == "cash_t1":
            warnings.append(
                "Cash-account model: sale proceeds become spendable at the next observed trading session"
            )
        return SandboxResult(
            run_id=str(uuid.uuid4()),
            source=bundle.source,
            start=bundle.start,
            end=bundle.end,
            initial_cash=state.starting_cash,
            final_equity=final_equity,
            net_pnl=final_equity - state.starting_cash,
            return_pct=(final_equity / state.starting_cash - 1.0) * 100.0,
            max_drawdown_pct=max_drawdown * 100.0,
            round_trips=len(closed_pnl),
            win_rate=(len(wins) / len(closed_pnl) * 100.0 if closed_pnl else 0.0),
            tqqqs_buy_hold_pct=(last.tqqq.close / first.tqqq.open - 1.0) * 100.0,
            sqqqs_buy_hold_pct=(last.sqqq.close / first.sqqq.open - 1.0) * 100.0,
            fills=converted_fills,
            equity_curve=curve,
            warnings=warnings,
            execution_events=execution_events,
            profit_factor=profit_factor,
            expectancy=statistics.fmean(closed_pnl) if closed_pnl else 0.0,
            average_win=statistics.fmean(wins) if wins else 0.0,
            average_loss=statistics.fmean(losses) if losses else 0.0,
            turnover=(
                sum(fill.quantity * fill.price for fill in converted_fills)
                / state.starting_cash
            ),
            exposure_pct=(
                sum(point.position_symbol is not None for point in curve) / len(curve) * 100.0
            ),
            max_drawdown_bars=max_drawdown_bars,
            sharpe=SandboxReplayEngine._risk_adjusted(
                returns,
                annualization,
                downside_only=False,
            ),
            sortino=SandboxReplayEngine._risk_adjusted(
                returns,
                annualization,
                downside_only=True,
            ),
            total_execution_cost=sum(fill.execution_cost for fill in converted_fills),
            ending_position=ending_position,
            daily_pnl=SandboxReplayEngine._daily_pnl(curve, self.config.market_hours),
            daily_returns=SandboxReplayEngine._daily_returns(
                curve,
                state.starting_cash,
                self.config.market_hours,
            ),
            final_unsettled_cash=state.unsettled_cash,
            runtime_observation_replay=True,
        )

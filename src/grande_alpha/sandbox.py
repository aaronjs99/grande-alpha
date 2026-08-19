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

from grande_alpha.candidate_execution import (
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
from grande_alpha.config import data_dir
from grande_alpha.execution import execution_profile
from grande_alpha.historical import HistoricalBundle, ReplayFrame
from grande_alpha.models import Bar, Signal
from grande_alpha.policy import (
    DecisionPolicy,
    PolicyConfig,
    PolicyPosition,
    session_bounds,
    session_key,
    session_minutes,
)
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
        contract_from_config(self)
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
    runtime_observation_replay: bool = False

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
            "runtime_observation_replay": self.runtime_observation_replay,
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class RuntimeObservationReplayResult:
    """Clock trace from the same causal quote path used by live shadow execution."""

    bars: tuple[Bar, ...]
    signals: tuple[Signal, ...]
    causal_timestamps: tuple[datetime, ...]
    fills: tuple[Any, ...]
    final_state: Any
    equity_curve: tuple[EquityPoint, ...] = ()
    session_states: tuple[Any, ...] = ()


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
        # Local import avoids a module cycle: live shadow consumes SandboxConfig but has no broker.
        from grande_alpha.shadow import LiveShadowEngine

        strategy = build_strategy(self.config.strategy_config())
        shadow = LiveShadowEngine(self.config, bar_minutes=interval_minutes(bundle.interval))
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
            prior_fill_count = len(shadow.state.fills)
            if self.config.force_flat_at_end and session_reached_close(frame):
                shadow.stop(
                    frame.runtime_quotes(),
                    flatten_at=frame.causal_timestamp,
                    flatten_reason="AUTO SHADOW DAILY FLAT at regular-session close",
                )
            extra_fills = shadow.state.fills[prior_fill_count:]
            fills.extend(extra_fills)
            if curve:
                curve[-1] = EquityPoint(
                    curve[-1].timestamp,
                    self.config.initial_cash + completed_session_pnl + shadow.state.pnl,
                    completed_session_pnl + shadow.state.cash,
                    shadow.state.position.symbol if shadow.state.position else None,
                    shadow.state.unsettled_cash,
                )
            session_states.append(shadow.state)
            completed_session_pnl += shadow.state.pnl

        for index, frame in enumerate(bundle.frames):
            assert frame.causal_timestamp is not None
            current_session = session_key(frame.causal_timestamp, bundle.market_hours)
            if active_session is not None and current_session != active_session:
                assert last_frame is not None
                finish_session(last_frame)
                strategy = build_strategy(self.config.strategy_config())
                shadow = LiveShadowEngine(
                    self.config,
                    bar_minutes=interval_minutes(bundle.interval),
                )
            elif active_stream is not None and frame.stream_id != active_stream:
                # A process restart discards the partial bar/indicator pipeline, but
                # durable live shadow restores its same-session cash, position, pending
                # transition, and risk state. Exact replay must preserve that execution
                # ledger while rewarming only the forecasting strategy.
                strategy = build_strategy(self.config.strategy_config())
            active_session = current_session
            active_stream = frame.stream_id
            signal = (
                supplied_signals[index]
                if supplied_signals is not None
                else strategy.on_bar(frame.qqq)
            )
            fills.extend(
                shadow.on_causal_quote(
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
                    self.config.initial_cash + completed_session_pnl + shadow.state.pnl,
                    completed_session_pnl + shadow.state.cash,
                    shadow.state.position.symbol if shadow.state.position else None,
                    shadow.state.unsettled_cash,
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
            shadow.state,
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


class SandboxReplayRunner:
    """Lock one evidence run to either exact-runtime or generic replay semantics."""

    def __init__(self, *, exact_runtime_observation: bool = False) -> None:
        self.exact_runtime_observation = exact_runtime_observation

    @classmethod
    def for_evidence_bundle(cls, bundle: HistoricalBundle) -> SandboxReplayRunner:
        return cls(exact_runtime_observation=bundle.runtime_observation_parity_eligible)

    def run(
        self,
        bundle: HistoricalBundle,
        config: SandboxConfig,
        *,
        signals: tuple[Signal, ...] | list[Signal] | None = None,
    ) -> SandboxResult:
        if self.exact_runtime_observation:
            # RuntimeObservationReplayEngine rejects even one non-exact child frame. There is
            # intentionally no catch-and-fallback path to generic OHLCV execution.
            return RuntimeObservationReplayEngine(config).run_result(bundle, signals=signals)
        if signals is not None:
            raise ValueError("Custom causal signals are supported only by exact runtime replay")
        return SandboxReplayEngine(config).run(bundle)


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

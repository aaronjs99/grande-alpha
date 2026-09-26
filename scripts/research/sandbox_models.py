from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from grande_alpha.domain.execution_profile import execution_profile
from grande_alpha.domain.market_models import Bar, Signal
from grande_alpha.execution.candidate_execution import contract_from_config
from grande_alpha.strategy.core import StrategyConfig

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
    """Clock trace from the causal quote path used by virtual runtime execution."""

    bars: tuple[Bar, ...]
    signals: tuple[Signal, ...]
    causal_timestamps: tuple[datetime, ...]
    fills: tuple[Any, ...]
    final_state: Any
    equity_curve: tuple[EquityPoint, ...] = ()
    session_states: tuple[Any, ...] = ()

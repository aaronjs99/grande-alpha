from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from grande_alpha.config import data_dir
from grande_alpha.historical import HistoricalBundle, ReplayFrame
from grande_alpha.models import Regime
from grande_alpha.strategy import MomentumStrategy, StrategyConfig


@dataclass
class SandboxConfig:
    lookback_days: int = 7
    initial_cash: float = 50.0
    order_notional: float = 25.0
    slippage_bps: float = 2.0
    commission_per_order: float = 0.0
    warmup_bars: int = 24
    fast_ema: int = 8
    slow_ema: int = 21
    trend_threshold_bps: float = 4.0
    momentum_bars: int = 3
    hard_stop_pct: float = 0.008
    take_profit_pct: float = 0.015
    max_hold_minutes: int = 45
    max_entries_per_day: int = 6
    no_trade_open_minutes: int = 5
    no_trade_close_minutes: int = 10

    def validate(self) -> None:
        if not 1 <= self.lookback_days <= 7:
            raise ValueError("Lookback must be between 1 and 7 calendar days")
        if self.initial_cash <= 0 or self.order_notional <= 0:
            raise ValueError("Starting cash and order notional must be positive")
        if self.order_notional > self.initial_cash:
            raise ValueError("Order notional cannot exceed starting virtual cash")
        if self.fast_ema < 1 or self.slow_ema < 2 or self.fast_ema >= self.slow_ema:
            raise ValueError("Fast EMA must be positive and smaller than slow EMA")
        if self.momentum_bars < 1 or self.warmup_bars < self.slow_ema + 2:
            raise ValueError("Warm-up must be at least slow EMA + 2; momentum must be positive")
        if self.trend_threshold_bps <= 0:
            raise ValueError("Trend threshold must be positive")
        if self.slippage_bps < 0 or self.commission_per_order < 0:
            raise ValueError("Slippage and commission cannot be negative")
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

    def metrics(self) -> dict[str, Any]:
        return {
            "initial_cash": self.initial_cash,
            "final_equity": self.final_equity,
            "net_pnl": self.net_pnl,
            "return_pct": self.return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "round_trips": self.round_trips,
            "win_rate": self.win_rate,
            "tqqqs_buy_hold_pct": self.tqqqs_buy_hold_pct,
            "sqqqs_buy_hold_pct": self.sqqqs_buy_hold_pct,
            "warnings": self.warnings,
        }


@dataclass
class _VirtualPosition:
    symbol: str
    quantity: float
    entry_price: float
    cost_total: float
    entry_index: int


class SandboxReplayEngine:
    """Pure virtual execution engine with no broker object or order-submission path."""

    def __init__(self, config: SandboxConfig) -> None:
        config.validate()
        self.config = config

    def run(self, bundle: HistoricalBundle) -> SandboxResult:
        if len(bundle.frames) < self.config.warmup_bars + 3:
            raise ValueError("The dataset is too short for the selected warm-up")
        strategy = MomentumStrategy(
            StrategyConfig(
                warmup_bars=self.config.warmup_bars,
                fast_ema=self.config.fast_ema,
                slow_ema=self.config.slow_ema,
                trend_threshold_bps=self.config.trend_threshold_bps,
                momentum_bars=self.config.momentum_bars,
            )
        )
        cash = self.config.initial_cash
        position: _VirtualPosition | None = None
        pending_target: str | None = None
        pending_change = False
        pending_reason = ""
        entries_by_day: dict[str, int] = {}
        fills: list[SandboxFill] = []
        closed_pnl: list[float] = []
        curve: list[EquityPoint] = []
        warnings: list[str] = []

        for index, frame in enumerate(bundle.frames):
            if pending_change:
                cash, position, transition_fills = self._transition(
                    frame,
                    index,
                    cash,
                    position,
                    pending_target,
                    pending_reason,
                    entries_by_day,
                )
                fills.extend(transition_fills)
                closed_pnl.extend(
                    fill.realized_pnl for fill in transition_fills if fill.realized_pnl is not None
                )
                pending_change = False

            signal = strategy.on_bar(frame.qqq)
            desired = {
                Regime.BULLISH: "TQQQS",
                Regime.BEARISH: "SQQQS",
                Regime.FLAT: None,
            }[signal.regime]
            reason = signal.reason
            eastern = frame.start.astimezone(ZoneInfo("America/New_York"))
            market_minute = (eastern.hour * 60 + eastern.minute) - (9 * 60 + 30)
            if market_minute < self.config.no_trade_open_minutes:
                desired, reason = None, "Configured opening no-trade window"
            elif market_minute >= 390 - self.config.no_trade_close_minutes:
                desired, reason = None, "Configured closing no-trade window"
            if position is not None:
                mark = frame.bar_for_alias(position.symbol).close
                return_pct = (mark - position.entry_price) / position.entry_price
                held_minutes = index - position.entry_index
                if return_pct <= -self.config.hard_stop_pct:
                    desired, reason = None, f"Sandbox stop {return_pct:+.2%}"
                elif return_pct >= self.config.take_profit_pct:
                    desired, reason = None, f"Sandbox take-profit {return_pct:+.2%}"
                elif held_minutes >= self.config.max_hold_minutes:
                    desired, reason = None, f"Sandbox max hold {held_minutes} minutes"
            current = position.symbol if position else None
            if desired != current:
                pending_target = desired
                pending_reason = reason
                pending_change = True

            equity = cash
            if position is not None:
                equity += position.quantity * frame.bar_for_alias(position.symbol).close
            curve.append(EquityPoint(frame.start, equity, cash, position.symbol if position else None))

        if position is not None:
            final_frame = bundle.frames[-1]
            cash, position, final_fills = self._transition(
                final_frame,
                len(bundle.frames),
                cash,
                position,
                None,
                "End of sandbox replay — forced virtual flatten",
                entries_by_day,
                use_close=True,
            )
            fills.extend(final_fills)
            closed_pnl.extend(fill.realized_pnl for fill in final_fills if fill.realized_pnl is not None)
            curve[-1] = EquityPoint(final_frame.start, cash, cash, None)

        final_equity = cash
        peak = self.config.initial_cash
        max_drawdown = 0.0
        for point in curve:
            peak = max(peak, point.equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - point.equity) / peak)
        wins = sum(value > 0 for value in closed_pnl)
        win_rate = wins / len(closed_pnl) if closed_pnl else 0.0
        first, last = bundle.frames[0], bundle.frames[-1]
        tqqq_hold = (last.tqqq.close / first.tqqq.open - 1.0) * 100.0
        sqqq_hold = (last.sqqq.close / first.sqqq.open - 1.0) * 100.0
        if not closed_pnl:
            warnings.append("No complete virtual round trips occurred with these settings")
        warnings.append("Historical replay is not evidence of future profitability")
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
            tqqqs_buy_hold_pct=tqqq_hold,
            sqqqs_buy_hold_pct=sqqq_hold,
            fills=fills,
            equity_curve=curve,
            warnings=warnings,
        )

    def _transition(
        self,
        frame: ReplayFrame,
        index: int,
        cash: float,
        position: _VirtualPosition | None,
        target: str | None,
        reason: str,
        entries_by_day: dict[str, int],
        use_close: bool = False,
    ) -> tuple[float, _VirtualPosition | None, list[SandboxFill]]:
        fills: list[SandboxFill] = []
        if position is not None and position.symbol != target:
            raw = (
                frame.bar_for_alias(position.symbol).close
                if use_close
                else frame.bar_for_alias(position.symbol).open
            )
            price = self._slipped(raw, "sell")
            proceeds = position.quantity * price - self.config.commission_per_order
            cash += proceeds
            realized = proceeds - position.cost_total
            fills.append(
                SandboxFill(
                    frame.start,
                    position.symbol,
                    "sell",
                    position.quantity,
                    price,
                    self.config.commission_per_order,
                    realized,
                    reason,
                    cash,
                )
            )
            position = None
        if target is not None and position is None:
            day = frame.start.date().isoformat()
            if entries_by_day.get(day, 0) >= self.config.max_entries_per_day:
                return cash, None, fills
            raw = frame.bar_for_alias(target).close if use_close else frame.bar_for_alias(target).open
            price = self._slipped(raw, "buy")
            budget = min(self.config.order_notional, cash - self.config.commission_per_order)
            if budget > 0 and math.isfinite(price) and price > 0:
                quantity = budget / price
                cost = quantity * price + self.config.commission_per_order
                cash -= cost
                position = _VirtualPosition(target, quantity, price, cost, index)
                entries_by_day[day] = entries_by_day.get(day, 0) + 1
                fills.append(
                    SandboxFill(
                        frame.start,
                        target,
                        "buy",
                        quantity,
                        price,
                        self.config.commission_per_order,
                        None,
                        reason,
                        cash,
                    )
                )
        return cash, position, fills

    def _slipped(self, price: float, side: str) -> float:
        direction = 1.0 if side == "buy" else -1.0
        return price * (1.0 + direction * self.config.slippage_bps / 10_000.0)


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

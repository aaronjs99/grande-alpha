from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from grande_alpha.models import Regime, Signal

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PolicyConfig:
    bullish_symbol: str = "TQQQ"
    bearish_symbol: str = "SQQQ"
    hard_stop_pct: float = 0.008
    take_profit_pct: float = 0.015
    max_hold_minutes: int = 45
    no_trade_open_minutes: int = 5
    no_trade_close_minutes: int = 10


@dataclass(frozen=True)
class PolicyPosition:
    symbol: str
    entry_price: float | None
    mark_price: float | None
    held_minutes: int | None = None


@dataclass(frozen=True)
class PolicyDecision:
    target_symbol: str | None
    reason: str
    signal_regime: Regime


def regular_session_allowed(
    timestamp: datetime,
    no_trade_open_minutes: int,
    no_trade_close_minutes: int,
) -> bool:
    local = timestamp.astimezone(EASTERN)
    if local.weekday() >= 5:
        return False
    opened = datetime.combine(local.date(), time(9, 30), tzinfo=EASTERN)
    closed = datetime.combine(local.date(), time(16, 0), tzinfo=EASTERN)
    start = opened.timestamp() + no_trade_open_minutes * 60
    end = closed.timestamp() - no_trade_close_minutes * 60
    return start <= local.timestamp() <= end


class DecisionPolicy:
    """Pure strategy-to-position policy shared by live, replay, and shadow execution."""

    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def decide(
        self,
        signal: Signal,
        timestamp: datetime,
        position: PolicyPosition | None = None,
    ) -> PolicyDecision:
        target = {
            Regime.BULLISH: self.config.bullish_symbol,
            Regime.BEARISH: self.config.bearish_symbol,
            Regime.FLAT: None,
        }[signal.regime]
        reason = signal.reason
        local = timestamp.astimezone(EASTERN)
        close_cutoff = datetime.combine(local.date(), time(16, 0), tzinfo=EASTERN).timestamp()
        if local.timestamp() >= close_cutoff - self.config.no_trade_close_minutes * 60:
            target, reason = None, "Scheduled regular-session flatten window"
        if position is not None:
            if (
                position.entry_price is not None
                and position.entry_price > 0
                and position.mark_price is not None
            ):
                change = position.mark_price / position.entry_price - 1.0
                if change <= -self.config.hard_stop_pct:
                    target, reason = None, f"Hard stop reached at {change:.2%}"
                elif change >= self.config.take_profit_pct:
                    target, reason = None, f"Take-profit reached at {change:.2%}"
            if position.held_minutes is not None and position.held_minutes >= self.config.max_hold_minutes:
                target, reason = None, f"Maximum hold reached at {position.held_minutes} minutes"
        return PolicyDecision(target, reason, signal.regime)

    def trading_window_allowed(self, timestamp: datetime) -> bool:
        return regular_session_allowed(
            timestamp,
            self.config.no_trade_open_minutes,
            self.config.no_trade_close_minutes,
        )

    def exit_window_allowed(self, timestamp: datetime) -> bool:
        return regular_session_allowed(timestamp, 0, 0)

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from grande_alpha.market_calendar import REGULAR_OPEN, regular_session_times
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
    market_hours: str = "regular_hours"


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


def trading_date(timestamp: datetime, market_hours: str = "regular_hours") -> date:
    local = timestamp.astimezone(EASTERN)
    if market_hours == "all_day_hours" and local.time() >= time(20, 0):
        return local.date() + timedelta(days=1)
    return local.date()


def session_bounds(timestamp: datetime, market_hours: str) -> tuple[datetime, datetime]:
    trade_date = trading_date(timestamp, market_hours)
    if regular_session_times(trade_date) is None:
        # TQQQ/SQQQ/QQQ have no equity session on weekends or scheduled full-day
        # exchange holidays, including for extended and overnight route labels.
        closed = datetime.combine(trade_date, REGULAR_OPEN, tzinfo=EASTERN)
        return closed, closed
    if market_hours == "regular_hours":
        session = regular_session_times(trade_date)
        assert session is not None
        opened_at, closed_at = session
        return (
            datetime.combine(trade_date, opened_at, tzinfo=EASTERN),
            datetime.combine(trade_date, closed_at, tzinfo=EASTERN),
        )
    if market_hours == "extended_hours":
        return (
            datetime.combine(trade_date, time(7, 0), tzinfo=EASTERN),
            datetime.combine(trade_date, time(20, 0), tzinfo=EASTERN),
        )
    if market_hours == "all_day_hours":
        closed = datetime.combine(trade_date, time(20, 0), tzinfo=EASTERN)
        return closed - timedelta(days=1), closed
    raise ValueError(f"Unsupported trading session: {market_hours}")


def market_session_allowed(
    timestamp: datetime,
    no_trade_open_minutes: int,
    no_trade_close_minutes: int,
    market_hours: str = "regular_hours",
) -> bool:
    local = timestamp.astimezone(EASTERN)
    trade_date = trading_date(timestamp, market_hours)
    if regular_session_times(trade_date) is None:
        return False
    opened, closed = session_bounds(timestamp, market_hours)
    start = opened.timestamp() + no_trade_open_minutes * 60
    end = closed.timestamp() - no_trade_close_minutes * 60
    return start <= local.timestamp() <= end


def regular_session_allowed(
    timestamp: datetime,
    no_trade_open_minutes: int,
    no_trade_close_minutes: int,
) -> bool:
    return market_session_allowed(
        timestamp,
        no_trade_open_minutes,
        no_trade_close_minutes,
        "regular_hours",
    )


def session_key(timestamp: datetime, market_hours: str = "regular_hours") -> str:
    return trading_date(timestamp, market_hours).isoformat()


def session_minutes(market_hours: str) -> int:
    return {"regular_hours": 390, "extended_hours": 780, "all_day_hours": 1440}[market_hours]


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
        _, closed = session_bounds(timestamp, self.config.market_hours)
        if local.timestamp() >= closed.timestamp() - self.config.no_trade_close_minutes * 60:
            target, reason = None, "Scheduled trading-session flatten window"
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
        return market_session_allowed(
            timestamp,
            self.config.no_trade_open_minutes,
            self.config.no_trade_close_minutes,
            self.config.market_hours,
        )

    def exit_window_allowed(self, timestamp: datetime) -> bool:
        return market_session_allowed(timestamp, 0, 0, self.config.market_hours)

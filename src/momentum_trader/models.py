from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


class Regime(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    FLAT = "flat"


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    last: float
    timestamp: datetime

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def spread_bps(self) -> float:
        if self.bid <= 0 or self.ask <= 0 or self.mid <= 0:
            return float("inf")
        return (self.ask - self.bid) / self.mid * 10_000

    def age_seconds(self, now: datetime | None = None) -> float:
        reference = now or utc_now()
        return max(0.0, (reference - self.timestamp).total_seconds())


@dataclass(frozen=True)
class Bar:
    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    samples: int


@dataclass(frozen=True)
class Signal:
    regime: Regime
    confidence: float
    reason: str
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class Account:
    account_number: str
    nickname: str
    account_type: str
    agentic_allowed: bool
    state: str

    @property
    def masked(self) -> str:
        return f"••••{self.account_number[-4:]}"


@dataclass(frozen=True)
class Portfolio:
    total_value: float
    buying_power: float
    cash: float
    currency: str = "USD"


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    sellable_quantity: float
    average_price: float | None = None


@dataclass(frozen=True)
class BrokerOrder:
    order_id: str
    symbol: str
    side: str
    state: str
    quantity: float | None
    dollar_amount: float | None
    average_price: float | None
    created_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderIntent:
    ref_id: str
    symbol: str
    side: str
    reason: str
    order_type: str = "market"
    dollar_amount: float | None = None
    quantity: float | None = None
    limit_price: float | None = None
    market_hours: str = "regular_hours"
    time_in_force: str = "gfd"
    created_at: datetime = field(default_factory=utc_now)

    @property
    def estimated_notional(self) -> float:
        return float(self.dollar_amount or 0.0)

    def broker_arguments(self, account_number: str) -> dict[str, str]:
        args = {
            "account_number": account_number,
            "symbol": self.symbol,
            "side": self.side,
            "type": self.order_type,
            "market_hours": self.market_hours,
            "time_in_force": self.time_in_force,
        }
        if self.dollar_amount is not None:
            args["dollar_amount"] = f"{self.dollar_amount:.2f}"
        if self.quantity is not None:
            args["quantity"] = f"{self.quantity:.6f}".rstrip("0").rstrip(".")
        if self.limit_price is not None:
            args["limit_price"] = f"{self.limit_price:.2f}"
        return args

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result


@dataclass(frozen=True)
class OrderReview:
    intent: OrderIntent
    market_data_disclosure: str
    checks: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class LiveGrant:
    account_number: str
    starts_at: datetime
    expires_at: datetime
    max_order_notional: float
    max_total_exposure: float
    max_daily_loss: float
    max_trades: int
    max_orders_per_minute: int
    max_spread_bps: float
    max_quote_age_seconds: float

    def active(self, now: datetime | None = None) -> bool:
        reference = now or utc_now()
        return self.starts_at <= reference < self.expires_at


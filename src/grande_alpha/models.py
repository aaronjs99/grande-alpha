from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from numbers import Real
from typing import Any

from grande_alpha.execution import ExecutionProfile, execution_profile


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

    def validate(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, Real)
            for value in (self.bid, self.ask, self.last)
        ):
            raise ValueError("Quote prices must be numeric")
        try:
            prices = (float(self.bid), float(self.ask), float(self.last))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Quote prices must be numeric") from exc
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise ValueError("Quote prices must be finite and positive")
        if prices[1] < prices[0]:
            raise ValueError("Quote ask cannot be below bid")
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError("Quote timestamp must be timezone-aware")

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
    volume: float = 0.0


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

    def validate(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, Real)
            for value in (self.total_value, self.buying_power, self.cash)
        ):
            raise ValueError("Portfolio values must be numeric")
        try:
            values = (float(self.total_value), float(self.buying_power), float(self.cash))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Portfolio values must be numeric") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Portfolio values must be finite")
        if values[0] < 0 or values[1] < 0:
            raise ValueError("Portfolio value and buying power cannot be negative")


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    sellable_quantity: float
    average_price: float | None = None


@dataclass(frozen=True)
class EquityTradability:
    symbol: str
    tradeable: bool
    all_day_tradeable: bool
    extended_hours_fractional_tradeable: bool


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
        if self.dollar_amount is not None:
            return float(self.dollar_amount)
        if self.quantity is not None and self.limit_price is not None:
            return float(self.quantity * self.limit_price)
        return 0.0

    def validate(self) -> None:
        profile = execution_profile(self)
        if self.side not in {"buy", "sell"}:
            raise ValueError("Order side must be buy or sell")
        if self.symbol not in {"TQQQ", "SQQQ"}:
            raise ValueError("Automatic equity orders are restricted to TQQQ and SQQQ")
        has_dollars = self.dollar_amount is not None
        has_quantity = self.quantity is not None
        if has_dollars == has_quantity:
            raise ValueError("Specify exactly one of dollar amount or quantity")
        for label, value in (
            ("Dollar amount", self.dollar_amount),
            ("Quantity", self.quantity),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{label} must be numeric")
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{label} must be numeric") from exc
            if not math.isfinite(numeric) or numeric <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.limit_price is not None:
            if isinstance(self.limit_price, bool) or not isinstance(self.limit_price, Real):
                raise ValueError("Limit price must be numeric")
            try:
                limit_price = float(self.limit_price)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Limit price must be numeric") from exc
            if not math.isfinite(limit_price):
                raise ValueError("Limit price must be finite")
        if profile.order_type == "market":
            if self.limit_price is not None:
                raise ValueError("Market orders cannot include a limit price")
        else:
            if self.dollar_amount is not None:
                raise ValueError("The Trading MCP accepts limit orders by share quantity only")
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError("Limit orders require a positive limit price")
            if self.quantity is None or abs(self.quantity - round(self.quantity)) > 1e-9:
                raise ValueError("The Trading MCP requires whole-share automatic limit orders")

    def broker_arguments(self, account_number: str) -> dict[str, str]:
        self.validate()
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
    market_hours: str = "regular_hours"
    order_type: str = "market"
    time_in_force: str = "gfd"
    limit_offset_bps: float = 10.0

    def validate(self) -> None:
        if not self.account_number:
            raise ValueError("Live grant must target an account")
        for label, value in (("start", self.starts_at), ("expiry", self.expires_at)):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"Live grant {label} must be timezone-aware")
        if self.expires_at <= self.starts_at:
            raise ValueError("Live grant expiry must be after its start")
        positive_limits = {
            "maximum order notional": self.max_order_notional,
            "maximum exposure": self.max_total_exposure,
            "maximum daily loss": self.max_daily_loss,
            "maximum spread": self.max_spread_bps,
            "maximum quote age": self.max_quote_age_seconds,
        }
        for label, value in positive_limits.items():
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"Live grant {label} must be numeric")
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"Live grant {label} must be numeric") from exc
            if not math.isfinite(numeric) or numeric <= 0:
                raise ValueError(f"Live grant {label} must be finite and positive")
        for label, value in (
            ("maximum trades", self.max_trades),
            ("maximum orders per minute", self.max_orders_per_minute),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Live grant {label} must be a positive integer")
        self.execution.validate()

    def active(self, now: datetime | None = None) -> bool:
        try:
            self.validate()
        except ValueError:
            return False
        reference = now or utc_now()
        if not isinstance(reference, datetime) or reference.tzinfo is None or reference.utcoffset() is None:
            return False
        return self.starts_at <= reference < self.expires_at

    @property
    def execution(self) -> ExecutionProfile:
        return execution_profile(self)

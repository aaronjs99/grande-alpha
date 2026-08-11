from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo

from grande_alpha.execution import ExecutionProfile, execution_profile

AUTHORITY_TIMEZONE = ZoneInfo("America/New_York")


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
    max_daily_notional: float = 50.0
    allowed_symbols: tuple[str, ...] = ("TQQQ", "SQQQ")
    strategy_fingerprint: str = ""
    authority_id: str = field(default_factory=lambda: str(uuid.uuid4()))
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
        starts_eastern = self.starts_at.astimezone(AUTHORITY_TIMEZONE).date()
        expires_eastern = self.expires_at.astimezone(AUTHORITY_TIMEZONE).date()
        if expires_eastern != starts_eastern:
            raise ValueError("Live grant must expire on the same Eastern calendar day")
        if not self.authority_id:
            raise ValueError("Live grant must have an authority id")
        if not isinstance(self.allowed_symbols, tuple) or not self.allowed_symbols:
            raise ValueError("Live grant must bind an exact nonempty ticker tuple")
        if len(set(self.allowed_symbols)) != len(self.allowed_symbols) or any(
            symbol not in {"TQQQ", "SQQQ"} for symbol in self.allowed_symbols
        ):
            raise ValueError("Live grant tickers must be unique and restricted to TQQQ/SQQQ")
        if (
            not isinstance(self.strategy_fingerprint, str)
            or len(self.strategy_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in self.strategy_fingerprint)
        ):
            raise ValueError("Live grant must bind an exact lowercase SHA-256 strategy fingerprint")
        positive_limits = {
            "maximum order notional": self.max_order_notional,
            "maximum daily notional": self.max_daily_notional,
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

    def scope_payload(self) -> dict[str, Any]:
        """Canonical money-moving authority scope; confirmation text is never retained."""
        return {
            "authority_id": self.authority_id,
            "account_number": self.account_number,
            "starts_at": self.starts_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "allowed_symbols": list(self.allowed_symbols),
            "strategy_fingerprint": self.strategy_fingerprint,
            "market_hours": self.market_hours,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "limit_offset_bps": self.limit_offset_bps,
            "max_order_notional": self.max_order_notional,
            "max_daily_notional": self.max_daily_notional,
            "max_total_exposure": self.max_total_exposure,
            "max_daily_loss": self.max_daily_loss,
            "max_trades": self.max_trades,
            "max_orders_per_minute": self.max_orders_per_minute,
            "max_spread_bps": self.max_spread_bps,
            "max_quote_age_seconds": self.max_quote_age_seconds,
        }

    @property
    def scope_digest(self) -> str:
        encoded = json.dumps(self.scope_payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

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


@dataclass(frozen=True)
class AuthorityActionReceipt:
    """Immutable, hash-chainable record emitted for each authority state/action decision."""

    receipt_id: str
    authority_id: str
    occurred_at: datetime
    action: str
    scope_digest: str
    previous_digest: str = ""
    ref_id: str = ""
    symbol: str = ""
    side: str = ""
    notional: float = 0.0
    reason: str = ""

    def validate(self) -> None:
        if not self.receipt_id or not self.authority_id:
            raise ValueError("Authority receipt ids cannot be empty")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("Authority receipt timestamp must be timezone-aware")
        if not self.action or len(self.scope_digest) != 64:
            raise ValueError("Authority receipt action and scope digest are required")
        if self.previous_digest and len(self.previous_digest) != 64:
            raise ValueError("Authority receipt previous digest must be empty or SHA-256")
        if not math.isfinite(float(self.notional)) or self.notional < 0:
            raise ValueError("Authority receipt notional must be finite and nonnegative")

    def payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "receipt_id": self.receipt_id,
            "authority_id": self.authority_id,
            "occurred_at": self.occurred_at.isoformat(),
            "action": self.action,
            "scope_digest": self.scope_digest,
            "previous_digest": self.previous_digest,
            "ref_id": self.ref_id,
            "symbol": self.symbol,
            "side": self.side,
            "notional": self.notional,
            "reason": self.reason,
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {**self.payload(), "receipt_digest": self.digest}

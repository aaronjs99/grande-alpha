"""Crypto-specific wire and execution economics; never reuse equity share/fee semantics."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext

from grande_alpha.models import Quote


def decimal_amount(value, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{field} must be an exact decimal")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be an exact decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        raise ValueError(f"{field} must be finite and {'positive' if positive else 'nonnegative'}")
    if parsed.as_tuple().exponent < -18 or parsed.adjusted() > 18:
        raise ValueError(f"{field} exceeds supported decimal precision")
    return parsed


@dataclass(frozen=True)
class CryptoPairRules:
    pair_id: str
    symbol: str
    tradability: str
    display_only: bool
    halted: bool
    min_quantity: Decimal
    max_quantity: Decimal
    quantity_increment: Decimal
    price_increment: Decimal
    min_notional: Decimal | None
    market_orders_only: bool
    account_type_overrides: tuple[tuple[str, str], ...] = ()

    def restriction(self, side: str, account_type: str = "") -> str:
        status = dict(self.account_type_overrides).get(account_type, self.tradability)
        if self.display_only:
            return "Pair is display-only"
        if self.halted:
            return "Pair has a reported halt; regional eligibility is unresolved"
        allowed = {"tradable", "buy_only" if side == "buy" else "sell_only"}
        if status not in allowed:
            return f"Pair does not allow {side} orders for this account type ({status})"
        return ""

    def validate_intent(self, intent: CryptoOrderIntent, account_type: str) -> None:
        intent.validate()
        if intent.symbol != self.symbol or intent.pair_id != self.pair_id:
            raise ValueError("Crypto intent does not match the provider pair")
        if reason := self.restriction(intent.side, account_type):
            raise ValueError(reason)
        if self.market_orders_only and intent.order_type != "market":
            raise ValueError("This crypto pair accepts market orders only")
        with localcontext() as context:
            context.prec = 60
            if intent.quantity is not None:
                quantity = decimal_amount(intent.quantity, "quantity", positive=True)
                if not self.min_quantity <= quantity <= self.max_quantity:
                    raise ValueError("Crypto quantity is outside the pair's limits")
                if quantity % self.quantity_increment != 0:
                    raise ValueError("Crypto quantity is off the provider increment")
            for price in (intent.limit_price, intent.stop_price):
                if price is not None and decimal_amount(price, "price", positive=True) % self.price_increment != 0:
                    raise ValueError("Crypto price is off the provider increment")
        if intent.dollar_amount is not None and self.min_notional is not None:
            if decimal_amount(intent.dollar_amount, "dollar amount", positive=True) < self.min_notional:
                raise ValueError("Crypto notional is below the pair's minimum")


@dataclass(frozen=True)
class CryptoQuote(Quote):
    routing: str = ""

    def validate(self) -> None:
        # Crypto independently omits either book clock. Keep the clocks we do know.
        Quote.validate(replace(self, bid_timestamp=None, ask_timestamp=None))
        for stamp in (self.bid_timestamp, self.ask_timestamp):
            if stamp is not None and (not isinstance(stamp, datetime) or stamp.utcoffset() is None):
                raise ValueError("Crypto book timestamp must be timezone-aware")

    def age_seconds(self, now: datetime | None = None) -> float:
        from grande_alpha.models import utc_now

        oldest = min(t for t in (self.timestamp, self.bid_timestamp, self.ask_timestamp) if t is not None)
        return max(0.0, ((now or utc_now()) - oldest).total_seconds())


@dataclass(frozen=True)
class CryptoOrderIntent:
    symbol: str
    pair_id: str
    side: str
    quantity: Decimal | None = None
    dollar_amount: Decimal | None = None
    order_type: str = "market"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "gtc"
    ref_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Z0-9]{1,16}-USD", self.symbol) or not self.pair_id.strip():
            raise ValueError("Crypto intent requires a canonical USD pair and provider identity")
        if self.side not in {"buy", "sell"} or self.order_type not in {"market", "limit", "stop_loss", "stop_limit"}:
            raise ValueError("Unsupported crypto side or order type")
        if (self.quantity is None) == (self.dollar_amount is None):
            raise ValueError("Specify exactly one crypto quantity or dollar amount")
        for name in ("quantity", "dollar_amount", "limit_price", "stop_price"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, Decimal):
                    raise ValueError(f"{name} must be a Decimal")
                decimal_amount(value, name, positive=True)
        if (self.limit_price is not None) != (self.order_type in {"limit", "stop_limit"}):
            raise ValueError("Crypto limit price does not match order type")
        if (self.stop_price is not None) != (self.order_type in {"stop_loss", "stop_limit"}):
            raise ValueError("Crypto stop price does not match order type")
        allowed = {"gtc"} if self.order_type in {"market", "limit"} else {"gtc", "gfd", "gfw", "gfm"}
        if self.time_in_force not in allowed:
            raise ValueError("Unsupported crypto time in force; IOC is never supported")
        try:
            if str(uuid.UUID(self.ref_id)) != self.ref_id:
                raise ValueError
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("Crypto intent needs one canonical UUID reference") from exc

    def arguments(self, rhs_account_number: str, *, placement: bool = False) -> dict:
        self.validate()
        if not rhs_account_number or not rhs_account_number.isascii() or not rhs_account_number.isdigit():
            raise ValueError("Crypto calls require the numeric RHS account identifier")
        result = {"rhs_account_number": rhs_account_number, "symbol": self.symbol,
                  "side": self.side, "type": self.order_type, "time_in_force": self.time_in_force}
        for name in ("quantity", "dollar_amount", "limit_price", "stop_price"):
            value = getattr(self, name)
            if value is not None:
                result[name] = format(decimal_amount(value, name, positive=True), "f")
        if placement:
            result["ref_id"] = self.ref_id
        return result


@dataclass(frozen=True)
class CryptoPosition:
    account_id: str
    symbol: str
    pair_id: str
    quantity: Decimal
    sellable_quantity: Decimal
    held_for_sell: Decimal
    # Only direct buys have a reported basis; missing transfer/reward basis stays unknown.
    direct_quantity: Decimal
    direct_cost_basis: Decimal


@dataclass(frozen=True)
class CryptoExecution:
    execution_id: str
    quantity: Decimal
    price: Decimal
    effective_price: Decimal
    notional: Decimal
    timestamp: datetime


@dataclass(frozen=True)
class CryptoOrder:
    order_id: str
    ref_id: str
    account_id: str
    pair_id: str
    side: str
    order_type: str
    time_in_force: str
    state: str
    quantity: Decimal | None
    cumulative_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    created_at: datetime
    updated_at: datetime
    speculative: bool | None
    routing: str
    executions: tuple[CryptoExecution, ...]
    net_estimated_notional: Decimal | None
    net_executed_notional: Decimal | None

    @property
    def terminal(self) -> bool:
        return self.state in {"filled", "canceled", "cancelled", "rejected", "failed", "voided"}

    @property
    def execution_complete(self) -> bool:
        if self.state == "filled" and (self.quantity is None or self.quantity <= 0 or self.cumulative_quantity != self.quantity):
            return False
        with localcontext() as context:
            context.prec = 60
            return sum((e.quantity for e in self.executions), Decimal(0)) == self.cumulative_quantity and (
                self.cumulative_quantity == 0 or self.net_executed_notional is not None
            )


@dataclass(frozen=True)
class CryptoReview:
    intent: CryptoOrderIntent
    account_number: str
    rhs_account_number: str
    rhc_account_number: str
    order: CryptoOrder
    estimated_fee: Decimal
    reviewed_at: datetime

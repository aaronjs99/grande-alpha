"""Stock-capable ticket/risk primitives; not connected to the live strategy driver."""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from grande_alpha.data.earnings import _number
from grande_alpha.domain.account_models import Account, Portfolio, Position
from grande_alpha.domain.loss_recovery import validate_recovery
from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.domain.market_models import Quote
from grande_alpha.domain.order_models import OrderIntent
from grande_alpha.domain.policy import EASTERN


def symbol_valid(symbol) -> bool:
    return isinstance(symbol, str) and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol) is not None


@dataclass(frozen=True)
class EquityOrderIntent(OrderIntent):
    def _validate_symbol(self) -> None:
        if not symbol_valid(self.symbol):
            raise ValueError("Invalid equity ticker")

    def validate(self) -> None:
        super().validate()
        try:
            if str(uuid.UUID(self.ref_id)) != self.ref_id:
                raise ValueError("Noncanonical idempotency key")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Equity ticket requires a canonical UUID reference") from exc
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Ticket creation time must be timezone-aware")
        if (self.order_type, self.market_hours, self.time_in_force) != ("market", "regular_hours", "gfd"):
            raise ValueError("Stock execution v1 supports only regular-hours market/GFD")
        for value, precision in ((self.dollar_amount, Decimal(".01")), (self.quantity, Decimal(".000001"))):
            if value is not None and Decimal(str(value)) % precision != 0:
                raise ValueError("Ticket amount would change during broker serialization")
        if self.side == "sell" and self.quantity is None:
            raise ValueError("Stock exits require an exact reconciled share quantity")


@dataclass(frozen=True)
class EquityScope:
    account_number: str
    allowed_symbols: tuple[str, ...]
    starts_at: datetime
    expires_at: datetime | None
    max_order_usd: float
    max_exposure_usd: float
    max_daily_notional_usd: float
    max_daily_loss_usd: float
    max_orders: int
    max_quote_age_seconds: float
    max_spread_bps: float
    max_orders_per_minute: int = 2
    loss_recovery_delay: int | None = None
    loss_recovery_unit: str = "manual"

    def validate(self):
        if not isinstance(self.account_number, str) or not self.account_number.strip():
            raise ValueError("An explicit account is required")
        if (not isinstance(self.allowed_symbols, tuple) or not 1 <= len(self.allowed_symbols) <= 100
                or any(not symbol_valid(s) for s in self.allowed_symbols)
                or len(set(self.allowed_symbols)) != len(self.allowed_symbols)):
            raise ValueError("An exact unique equity universe is required")
        for timestamp in (self.starts_at,):
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("Scope times must be timezone-aware")
        if self.expires_at is not None:
            if (not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None
                    or self.expires_at.utcoffset() is None or self.expires_at <= self.starts_at):
                raise ValueError("Optional scope expiry must be after the aware start time")
        for name in ("max_order_usd", "max_exposure_usd", "max_daily_notional_usd", "max_daily_loss_usd",
                     "max_quote_age_seconds", "max_spread_bps"):
            _number(getattr(self, name), name, positive=True)
        if self.max_order_usd > min(self.max_exposure_usd, self.max_daily_notional_usd):
            raise ValueError("Order cap exceeds portfolio or daily envelope")
        if type(self.max_orders) is not int or self.max_orders <= 0:
            raise ValueError("An explicit positive order count is required")
        if type(self.max_orders_per_minute) is not int or self.max_orders_per_minute <= 0:
            raise ValueError("An explicit positive order-rate limit is required")
        validate_recovery(self.loss_recovery_delay, self.loss_recovery_unit)


def assess_ticket(intent: EquityOrderIntent, scope: EquityScope, *, account: Account,
                  portfolio: Portfolio, positions: list[Position], quotes: dict[str, Quote],
                  now: datetime, reconciled_at: datetime, tradable: bool, fractional: bool,
                  daily_notional: float, daily_orders: int, daily_loss_latched: bool,
                  has_unresolved_orders: bool, orders_last_minute: int = 0) -> dict:
    """Pure preflight. Returning allowed=True grants no authority and submits nothing."""
    scope.validate()
    intent.validate()
    portfolio.validate()
    for flag in (tradable, fractional, daily_loss_latched, has_unresolved_orders):
        if type(flag) is not bool:
            raise ValueError("Broker eligibility and risk flags must be explicit booleans")
    for timestamp in (now, reconciled_at):
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Risk snapshot time must be timezone-aware")
    used = _number(daily_notional, "daily_notional")
    if used < 0 or type(daily_orders) is not int or daily_orders < 0:
        raise ValueError("Invalid durable daily usage")
    if type(orders_last_minute) is not int or orders_last_minute < 0:
        raise ValueError("Invalid durable order-rate usage")
    reasons = []
    if now < scope.starts_at or (scope.expires_at is not None and now >= scope.expires_at):
        reasons.append("SCOPE_EXPIRED_OR_NOT_STARTED")
    if intent.created_at > now or (now-intent.created_at).total_seconds() > scope.max_quote_age_seconds:
        reasons.append("TICKET_NOT_FRESH")
    if not 0 <= (now-reconciled_at).total_seconds() <= scope.max_quote_age_seconds:
        reasons.append("ACCOUNT_NOT_FRESH")
    if (account.account_number != scope.account_number or account.agentic_allowed is not True
            or account.state != "active" or account.account_type.lower() != "cash" or portfolio.currency != "USD"):
        reasons.append("ACCOUNT_OR_CURRENCY_MISMATCH")
    if intent.symbol not in scope.allowed_symbols:
        reasons.append("SYMBOL_OUTSIDE_SCOPE")
    session = regular_session_times(now.astimezone(EASTERN).date())
    if session is None or not session[0] <= now.astimezone(EASTERN).time().replace(tzinfo=None) < session[1]:
        reasons.append("REGULAR_SESSION_CLOSED")
    if not tradable:
        reasons.append("INSTRUMENT_NOT_TRADABLE")
    if has_unresolved_orders:
        reasons.append("UNRESOLVED_ORDER")
    by_symbol, exposure = {}, 0.0
    for position in positions:
        if not symbol_valid(position.symbol) or position.symbol in by_symbol:
            raise ValueError("Positions require unique valid symbols")
        quantity = _number(position.quantity, "position quantity")
        sellable = _number(position.sellable_quantity, "sellable quantity")
        if quantity < 0 or not 0 <= sellable <= quantity:
            raise ValueError("Portfolio must be long-only with reconciled sellable quantity")
        by_symbol[position.symbol] = position
    for symbol in set(by_symbol) | {intent.symbol}:
        quote = quotes.get(symbol)
        if quote is None:
            raise ValueError("Every held asset and ticket require exact quotes")
        quote.validate()
        if quote.symbol != symbol or quote.book_timestamp is None or quote.latest_book_timestamp is None:
            raise ValueError("Exact bid and ask timestamps are required")
        if (quote.latest_book_timestamp > now or quote.age_seconds(now) > scope.max_quote_age_seconds
                or quote.spread_bps > scope.max_spread_bps):
            reasons.append("QUOTE_NOT_EXECUTABLE")
        if symbol in by_symbol:
            exposure += by_symbol[symbol].quantity*quote.ask
    quote = quotes[intent.symbol]
    notional = float(intent.dollar_amount) if intent.dollar_amount is not None else float(intent.quantity)*quote.ask
    if not math.isfinite(notional) or not math.isfinite(exposure):
        raise ValueError("Risk calculation overflowed")
    if not fractional and (intent.dollar_amount is not None or intent.quantity != int(intent.quantity)):
        reasons.append("FRACTIONAL_ELIGIBILITY_MISSING")
    if notional > scope.max_order_usd or (intent.side == "buy" and
                                         (used+notional > scope.max_daily_notional_usd
                                          or daily_orders >= scope.max_orders)):
        reasons.append("ORDER_OR_DAILY_CAP")
    if orders_last_minute >= scope.max_orders_per_minute:
        reasons.append("ORDER_RATE_LIMIT")
    if intent.side == "buy":
        if daily_loss_latched:
            reasons.append("DAILY_LOSS_STOP")
        if exposure+notional > scope.max_exposure_usd:
            reasons.append("PORTFOLIO_EXPOSURE_CAP")
        if notional > min(portfolio.cash, portfolio.buying_power):
            reasons.append("INSUFFICIENT_SETTLED_BUYING_POWER")
        if any(s not in scope.allowed_symbols and p.quantity > 0 for s, p in by_symbol.items()):
            reasons.append("UNMANAGED_INVENTORY")
        opposite = {"TQQQ": "SQQQ", "SQQQ": "TQQQ"}.get(intent.symbol)
        if opposite in by_symbol and by_symbol[opposite].quantity > 0:
            reasons.append("OPPOSING_LEVERAGED_POSITION")
    else:
        held = by_symbol.get(intent.symbol)
        if held is None or intent.quantity > held.sellable_quantity:
            reasons.append("SELL_NOT_BACKED_BY_INVENTORY")
    return {"allowed": not reasons, "reasons": reasons, "estimated_notional": notional,
            "current_exposure": exposure, "authority_granted": False}

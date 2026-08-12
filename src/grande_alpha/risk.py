from __future__ import annotations

import math
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from grande_alpha.candidate_execution import session_drawdown_amount
from grande_alpha.models import (
    AuthorityActionReceipt,
    LiveGrant,
    OrderIntent,
    Portfolio,
    Quote,
    utc_now,
)
from grande_alpha.policy import EASTERN, market_session_allowed


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


class RiskEngine:
    def __init__(self, no_trade_open_minutes: int = 5, no_trade_close_minutes: int = 10) -> None:
        self.no_trade_open_minutes = no_trade_open_minutes
        self.no_trade_close_minutes = no_trade_close_minutes
        self.grant: LiveGrant | None = None
        self.killed = True
        self.paused = False
        self.trades_today = 0
        self.daily_notional_used = 0.0
        self.order_times: deque[datetime] = deque(maxlen=100)
        self.seen_ref_ids: set[str] = set()
        self.authorized_notionals: dict[str, float] = {}
        self._receipts: list[AuthorityActionReceipt] = []
        self._last_receipt_digest = ""
        self.session_start_value: float | None = None
        self.session_peak_value: float | None = None
        self.last_portfolio_value: float | None = None
        self.session_date: str | None = None

    def arm(
        self,
        grant: LiveGrant,
        portfolio: Portfolio,
        *,
        initial_daily_notional: float = 0.0,
        initial_trades: int = 0,
        previous_receipt_digest: str = "",
    ) -> None:
        grant.validate()
        portfolio.validate()
        if isinstance(initial_daily_notional, bool) or not isinstance(initial_daily_notional, (int, float)):
            raise ValueError("Initial daily notional must be numeric")
        restored_notional = float(initial_daily_notional)
        if not math.isfinite(restored_notional) or restored_notional < 0:
            raise ValueError("Initial daily notional must be finite and nonnegative")
        if restored_notional > grant.max_daily_notional + 1e-9:
            raise ValueError("Initial daily notional exceeds the authority cap")
        if isinstance(initial_trades, bool) or not isinstance(initial_trades, int) or initial_trades < 0:
            raise ValueError("Initial trade count must be a nonnegative integer")
        if initial_trades > grant.max_trades:
            raise ValueError("Initial trade count exceeds the authority cap")
        if previous_receipt_digest and (
            len(previous_receipt_digest) != 64
            or any(character not in "0123456789abcdef" for character in previous_receipt_digest)
        ):
            raise ValueError("Previous authority receipt digest must be empty or lowercase SHA-256")
        self.grant = grant
        self.killed = False
        self.paused = False
        self.trades_today = initial_trades
        self.daily_notional_used = restored_notional
        self.order_times.clear()
        self.seen_ref_ids.clear()
        self.authorized_notionals.clear()
        self.session_start_value = portfolio.total_value
        self.session_peak_value = portfolio.total_value
        self.last_portfolio_value = portfolio.total_value
        self.session_date = grant.starts_at.astimezone(EASTERN).date().isoformat()
        self._last_receipt_digest = previous_receipt_digest
        self._emit("authority_granted", "Bounded session authority created", grant.starts_at)

    def disarm(self, reason: str = "Live authority revoked") -> None:
        if self.grant is not None:
            self._emit("authority_revoked", reason)
        self.killed = True
        self.paused = False
        self.grant = None
        self.authorized_notionals.clear()

    def pause(self, reason: str = "Paused by user", when: datetime | None = None) -> bool:
        if self.killed or self.grant is None or not self.grant.active(when or utc_now()):
            return False
        if not self.paused:
            self.paused = True
            self._emit("authority_paused", reason, when)
        return True

    def resume(self, reason: str = "Resumed by user", when: datetime | None = None) -> bool:
        if self.killed or self.grant is None or not self.grant.active(when or utc_now()):
            return False
        if self.paused:
            self.paused = False
            self._emit("authority_resumed", reason, when)
        return True

    def revoke(self, reason: str = "Revoked by user", when: datetime | None = None) -> None:
        if self.grant is not None:
            self._emit("authority_revoked", reason, when)
        self.killed = True
        self.paused = False
        self.grant = None
        self.authorized_notionals.clear()

    def drain_receipts(self) -> tuple[AuthorityActionReceipt, ...]:
        """Return and clear immutable receipts for append-only persistence by the controller."""
        receipts = tuple(self._receipts)
        self._receipts.clear()
        return receipts

    def _emit(
        self,
        action: str,
        reason: str,
        when: datetime | None = None,
        intent: OrderIntent | None = None,
        notional: float = 0.0,
    ) -> None:
        grant = self.grant
        if grant is None:
            return
        receipt = AuthorityActionReceipt(
            receipt_id=str(uuid.uuid4()),
            authority_id=grant.authority_id,
            occurred_at=when or utc_now(),
            action=action,
            scope_digest=grant.scope_digest,
            previous_digest=self._last_receipt_digest,
            ref_id=intent.ref_id if intent is not None else "",
            symbol=intent.symbol if intent is not None else "",
            side=intent.side if intent is not None else "",
            notional=max(0.0, float(notional)),
            reason=reason,
        )
        self._receipts.append(receipt)
        self._last_receipt_digest = receipt.digest

    def update_portfolio(self, portfolio: Portfolio) -> None:
        portfolio.validate()
        self.last_portfolio_value = portfolio.total_value
        self.session_peak_value = max(self.session_peak_value or 0.0, portfolio.total_value)

    @property
    def drawdown(self) -> float:
        if self.session_peak_value is None or self.last_portfolio_value is None:
            return 0.0
        return session_drawdown_amount(
            session_peak_equity=self.session_peak_value,
            current_equity=self.last_portfolio_value,
        )

    def session_status(self, now: datetime | None = None) -> str:
        reference = now or utc_now()
        if self.killed:
            return "LOCKED"
        if self.grant is None or not self.grant.active(reference):
            return "EXPIRED"
        if self.paused:
            return "PAUSED"
        if self.drawdown >= self.grant.max_daily_loss:
            return "LOSS LIMIT"
        return "LIVE"

    def _session_allowed(self, now: datetime, market_hours: str) -> bool:
        return market_session_allowed(
            now,
            self.no_trade_open_minutes,
            self.no_trade_close_minutes,
            market_hours,
        )

    def authorize(
        self,
        intent: OrderIntent,
        quote: Quote,
        portfolio: Portfolio,
        current_exposure: float,
        now: datetime | None = None,
        *,
        account_number: str | None = None,
        strategy_fingerprint: str | None = None,
        reconciled_position_quantity: float | None = None,
        reconciled_sellable_quantity: float | None = None,
    ) -> RiskDecision:
        reference = now or utc_now()
        grant = self.grant
        if self.killed or grant is None:
            return RiskDecision(False, "Live authority is locked")
        if self.paused:
            return self._decision(False, "Live authority is paused", reference, intent)
        try:
            grant.validate()
        except ValueError as exc:
            self.revoke(f"Invalid live grant: {exc}", reference)
            return RiskDecision(False, f"Invalid live grant: {exc}")
        if not grant.active(reference):
            self._emit("authority_expired", "Live authority expired", reference)
            self.killed = True
            self.grant = None
            self.authorized_notionals.clear()
            return RiskDecision(False, "Live authority expired")
        if account_number != grant.account_number:
            return self._decision(False, "Account does not match the exact session authority", reference, intent)
        if strategy_fingerprint != grant.strategy_fingerprint:
            return self._decision(False, "Strategy fingerprint does not match the session authority", reference, intent)
        if intent.symbol not in grant.allowed_symbols:
            return self._decision(False, "Ticker is outside the exact session authority", reference, intent)
        if intent.ref_id in self.seen_ref_ids:
            return self._decision(False, "Duplicate order idempotency key", reference, intent)
        try:
            intent.validate()
            quote.validate()
            portfolio.validate()
        except ValueError as exc:
            return self._decision(False, f"Invalid live input: {exc}", reference, intent)
        if quote.symbol != intent.symbol:
            return self._decision(False, "Quote symbol does not match the order", reference, intent)
        if not math.isfinite(float(current_exposure)) or current_exposure < 0:
            return self._decision(False, "Current exposure must be finite and nonnegative", reference, intent)
        profile = grant.execution
        if (
            intent.market_hours != profile.market_hours
            or intent.order_type != profile.order_type
            or intent.time_in_force != profile.time_in_force
        ):
            return self._decision(
                False, "Order route does not match the explicitly authorized session", reference, intent
            )
        allowed_window = (
            market_session_allowed(reference, 0, 0, profile.market_hours)
            if intent.side == "sell"
            else self._session_allowed(reference, profile.market_hours)
        )
        if not allowed_window:
            return self._decision(
                False, "Outside the explicitly authorized trading-session window", reference, intent
            )
        if quote.age_seconds(reference) > grant.max_quote_age_seconds:
            return self._decision(
                False, f"Quote is stale ({quote.age_seconds(reference):.1f}s)", reference, intent
            )
        if quote.spread_bps > grant.max_spread_bps:
            return self._decision(
                False, f"Spread {quote.spread_bps:.1f} bps exceeds limit", reference, intent
            )
        if intent.order_type == "limit" and intent.limit_price is not None:
            if intent.side == "buy":
                maximum = quote.ask * (1 + grant.limit_offset_bps / 10_000)
                if intent.limit_price > maximum + 0.010001:
                    return self._decision(
                        False, "Buy limit exceeds the authorized quote offset", reference, intent
                    )
            else:
                minimum = quote.bid * (1 - grant.limit_offset_bps / 10_000)
                if intent.limit_price < minimum - 0.010001:
                    return self._decision(
                        False, "Sell limit exceeds the authorized quote offset", reference, intent
                    )
        if self.drawdown >= grant.max_daily_loss and intent.side != "sell":
            return self._decision(False, "Daily loss limit reached; entries locked", reference, intent)
        if self.trades_today >= grant.max_trades:
            return self._decision(False, "Trade-count limit reached", reference, intent)
        cutoff = reference - timedelta(minutes=1)
        while self.order_times and self.order_times[0] < cutoff:
            self.order_times.popleft()
        if len(self.order_times) >= grant.max_orders_per_minute:
            return self._decision(False, "Order-rate limit reached", reference, intent)
        notional = intent.estimated_notional or (
            float(intent.quantity) * quote.mid if intent.quantity is not None else 0.0
        )
        if notional <= 0 or not math.isfinite(notional):
            return self._decision(False, "Order has no positive finite notional", reference, intent)
        exact_reducing_exit = False
        if intent.side == "sell":
            quantities = (reconciled_position_quantity, reconciled_sellable_quantity)
            if any(
                value is None
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0
                for value in quantities
            ):
                return self._decision(
                    False,
                    "A sell requires exact freshly reconciled position and sellable quantities",
                    reference,
                    intent,
                    notional,
                )
            position_quantity = float(reconciled_position_quantity or 0.0)
            sellable_quantity = float(reconciled_sellable_quantity or 0.0)
            requested_quantity = float(intent.quantity or 0.0)
            tolerance = 1e-9
            if sellable_quantity > position_quantity + tolerance:
                return self._decision(
                    False,
                    "Reconciled sellable quantity exceeds held inventory",
                    reference,
                    intent,
                    notional,
                )
            if requested_quantity > sellable_quantity + tolerance:
                return self._decision(
                    False,
                    "Sell quantity exceeds freshly reconciled sellable inventory",
                    reference,
                    intent,
                    notional,
                )
            marked_exit_allowance = min(position_quantity, sellable_quantity) * quote.mid
            if notional > marked_exit_allowance + max(1e-9, marked_exit_allowance * 1e-9):
                return self._decision(
                    False,
                    "Sell notional exceeds the exact marked inventory exit allowance",
                    reference,
                    intent,
                    notional,
                )
            exact_reducing_exit = True
        if not exact_reducing_exit and notional > grant.max_order_notional + 1e-9:
            return self._decision(False, "Order notional exceeds session limit", reference, intent, notional)
        reserved = sum(
            value for ref_id, value in self.authorized_notionals.items() if ref_id != intent.ref_id
        )
        if (
            not exact_reducing_exit
            and self.daily_notional_used + reserved + notional > grant.max_daily_notional + 1e-9
        ):
            return self._decision(False, "Daily gross-notional limit would be exceeded", reference, intent, notional)
        if intent.side == "buy":
            if current_exposure + notional > grant.max_total_exposure + 1e-9:
                return self._decision(
                    False, "Total exposure would exceed session limit", reference, intent, notional
                )
            if notional > portfolio.buying_power + 1e-9:
                return self._decision(
                    False, "Broker-reported buying power is insufficient", reference, intent, notional
                )
        self.authorized_notionals[intent.ref_id] = notional
        return self._decision(
            True,
            (
                "Authorized exact inventory-reducing exit outside entry-notional caps"
                if exact_reducing_exit
                else "Authorized by active bounded session"
            ),
            reference,
            intent,
            notional,
        )

    def _decision(
        self,
        allowed: bool,
        reason: str,
        when: datetime,
        intent: OrderIntent,
        notional: float = 0.0,
    ) -> RiskDecision:
        self._emit("order_authorized" if allowed else "order_blocked", reason, when, intent, notional)
        return RiskDecision(allowed, reason)

    def release_authorization(self, ref_id: str, reason: str = "Order was not submitted") -> None:
        notional = self.authorized_notionals.pop(ref_id, 0.0)
        if notional and self.grant is not None:
            self._emit("authorization_released", reason, intent=None, notional=notional)

    def record_submission(self, intent: OrderIntent, when: datetime | None = None) -> None:
        reference = when or datetime.now(UTC)
        notional = self.authorized_notionals.pop(intent.ref_id, intent.estimated_notional)
        self.seen_ref_ids.add(intent.ref_id)
        self.order_times.append(reference)
        self.trades_today += 1
        self.daily_notional_used += max(0.0, notional)
        self._emit(
            "placement_invoked",
            "Broker placement invocation counted conservatively",
            reference,
            intent,
            notional,
        )

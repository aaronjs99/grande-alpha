from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from grande_alpha.models import LiveGrant, OrderIntent, Portfolio, Quote, utc_now

EASTERN = ZoneInfo("America/New_York")


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
        self.trades_today = 0
        self.order_times: deque[datetime] = deque(maxlen=100)
        self.seen_ref_ids: set[str] = set()
        self.session_start_value: float | None = None
        self.last_portfolio_value: float | None = None
        self.session_date: str | None = None

    def arm(self, grant: LiveGrant, portfolio: Portfolio) -> None:
        if grant.account_number == "":
            raise ValueError("Live grant must target an account")
        self.grant = grant
        self.killed = False
        self.trades_today = 0
        self.order_times.clear()
        self.seen_ref_ids.clear()
        self.session_start_value = portfolio.total_value
        self.last_portfolio_value = portfolio.total_value
        self.session_date = grant.starts_at.astimezone(EASTERN).date().isoformat()

    def disarm(self) -> None:
        self.killed = True
        self.grant = None

    def update_portfolio(self, portfolio: Portfolio) -> None:
        self.last_portfolio_value = portfolio.total_value

    @property
    def drawdown(self) -> float:
        if self.session_start_value is None or self.last_portfolio_value is None:
            return 0.0
        return max(0.0, self.session_start_value - self.last_portfolio_value)

    def session_status(self, now: datetime | None = None) -> str:
        reference = now or utc_now()
        if self.killed:
            return "LOCKED"
        if self.grant is None or not self.grant.active(reference):
            return "EXPIRED"
        return "LIVE"

    def _regular_session_allowed(self, now: datetime) -> bool:
        local = now.astimezone(EASTERN)
        if local.weekday() >= 5:
            return False
        opened = datetime.combine(local.date(), time(9, 30), tzinfo=EASTERN) + timedelta(
            minutes=self.no_trade_open_minutes
        )
        closing = datetime.combine(local.date(), time(16, 0), tzinfo=EASTERN) - timedelta(
            minutes=self.no_trade_close_minutes
        )
        return opened <= local <= closing

    def authorize(
        self,
        intent: OrderIntent,
        quote: Quote,
        portfolio: Portfolio,
        current_exposure: float,
        now: datetime | None = None,
    ) -> RiskDecision:
        reference = now or utc_now()
        grant = self.grant
        if self.killed or grant is None:
            return RiskDecision(False, "Live authority is locked")
        if not grant.active(reference):
            self.disarm()
            return RiskDecision(False, "Live authority expired")
        if intent.ref_id in self.seen_ref_ids:
            return RiskDecision(False, "Duplicate order idempotency key")
        if not self._regular_session_allowed(reference):
            return RiskDecision(False, "Outside configured regular-hours trading window")
        if quote.age_seconds(reference) > grant.max_quote_age_seconds:
            return RiskDecision(False, f"Quote is stale ({quote.age_seconds(reference):.1f}s)")
        if quote.spread_bps > grant.max_spread_bps:
            return RiskDecision(False, f"Spread {quote.spread_bps:.1f} bps exceeds limit")
        if self.drawdown >= grant.max_daily_loss and intent.side != "sell":
            self.disarm()
            return RiskDecision(False, "Daily loss limit reached; session locked")
        if self.trades_today >= grant.max_trades:
            return RiskDecision(False, "Trade-count limit reached")
        cutoff = reference - timedelta(minutes=1)
        while self.order_times and self.order_times[0] < cutoff:
            self.order_times.popleft()
        if len(self.order_times) >= grant.max_orders_per_minute:
            return RiskDecision(False, "Order-rate limit reached")
        notional = intent.estimated_notional
        if intent.side == "buy":
            if notional <= 0:
                return RiskDecision(False, "Buy order has no positive notional")
            if notional > grant.max_order_notional + 1e-9:
                return RiskDecision(False, "Order notional exceeds session limit")
            if current_exposure + notional > grant.max_total_exposure + 1e-9:
                return RiskDecision(False, "Total exposure would exceed session limit")
            if notional > portfolio.buying_power + 1e-9:
                return RiskDecision(False, "Broker-reported buying power is insufficient")
        return RiskDecision(True, "Authorized by active bounded session")

    def record_submission(self, intent: OrderIntent, when: datetime | None = None) -> None:
        reference = when or datetime.now(UTC)
        self.seen_ref_ids.add(intent.ref_id)
        self.order_times.append(reference)
        self.trades_today += 1
        if self.grant is not None and self.drawdown >= self.grant.max_daily_loss:
            self.disarm()

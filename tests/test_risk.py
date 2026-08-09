from datetime import UTC, datetime, timedelta

from grande_alpha.models import LiveGrant, OrderIntent, Portfolio, Quote
from grande_alpha.risk import RiskEngine

NOW = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)  # 11:00 ET Monday


def grant(**overrides) -> LiveGrant:
    values = {
        "account_number": "123456789",
        "starts_at": NOW - timedelta(minutes=1),
        "expires_at": NOW + timedelta(hours=1),
        "max_order_notional": 25.0,
        "max_total_exposure": 40.0,
        "max_daily_loss": 2.0,
        "max_trades": 6,
        "max_orders_per_minute": 2,
        "max_spread_bps": 20.0,
        "max_quote_age_seconds": 8.0,
    }
    values.update(overrides)
    return LiveGrant(**values)


def quote(*, bid=40.00, ask=40.04, timestamp=NOW) -> Quote:
    return Quote("TQQQ", bid, ask, 40.02, timestamp)


def intent(ref_id="one", amount=20.0) -> OrderIntent:
    return OrderIntent(ref_id=ref_id, symbol="TQQQ", side="buy", dollar_amount=amount, reason="test")


def test_risk_engine_requires_live_grant() -> None:
    engine = RiskEngine()
    decision = engine.authorize(intent(), quote(), Portfolio(50, 50, 50), 0, NOW)
    assert not decision.allowed


def test_bounded_buy_is_allowed() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    decision = engine.authorize(intent(), quote(), portfolio, 0, NOW)
    assert decision.allowed


def test_notional_and_exposure_are_enforced() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    assert not engine.authorize(intent(amount=30), quote(), portfolio, 0, NOW).allowed
    assert not engine.authorize(intent(amount=20), quote(), portfolio, 30, NOW).allowed


def test_stale_or_wide_quote_is_blocked() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    stale = quote(timestamp=NOW - timedelta(seconds=20))
    wide = quote(bid=40, ask=41)
    assert "stale" in engine.authorize(intent(), stale, portfolio, 0, NOW).reason.lower()
    assert "spread" in engine.authorize(intent(), wide, portfolio, 0, NOW).reason.lower()


def test_duplicate_ref_id_is_blocked() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    order = intent()
    assert engine.authorize(order, quote(), portfolio, 0, NOW).allowed
    engine.record_submission(order, NOW)
    assert "duplicate" in engine.authorize(order, quote(), portfolio, 0, NOW).reason.lower()


def test_expired_grant_fails_closed() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(expires_at=NOW - timedelta(seconds=1)), portfolio)
    assert not engine.authorize(intent(), quote(), portfolio, 0, NOW).allowed
    assert engine.session_status(NOW) == "LOCKED"

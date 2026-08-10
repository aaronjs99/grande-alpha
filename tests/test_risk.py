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


def sell_intent(ref_id="sell-one") -> OrderIntent:
    return OrderIntent(ref_id=ref_id, symbol="TQQQ", side="sell", quantity=0.5, reason="exit")


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


def test_close_window_blocks_entries_but_allows_risk_reducing_exit() -> None:
    close_window = datetime(2026, 8, 10, 19, 55, tzinfo=UTC)  # 15:55 ET Monday
    engine = RiskEngine(no_trade_close_minutes=10)
    portfolio = Portfolio(50, 50, 50)
    engine.arm(
        grant(
            starts_at=close_window - timedelta(minutes=1),
            expires_at=close_window + timedelta(minutes=10),
        ),
        portfolio,
    )
    current_quote = quote(timestamp=close_window)

    assert not engine.authorize(intent(), current_quote, portfolio, 20, close_window).allowed
    assert engine.authorize(sell_intent(), current_quote, portfolio, 20, close_window).allowed


def test_extended_session_requires_the_exact_authorized_limit_route() -> None:
    extended_now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)  # 08:00 ET Monday
    portfolio = Portfolio(100, 100, 100)
    engine = RiskEngine(no_trade_open_minutes=0, no_trade_close_minutes=0)
    engine.arm(
        grant(
            starts_at=extended_now - timedelta(minutes=1),
            expires_at=extended_now + timedelta(hours=1),
            market_hours="extended_hours",
            order_type="limit",
            time_in_force="gfd",
            limit_offset_bps=10,
            max_order_notional=50,
            max_total_exposure=80,
        ),
        portfolio,
    )
    current_quote = quote(bid=40.00, ask=40.04, timestamp=extended_now)
    allowed = OrderIntent(
        ref_id="extended-limit",
        symbol="TQQQ",
        side="buy",
        reason="test",
        order_type="limit",
        quantity=1,
        limit_price=40.08,
        market_hours="extended_hours",
        time_in_force="gfd",
    )
    wrong_route = OrderIntent(
        ref_id="regular-limit",
        symbol="TQQQ",
        side="buy",
        reason="test",
        order_type="limit",
        quantity=1,
        limit_price=40.08,
        market_hours="regular_hours",
        time_in_force="gfd",
    )

    assert engine.authorize(allowed, current_quote, portfolio, 0, extended_now).allowed
    assert "does not match" in engine.authorize(
        wrong_route, current_quote, portfolio, 0, extended_now
    ).reason

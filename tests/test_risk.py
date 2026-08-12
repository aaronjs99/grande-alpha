from datetime import UTC, datetime, timedelta

import pytest

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
        "max_daily_notional": 50.0,
        "strategy_fingerprint": "a" * 64,
    }
    values.update(overrides)
    return LiveGrant(**values)


def quote(*, bid=40.00, ask=40.04, timestamp=NOW) -> Quote:
    return Quote("TQQQ", bid, ask, 40.02, timestamp)


def intent(ref_id="one", amount=20.0) -> OrderIntent:
    return OrderIntent(ref_id=ref_id, symbol="TQQQ", side="buy", dollar_amount=amount, reason="test")


def sell_intent(ref_id="sell-one") -> OrderIntent:
    return OrderIntent(ref_id=ref_id, symbol="TQQQ", side="sell", quantity=0.5, reason="exit")


def authorize(engine, order, current_quote, portfolio, exposure, now=NOW):
    exit_quantities = (
        {
            "reconciled_position_quantity": float(order.quantity or 0.0),
            "reconciled_sellable_quantity": float(order.quantity or 0.0),
        }
        if order.side == "sell"
        else {}
    )
    return engine.authorize(
        order,
        current_quote,
        portfolio,
        exposure,
        now,
        account_number="123456789",
        strategy_fingerprint="a" * 64,
        **exit_quantities,
    )


def test_risk_engine_requires_live_grant() -> None:
    engine = RiskEngine()
    decision = authorize(engine, intent(), quote(), Portfolio(50, 50, 50), 0)
    assert not decision.allowed


def test_bounded_buy_is_allowed() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    decision = authorize(engine, intent(), quote(), portfolio, 0)
    assert decision.allowed


def test_notional_and_exposure_are_enforced() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    assert not authorize(engine, intent(amount=30), quote(), portfolio, 0).allowed
    assert not authorize(engine, intent(amount=20), quote(), portfolio, 30).allowed


def test_profitable_exact_exit_has_separate_bounded_notional_allowance() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(60, 35, 35)
    engine.arm(
        grant(max_order_notional=25, max_daily_notional=50),
        portfolio,
        initial_daily_notional=25,
        initial_trades=1,
    )
    winning_quote = quote(bid=51.98, ask=52.02)
    exit_order = OrderIntent(
        ref_id="winning-exit",
        symbol="TQQQ",
        side="sell",
        quantity=0.5,
        reason="take profit",
    )

    decision = engine.authorize(
        exit_order,
        winning_quote,
        portfolio,
        26.0,
        NOW,
        account_number="123456789",
        strategy_fingerprint="a" * 64,
        reconciled_position_quantity=0.5,
        reconciled_sellable_quantity=0.5,
    )

    assert decision.allowed
    assert "inventory-reducing exit" in decision.reason
    engine.record_submission(exit_order, NOW)
    assert engine.daily_notional_used == pytest.approx(51.0)
    # The liquidation exception cannot reopen the exhausted daily entry budget.
    assert "daily gross" in authorize(
        engine, intent("post-exit-buy", 1), winning_quote, portfolio, 0
    ).reason.lower()


def test_exit_allowance_rejects_missing_inconsistent_or_oversized_inventory() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(60, 35, 35)
    engine.arm(grant(max_order_notional=25, max_daily_notional=50), portfolio)
    winning_quote = quote(bid=51.98, ask=52.02)
    oversized = OrderIntent(
        ref_id="oversized-exit",
        symbol="TQQQ",
        side="sell",
        quantity=0.6,
        reason="malicious oversell",
    )

    missing = engine.authorize(
        oversized,
        winning_quote,
        portfolio,
        26.0,
        NOW,
        account_number="123456789",
        strategy_fingerprint="a" * 64,
    )
    oversized_result = engine.authorize(
        oversized,
        winning_quote,
        portfolio,
        26.0,
        NOW,
        account_number="123456789",
        strategy_fingerprint="a" * 64,
        reconciled_position_quantity=0.5,
        reconciled_sellable_quantity=0.5,
    )
    inconsistent = engine.authorize(
        OrderIntent(
            ref_id="inconsistent-exit",
            symbol="TQQQ",
            side="sell",
            quantity=0.5,
            reason="invalid inventory",
        ),
        winning_quote,
        portfolio,
        26.0,
        NOW,
        account_number="123456789",
        strategy_fingerprint="a" * 64,
        reconciled_position_quantity=0.5,
        reconciled_sellable_quantity=0.6,
    )

    assert not missing.allowed
    assert "exact freshly reconciled" in missing.reason
    assert not oversized_result.allowed
    assert "exceeds freshly reconciled" in oversized_result.reason
    assert not inconsistent.allowed
    assert "exceeds held inventory" in inconsistent.reason


def test_exit_inventory_arguments_never_relax_buy_caps() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(60, 60, 60)
    engine.arm(grant(max_order_notional=25, max_daily_notional=100), portfolio)

    decision = engine.authorize(
        intent("oversized-buy-with-exit-fields", 26),
        quote(),
        portfolio,
        0,
        NOW,
        account_number="123456789",
        strategy_fingerprint="a" * 64,
        reconciled_position_quantity=100,
        reconciled_sellable_quantity=100,
    )

    assert not decision.allowed
    assert "notional exceeds" in decision.reason.lower()


def test_stale_or_wide_quote_is_blocked() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    stale = quote(timestamp=NOW - timedelta(seconds=20))
    wide = quote(bid=40, ask=41)
    assert "stale" in authorize(engine, intent(), stale, portfolio, 0).reason.lower()
    assert "spread" in authorize(engine, intent(), wide, portfolio, 0).reason.lower()


def test_non_finite_live_inputs_fail_closed() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    with pytest.raises(ValueError, match="finite and positive"):
        engine.arm(grant(max_order_notional=float("nan")), portfolio)

    engine.arm(grant(), portfolio)
    assert not authorize(engine, intent(amount=float("nan")), quote(), portfolio, 0).allowed
    assert not authorize(engine, intent(), quote(bid=float("nan")), portfolio, 0).allowed
    assert not authorize(engine, intent(), quote(), portfolio, float("nan")).allowed


def test_grossly_malformed_live_inputs_return_structured_rejections() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    with pytest.raises(ValueError, match="must be numeric"):
        engine.arm(grant(max_order_notional="25"), portfolio)

    engine.arm(grant(), portfolio)
    malformed_quote = Quote("TQQQ", 40.0, 40.04, 40.02, None)
    malformed_portfolio = Portfolio(50, None, 50)
    assert "timestamp" in authorize(engine, intent(), malformed_quote, portfolio, 0).reason
    assert "numeric" in authorize(engine, intent(), quote(), malformed_portfolio, 0).reason
    assert "numeric" in authorize(engine, intent(amount="20"), quote(), portfolio, 0).reason


def test_duplicate_ref_id_is_blocked() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    order = intent()
    assert authorize(engine, order, quote(), portfolio, 0).allowed
    engine.record_submission(order, NOW)
    assert "duplicate" in authorize(engine, order, quote(), portfolio, 0).reason.lower()


def test_expired_grant_fails_closed() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(expires_at=NOW - timedelta(seconds=1)), portfolio)
    assert not authorize(engine, intent(), quote(), portfolio, 0).allowed
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

    assert not authorize(engine, intent(), current_quote, portfolio, 20, close_window).allowed
    assert authorize(engine, sell_intent(), current_quote, portfolio, 20, close_window).allowed


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

    assert authorize(engine, allowed, current_quote, portfolio, 0, extended_now).allowed
    assert "does not match" in authorize(
        engine, wrong_route, current_quote, portfolio, 0, extended_now
    ).reason


def test_authority_requires_exact_account_ticker_and_strategy_fingerprint() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(allowed_symbols=("TQQQ",)), portfolio)

    wrong_account = engine.authorize(
        intent(),
        quote(),
        portfolio,
        0,
        NOW,
        account_number="987654321",
        strategy_fingerprint="a" * 64,
    )
    wrong_strategy = engine.authorize(
        intent(),
        quote(),
        portfolio,
        0,
        NOW,
        account_number="123456789",
        strategy_fingerprint="b" * 64,
    )
    sqqq = OrderIntent(
        ref_id="sqqq",
        symbol="SQQQ",
        side="buy",
        dollar_amount=10,
        reason="outside exact scope",
    )
    wrong_ticker = engine.authorize(
        sqqq,
        Quote("SQQQ", 10, 10.01, 10.005, NOW),
        portfolio,
        0,
        NOW,
        account_number="123456789",
        strategy_fingerprint="a" * 64,
    )

    assert "account" in wrong_account.reason.lower()
    assert "fingerprint" in wrong_strategy.reason.lower()
    assert "ticker" in wrong_ticker.reason.lower()


def test_daily_gross_notional_reserves_authorizations_and_counts_submissions() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(100, 100, 100)
    engine.arm(grant(max_daily_notional=30), portfolio)
    first = intent("first", 20)

    assert authorize(engine, first, quote(), portfolio, 0).allowed
    assert "daily gross" in authorize(engine, intent("second", 20), quote(), portfolio, 0).reason.lower()
    engine.record_submission(first, NOW)
    assert engine.daily_notional_used == 20
    assert authorize(engine, intent("third", 10), quote(), portfolio, 0).allowed
    engine.release_authorization("third")
    assert engine.daily_notional_used == 20


def test_pause_resume_revoke_are_visible_state_transitions_and_fail_closed() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)

    assert engine.pause(when=NOW)
    assert engine.session_status(NOW) == "PAUSED"
    assert "paused" in authorize(engine, intent(), quote(), portfolio, 0).reason.lower()
    assert engine.resume(when=NOW)
    assert authorize(engine, intent(), quote(), portfolio, 0).allowed
    engine.revoke(when=NOW)
    assert engine.session_status(NOW) == "LOCKED"
    assert not authorize(engine, intent("after-revoke"), quote(), portfolio, 0).allowed


def test_authority_receipts_are_immutable_and_hash_chained() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(grant(), portfolio)
    authorize(engine, intent(), quote(), portfolio, 0)
    engine.pause(when=NOW)

    receipts = engine.drain_receipts()
    assert [receipt.action for receipt in receipts] == [
        "authority_granted",
        "order_authorized",
        "authority_paused",
    ]
    assert receipts[1].previous_digest == receipts[0].digest
    assert receipts[2].previous_digest == receipts[1].digest
    assert receipts[0].as_dict()["receipt_digest"] == receipts[0].digest
    with pytest.raises(AttributeError):
        receipts[0].action = "tampered"


def test_daily_expiry_uses_eastern_date_across_utc_midnight() -> None:
    starts = datetime(2026, 8, 10, 23, 30, tzinfo=UTC)  # 19:30 ET
    valid = grant(starts_at=starts, expires_at=starts + timedelta(hours=1))
    valid.validate()

    crosses_eastern_midnight = grant(
        starts_at=datetime(2026, 8, 11, 3, 30, tzinfo=UTC),  # 23:30 ET
        expires_at=datetime(2026, 8, 11, 4, 30, tzinfo=UTC),  # 00:30 ET
    )
    with pytest.raises(ValueError, match="same Eastern calendar day"):
        crosses_eastern_midnight.validate()


def test_session_loss_is_measured_from_peak_portfolio_value() -> None:
    engine = RiskEngine()
    engine.arm(grant(max_daily_loss=2), Portfolio(50, 50, 50))
    engine.update_portfolio(Portfolio(52, 52, 52))
    engine.update_portfolio(Portfolio(49.5, 49.5, 49.5))

    assert engine.drawdown == 2.5
    assert engine.session_status(NOW) == "LOSS LIMIT"
    assert "loss limit" in authorize(engine, intent(), quote(), Portfolio(49.5, 49.5, 49.5), 0).reason.lower()
    assert authorize(engine, sell_intent(), quote(), Portfolio(49.5, 49.5, 49.5), 20).allowed


def test_same_day_usage_can_be_restored_without_resetting_caps() -> None:
    engine = RiskEngine()
    portfolio = Portfolio(50, 50, 50)
    engine.arm(
        grant(max_daily_notional=30, max_trades=3),
        portfolio,
        initial_daily_notional=25,
        initial_trades=2,
        previous_receipt_digest="b" * 64,
    )

    assert engine.daily_notional_used == 25
    assert engine.trades_today == 2
    assert engine.drain_receipts()[0].previous_digest == "b" * 64
    assert "daily gross" in authorize(engine, intent(amount=10), quote(), portfolio, 0).reason.lower()

    with pytest.raises(ValueError, match="exceeds the authority cap"):
        RiskEngine().arm(
            grant(max_daily_notional=30),
            portfolio,
            initial_daily_notional=31,
        )

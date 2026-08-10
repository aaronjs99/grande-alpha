from datetime import UTC, datetime

import pytest

from grande_alpha.models import OrderIntent, Quote


def test_fractional_order_arguments_are_precise() -> None:
    order = OrderIntent(
        ref_id="id",
        symbol="TQQQ",
        side="sell",
        quantity=0.123456,
        reason="test",
    )
    args = order.broker_arguments("123")
    assert args["quantity"] == "0.123456"
    assert "dollar_amount" not in args


def test_quote_spread_and_age() -> None:
    now = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
    item = Quote("QQQ", 100.0, 100.1, 100.05, now)
    assert 9.9 < item.spread_bps < 10.1
    assert item.age_seconds(now) == 0


def test_extended_limit_order_uses_provider_exact_arguments() -> None:
    order = OrderIntent(
        ref_id="extended-id",
        symbol="SQQQ",
        side="buy",
        reason="test",
        order_type="limit",
        quantity=2,
        limit_price=10.25,
        market_hours="extended_hours",
        time_in_force="gtc",
    )

    args = order.broker_arguments("123")

    assert args["type"] == "limit"
    assert args["market_hours"] == "extended_hours"
    assert args["time_in_force"] == "gtc"
    assert args["quantity"] == "2"
    assert args["limit_price"] == "10.25"
    assert order.estimated_notional == 20.5


def test_provider_invalid_session_combinations_fail_before_review() -> None:
    with pytest.raises(ValueError, match="require limit"):
        OrderIntent(
            ref_id="bad-market",
            symbol="TQQQ",
            side="buy",
            reason="test",
            dollar_amount=20,
            market_hours="extended_hours",
        ).broker_arguments("123")
    with pytest.raises(ValueError, match="whole-share"):
        OrderIntent(
            ref_id="bad-fraction",
            symbol="TQQQ",
            side="sell",
            reason="test",
            order_type="limit",
            quantity=0.5,
            limit_price=40,
        ).broker_arguments("123")

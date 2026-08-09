from datetime import UTC, datetime

from momentum_trader.models import OrderIntent, Quote


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


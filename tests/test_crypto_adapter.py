from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from crypto_fixtures import ACCOUNT, EXECUTION, INTENT, ORDER, ORDER_ID, PAIR_ID, FakeCryptoServer

from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.crypto import CryptoOutcomeUnknown, parse_order
from grande_alpha.broker.discovery import RobinhoodDiscovery
from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker
from grande_alpha.crypto_models import CryptoOrderIntent, decimal_amount


@pytest.mark.parametrize("value", [True, 1.5, "NaN", "Infinity", "-1", "1e100", "1e-30"])
def test_exact_amounts_reject_coercion_and_unsupported_precision(value):
    with pytest.raises(ValueError):
        decimal_amount(value, "amount")


@pytest.mark.parametrize("changes", [
    {"quantity": Decimal("1")}, {"dollar_amount": None}, {"dollar_amount": 5.0},
    {"time_in_force": "ioc"}, {"time_in_force": "gfd"}, {"limit_price": Decimal("1")},
    {"order_type": "stop_market"}, {"order_type": "stop_loss"}, {"ref_id": "not-a-uuid"},
    {"symbol": "BTC"}, {"side": "short"},
])
def test_crypto_intent_rejects_equity_routes_and_ambiguous_sizing(changes):
    with pytest.raises(ValueError):
        replace(INTENT, **changes).validate()


def test_crypto_wire_uses_exact_decimal_strings_and_one_reference():
    intent = replace(INTENT, dollar_amount=None, quantity=Decimal("0.00000123"),
                     order_type="stop_limit", stop_price=Decimal("100000.01"), limit_price=Decimal("99999.99"), time_in_force="gfw")
    args = intent.arguments(ACCOUNT.rhs_account_number, placement=True)
    assert args["quantity"] == "0.00000123"
    assert args["ref_id"] == intent.ref_id
    assert args["type"] == "stop_limit"
    assert "ref_id" not in intent.arguments(ACCOUNT.rhs_account_number)
    with pytest.raises(ValueError, match="numeric RHS"):
        intent.arguments(ACCOUNT.rhc_account_number)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,reason", [
    ({"dollar_amount": None, "quantity": Decimal("0.000000011")}, "increment"),
    ({"dollar_amount": Decimal("0.5")}, "minimum"),
    ({"dollar_amount": None, "quantity": Decimal("101")}, "limits"),
    ({"order_type": "limit", "limit_price": Decimal("99999.999")}, "increment"),
])
async def test_pair_constraints_reject_before_preview(changes, reason):
    server = FakeCryptoServer()
    with pytest.raises(ValueError, match=reason):
        await server.adapter.preview(ACCOUNT.account_number, replace(INTENT, **changes))
    assert all(name != "preview_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
async def test_account_specific_tradability_and_market_only_restrictions():
    server = FakeCryptoServer()
    pair = server.responses["get_currency_pairs"]["results"][0]
    pair["tradability_by_account_type"] = {"individual": "sell_only"}
    with pytest.raises(ValueError, match="sell_only"):
        await server.adapter.preview(ACCOUNT.account_number, INTENT)
    pair.pop("tradability_by_account_type")
    pair["market_orders_only"] = True
    with pytest.raises(ValueError, match="market orders only"):
        await server.adapter.preview(ACCOUNT.account_number, replace(INTENT, order_type="limit", limit_price=Decimal("100000")))


@pytest.mark.asyncio
async def test_preview_place_is_account_scoped_and_reference_cannot_be_resent():
    server = FakeCryptoServer()
    review = await server.adapter.preview(ACCOUNT.account_number, INTENT)
    assert review.estimated_fee == Decimal("0.03")
    assert review.order.net_estimated_notional == Decimal("5.00")
    placed = await server.adapter.place(review)
    assert placed.order_id == ORDER_ID and not placed.terminal
    args = next(args for name, args in server.calls if name == "place_crypto_order")
    assert args["rhs_account_number"] == ACCOUNT.rhs_account_number
    assert args["ref_id"] == INTENT.ref_id and args["dollar_amount"] == "5.00"
    with pytest.raises(BrokerError, match="unused preview"):
        await server.adapter.place(review)
    with pytest.raises(BrokerError, match="already dispatched"):
        await server.adapter.preview(ACCOUNT.account_number, INTENT)
    assert sum(name == "place_crypto_order" for name, _ in server.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("account", [
    replace(ACCOUNT, rhs_account_number="RHC123"), replace(ACCOUNT, rhc_account_number=""),
    replace(ACCOUNT, agentic_allowed=False), replace(ACCOUNT, state="inactive"),
])
async def test_account_binding_fails_before_crypto_calls(account):
    server = FakeCryptoServer()
    server.accounts = [account]
    with pytest.raises(BrokerError):
        await server.adapter.preview(ACCOUNT.account_number, INTENT)
    assert not server.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("power", [None, {}, {"buying_power": "5.00"}])
async def test_equity_buying_power_never_funds_crypto_and_market_buffer_is_checked(power):
    server = FakeCryptoServer()
    server.responses["get_portfolio"] = {"buying_power": {"buying_power": "10000"}, "crypto_buying_power": power}
    with pytest.raises((ValueError, BrokerError)):
        await server.adapter.preview(ACCOUNT.account_number, INTENT)
    assert all(name != "place_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "funds", "halt", "binding", "disconnect", "copied"])
async def test_preflight_rechecks_reject_changed_or_forged_review(change):
    server = FakeCryptoServer()
    review = await server.adapter.preview(ACCOUNT.account_number, INTENT)
    if change == "expired":
        server.now += timedelta(seconds=16)
    elif change == "funds":
        server.responses["get_portfolio"]["crypto_buying_power"]["buying_power"] = "1"
    elif change == "halt":
        server.responses["get_currency_pairs"]["results"][0]["halted"] = True
    elif change == "binding":
        server.accounts = [replace(ACCOUNT, rhs_account_number="99999999")]
    elif change == "disconnect":
        server.adapter.invalidate_reviews()
    else:
        review = replace(review)
    with pytest.raises((ValueError, BrokerError)):
        await server.adapter.place(review)
    assert all(name != "place_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    TimeoutError(), {}, {"order": None}, {"order": {**ORDER, "ref_id": "wrong"}},
    {"order": {**ORDER, "account_id": "other"}}, {"order": {**ORDER, "currency_pair_id": "other"}},
    {"order": {**ORDER, "speculative": None}},
    {"order": ORDER, "crypto_account_number": "RHC-OTHER"},
])
async def test_ambiguous_placement_stays_unresolved_and_is_never_resent(response):
    server = FakeCryptoServer()
    review = await server.adapter.preview(ACCOUNT.account_number, INTENT)
    server.responses["place_crypto_order"] = response
    with pytest.raises(CryptoOutcomeUnknown) as failure:
        await server.adapter.place(review)
    assert failure.value.reference == INTENT.ref_id
    with pytest.raises(BrokerError):
        await server.adapter.place(review)
    assert sum(name == "place_crypto_order" for name, _ in server.calls) == 1


@pytest.mark.asyncio
async def test_cancelled_placement_wait_does_not_release_its_reference():
    server = FakeCryptoServer()
    review = await server.adapter.preview(ACCOUNT.account_number, INTENT)
    server.responses["place_crypto_order"] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await server.adapter.place(review)
    with pytest.raises(BrokerError):
        await server.adapter.place(review)


@pytest.mark.asyncio
async def test_sell_uses_transferable_quantity_and_keeps_incomplete_basis():
    server = FakeCryptoServer()
    intent = replace(INTENT, side="sell", dollar_amount=None, quantity=Decimal("0.00005"))
    server.responses["preview_crypto_order"]["order"]["side"] = "sell"
    review = await server.adapter.preview(ACCOUNT.account_number, intent)
    position = (await server.adapter.positions(ACCOUNT.account_number))[0]
    assert position.sellable_quantity == Decimal("0.00008")
    assert position.direct_quantity < position.quantity
    server.responses["get_crypto_positions"]["results"][0]["quantity_transferable"] = "0.00001"
    with pytest.raises(BrokerError, match="sellable"):
        await server.adapter.place(review)


def test_partial_fills_use_effective_prices_and_net_cash_without_double_counting_fees():
    order = parse_order({**ORDER, "state": "partially_filled", "cumulative_quantity": "0.00002",
                         "executions": [EXECUTION], "net_rounded_executed_notional": "2.04"})
    assert order.execution_complete and not order.terminal
    assert order.executions[0].effective_price == Decimal("100500")
    assert order.executions[0].notional == Decimal("2.01")
    assert order.net_executed_notional == Decimal("2.04")
    assert not replace(order, executions=()).execution_complete
    assert not replace(order, net_executed_notional=None).execution_complete
    assert not replace(order, state="filled").execution_complete
    assert not parse_order({**ORDER, "state": "future-provider-state"}).terminal


def test_duplicate_or_excess_fills_are_rejected():
    with pytest.raises(BrokerError, match="Duplicate"):
        parse_order({**ORDER, "executions": [EXECUTION, EXECUTION]})
    with pytest.raises(BrokerError, match="exceed"):
        parse_order({**ORDER, "executions": [EXECUTION]})


@pytest.mark.asyncio
async def test_order_read_validates_scope_every_page_and_preserves_filters():
    server = FakeCryptoServer()

    def page(args):
        if "cursor" not in args:
            return {"rhs_account_number": ACCOUNT.rhs_account_number, "results": [], "next": "https://example.invalid/?cursor=next"}
        assert args == {"rhs_account_number": ACCOUNT.rhs_account_number, "cursor": "next", "order_id": ORDER_ID}
        return {"rhs_account_number": "other", "results": [ORDER]}

    server.responses["get_crypto_orders"] = page
    with pytest.raises(BrokerError, match="RHS account"):
        await server.adapter.orders(ACCOUNT.account_number, order_id=ORDER_ID)


@pytest.mark.asyncio
async def test_order_and_position_uuid_mismatch_blocks_reconciliation():
    server = FakeCryptoServer()
    await server.adapter.positions(ACCOUNT.account_number)
    server.responses["get_crypto_orders"]["results"][0]["account_id"] = "wrong"
    with pytest.raises(BrokerError, match="UUID changed"):
        await server.adapter.orders(ACCOUNT.account_number)


@pytest.mark.asyncio
async def test_cancel_acceptance_is_not_a_terminal_order_and_foreign_order_cannot_cancel():
    server = FakeCryptoServer()
    assert await server.adapter.cancel(ACCOUNT.account_number, ORDER_ID) is True
    assert not (await server.adapter.orders(ACCOUNT.account_number))[0].terminal
    with pytest.raises(BrokerError, match="requested order"):
        await server.adapter.cancel(ACCOUNT.account_number, "foreign-order")
    assert sum(name == "cancel_crypto_order" for name, _ in server.calls) == 1


@pytest.mark.asyncio
async def test_dollar_preview_validates_provider_resolved_quantity_against_pair_limits():
    server = FakeCryptoServer()
    server.responses["preview_crypto_order"]["order"]["quantity"] = "101"
    with pytest.raises(ValueError, match="limits"):
        await server.adapter.preview(ACCOUNT.account_number, INTENT)


@pytest.mark.asyncio
async def test_invalid_schema_never_dispatches_guessed_arguments():
    server = FakeCryptoServer()
    server.schemas["preview_crypto_order"]["required"].append("unknown_required")
    with pytest.raises(BrokerError, match="contract"):
        await server.adapter.preview(ACCOUNT.account_number, INTENT)
    assert all(name != "preview_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
async def test_preview_quantity_order_and_limit_price_echo_must_match():
    server = FakeCryptoServer()
    intent = CryptoOrderIntent("BTC-USD", PAIR_ID, "buy", quantity=Decimal("0.00006"))
    with pytest.raises(BrokerError, match="different quantity"):
        await server.adapter.preview(ACCOUNT.account_number, intent)
    pairs = await RobinhoodDiscovery(server.call, server.schemas).currency_pairs()
    assert pairs[0].crypto_rules.min_notional == Decimal("1.00")


@pytest.mark.asyncio
async def test_mcp_account_and_portfolio_keep_distinct_crypto_identifiers_and_cash(monkeypatch):
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def call(name, args):
        if name == "get_accounts":
            return {"accounts": [{"account_number": "SYNTHETIC", "rhs_account_number": "12345678",
                                  "rhc_account_number": "RHC-FIXTURE", "type": "cash", "state": "active",
                                  "brokerage_account_type": "individual", "agentic_allowed": True,
                                  "deactivated": True, "permanently_deactivated": False}]}
        assert args == {"account_number": "SYNTHETIC"}
        return {"total_value": "100", "cash": "30", "buying_power": {"buying_power": "70"},
                "crypto_buying_power": {"buying_power": "20"}, "crypto_value": "10"}

    monkeypatch.setattr(broker, "_call", call)
    account = (await broker.get_accounts())[0]
    assert account.rhs_account_number == "12345678" and account.rhc_account_number == "RHC-FIXTURE"
    assert account.state == "inactive"
    portfolio = await broker.get_portfolio(account.account_number)
    portfolio.validate()
    assert portfolio.buying_power == 70 and portfolio.crypto_buying_power == 20
    assert portfolio.total_value == 100  # Already includes crypto; never add crypto_value again.


@pytest.mark.asyncio
async def test_preview_invalidated_during_preflight_cannot_dispatch():
    server = FakeCryptoServer()
    review = await server.adapter.preview(ACCOUNT.account_number, INTENT)

    def funds(_):
        server.adapter.invalidate_reviews()
        return {"crypto_buying_power": {"buying_power": "10.18"}}

    server.responses["get_portfolio"] = funds
    with pytest.raises(BrokerError, match="invalidated"):
        await server.adapter.place(review)
    assert all(name != "place_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
async def test_slow_funding_check_does_not_make_an_old_preview_fresh():
    server = FakeCryptoServer()

    def funds(_):
        server.now += timedelta(seconds=16)
        return {"crypto_buying_power": {"buying_power": "10.18"}}

    server.responses["get_portfolio"] = funds
    review = await server.adapter.preview(ACCOUNT.account_number, INTENT)
    with pytest.raises(BrokerError, match="expired"):
        await server.adapter.place(review)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-16, 3])
async def test_stale_or_future_provider_preview_is_rejected(offset):
    server = FakeCryptoServer()
    stamp = (server.now + timedelta(seconds=offset)).isoformat()
    server.responses["preview_crypto_order"]["order"].update(created_at=stamp, updated_at=stamp)
    with pytest.raises(BrokerError, match="stale or future"):
        await server.adapter.preview(ACCOUNT.account_number, INTENT)

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from grande_alpha.broker import robinhood_mcp
from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker

REQUIRED_TOOLS = {
    "get_accounts",
    "get_portfolio",
    "get_equity_quotes",
    "get_equity_tradability",
    "get_equity_positions",
    "get_equity_orders",
    "review_equity_order",
    "place_equity_order",
    "cancel_equity_order",
}


class TaskBoundContext:
    def __init__(self, value) -> None:
        self.value = value
        self.owner = None
        self.exited_by = None

    async def __aenter__(self):
        self.owner = asyncio.current_task()
        return self.value

    async def __aexit__(self, *_exc) -> None:
        self.exited_by = asyncio.current_task()
        assert self.exited_by is self.owner


class FakeSession:
    def __init__(self) -> None:
        self.call_tasks = []
        self.timeouts = []

    async def initialize(self) -> None:
        return None

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name=name, inputSchema={}) for name in REQUIRED_TOOLS]
        )

    async def call_tool(self, name, arguments, *, read_timeout_seconds=None):
        self.call_tasks.append(asyncio.current_task())
        self.timeouts.append(read_timeout_seconds)
        assert name == "get_accounts"
        assert arguments == {}
        return SimpleNamespace(
            isError=False,
            structuredContent={"data": {"accounts": []}},
            content=[],
        )


class FakeCallback:
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_mcp_transport_calls_and_teardown_share_one_owner_task(monkeypatch) -> None:
    transport = TaskBoundContext((object(), object(), None))
    session = FakeSession()
    session_context = TaskBoundContext(session)
    monkeypatch.setattr(robinhood_mcp, "OAuthCallbackServer", FakeCallback)
    monkeypatch.setattr(robinhood_mcp, "OAuthClientProvider", lambda **_kwargs: object())
    monkeypatch.setattr(robinhood_mcp, "streamablehttp_client", lambda *_args, **_kwargs: transport)
    monkeypatch.setattr(robinhood_mcp, "ClientSession", lambda *_args, **_kwargs: session_context)

    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    caller = asyncio.current_task()
    await broker.connect()
    assert broker.connected
    assert await broker.get_accounts() == []
    await broker.disconnect()

    assert not broker.connected
    assert transport.owner is transport.exited_by
    assert session_context.owner is session_context.exited_by
    assert transport.owner is session_context.owner
    assert transport.owner is not caller
    assert session.call_tasks == [transport.owner]
    assert session.timeouts[0].total_seconds() == 10


@pytest.mark.asyncio
async def test_connect_surfaces_actionable_leaf_from_taskgroup() -> None:
    broker = RobinhoodMCPBroker(allow_interactive_auth=False)

    async def fail_session(ready):
        failure = ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [BrokerError("Cached Robinhood authorization requires browser consent")],
        )
        ready.set_exception(failure)
        raise failure

    broker._session_owner = fail_session

    with pytest.raises(BrokerError, match="requires browser consent") as captured:
        await broker.connect()

    assert "TaskGroup" not in str(captured.value)


@pytest.mark.asyncio
async def test_quote_without_venue_timestamp_is_not_made_artificially_fresh(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(name, arguments):
        assert name == "get_equity_quotes"
        assert arguments == {"symbols": ["QQQ"]}
        return {
            "results": [
                {
                    "quote": {
                        "symbol": "QQQ",
                        "has_traded": True,
                        "state": "active",
                        "bid_price": "100.00",
                        "ask_price": "100.02",
                        "last_trade_price": "100.01",
                        "venue_bid_time": "2026-08-11T13:30:01Z",
                        "venue_ask_time": "2026-08-11T13:30:01Z",
                        "venue_last_trade_time": None,
                        "venue_last_non_reg_trade_time": None,
                    }
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)

    with pytest.raises(Exception, match="omitted a valid venue timestamp"):
        await broker.get_quotes(["QQQ"])


@pytest.mark.asyncio
async def test_quote_uses_latest_real_venue_timestamp(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "results": [
                {
                    "quote": {
                        "symbol": "QQQ",
                        "has_traded": True,
                        "state": "active",
                        "bid_price": "100.00",
                        "ask_price": "100.02",
                        "last_trade_price": "100.01",
                        "last_non_reg_trade_price": "100.03",
                        "venue_bid_time": "2026-08-11T13:30:00Z",
                        "venue_ask_time": "2026-08-11T13:30:01Z",
                        "venue_last_trade_time": "2026-08-11T13:29:59Z",
                        "venue_last_non_reg_trade_time": "2026-08-11T13:30:01Z",
                    }
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)

    quote = (await broker.get_quotes(["QQQ"]))["QQQ"]
    assert quote.last == 100.03
    assert quote.timestamp.isoformat() == "2026-08-11T13:30:01+00:00"
    assert quote.bid_timestamp.isoformat() == "2026-08-11T13:30:00+00:00"
    assert quote.ask_timestamp.isoformat() == "2026-08-11T13:30:01+00:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("has_traded", "state", "message"),
    [
        (False, "active", "has not traded"),
        ("true", "active", "has_traded must be a boolean"),
        (True, "inactive", "not actively listed"),
        (True, None, "listing state"),
    ],
)
async def test_quote_response_requires_traded_active_listing(
    monkeypatch, has_traded, state, message
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "results": [
                {
                    "quote": {
                        "symbol": "QQQ",
                        "has_traded": has_traded,
                        "state": state,
                        "bid_price": "100.00",
                        "ask_price": "100.02",
                        "last_trade_price": "100.01",
                        "venue_bid_time": "2026-08-11T13:30:00Z",
                        "venue_ask_time": "2026-08-11T13:30:01Z",
                        "venue_last_trade_time": "2026-08-11T13:30:01Z",
                    }
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match=message):
        await broker.get_quotes(["QQQ"])


@pytest.mark.asyncio
async def test_quote_response_rejects_duplicate_symbols(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    quote = {
        "symbol": "QQQ",
        "has_traded": True,
        "state": "active",
        "bid_price": "100.00",
        "ask_price": "100.02",
        "last_trade_price": "100.01",
        "venue_bid_time": "2026-08-11T13:30:01Z",
        "venue_ask_time": "2026-08-11T13:30:01Z",
        "venue_last_trade_time": "2026-08-11T13:30:01Z",
    }

    async def fake_call(_name, _arguments):
        return {"results": [{"quote": quote}, {"quote": dict(quote)}]}

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="duplicate quote symbols"):
        await broker.get_quotes(["QQQ"])


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"results": {}}, {"results": [None]}, {"results": [{"quote": []}]}])
async def test_quote_response_rejects_malformed_collection_and_rows(
    monkeypatch, payload
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return payload

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="results array|quote object"):
        await broker.get_quotes(["QQQ"])


@pytest.mark.asyncio
@pytest.mark.parametrize("pagination", [{"next_page_token": "more"}, {"next": "more"}])
async def test_paginated_order_set_fails_closed(monkeypatch, pagination) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        # _call unwraps the provider's top-level data object. The production
        # response currently exposes its continuation at data.next.
        return {"orders": [], **pagination}

    monkeypatch.setattr(broker, "_call", fake_call)

    with pytest.raises(Exception, match="paginated order set|pagination continuation"):
        await broker.get_orders("agentic")


@pytest.mark.asyncio
async def test_position_malformed_data_next_fails_closed_before_omitting_later_inventory(
    monkeypatch,
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        # This is the unwrapped form of the provider's real data.next response.
        return {"positions": [], "next": "later-position-page"}

    monkeypatch.setattr(broker, "_call", fake_call)

    with pytest.raises(Exception, match="pagination continuation"):
        await broker.get_positions("agentic")


@pytest.mark.asyncio
@pytest.mark.parametrize("method,field,value", [("get_positions", "positions", {}), ("get_orders", "orders", False)])
async def test_falsey_nonstring_continuation_never_looks_complete(
    monkeypatch, method: str, field: str, value
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {field: [], "next": value}

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="continuation must be a URL"):
        await getattr(broker, method)("agentic")


@pytest.mark.asyncio
async def test_position_pagination_aggregates_two_pages_with_exact_cursor(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    calls = []

    async def fake_call(_name, arguments):
        calls.append(dict(arguments))
        if "cursor" not in arguments:
            return {
                "positions": [
                    {
                        "symbol": "TQQQ",
                        "quantity": "0.2",
                        "shares_available_for_sells": "0.2",
                        "average_buy_price": "50",
                    }
                ],
                "next": "https://provider.invalid/orders?cursor=position-page-2",
            }
        return {
            "positions": [
                {
                    "symbol": "SQQQ",
                    "quantity": "0.1",
                    "shares_available_for_sells": "0.1",
                    "average_buy_price": "40",
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)

    positions = await broker.get_positions("agentic")
    assert [position.symbol for position in positions] == ["TQQQ", "SQQQ"]
    assert calls == [
        {"account_number": "agentic"},
        {"account_number": "agentic", "cursor": "position-page-2"},
    ]


@pytest.mark.asyncio
async def test_position_pagination_rejects_duplicate_symbol_across_pages(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, arguments):
        result = {
            "positions": [
                {
                    "symbol": "TQQQ",
                    "quantity": "0.1",
                    "shares_available_for_sells": "0.1",
                    "average_buy_price": "50",
                }
            ]
        }
        if "cursor" not in arguments:
            result["next"] = "https://provider.invalid/positions?cursor=page-2"
        return result

    monkeypatch.setattr(broker, "_call", fake_call)

    with pytest.raises(Exception, match="duplicate position symbols"):
        await broker.get_positions("agentic")


@pytest.mark.asyncio
async def test_order_pagination_aggregates_pages_and_rejects_repeated_cursor(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    calls = 0

    async def fake_call(_name, arguments):
        nonlocal calls
        calls += 1
        order_id = f"order-{calls}"
        return {
            "orders": [
                {
                    "id": order_id,
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "queued",
                    "created_at": "2026-08-11T15:00:00Z",
                    "placed_agent": "agentic",
                    "quantity": "0",
                    "dollar_based_amount": {"amount": "10"},
                }
            ],
            "next": "https://provider.invalid/orders?cursor=repeated",
        }

    monkeypatch.setattr(broker, "_call", fake_call)

    with pytest.raises(Exception, match="repeated a cursor"):
        await broker.get_orders("agentic")
    assert calls == 2


@pytest.mark.asyncio
async def test_order_pagination_aggregates_two_complete_pages(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, arguments):
        page = 2 if arguments.get("cursor") == "order-page-2" else 1
        result = {
            "orders": [
                {
                    "id": f"order-{page}",
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "queued",
                    "created_at": "2026-08-11T15:00:00Z",
                    "placed_agent": "agentic",
                    "quantity": "0",
                    "dollar_based_amount": {"amount": "10"},
                }
            ]
        }
        if page == 1:
            result["next"] = "https://provider.invalid/orders?cursor=order-page-2"
        return result

    monkeypatch.setattr(broker, "_call", fake_call)

    assert [order.order_id for order in await broker.get_orders("agentic")] == [
        "order-1",
        "order-2",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("quantity", None, "position quantity must be numeric"),
        ("quantity", "garbage", "position quantity must be numeric"),
        ("shares_available_for_sells", None, "sellable quantity must be numeric"),
        ("shares_available_for_sells", "garbage", "sellable quantity must be numeric"),
        ("symbol", "  ", "position symbol must be a nonempty string"),
    ],
)
async def test_position_parser_fails_closed_on_missing_or_malformed_identity_and_quantity(
    monkeypatch, field: str, bad_value, message: str
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    position = {
        "symbol": "TQQQ",
        "quantity": "0.2",
        "shares_available_for_sells": "0.2",
        "average_buy_price": "50.0",
    }
    position[field] = bad_value

    async def fake_call(_name, _arguments):
        return {"positions": [position]}

    monkeypatch.setattr(broker, "_call", fake_call)

    with pytest.raises(Exception, match=message):
        await broker.get_positions("agentic")


@pytest.mark.asyncio
async def test_order_without_stable_id_fails_closed(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "orders": [
                {
                    "id": "",
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "queued",
                    "created_at": "2026-08-11T15:00:00Z",
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)

    with pytest.raises(Exception, match="order id must be a nonempty string"):
        await broker.get_orders("agentic")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("get_positions", "omitted the positions field"),
        ("get_orders", "omitted the orders field"),
    ],
)
async def test_list_page_missing_documented_collection_fails_closed(
    monkeypatch, method: str, message: str
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {}

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match=message):
        await getattr(broker, method)("agentic")


@pytest.mark.asyncio
async def test_order_snapshot_requires_valid_aware_creation_timestamp(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "orders": [
                {
                    "id": "order-without-time",
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "queued",
                    "quantity": "0",
                    "dollar_based_amount": {"amount": "10"},
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="order creation timestamp"):
        await broker.get_orders("agentic")


def _market_buy_intent():
    return robinhood_mcp.OrderIntent(
        "provider-integrity-ref",
        "TQQQ",
        "buy",
        "provider integrity test",
        dollar_amount=10.0,
    )


def _review_echo(intent, **overrides):
    echo = {
        "symbol": intent.symbol,
        "side": intent.side,
        "type": intent.order_type,
        "dollar_amount": "10.00",
    }
    echo.update(overrides)
    return echo


def _review_quote(symbol: str = "TQQQ", **overrides):
    quote = {
        "symbol": symbol,
        "last_trade_price": "50.00",
        "venue_last_trade_time": "2026-08-11T15:00:00Z",
        "bid_price": "49.99",
        "venue_bid_time": "2026-08-11T14:59:59Z",
        "ask_price": "50.01",
        "venue_ask_time": "2026-08-11T15:00:00Z",
        "has_traded": True,
        "state": "active",
    }
    quote.update(overrides)
    return quote


def _placement_echo(intent, **overrides):
    echo = {
        "symbol": intent.symbol,
        "side": intent.side,
        "type": intent.order_type,
        "market_hours": intent.market_hours,
        "time_in_force": intent.time_in_force,
        "placed_agent": "agentic",
        "dollar_based_amount": {"amount": "10.00"},
    }
    echo.update(overrides)
    return echo


@pytest.mark.asyncio
@pytest.mark.parametrize("checks", [None, [], "", False])
async def test_order_review_requires_present_dict_checks(monkeypatch, checks) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    intent = _market_buy_intent()

    async def fake_call(_name, _arguments):
        return {
            **_review_echo(intent),
            "order_checks": checks,
            "quote_data": _review_quote(intent.symbol),
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="valid order_checks object"):
        await broker.review_order("agentic", intent)


@pytest.mark.asyncio
async def test_order_review_rejects_mismatched_ticket_echo(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    intent = _market_buy_intent()

    async def fake_call(_name, _arguments):
        return {
            **_review_echo(intent, symbol="SQQQ"),
            "order_checks": {},
            "quote_data": _review_quote(intent.symbol),
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="review echoed a different symbol"):
        await broker.review_order("agentic", intent)


@pytest.mark.asyncio
async def test_place_order_rejects_mismatched_ticket_echo(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    intent = _market_buy_intent()

    async def fake_call(_name, _arguments):
        return {
            "order": {
                **_placement_echo(intent, side="sell"),
                "id": "wrong-order",
                "state": "queued",
                "created_at": "2026-08-11T15:00:00Z",
            }
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="placement echoed a different side"):
        await broker.place_order("agentic", intent)


@pytest.mark.asyncio
async def test_review_schema_accepts_direct_dollar_amount_without_route_echo(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    intent = _market_buy_intent()

    async def fake_call(_name, _arguments):
        return {
            **_review_echo(intent),
            "order_checks": {},
            "quote_data": _review_quote(intent.symbol),
            "market_data_disclosure": "schema-faithful review",
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    review = await broker.review_order("agentic", intent)

    assert review.intent is intent
    assert review.checks == {}
    assert review.market_data_disclosure == "schema-faithful review"
    assert review.quote.symbol == intent.symbol
    assert review.quote.bid == pytest.approx(49.99)
    assert review.quote.ask == pytest.approx(50.01)
    assert review.quote.bid_timestamp == datetime(2026, 8, 11, 14, 59, 59, tzinfo=UTC)
    assert review.quote.ask_timestamp == datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
    assert "market_hours" not in review.raw
    assert "time_in_force" not in review.raw


@pytest.mark.asyncio
@pytest.mark.parametrize("disclosure", [False, 12, {}, []])
async def test_order_review_rejects_non_string_non_null_disclosure(
    monkeypatch, disclosure
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    intent = _market_buy_intent()

    async def fake_call(_name, _arguments):
        return {
            **_review_echo(intent),
            "order_checks": {},
            "quote_data": _review_quote(intent.symbol),
            "market_data_disclosure": disclosure,
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="must be a string or null"):
        await broker.review_order("agentic", intent)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quote_data", "message"),
    [
        (None, "omitted required quote_data"),
        ({}, "review quote has_traded"),
        (_review_quote("SQQQ"), "different symbol"),
        (_review_quote(bid_price="not-a-number"), "bid price must be numeric"),
        (_review_quote(venue_bid_time=None), "venue bid timestamp"),
        (_review_quote(venue_ask_time="not-a-time"), "venue ask timestamp"),
        (_review_quote(has_traded=False), "untraded instrument"),
        (_review_quote(has_traded="true"), "has_traded must be a boolean"),
        (_review_quote(state="inactive"), "instrument is not active"),
        (_review_quote(state=None), "listing state"),
    ],
)
async def test_order_review_rejects_missing_or_malformed_quote_data(
    monkeypatch, quote_data, message
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    intent = _market_buy_intent()

    async def fake_call(_name, _arguments):
        return {
            **_review_echo(intent),
            "order_checks": {},
            "quote_data": quote_data,
            "market_data_disclosure": None,
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match=message):
        await broker.review_order("agentic", intent)


@pytest.mark.asyncio
async def test_limit_review_schema_binds_limit_price_field(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    intent = robinhood_mcp.OrderIntent(
        "limit-review-ref",
        "TQQQ",
        "buy",
        "schema-faithful limit review",
        order_type="limit",
        quantity=1.0,
        limit_price=50.25,
    )

    async def fake_call(_name, _arguments):
        return {
            "symbol": "TQQQ",
            "side": "buy",
            "type": "limit",
            "quantity": "1",
            "limit_price": "50.25",
            "order_checks": {},
            "quote_data": _review_quote(intent.symbol),
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    review = await broker.review_order("agentic", intent)

    assert review.intent is intent
    assert review.raw["limit_price"] == "50.25"
    assert "price" not in review.raw


@pytest.mark.asyncio
async def test_limit_placement_schema_binds_price_field(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    intent = robinhood_mcp.OrderIntent(
        "limit-provider-ref",
        "TQQQ",
        "buy",
        "schema-faithful limit placement",
        order_type="limit",
        quantity=1.0,
        limit_price=50.25,
    )

    async def fake_call(_name, _arguments):
        return {
            "order": {
                **_placement_echo(
                    intent,
                    quantity="1",
                    dollar_based_amount=None,
                    price="50.25",
                ),
                "id": "limit-order",
                "state": "queued",
                "created_at": "2026-08-11T15:00:00Z",
                "ref_id": intent.ref_id,
            }
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    order = await broker.place_order("agentic", intent)

    assert order.order_id == "limit-order"
    assert order.quantity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_cancel_order_rejects_string_false_acceptance(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {"accepted": "false"}

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="acceptance must be a boolean"):
        await broker.cancel_order("agentic", "order-1")


@pytest.mark.asyncio
async def test_account_string_false_cannot_become_agentic_allowed(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "accounts": [
                {
                    "account_number": "1234",
                    "type": "cash",
                    "state": "active",
                    "agentic_allowed": "false",
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="agentic_allowed must be a boolean"):
        await broker.get_accounts()


@pytest.mark.asyncio
async def test_tradability_string_false_cannot_become_tradeable(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "results": [
                {
                    "symbol": "TQQQ",
                    "tradeable": "false",
                    "all_day_tradability": "not_tradable",
                    "extended_hours_fractional_tradability": False,
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="tradeable must be a boolean"):
        await broker.get_tradability("agentic", ["TQQQ"])


@pytest.mark.asyncio
async def test_order_parser_returns_exact_typed_provider_executions(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "orders": [
                {
                    "id": "order-1",
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "filled",
                    "quantity": "0.2",
                    "average_price": "50.00",
                    "created_at": "2026-08-11T15:00:00Z",
                    "placed_agent": "agentic",
                    "cumulative_quantity": "0.2",
                    "last_transaction_at": "2026-08-11T15:00:04Z",
                    "executions": [
                        {
                            "id": "execution-2",
                            "quantity": "0.1",
                            "price": "50.10",
                            "fees": "0.01",
                            "timestamp": "2026-08-11T15:00:04Z",
                        },
                        {
                            "id": "execution-1",
                            "quantity": "0.1",
                            "price": "49.90",
                            "fees": "0",
                            "timestamp": "2026-08-11T15:00:02Z",
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    order = (await broker.get_orders("agentic"))[0]

    assert [item.execution_id for item in order.executions] == ["execution-2", "execution-1"]
    assert order.cumulative_quantity == pytest.approx(0.2)
    assert order.executions[0].fees == pytest.approx(0.01)
    assert order.first_execution_at == datetime(2026, 8, 11, 15, 0, 2, tzinfo=UTC)
    assert order.placed_agent == "agentic"


@pytest.mark.asyncio
async def test_order_parser_requires_placed_agent_provenance(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "orders": [
                {
                    "id": "order-without-agent",
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "queued",
                    "quantity": "0",
                    "dollar_based_amount": {"amount": "10.00"},
                    "created_at": "2026-08-11T15:00:00Z",
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="order placed_agent"):
        await broker.get_orders("agentic")


@pytest.mark.asyncio
async def test_dollar_notional_order_quantity_zero_is_not_a_requested_share_quantity(
    monkeypatch,
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "orders": [
                {
                    "id": "dollar-order",
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "filled",
                    "quantity": "0",
                    "dollar_based_amount": {"amount": "10.00"},
                    "average_price": "50.00",
                    "created_at": "2026-08-11T15:00:00Z",
                    "placed_agent": "agentic",
                    "cumulative_quantity": "0.2",
                    "last_transaction_at": "2026-08-11T15:00:02Z",
                    "executions": [
                        {
                            "id": "dollar-execution",
                            "quantity": "0.2",
                            "price": "50.00",
                            "fees": "0",
                            "timestamp": "2026-08-11T15:00:02Z",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    order = (await broker.get_orders("agentic"))[0]

    assert order.quantity is None
    assert order.dollar_amount == pytest.approx(10.0)
    assert order.cumulative_quantity == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_share_order_quantity_zero_remains_fail_closed(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "orders": [
                {
                    "id": "share-order",
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "filled",
                    "quantity": "0",
                    "average_price": "50.00",
                    "created_at": "2026-08-11T15:00:00Z",
                    "placed_agent": "agentic",
                    "cumulative_quantity": "0.2",
                    "last_transaction_at": "2026-08-11T15:00:02Z",
                    "executions": [
                        {
                            "id": "share-execution",
                            "quantity": "0.2",
                            "price": "50.00",
                            "fees": "0",
                            "timestamp": "2026-08-11T15:00:02Z",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match="requested quantity must be finite and positive"):
        await broker.get_orders("agentic")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("id", "", "execution id"),
        ("quantity", "nan", "quantity must be finite"),
        ("price", "0", "price must be positive"),
        ("fees", "-0.01", "fees must be nonnegative"),
        ("timestamp", "not-a-time", "timestamp must be a timezone-aware"),
    ],
)
async def test_order_parser_fails_closed_on_malformed_execution(
    monkeypatch, field: str, bad_value: str, message: str
) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")
    execution = {
        "id": "execution-1",
        "quantity": "0.2",
        "price": "50.0",
        "fees": "0",
        "timestamp": "2026-08-11T15:00:02Z",
    }
    execution[field] = bad_value

    async def fake_call(_name, _arguments):
        return {
            "orders": [
                {
                    "id": "order-1",
                    "symbol": "TQQQ",
                    "side": "buy",
                    "state": "filled",
                    "quantity": "0.2",
                    "average_price": "50.0",
                    "created_at": "2026-08-11T15:00:00Z",
                    "placed_agent": "agentic",
                    "cumulative_quantity": "0.2",
                    "last_transaction_at": "2026-08-11T15:00:03Z",
                    "executions": [execution],
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)
    with pytest.raises(Exception, match=message):
        await broker.get_orders("agentic")

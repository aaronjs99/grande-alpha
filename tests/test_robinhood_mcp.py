from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from grande_alpha.broker import robinhood_mcp
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

    async def initialize(self) -> None:
        return None

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name=name, inputSchema={}) for name in REQUIRED_TOOLS]
        )

    async def call_tool(self, name, arguments):
        self.call_tasks.append(asyncio.current_task())
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
                        "bid_price": "100.00",
                        "ask_price": "100.02",
                        "last_trade_price": "100.01",
                        "venue_last_trade_time": None,
                        "venue_last_non_reg_trade_time": None,
                    }
                }
            ]
        }

    monkeypatch.setattr(broker, "_call", fake_call)

    assert await broker.get_quotes(["QQQ"]) == {}


@pytest.mark.asyncio
async def test_quote_uses_latest_real_venue_timestamp(monkeypatch) -> None:
    broker = RobinhoodMCPBroker("https://example.invalid/mcp")

    async def fake_call(_name, _arguments):
        return {
            "results": [
                {
                    "quote": {
                        "symbol": "QQQ",
                        "bid_price": "100.00",
                        "ask_price": "100.02",
                        "last_trade_price": "100.01",
                        "last_non_reg_trade_price": "100.03",
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

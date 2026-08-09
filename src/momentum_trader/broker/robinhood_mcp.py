from __future__ import annotations

import asyncio
import json
import webbrowser
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientMetadata

from momentum_trader.broker.base import Broker, BrokerError
from momentum_trader.broker.oauth import CredentialTokenStorage, OAuthCallbackServer
from momentum_trader.config import MCP_URL
from momentum_trader.models import (
    Account,
    BrokerOrder,
    OrderIntent,
    OrderReview,
    Portfolio,
    Position,
    Quote,
)

OPEN_STATES = {"new", "queued", "confirmed", "unconfirmed", "partially_filled", "pending_cancelled"}


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class RobinhoodMCPBroker(Broker):
    def __init__(self, server_url: str = MCP_URL) -> None:
        self.server_url = server_url
        self.storage = CredentialTokenStorage()
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: dict[str, dict[str, Any]] = {}
        self._call_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def tools(self) -> set[str]:
        return set(self._tools)

    async def connect(self) -> None:
        if self.connected:
            return
        callback = OAuthCallbackServer()
        callback.start()

        async def redirect_handler(url: str) -> None:
            await asyncio.to_thread(webbrowser.open, url, 2)

        async def callback_handler() -> tuple[str, str | None]:
            return await asyncio.to_thread(callback.wait, 300.0)

        metadata = OAuthClientMetadata.model_validate(
            {
                "client_name": "Momentum Trader",
                "redirect_uris": ["http://localhost:37654/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }
        )
        auth = OAuthClientProvider(
            server_url=self.server_url,
            client_metadata=metadata,
            storage=self.storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=300.0,
        )
        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(
                streamablehttp_client(
                    self.server_url,
                    timeout=30.0,
                    sse_read_timeout=300.0,
                    auth=auth,
                )
            )
            read_stream, write_stream, _ = streams
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            listing = await session.list_tools()
            self._tools = {
                item.name: (item.inputSchema if isinstance(item.inputSchema, dict) else {}) for item in listing.tools
            }
            required = {
                "get_accounts",
                "get_portfolio",
                "get_equity_quotes",
                "get_equity_positions",
                "get_equity_orders",
                "review_equity_order",
                "place_equity_order",
                "cancel_equity_order",
            }
            missing = sorted(required - self.tools)
            if missing:
                raise BrokerError(f"Robinhood MCP is missing required tools: {', '.join(missing)}")
            self._stack = stack
            self._session = session
        except Exception:
            await stack.aclose()
            raise
        finally:
            callback.stop()

    async def disconnect(self) -> None:
        self._session = None
        self._tools.clear()
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise BrokerError("Robinhood is not connected")
        if name not in self._tools:
            raise BrokerError(f"Robinhood tool is unavailable: {name}")
        async with self._call_lock:
            result = await self._session.call_tool(name, arguments)
        if getattr(result, "isError", False):
            message = "Robinhood tool error"
            for item in getattr(result, "content", []):
                if getattr(item, "type", "") == "text":
                    message = item.text
                    break
            raise BrokerError(message)
        payload = getattr(result, "structuredContent", None)
        if not payload:
            for item in getattr(result, "content", []):
                if getattr(item, "type", "") == "text":
                    try:
                        payload = json.loads(item.text)
                        break
                    except json.JSONDecodeError:
                        continue
        if not isinstance(payload, dict):
            raise BrokerError(f"Unexpected response from {name}")
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise BrokerError(f"Unexpected data from {name}")
        return data

    async def get_accounts(self) -> list[Account]:
        data = await self._call("get_accounts", {})
        accounts = []
        for row in data.get("accounts") or []:
            if not row:
                continue
            accounts.append(
                Account(
                    account_number=str(row.get("account_number", "")),
                    nickname=str(row.get("nickname") or row.get("brokerage_account_type") or "Account"),
                    account_type=str(row.get("type", "")),
                    agentic_allowed=bool(row.get("agentic_allowed")),
                    state=str(row.get("state", "")),
                )
            )
        return accounts

    async def get_portfolio(self, account_number: str) -> Portfolio:
        data = await self._call("get_portfolio", {"account_number": account_number})
        bp = data.get("buying_power") or {}
        return Portfolio(
            total_value=_number(data.get("total_value")),
            buying_power=_number(bp.get("buying_power")),
            cash=_number(data.get("cash")),
            currency=str(data.get("currency") or bp.get("display_currency") or "USD"),
        )

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        data = await self._call("get_equity_quotes", {"symbols": symbols})
        quotes: dict[str, Quote] = {}
        for row in data.get("results") or []:
            if not row or not row.get("quote"):
                continue
            item = row["quote"]
            regular_time = _datetime(item.get("venue_last_trade_time"))
            extended_time = _datetime(item.get("venue_last_non_reg_trade_time"))
            timestamp = max(filter(None, [regular_time, extended_time]), default=datetime.now(UTC))
            last = item.get("last_trade_price")
            if extended_time and (not regular_time or extended_time > regular_time):
                last = item.get("last_non_reg_trade_price") or last
            quote = Quote(
                symbol=str(item.get("symbol", "")).upper(),
                bid=_number(item.get("bid_price")),
                ask=_number(item.get("ask_price")),
                last=_number(last),
                timestamp=timestamp,
            )
            quotes[quote.symbol] = quote
        return quotes

    async def get_positions(self, account_number: str) -> list[Position]:
        data = await self._call("get_equity_positions", {"account_number": account_number})
        positions = []
        for row in data.get("positions") or []:
            if not row:
                continue
            quantity = _number(row.get("quantity"))
            if abs(quantity) < 1e-9:
                continue
            positions.append(
                Position(
                    symbol=str(row.get("symbol", "")).upper(),
                    quantity=quantity,
                    sellable_quantity=_number(row.get("shares_available_for_sells")),
                    average_price=(
                        _number(row.get("average_buy_price")) if row.get("average_buy_price") is not None else None
                    ),
                )
            )
        return positions

    def _parse_order(self, row: dict[str, Any]) -> BrokerOrder:
        dollar = row.get("dollar_based_amount") or {}
        return BrokerOrder(
            order_id=str(row.get("id", "")),
            symbol=str(row.get("symbol", "")).upper(),
            side=str(row.get("side", "")),
            state=str(row.get("state", "")),
            quantity=_number(row.get("quantity")) if row.get("quantity") is not None else None,
            dollar_amount=_number(dollar.get("amount")) if dollar.get("amount") is not None else None,
            average_price=_number(row.get("average_price")) if row.get("average_price") is not None else None,
            created_at=_datetime(row.get("created_at")),
            raw=row,
        )

    async def get_orders(self, account_number: str) -> list[BrokerOrder]:
        data = await self._call("get_equity_orders", {"account_number": account_number})
        return [self._parse_order(row) for row in data.get("orders") or [] if row]

    async def review_order(self, account_number: str, intent: OrderIntent) -> OrderReview:
        arguments = intent.broker_arguments(account_number)
        data = await self._call("review_equity_order", arguments)
        return OrderReview(
            intent=intent,
            market_data_disclosure=str(data.get("market_data_disclosure") or ""),
            checks=data.get("order_checks") or {},
            raw=data,
        )

    async def place_order(self, account_number: str, intent: OrderIntent) -> BrokerOrder:
        arguments = intent.broker_arguments(account_number)
        arguments["ref_id"] = intent.ref_id
        data = await self._call("place_equity_order", arguments)
        row = data.get("order")
        if not isinstance(row, dict):
            raise BrokerError("Robinhood did not return the submitted order")
        return self._parse_order(row)

    async def cancel_order(self, account_number: str, order_id: str) -> bool:
        data = await self._call(
            "cancel_equity_order", {"account_number": account_number, "order_id": order_id}
        )
        return bool(data.get("accepted"))

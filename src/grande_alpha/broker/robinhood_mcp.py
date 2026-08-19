from __future__ import annotations

import asyncio
import itertools
import json
import math
import webbrowser
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientMetadata

from grande_alpha.broker.base import Broker, BrokerError
from grande_alpha.broker.oauth import CredentialTokenStorage, OAuthCallbackServer
from grande_alpha.config import MCP_URL
from grande_alpha.models import (
    Account,
    BrokerExecution,
    BrokerOrder,
    EquityTradability,
    OrderIntent,
    OrderReview,
    Portfolio,
    Position,
    Quote,
    utc_now,
)


def _exception_details(exc: BaseException) -> str:
    """Expose actionable leaf failures hidden by AnyIO TaskGroup wrappers."""

    if isinstance(exc, BaseExceptionGroup):
        messages: list[str] = []
        for child in exc.exceptions:
            detail = _exception_details(child)
            if detail and detail not in messages:
                messages.append(detail)
        return "; ".join(messages)
    return str(exc).strip()

TOOL_PRIORITIES = {
    "cancel_equity_order": 0,
    "place_equity_order": 1,
    "review_equity_order": 2,
}
TOOL_TIMEOUT_SECONDS = {
    "cancel_equity_order": 10.0,
    "place_equity_order": 20.0,
    "review_equity_order": 15.0,
}
DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0
MAX_LIST_PAGES = 100


@dataclass
class _ToolRequest:
    name: str
    arguments: dict[str, Any]
    future: asyncio.Future[Any]
    timeout_seconds: float


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


def _required_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise BrokerError(f"Robinhood {field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BrokerError(f"Robinhood {field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise BrokerError(f"Robinhood {field} must be finite")
    return parsed


def _required_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise BrokerError(f"Robinhood {field} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrokerError(f"Robinhood {field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BrokerError(f"Robinhood {field} must be a timezone-aware timestamp")
    return parsed.astimezone(UTC)


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrokerError(f"Robinhood {field} must be a nonempty string")
    return value.strip()


def _required_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise BrokerError(f"Robinhood {field} must be a boolean")
    return value


def _next_cursor(data: dict[str, Any], *, resource: str) -> str | None:
    if any(data.get(key) for key in ("next_page_token", "next_cursor", "cursor")):
        raise BrokerError(
            f"Robinhood returned a paginated {resource} set without its exact continuation URL"
        )
    continuation = data.get("next")
    if continuation is None or continuation == "":
        if data.get("has_more") is True:
            raise BrokerError(
                f"Robinhood {resource} pagination claimed more data without a continuation URL"
            )
        return None
    if not isinstance(continuation, str):
        raise BrokerError(f"Robinhood {resource} pagination continuation must be a URL")
    values = parse_qs(urlparse(continuation).query, keep_blank_values=True).get("cursor", [])
    if len(values) != 1 or not values[0].strip():
        raise BrokerError(
            f"Robinhood {resource} pagination continuation omitted one exact cursor"
        )
    return values[0].strip()


def _require_order_identity(echo: dict[str, Any], intent: OrderIntent, *, context: str) -> None:
    for key, expected in (
        ("symbol", intent.symbol),
        ("side", intent.side),
        ("type", intent.order_type),
    ):
        if str(echo.get(key, "")).strip().lower() != str(expected).strip().lower():
            raise BrokerError(f"Robinhood {context} echoed a different {key}")


def _require_review_echo(data: dict[str, Any], intent: OrderIntent) -> None:
    """Bind a review using fields declared by the review tool response schema."""

    _require_order_identity(data, intent, context="review")
    if intent.quantity is not None:
        actual = _required_number(data.get("quantity"), field="review quantity")
        if not math.isclose(actual, float(intent.quantity), rel_tol=1e-9, abs_tol=1e-9):
            raise BrokerError("Robinhood review echoed a different quantity")
    if intent.dollar_amount is not None:
        actual = _required_number(data.get("dollar_amount"), field="review dollar amount")
        if not math.isclose(actual, float(intent.dollar_amount), rel_tol=1e-9, abs_tol=0.005):
            raise BrokerError("Robinhood review echoed a different dollar amount")
    if intent.limit_price is not None:
        actual = _required_number(data.get("limit_price"), field="review limit price")
        if not math.isclose(actual, float(intent.limit_price), rel_tol=1e-9, abs_tol=0.005):
            raise BrokerError("Robinhood review echoed a different limit price")


def _require_placement_echo(row: dict[str, Any], intent: OrderIntent) -> None:
    """Bind a placed order using fields declared by the order response schema."""

    _require_order_identity(row, intent, context="placement")
    for key, expected in (
        ("market_hours", intent.market_hours),
        ("time_in_force", intent.time_in_force),
    ):
        if str(row.get(key, "")).strip().lower() != str(expected).strip().lower():
            raise BrokerError(f"Robinhood placement echoed a different {key}")
    if intent.quantity is not None:
        actual = _required_number(row.get("quantity"), field="placement quantity")
        if not math.isclose(actual, float(intent.quantity), rel_tol=1e-9, abs_tol=1e-9):
            raise BrokerError("Robinhood placement echoed a different quantity")
    if intent.dollar_amount is not None:
        raw_dollars = row.get("dollar_based_amount")
        if isinstance(raw_dollars, dict):
            raw_dollars = raw_dollars.get("amount")
        actual = _required_number(raw_dollars, field="placement dollar amount")
        if not math.isclose(actual, float(intent.dollar_amount), rel_tol=1e-9, abs_tol=0.005):
            raise BrokerError("Robinhood placement echoed a different dollar amount")
    if intent.limit_price is not None:
        actual = _required_number(row.get("price"), field="placement price")
        if not math.isclose(actual, float(intent.limit_price), rel_tol=1e-9, abs_tol=0.005):
            raise BrokerError("Robinhood placement echoed a different price")


class RobinhoodMCPBroker(Broker):
    def __init__(self, server_url: str = MCP_URL, *, allow_interactive_auth: bool = True) -> None:
        self.server_url = server_url
        self.allow_interactive_auth = allow_interactive_auth
        self.storage = CredentialTokenStorage()
        self._tools: dict[str, dict[str, Any]] = {}
        self._requests: asyncio.PriorityQueue[tuple[int, int, _ToolRequest | None]] | None = None
        self._request_sequence = itertools.count()
        self._worker: asyncio.Task[None] | None = None
        self._connected = False
        self._accepting_calls = False
        self._lifecycle_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected and self._worker is not None and not self._worker.done()

    @property
    def tools(self) -> set[str]:
        return set(self._tools)

    def clear_credentials(self) -> None:
        if self.connected:
            raise BrokerError("Disconnect before forgetting stored broker credentials")
        self.storage.clear()

    async def connect(self) -> None:
        async with self._lifecycle_lock:
            if self.connected:
                return
            if self._worker is not None:
                await self._finish_worker()

            loop = asyncio.get_running_loop()
            ready: asyncio.Future[None] = loop.create_future()
            self._requests = asyncio.PriorityQueue()
            self._worker = asyncio.create_task(
                self._session_owner(ready),
                name="grande-alpha-robinhood-session",
            )
            try:
                await ready
            except BaseException as exc:
                if self._worker is not None and not self._worker.done():
                    self._worker.cancel()
                try:
                    await self._finish_worker()
                except BaseException:
                    # Preserve the readiness error. AnyIO may wrap the same leaf
                    # failure in a TaskGroup exception while the transport unwinds.
                    pass
                if isinstance(exc, asyncio.CancelledError):
                    raise
                details = _exception_details(exc)
                if details and details != str(exc):
                    raise BrokerError(details) from exc
                raise

    async def _session_owner(self, ready: asyncio.Future[None]) -> None:
        """Own the MCP contexts and every session call in one asyncio task.

        AnyIO transport cancel scopes must be exited by the same task that entered them.
        Public controller methods run in independent GUI timer tasks, so they communicate with
        this owner through a queue instead of touching ClientSession directly.
        """
        callback = OAuthCallbackServer()
        if self.allow_interactive_auth:
            callback.start()

        async def redirect_handler(url: str) -> None:
            if not self.allow_interactive_auth:
                raise BrokerError(
                    "Auto-shadow requires cached OAuth credentials; interactive browser consent is blocked"
                )
            await asyncio.to_thread(webbrowser.open, url, 2)

        async def callback_handler() -> tuple[str, str | None]:
            if not self.allow_interactive_auth:
                raise BrokerError(
                    "Auto-shadow requires cached OAuth credentials; OAuth callback is blocked"
                )
            return await asyncio.to_thread(callback.wait, 300.0)

        metadata = OAuthClientMetadata.model_validate(
            {
                "client_name": "GRANDE Alpha",
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
        try:
            async with AsyncExitStack() as stack:
                streams = await stack.enter_async_context(
                    streamablehttp_client(
                        self.server_url,
                        timeout=30.0,
                        sse_read_timeout=300.0,
                        # Robinhood currently rejects the optional MCP DELETE-session request with 400.
                        # Closing the authenticated HTTP transport is sufficient and avoids a false warning.
                        terminate_on_close=False,
                        auth=auth,
                    )
                )
                read_stream, write_stream, _ = streams
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                listing = await session.list_tools()
                self._tools = {
                    item.name: (item.inputSchema if isinstance(item.inputSchema, dict) else {})
                    for item in listing.tools
                }
                required = {
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
                missing = sorted(required - self.tools)
                if missing:
                    raise BrokerError(f"Robinhood MCP is missing required tools: {', '.join(missing)}")

                self._connected = True
                self._accepting_calls = True
                ready.set_result(None)
                await self._serve_requests(session)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            self._connected = False
            self._accepting_calls = False
            self._tools.clear()
            self._fail_pending_requests(BrokerError("Robinhood disconnected"))
            if self.allow_interactive_auth:
                callback.stop()

    async def _serve_requests(self, session: ClientSession) -> None:
        if self._requests is None:
            raise RuntimeError("Robinhood request queue was not initialized")
        while True:
            _priority, _sequence, request = await self._requests.get()
            if request is None:
                return
            if request.future.cancelled():
                continue
            try:
                result = await session.call_tool(
                    request.name,
                    request.arguments,
                    read_timeout_seconds=timedelta(seconds=request.timeout_seconds),
                )
            except TimeoutError:
                if not request.future.done():
                    request.future.set_exception(
                        BrokerError(
                            f"Robinhood {request.name} timed out after "
                            f"{request.timeout_seconds:.0f}s; the remote outcome is unknown"
                        )
                    )
            except Exception as exc:
                if not request.future.done():
                    request.future.set_exception(exc)
            else:
                if not request.future.done():
                    request.future.set_result(result)

    def _fail_pending_requests(self, exc: Exception) -> None:
        if self._requests is None:
            return
        while True:
            try:
                _priority, _sequence, request = self._requests.get_nowait()
            except asyncio.QueueEmpty:
                return
            if request is not None and not request.future.done():
                request.future.set_exception(exc)

    async def _finish_worker(self) -> None:
        worker = self._worker
        self._worker = None
        try:
            if worker is not None:
                await worker
        finally:
            self._requests = None
            self._connected = False
            self._accepting_calls = False
            self._tools.clear()

    async def disconnect(self) -> None:
        async with self._lifecycle_lock:
            if self._worker is None:
                self._connected = False
                self._accepting_calls = False
                self._tools.clear()
                return
            self._accepting_calls = False
            if self._requests is not None and not self._worker.done():
                await self._requests.put((-100, next(self._request_sequence), None))
            await self._finish_worker()

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.connected or not self._accepting_calls or self._requests is None:
            raise BrokerError("Robinhood is not connected")
        if name not in self._tools:
            raise BrokerError(f"Robinhood tool is unavailable: {name}")
        future = asyncio.get_running_loop().create_future()
        timeout_seconds = TOOL_TIMEOUT_SECONDS.get(name, DEFAULT_TOOL_TIMEOUT_SECONDS)
        priority = TOOL_PRIORITIES.get(name, 10)
        await self._requests.put(
            (
                priority,
                next(self._request_sequence),
                _ToolRequest(name, arguments, future, timeout_seconds),
            )
        )
        result = await future
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
            account_number = _required_text(
                row.get("account_number"), field="account number"
            )
            accounts.append(
                Account(
                    account_number=account_number,
                    nickname=str(row.get("nickname") or row.get("brokerage_account_type") or "Account"),
                    account_type=str(row.get("type", "")),
                    agentic_allowed=_required_bool(
                        row.get("agentic_allowed"), field="agentic_allowed"
                    ),
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
        if "results" not in data or not isinstance(data["results"], list):
            raise BrokerError("Robinhood quote response must contain a results array")
        quotes: dict[str, Quote] = {}
        for row in data["results"]:
            if not isinstance(row, dict) or not isinstance(row.get("quote"), dict):
                raise BrokerError("Robinhood quote result must contain a quote object")
            item = row["quote"]
            symbol = _required_text(item.get("symbol"), field="quote symbol").upper()
            if symbol in quotes:
                raise BrokerError("Robinhood returned duplicate quote symbols")
            if _required_bool(item.get("has_traded"), field=f"{symbol} has_traded") is not True:
                raise BrokerError(f"Robinhood quote for {symbol} has not traded")
            if _required_text(item.get("state"), field=f"{symbol} listing state").lower() != "active":
                raise BrokerError(f"Robinhood quote for {symbol} is not actively listed")
            bid_time = _required_datetime(
                item.get("venue_bid_time"), field=f"{symbol} venue bid timestamp"
            )
            ask_time = _required_datetime(
                item.get("venue_ask_time"), field=f"{symbol} venue ask timestamp"
            )
            regular_time = _datetime(item.get("venue_last_trade_time"))
            extended_time = _datetime(item.get("venue_last_non_reg_trade_time"))
            venue_times = [value for value in (regular_time, extended_time) if value is not None]
            # A local receive time is not evidence that a venue quote is current.  Missing
            # venue timestamps therefore make the row unusable instead of manufacturing a
            # fresh-looking quote, which is especially important for unattended shadow runs.
            if not venue_times:
                raise BrokerError(
                    f"Robinhood quote for {symbol} omitted a valid venue timestamp"
                )
            timestamp = max(venue_times)
            last = item.get("last_trade_price")
            if extended_time and (not regular_time or extended_time > regular_time):
                last = item.get("last_non_reg_trade_price") or last
            quote = Quote(
                symbol=symbol,
                bid=_required_number(item.get("bid_price"), field="quote bid price"),
                ask=_required_number(item.get("ask_price"), field="quote ask price"),
                last=_required_number(last, field="quote last price"),
                timestamp=timestamp,
                bid_timestamp=bid_time,
                ask_timestamp=ask_time,
            )
            try:
                quote.validate()
            except ValueError as exc:
                raise BrokerError(str(exc)) from exc
            quotes[quote.symbol] = quote
        return quotes

    async def get_tradability(self, account_number: str, symbols: list[str]) -> dict[str, EquityTradability]:
        data = await self._call(
            "get_equity_tradability",
            {"account_number": account_number, "symbols": symbols},
        )
        results: dict[str, EquityTradability] = {}
        for row in data.get("results") or []:
            if not row:
                continue
            item = EquityTradability(
                symbol=str(row.get("symbol", "")).upper(),
                tradeable=_required_bool(row.get("tradeable"), field="tradeable"),
                all_day_tradeable=str(row.get("all_day_tradability", "")).lower() == "tradable",
                extended_hours_fractional_tradeable=_required_bool(
                    row.get("extended_hours_fractional_tradability"),
                    field="extended_hours_fractional_tradability",
                ),
            )
            results[item.symbol] = item
        return results

    async def get_positions(self, account_number: str) -> list[Position]:
        rows: list[dict[str, Any]] = []
        arguments = {"account_number": account_number}
        seen_cursors: set[str] = set()
        for _page in range(MAX_LIST_PAGES):
            data = await self._call("get_equity_positions", arguments)
            if "positions" not in data:
                raise BrokerError("Robinhood position page omitted the positions field")
            page_rows = data["positions"]
            if page_rows is None:
                page_rows = []
            if not isinstance(page_rows, list) or any(
                not isinstance(row, dict) for row in page_rows
            ):
                raise BrokerError("Robinhood position page must contain an object array")
            rows.extend(page_rows)
            cursor = _next_cursor(data, resource="position")
            if cursor is None:
                break
            if cursor in seen_cursors:
                raise BrokerError("Robinhood position pagination repeated a cursor")
            seen_cursors.add(cursor)
            arguments = {"account_number": account_number, "cursor": cursor}
        else:
            raise BrokerError("Robinhood position pagination exceeded the bounded page limit")
        positions = []
        for row in rows:
            if not row:
                continue
            symbol = _required_text(row.get("symbol"), field="position symbol").upper()
            quantity = _required_number(row.get("quantity"), field="position quantity")
            sellable_quantity = _required_number(
                row.get("shares_available_for_sells"),
                field="position sellable quantity",
            )
            if abs(quantity) < 1e-9:
                continue
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=quantity,
                    sellable_quantity=sellable_quantity,
                    average_price=(
                        _required_number(
                            row.get("average_buy_price"), field="position average price"
                        )
                        if row.get("average_buy_price") is not None
                        else None
                    ),
                )
            )
        normalized_symbols = [position.symbol.strip().upper() for position in positions]
        if len(normalized_symbols) != len(set(normalized_symbols)):
            raise BrokerError("Robinhood returned duplicate position symbols across pages")
        return positions

    def _parse_order(self, row: dict[str, Any]) -> BrokerOrder:
        order_id = _required_text(row.get("id"), field="order id")
        symbol = _required_text(row.get("symbol"), field="order symbol").upper()
        side = _required_text(row.get("side"), field="order side").lower()
        state = _required_text(row.get("state"), field="order state").lower()
        created_at = _required_datetime(
            row.get("created_at"), field="order creation timestamp"
        )
        placed_agent = _required_text(
            row.get("placed_agent"), field="order placed_agent"
        ).lower()
        if side not in {"buy", "sell"}:
            raise BrokerError("Robinhood order side must be buy or sell")
        dollar = row.get("dollar_based_amount") or {}
        if not isinstance(dollar, dict):
            raise BrokerError("Robinhood dollar-based amount must be an object")
        dollar_amount = (
            _required_number(dollar.get("amount"), field="requested dollar amount")
            if dollar.get("amount") is not None
            else None
        )
        provider_quantity = (
            _required_number(row.get("quantity"), field="requested quantity")
            if row.get("quantity") is not None
            else None
        )
        # The provider reports quantity=0 for dollar-notional orders. Preserve
        # positive observed quantities, but normalize the non-applicable sentinel
        # so it cannot be mistaken for a requested share quantity.
        quantity = (
            None
            if dollar_amount is not None and provider_quantity == 0
            else provider_quantity
        )
        raw_executions = row.get("executions")
        cumulative_value = row.get("cumulative_quantity")
        if raw_executions is not None and not isinstance(raw_executions, list):
            raise BrokerError("Robinhood execution list must be an array")
        executions: list[BrokerExecution] = []
        for raw_execution in raw_executions or []:
            if not isinstance(raw_execution, dict):
                raise BrokerError("Robinhood execution must be an object")
            execution = BrokerExecution(
                execution_id=str(raw_execution.get("id", "")).strip(),
                quantity=_required_number(raw_execution.get("quantity"), field="execution quantity"),
                price=_required_number(raw_execution.get("price"), field="execution price"),
                fees=_required_number(raw_execution.get("fees"), field="execution fees"),
                timestamp=_required_datetime(
                    raw_execution.get("timestamp"), field="execution timestamp"
                ),
            )
            try:
                execution.validate()
            except ValueError as exc:
                raise BrokerError(str(exc)) from exc
            executions.append(execution)
        order = BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            state=state,
            quantity=quantity,
            dollar_amount=dollar_amount,
            average_price=_number(row.get("average_price")) if row.get("average_price") is not None else None,
            created_at=created_at,
            placed_agent=placed_agent,
            raw=row,
            executions=tuple(executions),
            cumulative_quantity=(
                _required_number(cumulative_value, field="cumulative execution quantity")
                if cumulative_value is not None
                else None
            ),
            last_transaction_at=(
                _required_datetime(row.get("last_transaction_at"), field="last transaction timestamp")
                if row.get("last_transaction_at") is not None
                else None
            ),
        )
        try:
            order.validate_execution_provenance(
                require_snapshot=(raw_executions is not None and cumulative_value is not None),
                observed_at=utc_now(),
            )
        except ValueError as exc:
            raise BrokerError(str(exc)) from exc
        return order

    async def get_orders(self, account_number: str) -> list[BrokerOrder]:
        rows: list[dict[str, Any]] = []
        arguments = {"account_number": account_number}
        seen_cursors: set[str] = set()
        for _page in range(MAX_LIST_PAGES):
            data = await self._call("get_equity_orders", arguments)
            if "orders" not in data:
                raise BrokerError("Robinhood order page omitted the orders field")
            page_rows = data["orders"]
            if page_rows is None:
                page_rows = []
            if not isinstance(page_rows, list) or any(
                not isinstance(row, dict) for row in page_rows
            ):
                raise BrokerError("Robinhood order page must contain an object array")
            rows.extend(page_rows)
            cursor = _next_cursor(data, resource="order")
            if cursor is None:
                break
            if cursor in seen_cursors:
                raise BrokerError("Robinhood order pagination repeated a cursor")
            seen_cursors.add(cursor)
            arguments = {"account_number": account_number, "cursor": cursor}
        else:
            raise BrokerError("Robinhood order pagination exceeded the bounded page limit")
        orders = [self._parse_order(row) for row in rows]
        if any(not order.order_id for order in orders):
            raise BrokerError("Robinhood returned an order without a stable order id")
        if len({order.order_id for order in orders}) != len(orders):
            raise BrokerError("Robinhood returned duplicate order ids")
        return orders

    async def review_order(self, account_number: str, intent: OrderIntent) -> OrderReview:
        arguments = intent.broker_arguments(account_number)
        data = await self._call("review_equity_order", arguments)
        if "order_checks" not in data or not isinstance(data["order_checks"], dict):
            raise BrokerError("Robinhood review omitted a valid order_checks object")
        _require_review_echo(data, intent)
        disclosure = data.get("market_data_disclosure")
        if disclosure is not None and not isinstance(disclosure, str):
            raise BrokerError("Robinhood market_data_disclosure must be a string or null")
        quote_data = data.get("quote_data")
        if not isinstance(quote_data, dict):
            raise BrokerError("Robinhood review omitted required quote_data")
        if _required_bool(
            quote_data.get("has_traded"), field="review quote has_traded"
        ) is not True:
            raise BrokerError("Robinhood review quote_data reports an untraded instrument")
        listing_state = _required_text(
            quote_data.get("state"), field="review quote listing state"
        ).lower()
        if listing_state != "active":
            raise BrokerError("Robinhood review quote_data instrument is not active")
        quote_symbol = _required_text(
            quote_data.get("symbol"), field="review quote symbol"
        ).upper()
        if quote_symbol != intent.symbol.strip().upper():
            raise BrokerError("Robinhood review quote_data echoed a different symbol")
        quote = Quote(
            symbol=quote_symbol,
            bid=_required_number(
                quote_data.get("bid_price"), field="review quote bid price"
            ),
            ask=_required_number(
                quote_data.get("ask_price"), field="review quote ask price"
            ),
            last=_required_number(
                quote_data.get("last_trade_price"), field="review quote last price"
            ),
            timestamp=_required_datetime(
                quote_data.get("venue_last_trade_time"),
                field="review quote last-trade timestamp",
            ),
            bid_timestamp=_required_datetime(
                quote_data.get("venue_bid_time"),
                field="review quote venue bid timestamp",
            ),
            ask_timestamp=_required_datetime(
                quote_data.get("venue_ask_time"),
                field="review quote venue ask timestamp",
            ),
        )
        try:
            quote.validate()
        except ValueError as exc:
            raise BrokerError(f"Robinhood review quote_data is invalid: {exc}") from exc
        return OrderReview(
            intent=intent,
            market_data_disclosure=disclosure,
            checks=data["order_checks"],
            quote=quote,
            raw=data,
        )

    async def place_order(self, account_number: str, intent: OrderIntent) -> BrokerOrder:
        arguments = intent.broker_arguments(account_number)
        arguments["ref_id"] = intent.ref_id
        data = await self._call("place_equity_order", arguments)
        row = data.get("order")
        if not isinstance(row, dict):
            raise BrokerError("Robinhood did not return the submitted order")
        _require_placement_echo(row, intent)
        echoed_ref = next(
            (row.get(key) for key in ("ref_id", "client_order_id", "client_id") if row.get(key)),
            None,
        )
        if echoed_ref is not None and str(echoed_ref) != intent.ref_id:
            raise BrokerError("Robinhood placement echoed a different reference id")
        return self._parse_order(row)

    async def cancel_order(self, account_number: str, order_id: str) -> bool:
        data = await self._call(
            "cancel_equity_order", {"account_number": account_number, "order_id": order_id}
        )
        accepted = data.get("accepted")
        if not isinstance(accepted, bool):
            raise BrokerError("Robinhood cancellation acceptance must be a boolean")
        return accepted

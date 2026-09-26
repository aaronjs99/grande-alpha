from __future__ import annotations

import asyncio
import itertools
from typing import Any

from grande_alpha.broker.base import Broker, BrokerError
from grande_alpha.broker.crypto import RobinhoodCrypto
from grande_alpha.broker.discovery import RobinhoodDiscovery
from grande_alpha.broker.oauth import CredentialTokenStorage
from grande_alpha.broker.robinhood_contract import (
    _datetime,
    _next_cursor,
    _number,
    _require_placement_echo,
    _require_review_echo,
    _required_bool,
    _required_datetime,
    _required_number,
    _required_text,
)
from grande_alpha.broker.robinhood_transport import RobinhoodTransport
from grande_alpha.configuration.config import MCP_URL
from grande_alpha.domain.account_models import Account, EquityTradability, Portfolio, Position
from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.crypto_models import CryptoOrder, CryptoOrderIntent, CryptoPosition, CryptoReview
from grande_alpha.domain.market_models import Quote
from grande_alpha.domain.order_models import BrokerExecution, BrokerOrder, OrderIntent, OrderReview
from grande_alpha.research.agent_models import Instrument

MAX_LIST_PAGES = 100


class RobinhoodMCPBroker(RobinhoodTransport, Broker):
    def __init__(self, server_url: str = MCP_URL, *, allow_interactive_auth: bool = True) -> None:
        self.server_url = server_url
        self.allow_interactive_auth = allow_interactive_auth
        self.storage = CredentialTokenStorage()
        self._tools: dict[str, dict[str, Any]] = {}
        self._tool_contracts: dict[str, dict[str, Any]] = {}
        self._agent_tool_contracts: dict[str, dict[str, Any]] = {}
        self._requests: asyncio.PriorityQueue[tuple[int, int, object | None]] | None = None
        self._request_sequence = itertools.count()
        self._worker: asyncio.Task[None] | None = None
        self._connected = False
        self._accepting_calls = False
        self._lifecycle_lock = asyncio.Lock()
        self._crypto = RobinhoodCrypto(
            lambda name, args: self._call(name, args), lambda: self._tools, lambda: self.get_accounts(),
        )





    async def discover_crypto(self) -> list[Instrument]:
        return await RobinhoodDiscovery(self._call, self._tools).currency_pairs()

    async def get_crypto_quotes(self, instruments: list[Instrument], *, rhs_account_number: str = "") -> dict[str, Quote]:
        return await RobinhoodDiscovery(self._call, self._tools).crypto_quotes(
            instruments, rhs_account_number=rhs_account_number,
        )

    async def discover_equities(self, scan_id: str) -> list[Instrument]:
        return await RobinhoodDiscovery(self._call, self._tools).scan(scan_id)

    async def get_scans(self) -> list[tuple[str, str]]:
        return await RobinhoodDiscovery(self._call, self._tools).scans()

    async def get_crypto_positions(self, account_number: str) -> list[CryptoPosition]:
        return await self._crypto.positions(account_number)

    async def get_crypto_orders(self, account_number: str, *, order_id: str = "") -> list[CryptoOrder]:
        return await self._crypto.orders(account_number, order_id=order_id)

    async def review_crypto_order(self, account_number: str, intent: CryptoOrderIntent) -> CryptoReview:
        return await self._crypto.preview(account_number, intent)

    async def place_crypto_order(self, review: CryptoReview) -> CryptoOrder:
        return await self._crypto.place(review)

    async def cancel_crypto_order(self, account_number: str, order_id: str) -> bool:
        return await self._crypto.cancel(account_number, order_id)










    async def get_accounts(self) -> list[Account]:
        data = await self._call("get_accounts", {})
        accounts = []
        for row in data.get("accounts") or []:
            if not row:
                continue
            account_number = _required_text(
                row.get("account_number"), field="account number"
            )
            inactive = any(
                _required_bool(row[key], field=key)
                for key in ("deactivated", "permanently_deactivated") if key in row
            )
            accounts.append(
                Account(
                    account_number=account_number,
                    nickname=str(row.get("nickname") or row.get("brokerage_account_type") or "Account"),
                    account_type=str(row.get("type", "")),
                    agentic_allowed=_required_bool(
                        row.get("agentic_allowed"), field="agentic_allowed"
                    ),
                    state="inactive" if inactive else str(row.get("state", "")),
                    rhs_account_number=(
                        _required_text(row["rhs_account_number"], field="RHS account number")
                        if row.get("rhs_account_number") not in (None, "") else ""
                    ),
                    rhc_account_number=(
                        _required_text(row["rhc_account_number"], field="linked crypto account number")
                        if row.get("rhc_account_number") not in (None, "") else ""
                    ),
                    brokerage_account_type=(
                        _required_text(row["brokerage_account_type"], field="brokerage account type")
                        if row.get("brokerage_account_type") not in (None, "") else ""
                    ),
                )
            )
        return accounts

    async def get_portfolio(self, account_number: str) -> Portfolio:
        data = await self._call("get_portfolio", {"account_number": account_number})
        bp = data.get("buying_power") or {}
        crypto_bp = data.get("crypto_buying_power")
        if crypto_bp is not None and not isinstance(crypto_bp, dict):
            raise BrokerError("Crypto buying power must be an object when available")
        return Portfolio(
            total_value=_number(data.get("total_value")),
            buying_power=_number(bp.get("buying_power")),
            cash=_number(data.get("cash")),
            currency=str(data.get("currency") or bp.get("display_currency") or "USD"),
            crypto_buying_power=(
                _required_number(crypto_bp.get("buying_power"), field="crypto buying power")
                if crypto_bp is not None else None
            ),
            crypto_value=(
                _required_number(data["crypto_value"], field="crypto holdings value")
                if data.get("crypto_value") is not None else None
            ),
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
            # fresh-looking quote, which is especially important before unattended entries.
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

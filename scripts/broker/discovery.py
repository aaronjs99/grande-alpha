"""Bounded, read-only parsing for optional Robinhood discovery capabilities.

The server's tool catalog is authoritative for arguments. Unknown contracts fail
visibly; no unsupported asset is silently routed through an equity order tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from grande_alpha.broker.base import BrokerError
from grande_alpha.domain.crypto_models import CryptoPairRules, CryptoQuote, decimal_amount
from grande_alpha.domain.models import Quote
from grande_alpha.research.agent_models import AssetClass, Instrument, parse_symbols

ReadCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
READ_TOOLS = frozenset({"get_currency_pairs", "get_crypto_quotes", "get_scans", "run_scan"})


def object_rows(data: dict, *keys: str) -> list[dict]:
    present = [key for key in keys if key in data]
    if len(present) != 1 or (data[present[0]] is not None and not isinstance(data[present[0]], list)):
        raise BrokerError(f"Discovery response requires one object array: {' / '.join(keys)}")
    rows = data[present[0]] or []
    if any(row is not None and not isinstance(row, dict) for row in rows):
        raise BrokerError("Discovery response contains a non-object row")
    return [row for row in rows if row is not None]


class RobinhoodDiscovery:
    def __init__(self, read: ReadCall, schemas: dict[str, dict]) -> None:
        self._read = read
        self._schemas = schemas

    async def _call(self, name: str, arguments: dict) -> dict:
        if name not in READ_TOOLS or name not in self._schemas:
            raise BrokerError(f"Read-only discovery tool unavailable: {name}")
        schema = self._schemas[name]
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - arguments.keys()
        if missing or any(key not in properties for key in arguments):
            raise BrokerError(f"Unsupported {name} input contract; refresh the broker adapter")
        return await self._read(name, arguments)

    async def _pages(self, name: str, arguments: dict, *keys: str) -> list[dict]:
        from grande_alpha.broker.robinhood_mcp import _next_cursor

        rows: list[dict] = []
        cursors: set[str] = set()
        for _page in range(10):
            data = await self._call(name, arguments)
            rows.extend(object_rows(data, *keys))
            if len(rows) > 2000:
                raise BrokerError("Discovery result exceeded the 2,000-row limit")
            cursor = _next_cursor(data, resource="discovery")
            if cursor is None:
                return rows
            if cursor in cursors:
                raise BrokerError("Discovery pagination repeated a cursor")
            cursors.add(cursor)
            arguments = {**arguments, "cursor": cursor}
        raise BrokerError("Discovery pagination exceeded the ten-page limit")

    async def currency_pairs(self) -> list[Instrument]:
        from grande_alpha.broker.robinhood_mcp import _required_bool, _required_text

        rows = await self._pages("get_currency_pairs", {"limit": 200}, "results")
        pairs: list[Instrument] = []
        seen: set[str] = set()
        for row in rows:
            symbol = _required_text(row.get("symbol"), field="pair symbol").upper()
            base = row.get("asset_currency") or {}
            counter = row.get("quote_currency") or {}
            if not isinstance(base, dict) or not isinstance(counter, dict):
                raise BrokerError("Currency metadata must contain currency objects")
            base_symbol, counter_symbol = base.get("code"), counter.get("code")
            if not base_symbol or not counter_symbol:
                pair = symbol.split("-")
                if len(pair) != 2:
                    raise BrokerError("Currency pair omitted its base/quote currency identity")
                base_symbol, counter_symbol = pair
            if symbol != f"{base_symbol}-{counter_symbol}".upper():
                raise BrokerError("Crypto pair symbol disagrees with its currency identities")
            if parse_symbols(symbol) != (symbol,):
                raise BrokerError("Invalid currency pair symbol")
            if str(counter_symbol).upper() != "USD":
                continue
            pair_id = _required_text(row.get("id"), field="currency pair id")
            if symbol in seen or any(pair.provider_id == pair_id for pair in pairs):
                raise BrokerError("Duplicate crypto pair identity")
            seen.add(symbol)
            overrides = row.get("tradability_by_account_type", {})
            if not isinstance(overrides, dict) or any(not isinstance(v, str) for v in overrides.values()):
                raise BrokerError("Invalid per-account crypto tradability")
            rules = CryptoPairRules(
                pair_id, symbol, _required_text(row.get("tradability"), field="pair tradability"),
                _required_bool(row.get("display_only"), field="display_only"),
                _required_bool(row.get("halted"), field="halted"),
                decimal_amount(row.get("min_order_size"), "minimum quantity", positive=True),
                decimal_amount(row.get("max_order_size"), "maximum quantity", positive=True),
                decimal_amount(row.get("min_order_quantity_increment"), "quantity increment", positive=True),
                decimal_amount(row.get("min_order_price_increment"), "price increment", positive=True),
                decimal_amount(row["min_order_quote_amount"], "minimum notional") if row.get("min_order_quote_amount") is not None else None,
                _required_bool(row.get("market_orders_only"), field="market_orders_only"),
                tuple(sorted(overrides.items())),
            )
            if rules.min_quantity > rules.max_quantity:
                raise BrokerError("Crypto pair minimum exceeds its maximum quantity")
            pairs.append(Instrument(AssetClass.CRYPTO, symbol, pair_id, "Robinhood currency pairs", rules))
        return sorted(pairs, key=lambda item: item.symbol)

    async def scans(self) -> list[tuple[str, str]]:
        from grande_alpha.broker.robinhood_mcp import _required_text

        rows = await self._pages("get_scans", {}, "scans")
        return [
            (
                _required_text(row.get("scan_id"), field="scan id"),
                _required_text(row.get("title"), field="scan title"),
            )
            for row in rows
        ]

    async def scan(self, scan_id: str) -> list[Instrument]:
        from grande_alpha.broker.robinhood_mcp import _required_text

        data = await self._call("run_scan", {"scan_id": scan_id})
        result = data.get("result")
        if not isinstance(result, dict) or result.get("scan_id") != scan_id:
            raise BrokerError("Scanner result omitted or changed the requested scan identity")
        rows = object_rows(result, "results")
        if len(rows) > 2000:
            raise BrokerError("Scanner exceeded the 2,000-row limit")
        symbols: list[str] = []
        for row in rows:
            # Saved scans can include options, indexes, futures and crypto. Never route
            # their tickers to equity quote/order tools based on a symbol alone.
            if row.get("instrument_type") != "EQUITY":
                continue
            symbol = _required_text(row.get("ticker"), field="scan ticker; enable its symbol column").upper()
            if parse_symbols(symbol) != (symbol,):
                raise BrokerError("Invalid scanner symbol")
            if symbol not in symbols:
                symbols.append(symbol)
        return [Instrument(AssetClass.EQUITY, s, source="Robinhood saved scan") for s in symbols]

    async def crypto_quotes(self, instruments: list[Instrument], *, rhs_account_number: str = "") -> dict[str, Quote]:
        from grande_alpha.broker.robinhood_mcp import (
            _required_datetime,
            _required_number,
            _required_text,
        )

        if not instruments or len(instruments) > 20:
            raise BrokerError("Crypto quote batches must contain 1–20 pairs")
        arguments = {"symbols": [item.symbol for item in instruments]}
        if rhs_account_number:
            if not rhs_account_number.isascii() or not rhs_account_number.isdigit():
                raise BrokerError("Crypto quote routing requires a numeric RHS account")
            arguments["rhs_account_number"] = rhs_account_number
        data = await self._call("get_crypto_quotes", arguments)
        rows = object_rows(data, "results")
        by_id = {item.provider_id: item for item in instruments}
        by_symbol = {item.symbol: item for item in instruments}
        if len(by_id) != len(instruments) or len(by_symbol) != len(instruments) or "" in by_id:
            raise BrokerError("Requested crypto pairs need unique provider IDs and symbols")
        quotes: dict[str, Quote] = {}
        for row in rows:
            item = row
            pair_id = _required_text(item.get("id"), field="crypto quote pair id")
            symbol = _required_text(item.get("symbol"), field="crypto quote symbol").upper()
            instrument = by_id.get(pair_id)
            if instrument is None or symbol != instrument.symbol.replace("-", ""):
                raise BrokerError("Crypto quote identity differs from the requested pair")
            if instrument.symbol in quotes:
                raise BrokerError("Duplicate crypto quote identity")
            stamp = _required_datetime(item.get("updated_at"), field="crypto quote updated_at")
            bid_at = _required_datetime(item["bid_time"], field="crypto bid time") if item.get("bid_time") is not None else None
            ask_at = _required_datetime(item["ask_time"], field="crypto ask time") if item.get("ask_time") is not None else None
            quote = CryptoQuote(
                symbol=_required_text(instrument.symbol, field="crypto symbol"),
                bid=_required_number(item.get("bid_price"), field="crypto bid"),
                ask=_required_number(item.get("ask_price"), field="crypto ask"),
                last=_required_number(item.get("mark_price"), field="crypto mark"),
                timestamp=stamp,
                bid_timestamp=bid_at,
                ask_timestamp=ask_at,
                routing=str(item.get("routing") or ""),
            )
            quote.validate()
            quotes[instrument.symbol] = quote
        if set(quotes) != set(by_symbol):
            raise BrokerError("Crypto quote response omitted requested pairs")
        return quotes

"""Bounded, read-only parsing for optional Robinhood discovery capabilities.

The server's tool catalog is authoritative for arguments. Unknown contracts fail
visibly; no unsupported asset is silently routed through an equity order tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from grande_alpha.agent_models import AssetClass, Instrument, parse_symbols
from grande_alpha.broker.base import BrokerError
from grande_alpha.models import Quote

ReadCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
READ_TOOLS = frozenset({"get_currency_pairs", "get_crypto_quotes", "get_scans", "run_scan"})


def object_rows(data: dict, *keys: str) -> list[dict]:
    present = [key for key in keys if key in data]
    if len(present) != 1 or not isinstance(data[present[0]], list):
        raise BrokerError(f"Discovery response requires one object array: {' / '.join(keys)}")
    rows = data[present[0]]
    if any(not isinstance(row, dict) for row in rows):
        raise BrokerError("Discovery response contains a non-object row")
    return rows


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
        from grande_alpha.broker.robinhood_mcp import _required_text

        rows = await self._pages("get_currency_pairs", {}, "currency_pairs", "results")
        pairs: list[Instrument] = []
        seen: set[str] = set()
        for row in rows:
            # Support the nested currency objects and explicit symbol pair formats.
            base = row.get("asset_currency") or {}
            counter = row.get("quote_currency") or {}
            if not isinstance(base, dict) or not isinstance(counter, dict):
                raise BrokerError("Currency metadata must contain currency objects")
            base_symbol, counter_symbol = base.get("code"), counter.get("code")
            if not base_symbol or not counter_symbol:
                pair = str(row.get("symbol", "")).upper().replace("/", "-").split("-")
                if len(pair) != 2:
                    raise BrokerError("Currency pair omitted its base/quote currency identity")
                base_symbol, counter_symbol = pair
            symbol = f"{base_symbol}-{counter_symbol}".upper()
            if parse_symbols(symbol) != (symbol,):
                raise BrokerError("Invalid currency pair symbol")
            if str(counter_symbol).upper() != "USD":
                continue
            if row.get("tradability") not in (None, "tradable") or row.get("tradeable") is False:
                continue
            pair_id = _required_text(row.get("id"), field="currency pair id")
            if symbol in seen or any(pair.provider_id == pair_id for pair in pairs):
                raise BrokerError("Duplicate crypto pair identity")
            seen.add(symbol)
            pairs.append(Instrument(AssetClass.CRYPTO, symbol, pair_id, "Robinhood currency pairs"))
        return sorted(pairs, key=lambda item: item.symbol)

    async def scans(self) -> list[tuple[str, str]]:
        from grande_alpha.broker.robinhood_mcp import _required_text

        rows = await self._pages("get_scans", {}, "scans", "results")
        return [
            (
                _required_text(row.get("id"), field="scan id"),
                str(row.get("title") or row.get("name") or row.get("id")),
            )
            for row in rows
        ]

    async def scan(self, scan_id: str) -> list[Instrument]:
        from grande_alpha.broker.robinhood_mcp import _required_text

        rows = await self._pages("run_scan", {"scan_id": scan_id}, "results", "instruments")
        symbols: list[str] = []
        for row in rows:
            item = row.get("instrument", row)
            if not isinstance(item, dict):
                raise BrokerError("Scanner instrument must be an object")
            symbol = _required_text(item.get("symbol"), field="scan symbol").upper()
            if parse_symbols(symbol) != (symbol,):
                raise BrokerError("Invalid scanner symbol")
            if symbol not in symbols:
                symbols.append(symbol)
        return [Instrument(AssetClass.EQUITY, s, source="Robinhood saved scan") for s in symbols]

    async def crypto_quotes(self, instruments: list[Instrument]) -> dict[str, Quote]:
        from grande_alpha.broker.robinhood_mcp import (
            _required_datetime,
            _required_number,
            _required_text,
        )

        if not instruments or len(instruments) > 20:
            raise BrokerError("Crypto quote batches must contain 1–20 pairs")
        properties = self._schemas.get("get_crypto_quotes", {}).get("properties", {})
        if "currency_pair_ids" in properties:
            arguments = {"currency_pair_ids": [item.provider_id for item in instruments]}
        elif "symbols" in properties:
            arguments = {"symbols": [item.symbol for item in instruments]}
        else:
            raise BrokerError("Unsupported get_crypto_quotes identity contract")
        data = await self._call("get_crypto_quotes", arguments)
        rows = object_rows(data, "results", "quotes")
        by_id = {item.provider_id: item for item in instruments}
        by_symbol = {item.symbol: item for item in instruments}
        quotes: dict[str, Quote] = {}
        for row in rows:
            item = row.get("quote", row)
            if not isinstance(item, dict):
                raise BrokerError("Crypto quote must be an object")
            pair_id = item.get("currency_pair_id", row.get("currency_pair_id"))
            symbol = str(item.get("symbol", "")).upper().replace("/", "-")
            instrument = by_id.get(pair_id) if pair_id is not None else by_symbol.get(symbol)
            if instrument is None or (symbol and symbol != instrument.symbol):
                raise BrokerError("Crypto quote identity differs from the requested pair")
            if instrument.symbol in quotes:
                raise BrokerError("Duplicate crypto quote identity")
            stamp = _required_datetime(item.get("updated_at"), field="crypto quote updated_at")
            bid_at = item.get("bid_timestamp")
            ask_at = item.get("ask_timestamp")
            if (bid_at is None) != (ask_at is None):
                raise BrokerError("Crypto quote omitted one book timestamp")
            quote = Quote(
                symbol=_required_text(instrument.symbol, field="crypto symbol"),
                bid=_required_number(item.get("bid_price"), field="crypto bid"),
                ask=_required_number(item.get("ask_price"), field="crypto ask"),
                last=_required_number(item.get("mark_price"), field="crypto mark"),
                timestamp=stamp,
                bid_timestamp=_required_datetime(bid_at, field="crypto bid time") if bid_at else None,
                ask_timestamp=_required_datetime(ask_at, field="crypto ask time") if ask_at else None,
            )
            quote.validate()
            quotes[instrument.symbol] = quote
        if set(quotes) != set(by_symbol):
            raise BrokerError("Crypto quote response omitted requested pairs")
        return quotes

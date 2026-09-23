from __future__ import annotations

import pytest

from grande_alpha.agent_models import AssetClass, Instrument
from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.discovery import RobinhoodDiscovery

PAIR = {
    "id": "btc-pair",
    "asset_currency": {"code": "BTC"},
    "quote_currency": {"code": "USD"},
    "tradability": "tradable",
}
QUOTE = {
    "currency_pair_id": "btc-pair",
    "bid_price": "99900",
    "ask_price": "100000",
    "mark_price": "99950",
    "updated_at": "2026-09-23T15:00:00Z",
}
SCHEMAS = {
    "get_currency_pairs": {"properties": {"cursor": {}}},
    "get_crypto_quotes": {"properties": {"currency_pair_ids": {}}, "required": ["currency_pair_ids"]},
    "get_scans": {"properties": {}},
    "run_scan": {"properties": {"scan_id": {}, "cursor": {}}, "required": ["scan_id"]},
}


@pytest.mark.asyncio
async def test_currency_pair_catalog_and_quote_identity_roundtrip():
    calls = []

    async def read(name, arguments):
        calls.append((name, arguments))
        if name == "get_currency_pairs":
            return {"currency_pairs": [PAIR, {**PAIR, "quote_currency": {"code": "EUR"}}]}
        return {"quotes": [QUOTE]}

    adapter = RobinhoodDiscovery(read, SCHEMAS)
    pairs = await adapter.currency_pairs()
    assert pairs == [Instrument(AssetClass.CRYPTO, "BTC-USD", "btc-pair", "Robinhood currency pairs")]
    quotes = await adapter.crypto_quotes(pairs)
    assert quotes["BTC-USD"].bid == 99900
    assert calls[-1] == ("get_crypto_quotes", {"currency_pair_ids": ["btc-pair"]})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"currency_pair_id": "other"},
        {"symbol": "ETH-USD"},
        {"updated_at": None},
        {"updated_at": "2026-09-23T15:00:00"},
        {"bid_price": "nan"},
        {"bid_price": "110000"},
        {"mark_price": True},
    ],
)
async def test_unproven_crypto_quote_contracts_fail(changes):
    async def read(_name, _arguments):
        return {"quotes": [{**QUOTE, **changes}]}

    with pytest.raises((BrokerError, ValueError)):
        await RobinhoodDiscovery(read, SCHEMAS).crypto_quotes(
            [Instrument(AssetClass.CRYPTO, "BTC-USD", "btc-pair")]
        )


@pytest.mark.asyncio
async def test_schema_mismatch_fails_before_network_call():
    async def read(*_args):
        raise AssertionError("Must not send guessed arguments")

    adapter = RobinhoodDiscovery(read, {"get_crypto_quotes": {"properties": {"unknown": {}}}})
    with pytest.raises(BrokerError, match="contract"):
        await adapter.crypto_quotes([Instrument(AssetClass.CRYPTO, "BTC-USD", "btc-pair")])
    with pytest.raises(BrokerError, match="unavailable"):
        await adapter._call("place_crypto_order", {})


@pytest.mark.asyncio
async def test_catalog_pagination_and_repeated_cursor_detection():
    async def read(_name, _arguments):
        return {"results": [PAIR], "next": "https://example.invalid/pairs?cursor=repeat"}

    with pytest.raises(BrokerError, match="repeated"):
        await RobinhoodDiscovery(read, SCHEMAS).currency_pairs()


@pytest.mark.asyncio
async def test_saved_scans_use_exact_id_and_return_equities():
    async def read(name, arguments):
        if name == "get_scans":
            return {"scans": [{"id": "scan-one", "title": "My scan"}]}
        assert arguments == {"scan_id": "scan-one"}
        return {"results": [{"instrument": {"symbol": "AAPL"}}]}

    adapter = RobinhoodDiscovery(read, SCHEMAS)
    assert await adapter.scans() == [("scan-one", "My scan")]
    assert (await adapter.scan("scan-one"))[0].key == "equity:AAPL"

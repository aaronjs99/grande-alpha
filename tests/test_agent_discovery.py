from __future__ import annotations

import pytest

from crypto_fixtures import NOW, PAIR, PAIR_ID, QUOTE, SCHEMAS
from grande_alpha.agent_models import AssetClass, Instrument
from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.discovery import RobinhoodDiscovery


@pytest.mark.asyncio
async def test_currency_pair_catalog_and_quote_identity_roundtrip():
    calls = []

    async def read(name, arguments):
        calls.append((name, arguments))
        if name == "get_currency_pairs":
            return {"results": [None, PAIR, {**PAIR, "symbol": "BTC-EUR", "quote_currency": {"code": "EUR"}}]}
        return {"results": [None, QUOTE]}

    adapter = RobinhoodDiscovery(read, SCHEMAS)
    pairs = await adapter.currency_pairs()
    assert len(pairs) == 1 and pairs[0].provider_id == PAIR_ID
    assert str(pairs[0].crypto_rules.quantity_increment) == "1E-8"
    quotes = await adapter.crypto_quotes(pairs, rhs_account_number="12345678")
    assert quotes["BTC-USD"].bid == 99900
    assert calls[-1] == ("get_crypto_quotes", {"symbols": ["BTC-USD"], "rhs_account_number": "12345678"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"id": "other"},
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
        return {"results": [{**QUOTE, **changes}]}

    with pytest.raises((BrokerError, ValueError)):
        await RobinhoodDiscovery(read, SCHEMAS).crypto_quotes(
            [Instrument(AssetClass.CRYPTO, "BTC-USD", PAIR_ID)]
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
            return {"scans": [{"scan_id": "scan-one", "title": "My scan"}]}
        assert arguments == {"scan_id": "scan-one"}
        return {"result": {"scan_id": "scan-one", "results": [
            {"ticker": "AAPL", "instrument_type": "EQUITY"},
            {"ticker": "AAPL", "instrument_type": "OPTION"},
            {"ticker": "BTC", "instrument_type": "CRYPTO"},
        ]}}

    adapter = RobinhoodDiscovery(read, SCHEMAS)
    assert await adapter.scans() == [("scan-one", "My scan")]
    assert [p.key for p in await adapter.scan("scan-one")] == ["equity:AAPL"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bid_time,ask_time", [(None, "2026-09-23T14:59:00Z"), ("2026-09-23T14:59:00Z", None)])
async def test_independently_optional_book_clocks_remain_visible_and_stale(bid_time, ask_time):
    async def read(*_):
        return {"results": [{**QUOTE, "bid_time": bid_time, "ask_time": ask_time, "routing": "fixture-route"}]}

    quote = (await RobinhoodDiscovery(read, SCHEMAS).crypto_quotes([Instrument(AssetClass.CRYPTO, "BTC-USD", PAIR_ID)]))["BTC-USD"]
    assert quote.age_seconds(NOW) == 60
    assert quote.routing == "fixture-route"
    assert quote.timestamp == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"scan_id": "other", "results": []}, {"scan_id": "one", "results": [{"ticker": "", "instrument_type": "EQUITY"}]}])
async def test_scan_requires_identity_and_visible_equity_ticker(result):
    async def read(*_):
        return {"result": result}

    with pytest.raises(BrokerError):
        await RobinhoodDiscovery(read, SCHEMAS).scan("one")


@pytest.mark.asyncio
async def test_catalog_keeps_restrictions_instead_of_silently_hiding_pairs():
    async def read(*_):
        return {"results": [{**PAIR, "tradability": "sell_only", "halted": True}]}

    pair = (await RobinhoodDiscovery(read, SCHEMAS).currency_pairs())[0]
    assert "halt" in pair.crypto_rules.restriction("buy")

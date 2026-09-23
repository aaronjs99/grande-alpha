from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from grande_alpha.agent_analyst import parse_decisions
from grande_alpha.agent_models import AgentSettings, AssetClass, Instrument, parse_symbols
from grande_alpha.agent_runtime import AgentRuntime
from grande_alpha.broker.discovery import RobinhoodDiscovery
from grande_alpha.crypto_models import CryptoQuote
from grande_alpha.models import Quote

NOW = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_reported_pair_halts_refresh_each_cycle_and_account_overrides_block_buys():
    from crypto_fixtures import FakeCryptoServer

    server = FakeCryptoServer()
    market = ReadMarket()
    agent = market.runtime()
    agent._crypto_pairs = RobinhoodDiscovery(server.call, server.schemas).currency_pairs
    agent._crypto_account_type = lambda: "individual"
    await agent.cycle()
    pair = server.responses["get_currency_pairs"]["results"][0]
    pair["tradability_by_account_type"] = {"individual": "sell_only"}
    market.now += timedelta(seconds=30)
    await agent.cycle()
    assert "sell_only" in agent.snapshot.decisions[-1].reason
    pair["halted"] = True
    market.now += timedelta(seconds=30)
    await agent.cycle()
    assert "halt" in agent.snapshot.decisions[-1].reason
    assert sum(name == "get_currency_pairs" for name, _ in server.calls) == 3


@pytest.mark.parametrize("changes,reason", [
    ({"bid_timestamp": NOW - timedelta(seconds=30)}, "stale"),
    ({"ask_timestamp": NOW + timedelta(seconds=3)}, "future"),
    ({"timestamp": NOW - timedelta(seconds=30), "bid_timestamp": NOW, "ask_timestamp": NOW}, "stale"),
    ({"timestamp": NOW + timedelta(seconds=3), "bid_timestamp": NOW, "ask_timestamp": NOW}, "future"),
])
def test_crypto_freshness_checks_all_available_provider_clocks(changes, reason):
    market = ReadMarket()
    quote = replace(CryptoQuote("BTC-USD", 100, 100.02, 100, NOW), **changes)
    decision = market.runtime()._inspect(Instrument(AssetClass.CRYPTO, "BTC-USD"), quote, NOW)
    assert decision.risk_status == "Blocked" and reason in decision.reason


def test_equity_midpoint_research_still_uses_book_clocks_not_old_last_trade():
    quote = Quote("AAPL", 100, 100.02, 100, NOW - timedelta(seconds=60), NOW, NOW)
    decision = ReadMarket().runtime()._inspect(Instrument(AssetClass.EQUITY, "AAPL"), quote, NOW)
    assert decision.risk_status != "Blocked"


class ReadMarket:
    def __init__(self):
        self.now = NOW
        self.price = 100.0
        self.connected = True
        self.calls = []
        self.events = []
        self.snapshots = []
        self.crypto_error = False
        self.crypto_timestamp = None
        self.spread = 0.02

    def quote(self, symbol, timestamp=None):
        return Quote(symbol, self.price, self.price + self.spread, self.price, timestamp or self.now)

    async def equities(self, symbols):
        self.calls.append(("equity", symbols))
        return {symbol: self.quote(symbol) for symbol in symbols}

    async def pairs(self):
        if self.crypto_error:
            raise ValueError("Missing crypto capability")
        return [Instrument(AssetClass.CRYPTO, "BTC-USD", "btc-id")]

    async def crypto(self, instruments):
        self.calls.append(("crypto", [item.symbol for item in instruments]))
        return {item.symbol: self.quote(item.symbol, self.crypto_timestamp) for item in instruments}

    async def scan(self, scan_id):
        self.calls.append(("scan", scan_id))
        return [Instrument(AssetClass.EQUITY, "AAPL", source="saved scan")]

    def runtime(self, analyst=None):
        return AgentRuntime(
            equity_quotes=self.equities,
            crypto_pairs=self.pairs,
            crypto_quotes=self.crypto,
            equity_scan=self.scan,
            connected=lambda: self.connected,
            changed=self.snapshots.append,
            log=lambda *a, **kw: self.events.append((a, kw)),
            clock=lambda: self.now,
            analyst=analyst,
        )


@pytest.mark.asyncio
async def test_both_markets_scan_warmup_and_propose_without_order_authority():
    market = ReadMarket()
    agent = market.runtime()
    agent.settings = AgentSettings(equity_symbols=("AAPL",), scan_id="my-scan")
    for _ in range(4):
        await agent.cycle()
        market.now += timedelta(seconds=30)
        market.price += 0.2
    assert {item.instrument.key for item in agent.snapshot.decisions} == {"equity:AAPL", "crypto:BTC-USD"}
    assert all(item.action == "buy" for item in agent.snapshot.decisions)
    assert all("Not submitted" in item.execution_status for item in agent.snapshot.decisions)
    assert agent.snapshot.cycle == 4
    assert all(call[0] in {"equity", "crypto", "scan"} for call in market.calls)
    assert len(market.events) == 4
    assert market.events[-1][1]["payload"]["decisions"][0]["analyst"] == "Rules baseline"


@pytest.mark.asyncio
async def test_crypto_continues_on_weekend_while_equities_are_blocked():
    market = ReadMarket()
    market.now = datetime(2026, 9, 26, 15, tzinfo=UTC)
    agent = market.runtime()
    for _ in range(4):
        await agent.cycle()
        market.now += timedelta(seconds=30)
        market.price += 0.2
    assert all(
        item.risk_status == "Blocked"
        for item in agent.snapshot.decisions
        if item.instrument.asset_class == AssetClass.EQUITY
    )
    crypto = [item for item in agent.snapshot.decisions if item.instrument.asset_class == AssetClass.CRYPTO]
    assert crypto[0].action == "buy"


@pytest.mark.asyncio
async def test_crypto_failure_does_not_hide_equity_results():
    market = ReadMarket()
    market.crypto_error = True
    agent = market.runtime()
    await agent.cycle()
    assert agent.snapshot.market_status["crypto"].startswith("Unavailable")
    assert len(agent.snapshot.decisions) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("offset, expected", [(-60, "stale"), (60, "future")])
async def test_stale_and_future_quotes_cannot_become_proposals(offset, expected):
    market = ReadMarket()
    market.crypto_timestamp = NOW + timedelta(seconds=offset)
    agent = market.runtime()
    await agent.cycle()
    crypto = agent.snapshot.decisions[-1]
    assert crypto.action == "hold"
    assert crypto.risk_status == "Blocked"
    assert expected in crypto.reason


@pytest.mark.asyncio
async def test_repeated_timestamp_does_not_inflate_history():
    market = ReadMarket()
    agent = market.runtime()
    for _ in range(5):
        await agent.cycle()
    assert all(item.samples == 1 for item in agent.snapshot.decisions)
    assert all("newer" in item.reason for item in agent.snapshot.decisions)


@pytest.mark.asyncio
async def test_different_spread_checks_for_equities_and_crypto():
    market = ReadMarket()
    market.spread = 0.5
    agent = market.runtime()
    await agent.cycle()
    assert agent.snapshot.decisions[0].risk_status == "Blocked"
    assert agent.snapshot.decisions[-1].risk_status == "Warming up"


@pytest.mark.asyncio
async def test_local_ai_uses_observations_and_is_rechecked_after_latency():
    market = ReadMarket()

    class SlowAI:
        async def analyze(self, model, observations):
            assert model == "local-model"
            assert all(
                set(row) == {"key", "bid", "ask", "spread_bps", "change_bps", "samples", "observations"}
                for row in observations
            )
            market.now += timedelta(seconds=20)
            return {row["key"]: ("buy", "Model proposal") for row in observations}

    agent = market.runtime(SlowAI())
    agent.settings = AgentSettings(local_ai_enabled=True, local_ai_model="local-model")
    for _ in range(4):
        await agent.cycle()
        market.now += timedelta(seconds=30)
    assert all(item.action == "hold" for item in agent.snapshot.decisions)
    assert all(item.reason == "Quote expired during analysis" for item in agent.snapshot.decisions)


@pytest.mark.asyncio
async def test_ai_failure_does_not_silently_switch_to_rules():
    market = ReadMarket()

    class BrokenAI:
        async def analyze(self, *_args):
            raise ValueError("Bad model output")

    agent = market.runtime(BrokenAI())
    agent.settings = AgentSettings(local_ai_enabled=True, local_ai_model="local-model")
    for _ in range(4):
        await agent.cycle()
        market.now += timedelta(seconds=30)
        market.price += 1
    assert all(item.action == "hold" and item.risk_status == "Blocked" for item in agent.snapshot.decisions)


@pytest.mark.asyncio
async def test_stop_cancels_inflight_scan_and_does_not_publish_late_decisions():
    market = ReadMarket()
    agent = market.runtime()
    entered = asyncio.Event()

    async def blocked(_symbols):
        entered.set()
        await asyncio.Event().wait()

    agent._equity_quotes = blocked
    agent.start(AgentSettings())
    task = agent._task
    await entered.wait()
    agent.stop()
    await task
    assert agent.snapshot.phase == "Stopped"
    assert not agent.snapshot.running
    assert not agent.snapshot.decisions
    assert not any(event[1].get("category") == "agent_cycle" for event in market.events)


@pytest.mark.asyncio
async def test_cycles_coalesce_and_disconnection_prevents_new_reads():
    market = ReadMarket()
    agent = market.runtime()
    await agent._cycle_lock.acquire()
    await agent.cycle()
    assert not market.calls
    agent._cycle_lock.release()
    market.connected = False
    await agent.cycle()
    assert not market.calls
    with pytest.raises(ValueError, match="Connect"):
        agent.start(AgentSettings())


def test_model_cannot_invent_symbols_orders_or_duplicate_decisions():
    valid = {"key": "crypto:BTC-USD", "action": "buy", "reason": "Numeric trend"}
    assert parse_decisions(json.dumps({"decisions": [valid]}), {valid["key"]})[valid["key"]][0] == "buy"
    for rows in ([{**valid, "key": "equity:BTC"}], [valid, valid], [{**valid, "quantity": 100}], []):
        with pytest.raises(ValueError):
            parse_decisions(json.dumps({"decisions": rows}), {valid["key"]})


def test_bounded_rotation_visits_every_candidate():
    items = [Instrument(AssetClass.EQUITY, f"S{i}") for i in range(41)]
    assert {i.key for cycle in range(1, 4) for i in AgentRuntime._batch(items, cycle)} == {
        i.key for i in items
    }
    assert all(len(AgentRuntime._batch(items, cycle)) == 20 for cycle in range(1, 4))


def test_agent_settings_validate_and_keep_asset_names_explicit():
    assert parse_symbols("aapl, QQQ aapl") == ("AAPL", "QQQ")
    for settings in (
        AgentSettings(interval_seconds=1),
        AgentSettings(local_ai_enabled=True),
        AgentSettings(crypto_max_spread_bps=float("nan")),
        replace(AgentSettings(), equity_symbols=("<script>",)),
    ):
        with pytest.raises(ValueError):
            settings.validate()


@pytest.mark.asyncio
async def test_market_close_during_model_inference_blocks_only_equities():
    market = ReadMarket()
    market.now = datetime(2026, 9, 23, 19, 58, 28, tzinfo=UTC)

    class AI:
        async def analyze(self, _model, observations):
            market.now += timedelta(seconds=5)
            return {row["key"]: ("buy", "Model proposal") for row in observations}

    agent = market.runtime(AI())
    agent.settings = AgentSettings(local_ai_enabled=True, local_ai_model="local-model")
    for _ in range(4):
        await agent.cycle()
        market.now += timedelta(seconds=30)
    assert all(
        d.reason == "Equity session closed during analysis" and d.action == "hold"
        for d in agent.snapshot.decisions
        if d.instrument.asset_class == AssetClass.EQUITY
    )
    assert agent.snapshot.decisions[-1].action == "buy"


@pytest.mark.asyncio
async def test_quote_arithmetic_overflow_cannot_produce_a_buy():
    market = ReadMarket()
    market.price = 1.7e308
    agent = market.runtime()
    await agent.cycle()
    assert all(d.risk_status == "Blocked" and d.action == "hold" for d in agent.snapshot.decisions)

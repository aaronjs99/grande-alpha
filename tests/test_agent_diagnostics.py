from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from grande_alpha.research.agent_models import AgentSettings, AssetClass, Instrument
from test_agent_continuous import manual_agent, shutdown
from test_agent_runtime import NOW, ReadMarket


@pytest.mark.asyncio
async def test_wide_spreads_block_but_fresh_narrow_quotes_recover_to_paper_buys_and_sells():
    market = ReadMarket()

    async def pairs():
        return [Instrument(AssetClass.CRYPTO, 'BTC-USD', 'btc'), Instrument(AssetClass.CRYPTO, 'ETH-USD', 'eth')]

    market.pairs = pairs
    market.spread = 2
    agent = await manual_agent(market, AgentSettings(equity_symbols=(), crypto_symbols=('BTC', 'ETH')))
    for index in range(8):
        market.now = NOW + timedelta(seconds=index * 20)
        await agent.cycle()
    report = agent.snapshot.diagnostics
    assert agent.paper_context()['fill_count'] == 0
    assert all(d.samples == 0 and d.risk_status == 'Blocked' for d in agent.snapshot.decisions)
    assert 'spread 1.980% · limit 1.000%' in report
    assert 'bid 100; ask 102' in report
    assert 'continuous AI is OFF' in report and 'Virtual fills: 0' in report
    assert 'rules entry threshold: >+3.960%' in report

    market.spread = 0.02
    for index in range(5):
        market.now += timedelta(seconds=20)
        market.price = 100 + index
        await agent.cycle()
        if index < 3:
            assert all(d.risk_status == 'Warming up' for d in agent.snapshot.decisions)
            assert agent.paper_context()['fill_count'] == 0
    assert agent.paper_context()['fill_count'] == 2
    for index in range(2):
        market.now += timedelta(seconds=20)
        market.price = 95 - index
        await agent.cycle()
    assert agent.paper_context()['fill_count'] == 4
    assert not agent.paper_context()['positions']
    assert {f['side'] for f in agent.paper_context()['fills']} == {'buy', 'sell'}
    assert agent.settings.crypto_max_spread_bps == 100  # Reporting never loosens a limit.
    await shutdown(agent)


@pytest.mark.asyncio
async def test_completed_report_survives_pending_reads_and_stop_but_new_run_resets_it():
    market = ReadMarket()
    agent = await manual_agent(market)
    await agent.cycle()
    report = agent.snapshot.diagnostics
    assert 'Last completed check 1' in report and 'Warm-up: 1 distinct quotes' in report
    entered, release = asyncio.Event(), asyncio.Event()
    original = agent._crypto_quotes

    async def waiting(items):
        entered.set()
        await release.wait()
        return await original(items)

    agent._crypto_quotes = waiting
    market.now += timedelta(seconds=5)
    task = asyncio.create_task(agent.cycle())
    await asyncio.wait_for(entered.wait(), 2)
    assert agent.snapshot.phase == 'Working' and agent.snapshot.diagnostics == report
    release.set()
    await task
    assert 'Last completed check 2' in agent.snapshot.diagnostics
    report = agent.snapshot.diagnostics
    await shutdown(agent)
    assert agent.snapshot.diagnostics == report and not agent.snapshot.running
    agent.start_paper(agent.settings, 'broker_quotes')
    assert not agent.snapshot.diagnostics
    task = agent._task
    agent.stop()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_report_does_not_copy_prompts_credentials_or_raw_provider_errors():
    market = ReadMarket()
    agent = await manual_agent(market, AgentSettings(research_brief='PRIVATE_PROMPT'))

    async def failed(_items):
        raise ValueError('SECRET_REQUEST_DETAILS')

    agent._crypto_quotes = failed
    await agent.cycle()
    report = agent.snapshot.diagnostics
    assert 'Crypto: Data unavailable' in report
    assert 'PRIVATE_PROMPT' not in report and 'SECRET_REQUEST_DETAILS' not in report
    assert '08:00:00 AM PDT' in report
    await shutdown(agent)


@pytest.mark.asyncio
async def test_report_distinguishes_research_only_from_paper_trading():
    market = ReadMarket()
    agent = market.runtime()
    agent.settings = replace(agent.settings, equity_symbols=())
    await agent.cycle()
    assert 'Mode: Research only' in agent.snapshot.diagnostics
    assert 'Paper trading is off' in agent.snapshot.diagnostics

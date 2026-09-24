from __future__ import annotations

import asyncio

import pytest

from grande_alpha.research.agent_models import AgentSettings
from test_agent_runtime import ReadMarket


@pytest.mark.asyncio
async def test_stock_and_crypto_reads_overlap_and_timeout_isolated(monkeypatch):
    import grande_alpha.research.agent_runtime as runtime
    market = ReadMarket()
    agent = market.runtime()
    stock_started = asyncio.Event()
    crypto_started = asyncio.Event()
    async def stuck_stock(_symbols):
        stock_started.set()
        await crypto_started.wait()
        await asyncio.Event().wait()
    original_crypto = agent._crypto_quotes
    async def crypto(instruments):
        crypto_started.set()
        await stock_started.wait()
        return await original_crypto(instruments)
    agent._equity_quotes = stuck_stock
    agent._crypto_quotes = crypto
    monkeypatch.setattr(runtime, 'MARKET_WORKER_TIMEOUT_SECONDS', 0.1)
    await agent.cycle()
    assert stock_started.is_set() and crypto_started.is_set()
    assert agent.snapshot.worker_status == {'equity': 'Unavailable', 'crypto': 'Cycle complete'}
    assert [d.instrument.key for d in agent.snapshot.decisions] == ['crypto:BTC-USD']
    assert 'timed out' in agent.snapshot.market_status['equity']


@pytest.mark.asyncio
async def test_model_workers_overlap_and_prompts_are_frozen_for_each_cycle():
    from datetime import timedelta
    market = ReadMarket()
    entered = {key: asyncio.Event() for key in ('equity', 'crypto')}
    calls = []
    class Model:
        async def analyze(self, model, rows, **prompts):
            key = rows[0]['key'].split(':')[0]
            calls.append((key, prompts))
            entered[key].set()
            if key == 'equity':
                agent.set_brief('crypto', 'New crypto brief')
            await entered['crypto' if key == 'equity' else 'equity'].wait()
            return {row['key']: ('hold', 'Research only') for row in rows}
    agent = market.runtime(Model())
    agent.settings = AgentSettings(local_ai_enabled=True, local_ai_model='fixture', research_brief='Team', equity_brief='Stocks', crypto_brief='Original crypto')
    for _ in range(3):
        await agent.cycle()
        market.now += timedelta(seconds=30)
    await asyncio.wait_for(agent.cycle(), 1)
    assert dict(calls)['crypto'] == {'research_brief': 'Team', 'market_brief': 'Original crypto'}
    assert dict(calls)['equity'] == {'research_brief': 'Team', 'market_brief': 'Stocks'}
    assert agent.settings.crypto_brief == 'New crypto brief'


@pytest.mark.asyncio
async def test_stop_joins_both_workers_and_prevents_late_replies():
    market = ReadMarket()
    agent = market.runtime()
    entered = [asyncio.Event(), asyncio.Event()]
    cancelled = []
    def waiting(index):
        async def read(_items):
            entered[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(index)
        return read
    agent._equity_quotes = waiting(0)
    agent._crypto_quotes = waiting(1)
    agent.start(AgentSettings())
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
    task = agent._task
    agent.stop()
    await task
    assert sorted(cancelled) == [0, 1]
    assert not agent.snapshot.decisions
    assert agent.snapshot.phase == 'Stopped'

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from test_agent_paper import decision
from test_agent_runtime import NOW, ReadMarket

from grande_alpha.agent_models import AgentSettings, AssetClass, Instrument


async def manual_agent(market, settings=None, analyst=None):
    agent = market.runtime(analyst)
    agent.start_paper(settings or AgentSettings(equity_symbols=('AAPL',), crypto_symbols=('BTC',)), 'broker_quotes')
    task, agent._task = agent._task, None  # Take ownership to advance provider clocks explicitly.
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return agent


async def shutdown(agent):
    pending = tuple(agent._background_tasks)
    agent.stop()
    await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_paper_loop_has_no_demo_cycle_limit_and_stop_freezes_updates(monkeypatch):
    market = ReadMarket()
    agent = market.runtime()
    reached = asyncio.Event()
    original = agent._changed

    def changed(snapshot):
        original(snapshot)
        if snapshot.phase == 'Waiting' and snapshot.cycle >= 30:
            reached.set()

    agent._changed = changed
    monkeypatch.setattr(agent, '_poll_delay', lambda _elapsed, _failures: 0)
    agent.start_paper(AgentSettings(equity_symbols=('AAPL',), crypto_symbols=('BTC',)), 'broker_quotes')
    await asyncio.wait_for(reached.wait(), 3)
    assert agent.snapshot.running and agent.snapshot.cycle >= 30
    task = agent._task
    agent.stop()
    count = len(market.calls)
    await asyncio.gather(task, return_exceptions=True)
    assert len(market.calls) == count and not agent.snapshot.running


@pytest.mark.asyncio
async def test_fast_quotes_finish_warmup_and_continue_holding_buying_and_selling():
    market = ReadMarket()
    agent = await manual_agent(market)
    for index in range(60):
        market.now = NOW + timedelta(seconds=5 * index)
        market.price = 100 + (index if index < 20 else 40 - index) * 0.2
        await agent.cycle()
        if index < 12:
            assert agent.paper_context()['fill_count'] == 0
        if index == 13:
            assert agent.paper_context()['fill_count'] == 2
            assert len(agent.paper_context()['positions']) == 2
    result = agent.paper_context()
    assert agent.snapshot.running and agent.snapshot.cycle == 60
    assert result['fill_count'] == 4 and not result['positions']
    assert {f['side'] for f in result['fills']} == {'buy', 'sell'}
    assert result['equity_history'][-1]['equity'] == result['equity']
    assert len(result['equity_history']) == 60  # Both workers at the same instant coalesce.
    await shutdown(agent)


@pytest.mark.asyncio
async def test_quote_checks_continue_while_one_analysis_per_market_is_pending():
    market = ReadMarket()
    release = asyncio.Event()
    calls = []

    class Analyst:
        async def analyze(self, _model, observations):
            calls.append(observations)
            await release.wait()
            return {o['key']: ('buy', 'Fixture analysis') for o in observations}

    agent = await manual_agent(market, AgentSettings(equity_symbols=('AAPL',), crypto_symbols=('BTC',),
                                                   local_ai_enabled=True, local_ai_model='fixture'), Analyst())
    for index in range(5):
        market.now = NOW + timedelta(seconds=15 * index)
        await asyncio.wait_for(agent.cycle(), 1)
        await asyncio.sleep(0)
    assert len(calls) == 2 and len(agent._ai_jobs) == 2
    for _ in range(2):
        market.now += timedelta(seconds=5)
        await asyncio.wait_for(agent.cycle(), 1)
    assert len(calls) == 2
    assert all(d.quote.timestamp == market.now for d in agent.snapshot.decisions)
    assert agent.paper_context()['fill_count'] == 0
    release.set()
    await asyncio.sleep(0)
    market.now += timedelta(seconds=5)
    await agent.cycle()
    assert all(d.action == 'buy' for d in agent.snapshot.decisions)
    assert agent.paper_context()['fill_count'] == 0  # Acceptance still needs a subsequent quote.
    await asyncio.sleep(0)
    market.now += timedelta(seconds=5)
    await agent.cycle()
    assert agent.paper_context()['fill_count'] == 2
    await shutdown(agent)


@pytest.mark.asyncio
@pytest.mark.parametrize('expired', [False, True])
async def test_invalid_or_expired_background_analysis_cannot_create_buys(expired):
    market = ReadMarket()
    release = asyncio.Event()

    class Analyst:
        async def analyze(self, _model, observations):
            await release.wait()
            if not expired:
                raise ValueError('Invalid fixture output')
            return {o['key']: ('buy', 'Old proposal') for o in observations}

    agent = await manual_agent(market, AgentSettings(equity_symbols=('AAPL',), crypto_symbols=('BTC',),
                                                   local_ai_enabled=True, local_ai_model='fixture'), Analyst())
    for index in range(5):
        market.now = NOW + timedelta(seconds=15 * index)
        await agent.cycle()
        await asyncio.sleep(0)
    market.now += timedelta(seconds=20 if expired else 5)
    release.set()
    await asyncio.sleep(0)
    await agent.cycle()
    assert all(d.action == 'hold' and not d.buy_allowed for d in agent.snapshot.decisions)
    assert all(d.quote.timestamp == market.now for d in agent.snapshot.decisions)
    assert all('expired' in d.reason if expired else 'unavailable' in d.reason for d in agent.snapshot.decisions)
    assert agent.paper_context()['fill_count'] == 0
    await shutdown(agent)


@pytest.mark.asyncio
async def test_fast_market_fills_before_slow_peer_finishes():
    market = ReadMarket()
    agent = await manual_agent(market)
    for index in range(5):
        market.now = NOW + timedelta(seconds=15 * index)
        market.price += 1
        await agent.cycle()
    assert agent.paper_context()['pending_count'] == 2
    filled = asyncio.Event()
    original = agent._changed

    def changed(snapshot):
        original(snapshot)
        if snapshot.paper and snapshot.paper['fill_count']:
            filled.set()

    async def slow(_instruments):
        await asyncio.Event().wait()

    agent._changed = changed
    agent._crypto_quotes = slow
    market.now += timedelta(seconds=5)
    task = asyncio.create_task(agent.cycle())
    await asyncio.wait_for(filled.wait(), 1)
    assert not task.done()
    assert agent.paper_context()['fill_count'] == 1
    assert agent.paper_context()['fills'][0]['key'] == 'equity:AAPL'
    agent.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert agent.paper_context()['fill_count'] == 1


@pytest.mark.asyncio
async def test_stop_cancels_background_models_without_late_updates():
    market = ReadMarket()
    cancelled = []

    class Analyst:
        async def analyze(self, _model, observations):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(observations[0]['key'])

    agent = await manual_agent(market, AgentSettings(equity_symbols=('AAPL',), crypto_symbols=('BTC',),
                                                   local_ai_enabled=True, local_ai_model='fixture'), Analyst())
    for index in range(5):
        market.now = NOW + timedelta(seconds=15 * index)
        await agent.cycle()
        await asyncio.sleep(0)
    assert len(agent._background_tasks) == 2
    await shutdown(agent)
    before = len(market.snapshots)
    await asyncio.sleep(0)
    assert len(cancelled) == 2 and not agent._background_tasks
    assert len(market.snapshots) == before and not agent.snapshot.analysis_status
    assert agent.paper_context()['fill_count'] == 0


def test_deadlines_skip_catchup_bursts_and_back_off_on_provider_errors():
    agent = ReadMarket().runtime()
    assert agent.settings.interval_seconds == 5
    assert agent._poll_delay(elapsed=2, failures=0) == 3
    assert agent._poll_delay(elapsed=12, failures=0) == 1
    assert agent._poll_delay(elapsed=1, failures=1) == 10
    assert agent._poll_delay(elapsed=1, failures=100) == 60


def test_open_positions_and_pending_intents_are_prioritized_without_exceeding_batch_limit():
    agent = ReadMarket().runtime()
    agent.paper.start('broker_quotes', 1000, 100)
    agent.paper.consume([decision(key='S39')], NOW, 15)
    at = NOW + timedelta(seconds=5)
    agent.paper.consume([decision(key='S39', at=at)], at, 15)
    agent.paper.consume([decision(key='S38', at=at)], at, 15)
    items = [Instrument(AssetClass.EQUITY, f'S{i}') for i in range(40)]
    for cycle in range(1, 5):
        batch = agent._paper_batch(items, cycle)
        assert len(batch) == 20 and len({i.key for i in batch}) == 20
        assert {'equity:S38', 'equity:S39'} <= {i.key for i in batch}


def test_news_recheck_preserves_a_rejected_ai_buy():
    from test_agent_sources import prepared_sources

    agent = ReadMarket().runtime()
    agent.sources = prepared_sources()
    item = replace(decision('hold'), buy_allowed=False)
    result = agent._recheck([item], AgentSettings(news_enabled=True))
    assert result[0].source_context['buy_supported']
    assert not result[0].buy_allowed


@pytest.mark.asyncio
async def test_background_refresh_uses_cached_feed_expiry_instead_of_delaying_another_ten_minutes():
    from test_agent_sources import prepared_sources

    market = ReadMarket()
    market.now = NOW + timedelta(minutes=5)
    agent = await manual_agent(market, AgentSettings(equity_symbols=('AAPL',), crypto_symbols=('BTC',), news_enabled=True))
    agent.sources = prepared_sources(clock=lambda: market.now)

    async def cached(*args, **kwargs):
        pass

    agent.sources.refresh = cached
    await agent.cycle()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert agent._source_due == NOW + timedelta(minutes=10)
    assert agent.snapshot.research_sources['items'] and not agent.snapshot.sources_loading
    await shutdown(agent)


@pytest.mark.asyncio
@pytest.mark.parametrize('before_start', [True, False])
async def test_unexpected_task_exit_clears_running_state_and_pending_paper_orders(before_start):
    market = ReadMarket()
    agent = market.runtime()
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    agent.cycle = blocked
    agent.start_paper(AgentSettings(), 'broker_quotes')
    agent.paper.consume([decision()], NOW, 15)
    assert agent.paper_context()['pending_count'] == 1
    task = agent._task
    if not before_start:
        await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert not agent.snapshot.running and agent.snapshot.phase == 'Error'
    assert 'Monitoring was interrupted' in agent.snapshot.error
    assert agent.paper_context()['pending_count'] == 0
    assert agent.paper.state['pending'] == {}
    assert agent.paper_context()['fill_count'] == 0
    assert agent.snapshot.next_cycle_at is None


@pytest.mark.asyncio
async def test_old_worker_cleanup_cannot_stop_a_restarted_session():
    agent = ReadMarket().runtime()
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    agent.cycle = blocked
    agent.start_paper(AgentSettings(), 'broker_quotes')
    await entered.wait()
    old = agent._task
    agent.stop()
    agent.start_paper(AgentSettings(), 'broker_quotes')
    new = agent._task
    await asyncio.gather(old, return_exceptions=True)
    await asyncio.sleep(0)
    assert agent.snapshot.running and not agent.snapshot.error and agent._task is new
    agent.stop()
    await asyncio.gather(new, return_exceptions=True)
    assert agent.snapshot.phase == 'Stopped' and not agent.snapshot.error

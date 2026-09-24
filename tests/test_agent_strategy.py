from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from test_agent_continuous import manual_agent, shutdown
from test_agent_paper import decision
from test_agent_runtime import NOW, ReadMarket

from grande_alpha.agent_models import AgentSettings, AssetClass, Instrument
from grande_alpha.agent_paper import PaperLedger
from grande_alpha.agent_strategy import adaptive_decision, limit_entries


async def quiet_sources(*_args, **_kwargs):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('ai', ['off', 'pending', 'invalid', 'hold'])
async def test_adaptive_entries_and_exits_continue_without_headlines_or_ai_permission(ai):
    market = ReadMarket()
    calls = []

    class Analyst:
        async def analyze(self, _model, observations):
            calls.append(observations)
            if ai == 'pending':
                await asyncio.Event().wait()
            if ai == 'invalid':
                raise ValueError('Invalid test reply')
            return {o['key']: ('hold', 'Fixture advisory hold') for o in observations}

    settings = AgentSettings(equity_symbols=('AAPL',), crypto_symbols=('BTC',), paper_strategy='adaptive',
                             news_enabled=True, local_ai_enabled=ai != 'off', local_ai_model='fixture')
    agent = await manual_agent(market, settings, Analyst())
    agent.sources.refresh = quiet_sources
    try:
        for index in range(22):
            market.now = NOW + timedelta(seconds=5 * index)
            market.price = 100 + index * .03
            await agent.cycle()
            await asyncio.sleep(0)
            if index == 12:
                assert agent.paper_context()['fill_count'] == 0
                assert agent.paper_context()['pending_count'] == 2
            if index == 13:
                assert agent.paper_context()['fill_count'] == 2
        assert all(not d.source_context['news_required'] for d in agent.snapshot.decisions)
        assert all(not d.source_context['buy_supported'] for d in agent.snapshot.decisions)
        assert 'Adaptive trend v1' in agent.snapshot.diagnostics
        assert 'missing coverage does not block' in agent.snapshot.diagnostics
        assert 'rules entry threshold' not in agent.snapshot.diagnostics
        if ai != 'off':
            assert calls and all(o['ai_role'] == 'advisory' for batch in calls for o in batch)
        # A loss is allowed to be recorded. Stops need a later eligible quote.
        market.now += timedelta(seconds=5)
        market.price = 98
        await agent.cycle()
        assert all(d.action == 'exit' for d in agent.snapshot.decisions)
        assert agent.paper_context()['fill_count'] == 2
        market.now += timedelta(seconds=5)
        await agent.cycle()
        assert agent.paper_context()['fill_count'] == 4
        assert not agent.paper_context()['positions']
        assert Decimal(agent.paper_context()['realized_pnl']) < 0
        market.now += timedelta(seconds=5)
        market.price = 101
        await agent.cycle()
        assert all('cooldown' in d.reason for d in agent.snapshot.decisions)
        assert agent.paper_context()['pending_count'] == 0
    finally:
        await shutdown(agent)


@pytest.mark.asyncio
@pytest.mark.parametrize('pattern', ['flat', 'falling', 'wide', 'stale', 'noise'])
async def test_adaptive_does_not_force_trades_when_price_or_data_is_unsuitable(pattern):
    market = ReadMarket()
    agent = await manual_agent(market, AgentSettings(equity_symbols=(), crypto_symbols=('BTC',), paper_strategy='adaptive'))
    try:
        for index in range(90):
            market.now = NOW + timedelta(seconds=index * 5)
            if pattern == 'falling':
                market.price = 100 - index * .03
            elif pattern == 'wide':
                market.price = 100 + index * .1
                market.spread = 2.5
            elif pattern == 'stale':
                market.price = 100 + index * .1
                market.crypto_timestamp = market.now - timedelta(seconds=20)
            elif pattern == 'noise':
                market.price = 100 + (.04 if index % 2 else -.04)
            await agent.cycle()
        assert agent.paper_context()['fill_count'] == agent.paper_context()['pending_count'] == 0
        assert agent.settings.crypto_max_spread_bps == 100
        assert len(agent._history['crypto:BTC-USD']) <= 121
    finally:
        await shutdown(agent)


@pytest.mark.asyncio
async def test_adaptive_limits_combined_stock_and_crypto_exposure():
    market = ReadMarket()
    settings = AgentSettings(equity_symbols=('QQQ', 'TQQQ', 'SQQQ', 'AAPL', 'MSFT', 'NVDA'),
                             crypto_symbols=('BTC',), paper_strategy='adaptive')
    agent = await manual_agent(market, settings)
    try:
        for index in range(20):
            market.now = NOW + timedelta(seconds=index * 5)
            market.price = 100 + index * .03
            await agent.cycle()
        positions = agent.paper.state['positions']
        assert 1 <= len(positions) <= 4
        assert len(set(positions) & {'equity:QQQ', 'equity:TQQQ', 'equity:SQQQ'}) <= 1
        assert sum(Decimal(p['cost']) for p in positions.values()) <= Decimal('400')
        assert any('limit' in d.reason for d in agent.snapshot.decisions)
    finally:
        await shutdown(agent)


@pytest.mark.parametrize('exit_kind', ['stop', 'target', 'trailing', 'time', 'reversal'])
def test_position_exit_rules_work_independently_of_entry_signals(exit_kind):
    book = PaperLedger()
    book.start('broker_quotes', 1000, 100)
    book.consume([decision(at=NOW, ask=100.02)], NOW, 15)
    opened = NOW + timedelta(seconds=5)
    book.consume([decision(at=opened, ask=100.02)], opened, 15)
    holding = next(iter(book.state['positions'].values()))
    bid = {'stop': 98, 'target': 103, 'trailing': 101, 'time': 100.1, 'reversal': 100.3}[exit_kind]
    now = opened + timedelta(seconds=1801 if exit_kind == 'time' else 15)
    if exit_kind == 'trailing':
        holding['peak_bid'] = '102'
    history = [(now - timedelta(seconds=(4 - i) * 15), 100.6 - i * .07) for i in range(5)]
    item = decision(at=now, bid=bid, ask=bid + .02)
    if exit_kind != 'reversal':
        item = replace(item, risk_status='Warming up')
    result = adaptive_decision(item, history, book.state, now)
    assert result.action == 'exit' and result.risk_status == 'Data checks passed'
    assert {'stop': 'stop', 'target': 'target', 'trailing': 'trailing', 'time': 'time', 'reversal': 'trend'}[exit_kind] in result.reason
    blocked = replace(item, risk_status='Blocked', reason='Quote is stale')
    assert adaptive_decision(blocked, history, book.state, now) == blocked


def test_exposure_and_drawdown_limits_include_pending_entries_without_blocking_exits():
    book = PaperLedger()
    book.start('broker_quotes', 1000, 300)
    rows = [replace(decision(), instrument=Instrument(AssetClass.EQUITY, symbol)) for symbol in ('AAPL', 'MSFT')]
    result = limit_entries(rows, book.state)
    assert result[0].action == 'buy' and result[1].action == 'hold'
    assert '40%' in result[1].reason
    book.state['max_drawdown_pct'] = '3'
    result = limit_entries([rows[0], replace(rows[1], action='exit')], book.state)
    assert result[0].action == 'hold' and 'drawdown' in result[0].reason
    assert result[1].action == 'exit'


def test_queued_exit_fills_on_valid_warmup_quote_and_costly_entry_is_rejected():
    book = PaperLedger()
    book.start('broker_quotes', 1000, 100)
    book.consume([decision(ask=100.02)], NOW, 15)
    now = NOW + timedelta(seconds=5)
    book.consume([decision(at=now, ask=100.02)], now, 15)
    now += timedelta(seconds=5)
    stop = adaptive_decision(decision(at=now, bid=98, ask=98.02), [(now, 98.01)], book.state, now)
    book.consume([stop], now, 15)
    now += timedelta(seconds=5)
    recovered = replace(decision(at=now, bid=100.1, ask=100.12), risk_status='Warming up')
    managed = adaptive_decision(recovered, [(now, 100.11)], book.state, now)
    assert managed.action == 'hold'
    book.consume([managed], now, 15)
    assert book.state['fill_count'] == 2 and not book.state['positions']
    history = [(NOW + timedelta(seconds=15 * i), 90 + i) for i in range(5)]
    costly = adaptive_decision(decision(at=now, bid=100, ask=100.95), history, book.state, now)
    assert costly.action == 'hold' and 'stop budget' in costly.reason


@pytest.mark.asyncio
async def test_headline_risks_still_block_adaptive_buys_and_legacy_keeps_coverage_rule():
    from test_agent_sources import prepared_sources

    market = ReadMarket()
    for strategy in ('adaptive', 'legacy'):
        agent = await manual_agent(market, AgentSettings(equity_symbols=('AAPL',), crypto_symbols=('BTC',),
                                                       paper_strategy=strategy, news_enabled=True))
        agent.sources = prepared_sources(clock=lambda: market.now)
        agent.sources.refresh = quiet_sources
        agent.sources._items = tuple(replace(a, title=a.title + ' fraud') for a in agent.sources._items)
        try:
            for index in range(20):
                market.now = NOW + timedelta(seconds=index * 5)
                market.price = 100 + index * .03
                await agent.cycle()
                await asyncio.sleep(0)
            assert agent.paper_context()['fill_count'] == 0
            assert all(not d.buy_allowed for d in agent.snapshot.decisions)
            agent.sources._items = ()
            market.now += timedelta(seconds=5)
            market.price += .03
            await agent.cycle()
            assert all(d.buy_allowed == (strategy == 'adaptive') for d in agent.snapshot.decisions)
        finally:
            await shutdown(agent)

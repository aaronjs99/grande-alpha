from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from dataclasses import replace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from PySide6.QtWidgets import QApplication
from test_agent_runtime import ReadMarket
from test_responsive_ui import DisabledBroker

from grande_alpha.agent_bridge import MAX_PENDING, AgentBridge, BridgeUnavailable
from grande_alpha.agent_mcp import create_server
from grande_alpha.agent_models import AgentSettings
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.models import Account, Portfolio
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow


@pytest.fixture
def desktop(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = AuditStore(tmp_path / 'desk.db')
    controller = TradingController(DisabledBroker(), AppConfig(broker_connection_enabled=True), store)
    controller.snapshot.connected = True
    controller.snapshot.account = Account('SECRET-ACCOUNT', 'PRIVATE-NAME', 'cash', True, 'active')
    controller.snapshot.portfolio = Portfolio(123.45, 100, 100)
    yield app, controller
    controller.stop_agent()
    store.close()


async def pump(controller):
    while True:
        controller._poll_agent_mcp()
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_mcp_protocol_tools_prompts_and_no_broker_write_capabilities(desktop):
    _, controller = desktop
    bridge = controller.agent_bridge
    assert not bridge.path.exists()
    server = create_server(AgentBridge(bridge.path))
    async with create_connected_server_and_client_session(server) as client:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == {
            'get_research_context', 'set_research_brief', 'configure_research_universe', 'start_research', 'stop_research'
        }
        assert (await client.call_tool('get_research_context')).isError
        assert (await client.list_prompts()).prompts[0].name == 'review_markets'
        prompt = await client.get_prompt('review_markets', {'focus': 'Compare AAPL and BTC'})
        assert 'Compare AAPL and BTC' in prompt.messages[0].content.text
        assert 'get_research_context' in prompt.messages[0].content.text
        controller.set_agent_mcp_enabled(True)
        task = asyncio.create_task(pump(controller))
        try:
            result = await client.call_tool('set_research_brief', {'brief': 'Compare spreads', 'market': 'crypto'})
            assert not result.isError
            context = await client.call_tool('get_research_context')
            assert not context.isError
            payload = json.loads(context.content[0].text)
            assert payload['briefs']['crypto'] == 'Compare spreads'
            assert not payload['orders_available']
            assert payload['mode'] == 'research_only'
            assert 'Rules are unchanged' in payload['prompt_effect']
            assert not any(secret in json.dumps(payload) for secret in ('SECRET-ACCOUNT', 'PRIVATE-NAME', '123.45'))
            assert (await client.call_tool('set_research_brief', {'brief': 'a' * 2001})).isError
            assert (await client.call_tool('set_research_brief', {'brief': 'Trade', 'market': 'execution'})).isError
            assert (await client.call_tool('configure_research_universe', {'equity_symbols': ['AAPL'], 'crypto_symbols': ['BTC']})).isError is False
            assert controller.agent.settings.equity_symbols == ('AAPL',)
            assert controller.risk.grant is None
            assert controller.agent_executor._authorize(None) is False
            controller.agent = ReadMarket().runtime()
            assert not (await client.call_tool('start_research')).isError
            running = controller.agent._task
            assert controller.agent.snapshot.running
            assert not (await client.call_tool('stop_research')).isError
            await running
            assert not controller.agent.snapshot.running
            assert bridge.session
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_revocation_discards_queued_start_and_new_session_cannot_replay(desktop):
    _, controller = desktop
    controller.set_agent_mcp_enabled(True)
    bridge = controller.agent_bridge
    old_session = bridge.session
    pending = asyncio.create_task(AgentBridge(bridge.path).request('start'))
    await asyncio.sleep(0)
    controller._stop_for_cancel('User STOP')
    assert not bridge.session
    assert not controller._agent_mcp_timer.isActive()
    assert not controller.agent.snapshot.running
    controller.set_agent_mcp_enabled(True)
    assert bridge.session != old_session
    controller._poll_agent_mcp()
    with pytest.raises(BridgeUnavailable):
        await pending
    assert not controller.agent.snapshot.running
    controller.update_config(replace(controller.config, broker_connection_enabled=False))
    assert not bridge.session


@pytest.mark.asyncio
async def test_expired_desktop_lease_and_bounded_command_queue(tmp_path):
    bridge = AgentBridge(tmp_path / 'bridge.db')
    bridge.enable()
    with sqlite3.connect(bridge.path) as db:
        db.execute('UPDATE session SET heartbeat = 0')
    with pytest.raises(BridgeUnavailable):
        await bridge.request('context')
    bridge.enable()
    requests = [asyncio.create_task(bridge.request('context')) for _ in range(MAX_PENDING)]
    await asyncio.sleep(0)
    with pytest.raises(BridgeUnavailable, match='queue is full'):
        await bridge.request('context')
    for task in requests:
        task.cancel()
    await asyncio.gather(*requests, return_exceptions=True)
    with sqlite3.connect(bridge.path) as db:
        assert db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
    bridge.disable()
    assert not bridge.session


@pytest.mark.asyncio
async def test_real_stdio_process_exposes_tools_and_reads_enabled_desktop(desktop):
    _, controller = desktop
    controller.set_agent_mcp_enabled(True)
    params = StdioServerParameters(command=sys.executable, args=['-m', 'grande_alpha.agent_mcp', '--bridge', str(controller.agent_bridge.path)])
    # Explicitly pass the test dependency path; stdio clients sanitize inherited env.
    import os
    params.env = {'PYTHONPATH': os.environ['PYTHONPATH']}
    task = asyncio.create_task(pump(controller))
    try:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool('get_research_context')
            assert not result.isError
            assert json.loads(result.content[0].text)['mode'] == 'research_only'
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_ui_prompts_copy_config_and_shutdown_revoke_without_live_authority(desktop):
    app, controller = desktop
    window = MainWindow(controller, controller.config)
    widget = window.agent_widget
    widget.prompts_toggle.setChecked(True)
    widget.briefs['equity'].setText('Inspect spread changes')
    widget.briefs['crypto'].setText('Explain volatility')
    widget._apply_briefs()
    assert controller.agent.settings.equity_brief == 'Inspect spread changes'
    assert controller.agent.settings.crypto_brief == 'Explain volatility'
    widget.equities.setText('AAPL, MSFT')
    widget._save_settings()
    assert controller.agent.settings.equity_symbols == ('AAPL', 'MSFT')
    widget._copy_mcp_config()
    config = json.loads(app.clipboard().text())['mcpServers']['grande-alpha']
    assert config['command'] == sys.executable
    assert config['args'] == ['-m', 'grande_alpha.agent_mcp', '--bridge', str(controller.agent_bridge.path)]
    widget.mcp_enabled.setChecked(True)
    assert controller.agent_bridge.session
    widget.shutdown()
    assert not controller.agent_bridge.session
    assert not widget.mcp_enabled.isChecked()
    assert controller.risk.grant is None
    window._closing_after_cleanup = True
    window.close()


@pytest.mark.asyncio
async def test_stock_and_crypto_reads_overlap_and_timeout_isolated(monkeypatch):
    import grande_alpha.agent_runtime as runtime
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

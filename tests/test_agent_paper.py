from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from test_agent_mcp import desktop as _desktop
from test_agent_mcp import pump
from test_agent_runtime import NOW, ReadMarket

from grande_alpha.agent_bridge import AgentBridge
from grande_alpha.agent_mcp import create_server
from grande_alpha.agent_models import AgentDecision, AgentSettings, AssetClass, Instrument
from grande_alpha.agent_paper import PaperLedger, validate_paper_settings
from grande_alpha.models import Quote

desktop = _desktop


def decision(side="buy", at=NOW, bid=100, ask=101, key="AAPL", status="Data checks passed"):
    return AgentDecision(Instrument(AssetClass.EQUITY, key), Quote(key, bid, ask, bid, at), side, "Test", status)


def test_next_quote_fills_spread_slippage_pnl_and_cash_conservation():
    book = PaperLedger()
    book.start("broker_quotes", 1000, 100)
    book.consume([decision()], NOW, 15)
    assert book.summary(active=True)["pending_count"] == 1
    assert book.summary()["fill_count"] == 0
    book.consume([decision()], NOW, 15)
    assert book.summary()["fill_count"] == 0
    later = NOW + timedelta(seconds=30)
    book.consume([decision(at=later)], later, 15)
    filled = book.summary()
    assert filled["fill_count"] == 1
    fill = filled["fills"][0]
    assert Decimal(fill["price"]) == Decimal("101.0505")
    quantity = Decimal(fill["quantity"])
    assert Decimal(filled["cash"]) + quantity * Decimal(fill["price"]) == 1000
    assert Decimal(filled["unrealized_pnl"]) < 0
    later += timedelta(seconds=30)
    book.consume([decision(at=later)], later, 15)
    assert book.summary()["fill_count"] == 1  # no repeated accumulation
    later += timedelta(seconds=30)
    book.consume([decision("exit", later, 110, 111)], later, 15)
    assert book.summary()["fill_count"] == 1
    later += timedelta(seconds=30)
    book.consume([decision("hold", later, 110, 111)], later, 15)
    result = book.summary()
    assert result["fill_count"] == 2 and result["positions"] == []
    assert Decimal(result["cash"]) == 1000 + Decimal(result["realized_pnl"])
    assert Decimal(result["realized_pnl"]) == quantity * (Decimal("109.945") - Decimal("101.0505"))
    assert result["total_pnl"] == result["realized_pnl"]


def test_two_workers_share_cash_without_overdraft_or_short_sells():
    book = PaperLedger()
    book.start("demo", 100, 100)
    book.consume([decision(key="AAPL"), decision(key="MSFT")], NOW, 15)
    later = NOW + timedelta(seconds=30)
    book.consume([decision(at=later, key="AAPL"), decision(at=later, key="MSFT")], later, 15)
    summary = book.summary()
    assert summary["fill_count"] == 1 and Decimal(summary["cash"]) >= 0
    later += timedelta(seconds=30)
    book.consume([decision("exit", later, key="NOT-HELD")], later, 15)
    assert book.summary()["fill_count"] == 1


@pytest.mark.parametrize("kind", ["blocked", "stale", "future", "invalid", "mismatch"])
def test_bad_data_cancels_pending_and_never_fills(kind):
    book = PaperLedger()
    book.start("broker_quotes", 1000, 100)
    book.consume([decision()], NOW, 15)
    later = NOW + timedelta(seconds=30)
    item = decision(at=later)
    if kind == "blocked":
        item = replace(item, risk_status="Blocked")
    elif kind == "stale":
        item = replace(item, quote=replace(item.quote, timestamp=NOW))
    elif kind == "future":
        item = replace(item, quote=replace(item.quote, timestamp=later + timedelta(seconds=3)))
    elif kind == "invalid":
        item = replace(item, quote=replace(item.quote, ask=float("nan")))
    else:
        item = replace(item, quote=replace(item.quote, symbol="MSFT"))
    book.consume([item], later, 15)
    assert book.summary()["fill_count"] == 0
    assert book.summary(active=True)["pending_count"] == 0
    assert book.summary()["cash"] == "1000"


def test_saved_session_reopens_without_authority_and_preserves_previous_sessions(tmp_path):
    path = tmp_path / "paper.db"
    book = PaperLedger(path)
    book.start("demo", 1000, 100)
    book.consume([decision()], NOW, 15)
    later = NOW + timedelta(seconds=30)
    book.consume([decision(at=later)], later, 15)
    original = book.summary()
    book.close()
    reopened = PaperLedger(path)
    assert reopened.summary() == original
    assert not reopened.summary()["active"]
    assert reopened._db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1
    assert reopened.summary(now=later + timedelta(seconds=20))["positions"][0]["stale"]
    reopened.start("broker_quotes", 500, 50)
    assert reopened.summary()["cash"] == "500"
    assert reopened.summary()["session_id"] != original["session_id"]
    assert reopened._db.execute("SELECT count(*) FROM paper_sessions").fetchone()[0] == 2
    reopened.close()


@pytest.mark.parametrize("capital,allocation", [(float("nan"), 100), (1000, float("inf")), (True, 1),
                                                (0, 1), (1000001, 1), (100, 101), (100, -1)])
def test_invalid_virtual_limits_are_rejected(capital, allocation):
    with pytest.raises(ValueError):
        validate_paper_settings("demo", capital, allocation)


@pytest.mark.asyncio
async def test_offline_demo_runs_both_workers_buys_and_sells_without_broker_or_model(monkeypatch):
    monkeypatch.setattr("grande_alpha.agent_runtime.DEMO_INTERVAL_SECONDS", 0)
    market = ReadMarket()
    market.connected = False

    class ForbiddenAI:
        async def analyze(self, *_args, **_kwargs):
            pytest.fail("Offline demo must never call an AI provider")

    agent = market.runtime(ForbiddenAI())
    agent.start_paper(AgentSettings(local_ai_enabled=True, local_ai_model="unused"))
    await agent._task
    assert not market.calls
    assert agent.snapshot.phase == "Demo complete"
    assert agent.snapshot.cycle == 24 and not agent.snapshot.running
    summary = agent.paper_context()
    assert summary["source"] == "demo" and not summary["active"]
    assert summary["fill_count"] == 4
    assert {f["side"] for f in summary["fills"]} == {"buy", "sell"}
    assert {f["key"].split(":")[0] for f in summary["fills"]} == {"equity", "crypto"}
    assert summary["positions"] == [] and summary["pending_count"] == 0
    assert agent.snapshot.analyst == "Demo rules baseline"
    assert "virtual" in agent.snapshot.execution_status


@pytest.mark.asyncio
async def test_stop_during_broker_read_prevents_paper_fills_and_discards_intents():
    market = ReadMarket()
    agent = market.runtime()
    entered = asyncio.Event()

    async def hanging(_symbols):
        entered.set()
        await asyncio.Event().wait()

    agent._equity_quotes = hanging
    agent.start_paper(AgentSettings(), "broker_quotes")
    task = agent._task
    await entered.wait()
    agent.paper.consume([decision()], NOW, 15)
    agent.stop()
    await task
    assert not agent.snapshot.running
    assert agent.paper_context()["fill_count"] == 0
    assert agent.paper_context()["pending_count"] == 0
    assert agent.paper_context()["cash"] == "1000"


@pytest.mark.asyncio
async def test_live_paper_requires_broker_and_does_not_replace_running_session():
    market = ReadMarket()
    agent = market.runtime()
    market.connected = False
    with pytest.raises(ValueError, match="Connect"):
        agent.start_paper(AgentSettings(), "broker_quotes")
    assert agent.paper_context() is None
    agent.start_paper(AgentSettings())
    session = agent.paper_context()["session_id"]
    task = agent._task
    with pytest.raises(ValueError, match="Stop"):
        agent.start_paper(AgentSettings())
    assert agent.paper_context()["session_id"] == session
    agent.stop()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_still_works_if_paper_journal_write_fails(monkeypatch):
    agent = ReadMarket().runtime()
    agent.start_paper(AgentSettings())
    task = agent._task
    agent.paper.consume([decision()], NOW, 15)

    def fail(_state):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(agent.paper, "_save", fail)
    agent.stop()
    await asyncio.gather(task, return_exceptions=True)
    assert not agent.snapshot.running
    assert not agent.paper.state["pending"]


@pytest.mark.asyncio
async def test_broker_paper_preserves_market_guards_then_fills_eligible_signals():
    market = ReadMarket()
    agent = market.runtime()
    agent.start_paper(AgentSettings(), "broker_quotes")
    task = agent._task
    task.cancel()  # Drive cycles deterministically instead of waiting for real time.
    await asyncio.gather(task, return_exceptions=True)
    market.now = NOW.replace(hour=23)
    market.spread = 2
    for _ in range(5):
        await agent.cycle()
        market.now += timedelta(seconds=30)
        market.price += 1
    assert all(d.risk_status == "Blocked" for d in agent.snapshot.decisions)
    assert agent.paper_context()["fill_count"] == 0
    market.now = NOW
    market.spread = 0.02
    for _ in range(5):
        await agent.cycle()
        market.now += timedelta(seconds=30)
        market.price += 1
    report = agent.paper_context()
    assert report["fill_count"] == 4
    assert len(report["positions"]) == 4
    assert Decimal(report["cash"]) >= 600
    assert market.calls
    agent.stop()


@pytest.mark.asyncio
async def test_mcp_paper_demo_without_broker_reports_virtual_results_and_stop(desktop):
    _, controller = desktop
    controller.snapshot.connected = False
    controller.set_agent_mcp_enabled(True)
    server = create_server(AgentBridge(controller.agent_bridge.path))
    task = asyncio.create_task(pump(controller))
    try:
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("start_paper_trading", {"source": "demo", "initial_cash": 500, "trade_cash": 50})
            assert not result.isError
            running = controller.agent._task
            context = await client.call_tool("get_research_context")
            payload = json.loads(context.content[0].text)
            assert payload["observation_source"] == "synthetic_demo"
            assert payload["paper"]["initial_cash"] == "500.0"
            assert not payload["orders_available"]
            assert controller.risk.grant is None
            assert controller.store.agent_ledger.records("SECRET-ACCOUNT") == []
            assert not any(s in json.dumps(payload) for s in ("SECRET-ACCOUNT", "PRIVATE-NAME", "123.45"))
            assert not (await client.call_tool("stop_research")).isError
            await asyncio.gather(running, return_exceptions=True)
            assert controller.agent_bridge.session and not controller.agent.snapshot.running
            assert (await client.call_tool("start_paper_trading", {"source": "broker_quotes"})).isError
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_ui_paper_demo_button_works_disconnected_and_results_survive_stop(desktop, monkeypatch):
    from grande_alpha.ui.agent_widget import AgentWidget

    app, controller = desktop
    controller.snapshot.connected = False
    monkeypatch.setattr("grande_alpha.agent_runtime.DEMO_INTERVAL_SECONDS", 0)
    widget = AgentWidget(controller)
    widget.update_account(controller.snapshot)
    assert widget.paper_start.isEnabled()
    widget.paper_source.setCurrentIndex(1)
    assert not widget.paper_start.isEnabled()
    widget.paper_source.setCurrentIndex(0)
    widget.paper_start.click()
    await controller.agent._task
    app.processEvents()
    assert "4 simulated fills" in widget.paper_status.text()
    assert widget.paper_fills.rowCount() == 4
    assert "VIRTUAL MONEY" in widget.mode.text()
    assert "Realized" in widget.paper_summary.text()
    assert widget.paper_start.isEnabled()
    controller.stop_agent()
    assert widget.paper_fills.rowCount() == 4
    controller.shadow_only_runtime = True
    with pytest.raises(RuntimeError, match="Scheduled"):
        controller.start_agent_paper(AgentSettings())
    widget.shutdown()
    widget.close()

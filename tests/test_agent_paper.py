from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from grande_alpha.domain.models import Quote
from grande_alpha.research.agent_models import AgentDecision, AgentSettings, AssetClass, Instrument
from grande_alpha.research.paper.ledger import PaperLedger, demo_market, demo_time, validate_paper_settings
from test_agent_runtime import NOW, ReadMarket


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
    assert result["closed_trades"] == 1 and result["wins"] == 1 and result["win_rate"] == 100
    assert len(result["equity_history"]) == 5  # repeated quote/time never adds a point
    assert result["equity_history"][-1]["equity"] == result["equity"]


def test_win_rate_counts_closed_trades_and_upgrade_reads_complete_journal(tmp_path):
    path = tmp_path / "old-paper.db"
    book = PaperLedger(path)
    book.start("demo", 1000, 100)
    book.state["slippage_bps"] = "0"  # Exact $10 gain, $10 loss and $0 outcome in this fixture.
    now = NOW
    for exit_price in (110, 90, 100):
        for side, price in (("buy", 100), ("hold", 100), ("exit", exit_price), ("hold", exit_price)):
            book.consume([decision(side, now, price, price)], now, 15)
            now += timedelta(seconds=30)
    for _ in range(2):
        book.consume([decision("buy", now, 100, 100)], now, 15)
        now += timedelta(seconds=30)
    result = book.summary()
    assert result["fill_count"] == 7 and len(result["positions"]) == 1
    assert result["closed_trades"] == 3
    assert result["wins"] == result["losses"] == result["breakeven"] == 1
    assert result["win_rate"] == pytest.approx(100 / 3)
    assert Decimal(result["return_pct"]) == 0
    assert len(result["equity_history"]) == 14
    legacy = dict(book.state)
    for key in ("wins", "losses", "breakeven", "equity_history"):
        legacy.pop(key)
    legacy["fills"] = legacy["fills"][-1:]
    with book._db:
        book._db.execute("UPDATE paper_sessions SET payload=?", (json.dumps(legacy),))
    book.close()
    reopened = PaperLedger(path)
    report = reopened.summary()
    assert report["closed_trades"] == 3 and report["win_rate"] == pytest.approx(100 / 3)
    assert report["equity_history"] == []  # Old points cannot be reconstructed truthfully.
    reopened.close()


def test_repeat_demo_prices_repeat_but_clocks_keep_advancing_through_weekdays():
    for market in AssetClass:
        _, first = demo_market(market, 1)
        _, repeated = demo_market(market, 25)
        a, b = next(iter(first.values())), next(iter(repeated.values()))
        assert a.bid == b.bid and a.timestamp < b.timestamp
    assert demo_time(599) < demo_time(600)
    assert demo_time(1800).weekday() == 0  # Friday rolls over to Monday.


@pytest.mark.asyncio
async def test_repeating_demo_runs_past_24_cycles_and_stop_freezes_results_and_events(monkeypatch):
    monkeypatch.setattr("grande_alpha.research.agent_runtime.DEMO_INTERVAL_SECONDS", 0)
    market = ReadMarket()
    market.connected = False
    agent = market.runtime()
    agent.start_paper(AgentSettings(), loop_demo=True)
    task = agent._task
    async with asyncio.timeout(5):
        while agent.snapshot.cycle < 51:
            await asyncio.sleep(0)
    agent.stop()
    frozen = agent.paper_context()
    events = agent.snapshot.team_events
    await task
    assert not market.calls and not agent.snapshot.running
    assert frozen["fill_count"] >= 8 and frozen["pending_count"] == 0
    assert agent.paper_context() == frozen and agent.snapshot.team_events == events
    assert len(events) <= 120
    assert {event["from"] for event in events} == {"NOVA", "ORIN", "VELA", "KADE", "RUNE", "ZARA"}
    assert all(event["kind"] != "FILL" or "PAPER" in event["message"] for event in events)
    assert agent.snapshot.elapsed_seconds >= 0


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
    monkeypatch.setattr("grande_alpha.research.agent_runtime.DEMO_INTERVAL_SECONDS", 0)
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
    task, agent._task = agent._task, None  # Transfer loop ownership to this deterministic driver.
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

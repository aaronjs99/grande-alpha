from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from test_agent_paper import decision
from test_agent_runtime import NOW, ReadMarket

from grande_alpha.agent_models import AgentSettings, AssetClass, Instrument
from grande_alpha.paper_experiment import ExperimentLedger, settlement_time


def quote(side="buy", step=0, price=100, crypto=True, status="Data checks passed"):
    d = decision(side, NOW + timedelta(seconds=5 * step), bid=price, ask=price, status=status)
    return replace(d, instrument=Instrument(AssetClass.CRYPTO if crypto else AssetClass.EQUITY, "AAPL"))


def feed(book, side="buy", step=0, price=100, **kwargs):
    d = quote(side, step, price, **kwargs)
    book.consume([d], d.quote.timestamp, 15)


def book_at(path=None):
    book = ExperimentLedger(path)
    book.initialize(equity_fee_bps=25, crypto_fee_bps=25, slippage_bps=5)
    return book


def test_fees_and_operating_costs_conserve_all_cash_without_double_charging(tmp_path):
    book = book_at(tmp_path / "paper.db")
    for step, side in enumerate(("buy", "hold", "exit", "hold")):
        feed(book, side, step)
    before = book.summary()
    fees = Decimal(before["fees_paid"])
    fills = before["fills"]
    assert fees == sum(Decimal(f["fee"]) for f in fills)
    assert fees > 0 and Decimal(before["trading_profit"]) < -fees
    assert not before["positions"] and len(fills) == 2
    assert Decimal(before["cash"]) == 100 + Decimal(before["realized_pnl"])
    book.add_expense("0.50", "Electricity", "receipt-1")
    book.add_expense("0.50", "Electricity", "receipt-1")
    after = book.summary()
    assert Decimal(after["net_profit"]) == Decimal(before["net_profit"]) - Decimal("0.50")
    assert after["trading_profit"] == before["trading_profit"]
    assert after["operating_expenses"] == "0.50"
    with pytest.raises(ValueError):
        book.add_expense("1", "Electricity", "receipt-1")
    ident = after["session_id"]
    book.close()
    reopened = book_at(tmp_path / "paper.db")
    assert reopened.summary()["session_id"] == ident
    assert reopened.summary()["net_profit"] == after["net_profit"]
    reopened.close()


def test_unrealized_liquidation_costs_and_loss_lock_survive_restart(tmp_path):
    book = book_at(tmp_path / "paper.db")
    feed(book, step=0)
    feed(book, "hold", 1)
    p = book.summary()["positions"][0]
    assert Decimal(p["value"]) < Decimal(p["quantity"]) * Decimal(p["bid"])
    book.add_expense("9.90", "Operating costs", "cost")
    feed(book, "hold", 2, price=90)
    assert book.state["loss_locked"] and book.state["pending"]["crypto:AAPL"]["side"] == "sell"
    feed(book, "hold", 3, price=89)
    assert not book.state["positions"]
    assert Decimal(book.summary()["net_profit"]) < -10
    book.close()
    book = book_at(tmp_path / "paper.db")
    book.set_paused(False)
    feed(book, "buy", 4)
    feed(book, "buy", 5)
    assert book.state["loss_locked"] and not book.state["pending"] and not book.state["positions"]
    assert book.summary()["loss_remaining"] == "0"
    book.close()


def test_price_recovery_does_not_restore_latched_loss_budget():
    book = book_at()
    feed(book)
    feed(book, "hold", 1)
    book.add_expense("10", "Power", "e")
    feed(book, "hold", 2, price=200)
    assert book.state["loss_locked"]
    assert book.summary()["loss_remaining"] == "0"


def test_pause_cancels_pending_entries_but_keeps_exits():
    book = book_at()
    feed(book)
    book.set_paused(True)
    feed(book, step=1)
    assert not book.state["fills"] and not book.state["pending"]
    book.set_paused(False)
    feed(book, step=2)
    feed(book, "hold", 3)
    book.set_paused(True)
    feed(book, "exit", 4)
    feed(book, "hold", 5)
    assert len(book.state["fills"]) == 2 and not book.state["positions"]


def test_equity_sale_cash_cannot_be_reused_until_next_session():
    book = book_at()
    for step, side in enumerate(("buy", "hold", "exit", "hold")):
        feed(book, side, step, crypto=False)
    summary = book.summary()
    assert Decimal(summary["cash"]) < Decimal(summary["equity"])
    assert Decimal(summary["unsettled_cash"]) > 0
    from datetime import datetime
    due = datetime.fromisoformat(book.state["unsettled"][0]["available_at"])
    book.consume([], due, 15)
    assert book.summary()["unsettled_cash"] == "0"
    assert book.summary()["cash"] == book.summary()["equity"]
    assert settlement_time(datetime.fromisoformat("2026-09-04T19:00:00+00:00")) == "2026-09-08T13:30:00+00:00"


def test_blocked_holding_prevents_new_entries_even_with_recent_old_mark():
    book = book_at()
    feed(book)
    feed(book, "hold", 1)
    blocked = quote("hold", 2, status="Blocked")
    new = decision(at=blocked.quote.timestamp, key="OTHER", bid=100, ask=100)
    book.consume([blocked, new], blocked.quote.timestamp, 15)
    assert not book.state["pending"]
    assert book.summary(now=blocked.quote.timestamp)["positions"][0]["stale"]


@pytest.mark.asyncio
async def test_runtime_resume_keeps_session_and_drops_old_pending_orders():
    import asyncio
    market = ReadMarket()
    runtime = market.runtime()
    book = book_at()
    runtime.paper = book
    feed(book)
    ident = book.state["session_id"]
    book.add_expense("1", "Expense", "e")
    runtime.resume_paper(AgentSettings(equity_symbols=("AAPL",), crypto_symbols=("BTC",), paper_strategy="adaptive"))
    task = runtime._task
    assert book.state["session_id"] == ident
    assert not book.state["pending"] and book.state["operating_expenses"] == "1"
    runtime.stop()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "-1", "0", "0.001"])
def test_bad_expenses_do_not_change_balance(value):
    book = book_at()
    before = book.summary()
    with pytest.raises(ValueError):
        book.add_expense(value, "test", "test")
    assert book.summary() == before

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from grande_alpha.agent_ledger import AgentBudget, ExecutionObservation, ExecutionTicket
from grande_alpha.storage import AuditStore

D = Decimal
NOW = datetime(2026, 9, 23, 15, tzinfo=UTC)
LIMITS = AgentBudget(D("6"), D("10"), D("12"), D("1"))


def ticket(*, market="equity", symbol="TQQQ", cash="6", side="buy", quantity="1", account="fixture"):
    crypto = market == "crypto"
    return ExecutionTicket(str(uuid.uuid4()), account, market, symbol, "pair" if crypto else "", side,
                           D(quantity) if quantity else None, D(cash), "fixture-scope", "a" * 64, "{}",
                           "crypto-uuid" if crypto else "", "123456" if crypto else "", "RHC-fixture" if crypto else "")


def observation(t, *, quantity="1", cash="5.25", terminal=True, state="filled", executions=None):
    executions = executions if executions is not None else (
        ({"id": f"fill-{t.ref_id}", "quantity": quantity, "price": "5.25", "timestamp": NOW.isoformat()},)
        if D(quantity) else ()
    )
    return ExecutionObservation(t.ref_id, f"order-{t.ref_id}", t.key, t.side, t.provider_id,
                                t.broker_account_id, D(quantity), D(cash), terminal, state, executions)


@pytest.fixture
def store(tmp_path):
    result = AuditStore(tmp_path / "agent-ledger.db")
    result.agent_ledger.save_budget("fixture", LIMITS, now=NOW)
    yield result
    result.close()


def dispatch(ledger, t, now=NOW):
    ledger.reserve(t, now=now)
    ledger.claim_dispatch(t, now=now)


def test_shared_stock_crypto_cap_and_never_dispatched_release(store):
    ledger = store.agent_ledger
    equity = ticket()
    crypto = ticket(market="crypto", symbol="BTC-USD")
    ledger.reserve(equity, now=NOW)
    with pytest.raises(ValueError, match="Combined"):
        ledger.reserve(crypto, now=NOW)
    ledger.release(equity.ref_id, now=NOW)
    ledger.reserve(crypto, now=NOW)
    assert ledger.status("fixture", now=NOW).committed_cash == D(6)
    with pytest.raises(ValueError, match="already exists"):
        ledger.reserve(equity, now=NOW)


def test_two_connections_cannot_overbook_the_same_account(tmp_path):
    first, second = AuditStore(tmp_path / "shared.db"), AuditStore(tmp_path / "shared.db")
    first.agent_ledger.save_budget("fixture", LIMITS, now=NOW)

    def reserve(args):
        ledger, t = args
        try:
            ledger.reserve(t, now=NOW)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [(first.agent_ledger, ticket()), (second.agent_ledger, ticket(market="crypto", symbol="BTC-USD"))]))
    assert sorted(results) == [False, True]
    assert first.agent_ledger.status("fixture", now=NOW).committed_cash == 6
    first.close()
    second.close()


def test_dispatch_survives_reopen_and_reference_cannot_be_released_or_reclaimed(tmp_path):
    path = tmp_path / "restart.db"
    store = AuditStore(path)
    store.agent_ledger.save_budget("fixture", LIMITS, now=NOW)
    t = ticket()
    dispatch(store.agent_ledger, t)
    store.close()
    reopened = AuditStore(path)
    ledger = reopened.agent_ledger
    assert ledger.status("fixture", now=NOW).unresolved_orders == 1
    assert ledger.status("fixture", now=NOW).committed_cash == 6
    with pytest.raises(ValueError, match="already dispatched"):
        ledger.claim_dispatch(t, now=NOW)
    with pytest.raises(ValueError, match="never-dispatched"):
        ledger.release(t.ref_id)
    reopened.close()


def test_order_not_found_never_proves_that_no_order_was_sent(store):
    ledger = store.agent_ledger
    t = ticket()
    dispatch(ledger, t)
    ledger.reconcile("fixture", [], {}, now=NOW)
    state = ledger.status("fixture", now=NOW)
    assert state.unresolved_orders == 1 and state.committed_cash == 6
    with pytest.raises(ValueError, match="unresolved"):
        ledger.reserve(ticket(symbol="SQQQ", cash="1"), now=NOW)


def test_partial_cancel_keeps_actual_cost_and_releases_only_unused_capital(store):
    ledger = store.agent_ledger
    t = ticket(quantity="2")
    dispatch(ledger, t)
    partial = observation(t, quantity="1", cash="2.75", terminal=False, state="partially_filled")
    ledger.reconcile("fixture", [partial], {t.key: D(1)}, now=NOW)
    assert ledger.status("fixture", now=NOW).committed_cash == 6
    ledger.reconcile("fixture", [partial], {t.key: D(1)}, now=NOW)
    assert ledger.inventory("fixture")[t.key] == (D(1), D("2.75"))
    ledger.reconcile("fixture", [replace(partial, terminal=True, state="canceled")], {t.key: D(1)}, now=NOW)
    status = ledger.status("fixture", now=NOW)
    assert status.committed_cash == D("2.75") and status.daily_buy_cash == 6


def test_partial_sell_fee_net_cost_basis_and_losses_are_durable(store):
    ledger = store.agent_ledger
    buy = ticket(quantity="3")
    dispatch(ledger, buy)
    bought = observation(buy, quantity="3", cash="5")
    ledger.reconcile("fixture", [bought], {buy.key: D(3)}, now=NOW)
    sell = ticket(side="sell", quantity="1", cash="2")
    dispatch(ledger, sell)
    sold = observation(sell, quantity="1", cash="0.50")
    ledger.reconcile("fixture", [bought, sold], {buy.key: D(2)}, now=NOW)
    status = ledger.status("fixture", now=NOW)
    assert status.realized_losses == D("1.166666666666666667")
    assert "loss budget" in status.block_reason
    assert ledger.inventory("fixture")[buy.key] == (D(2), D("3.333333333333333333"))
    with pytest.raises(ValueError, match="loss budget"):
        ledger.reserve(ticket(symbol="SQQQ", cash="1"), now=NOW)
    # Loss limits stop new buys, not reduction of verified holdings.
    exit_ticket = ticket(side="sell", quantity="2", cash="4")
    dispatch(ledger, exit_ticket)
    exited = observation(exit_ticket, quantity="2", cash="3")
    ledger.reconcile("fixture", [bought, sold, exited], {}, now=NOW)
    assert ledger.inventory("fixture")[buy.key] == (D(0), D(0))
    assert ledger.status("fixture", now=NOW + timedelta(days=1)).realized_losses == D("1.50")


def test_inventory_mismatch_rolls_back_fills_and_holds_budget_until_consistent(store):
    ledger = store.agent_ledger
    t = ticket()
    dispatch(ledger, t)
    filled = observation(t)
    with pytest.raises(ValueError, match="inventory disagrees"):
        ledger.reconcile("fixture", [filled], {t.key: D("0.5")}, now=NOW)
    assert ledger.inventory("fixture") == {}
    assert ledger.status("fixture", now=NOW).committed_cash == 6
    ledger.reconcile("fixture", [filled], {t.key: D(1)}, now=NOW)
    assert not ledger.status("fixture", now=NOW).block_reason


@pytest.mark.parametrize("mutation", ["quantity", "price", "missing", "reference"])
def test_changed_or_missing_execution_provenance_cannot_rewrite_history(store, mutation):
    ledger = store.agent_ledger
    t = ticket(quantity="2")
    dispatch(ledger, t)
    partial = observation(t, quantity="1", cash="3", terminal=False, state="partially_filled")
    ledger.reconcile("fixture", [partial], {t.key: D(1)}, now=NOW)
    if mutation == "missing":
        bad = replace(partial, executions=())
    elif mutation == "reference":
        bad = replace(partial, broker_account_id="different")
    else:
        fill = {**partial.executions[0], mutation: "0.5"}
        bad = replace(partial, executions=(fill,))
    with pytest.raises(ValueError):
        ledger.reconcile("fixture", [bad], {t.key: D(1)}, now=NOW)
    assert ledger.inventory("fixture")[t.key] == (D(1), D(3))
    assert ledger.status("fixture", now=NOW).unresolved_orders == 1


def test_actual_broker_overrun_records_truth_and_blocks_new_buys(store):
    ledger = store.agent_ledger
    t = ticket()
    dispatch(ledger, t)
    ledger.reconcile("fixture", [observation(t, cash="6.25")], {t.key: D(1)}, now=NOW)
    status = ledger.status("fixture", now=NOW)
    assert status.committed_cash == D("6.25") and status.daily_buy_cash == D("6.25")
    assert "exceeded the reserved" in status.block_reason


def test_false_terminal_flag_cannot_release_an_open_reservation(store):
    ledger = store.agent_ledger
    t = ticket()
    dispatch(ledger, t)
    with pytest.raises(ValueError, match="terminal status"):
        ledger.reconcile("fixture", [observation(t, quantity="0", cash="0", state="confirmed")], {}, now=NOW)
    assert ledger.status("fixture", now=NOW).committed_cash == 6


@pytest.mark.parametrize("offset", [-3, 6])
def test_execution_outside_dispatch_observation_times_cannot_establish_a_fill(store, offset):
    ledger = store.agent_ledger
    t = ticket()
    dispatch(ledger, t)
    fill = {"id": "bad-clock", "quantity": "1", "timestamp": (NOW + timedelta(seconds=offset)).isoformat()}
    with pytest.raises(ValueError, match="timestamp falls outside"):
        ledger.reconcile("fixture", [observation(t, executions=(fill,))], {t.key: D(1)}, now=NOW)
    assert ledger.inventory("fixture") == {}


def test_eastern_midnight_resets_daily_attempts_but_not_open_reservations(store):
    ledger = store.agent_ledger
    before = datetime(2026, 9, 24, 3, 59, 50, tzinfo=UTC)
    after = before + timedelta(seconds=20)
    old = ticket()
    dispatch(ledger, old, before)
    ledger.reconcile("fixture", [observation(old, quantity="0", cash="0", state="rejected")], {}, now=before)
    pending = ticket(symbol="SQQQ")
    ledger.reserve(pending, now=before)
    assert ledger.status("fixture", now=before).daily_buy_cash == 12
    assert ledger.status("fixture", now=after).daily_buy_cash == 6
    ledger.claim_dispatch(pending, now=after)
    assert ledger.status("fixture", now=after).daily_buy_cash == 6
    assert ledger.status("fixture", now=after).committed_cash == 6


def test_budget_changes_do_not_reset_usage_or_authorize_a_dispatch(store):
    ledger = store.agent_ledger
    t = ticket()
    dispatch(ledger, t)
    ledger.note_submission(t.ref_id, None, now=NOW)
    ledger.save_budget("fixture", AgentBudget(D(20), D(50), D(100), D(5)), now=NOW)
    assert ledger.status("fixture", now=NOW).daily_buy_cash == 6
    with pytest.raises(ValueError, match="already dispatched"):
        ledger.claim_dispatch(t, now=NOW)


def test_account_isolation_and_global_uuid_uniqueness(store):
    ledger = store.agent_ledger
    t = ticket()
    dispatch(ledger, t)
    ledger.save_budget("another", LIMITS, now=NOW)
    assert ledger.status("another", now=NOW).committed_cash == 0
    with pytest.raises(ValueError, match="already exists"):
        ledger.reserve(replace(t, account_number="another"), now=NOW)


def test_fresh_cash_check_is_atomic_with_other_market_reservations(store):
    ledger = store.agent_ledger
    ledger.reserve(ticket(cash="4"), available_cash=D(7), now=NOW)
    with pytest.raises(ValueError, match="available broker cash"):
        ledger.reserve(ticket(market="crypto", symbol="BTC-USD", cash="4"), available_cash=D(7), now=NOW)


def test_tampered_immutable_ticket_and_expired_reservation_cannot_dispatch(store):
    ledger = store.agent_ledger
    t = ticket()
    ledger.reserve(t, now=NOW)
    with pytest.raises(ValueError, match="expired"):
        ledger.claim_dispatch(t, now=NOW + timedelta(seconds=61))
    with store._connection:
        store._connection.execute("UPDATE agent_tickets SET ticket_json='{}' WHERE ref_id=?", (t.ref_id,))
    with pytest.raises(ValueError, match="ticket changed"):
        ledger.claim_dispatch(t, now=NOW)


@pytest.mark.parametrize("budget", [AgentBudget(D("NaN")), AgentBudget(5.0), AgentBudget(D("1.001")), AgentBudget(D(10), D(5), D(20), D(1))])
def test_invalid_budgets_fail_without_persistence(store, budget):
    with pytest.raises(ValueError):
        store.agent_ledger.save_budget("fixture", budget)
    assert store.agent_ledger.status("fixture", now=NOW).limits == LIMITS

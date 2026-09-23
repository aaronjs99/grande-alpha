from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from crypto_fixtures import ACCOUNT, EXECUTION, INTENT, NOW, POSITION, FakeCryptoServer

from grande_alpha.agent_execution import AgentExecutor, crypto_ticket, equity_ticket, observe_crypto
from grande_alpha.agent_ledger import AgentBudget
from grande_alpha.broker.crypto import CryptoOutcomeUnknown
from grande_alpha.models import (
    BrokerExecution,
    BrokerOrder,
    OrderIntent,
    OrderReview,
    Portfolio,
    Position,
    Quote,
)
from grande_alpha.storage import AuditStore

D = Decimal


class FixtureBroker:
    def __init__(self, server):
        self.server = server

    async def get_accounts(self):
        return self.server.accounts

    async def get_portfolio(self, account):
        assert account == ACCOUNT.account_number
        return Portfolio(10.18, 10.18, 10.18, crypto_buying_power=10.18)

    async def get_positions(self, account):
        return []

    async def get_orders(self, account):
        return []

    async def get_crypto_positions(self, account):
        return await self.server.adapter.positions(account)

    async def get_crypto_orders(self, account):
        return await self.server.adapter.orders(account)

    async def place_crypto_order(self, review):
        return await self.server.adapter.place(review)


@pytest.fixture
def fixture(tmp_path):
    store = AuditStore(tmp_path / "execution.db")
    store.agent_ledger.save_budget(ACCOUNT.account_number, AgentBudget(D(6), D(10), D(12), D(1)), now=NOW)
    server = FakeCryptoServer()
    server.responses["get_crypto_orders"]["results"] = []
    server.responses["get_crypto_positions"]["results"] = []
    intent = replace(INTENT, dollar_amount=None, quantity=D("0.00005"), order_type="limit", limit_price=D("100000"))
    for method in ("preview_crypto_order", "place_crypto_order"):
        server.responses[method]["order"].update(type="limit", limit_price="100000")
    yield store, server, FixtureBroker(server), intent
    store.close()


async def reviewed(fixture):
    store, server, broker, intent = fixture
    review = await server.adapter.preview(ACCOUNT.account_number, intent)
    ticket = crypto_ticket(review, scope_id="test-only-authority", strategy_fingerprint="a" * 64)
    return review, ticket


def filled(server):
    order = {**server.responses["place_crypto_order"]["order"], "state": "filled", "cumulative_quantity": "0.00005",
             "executions": [{**EXECUTION, "quantity": "0.00005", "notional": "5.025"}],
             "net_rounded_executed_notional": "5.03", "net_rounded_estimated_notional": None}
    server.responses["get_crypto_orders"]["results"] = [order]
    server.responses["get_crypto_positions"]["results"] = [{**POSITION, "quantity": "0.00005", "quantity_transferable": "0.00005", "quantity_held_for_sell": "0", "cost_bases": []}]


@pytest.mark.asyncio
async def test_saved_budget_does_not_authorize_real_dispatch(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    executor = AgentExecutor(broker, store.agent_ledger, clock=lambda: NOW)
    with pytest.raises(PermissionError, match="saved budgets"):
        await executor.submit_crypto(review, ticket)
    assert store.agent_ledger.records(ACCOUNT.account_number) == []
    assert all(name != "place_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
async def test_dispatch_commits_before_broker_call_and_reconciles_without_double_fees(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    original = broker.place_crypto_order

    async def place(review):
        row = store.agent_ledger.records(ACCOUNT.account_number)[0]
        assert row["state"] == "dispatching" and row["submission_started_at"] is not None
        return await original(review)

    broker.place_crypto_order = place
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    await executor.submit_crypto(review, ticket)
    assert store.agent_ledger.status(ACCOUNT.account_number, now=NOW).unresolved_orders == 1
    filled(server)
    await executor.recover(ACCOUNT.account_number)
    status = store.agent_ledger.status(ACCOUNT.account_number, now=NOW)
    assert status.committed_cash == D("5.03") and status.pending_orders == 0
    assert status.daily_buy_cash == ticket.reserved_cash
    await executor.recover(ACCOUNT.account_number)
    assert store.agent_ledger.inventory(ACCOUNT.account_number)[ticket.key] == (D("0.00005"), D("5.03"))
    assert sum(name == "place_crypto_order" for name, _ in server.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), asyncio.CancelledError()])
async def test_failed_or_cancelled_wait_keeps_durable_reference_across_restart(fixture, failure):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    server.responses["place_crypto_order"] = failure
    with pytest.raises(asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else CryptoOutcomeUnknown):
        await executor.submit_crypto(review, ticket)
    reopened = AuditStore(store.path)
    fresh = AgentExecutor(broker, reopened.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    await fresh.recover(ACCOUNT.account_number)
    with pytest.raises(ValueError, match="reference already exists"):
        await fresh.submit_crypto(review, ticket)
    assert reopened.agent_ledger.status(ACCOUNT.account_number, now=NOW).unresolved_orders == 1
    assert sum(name == "place_crypto_order" for name, _ in server.calls) == 1
    reopened.close()


@pytest.mark.asyncio
async def test_crash_after_dispatch_commit_recovers_reference_without_resending(fixture):
    store, server, broker, _ = fixture
    _, ticket = await reviewed(fixture)
    store.agent_ledger.reserve(ticket, now=NOW)
    store.agent_ledger.claim_dispatch(ticket, now=NOW)
    server.responses["get_crypto_orders"]["results"] = [server.responses["place_crypto_order"]["order"]]
    reopened = AuditStore(store.path)
    fresh = AgentExecutor(broker, reopened.agent_ledger, clock=lambda: NOW)
    await fresh.recover(ACCOUNT.account_number)
    row = reopened.agent_ledger.records(ACCOUNT.account_number)[0]
    assert row["state"] == "open" and row["broker_order_id"]
    assert all(name != "place_crypto_order" for name, _ in server.calls)
    reopened.close()


@pytest.mark.asyncio
async def test_recovery_rejects_changed_account_mapping(fixture):
    store, server, broker, _ = fixture
    _, ticket = await reviewed(fixture)
    store.agent_ledger.reserve(ticket, now=NOW)
    store.agent_ledger.claim_dispatch(ticket, now=NOW)
    server.accounts = [replace(ACCOUNT, rhs_account_number="99999999")]
    with pytest.raises(ValueError, match="account binding changed"):
        await AgentExecutor(broker, store.agent_ledger, clock=lambda: NOW).recover(ACCOUNT.account_number)
    assert store.agent_ledger.status(ACCOUNT.account_number, now=NOW).unresolved_orders == 1


@pytest.mark.asyncio
async def test_authority_revocation_before_dispatch_releases_local_reservation_only(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    decisions = iter((True, False))
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: next(decisions), clock=lambda: NOW)
    with pytest.raises(PermissionError, match="revoked"):
        await executor.submit_crypto(review, ticket)
    assert store.agent_ledger.records(ACCOUNT.account_number)[0]["state"] == "released"
    assert store.agent_ledger.status(ACCOUNT.account_number, now=NOW).committed_cash == 0
    assert all(name != "place_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
async def test_journal_failure_stops_before_network_mutation(fixture, monkeypatch):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)

    def fail(*_, **__):
        raise OSError("Disk full")

    monkeypatch.setattr(store.agent_ledger, "claim_dispatch", fail)
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    with pytest.raises(OSError, match="Disk full"):
        await executor.submit_crypto(review, ticket)
    assert all(name != "place_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
async def test_unmanaged_holdings_cannot_be_silently_treated_as_free_budget(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    server.responses["get_crypto_positions"]["results"] = [POSITION]
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    with pytest.raises(ValueError, match="Unmanaged"):
        await executor.submit_crypto(review, ticket)
    assert not store.agent_ledger.records(ACCOUNT.account_number)


@pytest.mark.asyncio
async def test_open_order_and_cancel_acceptance_do_not_release_budget(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    await executor.submit_crypto(review, ticket)
    server.responses["get_crypto_orders"]["results"] = [server.responses["place_crypto_order"]["order"]]
    assert await server.adapter.cancel(ACCOUNT.account_number, review.order.order_id)
    await executor.recover(ACCOUNT.account_number)
    assert store.agent_ledger.status(ACCOUNT.account_number, now=NOW).committed_cash == ticket.reserved_cash
    server.responses["get_crypto_orders"]["results"][0]["state"] = "canceled"
    await executor.recover(ACCOUNT.account_number)
    assert store.agent_ledger.status(ACCOUNT.account_number, now=NOW).committed_cash == 0


@pytest.mark.asyncio
async def test_changed_pair_or_account_uuid_blocks_recovery_after_restart(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    await executor.submit_crypto(review, ticket)
    filled(server)
    server.responses["get_crypto_orders"]["results"][0]["currency_pair_id"] = "different-pair"
    with pytest.raises(ValueError, match="identity disagrees"):
        await executor.recover(ACCOUNT.account_number)
    assert store.agent_ledger.inventory(ACCOUNT.account_number) == {}


@pytest.mark.asyncio
async def test_recovery_ignores_unowned_orders_and_never_adopts_positions(fixture):
    store, server, broker, _ = fixture
    _, ticket = await reviewed(fixture)
    store.agent_ledger.reserve(ticket, now=NOW)
    store.agent_ledger.claim_dispatch(ticket, now=NOW)
    foreign = {**server.responses["place_crypto_order"]["order"], "ref_id": "other-reference"}
    server.responses["get_crypto_orders"]["results"] = [foreign]
    await AgentExecutor(broker, store.agent_ledger, clock=lambda: NOW).recover(ACCOUNT.account_number)
    assert store.agent_ledger.records(ACCOUNT.account_number)[0]["broker_order_id"] is None


@pytest.mark.asyncio
async def test_crypto_observation_keeps_net_cash_and_rejects_missing_fills(fixture):
    # Full adapter already validates fill rows; the coordinator independently
    # requires completeness before allowing those rows into a durable balance.
    from grande_alpha.broker.crypto import parse_order

    _, server, _, _ = fixture
    _, ticket = await reviewed(fixture)
    filled(server)
    order = parse_order(server.responses["get_crypto_orders"]["results"][0])
    assert observe_crypto(ticket, order).net_cash == D("5.03")
    with pytest.raises(ValueError, match="complete fill"):
        observe_crypto(ticket, replace(order, executions=()))


@pytest.mark.asyncio
async def test_crypto_route_requires_price_bound_and_exact_quantity(fixture):
    _, server, _, _ = fixture
    review, _ = await reviewed(fixture)
    with pytest.raises(ValueError, match="quantity-based limit"):
        crypto_ticket(replace(review, intent=INTENT), scope_id="scope", strategy_fingerprint="a" * 64)


@pytest.mark.asyncio
async def test_slow_preflight_cannot_dispatch_an_expired_review(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    now = [NOW]
    original = broker.get_portfolio

    async def slow(account):
        now[0] += timedelta(seconds=16)
        return await original(account)

    broker.get_portfolio = slow
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: now[0])
    with pytest.raises(ValueError, match="preview expired"):
        await executor.submit_crypto(review, ticket)
    assert not store.agent_ledger.records(ACCOUNT.account_number)
    assert all(name != "place_crypto_order" for name, _ in server.calls)


@pytest.mark.asyncio
async def test_unowned_round_trip_cannot_hide_behind_unchanged_position_quantity(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    await executor.submit_crypto(review, ticket)
    filled(server)
    foreign = {**server.responses["get_crypto_orders"]["results"][0], "id": "foreign-order", "ref_id": "foreign-ref",
               "executions": [{**EXECUTION, "id": "foreign-fill", "quantity": "0.00005"}]}
    server.responses["get_crypto_orders"]["results"].append(foreign)
    with pytest.raises(ValueError, match="unowned execution"):
        await executor.recover(ACCOUNT.account_number)
    assert store.agent_ledger.status(ACCOUNT.account_number, now=NOW).unresolved_orders == 1


def equity_review():
    intent = OrderIntent(str(uuid.uuid4()), "TQQQ", "buy", "fixture", order_type="limit", quantity=1,
                         limit_price=5, created_at=NOW)
    return OrderReview(intent, None, {}, Quote("TQQQ", 4.99, 5, 5, NOW, NOW, NOW), {})


def test_equity_ticket_requires_price_bound_sufficient_cash_and_clear_preview():
    review = equity_review()
    args = {"scope_id": "fixture-scope", "strategy_fingerprint": "a" * 64, "reserved_cash": D("4.99")}
    with pytest.raises(ValueError, match="below.*notional"):
        equity_ticket(ACCOUNT.account_number, review, **args)
    args["reserved_cash"] = D("5.10")
    with pytest.raises(ValueError, match="blocking"):
        equity_ticket(ACCOUNT.account_number, replace(review, checks={"blocked": True}), **args)
    with pytest.raises(ValueError, match="quantity-based limit"):
        equity_ticket(ACCOUNT.account_number, replace(review, intent=replace(review.intent, order_type="market", limit_price=None)), **args)


@pytest.mark.asyncio
async def test_equity_dispatch_and_fee_net_recovery_use_the_same_shared_ledger(fixture):
    store, _, broker, _ = fixture
    review = equity_review()
    ticket = equity_ticket(ACCOUNT.account_number, review, scope_id="fixture-scope", strategy_fingerprint="a" * 64,
                           reserved_cash=D("5.10"))
    raw = {"ref_id": ticket.ref_id, "type": "limit", "time_in_force": "gfd", "market_hours": "regular_hours", "price": "5"}
    order = BrokerOrder("stock-order", "TQQQ", "buy", "confirmed", 1, None, None, NOW, raw, cumulative_quantity=0)

    async def place(account, intent):
        assert account == ACCOUNT.account_number and intent == review.intent
        assert store.agent_ledger.records(account)[0]["state"] == "dispatching"
        return order

    broker.place_order = place
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    await executor.submit_equity(review, ticket)
    filled_order = replace(order, state="filled", average_price=5, cumulative_quantity=1,
                           executions=(BrokerExecution("stock-fill", 1, 5, .02, NOW),), last_transaction_at=NOW)

    async def orders(_):
        return [filled_order]

    async def positions(_):
        return [Position("TQQQ", 1, 1)]

    broker.get_orders, broker.get_positions = orders, positions
    await executor.recover(ACCOUNT.account_number)
    status = store.agent_ledger.status(ACCOUNT.account_number, now=NOW)
    assert status.committed_cash == D("5.02") and status.daily_buy_cash == D("5.10")
    assert status.unresolved_orders == 0

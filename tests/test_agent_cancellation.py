from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from crypto_fixtures import ACCOUNT, EXECUTION, NOW
from grande_alpha.agent_execution import AgentExecutor, crypto_ticket
from grande_alpha.agent_ledger import AgentBudget
from grande_alpha.broker.crypto import CryptoOutcomeUnknown
from grande_alpha.storage import AuditStore
from test_agent_execution import FixtureBroker, filled, reviewed
from test_agent_execution import fixture as fixture


async def open_managed(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    broker.cancel_crypto_order = server.adapter.cancel
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True,
                             authorize_cancel=lambda _: True, clock=lambda: NOW)
    await executor.submit_crypto(review, ticket)
    server.responses['get_crypto_orders']['results'] = [server.responses['place_crypto_order']['order']]
    return executor, ticket


@pytest.mark.asyncio
async def test_cancellation_default_deny_is_separate_from_order_authority(fixture):
    _, ticket = await open_managed(fixture)
    store, server, broker, _ = fixture
    executor = AgentExecutor(broker, store.agent_ledger, authorize=lambda _: True, clock=lambda: NOW)
    with pytest.raises(PermissionError, match='cancellation authority'):
        await executor.cancel_managed(ticket)
    assert store.agent_ledger.cancellation(ticket.ref_id) is None
    assert not any(name == 'cancel_crypto_order' for name, _ in server.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', [True, False])
async def test_cancel_ack_does_not_release_cash_or_allow_resend(fixture, accepted):
    executor, ticket = await open_managed(fixture)
    store, server, broker, _ = fixture
    server.responses['cancel_crypto_order'] = {'accepted': accepted}
    original = broker.cancel_crypto_order

    async def cancel(account, order_id):
        assert store.agent_ledger.cancellation(ticket.ref_id)['state'] == 'dispatching'
        return await original(account, order_id)

    broker.cancel_crypto_order = cancel
    assert await executor.cancel_managed(ticket) is accepted
    await executor.recover(ticket.account_number)
    assert store.agent_ledger.status(ticket.account_number, now=NOW).committed_cash == ticket.reserved_cash
    assert store.agent_ledger.cancellation(ticket.ref_id)['state'] == 'awaiting_terminal'
    reopened = AuditStore(store.path)
    restarted = AgentExecutor(broker, reopened.agent_ledger, authorize_cancel=lambda _: True, clock=lambda: NOW)
    try:
        with pytest.raises(ValueError, match='already attempted'):
            await restarted.cancel_managed(ticket)
        server.responses['get_crypto_orders']['results'][0]['state'] = 'canceled'
        await restarted.recover(ticket.account_number)
        assert reopened.agent_ledger.status(ticket.account_number, now=NOW).committed_cash == 0
        assert reopened.agent_ledger.cancellation(ticket.ref_id)['state'] == 'terminal'
    finally:
        reopened.close()
    assert sum(name == 'cancel_crypto_order' for name, _ in server.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [TimeoutError(), asyncio.CancelledError()])
async def test_cancel_unknown_outcome_never_retries_on_recovery(fixture, failure):
    executor, ticket = await open_managed(fixture)
    store, server, broker, _ = fixture
    server.responses['cancel_crypto_order'] = failure
    with pytest.raises(asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else CryptoOutcomeUnknown):
        await executor.cancel_managed(ticket)
    assert store.agent_ledger.cancellation(ticket.ref_id)['state'] == 'unknown'
    reopened = AuditStore(store.path)
    try:
        restarted = AgentExecutor(FixtureBroker(server), reopened.agent_ledger, clock=lambda: NOW)
        await restarted.recover(ticket.account_number)
        assert reopened.agent_ledger.status(ticket.account_number, now=NOW).pending_orders == 1
        filled(server)  # Cancellation lost the race: the order filled normally.
        await restarted.recover(ticket.account_number)
        assert reopened.agent_ledger.cancellation(ticket.ref_id)['state'] == 'terminal'
        assert reopened.agent_ledger.status(ticket.account_number, now=NOW).committed_cash == Decimal('5.03')
    finally:
        reopened.close()
    assert sum(name == 'cancel_crypto_order' for name, _ in server.calls) == 1


@pytest.mark.asyncio
async def test_crash_after_cancel_boundary_and_late_ack_preserve_terminal_state(fixture):
    executor, ticket = await open_managed(fixture)
    store, server, broker, _ = fixture
    await executor.recover(ticket.account_number)
    row = store.agent_ledger.records(ticket.account_number)[0]
    store.agent_ledger.claim_cancellation(ticket, row['broker_order_id'], now=NOW)
    reopened = AuditStore(store.path)
    try:
        restarted = AgentExecutor(broker, reopened.agent_ledger, authorize_cancel=lambda _: True, clock=lambda: NOW)
        with pytest.raises(ValueError, match='already attempted'):
            await restarted.cancel_managed(ticket)
        filled(server)
        await restarted.recover(ticket.account_number)
        store.agent_ledger.note_cancellation(ticket.ref_id, True, now=NOW)
        assert reopened.agent_ledger.cancellation(ticket.ref_id)['state'] == 'terminal'
    finally:
        reopened.close()
    assert not any(name == 'cancel_crypto_order' for name, _ in server.calls)


@pytest.mark.asyncio
async def test_cancel_rechecks_authority_and_rejects_foreign_or_terminal_orders(fixture):
    executor, ticket = await open_managed(fixture)
    store, server, _, _ = fixture
    with pytest.raises(ValueError, match='unowned or changed'):
        await executor.cancel_managed(replace(ticket, ref_id=str(uuid.uuid4())))
    calls = iter([True, False])
    executor._authorize_cancel = lambda _: next(calls)
    with pytest.raises(PermissionError, match='revoked'):
        await executor.cancel_managed(ticket)
    assert store.agent_ledger.cancellation(ticket.ref_id) is None
    executor._authorize_cancel = lambda _: True
    filled(server)
    with pytest.raises(ValueError, match='reconciled open'):
        await executor.cancel_managed(ticket)
    assert not any(name == 'cancel_crypto_order' for name, _ in server.calls)


@pytest.mark.asyncio
async def test_cancel_journal_failure_prevents_broker_write(fixture, monkeypatch):
    executor, ticket = await open_managed(fixture)
    store, server, _, _ = fixture

    def disk_failure(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(store.agent_ledger, 'claim_cancellation', disk_failure)
    with pytest.raises(OSError, match='disk full'):
        await executor.cancel_managed(ticket)
    assert not any(name == 'cancel_crypto_order' for name, _ in server.calls)


async def sell_review(fixture):
    executor, buy = await open_managed(fixture)
    store, server, _, intent = fixture
    filled(server)
    await executor.recover(buy.account_number)
    sell = replace(intent, side='sell', ref_id=str(uuid.uuid4()))
    for method in ('preview_crypto_order', 'place_crypto_order'):
        server.responses[method]['order'].update(side='sell', ref_id=sell.ref_id, id=str(uuid.uuid4()))
    review = await server.adapter.preview(ACCOUNT.account_number, sell)
    ticket = crypto_ticket(review, scope_id=buy.scope_id, strategy_fingerprint=buy.strategy_fingerprint)
    return executor, review, ticket


@pytest.mark.asyncio
async def test_managed_sale_works_with_zero_entry_budget_and_records_net_loss(fixture):
    executor, review, ticket = await sell_review(fixture)
    store, server, _, _ = fixture
    store.agent_ledger.save_budget(ticket.account_number, AgentBudget(), now=NOW)
    await executor.submit_crypto(review, ticket)
    sale = {**server.responses['place_crypto_order']['order'], 'state': 'filled', 'cumulative_quantity': '0.00005',
            'executions': [{**EXECUTION, 'id': str(uuid.uuid4()), 'quantity': '0.00005', 'notional': '4.975', 'effective_price': '99500'}],
            'net_rounded_executed_notional': '4.97', 'net_rounded_estimated_notional': None}
    server.responses['get_crypto_orders']['results'].append(sale)
    server.responses['get_crypto_positions']['results'] = []
    await executor.recover(ticket.account_number)
    status = store.agent_ledger.status(ticket.account_number, now=NOW)
    assert status.committed_cash == 0 and status.realized_losses == Decimal('0.06')
    assert not status.has_inventory
    assert sum(name == 'place_crypto_order' for name, _ in server.calls) == 2


@pytest.mark.asyncio
async def test_unowned_open_order_blocks_managed_sale(fixture):
    executor, review, ticket = await sell_review(fixture)
    store, server, _, _ = fixture
    foreign = {**server.responses['place_crypto_order']['order'], 'id': str(uuid.uuid4()), 'ref_id': str(uuid.uuid4())}
    server.responses['get_crypto_orders']['results'].append(foreign)
    with pytest.raises(ValueError, match='conflicts with an open order'):
        await executor.submit_crypto(review, ticket)
    assert not any(row['ref_id'] == ticket.ref_id for row in store.agent_ledger.records(ticket.account_number))
    assert sum(name == 'place_crypto_order' for name, _ in server.calls) == 1


@pytest.mark.asyncio
async def test_partial_fill_then_cancellation_keeps_inventory_cost(fixture):
    executor, ticket = await open_managed(fixture)
    store, server, _, _ = fixture
    await executor.cancel_managed(ticket)
    filled(server)
    order = server.responses['get_crypto_orders']['results'][0]
    order.update(state='canceled', cumulative_quantity='0.00002', net_rounded_executed_notional='2.01',
                 executions=[EXECUTION])
    position = server.responses['get_crypto_positions']['results'][0]
    position.update(quantity='0.00002', quantity_transferable='0.00002')
    await executor.recover(ticket.account_number)
    status = store.agent_ledger.status(ticket.account_number, now=NOW)
    assert status.committed_cash == Decimal('2.01')
    assert status.daily_buy_cash == ticket.reserved_cash
    assert status.pending_orders == 0 and status.has_inventory
    assert store.agent_ledger.cancellation(ticket.ref_id)['state'] == 'terminal'


@pytest.mark.asyncio
async def test_equity_cancellation_uses_account_scoped_stock_route(fixture):
    from grande_alpha.agent_execution import equity_ticket
    from grande_alpha.models import BrokerOrder
    from test_agent_execution import equity_review

    store, _, broker, _ = fixture
    review = equity_review()
    ticket = equity_ticket(ACCOUNT.account_number, review, scope_id='fixture', strategy_fingerprint='a' * 64,
                           reserved_cash=Decimal('5.10'))
    raw = {'ref_id': ticket.ref_id, 'type': 'limit', 'time_in_force': 'gfd', 'market_hours': 'regular_hours', 'price': '5'}
    order = BrokerOrder('stock-order', 'TQQQ', 'buy', 'confirmed', 1, None, None, NOW, raw, cumulative_quantity=0)

    async def orders(account):
        assert account == ticket.account_number
        return [order]

    calls = []

    async def cancel(account, order_id):
        calls.append((account, order_id))
        return True

    broker.get_orders, broker.cancel_order = orders, cancel
    store.agent_ledger.reserve(ticket, now=NOW)
    store.agent_ledger.claim_dispatch(ticket, now=NOW)
    store.agent_ledger.note_submission(ticket.ref_id, order.order_id, now=NOW)
    executor = AgentExecutor(broker, store.agent_ledger, authorize_cancel=lambda _: True, clock=lambda: NOW)
    assert await executor.cancel_managed(ticket)
    assert calls == [(ACCOUNT.account_number, 'stock-order')]
    assert store.agent_ledger.status(ticket.account_number, now=NOW).committed_cash == Decimal('5.10')
    order = replace(order, state='canceled')
    await executor.recover(ticket.account_number)
    assert store.agent_ledger.cancellation(ticket.ref_id)['state'] == 'terminal'


def test_two_database_connections_cannot_both_claim_cancellation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from test_agent_ledger import LIMITS, dispatch, observation, ticket

    first = AuditStore(tmp_path / 'cancel-race.db')
    second = AuditStore(first.path)
    try:
        first.agent_ledger.save_budget('fixture', LIMITS, now=NOW)
        t = ticket()
        dispatch(first.agent_ledger, t)
        observed = observation(t, quantity='0', cash='0', terminal=False, state='confirmed')
        first.agent_ledger.reconcile('fixture', [observed], {}, now=NOW)

        def claim(ledger):
            try:
                ledger.claim_cancellation(t, observed.order_id, now=NOW)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(claim, [first.agent_ledger, second.agent_ledger])) == [False, True]
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_preview_expiring_after_reservation_releases_only_unsent_ticket(fixture):
    store, server, broker, _ = fixture
    review, ticket = await reviewed(fixture)
    from datetime import timedelta

    current = NOW

    def authorize(_):
        nonlocal current
        if store.agent_ledger.records(ticket.account_number):
            current = NOW + timedelta(seconds=16)
        return True

    executor = AgentExecutor(broker, store.agent_ledger, authorize=authorize, clock=lambda: current)
    with pytest.raises(ValueError, match='expired'):
        await executor.submit_crypto(review, ticket)
    row = store.agent_ledger.records(ticket.account_number)[0]
    assert row['state'] == 'released' and row['submission_started_at'] is None
    assert not any(name == 'place_crypto_order' for name, _ in server.calls)

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_live_autonomy import _bind_owned_order, _controller, _order
from test_live_autonomy import _fixed_live_clock as _fixed_live_clock
from test_robinhood_mcp import FakeCallback, FakeSession, TaskBoundContext

from grande_alpha import controller as controller_module
from grande_alpha.broker import robinhood_mcp
from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker


@pytest.fixture
def transport(monkeypatch):
    session = FakeSession()
    wire = TaskBoundContext((object(), object(), None))
    context = TaskBoundContext(session)
    monkeypatch.setattr(robinhood_mcp, "OAuthCallbackServer", FakeCallback)
    monkeypatch.setattr(robinhood_mcp, "OAuthClientProvider", lambda **_kwargs: object())
    monkeypatch.setattr(robinhood_mcp, "streamablehttp_client", lambda *_args, **_kwargs: wire)
    monkeypatch.setattr(robinhood_mcp, "ClientSession", lambda *_args, **_kwargs: context)
    return RobinhoodMCPBroker("https://example.invalid/mcp"), session, wire, context


@pytest.mark.asyncio
async def test_interrupted_owner_releases_active_and_queued_callers(transport):
    broker, session, wire, context = transport
    entered = asyncio.Event()

    async def hanging_call(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    session.call_tool = hanging_call
    await broker.connect()
    active = asyncio.create_task(broker.get_accounts())
    await entered.wait()
    queued = asyncio.create_task(broker.get_accounts())
    await asyncio.sleep(0)
    broker._worker.cancel()
    try:
        for caller in (active, queued):
            with pytest.raises(BrokerError, match="disconnected|interrupted|unknown"):
                await asyncio.wait_for(asyncio.shield(caller), 0.2)
        assert not broker.connected
        assert wire.owner is wire.exited_by
        assert context.owner is context.exited_by
    finally:
        for caller in (active, queued):
            if not caller.done():
                caller.cancel()
        await asyncio.gather(active, queued, return_exceptions=True)
        await asyncio.gather(broker._worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_disconnect_interrupts_stalled_read_in_its_owner_task(transport, monkeypatch):
    broker, session, wire, context = transport
    entered = asyncio.Event()

    async def hanging_call(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    session.call_tool = hanging_call
    monkeypatch.setattr(robinhood_mcp, "DISCONNECT_GRACE_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(robinhood_mcp, "DISCONNECT_CANCEL_SECONDS", 0.1, raising=False)
    await broker.connect()
    caller = asyncio.create_task(broker.get_accounts())
    await entered.wait()
    worker = broker._worker
    queued_write = asyncio.create_task(broker._call("place_equity_order", {}))
    await asyncio.sleep(0)
    try:
        await asyncio.wait_for(broker.disconnect(), 0.5)
        with pytest.raises(BrokerError, match="interrupted|unknown|disconnected"):
            await asyncio.wait_for(caller, 0.2)
        with pytest.raises(BrokerError, match="queued request was not sent"):
            await queued_write
        assert not broker.connected
        assert broker._worker is None
        assert wire.owner is wire.exited_by
        assert context.owner is context.exited_by
    finally:
        for task in (worker, caller, queued_write):
            if not task.done():
                task.cancel()
        await asyncio.gather(worker, caller, queued_write, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['get_accounts', 'place_equity_order'])
async def test_stalled_mcp_send_times_out_and_releases_the_queue_without_retry(transport, monkeypatch, name):
    from mcp import ClientSession

    broker, session, wire, context = transport
    entered = asyncio.Event()
    calls = []
    original = session.call_tool

    class BlockedWrite:
        async def send(self, _message):
            entered.set()
            await asyncio.Event().wait()

    # Exercise the real SDK send path: its read timeout starts AFTER this send.
    blocked_session = ClientSession(None, BlockedWrite())

    async def call(tool, arguments, **kwargs):
        calls.append(tool)
        if len(calls) == 1:
            return await blocked_session.call_tool(tool, arguments, **kwargs)
        return await original(tool, arguments, **kwargs)

    session.call_tool = call
    monkeypatch.setattr(robinhood_mcp, 'DEFAULT_TOOL_TIMEOUT_SECONDS', 0.03)
    monkeypatch.setitem(robinhood_mcp.TOOL_TIMEOUT_SECONDS, name, 0.03)
    monkeypatch.setattr(robinhood_mcp, 'DISCONNECT_GRACE_SECONDS', 0.01)
    await broker.connect()
    first = asyncio.create_task(broker._call(name, {}))
    await entered.wait()
    queued = asyncio.create_task(broker.get_accounts())
    try:
        with pytest.raises(BrokerError, match='timed out.*remote outcome is unknown'):
            await asyncio.wait_for(asyncio.shield(first), 0.5)
        assert await asyncio.wait_for(queued, 0.5) == []
        assert calls == [name, 'get_accounts']  # No automatic resubmission, including writes.
        assert broker.connected
    finally:
        await broker.disconnect()
        await asyncio.gather(first, queued, return_exceptions=True)
    assert wire.owner is wire.exited_by
    assert context.owner is context.exited_by
    assert session.call_tasks == [wire.owner]


@pytest.mark.asyncio
async def test_read_only_stop_and_disconnect_do_not_wait_for_reconciliation(tmp_path, monkeypatch):
    controller, broker, store, grant = _controller(tmp_path, monkeypatch)
    controller.authorize_live(grant)
    controller.snapshot.strategy_running = True
    calls = []

    async def disconnect():
        calls.append("disconnect")

    async def forbidden_read():
        raise AssertionError("No application order history requires no broker cleanup read")

    broker.disconnect = disconnect
    broker.get_accounts = forbidden_read
    await controller._reconcile_lock.acquire()
    try:
        plan = await asyncio.wait_for(controller.prepare_cancel_plan(), 0.2)
        assert not plan.order_ids
        assert controller.risk.grant is None and not controller.snapshot.strategy_running
        await asyncio.wait_for(controller.disconnect(), 0.2)
        assert not controller.snapshot.connected
        assert calls == ["disconnect"]
        assert not broker.cancel_calls and not broker.place_calls
    finally:
        controller._reconcile_lock.release()
        store.close()


@pytest.mark.asyncio
async def test_unknown_orders_require_deliberate_disconnect_and_keep_durable_ownership(tmp_path, monkeypatch):
    controller, broker, store, _ = _controller(tmp_path, monkeypatch)
    owned = _bind_owned_order(store, _order("owned-unresolved"))
    broker.orders = [owned]
    account = controller.snapshot.account.account_number
    before = store.owned_broker_order_bindings(account)
    disconnected = []

    async def disconnect():
        disconnected.append(True)

    broker.disconnect = disconnect
    monkeypatch.setattr(controller_module, "DISCONNECT_TRUTH_TIMEOUT_SECONDS", 0.01)
    await controller._reconcile_lock.acquire()
    try:
        with pytest.raises(BrokerError, match="timed out"):
            await controller.disconnect()
        assert not disconnected
        assert controller.snapshot.connected and controller.risk.killed
        await controller.disconnect_without_order_cleanup(unverified=True)
        assert disconnected and not controller.snapshot.connected
        assert store.owned_broker_order_bindings(account) == before
        assert controller._cleanup_unresolved
        assert broker.cancel_calls == []
    finally:
        controller._reconcile_lock.release()
        store.close()


@pytest.mark.asyncio
async def test_failed_initial_account_read_closes_transport_and_resets_state(tmp_path, monkeypatch):
    controller, broker, store, _ = _controller(tmp_path, monkeypatch)
    calls = []

    async def failure():
        raise BrokerError("Account unavailable")

    async def disconnect():
        calls.append("disconnect")

    broker.get_accounts = failure
    broker.disconnect = disconnect
    try:
        with pytest.raises(BrokerError, match="Account unavailable"):
            await controller.connect()
        assert calls == ["disconnect"]
        assert not controller.snapshot.connected
        assert controller.snapshot.account is None
        assert controller.risk.grant is None
    finally:
        store.close()


def test_actual_qt_event_loop_can_stop_disconnect_and_exit_after_a_stalled_read():
    script = Path(__file__).with_name("run_connection_ui_scenario.py")
    result = subprocess.run(
        [sys.executable, str(script)],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Qt connection lifecycle passed" in result.stdout

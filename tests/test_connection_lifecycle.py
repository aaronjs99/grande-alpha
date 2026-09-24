from __future__ import annotations

import asyncio

import pytest
from PySide6.QtGui import QCloseEvent

from grande_alpha.broker import robinhood_mcp
from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker
from grande_alpha.desktop import controller as controller_module
from grande_alpha.ui.main_window import MainWindow
from test_live_autonomy import _bind_owned_order, _controller, _order, _qt_app
from test_live_autonomy import _fixed_live_clock as _fixed_live_clock
from test_robinhood_mcp import FakeCallback, FakeSession, TaskBoundContext


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
    monkeypatch.setattr(robinhood_mcp, "DISCONNECT_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(robinhood_mcp, "DISCONNECT_CANCEL_SECONDS", 0.1)
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
async def test_desktop_disconnect_offer_keeps_unresolved_records(tmp_path, monkeypatch):
    _qt_app()
    controller, broker, store, _ = _controller(tmp_path, monkeypatch)
    account = controller.snapshot.account.account_number
    broker.orders = [_bind_owned_order(store, _order("owned-ui"))]
    before = store.owned_broker_order_bindings(account)
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)
    decisions = []

    async def accept_unverified(title, text, **kwargs):
        decisions.append((title, text, kwargs))
        return True

    monkeypatch.setattr(window, "_stop_message", accept_unverified)
    monkeypatch.setattr(controller_module, "DISCONNECT_TRUTH_TIMEOUT_SECONDS", 0.01)
    await controller._reconcile_lock.acquire()
    try:
        await asyncio.wait_for(window._connect(), 1)
        assert decisions and decisions[0][2]["question"] is True
        assert not controller.snapshot.connected
        assert store.owned_broker_order_bindings(account) == before
        assert not broker.cancel_calls
    finally:
        controller._reconcile_lock.release()
        window._closing_after_cleanup = True
        window.close()
        store.close()


@pytest.mark.asyncio
async def test_window_close_during_connection_uses_shutdown_path(tmp_path, monkeypatch):
    _qt_app()
    controller, _broker, store, _ = _controller(tmp_path, monkeypatch)
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)
    controller.snapshot.connected = False
    window._on_snapshot(controller.snapshot)
    window._connection_task = asyncio.current_task()
    pending = []

    def capture(_name, awaitable):
        pending.append(awaitable)

    def choose_stop(dialog):
        next(button for button in dialog.buttons() if button.text() == "Stop trading and exit").click()
        return 0

    monkeypatch.setattr(window, "_start_task", capture)
    monkeypatch.setattr("grande_alpha.ui.main_window.QMessageBox.exec", choose_stop)
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert window._close_requested
        assert len(pending) == 1
    finally:
        for awaitable in pending:
            awaitable.close()
        window._connection_task = None
        window._closing_after_cleanup = True
        window.close()
        store.close()


@pytest.mark.parametrize("width,height", [(720, 560), (900, 1200), (1366, 768)])
def test_desktop_renders_at_narrow_portrait_and_landscape_sizes(tmp_path, monkeypatch, width, height):
    app = _qt_app()
    controller, _broker, store, _ = _controller(tmp_path, monkeypatch)
    window = MainWindow(controller, controller.config)
    try:
        window.resize(width, height)
        window.show()
        app.processEvents()
        image = window.grab()
        assert not image.isNull()
        assert image.width() == width and image.height() == height
        assert window.connect_button.width() > 0
        assert window.mode_badge.width() > 0
    finally:
        window._closing_after_cleanup = True
        window.close()
        store.close()

from __future__ import annotations

import asyncio

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_live_autonomy import _bind_owned_order, _controller, _order
from test_live_autonomy import _fixed_live_clock as _fixed_live_clock

from grande_alpha.ui import main_window
from grande_alpha.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(tmp_path, monkeypatch, qt_app):
    controller, broker, store, grant = _controller(tmp_path, monkeypatch, name="stop-ui")
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)
    window.timer.stop()
    window.reconcile_timer.stop()
    messages = []

    async def message(title, text, *, question=False, error=False):
        messages.append((title, text, question, error))
        return True

    monkeypatch.setattr(window, "_stop_message", message)
    yield window, controller, broker, store, grant, messages
    window._closing_after_cleanup = True
    window.close()
    window.deleteLater()
    qt_app.sendPostedEvents()
    store.close()


@pytest.mark.asyncio
async def test_prepare_stops_before_waiting_for_existing_reconciliation(tmp_path, monkeypatch):
    controller, broker, store, grant = _controller(tmp_path, monkeypatch)
    controller.authorize_live(grant)
    controller.snapshot.strategy_running = True
    await controller._reconcile_lock.acquire()
    task = asyncio.create_task(controller.prepare_cancel_plan())
    try:
        await asyncio.sleep(0)
        assert not task.done()
        assert not controller.snapshot.strategy_running
        assert controller.risk.grant is None and controller.risk.killed
        assert broker.cancel_calls == []
    finally:
        controller._reconcile_lock.release()
        await task
        store.close()


@pytest.mark.asyncio
async def test_stop_locks_authority_even_when_shadow_persistence_fails(tmp_path, monkeypatch):
    controller, broker, store, grant = _controller(tmp_path, monkeypatch)
    controller.authorize_live(grant)
    controller.snapshot.strategy_running = True

    def broken_checkpoint(_reason):
        raise OSError("Disk full")

    monkeypatch.setattr(controller, "stop_shadow", broken_checkpoint)
    try:
        with pytest.raises(OSError, match="Disk full"):
            await controller.prepare_cancel_plan()
        assert not controller.snapshot.strategy_running
        assert controller.risk.grant is None and controller.risk.killed
        assert broker.cancel_calls == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_button_reports_no_orders_and_keeps_robinhood_connected(setup):
    window, controller, broker, _, _, messages = setup
    controller.start_shadow()
    assert controller.snapshot.shadow_running
    finished = asyncio.Event()
    original_message = window._stop_message

    async def message(*args, **kwargs):
        result = await original_message(*args, **kwargs)
        finished.set()
        return result

    window._stop_message = message
    window.kill_button.click()
    await asyncio.wait_for(finished.wait(), 2)
    assert "No GRANDE-owned open orders" in window.stop_status.text()
    assert not messages[0][2]  # No meaningless confirmation for zero orders.
    assert controller.snapshot.connected
    assert not controller.snapshot.strategy_running
    assert not controller.snapshot.shadow_running
    assert broker.cancel_calls == []
    assert window.kill_button.text() == "STOP + CANCEL"
    assert window.kill_button.isEnabled()
    assert controller._cancel_plans == {}
    controller.start_shadow()
    assert window.stop_status.isHidden()  # A new session must not show a stale stopped banner.
    controller.stop_shadow()


@pytest.mark.asyncio
async def test_busy_progress_survives_snapshot_and_duplicate_clicks(setup, monkeypatch):
    window, controller, broker, _, grant, messages = setup
    controller.authorize_live(grant)
    controller.snapshot.strategy_running = True
    entered, release = asyncio.Event(), asyncio.Event()
    original = broker.get_accounts
    calls = 0

    async def delayed_accounts():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original()

    monkeypatch.setattr(broker, "get_accounts", delayed_accounts)
    task = asyncio.create_task(window._stop_and_cancel())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        window._on_snapshot(controller.snapshot)
        assert not controller.snapshot.strategy_running
        assert controller.risk.grant is None
        assert not window.kill_button.isEnabled()
        assert not window.stop_cancel_action.isEnabled()
        assert not window.stop_status.isHidden()
        await window._stop_and_cancel()
        assert calls == 1 and messages == []
    finally:
        release.set()
        await task
    assert window.kill_button.isEnabled()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("Broker unavailable"), TimeoutError()])
async def test_preview_failure_is_visible_and_leaves_automation_stopped(setup, monkeypatch, failure):
    window, controller, broker, _, grant, messages = setup
    controller.authorize_live(grant)
    controller.snapshot.strategy_running = True

    async def failed_accounts():
        raise failure

    monkeypatch.setattr(broker, "get_accounts", failed_accounts)
    await window._stop_and_cancel()
    assert messages[-1][3]
    assert "timed out" in messages[-1][1] if isinstance(failure, TimeoutError) else "Broker unavailable" in messages[-1][1]
    assert controller.risk.grant is None
    assert not controller.snapshot.strategy_running
    assert broker.cancel_calls == []
    assert not window._stop_cancel_busy


@pytest.mark.asyncio
async def test_slow_preview_times_out_without_a_broker_write(setup, monkeypatch):
    window, controller, broker, _, _, messages = setup
    read_cancelled = asyncio.Event()

    async def slow_accounts():
        try:
            await asyncio.Event().wait()
        finally:
            read_cancelled.set()

    monkeypatch.setattr(broker, "get_accounts", slow_accounts)
    monkeypatch.setattr(main_window, "STOP_PREVIEW_TIMEOUT_SECONDS", 0.01)
    await asyncio.wait_for(window._stop_and_cancel(), 2)
    assert read_cancelled.is_set()
    assert "timed out" in messages[-1][1]
    assert broker.cancel_calls == []
    assert controller.risk.killed
    assert window.kill_button.isEnabled()


@pytest.mark.asyncio
@pytest.mark.parametrize("accept", [True, False])
async def test_owned_scope_requires_confirmation_and_reports_outcome(setup, monkeypatch, accept):
    window, controller, broker, store, _, messages = setup
    owned = _bind_owned_order(store, _order("owned", state="queued"))
    unrelated = _order("manual", placed_agent="user")
    broker.orders = [owned, unrelated]
    original = window._stop_message

    async def message(*args, **kwargs):
        assert not controller.snapshot.strategy_running
        assert controller.risk.grant is None
        await original(*args, **kwargs)
        return accept

    monkeypatch.setattr(window, "_stop_message", message)
    await window._stop_and_cancel()
    assert messages[0][2]
    assert "1 unrelated" in messages[0][1]
    assert broker.cancel_calls == (["owned"] if accept else [])
    assert broker.orders[1].state == "queued"
    assert ("verified terminal" if accept else "Cancellation declined") in window.stop_status.text()
    assert controller.snapshot.connected
    assert controller._cancel_plans == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [True, False])
async def test_execution_failure_is_visible_and_restores_controls(setup, monkeypatch, raises):
    window, controller, broker, store, _, messages = setup
    broker.orders = [_bind_owned_order(store, _order("owned"))]
    original = controller.execute_confirmed_cancel

    async def execute(plan, **kwargs):
        if plan is not None:
            if raises:
                raise OSError("Receipt unavailable")
            return False
        return await original(plan, **kwargs)

    monkeypatch.setattr(controller, "execute_confirmed_cancel", execute)
    await window._stop_and_cancel()
    assert ("Receipt unavailable" if raises else "could not be verified") in messages[-1][1]
    assert messages[-1][3]
    assert window.kill_button.isEnabled()
    assert not window._stop_cancel_busy
    assert broker.cancel_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("accept", [True, False])
async def test_confirmation_dialog_yields_to_asyncio_and_defaults_to_no(setup, accept):
    window, _, _, _, _, _ = setup
    task = asyncio.create_task(MainWindow._stop_message(window, "Cancel test orders?", "Fixture only", question=True))
    try:
        await asyncio.sleep(0)
        assert not task.done()  # A blocking QMessageBox would prevent this task from resuming.
        dialog = next(child for child in window.findChildren(QMessageBox) if child.isVisible())
        assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.No)
        if accept:
            dialog.button(QMessageBox.StandardButton.Yes).click()
        else:
            dialog.close()
        assert await asyncio.wait_for(task, 2) is accept
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

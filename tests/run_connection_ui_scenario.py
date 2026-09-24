"""Real qasync loop, synthetic transport only; optionally capture disconnect status."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from qasync import QEventLoop
from test_robinhood_mcp import FakeCallback, FakeSession, TaskBoundContext

from grande_alpha.broker import robinhood_mcp
from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.models import Account, OrderIntent, Portfolio
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


async def scenario(app, output):
    errors = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: errors.append(context))
    with tempfile.TemporaryDirectory() as directory:
        session = FakeSession()
        wire = TaskBoundContext((object(), object(), None))
        context = TaskBoundContext(session)
        entered = asyncio.Event()

        async def stalled_read(name, *_args, **_kwargs):
            assert name == "get_portfolio"
            entered.set()
            await asyncio.Event().wait()

        session.call_tool = stalled_read
        with (
            patch.object(robinhood_mcp, "OAuthCallbackServer", FakeCallback),
            patch.object(robinhood_mcp, "OAuthClientProvider", lambda **_kwargs: object()),
            patch.object(robinhood_mcp, "streamablehttp_client", lambda *_args, **_kwargs: wire),
            patch.object(robinhood_mcp, "ClientSession", lambda *_args, **_kwargs: context),
            patch.object(robinhood_mcp, "DISCONNECT_GRACE_SECONDS", 0.01),
        ):
            broker = RobinhoodMCPBroker("https://example.invalid/mcp")
            store = AuditStore(Path(directory) / "synthetic.db")
            controller = TradingController(broker, AppConfig(broker_connection_enabled=True), store)
            window = MainWindow(controller, controller.config)
            window.setWindowTitle("GRANDE Alpha · Synthetic disconnect verification · No real account")
            window.resize(1440, 960)
            window.tabs.setCurrentWidget(window.agent_widget)
            window.show()
            try:
                await broker.connect()
                # Verify timeout recovery and real Qt responsiveness before the
                # disconnect scenario. These are synthetic reads, never orders.
                ticks = []
                heartbeat = QTimer(window)
                heartbeat.setInterval(5)
                heartbeat.timeout.connect(lambda: ticks.append(True))
                heartbeat.start()
                calls = []
                healthy = FakeSession()

                async def one_stalled_send(name, arguments, **kwargs):
                    calls.append(name)
                    if len(calls) == 1:
                        await asyncio.Event().wait()
                    return await healthy.call_tool(name, arguments, **kwargs)

                session.call_tool = one_stalled_send
                with patch.object(robinhood_mcp, 'DEFAULT_TOOL_TIMEOUT_SECONDS', 0.05):
                    try:
                        await broker.get_accounts()
                    except BrokerError as exc:
                        assert 'timed out' in str(exc)
                    else:
                        raise AssertionError('A stalled send did not time out')
                    assert await broker.get_accounts() == []
                heartbeat.stop()
                assert ticks and calls == ['get_accounts', 'get_accounts']
                session.call_tool = stalled_read
                controller.snapshot.connected = True
                controller.snapshot.account = Account("synthetic-0000", "Synthetic", "cash", True, "active")
                controller.snapshot.portfolio = Portfolio(100, 100, 100)
                controller._emit()
                read = asyncio.create_task(controller.reconcile())
                await entered.wait()
                assert controller._reconcile_lock.locked()

                window.kill_button.click()
                await until(lambda: any(dialog.isVisible() for dialog in window.findChildren(QMessageBox)))
                dialog = next(dialog for dialog in window.findChildren(QMessageBox) if dialog.isVisible())
                assert "No GRANDE-owned open orders" in dialog.text()
                assert controller.risk.killed
                dialog.button(QMessageBox.StandardButton.Ok).click()
                await until(lambda: not window._stop_cancel_busy)

                window.connect_button.click()
                await until(lambda: not controller.snapshot.connected and window._connection_task is None)
                await asyncio.wait_for(read, 1)
                assert not controller._reconcile_lock.locked()
                assert not window.timer.isActive() and not window.reconcile_timer.isActive()
                assert "disconnected" in window.stop_status.text()
                assert window.connect_button.text() == "Connect Robinhood"
                assert wire.owner is wire.exited_by and context.owner is context.exited_by
                if output:
                    app.processEvents()
                    output.parent.mkdir(parents=True, exist_ok=True)
                    assert window.grab().save(str(output))

                # A second transport must also close through a real window-close event.
                await broker.connect()
                controller.snapshot.connected = True
                controller.snapshot.account = Account("synthetic-0000", "Synthetic", "cash", True, "active")
                controller._emit()
                window.kill_button.click()
                await until(lambda: any(dialog.isVisible() for dialog in window.findChildren(QMessageBox)))
                window.close()
                await until(lambda: window._closing_after_cleanup and not window.isVisible())
                assert window._stop_task is None
                assert not broker.connected and not controller.snapshot.connected
                assert wire.owner is wire.exited_by and context.owner is context.exited_by

                # Unknown submissions require an explicit decision, with a safe No
                # default. Choosing to leave must preserve the durable unresolved row.
                window._closing_after_cleanup = False
                window.show()
                await broker.connect()
                account_number = "synthetic-0000"
                controller.snapshot.connected = True
                controller.snapshot.account = Account(account_number, "Synthetic", "cash", True, "active")
                intent = OrderIntent("synthetic-unresolved", "TQQQ", "buy", "Synthetic fixture", dollar_amount=1)
                store.record_intent(intent)
                store.mark_intent_submitting(
                    intent.ref_id, account_number=account_number, authority_id="fixture",
                    strategy_fingerprint="a" * 64, authorized_notional=1,
                )
                controller._submission_reconcile_required[intent.ref_id] = None
                retained = store.unresolved_order_intents(account_number)

                async def unavailable(*_args, **_kwargs):
                    raise BrokerError("Synthetic connection unavailable")

                session.call_tool = unavailable
                controller._emit()
                window.close()
                await until(lambda: any(dialog.isVisible() for dialog in window.findChildren(QMessageBox)))
                dialog = next(dialog for dialog in window.findChildren(QMessageBox) if dialog.isVisible())
                assert dialog.windowTitle() == "Exit without verified order cleanup?"
                assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.No)
                window.close()
                assert len([item for item in window.findChildren(QMessageBox) if item.isVisible()]) == 1
                dialog.button(QMessageBox.StandardButton.No).click()
                await until(lambda: not window._close_requested)
                assert window.isVisible() and controller.snapshot.connected
                window.close()
                await until(lambda: any(dialog.isVisible() for dialog in window.findChildren(QMessageBox)))
                dialog = next(dialog for dialog in window.findChildren(QMessageBox) if dialog.isVisible())
                dialog.button(QMessageBox.StandardButton.Yes).click()
                await until(lambda: window._closing_after_cleanup and not window.isVisible())
                assert not controller.snapshot.connected and not broker.connected
                assert store.unresolved_order_intents(account_number) == retained
                assert not errors, errors
            finally:
                window._closing_after_cleanup = True
                window.close()
                await broker.disconnect()
                store.close()
    print("Qt connection lifecycle passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(scenario(app, args.output))

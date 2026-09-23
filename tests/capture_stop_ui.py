"""Capture the STOP result with synthetic data and a broker that cannot write."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from fake_broker import DisabledBroker
from PySide6.QtWidgets import QApplication

from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.models import Account, Portfolio
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow

ACCOUNT = Account("synthetic-0000", "Synthetic", "cash", True, "active")


class EmptyFixtureBroker(DisabledBroker):
    async def get_accounts(self):
        return [ACCOUNT]

    async def get_portfolio(self, account_number):
        return Portfolio(100, 100, 100)

    async def get_positions(self, account_number):
        return []

    async def get_orders(self, account_number):
        return []


async def capture(path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        store = AuditStore(Path(directory) / "synthetic.db")
        controller = TradingController(EmptyFixtureBroker(), AppConfig(broker_connection_enabled=True), store)
        controller.snapshot.connected = True
        controller.snapshot.account = ACCOUNT
        controller.snapshot.portfolio = Portfolio(100, 100, 100)
        window = MainWindow(controller, controller.config)
        window.resize(1440, 960)
        window.setWindowTitle("GRANDE Alpha · Synthetic STOP verification · No real account data")
        window.tabs.setCurrentWidget(window.agent_widget)
        window._on_snapshot(controller.snapshot)
        window.timer.stop()
        window.reconcile_timer.stop()

        async def acknowledge(*_args, **_kwargs):
            return False

        window._stop_message = acknowledge
        await window._stop_and_cancel()
        window.timer.stop()
        window.reconcile_timer.stop()
        window.show()
        app.processEvents()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(path)):
            raise RuntimeError(f"Could not save {path}")
        window._closing_after_cleanup = True
        window.close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/images/stop-cancel-status.png"))
    asyncio.run(capture(parser.parse_args().output))

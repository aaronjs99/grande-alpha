from __future__ import annotations

import asyncio
import logging
import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox
from qasync import QEventLoop

from momentum_trader.broker import RobinhoodMCPBroker
from momentum_trader.config import data_dir, load_config
from momentum_trader.controller import TradingController
from momentum_trader.storage import AuditStore
from momentum_trader.ui.main_window import MainWindow


def main() -> int:
    logging.basicConfig(
        filename=data_dir() / "momentum_trader.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Momentum Trader")
    app.setOrganizationName("AaronJS")
    instance_lock = QLockFile(str(data_dir() / "app.lock"))
    instance_lock.setStaleLockTime(10_000)
    if not instance_lock.tryLock(100) and not (
        instance_lock.removeStaleLockFile() and instance_lock.tryLock(100)
    ):
        logging.warning("A second Momentum Trader instance was rejected")
        return 2
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    try:
        config = load_config()
        store = AuditStore()
        broker = RobinhoodMCPBroker()
        controller = TradingController(broker, config, store)
        window = MainWindow(controller, config)
        window.show()
        with loop:
            loop.run_forever()
        store.close()
        return 0
    except Exception as exc:
        logging.exception("Fatal startup error")
        QMessageBox.critical(None, "Momentum Trader failed to start", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
from importlib.resources import as_file, files

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from qasync import QEventLoop

from grande_alpha import __version__
from grande_alpha.broker import RobinhoodMCPBroker
from grande_alpha.config import ONBOARDING_VERSION, data_dir, load_config, save_config
from grande_alpha.controller import TradingController
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.onboarding import OnboardingWizard
from grande_alpha.windows_shortcut import WINDOWS_APP_USER_MODEL_ID


def _set_windows_app_identity() -> None:
    """Give Windows a stable taskbar identity so the packaged logo is used consistently."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        logging.getLogger(__name__).warning("Windows app identity could not be registered")


def main() -> int:
    if "--version" in sys.argv:
        print(f"GRANDE Alpha {__version__}")
        return 0
    _set_windows_app_identity()
    logging.basicConfig(
        filename=data_dir() / "grande_alpha.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("GRANDE Alpha")
    app.setApplicationDisplayName("GRANDE Alpha")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AaronJS")
    app.setOrganizationDomain("local.grandealpha")
    icon_resource = files("grande_alpha.assets").joinpath("app-icon.png")
    with as_file(icon_resource) as icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))
    instance_lock = QLockFile(str(data_dir() / "app.lock"))
    instance_lock.setStaleLockTime(10_000)
    if not instance_lock.tryLock(100) and not (
        instance_lock.removeStaleLockFile() and instance_lock.tryLock(100)
    ):
        logging.warning("A second GRANDE Alpha instance was rejected")
        return 2
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    try:
        config = load_config()
        if config.onboarding_version < ONBOARDING_VERSION:
            onboarding = OnboardingWizard(config)
            if onboarding.exec() != QDialog.DialogCode.Accepted:
                logging.info("First-run onboarding was declined; application remained closed")
                return 0
            config = onboarding.updated_config()
            save_config(config)
        store = AuditStore()
        store.prune_market_history(config.market_history_retention_days)
        broker = RobinhoodMCPBroker()
        controller = TradingController(broker, config, store)
        window = MainWindow(controller, config)
        window.setWindowIcon(app.windowIcon())
        window.show()
        with loop:
            loop.run_forever()
        store.close()
        return 0
    except Exception as exc:
        logging.exception("Fatal startup error")
        QMessageBox.critical(None, "GRANDE Alpha failed to start", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

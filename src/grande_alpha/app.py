from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
from dataclasses import replace
from importlib.resources import as_file, files

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from qasync import QEventLoop

from grande_alpha import __version__
from grande_alpha.broker import RobinhoodMCPBroker
from grande_alpha.broker.base import ShadowOnlyBroker
from grande_alpha.config import ONBOARDING_VERSION, data_dir, load_config, save_config
from grande_alpha.controller import TradingController
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.onboarding import OnboardingWizard
from grande_alpha.windows_shortcut import WINDOWS_APP_USER_MODEL_ID


def auto_shadow_runtime_config(config):
    """Return the non-persistent, structurally read-only scheduled-shadow profile.

    A user's normal-app route may be extended-hours, limit, or GTC.  Scheduled shadow
    must still start with the one supported observation lifecycle, and it must never
    inherit a saved real-order capability.  The returned dataclass is process-local;
    ``save_config`` is deliberately not called.
    """

    return replace(
        config,
        live_trading_enabled=False,
        market_hours="regular_hours",
        order_type="market",
        time_in_force="gfd",
        settlement_model="cash_t1",
    )


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
    auto_shadow = "--auto-shadow" in sys.argv
    qt_argv = [argument for argument in sys.argv if argument != "--auto-shadow"]
    _set_windows_app_identity()
    logging.basicConfig(
        filename=data_dir() / "grande_alpha.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = QApplication(qt_argv)
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
            if auto_shadow:
                logging.error("AUTO SHADOW BLOCKED: first-run onboarding is incomplete")
                return 3
            onboarding = OnboardingWizard(config)
            if onboarding.exec() != QDialog.DialogCode.Accepted:
                logging.info("First-run onboarding was declined; application remained closed")
                return 0
            config = onboarding.updated_config()
            save_config(config)
        store = AuditStore()
        if auto_shadow:
            persisted_route = {
                "live_trading_enabled": config.live_trading_enabled,
                "market_hours": config.market_hours,
                "order_type": config.order_type,
                "time_in_force": config.time_in_force,
                "settlement_model": config.settlement_model,
            }
            config = auto_shadow_runtime_config(config)
            store.receipt(
                "auto_shadow_runtime",
                "Applied non-persistent regular-hours read-only shadow profile",
                {
                    "persisted_route": persisted_route,
                    "effective_route": {
                        "live_trading_enabled": config.live_trading_enabled,
                        "market_hours": config.market_hours,
                        "order_type": config.order_type,
                        "time_in_force": config.time_in_force,
                        "settlement_model": config.settlement_model,
                    },
                    "saved_config_modified": False,
                    "broker_write_capability": False,
                },
                "warning",
            )
        store.prune_market_history(config.market_history_retention_days)
        broker_adapter = RobinhoodMCPBroker(allow_interactive_auth=not auto_shadow)
        broker = ShadowOnlyBroker(broker_adapter) if auto_shadow else broker_adapter
        controller = TradingController(
            broker,
            config,
            store,
            shadow_only_runtime=auto_shadow,
        )
        window = MainWindow(controller, config, auto_shadow=auto_shadow)
        window.setWindowIcon(app.windowIcon())
        window.show()
        with loop:
            loop.run_forever()
        store.close()
        return 0
    except Exception as exc:
        logging.exception("Fatal startup error")
        if not auto_shadow:
            QMessageBox.critical(None, "GRANDE Alpha failed to start", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

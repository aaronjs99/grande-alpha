from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import math
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from qasync import QEventLoop

from grande_alpha import __version__
from grande_alpha.broker import RobinhoodMCPBroker
from grande_alpha.broker.base import ShadowOnlyBroker
from grande_alpha.config import ONBOARDING_VERSION, data_dir, load_config, save_config
from grande_alpha.controller import TradingController, TradingSnapshot
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.onboarding import OnboardingWizard
from grande_alpha.windows_shortcut import WINDOWS_APP_USER_MODEL_ID

AUTO_SHADOW_HEARTBEAT_INTERVAL_MS = 60_000
AUTO_SHADOW_HEARTBEAT_FILENAME = "scheduled-shadow-heartbeat.json"


def record_auto_shadow_heartbeat(
    path: Path,
    *,
    state: str,
    observed_at: datetime | None = None,
    process_id: int | None = None,
    session_open: bool = False,
    snapshot: TradingSnapshot | None = None,
) -> None:
    """Atomically record liveness plus non-identifying read-only runtime state."""

    if state not in {"running", "stopped", "failed"}:
        raise ValueError(f"Unsupported auto-shadow heartbeat state: {state}")
    timestamp = observed_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("Auto-shadow heartbeat timestamps must be timezone-aware")
    pid = process_id if process_id is not None else os.getpid()
    if pid <= 0:
        raise ValueError("Auto-shadow heartbeat process IDs must be positive")
    runtime = snapshot or TradingSnapshot()
    for value, label in (
        (runtime.shadow_equity, "shadow equity"),
        (runtime.shadow_pnl, "shadow P/L"),
    ):
        if not math.isfinite(value):
            raise ValueError(f"Auto-shadow heartbeat {label} must be finite")
    if runtime.shadow_fills < 0:
        raise ValueError("Auto-shadow heartbeat fill count cannot be negative")

    def timestamp_or_none(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Auto-shadow runtime timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat()

    payload = {
        "schema_version": 1,
        "observed_at_utc": timestamp.astimezone(UTC).isoformat(),
        "process_id": int(pid),
        "state": state,
        "liveness_source": "qt_event_loop_timer",
        "mode": "--auto-shadow",
        "read_only": True,
        "broker_writes": False,
        "live_authority": False,
        "runtime": {
            "connected": bool(runtime.connected),
            "shadow_running": bool(runtime.shadow_running),
            "session_open": bool(session_open),
            "last_refresh_utc": timestamp_or_none(runtime.last_refresh),
            "last_reconcile_at_utc": timestamp_or_none(runtime.last_reconcile_at),
            "shadow_equity": float(runtime.shadow_equity),
            "shadow_pnl": float(runtime.shadow_pnl),
            "shadow_fills": int(runtime.shadow_fills),
        },
        "version": __version__,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{pid}.tmp")
    temporary_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _safe_record_auto_shadow_heartbeat(
    path: Path,
    state: str,
    snapshot: TradingSnapshot | None = None,
    session_open: bool = False,
) -> None:
    try:
        record_auto_shadow_heartbeat(
            path,
            state=state,
            snapshot=snapshot,
            session_open=session_open,
        )
    except (OSError, ValueError):
        logging.getLogger(__name__).exception("AUTO SHADOW heartbeat write failed")


def _auto_shadow_session_is_open(
    controller: TradingController,
    observed_at: datetime | None = None,
) -> bool:
    observed = observed_at or datetime.now(UTC)
    return (
        controller.auto_shadow_start_allowed(observed)
        and observed >= controller.auto_shadow_market_open(observed)
    )


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
    app.setOrganizationName("GRANDE Alpha")
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
    heartbeat_path = data_dir() / AUTO_SHADOW_HEARTBEAT_FILENAME
    heartbeat_timer: QTimer | None = None

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
        if auto_shadow:
            heartbeat_timer = QTimer()
            heartbeat_timer.setInterval(AUTO_SHADOW_HEARTBEAT_INTERVAL_MS)
            heartbeat_timer.timeout.connect(
                lambda: _safe_record_auto_shadow_heartbeat(
                    heartbeat_path,
                    "running",
                    controller.snapshot,
                    session_open=_auto_shadow_session_is_open(controller),
                )
            )
            _safe_record_auto_shadow_heartbeat(
                heartbeat_path,
                "running",
                controller.snapshot,
                session_open=_auto_shadow_session_is_open(controller),
            )
            heartbeat_timer.start()
        with loop:
            loop.run_forever()
        if heartbeat_timer is not None:
            heartbeat_timer.stop()
            _safe_record_auto_shadow_heartbeat(
                heartbeat_path,
                "stopped",
                controller.snapshot,
                session_open=_auto_shadow_session_is_open(controller),
            )
        store.close()
        return 0
    except Exception as exc:
        logging.exception("Fatal startup error")
        if auto_shadow:
            _safe_record_auto_shadow_heartbeat(heartbeat_path, "failed")
        if not auto_shadow:
            QMessageBox.critical(None, "GRANDE Alpha failed to start", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Tray behavior and bounded desktop shutdown for the session window."""

from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon


class SessionWindowLifecycle:
    """Handle close choices without hiding the state of the worker or broker."""

    def _ensure_tray(self) -> QSystemTrayIcon:
        if self._tray_icon is not None:
            return self._tray_icon
        tray = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        tray.setToolTip("GRANDE Alpha local worker")
        menu = QMenu()
        restore = menu.addAction("Open GRANDE Alpha")
        restore.triggered.connect(self._restore_from_tray)
        menu.addSeparator()
        stop_exit = menu.addAction("Stop trading and exit")
        stop_exit.triggered.connect(self._begin_stop_and_exit)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._restore_from_tray()
            if reason in (
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            )
            else None
        )
        self._tray_icon = tray
        return tray

    def _ask_exit_action(self) -> str:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Close GRANDE Alpha")
        dialog.setText("What should happen to the local trading worker?")
        dialog.setInformativeText(
            "Stop fences new local submissions. Already-sent broker orders and filled holdings can remain; "
            "verify them in the broker."
        )
        tray_button = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            tray_button = dialog.addButton("Keep running in tray", QMessageBox.ButtonRole.ActionRole)
        stop_button = dialog.addButton(
            "Stop trading and exit", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        clicked = dialog.clickedButton()
        if tray_button is not None and clicked is tray_button:
            return "tray"
        if clicked is stop_button:
            return "stop"
        if clicked is cancel_button:
            return "cancel"
        return "cancel"

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._closing:
            event.accept()
            return
        if not self._worker_seen and not self._launch_in_progress and not self._status:
            self.status_timer.stop()
            event.accept()
            return
        choice = self._ask_exit_action()
        if choice == "tray":
            QApplication.instance().setQuitOnLastWindowClosed(False)
            tray = self._ensure_tray()
            tray.show()
            tray.showMessage(
                "GRANDE Alpha",
                "The desktop is still open in the tray. Worker state was not changed.",
            )
            self.hide()
            event.ignore()
            return
        if choice == "stop":
            event.ignore()
            self._begin_stop_and_exit()
            return
        event.ignore()

    def _begin_stop_and_exit(self) -> None:
        if self._closing or self._exit_in_flight:
            return
        self.show()
        self.raise_()
        self._spawn(self._shutdown_then_close())

    async def _shutdown_then_close(self) -> None:
        self._stop_requested = True
        if self._approval_dialog is not None:
            self._approval_dialog.reject()
        self._exit_in_flight = True
        self._refresh_controls()
        self._clear_banner()
        result: dict[str, Any] = {}
        warning = ""
        try:
            if self._launch_in_progress and not self._worker_seen:
                deadline = asyncio.get_running_loop().time() + 13
                while self._launch_in_progress and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.05)
                if self._launch_in_progress:
                    raise RuntimeError("The local worker is still starting")
            if self._worker_seen:
                result = await self._request("shutdown", {}, timeout_seconds=8)
                if result.get("stopped") is not True:
                    raise RuntimeError("The worker did not confirm its stop fence")
        except Exception as error:
            try:
                fallback = await self._durable_stop_fallback()
                result = {"stopped": True, **fallback}
                warning = (
                    "Worker communication failed, so GRANDE Alpha wrote the durable local stop fence. "
                    "Broker orders and holdings were not verified."
                )
            except Exception as fallback_error:
                warning = (
                    f"The worker stop response and local stop fence were unavailable ({error}; "
                    f"{fallback_error}). Check the broker immediately."
                )

        warning = warning or self._cleanup_warning(result)
        self._exit_in_flight = False
        self._refresh_controls()
        if warning:
            self._show_exit_warning_then_close(warning)
        else:
            self._complete_close()

    @staticmethod
    def _cleanup_warning(result: dict[str, Any]) -> str:
        if not result:
            return ""
        open_orders = result.get("open_order_ids")
        positions = result.get("positions")
        unresolved = result.get("local_unresolved_references")
        parts = []
        if result.get("broker_verified") is not True:
            parts.append("broker state was not verified")
        if isinstance(open_orders, list) and open_orders:
            parts.append(f"{len(open_orders)} broker order(s) remain open")
        if isinstance(positions, list) and positions:
            parts.append(f"{len(positions)} holding(s) remain")
        if isinstance(unresolved, list) and unresolved:
            parts.append(f"{len(unresolved)} local order record(s) remain unresolved")
        if not parts:
            return ""
        return "Trading is locally stopped, but " + "; ".join(parts) + ". Check the broker directly."

    def _show_exit_warning_then_close(self, message: str) -> None:
        self._log("warning", message)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Stopped — verify the broker")
        box.setText(message)
        box.setInformativeText(
            "GRANDE Alpha will now exit. This warning does not claim cancellation or a flat account."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.finished.connect(lambda _result: self._complete_close())
        self._exit_warning_box = box
        box.open()
        QTimer.singleShot(4000, box.accept)

    def _complete_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.status_timer.stop()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        QApplication.instance().setQuitOnLastWindowClosed(True)
        self.close()
        QApplication.instance().quit()

    def _restore_from_tray(self) -> None:
        QApplication.instance().setQuitOnLastWindowClosed(True)
        self.show()
        self.raise_()
        self.activateWindow()

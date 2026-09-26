"""Streamlined desktop controls for the user-owned local session worker.

The desktop process never owns a broker or a trading controller.  It only
starts (or finds) the separate worker and sends authenticated local control
messages through :class:`LocalControlClient`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from grande_alpha import __version__
from grande_alpha.execution.worker_ipc import LocalControlClient
from grande_alpha.execution.worker_process import launch_worker
from grande_alpha.ui.session_components import (
    StartApprovalDialog,
    StatusCard,
    review_text,
)
from grande_alpha.ui.session_lifecycle import SessionWindowLifecycle
from grande_alpha.ui.session_panels import SessionPanelBuilder
from grande_alpha.ui.session_setup import SessionSetup
from grande_alpha.ui.session_status import SessionStatus


class SessionWindow(SessionWindowLifecycle, SessionSetup, SessionStatus, SessionPanelBuilder, QMainWindow):
    """One guided UI for the independently running local worker.

    ``launcher`` and ``client_factory`` are injectable so the desktop contract
    can be tested without a broker, subprocess, or network connection.
    """

    LIMIT_FIELDS = (
        ("max_order_usd", "Per order", "money"),
        ("max_exposure_usd", "Total exposure", "money"),
        ("max_daily_notional_usd", "Daily traded amount", "money"),
        ("max_daily_loss_usd", "Daily loss stop", "money"),
        ("max_orders", "Orders per day", "integer"),
        ("max_orders_per_minute", "Orders per minute", "integer"),
        ("max_spread_bps", "Maximum spread", "bps"),
        ("max_quote_age_seconds", "Maximum quote age", "seconds"),
    )

    def __init__(
        self,
        runtime_data_dir: Path,
        *,
        launcher: Callable[..., LocalControlClient] = launch_worker,
        client_factory: Callable[[Path], LocalControlClient] = LocalControlClient,
        auto_probe: bool = True,
    ) -> None:
        super().__init__()
        self.data_dir = Path(runtime_data_dir)
        self._launcher = launcher
        self._client_factory = client_factory
        self._client: LocalControlClient | None = client_factory(self.data_dir)
        self._status: dict[str, Any] = {}
        self._review_result: dict[str, Any] | None = None
        self._authorized_scope = ""
        self._candidate_document: dict[str, Any] | None = None
        self._limits_dirty = False
        self._loading_limits = False
        self._busy = False
        self._stop_in_flight = False
        self._stop_requested = False
        self._exit_in_flight = False
        self._research_busy = False
        self._paper_busy = False
        self._status_in_flight = False
        self._worker_seen = False
        self._launch_in_progress = False
        self._closing = False
        self._tray_icon: QSystemTrayIcon | None = None
        self._approval_dialog: StartApprovalDialog | None = None
        self._exit_warning_box: QMessageBox | None = None
        self._tasks: set[asyncio.Task] = set()
        self._layout_mode = ""
        self._primary_panel: QWidget | None = None

        self.setWindowTitle(f"GRANDE Alpha {__version__}")
        self.setMinimumSize(390, 560)
        self.resize(1120, 780)
        self._build_ui()
        self._build_menus()
        self._apply_responsive_layout(self.width(), self.height(), force=True)
        self._refresh_controls()

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(2500)
        self.status_timer.timeout.connect(self._schedule_status)
        self.status_timer.start()
        if auto_probe:
            QTimer.singleShot(0, self._schedule_status)

    # ---------- UI construction ----------
    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root = QWidget()
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        self.outer = QVBoxLayout(root)
        self.outer.setContentsMargins(18, 15, 18, 18)
        self.outer.setSpacing(14)

        self.header = QWidget()
        self.header_layout = QGridLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setHorizontalSpacing(14)
        self.header_layout.setVerticalSpacing(10)
        self.brand_panel = QWidget()
        brand_layout = QVBoxLayout(self.brand_panel)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(1)
        brand = QLabel("GRANDE ALPHA")
        font = QFont(QApplication.font())
        font.setPointSize(18)
        font.setBold(True)
        brand.setFont(font)
        subtitle = QLabel("One local worker · exact limits · explicit start · immediate stop")
        subtitle.setObjectName("settingsDescription")
        subtitle.setWordWrap(True)
        brand_layout.addWidget(brand)
        brand_layout.addWidget(subtitle)
        self.stop_button = QPushButton("STOP TRADING")
        self.stop_button.setObjectName("danger")
        self.stop_button.setMinimumHeight(44)
        self.stop_button.setAccessibleName("Stop the local trading worker")
        self.stop_button.setToolTip(
            "Immediately fence new local submissions. Already-sent broker orders and filled holdings remain."
        )
        self.stop_button.clicked.connect(lambda: self._spawn(self._stop()))
        self.header_layout.addWidget(self.brand_panel, 0, 0)
        self.header_layout.addWidget(self.stop_button, 0, 1)
        self.outer.addWidget(self.header)

        self.banner = QLabel()
        self.banner.setObjectName("validationWarning")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        self.outer.addWidget(self.banner)

        self.status_panel = QWidget()
        self.status_layout = QGridLayout(self.status_panel)
        self.status_layout.setContentsMargins(0, 0, 0, 0)
        self.status_layout.setSpacing(9)
        self.worker_card = StatusCard("Worker", "OFFLINE")
        self.account_card = StatusCard("Account", "Not connected")
        self.session_card = StatusCard("Session", "Stopped")
        self.coverage_card = StatusCard("Data coverage", "Awaiting first cycle")
        self.event_card = StatusCard("Last control update", "Open the app")
        self.status_cards = (
            self.worker_card,
            self.account_card,
            self.session_card,
            self.coverage_card,
            self.event_card,
        )
        self.outer.addWidget(self.status_panel)

        self.content = QWidget()
        self.content_layout = QGridLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(12)
        self.flow_panel = self._build_flow_panel()
        self.monitor_panel = self._build_monitor_panel()
        self.activity_panel = self._build_activity_panel()
        self.outer.addWidget(self.content, 1)

        footer = QLabel(
            "The desktop never holds the broker connection. The separate local worker owns it and "
            "accepts only authenticated machine-local commands."
        )
        footer.setObjectName("settingsDescription")
        footer.setWordWrap(True)
        self.outer.addWidget(footer)


    def _build_menus(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)
        session = menu_bar.addMenu("Session")
        connect = QAction("Connect", self)
        connect.triggered.connect(lambda: self._spawn(self._connect()))
        session.addAction(connect)
        review = QAction("Review exact session", self)
        review.triggered.connect(lambda: self._spawn(self._review()))
        session.addAction(review)
        stop = QAction("Stop trading", self)
        stop.triggered.connect(lambda: self._spawn(self._stop()))
        stop.setShortcut("Ctrl+Shift+S")
        session.addAction(stop)
        session.addSeparator()
        exit_action = QAction("Exit…", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        session.addAction(exit_action)

        help_menu = menu_bar.addMenu("Help")
        about = QAction("About GRANDE Alpha", self)
        about.triggered.connect(
            lambda: QMessageBox.information(
                self,
                "About GRANDE Alpha",
                f"GRANDE Alpha {__version__}\n\n"
                "This desktop is a local control panel for a separate user-owned worker. "
                "It does not guarantee profits, fills, or availability.",
            )
        )
        help_menu.addAction(about)

    # ---------- Responsive layout ----------
    @staticmethod
    def _reflow_grid(layout: QGridLayout, widgets: tuple[QWidget, ...], columns: int) -> None:
        for widget in widgets:
            layout.removeWidget(widget)
        for column in range(len(widgets)):
            layout.setColumnStretch(column, 0)
        for index, widget in enumerate(widgets):
            layout.addWidget(widget, index // columns, index % columns)
        for column in range(columns):
            layout.setColumnStretch(column, 1)

    def _apply_responsive_layout(self, width: int, height: int, *, force: bool = False) -> None:
        narrow = width < 900 or height > width
        mode = "portrait" if narrow else "landscape"
        primary_panel = (
            self.monitor_panel if self._status.get("running") is True else self.flow_panel
        )
        if not force and mode == self._layout_mode and primary_panel is self._primary_panel:
            return
        self._layout_mode = mode
        self._primary_panel = primary_panel
        self.header_layout.removeWidget(self.brand_panel)
        self.header_layout.removeWidget(self.stop_button)
        self.content_layout.removeWidget(self.flow_panel)
        self.content_layout.removeWidget(self.monitor_panel)
        self.content_layout.removeWidget(self.activity_panel)
        self.flow_panel.hide()
        self.monitor_panel.hide()
        self.activity_panel.hide()
        if narrow:
            self.header_layout.addWidget(self.brand_panel, 0, 0)
            self.header_layout.addWidget(self.stop_button, 1, 0)
            self.content_layout.addWidget(primary_panel, 0, 0)
            self.content_layout.addWidget(self.activity_panel, 1, 0)
            primary_panel.show()
            self.activity_panel.show()
            self.content_layout.setRowStretch(0, 0)
            self.content_layout.setRowStretch(1, 1)
            self.content_layout.setColumnStretch(0, 1)
            self._reflow_grid(self.status_layout, self.status_cards, 2)
            self.activity.setMinimumHeight(230)
            self.outer.setContentsMargins(10, 9, 10, 12)
        else:
            self.header_layout.addWidget(self.brand_panel, 0, 0)
            self.header_layout.addWidget(self.stop_button, 0, 1)
            self.header_layout.setColumnStretch(0, 1)
            self.content_layout.addWidget(primary_panel, 0, 0)
            self.content_layout.addWidget(self.activity_panel, 0, 1)
            primary_panel.show()
            self.activity_panel.show()
            self.content_layout.setColumnStretch(0, 3)
            self.content_layout.setColumnStretch(1, 2)
            self.content_layout.setRowStretch(0, 1)
            self._reflow_grid(self.status_layout, self.status_cards, 5)
            self.activity.setMinimumHeight(0)
            self.outer.setContentsMargins(18, 15, 18, 18)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width(), event.size().height())

    # ---------- Worker controls ----------
    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _request(
        self, operation: str, payload: dict[str, Any], *, timeout_seconds: float = 10
    ) -> dict[str, Any]:
        if self._client is None:
            self._client = self._client_factory(self.data_dir)
        return await asyncio.to_thread(
            self._client.request, operation, payload, timeout_seconds=timeout_seconds
        )

    def _write_durable_stop_fence(self) -> dict[str, Any]:
        """Emergency local fence used only when the authenticated IPC is unavailable."""
        from grande_alpha.execution.worker_control import WorkerControlStore

        control_path = self.data_dir / "grande_alpha.db"
        if not control_path.is_file():
            raise RuntimeError("No local worker control record exists")
        control = WorkerControlStore(control_path)
        try:
            state = control.stop()
        finally:
            control.close()
        return {
            "running": False,
            "generation": state.generation,
            "broker_verified": False,
            "orders_cancelled": False,
        }

    async def _durable_stop_fallback(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._write_durable_stop_fence)

    async def _connect(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._clear_banner()
        self._log("info", "Starting or finding the local worker…")
        try:
            self._launch_in_progress = True
            try:
                self._client = await asyncio.to_thread(
                    self._launcher, self.data_dir, timeout_seconds=12
                )
            finally:
                self._launch_in_progress = False
            self._worker_seen = True
            result = await self._request(
                "connect", {"authenticate": True}, timeout_seconds=300
            )
            accounts = result.get("accounts") or []
            agentic = [item.get("masked", "") for item in accounts if item.get("agentic_allowed") is True]
            self.connect_detail.setText(
                "Agentic account available: " + ", ".join(filter(None, agentic))
                if agentic
                else "Connected, but no Agentic-enabled account was reported."
            )
            self._log("success", self.connect_detail.text())
            await self._refresh_status(show_errors=True)
        except Exception as error:
            self._show_error(f"Connection did not complete: {error}")
        finally:
            self._set_busy(False)

    async def _review(self) -> None:
        if self._busy or not self._can_review():
            return
        self._set_busy(True)
        self._clear_banner()
        payload = {
            "candidate_path": str(Path(self.candidate_edit.text().strip()).resolve()),
            "authorization_path": str(Path(self.authorization_edit.text().strip()).resolve()),
            "earnings_database": str(Path(self.earnings_edit.text().strip()).resolve()),
            "poll_seconds": self.poll_seconds.value(),
        }
        try:
            result = await self._request("review", payload, timeout_seconds=300)
            self._worker_seen = True
            self._review_result = result
            self._authorized_scope = ""
            self._render_review(result)
            self._log("success", "Exact account, symbols, window, and limits reviewed. No order was sent.")
            await self._refresh_status(show_errors=True)
        except Exception as error:
            self._review_result = None
            self._show_error(f"Review failed; Start remains locked: {error}")
        finally:
            self._set_busy(False)

    def _render_review(self, review: dict[str, Any]) -> None:
        self.review_summary.setPlainText(review_text(review))
        self.start_summary.setText(
            "Ready for exact-scope approval. Starting can submit real-money orders only within the "
            "reviewed scope; it does not guarantee a trade or a profit."
        )
        self._refresh_controls()

    def _prompt_start(self) -> None:
        if not self._review_result or self._busy or self._stop_in_flight:
            return
        # A previous completed Stop does not permanently block a later explicit
        # approval. A Stop pressed while this dialog is open sets the flag again.
        self._stop_requested = False
        dialog = StartApprovalDialog(self, self._review_result)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._approval_dialog = dialog
        dialog.accepted.connect(
            lambda: self._spawn(self._authorize_and_start(dialog.entry.text().strip()))
        )
        dialog.finished.connect(
            lambda _result: setattr(
                self, "_approval_dialog", None
            ) if self._approval_dialog is dialog else None
        )
        dialog.open()

    async def _authorize_and_start(self, phrase: str) -> None:
        if self._busy or self._stop_in_flight or self._stop_requested or not self._review_result:
            return
        scope = self._review_result.get("scope_digest")
        if not isinstance(scope, str) or phrase != f"AUTHORIZE {scope}":
            self._show_error("The exact approval phrase did not match. Nothing was authorized.")
            return
        self._set_busy(True)
        self._clear_banner()
        try:
            await self._request("authorize", {"phrase": phrase})
            self._authorized_scope = scope
            if self._stop_requested:
                self._log("warning", "Start was cancelled because Stop was pressed.")
                return
            result = await self._request("start", {
                "scope_digest": scope,
                "expected_generation": self._review_result["generation"],
            })
            if self._stop_requested:
                # Stop may race the tiny interval between the local check and
                # pipe delivery. Reapply it after Start returns so Stop wins.
                result = await self._request("stop", {}, timeout_seconds=8)
                self._apply_status(result)
                self._log("warning", "Start completed during Stop; the stop fence was reapplied.")
                return
            self._worker_seen = True
            self._apply_status(result)
            self._log("success", "Session started. The worker will abstain outside its exact rules.")
        except Exception as error:
            self._show_error(f"Session did not start: {error}")
        finally:
            self._set_busy(False)

    async def _stop(self) -> None:
        if self._stop_in_flight or self._exit_in_flight:
            return
        self._stop_requested = True
        if self._approval_dialog is not None:
            self._approval_dialog.reject()
        self._stop_in_flight = True
        self._refresh_controls()
        self._clear_banner()
        try:
            result = await self._request("stop", {}, timeout_seconds=8)
            self._worker_seen = True
            self._apply_status(result)
            self._log(
                "warning",
                "Trading stopped. New local submissions are fenced; verify broker orders and holdings separately.",
            )
        except Exception as error:
            try:
                fallback = await self._durable_stop_fallback()
                self._status.update(
                    running=False,
                    phase="Stopped — local fence",
                    generation=fallback["generation"],
                )
                self._apply_status(dict(self._status))
                self._log(
                    "warning",
                    "Worker IPC was unavailable. The durable local stop fence was written; broker orders and holdings are unverified.",
                )
            except Exception as fallback_error:
                self._show_error(
                    f"Stop could not be confirmed ({error}); local fence also failed ({fallback_error}). "
                    "Use the broker directly now."
                )
        finally:
            self._stop_in_flight = False
            self._refresh_controls()

    async def _toggle_research(self) -> None:
        if self._research_busy or self._exit_in_flight:
            return
        research = self._status.get("research")
        enabled = isinstance(research, dict) and research.get("enabled") is True
        operation = "research_disable" if enabled else "research_enable"
        self._research_busy = True
        self._refresh_controls()
        self._clear_banner()
        try:
            result = await self._request(operation, {}, timeout_seconds=30)
            self._status["research"] = result
            self._render_research_status(result)
            self._log(
                "info",
                "Research MCP disabled."
                if operation == "research_disable"
                else "Research MCP enabled with no order tools.",
            )
            await self._refresh_status(show_errors=False)
        except Exception as error:
            self._show_error(f"Research MCP could not be changed: {error}")
        finally:
            self._research_busy = False
            self._refresh_controls()

    async def _start_paper(self) -> None:
        if self._paper_busy or self._exit_in_flight:
            return
        initial_cash = self.paper_initial_cash.text().strip()
        trade_cash = self.paper_trade_cash.text().strip()
        if not initial_cash or not trade_cash:
            self._show_error("Enter virtual starting cash and virtual cash per entry.")
            return
        self._paper_busy = True
        self._refresh_controls()
        try:
            result = await self._request(
                "research_paper_start",
                {
                    "source": self.paper_source.currentData(),
                    "initial_cash": initial_cash,
                    "trade_cash": trade_cash,
                    "loop_demo": self.paper_loop_demo.isChecked(),
                    "news_enabled": self.paper_news.isChecked(),
                    "social_enabled": self.paper_social.isChecked(),
                    "local_ai_enabled": self.paper_local_ai.isChecked(),
                    "local_ai_model": self.paper_ai_model.text().strip(),
                },
                timeout_seconds=10,
            )
            self._status["research"] = result
            self._render_research_status(result)
            self._log("info", "Virtual paper session started; no broker orders are available.")
            await self._refresh_status(show_errors=False)
        except Exception as error:
            self._show_error(f"Paper session could not start: {error}")
        finally:
            self._paper_busy = False
            self._refresh_controls()

    async def _stop_paper(self) -> None:
        if self._paper_busy or self._exit_in_flight:
            return
        self._paper_busy = True
        self._refresh_controls()
        try:
            result = await self._request("research_paper_stop", {}, timeout_seconds=10)
            self._status["research"] = result
            self._render_research_status(result)
            self._log("info", "Virtual paper session stopped; recorded fills and holdings remain saved.")
            await self._refresh_status(show_errors=False)
        except Exception as error:
            self._show_error(f"Paper session could not stop: {error}")
        finally:
            self._paper_busy = False
            self._refresh_controls()

    # ---------- State and messaging ----------
    def _invalidate_review(self, reason: str) -> None:
        if self._review_result is not None:
            self._log("info", f"Previous review cleared: {reason.lower()}.")
        self._review_result = None
        self._authorized_scope = ""
        self.review_summary.setPlainText(
            "The worker will verify the exact Agentic account and return the account, symbols, "
            "time window, risk limits, allocation policy, and earnings thresholds."
        )
        self.start_summary.setText(
            "Start stays locked until the worker has reviewed this exact candidate. Final approval requires the displayed exact-scope phrase."
        )

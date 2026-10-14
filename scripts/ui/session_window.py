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
from PySide6.QtGui import QAction, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from grande_alpha import __version__
from grande_alpha.execution.worker_ipc import LocalControlClient
from grande_alpha.execution.worker_process import launch_worker
from grande_alpha.execution.worker_status import stop_existing_worker
from grande_alpha.ui.desk_chat import DeskChatDialog
from grande_alpha.ui.desk_theme import DASHBOARD_STYLE
from grande_alpha.ui.research_desk import ResearchDesk
from grande_alpha.ui.session_components import (
    StartApprovalDialog,
    StatusCard,
    review_text,
)
from grande_alpha.ui.session_lifecycle import SessionWindowLifecycle
from grande_alpha.ui.session_panels import SessionPanelBuilder
from grande_alpha.ui.session_setup import SessionSetup
from grande_alpha.ui.session_status import SessionStatus
from grande_alpha.ui.themes import apply_application_theme, current_theme, theme_css


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
        self._status: dict[str, Any] = {"worker_reachable": False, "control_record": "unchecked"}
        self._last_worker_status: dict[str, Any] = {}
        self._last_status_at: str | None = None
        self._status_epoch = 0
        self._review_result: dict[str, Any] | None = None
        self._authorized_scope = ""
        self._candidate_document: dict[str, Any] | None = None
        self._candidate_ready = False
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
        self._candidate_editor: QDialog | None = None
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
        self.setStyleSheet(theme_css(DASHBOARD_STYLE, base="light"))
        root = QWidget()
        root.setObjectName("agentCanvas")
        self.setCentralWidget(root)
        self.outer = QVBoxLayout(root)
        self.outer.setContentsMargins(18, 15, 18, 18)
        self.outer.setSpacing(10)
        self.header = QWidget()
        self.header_layout = QGridLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.brand_panel = QWidget()
        brand_layout = QVBoxLayout(self.brand_panel)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)
        eyebrow = QLabel("GRANDE ALPHA")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("AGENT DESK")
        title.setObjectName("dashboardTitle")
        brand_layout.addWidget(eyebrow)
        brand_layout.addWidget(title)
        self.toolbar = QWidget()
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.setup_button = QPushButton("Setup")
        self.setup_button.setAccessibleName("Open session setup")
        self.setup_button.clicked.connect(lambda: self._show_setup("paper"))
        self.chat_button = QPushButton("AI chat")
        self.chat_button.clicked.connect(self._show_chat)
        self.stop_button = QPushButton("STOP")
        self.stop_button.setObjectName("stopAgent")
        self.stop_button.setMinimumHeight(32)
        self.stop_button.setAccessibleName("Stop trading and paper research")
        self.stop_button.setToolTip("Fence new local submissions and stop research. Verify already-sent orders and holdings with the broker.")
        self.stop_button.clicked.connect(lambda: self._spawn(self._stop()))
        for button in (self.setup_button, self.chat_button, self.stop_button):
            row.addWidget(button)
        self.outer.addWidget(self.header)

        self.status_notice = QLabel("Checking worker…")
        self.status_notice.setObjectName("deskHeartbeat")
        self.status_notice.setWordWrap(True)
        self.status_notice.setAccessibleName("Worker and trading status")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_notice, 1)
        self.refresh_status_button = QPushButton("Refresh")
        self.refresh_status_button.setAccessibleName("Refresh local worker status")
        self.refresh_status_button.clicked.connect(lambda: self._spawn(self._refresh_status(show_errors=False)))
        status_row.addWidget(self.refresh_status_button)
        self.outer.addLayout(status_row)
        self.banner = QLabel()
        self.banner.setObjectName("validationWarning")
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.TextFormat.PlainText)
        self.banner.hide()
        self.outer.addWidget(self.banner)

        self.desk = ResearchDesk()
        self.desk_scroll = QScrollArea()
        self.desk_scroll.setWidgetResizable(True)
        self.desk_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.desk_scroll.setWidget(self.desk)
        self.outer.addWidget(self.desk_scroll, 1)
        self.desk_controls = QHBoxLayout()
        self.desk_start_button = QPushButton("Start paper")
        self.desk_start_button.setObjectName("primary")
        self.desk_start_button.clicked.connect(lambda: self._show_setup("paper"))
        self.desk_stop_button = QPushButton("Stop paper")
        self.desk_stop_button.clicked.connect(lambda: self._spawn(self._stop_paper()))
        self.details_button = QPushButton("Session details")
        self.details_button.clicked.connect(lambda: self._show_setup("details"))
        self.desk_controls.addWidget(self.desk_start_button)
        self.desk_controls.addWidget(self.desk_stop_button)
        self.desk_controls.addStretch()
        self.desk_controls.addWidget(self.details_button)
        self.outer.addLayout(self.desk_controls)

        self.setup_dialog = QDialog(self)
        self.setup_dialog.setWindowTitle("GRANDE · Session setup")
        self.setup_dialog.setMinimumSize(340, 430)
        setup_layout = QVBoxLayout(self.setup_dialog)
        self.setup_tabs = QTabWidget()
        self.flow_panel = self._build_flow_panel()
        self.paper_panel = self._build_paper_panel()
        self.details_panel = QWidget()
        details_layout = QVBoxLayout(self.details_panel)
        self.status_panel = QWidget()
        self.status_layout = QGridLayout(self.status_panel)
        self.worker_card = StatusCard("Worker", "CHECKING")
        self.account_card = StatusCard("Account", "Unverified")
        self.session_card = StatusCard("Session", "Unverified")
        self.coverage_card = StatusCard("Data coverage", "Awaiting first cycle")
        self.event_card = StatusCard("Last control update", "Open the app")
        self.status_cards = (self.worker_card, self.account_card, self.session_card,
                             self.coverage_card, self.event_card)
        self._reflow_grid(self.status_layout, self.status_cards, 2)
        details_layout.addWidget(self.status_panel)
        self.recovery_detail = QLabel("Checking the worker")
        self.recovery_detail.setWordWrap(True)
        self.recovery_detail.setTextFormat(Qt.TextFormat.PlainText)
        details_layout.addWidget(self.recovery_detail)
        self.monitor_panel = self._build_monitor_panel()
        details_layout.addWidget(self.monitor_panel)
        details_layout.addWidget(self.research_status)
        details_layout.addWidget(self.research_button)
        self.activity_panel = self._build_activity_panel()
        details_layout.addWidget(self.activity_panel)
        for title, widget in (("Paper", self.paper_panel), ("Broker session", self.flow_panel),
                              ("Details", self.details_panel)):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(widget)
            self.setup_tabs.addTab(scroll, title)
        setup_layout.addWidget(self.setup_tabs)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.setup_dialog.reject)
        setup_layout.addWidget(close)
        self._chat_dialog = DeskChatDialog(self)
        self._update_paper_options()

    def _show_setup(self, section: str = "paper") -> None:
        self.setup_tabs.setCurrentIndex({"paper": 0, "broker": 1, "details": 2}[section])
        self.setup_dialog.resize(min(720, self.width() - 20), min(740, self.height() - 40))
        self.setup_dialog.show()
        self.setup_dialog.raise_()
        self.setup_dialog.activateWindow()

    def _show_chat(self) -> None:
        self._chat_dialog.resize(min(540, self.width() - 20), min(640, self.height() - 40))
        self._chat_dialog.show()
        self._chat_dialog.raise_()
        self._chat_dialog.activateWindow()

    def _toggle_theme(self) -> None:
        apply_application_theme("dark" if current_theme() == "light" else "light")
        self.setStyleSheet(theme_css(DASHBOARD_STYLE, base="light"))
        self.desk.apply_theme()

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)
        session = menu_bar.addMenu("Session")
        setup = session.addAction("Broker session setup…")
        setup.triggered.connect(lambda: self._show_setup("broker"))
        self._connect_action = QAction("Connect", self)
        self._connect_action.triggered.connect(lambda: self._spawn(self._connect()))
        session.addAction(self._connect_action)
        review = QAction("Review exact session", self)
        review.triggered.connect(lambda: self._show_setup("broker"))
        session.addAction(review)
        stop = QAction("Stop trading", self)
        stop.triggered.connect(lambda: self._spawn(self._stop()))
        stop.setShortcut("Ctrl+Shift+S")
        session.addAction(stop)
        refresh = QAction("Refresh status", self)
        refresh.setShortcut("F5")
        refresh.triggered.connect(lambda: self._spawn(self._refresh_status(show_errors=False)))
        session.addAction(refresh)
        session.addSeparator()
        exit_action = QAction("Exit…", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        session.addAction(exit_action)

        appearance = menu_bar.addMenu("View")
        theme = appearance.addAction("Switch light / dark")
        theme.triggered.connect(self._toggle_theme)
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
        narrow = width < 750
        mode = "portrait" if narrow else "landscape"
        self._layout_mode = mode
        self.header_layout.removeWidget(self.brand_panel)
        self.header_layout.removeWidget(self.toolbar)
        self.header_layout.addWidget(self.brand_panel, 0, 0)
        self.header_layout.addWidget(self.toolbar, 1 if narrow else 0, 0 if narrow else 1)
        self.header_layout.setColumnStretch(0, 1)
        self.outer.setContentsMargins(10 if narrow else 18, 10, 10 if narrow else 18, 12)
        self.desk.reflow(width - (40 if narrow else 55))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width(), event.size().height())

    # ---------- Worker controls ----------
    def _spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

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
        return stop_existing_worker(self.data_dir)

    async def _durable_stop_fallback(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._write_durable_stop_fence)

    async def _connect(self) -> None:
        if self._busy or self._paper_busy or self._stop_in_flight or self._exit_in_flight or not self._can_connect():
            return
        self._set_busy(True)
        self._clear_banner()
        self._log("info", "Starting or finding the local worker…")
        try:
            self._launch_in_progress = True
            try:
                self._client = await asyncio.to_thread(
                    self._launcher, self.data_dir, timeout_seconds=12, recover_existing=False
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
        self._status_epoch += 1
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
            self._status_epoch += 1
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
        if not self._review_result or not self.start_button.isEnabled():
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
        if (self._busy or self._stop_in_flight or self._stop_requested or self._exit_in_flight
                or not self._review_result or self._status.get("worker_reachable") is not True
                or self._status.get("connected") is not True):
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
        self._status_epoch += 1
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
                self._apply_disconnected_status(fallback)
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
        self._status_epoch += 1
        self._research_busy = True
        self._refresh_controls()
        self._clear_banner()
        try:
            result = await self._request(operation, {}, timeout_seconds=30)
            self._status_epoch += 1
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
        if not self._can_start_paper():
            return
        self._stop_requested = False
        self._status_epoch += 1
        initial_cash = self.paper_initial_cash.text().strip()
        trade_cash = self.paper_trade_cash.text().strip()
        if not initial_cash or not trade_cash:
            self._show_error("Enter virtual starting cash and virtual cash per entry.")
            return
        self._paper_busy = True
        self._refresh_controls()
        try:
            if self._status.get("worker_reachable") is not True:
                self._launch_in_progress = True
                try:
                    self._client = await asyncio.to_thread(self._launcher, self.data_dir,
                                                          timeout_seconds=12, recover_existing=False)
                finally:
                    self._launch_in_progress = False
                self._worker_seen = True
            if self._stop_requested or self._exit_in_flight:
                return
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
            if self._stop_requested or self._exit_in_flight:
                await self._request("stop", {}, timeout_seconds=8)
                await self._refresh_status(show_errors=False)
                return
            self._status_epoch += 1
            self._status["research"] = result
            self._render_research_status(result)
            self.setup_dialog.hide()
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
        self._status_epoch += 1
        self._refresh_controls()
        try:
            result = await self._request("research_paper_stop", {}, timeout_seconds=10)
            self._status_epoch += 1
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
        if self._approval_dialog is not None:
            self._approval_dialog.reject()
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

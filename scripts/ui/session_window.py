"""Streamlined desktop controls for the user-owned local session worker.

The desktop process never owns a broker or a trading controller.  It only
starts (or finds) the separate worker and sends authenticated local control
messages through :class:`LocalControlClient`.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from grande_alpha import __version__
from grande_alpha.execution.worker_ipc import LocalControlClient
from grande_alpha.execution.worker_process import launch_worker
from grande_alpha.ui.session_components import (
    PreciseDoubleSpinBox,
    StartApprovalDialog,
    StatusCard,
    display_value,
    review_text,
)


class SessionWindow(QMainWindow):
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

    def _build_monitor_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(11)

        heading = QLabel("Session monitor")
        heading.setObjectName("dialogTitle")
        self.monitor_summary = QLabel("Start a session to monitor it here.")
        self.monitor_summary.setObjectName("settingsDescription")
        self.monitor_summary.setWordWrap(True)

        cycle_heading = QLabel("Latest strategy check")
        cycle_heading.setObjectName("settingsDescription")
        self.monitor_cycle = QLabel("No cycle reported yet")
        self.monitor_cycle.setObjectName("dialogTitle")
        self.monitor_cycle.setWordWrap(True)
        self.monitor_cycle_time = QLabel("")
        self.monitor_cycle_time.setObjectName("settingsDescription")

        data_heading = QLabel("Market data")
        data_heading.setObjectName("settingsDescription")
        self.monitor_data = QLabel("No market snapshot reported yet")
        self.monitor_data.setWordWrap(True)

        self.monitor_note = QLabel(
            "A recorded broker response is not proof of a fill. Verify orders, fills, balances, "
            "and positions with Robinhood."
        )
        self.monitor_note.setObjectName("settingsDescription")
        self.monitor_note.setWordWrap(True)

        for widget in (
            heading,
            self.monitor_summary,
            cycle_heading,
            self.monitor_cycle,
            self.monitor_cycle_time,
            data_heading,
            self.monitor_data,
            self.monitor_note,
        ):
            layout.addWidget(widget)
        layout.addStretch()
        return panel

    def _build_flow_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(11)

        heading = QLabel("Start a bounded session")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)

        self.connect_group = QGroupBox("1  Connect")
        connect_layout = QVBoxLayout(self.connect_group)
        connect_text = QLabel(
            "Open the broker sign-in only if needed, then verify the Agentic account exposed to the worker."
        )
        connect_text.setObjectName("settingsDescription")
        connect_text.setWordWrap(True)
        self.connect_button = QPushButton("Connect Robinhood")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(lambda: self._spawn(self._connect()))
        self.connect_detail = QLabel("Not connected")
        self.connect_detail.setObjectName("settingsDescription")
        self.connect_detail.setWordWrap(True)
        connect_layout.addWidget(connect_text)
        connect_layout.addWidget(self.connect_button)
        connect_layout.addWidget(self.connect_detail)
        layout.addWidget(self.connect_group)

        self.limits_group = QGroupBox("2  Set limits")
        limits_layout = QVBoxLayout(self.limits_group)
        help_text = QLabel(
            "Choose an existing strategy candidate, adjust only its risk envelope, then save a new copy. "
            "The strategy, account, symbols, time window, and research thresholds remain unchanged."
        )
        help_text.setObjectName("settingsDescription")
        help_text.setWordWrap(True)
        limits_layout.addWidget(help_text)

        candidate_row = QHBoxLayout()
        self.candidate_edit = QLineEdit()
        self.candidate_edit.setPlaceholderText("Strategy + limits candidate (.json)")
        self.candidate_edit.setAccessibleName("Strategy and limits candidate file")
        self.candidate_edit.editingFinished.connect(self._candidate_path_entered)
        candidate_button = QPushButton("Choose…")
        candidate_button.clicked.connect(self._choose_candidate)
        candidate_row.addWidget(self.candidate_edit, 1)
        candidate_row.addWidget(candidate_button)
        limits_layout.addLayout(candidate_row)

        self.limits_form = QFormLayout()
        self.limit_inputs: dict[str, QDoubleSpinBox | QSpinBox] = {}
        for key, label, kind in self.LIMIT_FIELDS:
            if kind == "integer":
                control: QDoubleSpinBox | QSpinBox = QSpinBox()
                control.setRange(1, 1_000_000_000)
            else:
                control = PreciseDoubleSpinBox()
                control.setDecimals(8)
                control.setRange(0.00000001, 1_000_000_000_000.0)
                if kind == "money":
                    control.setPrefix("$")
                elif kind == "bps":
                    control.setSuffix(" bps")
                elif kind == "seconds":
                    control.setSuffix(" s")
            control.setEnabled(False)
            control.setAccessibleName(label)
            control.valueChanged.connect(self._limit_changed)
            self.limit_inputs[key] = control
            self.limits_form.addRow(label, control)
        limits_layout.addLayout(self.limits_form)

        support_form = QFormLayout()
        self.authorization_edit = QLineEdit(str(self.data_dir / "mixed-authorization.json"))
        self.authorization_edit.setAccessibleName("Local exact-scope authorization file")
        self.authorization_edit.textChanged.connect(self._review_input_changed)
        authorization_row = self._path_row(self.authorization_edit, self._choose_authorization)
        support_form.addRow("Local permit", authorization_row)
        self.earnings_edit = QLineEdit(str(self.data_dir / "earnings.db"))
        self.earnings_edit.setAccessibleName("Local earnings observations database")
        self.earnings_edit.textChanged.connect(self._review_input_changed)
        earnings_row = self._path_row(self.earnings_edit, self._choose_earnings_database)
        support_form.addRow("Earnings data", earnings_row)
        self.poll_seconds = QDoubleSpinBox()
        self.poll_seconds.setRange(0.25, 3600.0)
        self.poll_seconds.setDecimals(2)
        self.poll_seconds.setValue(5.0)
        self.poll_seconds.setSuffix(" s")
        self.poll_seconds.valueChanged.connect(self._review_input_changed)
        support_form.addRow("Decision cadence", self.poll_seconds)
        limits_layout.addLayout(support_form)

        self.save_limits_button = QPushButton("Save limits as a new candidate…")
        self.save_limits_button.setEnabled(False)
        self.save_limits_button.clicked.connect(self._choose_limit_destination)
        self.limit_state = QLabel("Choose a candidate to load its current limits.")
        self.limit_state.setObjectName("settingsDescription")
        self.limit_state.setWordWrap(True)
        limits_layout.addWidget(self.save_limits_button)
        limits_layout.addWidget(self.limit_state)
        layout.addWidget(self.limits_group)

        self.review_group = QGroupBox("3  Review")
        review_layout = QVBoxLayout(self.review_group)
        self.review_summary = QPlainTextEdit(
            "The worker will verify the exact Agentic account and return the account, symbols, "
            "time window, risk limits, allocation policy, and earnings thresholds."
        )
        self.review_summary.setReadOnly(True)
        self.review_summary.setAccessibleName("Complete reviewed session")
        self.review_summary.setMinimumHeight(92)
        self.review_summary.setMaximumHeight(260)
        self.review_button = QPushButton("Review exact session")
        self.review_button.clicked.connect(lambda: self._spawn(self._review()))
        review_layout.addWidget(self.review_summary)
        review_layout.addWidget(self.review_button)
        layout.addWidget(self.review_group)

        self.start_group = QGroupBox("4  Start")
        start_layout = QVBoxLayout(self.start_group)
        self.start_summary = QLabel(
            "Start stays locked until the worker has reviewed this exact candidate. Final approval requires the displayed exact-scope phrase."
        )
        self.start_summary.setObjectName("settingsDescription")
        self.start_summary.setWordWrap(True)
        self.start_button = QPushButton("Review first")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._prompt_start)
        start_layout.addWidget(self.start_summary)
        start_layout.addWidget(self.start_button)
        layout.addWidget(self.start_group)

        self.research_disclosure = QPushButton("Optional research & paper  ▸")
        self.research_disclosure.setCheckable(True)
        self.research_disclosure.setToolTip(
            "Show controls for the worker-hosted research-only MCP bridge"
        )
        self.research_disclosure.toggled.connect(self._toggle_research_panel)
        layout.addWidget(self.research_disclosure)
        self.research_panel = QFrame()
        self.research_panel.setObjectName("card")
        research_layout = QVBoxLayout(self.research_panel)
        research_layout.setContentsMargins(12, 10, 12, 12)
        research_note = QLabel(
            "Optional research bridge hosted by the same worker and broker session. It can read research "
            "context, but order tools are structurally unavailable. Stop, Revoke, and Exit disable it."
        )
        research_note.setObjectName("settingsDescription")
        research_note.setWordWrap(True)
        self.research_status = QLabel("Off · orders unavailable")
        self.research_status.setWordWrap(True)
        self.research_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.research_button = QPushButton("Enable research MCP")
        self.research_button.clicked.connect(lambda: self._spawn(self._toggle_research()))
        research_layout.addWidget(research_note)
        research_layout.addWidget(self.research_status)
        research_layout.addWidget(self.research_button)

        paper_note = QLabel(
            "Virtual paper only · no broker orders. Choose the amounts yourself; current quotes require a reviewed account."
        )
        paper_note.setObjectName("settingsDescription")
        paper_note.setWordWrap(True)
        research_layout.addWidget(paper_note)
        paper_form = QFormLayout()
        self.paper_source = QComboBox()
        self.paper_source.addItem("Synthetic demo", "demo")
        self.paper_source.addItem("Current broker quotes", "broker_quotes")
        self.paper_initial_cash = QLineEdit()
        self.paper_initial_cash.setPlaceholderText("Virtual starting cash")
        self.paper_trade_cash = QLineEdit()
        self.paper_trade_cash.setPlaceholderText("Virtual cash per entry")
        self.paper_loop_demo = QCheckBox("Repeat the synthetic demo until stopped")
        self.paper_news = QCheckBox("Include public news headlines")
        self.paper_social = QCheckBox("Include unverified public Bluesky posts")
        self.paper_social.setEnabled(False)
        self.paper_local_ai = QCheckBox("Use a local Ollama model for advisory context")
        self.paper_ai_model = QLineEdit()
        self.paper_ai_model.setPlaceholderText("Installed local model name")
        self.paper_ai_model.setEnabled(False)
        paper_form.addRow("Price source", self.paper_source)
        paper_form.addRow("Starting amount", self.paper_initial_cash)
        paper_form.addRow("Amount per entry", self.paper_trade_cash)
        paper_form.addRow("Demo", self.paper_loop_demo)
        paper_form.addRow("Research", self.paper_news)
        paper_form.addRow("Social", self.paper_social)
        paper_form.addRow("Local AI", self.paper_local_ai)
        paper_form.addRow("Ollama model", self.paper_ai_model)
        research_layout.addLayout(paper_form)
        paper_buttons = QHBoxLayout()
        self.paper_start_button = QPushButton("Start virtual paper")
        self.paper_start_button.clicked.connect(lambda: self._spawn(self._start_paper()))
        self.paper_stop_button = QPushButton("Stop paper")
        self.paper_stop_button.clicked.connect(lambda: self._spawn(self._stop_paper()))
        self.paper_stop_button.setEnabled(False)
        paper_buttons.addWidget(self.paper_start_button)
        paper_buttons.addWidget(self.paper_stop_button)
        research_layout.addLayout(paper_buttons)
        self.paper_initial_cash.textChanged.connect(self._refresh_controls)
        self.paper_trade_cash.textChanged.connect(self._refresh_controls)
        self.paper_source.currentIndexChanged.connect(self._refresh_controls)
        self.paper_source.currentIndexChanged.connect(self._update_paper_options)
        self.paper_loop_demo.toggled.connect(self._refresh_controls)
        self.paper_news.toggled.connect(self.paper_social.setEnabled)
        self.paper_news.toggled.connect(
            lambda enabled: self.paper_social.setChecked(False) if not enabled else None
        )
        self.paper_local_ai.toggled.connect(self.paper_ai_model.setEnabled)
        self.paper_local_ai.toggled.connect(self._refresh_controls)
        self.paper_ai_model.textChanged.connect(self._refresh_controls)
        self._update_paper_options()
        self.research_panel.setVisible(False)
        layout.addWidget(self.research_panel)
        layout.addStretch()
        return panel

    def _toggle_research_panel(self, expanded: bool) -> None:
        self.research_panel.setVisible(expanded)
        self.research_disclosure.setText(
            "Optional research & paper  ▾" if expanded else "Optional research & paper  ▸"
        )

    def _update_paper_options(self) -> None:
        current_quotes = self.paper_source.currentData() == "broker_quotes"
        self.paper_news.setEnabled(current_quotes)
        if not current_quotes:
            self.paper_news.setChecked(False)
            self.paper_social.setChecked(False)
            self.paper_local_ai.setChecked(False)
        self.paper_social.setEnabled(current_quotes and self.paper_news.isChecked())
        self.paper_local_ai.setEnabled(current_quotes)
        self.paper_ai_model.setEnabled(current_quotes and self.paper_local_ai.isChecked())
        self._refresh_controls()

    @staticmethod
    def _path_row(line_edit: QLineEdit, callback: Callable[[], None]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        button = QPushButton("Choose…")
        button.clicked.connect(callback)
        layout.addWidget(line_edit, 1)
        layout.addWidget(button)
        return row

    def _build_activity_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        title = QLabel("Activity")
        title.setObjectName("dialogTitle")
        note = QLabel("Local control events only. Broker confirmations remain authoritative.")
        note.setObjectName("settingsDescription")
        note.setWordWrap(True)
        self.activity = QListWidget()
        self.activity.setAccessibleName("Session activity")
        self.activity.setAlternatingRowColors(True)
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addWidget(self.activity, 1)
        self._log("info", "Ready. Connect to begin; no worker was started by opening this window.")
        return panel

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

    # ---------- Local candidate editing ----------
    def _choose_candidate(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose strategy and limits candidate", str(self.data_dir), "JSON files (*.json)"
        )
        if path:
            self.candidate_edit.setText(path)
            self._load_candidate(Path(path))

    def _candidate_path_entered(self) -> None:
        text = self.candidate_edit.text().strip()
        if text:
            self._load_candidate(Path(text))
        else:
            self._candidate_document = None
            self._limits_dirty = False
            self._invalidate_review("Candidate cleared")
            self._refresh_controls()

    def _load_candidate(self, path: Path) -> None:
        try:
            if not path.is_file() or path.stat().st_size > 65_536:
                raise ValueError("Choose an existing candidate no larger than 64 KiB")
            document = json.loads(path.read_text(encoding="utf-8"))
            scope = document.get("scope") if isinstance(document, dict) else None
            if not isinstance(scope, dict):
                raise ValueError("The candidate has no execution scope")
            missing = [key for key, _label, _kind in self.LIMIT_FIELDS if key not in scope]
            if missing:
                raise ValueError(f"The candidate is missing {', '.join(missing)}")
            self._loading_limits = True
            for key, _label, kind in self.LIMIT_FIELDS:
                raw_value = scope[key]
                if kind == "integer":
                    if type(raw_value) is not int or raw_value <= 0:
                        raise ValueError(f"{key} must be a positive whole number")
                    self.limit_inputs[key].setValue(raw_value)
                else:
                    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                        raise ValueError(f"{key} must be numeric")
                    numeric = float(raw_value)
                    self.limit_inputs[key].setValue(numeric)
                    if self.limit_inputs[key].value() != numeric:
                        raise ValueError(
                            f"{key} uses precision or range this visual editor cannot represent"
                        )
                self.limit_inputs[key].setEnabled(True)
            self._candidate_document = document
            self._limits_dirty = False
            self.candidate_edit.setText(str(path.resolve()))
            symbols = ", ".join(scope.get("allowed_symbols") or ()) or "no symbols"
            self.limit_state.setText(
                f"Loaded {symbols}. Adjust the risk envelope only, or continue with the unchanged candidate."
            )
            self._invalidate_review("Candidate loaded")
            self._clear_banner()
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            self._candidate_document = None
            self._limits_dirty = False
            for control in self.limit_inputs.values():
                control.setEnabled(False)
            self.limit_state.setText("Candidate not ready.")
            self._show_error(f"Could not load the candidate: {error}")
        finally:
            self._loading_limits = False
            self._refresh_controls()

    def _limit_changed(self, *_args) -> None:
        if self._loading_limits or self._candidate_document is None:
            return
        self._limits_dirty = True
        self.limit_state.setText("Limits changed. Save a new candidate before Review.")
        self._invalidate_review("Limits changed")
        self._refresh_controls()

    def _review_input_changed(self, *_args) -> None:
        self._invalidate_review("Session inputs changed")
        self._refresh_controls()

    def _choose_limit_destination(self) -> None:
        source = Path(self.candidate_edit.text().strip()) if self.candidate_edit.text().strip() else None
        default = self.data_dir / "session-candidate.json"
        if source is not None:
            default = source.with_name(f"{source.stem}-bounded.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save bounded candidate", str(default), "JSON files (*.json)"
        )
        if path:
            target = Path(path)
            source = Path(self.candidate_edit.text().strip()).resolve()
            if target.resolve() == source:
                self._show_error("Choose a different file. The loaded source candidate is never overwritten.")
                return
            overwrite = False
            if target.exists():
                answer = QMessageBox.question(
                    self,
                    "Replace bounded candidate?",
                    f"{target.name} already exists. Replace that copy? The loaded source remains unchanged.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                overwrite = True
            try:
                self._save_limits(target, overwrite=overwrite)
            except (OSError, TypeError, ValueError) as error:
                self._show_error(f"Limits were not saved: {error}")

    def _save_limits(self, target: Path, *, overwrite: bool = False) -> None:
        if self._candidate_document is None:
            raise ValueError("Load a candidate first")
        values = {key: control.value() for key, control in self.limit_inputs.items()}
        for key in ("max_orders", "max_orders_per_minute"):
            values[key] = int(values[key])
        if values["max_order_usd"] > min(
            values["max_exposure_usd"], values["max_daily_notional_usd"]
        ):
            raise ValueError("Per-order limit cannot exceed exposure or daily traded amount")
        document = copy.deepcopy(self._candidate_document)
        document["scope"].update(values)
        target = target.resolve()
        source = Path(self.candidate_edit.text().strip()).resolve()
        if target == source:
            raise ValueError("The loaded source candidate is never overwritten; choose a new file")
        if target.exists() and not overwrite:
            raise ValueError("The destination already exists; explicit overwrite confirmation is required")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(target)
        self._candidate_document = document
        self.candidate_edit.setText(str(target))
        self._limits_dirty = False
        self.limit_state.setText(f"Saved bounded candidate: {target.name}")
        self._invalidate_review("Bounded candidate saved")
        self._clear_banner()
        self._log("info", f"Saved a new candidate copy: {target.name}")
        self._refresh_controls()

    def _choose_authorization(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose local permit file",
            self.authorization_edit.text() or str(self.data_dir / "mixed-authorization.json"),
            "JSON files (*.json)",
        )
        if path:
            self.authorization_edit.setText(path)

    def _choose_earnings_database(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose earnings observations",
            self.earnings_edit.text() or str(self.data_dir),
            "SQLite databases (*.db *.sqlite);;All files (*)",
        )
        if path:
            self.earnings_edit.setText(path)

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

    def _schedule_status(self) -> None:
        if self._status_in_flight or self._closing:
            return
        self._spawn(self._refresh_status(show_errors=False))

    async def _refresh_status(self, *, show_errors: bool) -> None:
        if self._status_in_flight:
            return
        self._status_in_flight = True
        try:
            status = await self._request("status", {}, timeout_seconds=3)
            self._worker_seen = True
            self._apply_status(status)
        except Exception as error:
            previous = self._status
            self._status = {}
            self.worker_card.value.setText("OFFLINE")
            self.session_card.value.setText("Stopped")
            if previous:
                self._log("warning", "Local worker is no longer reachable.")
            if show_errors:
                self._show_error(f"Worker status is unavailable: {error}")
            self._refresh_controls()
        finally:
            self._status_in_flight = False

    def _apply_status(self, status: dict[str, Any]) -> None:
        was_running = self._status.get("running") is True
        previous_phase = self._status.get("phase")
        previous_error = self._status.get("error")
        self._status = status
        if self._review_result is not None:
            reviewed_scope = self._review_result.get("scope_digest")
            worker_scope = status.get("scope_digest")
            if worker_scope and reviewed_scope != worker_scope:
                self._invalidate_review("Worker scope changed in another client")
        running = status.get("running") is True
        connected = status.get("connected") is True
        phase = str(status.get("phase") or ("Running" if running else "Stopped"))
        self.worker_card.value.setText("RUNNING" if running else "READY")
        self.session_card.value.setText(phase)
        account = status.get("account_masked") or ""
        if account:
            self.account_card.value.setText(str(account))
        elif connected:
            self.account_card.value.setText("Connected")
        else:
            self.account_card.value.setText("Not connected")
        if connected:
            self.connect_detail.setText(
                f"Worker connected{f' to {account}' if account else ''}."
            )
        error = str(status.get("error") or "")
        self._render_research_status(status.get("research"))
        coverage = status.get("data_coverage")
        if isinstance(coverage, dict) and coverage:
            market = "market ready" if coverage.get("market_history_ready") is True else "market warming"
            events = coverage.get("stock_events", 0)
            missing = coverage.get("missing")
            missing_count = len(missing) if isinstance(missing, list) else 0
            self.coverage_card.value.setText(
                f"{market} · {events} events · {missing_count} missing"
            )
            self.coverage_card.setToolTip(display_value(coverage))
        else:
            self.coverage_card.value.setText("Awaiting first cycle")
            self.coverage_card.setToolTip("The worker has not reported a completed data snapshot.")
        self._update_monitor(status, phase)
        if running != was_running:
            self._apply_responsive_layout(self.width(), self.height(), force=True)
        if phase != previous_phase and previous_phase is not None:
            self._log("info", f"Worker phase: {phase}")
        if error and error != previous_error:
            self._show_error(f"Worker reported: {error}")
        self._refresh_controls()

    def _update_monitor(self, status: dict[str, Any], phase: str) -> None:
        running = status.get("running") is True
        if not running:
            self.monitor_summary.setText("Start a session to monitor it here.")
        elif phase == "Waiting for market":
            self.monitor_summary.setText(
                "Waiting for the supported market window. The worker stays active but does not "
                "request a strategy snapshot or submit orders while the market is closed."
            )
        else:
            self.monitor_summary.setText(
                "The worker checks current data against the approved allocation and limits. "
                "Use STOP TRADING to block further automatic submissions."
            )

        cycle = status.get("last_cycle")
        if isinstance(cycle, dict) and isinstance(cycle.get("status"), str):
            labels = {
                "NO_TICKET": "No portfolio change needed",
                "THESIS_NOT_VALID": "Entry skipped — research condition not met",
                "BELOW_MINIMUM": "No order — below the minimum size",
                "RISK_BLOCKED": "Risk controls blocked a new order",
                "RESPONSE_RECORDED": "Broker response recorded",
                "BUSY": "A strategy check is already in progress",
            }
            label = labels.get(cycle["status"], "Strategy check completed")
            self.monitor_cycle.setText(label)
            cycle_time = self._age_text(cycle.get("at"))
            self.monitor_cycle_time.setText(
                f"Last check {cycle_time}. "
                + (
                    "The broker response is not a fill confirmation."
                    if cycle.get("submitted") is True
                    else "No order submission was reported for this check."
                )
            )
        else:
            self.monitor_cycle.setText("No cycle reported yet")
            self.monitor_cycle_time.setText("")

        data_time = self._age_text(status.get("last_data_at"))
        self.monitor_data.setText(
            "No market snapshot reported yet"
            if data_time is None
            else f"Latest snapshot {data_time}"
        )

    @staticmethod
    def _age_text(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                return "time unavailable (no timezone)"
            seconds = (datetime.now().astimezone() - instant.astimezone()).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return "time unavailable"
        if seconds < -5:
            return "time unavailable (device clock differs)"
        seconds = max(0, seconds)
        if seconds < 60:
            return "just now" if seconds < 5 else f"{int(seconds)} seconds ago"
        if seconds < 3600:
            minutes = int(seconds // 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m ago" if minutes else f"{hours}h ago"

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

    def _can_review(self) -> bool:
        if self._limits_dirty or self._candidate_document is None:
            return False
        candidate = Path(self.candidate_edit.text().strip()) if self.candidate_edit.text().strip() else None
        return bool(
            self._status.get("connected") is True
            and not self._status.get("running")
            and candidate is not None
            and candidate.is_file()
            and self.authorization_edit.text().strip()
            and self.earnings_edit.text().strip()
        )

    def _refresh_controls(self) -> None:
        connected = self._status.get("connected") is True
        running = self._status.get("running") is True
        reviewed_scope = self._review_result.get("scope_digest") if self._review_result else ""
        transition_busy = self._busy or self._stop_in_flight or self._exit_in_flight
        self.connect_button.setEnabled(not transition_busy and not running)
        self.connect_button.setText("Connected" if connected else "Connect Robinhood")
        self.limits_group.setEnabled(connected and not running and not transition_busy)
        self.save_limits_button.setEnabled(
            connected and not running and not transition_busy and self._candidate_document is not None
            and self._limits_dirty
        )
        self.review_button.setEnabled(not transition_busy and self._can_review())
        self.start_button.setEnabled(
            bool(reviewed_scope) and connected and not running and not transition_busy
        )
        self.start_button.setText("Approve and start" if reviewed_scope else "Review first")
        self.research_button.setEnabled(
            connected and bool(self._status.get("account")) and not transition_busy
            and not self._research_busy and not self._status.get("running")
        )
        research = self._status.get("research")
        paper = research.get("paper") if isinstance(research, dict) else None
        paper_active = isinstance(paper, dict) and paper.get("active") is True
        broker_quotes = self.paper_source.currentData() == "broker_quotes"
        account_ready = connected and bool(self._status.get("account"))
        can_start_paper = (
            (account_ready if broker_quotes else True) and not self._status.get("running")
            and not transition_busy and not self._paper_busy and not paper_active
            and bool(self.paper_initial_cash.text().strip()) and bool(self.paper_trade_cash.text().strip())
            and (not self.paper_local_ai.isChecked() or bool(self.paper_ai_model.text().strip()))
        )
        self.paper_start_button.setEnabled(can_start_paper)
        self.paper_stop_button.setEnabled(paper_active and not self._paper_busy and not transition_busy)
        # Stop remains visible and actionable even when status probing failed. It never starts a worker.
        self.stop_button.setEnabled(not self._stop_in_flight and not self._exit_in_flight)

    def _render_research_status(self, research: Any) -> None:
        if not isinstance(research, dict):
            self.research_status.setText("Research MCP off · broker orders unavailable")
            self.research_button.setText("Enable research MCP")
            return
        enabled = research.get("enabled") is True
        phase = research.get("phase") or ("Running" if research.get("running") else "Stopped")
        bridge = research.get("bridge_path") or ("MCP connection off" if not enabled else "Local bridge path pending")
        paper = research.get("paper")
        paper_status = "Paper session: not started"
        if isinstance(paper, dict):
            state = "running" if paper.get("active") else "saved"
            paper_status = (
                f"Paper {state} · {paper.get('source', 'unknown source')} · "
                f"virtual equity ${paper.get('equity', '—')} · "
                f"P&L ${paper.get('total_pnl', '—')} · "
                f"{len(paper.get('positions', []))} virtual holdings"
            )
        self.research_status.setText(
            f"Research: {phase} · broker orders unavailable\n{paper_status}\n{bridge}"
        )
        self.research_button.setText("Disable research MCP" if enabled else "Enable research MCP")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_controls()

    def _log(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        item = QListWidgetItem(f"{timestamp}  {message}")
        if level == "success":
            item.setForeground(QColor("#00c805"))
        elif level == "warning":
            item.setForeground(QColor("#f2c14e"))
        elif level == "error":
            item.setForeground(QColor("#ff697d"))
        self.activity.insertItem(0, item)
        while self.activity.count() > 200:
            self.activity.takeItem(self.activity.count() - 1)
        self.event_card.value.setText(message[:48] + ("…" if len(message) > 48 else ""))

    def _show_error(self, message: str) -> None:
        self.banner.setText(message)
        self.banner.setVisible(True)
        self._log("error", message)

    def _clear_banner(self) -> None:
        self.banner.clear()
        self.banner.setVisible(False)

    # ---------- Tray and bounded exit ----------
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
        box.setInformativeText("GRANDE Alpha will now exit. This warning does not claim cancellation or a flat account.")
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

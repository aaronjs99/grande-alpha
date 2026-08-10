from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from grande_alpha import __version__
from grande_alpha.config import AppConfig, save_config
from grande_alpha.controller import TradingController, TradingSnapshot
from grande_alpha.models import Regime
from grande_alpha.privacy import export_diagnostics
from grande_alpha.ui.dialogs import FundPlanDialog, LiveGrantDialog
from grande_alpha.ui.sandbox_widget import SandboxWidget
from grande_alpha.ui.settings_dialog import SettingsDialog
from grande_alpha.ui.welcome_widget import WelcomeWidget

STYLESHEET = """
QWidget { background: #0b1118; color: #e9f0f6; font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow { background: #081018; }
QMenuBar { background: #0a141d; border-bottom: 1px solid #223142; padding: 2px 5px; }
QMenuBar::item { background: transparent; padding: 6px 10px; border-radius: 4px; }
QMenuBar::item:selected { background: #1b2b3b; color: #8fd3ff; }
QMenu { background: #101a24; border: 1px solid #304357; padding: 5px; }
QMenu::item { padding: 7px 34px 7px 24px; border-radius: 4px; }
QMenu::item:selected { background: #1f3446; color: #ffffff; }
QMenu::item:disabled { color: #5d7182; }
QMenu::separator { height: 1px; background: #2c3c4b; margin: 5px 8px; }
QFrame#card { background: #111a24; border: 1px solid #223142; border-radius: 10px; }
QLabel#cardTitle { color: #8fa4b8; font-size: 9pt; }
QLabel#cardValue { font-size: 18pt; font-weight: 650; }
QLabel#dialogTitle { font-size: 17pt; font-weight: 650; }
QPushButton { background: #182634; border: 1px solid #2c4155; border-radius: 7px; padding: 8px 13px; }
QPushButton:hover { background: #213447; }
QPushButton:disabled { color: #596b7a; background: #121b24; }
QPushButton#primary { background: #00c805; border-color: #00c805; color: #021004; font-weight: 700; }
QPushButton#danger { background: #c62d42; border-color: #ec5266; color: white; font-weight: 700; }
QPushButton#flatten { background: #7f3d18; border-color: #c66a2e; color: white; }
QTableWidget { background: #0e1720; alternate-background-color: #101c27; border: 1px solid #223142; gridline-color: #223142; }
QHeaderView::section { background: #14202b; color: #a9bac8; padding: 6px; border: 0; border-right: 1px solid #223142; }
QTabWidget::pane { border: 1px solid #223142; }
QTabBar::tab { background: #111a24; padding: 9px 16px; }
QTabBar::tab:selected { background: #1b2b3b; color: #00e507; }
QLineEdit, QSpinBox, QDoubleSpinBox { background: #0e1720; border: 1px solid #2c4155; border-radius: 5px; padding: 6px; }
QCheckBox { spacing: 8px; }
QGroupBox { border: 1px solid #2b3b4b; border-radius: 9px; margin-top: 13px; padding-top: 12px; font-weight: 650; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 7px; color: #d7e5f0; }
QLabel#settingsDescription { color: #91a6b8; font-size: 9pt; }
QLabel#settingsStatus { border-radius: 6px; padding: 6px 9px; font-size: 9pt; font-weight: 700; }
QLabel#validationWarning { color: #ffd27a; background: #2b2315; border: 1px solid #6f5727; border-radius: 6px; padding: 8px; }
QToolTip { background: #243648; color: white; border: 1px solid #45617a; }
"""


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "—") -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.value = QLabel(value)
        self.value.setObjectName("cardValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value)


class MainWindow(QMainWindow):
    def __init__(self, controller: TradingController, config: AppConfig) -> None:
        super().__init__()
        self.controller = controller
        self.config = config
        self._snapshot = TradingSnapshot()
        self._chart_times: deque[float] = deque(maxlen=1800)
        self._chart_prices: deque[float] = deque(maxlen=1800)
        self._closing_after_cleanup = False
        self.setWindowTitle(f"GRANDE Alpha {__version__} — Community Preview")
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)
        QApplication.instance().setStyleSheet(STYLESHEET)
        self._build_ui()

        controller.snapshot_changed.connect(self._on_snapshot)
        controller.event.connect(self._on_event)
        controller.connection_busy.connect(self._on_busy)
        self.timer = QTimer(self)
        self.timer.setInterval(int(config.poll_seconds * 1000))
        self.timer.timeout.connect(lambda: asyncio.create_task(self.controller.refresh_quotes()))
        self.timer.start()
        self.reconcile_timer = QTimer(self)
        self.reconcile_timer.setInterval(int(config.reconcile_seconds * 1000))
        self.reconcile_timer.timeout.connect(lambda: asyncio.create_task(self.controller.reconcile()))
        self.reconcile_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        header = QHBoxLayout()
        brand = QLabel("GRANDE ALPHA")
        font = QFont("Segoe UI", 18)
        font.setBold(True)
        brand.setFont(font)
        header.addWidget(brand)
        self.mode_badge = QLabel("RESEARCH MODE")
        self.mode_badge.setStyleSheet(
            "background:#15324a;color:#8fd3ff;border:1px solid #3478a4;border-radius:7px;padding:7px 10px;font-weight:700"
        )
        header.addWidget(self.mode_badge)
        header.addStretch()
        self.connect_button = QPushButton("Connect Robinhood")
        self.connect_button.setAccessibleName("Connect or disconnect Robinhood")
        self.connect_button.setToolTip("Connect to the consented Robinhood provider session")
        self.connect_button.clicked.connect(lambda: asyncio.create_task(self._connect()))
        self.authorize_button = QPushButton("Authorize Live Session")
        self.authorize_button.setObjectName("primary")
        self.authorize_button.clicked.connect(self._authorize)
        self.start_button = QPushButton("Start Strategy")
        self.start_button.clicked.connect(self._start_strategy)
        self.shadow_button = QPushButton("Start Live Shadow")
        self.shadow_button.setToolTip("Run live observations and virtual fills without sending orders")
        self.shadow_button.clicked.connect(self._toggle_shadow)
        self.kill_button = QPushButton("STOP + CANCEL")
        self.kill_button.setObjectName("danger")
        self.kill_button.clicked.connect(lambda: asyncio.create_task(self.controller.stop_and_cancel()))
        self.flatten_button = QPushButton("Flatten Position")
        self.flatten_button.setObjectName("flatten")
        self.flatten_button.clicked.connect(lambda: asyncio.create_task(self._flatten()))
        self.settings_button = QPushButton("Settings && Permissions")
        self.settings_button.setAccessibleName("Settings and permissions")
        self.settings_button.setToolTip("Review account scope, capabilities, privacy, and cadence")
        self.settings_button.clicked.connect(self._open_settings)
        for button in (
            self.connect_button,
            self.authorize_button,
            self.start_button,
            self.shadow_button,
            self.kill_button,
            self.flatten_button,
            self.settings_button,
        ):
            header.addWidget(button)
        outer.addLayout(header)

        self.broker_panel = QWidget()
        broker_layout = QVBoxLayout(self.broker_panel)
        broker_layout.setContentsMargins(0, 0, 0, 0)
        cards = QGridLayout()
        self.account_card = MetricCard("Agentic account", "Disconnected")
        self.value_card = MetricCard("Account value", "—")
        self.buying_power_card = MetricCard("Buying power", "—")
        self.session_card = MetricCard("Live authority", "LOCKED")
        self.signal_card = MetricCard("QQQ regime", "FLAT")
        self.pair_action_card = MetricCard("Pair action (T,S)", "(0,0)")
        self.drawdown_card = MetricCard("Session drawdown", "$0.00")
        self.shadow_card = MetricCard("Live shadow", "OFF")
        for column, card in enumerate(
            (
                self.account_card,
                self.value_card,
                self.buying_power_card,
                self.session_card,
                self.signal_card,
                self.pair_action_card,
                self.drawdown_card,
                self.shadow_card,
            )
        ):
            cards.addWidget(card, 0, column)
        broker_layout.addLayout(cards)

        top = QSplitter(Qt.Orientation.Horizontal)
        self.market_splitter = top
        top.setMinimumHeight(220)
        top.setChildrenCollapsible(False)
        self.chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.chart.setMinimumWidth(560)
        self.chart.setBackground("#0e1720")
        self.chart.showGrid(x=True, y=True, alpha=0.18)
        self.chart.setLabel("left", "QQQ midpoint", units="$")
        self.chart_curve = self.chart.plot(pen=pg.mkPen("#00d407", width=2))
        top.addWidget(self.chart)

        self.quotes_table = self._table(["Symbol", "Bid", "Ask", "Last", "Spread", "Age"])
        self.quotes_table.setMinimumWidth(390)
        top.addWidget(self.quotes_table)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)
        top.setSizes([850, 500])
        broker_layout.addWidget(top)
        outer.addWidget(self.broker_panel)

        self.tabs = QTabWidget()
        self.welcome_widget = WelcomeWidget(self.config)
        self.welcome_widget.open_sandbox.connect(self._open_sandbox)
        self.welcome_widget.open_settings.connect(self._open_settings)
        self.positions_table = self._table(["Symbol", "Quantity", "Sellable", "Average", "Mark", "P/L"])
        self.orders_table = self._table(["Time", "Symbol", "Side", "State", "Quantity/$", "Fill", "Order ID"])
        self.activity_table = self._table(["Time", "Severity", "Event"])
        self.fund_widget = QWidget()
        fund_layout = QVBoxLayout(self.fund_widget)
        fund_notice = QLabel(
            "Ledger only — GRANDE Alpha never transfers brokerage, university, grant, or laboratory funds. "
            "A planned contribution becomes confirmed only after you verify an independent personal transfer."
        )
        fund_notice.setWordWrap(True)
        fund_layout.addWidget(fund_notice)
        fund_actions = QHBoxLayout()
        self.fund_total_label = QLabel("Confirmed personal contributions: $0.00")
        self.fund_plan_button = QPushButton("Plan contribution")
        self.fund_plan_button.clicked.connect(self._plan_contribution)
        self.fund_confirm_button = QPushButton("Mark selected contribution confirmed")
        self.fund_confirm_button.clicked.connect(self._confirm_contribution)
        fund_actions.addWidget(self.fund_total_label)
        fund_actions.addStretch()
        fund_actions.addWidget(self.fund_plan_button)
        fund_actions.addWidget(self.fund_confirm_button)
        fund_layout.addLayout(fund_actions)
        self.fund_table = self._table(
            ["ID", "Period", "Realized", "Fees", "Tax reserve", "Rate", "Eligible", "Status", "Confirmed"]
        )
        fund_layout.addWidget(self.fund_table)
        self.tabs.addTab(self.welcome_widget, "Getting Started")
        self.sandbox_widget = SandboxWidget(
            self.controller.store, allow_remote_data=self.config.remote_market_data_enabled
        )
        self.tabs.addTab(self.sandbox_widget, "Research Sandbox")
        self.tabs.addTab(self.positions_table, "Positions")
        self.tabs.addTab(self.orders_table, "Orders")
        self.tabs.addTab(self.activity_table, "Receipts")
        if self.config.personal_ledger_enabled:
            self.tabs.addTab(self.fund_widget, "Personal Research Fund")
        outer.addWidget(self.tabs, 1)

        self.status = QLabel(
            "RESEARCH MODE • No optional network or broker action occurs without your consent."
        )
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.setCentralWidget(root)
        self._build_menus()
        self._refresh_fund()
        self._set_controls()

    def _action(self, text: str, callback, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        return action

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)

        self.file_menu = menu_bar.addMenu("File")
        self.export_action = self._action(
            "Export redacted support diagnostics…", self._export_diagnostics
        )
        self.file_menu.addAction(self.export_action)
        self.settings_action = self._action(
            "Settings && Permissions…", self._open_settings, "Ctrl+,"
        )
        self.file_menu.addAction(self.settings_action)
        self.file_menu.addSeparator()
        self.exit_action = self._action("Exit", self.close, "Ctrl+Q")
        self.file_menu.addAction(self.exit_action)

        self.view_menu = menu_bar.addMenu("View")
        destinations = (
            ("Getting Started", self.welcome_widget, "Ctrl+1"),
            ("Research Sandbox", self.sandbox_widget, "Ctrl+2"),
            ("Positions", self.positions_table, "Ctrl+3"),
            ("Orders", self.orders_table, "Ctrl+4"),
            ("Receipts", self.activity_table, "Ctrl+5"),
        )
        self.view_actions: list[QAction] = []
        for label, widget, shortcut in destinations:
            action = self._action(
                label,
                lambda _checked=False, target=widget: self._show_tab(target),
                shortcut,
            )
            self.view_menu.addAction(action)
            self.view_actions.append(action)
        self.fund_view_action = self._action(
            "Personal Research Fund",
            lambda _checked=False: self._show_tab(self.fund_widget),
            "Ctrl+6",
        )
        self.view_menu.addAction(self.fund_view_action)
        self.view_menu.addSeparator()
        self.reset_layout_action = self._action("Reset Window Layout", self._reset_layout)
        self.view_menu.addAction(self.reset_layout_action)
        self.full_screen_action = self._action("Full Screen", self._toggle_full_screen, "F11")
        self.full_screen_action.setCheckable(True)
        self.view_menu.addAction(self.full_screen_action)

        self.broker_menu = menu_bar.addMenu("Broker")
        self.broker_connect_action = self._action(
            "Connect Robinhood…",
            lambda _checked=False: asyncio.create_task(self._connect()),
            "Ctrl+Shift+C",
        )
        self.broker_menu.addAction(self.broker_connect_action)
        self.refresh_action = self._action(
            "Refresh Account && Quotes",
            lambda _checked=False: asyncio.create_task(self._refresh_broker()),
            "F5",
        )
        self.broker_menu.addAction(self.refresh_action)
        self.shadow_action = self._action("Start Live Shadow", self._toggle_shadow)
        self.broker_menu.addAction(self.shadow_action)
        self.broker_menu.addSeparator()
        self.forget_credentials_action = self._action(
            "Forget Stored OAuth Credentials…", self._confirm_forget_credentials
        )
        self.broker_menu.addAction(self.forget_credentials_action)

        self.research_menu = menu_bar.addMenu("Research")
        research_tabs = (
            ("Replay", 0),
            ("Comparison", 1),
            ("Sensitivity", 2),
            ("Walk-forward && Gates", 3),
            ("9-action Lab", self.sandbox_widget.action_tab_index),
        )
        self.research_actions: list[QAction] = []
        for label, index in research_tabs:
            action = self._action(
                label,
                lambda _checked=False, tab_index=index: self._show_research_tab(tab_index),
            )
            self.research_menu.addAction(action)
            self.research_actions.append(action)

        self.safety_menu = menu_bar.addMenu("Safety")
        self.authorize_action = self._action("Authorize Live Session…", self._authorize)
        self.safety_menu.addAction(self.authorize_action)
        self.start_strategy_action = self._action("Start Live Strategy", self._start_strategy)
        self.safety_menu.addAction(self.start_strategy_action)
        self.stop_cancel_action = self._action(
            "STOP + CANCEL Agentic Orders",
            lambda _checked=False: asyncio.create_task(self.controller.stop_and_cancel()),
            "Ctrl+Shift+X",
        )
        self.safety_menu.addAction(self.stop_cancel_action)
        self.flatten_action = self._action(
            "Flatten Position…", lambda _checked=False: asyncio.create_task(self._flatten())
        )
        self.safety_menu.addAction(self.flatten_action)
        self.safety_menu.addSeparator()
        self.safety_explainer_action = self._action(
            "Explain Safety Locks…", self._show_safety_help
        )
        self.safety_menu.addAction(self.safety_explainer_action)

        self.help_menu = menu_bar.addMenu("Help")
        self.quickstart_action = self._action("Quick Start…", self._show_quickstart)
        self.help_menu.addAction(self.quickstart_action)
        self.account_scope_action = self._action(
            "Account Scope && Privacy…", self._show_account_scope
        )
        self.help_menu.addAction(self.account_scope_action)
        self.help_menu.addSeparator()
        self.about_action = self._action("About GRANDE Alpha", self._about)
        self.help_menu.addAction(self.about_action)

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        return table

    def _open_sandbox(self) -> None:
        index = self.tabs.indexOf(self.sandbox_widget)
        if index >= 0:
            self.tabs.setCurrentIndex(index)

    def _show_tab(self, widget: QWidget) -> None:
        index = self.tabs.indexOf(widget)
        if index >= 0:
            self.tabs.setCurrentIndex(index)

    def _show_research_tab(self, index: int) -> None:
        self._open_sandbox()
        if 0 <= index < self.sandbox_widget.tabs.count():
            self.sandbox_widget.tabs.setCurrentIndex(index)

    def _reset_layout(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        self.full_screen_action.setChecked(False)
        self.resize(1440, 900)
        self.market_splitter.setSizes([850, 500])

    def _toggle_full_screen(self, checked: bool) -> None:
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()

    async def _refresh_broker(self) -> None:
        try:
            await self.controller.refresh(evaluate=False)
        except Exception as exc:
            QMessageBox.critical(self, "Broker refresh failed", str(exc))

    def _confirm_forget_credentials(self) -> None:
        answer = QMessageBox.question(
            self,
            "Forget stored Robinhood credentials?",
            "This removes GRANDE Alpha's local OAuth credential, disconnects the app, and requires "
            "browser consent next time. It does not revoke the connection inside Robinhood. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            asyncio.create_task(self._forget_credentials())

    async def _forget_credentials(self) -> None:
        try:
            await self.controller.forget_broker_credentials()
            QMessageBox.information(
                self,
                "Stored credentials removed",
                "The local OAuth credential was removed. Reconnect to restore broker access.",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Credentials were not forgotten", str(exc))

    def _show_quickstart(self) -> None:
        QMessageBox.information(
            self,
            "GRANDE Alpha quick start",
            "1. Use Research Sandbox to replay and inspect virtual fills.\n"
            "2. Use Live Shadow to observe current quotes without sending orders.\n"
            "3. Review every receipt and evidence gate before considering live controls.\n\n"
            "No strategy is guaranteed profitable, and live authority stays locked unless the "
            "saved capability, evidence certificate, account limits, and session confirmation all agree.",
        )

    def _show_safety_help(self) -> None:
        QMessageBox.information(
            self,
            "Why live controls are locked",
            "GRANDE Alpha defaults to research and shadow mode. Real-order controls require a current "
            "passing Evidence Lab certificate for the exact strategy, an enabled broker capability, "
            "an enabled live-order capability, a connected funded Agentic account, and a short-lived "
            "account-specific authorization. Any mismatch fails closed.",
        )

    def _show_account_scope(self) -> None:
        QMessageBox.information(
            self,
            "Robinhood account scope and privacy",
            "Robinhood's OAuth consent may expose metadata and read data across connected accounts. "
            "GRANDE Alpha filters the provider response to the active Agentic account and requests "
            "portfolio, positions, and orders for that selected Agentic account. The regular investing "
            "account is not selected for those app views. Trading is provider-restricted to the Agentic "
            "account. GRANDE Alpha sends no first-party telemetry.",
        )

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self.config,
            live_evidence_ready=self.controller.live_evidence_ready(),
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        previous = self.config
        updated = dialog.updated_config()
        self.config = updated
        self.controller.update_config(updated)
        self.timer.setInterval(int(updated.poll_seconds * 1000))
        self.reconcile_timer.setInterval(int(updated.reconcile_seconds * 1000))
        save_config(updated)
        self.welcome_widget.update_config(updated)
        self.sandbox_widget.set_remote_data_allowed(updated.remote_market_data_enabled)
        self._sync_optional_tabs()
        self.controller.log(
            "Settings and capability boundaries updated",
            "warning",
            "permissions",
            {
                "broker_connection_enabled": updated.broker_connection_enabled,
                "live_trading_enabled": updated.live_trading_enabled,
                "remote_market_data_enabled": updated.remote_market_data_enabled,
                "personal_ledger_enabled": updated.personal_ledger_enabled,
            },
        )
        asyncio.create_task(
            self._apply_permission_revocations(previous, updated, dialog.forget_credentials.isChecked())
        )
        self._set_controls()

    async def _apply_permission_revocations(
        self, previous: AppConfig, updated: AppConfig, forget_credentials: bool
    ) -> None:
        if previous.broker_connection_enabled and not updated.broker_connection_enabled:
            if self._snapshot.connected:
                await self.controller.disconnect()
        elif previous.live_trading_enabled and not updated.live_trading_enabled:
            await self.controller.stop_and_cancel("Real-order capability revoked in Settings")
        if forget_credentials:
            try:
                await self.controller.forget_broker_credentials()
            except Exception as exc:
                QMessageBox.warning(self, "Credentials were not forgotten", str(exc))

    def _sync_optional_tabs(self) -> None:
        index = self.tabs.indexOf(self.fund_widget)
        if self.config.personal_ledger_enabled and index < 0:
            self.tabs.addTab(self.fund_widget, "Personal Research Fund")
        elif not self.config.personal_ledger_enabled and index >= 0:
            self.tabs.removeTab(index)
        self.fund_view_action.setVisible(self.config.personal_ledger_enabled)

    def _export_diagnostics(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export redacted support diagnostics",
            "grande-alpha-diagnostics.json",
            "JSON (*.json)",
        )
        if not filename:
            return
        try:
            export_diagnostics(self.config, self.controller.store, Path(filename))
            QMessageBox.information(
                self,
                "Diagnostics exported",
                "The export redacts known credential, account, order, and reference identifiers. "
                "Review the JSON yourself before sharing it.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Diagnostics export failed", str(exc))

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About GRANDE Alpha",
            f"GRANDE Alpha {__version__}\n\n"
            "Local-first leveraged-ETF strategy research and consent-gated execution workstation.\n\n"
            "Independent community software. Not affiliated with or endorsed by Robinhood, "
            "ProShares, Nasdaq, or Yahoo. No telemetry. No investment, legal, or tax advice.\n\n"
            "Licensed under Apache-2.0. See README, PRIVACY.md, SECURITY.md, and docs/ for details.",
        )

    async def _connect(self) -> None:
        try:
            if self._snapshot.connected:
                await self.controller.disconnect()
            else:
                await self.controller.connect()
        except Exception as exc:
            QMessageBox.critical(self, "Robinhood connection", str(exc))

    def _authorize(self) -> None:
        if not self._snapshot.account or not self._snapshot.portfolio:
            return
        dialog = LiveGrantDialog(self._snapshot.account, self._snapshot.portfolio, self.config, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            try:
                self.controller.authorize_live(dialog.grant())
            except Exception as exc:
                QMessageBox.critical(self, "Live authority not granted", str(exc))

    def _start_strategy(self) -> None:
        try:
            self.controller.start_strategy()
        except Exception as exc:
            QMessageBox.warning(self, "Strategy remains stopped", str(exc))

    def _toggle_shadow(self) -> None:
        try:
            if self._snapshot.shadow_running:
                self.controller.stop_shadow()
            else:
                self.controller.start_shadow()
        except Exception as exc:
            QMessageBox.warning(self, "Live shadow unchanged", str(exc))

    def _plan_contribution(self) -> None:
        dialog = FundPlanDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            entry_id = self.controller.store.plan_research_contribution(**dialog.values())
            self.controller.log(
                f"Saved GRANDE Research Fund plan #{entry_id}; no money was transferred",
                category="research_fund",
            )
            self._refresh_fund()
        except Exception as exc:
            QMessageBox.critical(self, "Contribution plan not saved", str(exc))

    def _confirm_contribution(self) -> None:
        row = self.fund_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Research Fund", "Select a planned contribution first.")
            return
        entry_id = int(self.fund_table.item(row, 0).text())
        amount = self.fund_table.item(row, 6).text()
        status = self.fund_table.item(row, 7).text().lower()
        if status == "confirmed":
            QMessageBox.information(self, "Research Fund", "This contribution is already confirmed.")
            return
        reference, ok = QInputDialog.getText(
            self,
            "External transfer reference",
            "After independently completing the personal contribution, enter its confirmation/reference:",
        )
        if not ok or not reference.strip():
            return
        phrase = f"CONFIRM GRANDE {amount}"
        confirmation, ok = QInputDialog.getText(
            self,
            "Confirm ledger entry",
            f"This does not transfer money. It records that you independently transferred {amount}.\n"
            f"Type exactly: {phrase}",
        )
        if not ok or confirmation.strip() != phrase:
            self._on_event("info", "Research Fund confirmation declined; ledger unchanged")
            return
        try:
            self.controller.store.confirm_research_contribution(entry_id, reference)
            self.controller.log(
                f"Marked GRANDE Research Fund entry #{entry_id} confirmed at {amount}",
                "warning",
                "research_fund",
            )
            self._refresh_fund()
        except Exception as exc:
            QMessageBox.critical(self, "Contribution not confirmed", str(exc))

    async def _flatten(self) -> None:
        positions = [item for item in self._snapshot.positions if item.symbol in {"TQQQ", "SQQQ"}]
        if not positions:
            QMessageBox.information(self, "Flatten", "There is no TQQQ or SQQQ position to flatten.")
            return
        if len(positions) > 1:
            symbol, ok = QInputDialog.getItem(
                self, "Select position", "Position", [item.symbol for item in positions], 0, False
            )
            if not ok:
                return
        else:
            symbol = positions[0].symbol
        try:
            intent, review = await self.controller.review_flatten(symbol)
        except Exception as exc:
            QMessageBox.critical(self, "Flatten review failed", str(exc))
            return
        disclosure = review.market_data_disclosure or "Live market disclosure unavailable."
        phrase = f"SELL {intent.quantity:g} {intent.symbol}"
        text, ok = QInputDialog.getText(
            self,
            "Confirm real-money sell",
            f"Robinhood reviewed this order:\n\n{disclosure}\n\n"
            f"SELL {intent.quantity:g} {intent.symbol} at market during regular hours.\n"
            f"Type exactly: {phrase}",
        )
        if not ok or text.strip() != phrase:
            self._on_event("info", "Manual flatten declined; no sell order placed")
            return
        try:
            await self.controller.place_reviewed_flatten(intent, review)
            await self.controller.refresh(evaluate=False)
        except Exception as exc:
            QMessageBox.critical(self, "Flatten failed", str(exc))

    def _on_busy(self, busy: bool) -> None:
        self.connect_button.setEnabled(not busy)
        self.connect_button.setText(
            "Connecting in browser…"
            if busy
            else ("Disconnect" if self._snapshot.connected else "Connect Robinhood")
        )

    def _on_snapshot(self, snapshot: TradingSnapshot) -> None:
        self._snapshot = snapshot
        if snapshot.account:
            self.account_card.value.setText(f"{snapshot.account.nickname} {snapshot.account.masked}")
        else:
            self.account_card.value.setText("Disconnected")
        if snapshot.portfolio:
            self.value_card.value.setText(f"${snapshot.portfolio.total_value:,.2f}")
            self.buying_power_card.value.setText(f"${snapshot.portfolio.buying_power:,.2f}")
        else:
            self.value_card.value.setText("—")
            self.buying_power_card.value.setText("—")
        session = snapshot.live_status if self.config.live_trading_enabled else "DISABLED"
        if session == "LIVE" and snapshot.session_expires_at:
            session = f"LIVE to {snapshot.session_expires_at.astimezone().strftime('%I:%M %p')}"
        self.session_card.value.setText(session)
        self.session_card.value.setStyleSheet(
            "color:#00e507" if snapshot.live_status == "LIVE" else "color:#8fa4b8"
        )
        self.signal_card.value.setText(snapshot.signal.regime.value.upper())
        signal_color = {Regime.BULLISH: "#00e507", Regime.BEARISH: "#ff697d", Regime.FLAT: "#f2c14e"}
        self.signal_card.value.setStyleSheet(f"color:{signal_color[snapshot.signal.regime]}")
        self.pair_action_card.value.setText(snapshot.pair_action_label)
        self.pair_action_card.value.setStyleSheet(
            "color:#f2c14e" if snapshot.pair_action_id == 4 else "color:#65b9ff"
        )
        self.drawdown_card.value.setText(f"${snapshot.drawdown:,.2f}")
        if snapshot.shadow_running:
            self.shadow_card.value.setText(
                f"${snapshot.shadow_pnl:+,.2f} • {snapshot.shadow_position or 'cash'}"
            )
            self.shadow_card.value.setStyleSheet("color:#65b9ff")
        else:
            self.shadow_card.value.setText("OFF")
            self.shadow_card.value.setStyleSheet("color:#8fa4b8")
        self.shadow_button.setText("Stop Live Shadow" if snapshot.shadow_running else "Start Live Shadow")
        self.connect_button.setText("Disconnect" if snapshot.connected else "Connect Robinhood")
        self._update_quotes(snapshot)
        self._update_positions(snapshot)
        self._update_orders(snapshot)
        self._update_chart(snapshot)
        refreshed = (
            snapshot.last_refresh.astimezone().strftime("%I:%M:%S %p") if snapshot.last_refresh else "never"
        )
        if not self.config.broker_connection_enabled:
            self.status.setText(
                "RESEARCH MODE • Broker capability is off • Local sandbox and CSV import only • No telemetry"
            )
        else:
            self.status.setText(
                f"{session} • Strategy {'RUNNING' if snapshot.strategy_running else 'STOPPED'} • "
                f"Shadow {'RUNNING — NO ORDERS' if snapshot.shadow_running else 'OFF'} • "
                f"Action {snapshot.pair_action_label} every {self.config.trade_seconds}s nominal • "
                f"Orders {snapshot.trades_today} • Last broker refresh {refreshed} • {snapshot.signal.reason}"
            )
        self._set_controls()

    def _set_controls(self) -> None:
        connected = self._snapshot.connected
        funded = bool(self._snapshot.portfolio and self._snapshot.portfolio.buying_power > 0)
        live = self._snapshot.live_status == "LIVE"
        shadow = self._snapshot.shadow_running
        broker_enabled = self.config.broker_connection_enabled
        live_enabled = self.config.live_trading_enabled
        evidence_ready = live_enabled and self.controller.live_evidence_ready()
        self.broker_panel.setVisible(broker_enabled)
        self.connect_button.setVisible(broker_enabled)
        self.shadow_button.setVisible(broker_enabled)
        self.authorize_button.setVisible(evidence_ready)
        self.start_button.setVisible(evidence_ready)
        self.kill_button.setVisible(evidence_ready)
        self.flatten_button.setVisible(evidence_ready)
        self.mode_badge.setText(
            "LIVE EVIDENCE READY"
            if evidence_ready
            else "SHADOW ONLY — EVIDENCE REQUIRED"
            if live_enabled
            else ("BROKER SHADOW ENABLED" if broker_enabled else "RESEARCH MODE")
        )
        self.mode_badge.setStyleSheet(
            "background:#4b2516;color:#ffc07a;border:1px solid #9a5328;border-radius:7px;padding:7px 10px;font-weight:700"
            if evidence_ready
            else "background:#15324a;color:#8fd3ff;border:1px solid #3478a4;border-radius:7px;padding:7px 10px;font-weight:700"
        )
        self.authorize_button.setEnabled(evidence_ready and connected and funded and not shadow)
        self.start_button.setEnabled(
            evidence_ready and live and not self._snapshot.strategy_running and not shadow
        )
        self.shadow_button.setEnabled(connected and (shadow or not live))
        self.kill_button.setEnabled(connected)
        self.flatten_button.setEnabled(bool(self._snapshot.positions))
        self.fund_view_action.setVisible(self.config.personal_ledger_enabled)
        self.broker_connect_action.setEnabled(broker_enabled)
        self.broker_connect_action.setText(
            "Disconnect Robinhood" if connected else "Connect Robinhood…"
        )
        self.refresh_action.setEnabled(broker_enabled and connected)
        self.shadow_action.setEnabled(broker_enabled and connected and (shadow or not live))
        self.shadow_action.setText("Stop Live Shadow" if shadow else "Start Live Shadow")
        self.forget_credentials_action.setEnabled(broker_enabled)
        self.authorize_action.setEnabled(evidence_ready and connected and funded and not shadow)
        self.start_strategy_action.setEnabled(
            evidence_ready and live and not self._snapshot.strategy_running and not shadow
        )
        self.stop_cancel_action.setEnabled(evidence_ready and connected)
        self.flatten_action.setEnabled(evidence_ready and bool(self._snapshot.positions))

    def _update_quotes(self, snapshot: TradingSnapshot) -> None:
        symbols = [symbol for symbol in ("QQQ", "TQQQ", "SQQQ") if symbol in snapshot.quotes]
        self.quotes_table.setRowCount(len(symbols))
        for row, symbol in enumerate(symbols):
            quote = snapshot.quotes[symbol]
            values = [
                symbol,
                f"${quote.bid:,.2f}",
                f"${quote.ask:,.2f}",
                f"${quote.last:,.2f}",
                f"{quote.spread_bps:.1f} bps",
                f"{quote.age_seconds():.1f}s",
            ]
            for column, value in enumerate(values):
                self.quotes_table.setItem(row, column, QTableWidgetItem(value))

    def _update_positions(self, snapshot: TradingSnapshot) -> None:
        self.positions_table.setRowCount(len(snapshot.positions))
        for row, position in enumerate(snapshot.positions):
            quote = snapshot.quotes.get(position.symbol)
            mark = quote.mid if quote else None
            pnl = (
                (mark - position.average_price) * position.quantity
                if mark is not None and position.average_price is not None
                else None
            )
            values = [
                position.symbol,
                f"{position.quantity:g}",
                f"{position.sellable_quantity:g}",
                f"${position.average_price:,.2f}" if position.average_price is not None else "—",
                f"${mark:,.2f}" if mark is not None else "—",
                f"${pnl:+,.2f}" if pnl is not None else "—",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 5 and pnl is not None:
                    item.setForeground(QColor("#00e507" if pnl >= 0 else "#ff697d"))
                self.positions_table.setItem(row, column, item)

    def _update_orders(self, snapshot: TradingSnapshot) -> None:
        orders = snapshot.orders[:100]
        self.orders_table.setRowCount(len(orders))
        for row, order in enumerate(orders):
            amount = (
                f"{order.quantity:g} sh" if order.quantity is not None else f"${order.dollar_amount:,.2f}"
            )
            values = [
                order.created_at.astimezone().strftime("%m/%d %I:%M:%S") if order.created_at else "—",
                order.symbol,
                order.side.upper(),
                order.state,
                amount,
                f"${order.average_price:,.2f}" if order.average_price is not None else "—",
                order.order_id,
            ]
            for column, value in enumerate(values):
                self.orders_table.setItem(row, column, QTableWidgetItem(value))

    def _update_chart(self, snapshot: TradingSnapshot) -> None:
        quote = snapshot.quotes.get("QQQ")
        if quote and (not self._chart_times or quote.timestamp.timestamp() > self._chart_times[-1]):
            self._chart_times.append(quote.timestamp.timestamp())
            self._chart_prices.append(quote.mid)
            self.chart_curve.setData(list(self._chart_times), list(self._chart_prices))

    def _refresh_fund(self) -> None:
        entries = self.controller.store.research_fund_entries()
        self.fund_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                str(entry["id"]),
                str(entry["period"]),
                f"${float(entry['realized_profit']):,.2f}",
                f"${float(entry['fees']):,.2f}",
                f"${float(entry['tax_reserve']):,.2f}",
                f"{float(entry['contribution_rate']):.1%}",
                f"${float(entry['eligible_contribution']):,.2f}",
                str(entry["status"]),
                str(entry["confirmed_at"] or "—"),
            ]
            for column, value in enumerate(values):
                self.fund_table.setItem(row, column, QTableWidgetItem(value))
        total = self.controller.store.confirmed_research_total()
        self.fund_total_label.setText(f"Confirmed personal contributions: ${total:,.2f}")

    def _on_event(self, severity: str, summary: str) -> None:
        self.activity_table.insertRow(0)
        now = datetime.now().strftime("%I:%M:%S %p")
        for column, value in enumerate((now, severity.upper(), summary)):
            item = QTableWidgetItem(value)
            if severity in {"error", "critical"}:
                item.setForeground(QColor("#ff697d"))
            elif severity == "warning":
                item.setForeground(QColor("#f2c14e"))
            elif severity == "market":
                item.setForeground(QColor("#65b9ff"))
            self.activity_table.setItem(0, column, item)
        if self.activity_table.rowCount() > 500:
            self.activity_table.removeRow(500)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing_after_cleanup:
            event.accept()
            return
        if self._snapshot.connected:
            answer = QMessageBox.question(
                self,
                "Exit GRANDE Alpha",
                "Exit will lock the strategy and attempt to cancel open agentic orders. Filled positions remain open. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            event.ignore()
            asyncio.create_task(self._shutdown_then_close())
            return
        event.accept()

    async def _shutdown_then_close(self) -> None:
        try:
            await self.controller.disconnect()
        finally:
            self._closing_after_cleanup = True
            self.close()

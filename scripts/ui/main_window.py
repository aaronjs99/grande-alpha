from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from grande_alpha import __version__
from grande_alpha.application.activation_guidance import activation_guidance
from grande_alpha.configuration.config import AppConfig, save_config
from grande_alpha.configuration.privacy import export_diagnostics
from grande_alpha.desktop.controller import ShadowRecoveryRequired, TradingController, TradingSnapshot
from grande_alpha.domain.models import (
    LiveGrant,
    OrderConfirmationDecision,
    OrderConfirmationRequest,
    Regime,
    utc_now,
)
from grande_alpha.domain.terminology import term_help
from grande_alpha.strategy.core import STRATEGY_NAMES
from grande_alpha.ui.activation_widget import ActivationChecklistWidget
from grande_alpha.ui.agent_widget import AgentWidget
from grande_alpha.ui.dialogs import (
    AuthorityControlPanel,
    FundPlanDialog,
    LiveGrantDialog,
    OrderConfirmationDialog,
)
from grande_alpha.ui.glossary import (
    ExplainedLabel,
    GlossaryDialog,
    apply_table_header_help,
)
from grande_alpha.ui.product_dialog import ProductPlansDialog
from grande_alpha.ui.sandbox_widget import SandboxWidget
from grande_alpha.ui.settings_dialog import SettingsDialog
from grande_alpha.ui.table_layout import configure_adjustable_columns, reset_column_widths
from grande_alpha.ui.task_supervisor import TaskSupervisor
from grande_alpha.ui.themes import (
    appearance_settings,
    apply_application_theme,
    current_theme,
    saved_theme,
    set_item_foreground,
    set_widget_style,
    theme_plot,
)
from grande_alpha.ui.welcome_widget import WelcomeWidget
from grande_alpha.ui.workspace import DisclosureSection, WorkspaceNavigation, WorkspaceTabs

STOP_PREVIEW_TIMEOUT_SECONDS = 30.0


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "—") -> None:
        super().__init__()
        self.setObjectName("card")
        self.card_layout = QVBoxLayout(self)
        self.card_layout.setSpacing(0)
        explanation = term_help(title)
        if explanation:
            title_label = ExplainedLabel(title, explanation, compact=True)
        else:
            title_label = QLabel(title)
            title_label.setObjectName("cardTitle")
        self.title = title_label
        self.value = QLabel(value)
        self.value.setObjectName("cardValue")
        self.value.setMinimumWidth(0)
        self.value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.card_layout.addWidget(title_label)
        self.card_layout.addWidget(self.value)
        self.set_compact(False)

    def set_compact(self, compact: bool) -> None:
        """Keep both metric lines legible when the cards reflow into multiple rows."""

        if getattr(self, "_compact", None) == compact:
            return
        self._compact = compact
        value_points = 16 if compact else 18
        margins = (9, 4, 9, 5) if compact else (10, 5, 10, 6)
        set_widget_style(self.value, f"QLabel#cardValue {{ font-size:{value_points}pt; font-weight:650; }}")
        self.value.ensurePolished()
        self.title.ensurePolished()
        title_height = self.title.fontMetrics().lineSpacing() + 2
        value_height = self.value.fontMetrics().lineSpacing() + 3
        self.title.setMinimumHeight(title_height)
        self.value.setMinimumHeight(value_height)
        self.card_layout.setContentsMargins(*margins)
        self.setMinimumHeight(
            margins[1] + title_height + value_height + margins[3]
        )


class MainWindow(QMainWindow):
    def __init__(
        self,
        controller: TradingController,
        config: AppConfig,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.config = config
        self._snapshot = TradingSnapshot()
        self._chart_times: deque[float] = deque(maxlen=1800)
        self._chart_prices: deque[float] = deque(maxlen=1800)
        self._closing_after_cleanup = False
        self._tray_icon: QSystemTrayIcon | None = None
        self._connection_busy = False
        self._connection_task: asyncio.Task | None = None
        self._close_requested = False
        self.tasks = TaskSupervisor(self._on_task_error)
        self._stop_cancel_busy = False
        self.setWindowTitle(f"GRANDE Alpha {__version__} — Community Preview")
        self.setMinimumSize(720, 560)
        self.resize(1440, 900)
        apply_application_theme(saved_theme())
        self._build_ui()
        self._apply_theme_widgets()
        if not controller.order_confirmation_available:
            controller.set_order_confirmer(self._confirm_strategy_order)

        controller.snapshot_changed.connect(self._on_snapshot)
        controller.event.connect(self._on_event)
        controller.connection_busy.connect(self._on_busy)
        controller.agent_changed.connect(self._clear_stop_status_on_restart)
        self.timer = QTimer(self)
        self.timer.setInterval(int(config.poll_seconds * 1000))
        self.timer.timeout.connect(self._schedule_quote_refresh)
        self.reconcile_timer = QTimer(self)
        self.reconcile_timer.setInterval(int(config.reconcile_seconds * 1000))
        self.reconcile_timer.timeout.connect(self._schedule_reconcile)
        self.research_mcp_timer = QTimer(self)
        self.research_mcp_timer.setInterval(250)
        self.research_mcp_timer.timeout.connect(controller.poll_agent_mcp)
        self.research_mcp_timer.start()

    def _apply_theme_widgets(self) -> None:
        dark = current_theme() == "dark"
        self.theme_button.setText("Light mode" if dark else "Dark mode")
        for control in (self.theme_button, self.theme_action):
            control.blockSignals(True)
            control.setChecked(dark)
            control.blockSignals(False)
        for chart in (self.chart, self.sandbox_widget.chart, self.sandbox_widget.trade_chart):
            theme_plot(chart)
        self.agent_widget.apply_theme()

    def _toggle_theme(self, dark: bool) -> None:
        theme = "dark" if dark else "light"
        settings = appearance_settings()
        settings.setValue("theme", theme)
        settings.sync()
        if settings.status() != settings.Status.NoError:
            self._apply_theme_widgets()
            QMessageBox.warning(self, "Appearance not saved", "The appearance preference could not be saved on this computer.")
            return
        apply_application_theme(theme)
        self._apply_theme_widgets()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        self.outer_layout = outer
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        self.header = QWidget()
        self.header_layout = QGridLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setHorizontalSpacing(12)
        self.header_layout.setVerticalSpacing(8)
        self.brand_row = QWidget()
        brand_layout = QHBoxLayout(self.brand_row)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(10)
        self.brand = QLabel("GRANDE ALPHA")
        font = QFont(QApplication.font())
        font.setPointSize(18)
        font.setBold(True)
        self.brand.setFont(font)
        brand_layout.addWidget(self.brand)
        self.plan_button = QPushButton("COMMUNITY · FREE")
        self.plan_button.setAccessibleName("Current plan: Community, free")
        self.plan_button.setToolTip("View the current free plan and the truthful Pro roadmap")
        self.plan_button.clicked.connect(self._show_plans)
        brand_layout.addWidget(self.plan_button)
        self.theme_button = QPushButton("Dark mode")
        self.theme_button.setCheckable(True)
        self.theme_button.setAccessibleName("Dark mode for the whole application")
        self.theme_button.setToolTip("Switch the whole app between light and dark; remembered on this computer")
        self.theme_button.toggled.connect(self._toggle_theme)
        brand_layout.addWidget(self.theme_button)
        self.mode_badge = QLabel("RESEARCH MODE")
        set_widget_style(self.mode_badge, "background:#15324a;color:#8fd3ff;border:1px solid #3478a4;border-radius:7px;padding:7px 10px;font-weight:700")
        brand_layout.addWidget(self.mode_badge)
        brand_layout.addStretch()
        self.header_actions_widget = QWidget()
        self.header_actions_layout = QGridLayout(self.header_actions_widget)
        self.header_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.header_actions_layout.setHorizontalSpacing(8)
        self.header_actions_layout.setVerticalSpacing(8)
        self.connect_button = QPushButton("Connect Robinhood")
        self.connect_button.setAccessibleName("Connect or disconnect Robinhood")
        self.connect_button.setToolTip("Connect to the consented Robinhood provider session")
        self.connect_button.clicked.connect(lambda: self._start_task("connection", self._connect()))
        self.authorize_button = QPushButton("Authorize && Start Session")
        self.authorize_button.setObjectName("primary")
        self.authorize_button.clicked.connect(self._authorize)
        self.start_button = QPushButton("Start Strategy")
        self.start_button.clicked.connect(self._start_strategy)
        self.shadow_button = QPushButton("Start Live Shadow")
        self.shadow_button.setToolTip("Run live observations and virtual fills without sending orders")
        self.shadow_button.clicked.connect(self._toggle_shadow)
        self.kill_button = QPushButton("Stop / cancel…")
        self.kill_button.setObjectName("danger")
        self.kill_button.clicked.connect(
            lambda: self._start_task("stop-and-cancel", self._stop_and_cancel())
        )
        self.kill_button.setToolTip(
            "Stop automation immediately, then review GRANDE-owned open orders for cancellation. "
            "Robinhood stays connected; filled positions remain open."
        )
        self.flatten_button = QPushButton("Flatten Position")
        self.flatten_button.setObjectName("flatten")
        self.flatten_button.clicked.connect(lambda: self._start_task("flatten", self._flatten()))
        self.settings_button = QPushButton("Settings")
        self.settings_button.setAccessibleName("Settings and permissions")
        self.settings_button.setToolTip("Review account scope, capabilities, privacy, and cadence")
        self.settings_button.clicked.connect(self._open_settings)
        self.header_actions = (
            self.connect_button,
            self.kill_button,
            self.settings_button,
        )
        for button in self.header_actions:
            button.setMinimumWidth(0)
        self.header_layout.addWidget(self.brand_row, 0, 0)
        self.header_layout.addWidget(self.header_actions_widget, 0, 1)
        self.header_layout.setColumnStretch(1, 1)
        outer.addWidget(self.header)
        self.notice_bar = QFrame()
        self.notice_bar.setObjectName("card")
        notice_layout = QHBoxLayout(self.notice_bar)
        self.notice_text = QLabel()
        self.notice_text.setWordWrap(True)
        self.notice_text.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_text.setAccessibleName("Latest operation result")
        self.notice_dismiss = QPushButton("Dismiss")
        self.notice_dismiss.clicked.connect(self.notice_bar.hide)
        notice_layout.addWidget(self.notice_text, 1)
        notice_layout.addWidget(self.notice_dismiss)
        outer.addWidget(self.notice_bar)
        self.notice_bar.hide()

        self.stop_status = QLabel()
        self.stop_status.setWordWrap(True)
        self.stop_status.setAccessibleName("Stop and cancellation status")
        self.stop_status.hide()
        outer.addWidget(self.stop_status)

        self.broker_panel = QScrollArea()
        self.broker_panel.setWidgetResizable(True)
        self.broker_panel.setFrameShape(QFrame.Shape.NoFrame)
        self.broker_panel.setAccessibleName("Trading session details")
        session_content = QWidget()
        session_content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.broker_panel.setWidget(session_content)
        broker_layout = QVBoxLayout(session_content)
        broker_layout.setContentsMargins(0, 0, 0, 0)
        session_title = QLabel("Trading session")
        session_title.setObjectName("dialogTitle")
        broker_layout.addWidget(session_title)
        session_intro = QLabel("Observe in shadow, or review a bounded live session. No unattended orders are enabled.")
        session_intro.setWordWrap(True)
        session_intro.setObjectName("settingsDescription")
        broker_layout.addWidget(session_intro)
        self.session_state_title = QLabel("Not connected")
        self.session_state_title.setObjectName("dialogTitle")
        self.session_state_title.setWordWrap(True)
        self.session_state_detail = QLabel("Connect Robinhood to see your account. No trading is active.")
        self.session_state_detail.setWordWrap(True)
        self.session_state_detail.setObjectName("settingsDescription")
        broker_layout.addWidget(self.session_state_title)
        broker_layout.addWidget(self.session_state_detail)
        self.session_actions_layout = QGridLayout()
        self.session_actions = (self.shadow_button, self.authorize_button, self.start_button, self.flatten_button)
        broker_layout.addLayout(self.session_actions_layout)
        cards = QGridLayout()
        self.cards_layout = cards
        cards.setHorizontalSpacing(8)
        cards.setVerticalSpacing(8)
        self.account_card = MetricCard("Agentic account", "Disconnected")
        self.value_card = MetricCard("Account value", "—")
        self.buying_power_card = MetricCard("Available buying power", "—")
        self.session_card = MetricCard("Trading permission", "Not approved")
        self.signal_card = MetricCard("QQQ regime", "FLAT")
        self.pair_action_card = MetricCard("Pair action (T,S)", "(0,0)")
        self.drawdown_card = MetricCard("Loss from session peak", "$0.00")
        self.shadow_card = MetricCard("Live shadow", "OFF")
        self.metric_cards = (
            self.value_card,
            self.buying_power_card,
            self.session_card,
            self.drawdown_card,
        )
        broker_layout.addLayout(cards)
        self.authority_controls = AuthorityControlPanel()
        self.authority_controls.pause_requested.connect(self._pause_authority)
        self.authority_controls.resume_requested.connect(self._resume_authority)
        self.authority_controls.revoke_requested.connect(
            lambda: self._start_task("revoke-authority", self._revoke_authority())
        )
        broker_layout.addWidget(self.authority_controls)

        top = QSplitter(Qt.Orientation.Horizontal)
        self.market_splitter = top
        top.setObjectName("marketOverviewSplitter")
        top.setMinimumHeight(180)
        top.setChildrenCollapsible(False)
        self.chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.chart.setMinimumSize(0, 90)
        self.chart.setBackground("#141d27")
        self.chart.showGrid(x=True, y=True, alpha=0.18)
        self.chart.setLabel("left", "QQQ midpoint", units="$")
        self.chart_curve = self.chart.plot(pen=pg.mkPen("#d2bc8e", width=2))
        top.addWidget(self.chart)

        self.quotes_table = self._table(["Symbol", "Bid", "Ask", "Last", "Spread", "Age"])
        # Header plus all three tracked symbols must remain visible in either split orientation.
        self.quotes_table.setMinimumSize(0, 120)
        top.addWidget(self.quotes_table)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)
        top.setSizes([850, 500])
        technical = QWidget()
        technical_layout = QVBoxLayout(technical)
        technical_layout.setContentsMargins(0, 0, 0, 0)
        detail_cards = QGridLayout()
        for index, card in enumerate((self.account_card, self.signal_card, self.pair_action_card, self.shadow_card)):
            detail_cards.addWidget(card, index // 2, index % 2)
        technical_layout.addLayout(detail_cards)
        technical_layout.addWidget(top)
        self.market_details = DisclosureSection("Market data and strategy details", technical)
        broker_layout.addWidget(self.market_details)
        broker_layout.addStretch()

        self.tabs = WorkspaceTabs()
        self.welcome_widget = WelcomeWidget(self.config)
        self.welcome_widget.open_activation.connect(self._open_activation)
        self.welcome_widget.open_sandbox.connect(self._open_sandbox)
        self.welcome_widget.open_settings.connect(self._open_settings)
        self.positions_table = self._table(["Symbol", "Quantity", "Sellable", "Average", "Mark", "P/L"])
        self.orders_table = self._table(["Time", "Symbol", "Side", "State", "Quantity/$", "Fill", "Order ID"])
        self.activity_table = self._table(["Time", "Severity", "Event"])
        self.fund_widget = QWidget()
        fund_layout = QVBoxLayout(self.fund_widget)
        fund_notice = QLabel(
            "Ledger only — GRANDE Alpha never transfers money. A planned contribution becomes confirmed "
            "only after an authorized operator verifies an independent external transfer."
        )
        fund_notice.setWordWrap(True)
        fund_layout.addWidget(fund_notice)
        fund_actions = QHBoxLayout()
        self.fund_total_label = QLabel("Confirmed capital contributions: $0.00")
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
        self.tabs.overview.addTab(self.welcome_widget, "Summary")
        self.tabs.addTab(self.tabs.overview, "Overview")
        self.tabs.addTab(self.broker_panel, "Trading")
        self.agent_widget = AgentWidget(self.controller)
        self.tabs.addTab(self.agent_widget, "Agent · Stocks + Crypto")
        self.activation_widget = ActivationChecklistWidget()
        self.activation_widget.run_safe_checks.connect(
            lambda: self._start_task("safe-checks", self._run_safe_activation_checks())
        )
        self.activation_widget.open_next_action.connect(self._open_activation_next_action)
        self.live_readiness_table = self.activation_widget.table
        self.sandbox_widget = SandboxWidget(
            self.controller.store,
            allow_remote_data=self.config.remote_market_data_enabled,
            task_supervisor=self.tasks,
        )
        self.tabs.addTab(self.sandbox_widget, "Research")
        self.tabs.overview.addTab(self.activation_widget, "Readiness checks")
        self.tabs.add_record(self.positions_table, "Positions")
        self.tabs.add_record(self.orders_table, "Orders")
        self.tabs.add_record(self.activity_table, "Activity")
        if self.config.personal_ledger_enabled:
            self.tabs.add_record(self.fund_widget, "Capital ledger")
        self.tabs.addTab(self.tabs.records, "Activity")
        self.tabs.currentChanged.connect(self._workspace_changed)
        self.tabs.records.currentChanged.connect(self._workspace_changed)
        self.tabs.overview.currentChanged.connect(self._workspace_changed)
        self.market_details.toggle.toggled.connect(self._workspace_changed)
        self.tabs.setUsesScrollButtons(True)
        # Every page advertises a large ideal size, but task pages own their scrolling.
        # Ignoring that aggregate vertical hint lets the workspace splitter honor a
        # constrained landscape window without crushing the broker/quote overview.
        self.tabs.setMinimumHeight(0)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.navigation = WorkspaceNavigation(self.tabs)
        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setObjectName("primaryWorkspaceSplitter")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(self.navigation)
        self.workspace_splitter.addWidget(self.tabs)
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setSizes([176, 1160])
        outer.addWidget(self.workspace_splitter, 1)

        self.status = QLabel(
            "RESEARCH MODE • No optional network or broker action occurs without your consent."
        )
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.setCentralWidget(root)
        self._build_menus()
        self.menu_toggle = QPushButton("Tools")
        self.menu_toggle.setCheckable(True)
        self.menu_toggle.setAccessibleName("Show advanced menus")
        self.menu_toggle.toggled.connect(self.menuBar().setVisible)
        self.menuBar().hide()
        self.header_actions = (*self.header_actions, self.menu_toggle)
        for button in (self.settings_button, self.menu_toggle):
            button.setParent(self.header_actions_widget)
            button.show()
        self.plan_button.hide()
        self._refresh_fund()
        self._set_controls()
        # Populate the readiness page before it is first selected so navigation
        # never presents an empty checklist while a broker snapshot is available.
        self._update_live_readiness()
        self._responsive_mode: tuple[object, ...] | None = None
        self._apply_responsive_layout(self.width(), self.height(), force=True)

    @staticmethod
    def _reflow_grid(
        layout: QGridLayout, widgets: tuple[QWidget, ...], columns: int, *, visible_only: bool = False
    ) -> None:
        for widget in widgets:
            layout.removeWidget(widget)
        for column in range(len(widgets)):
            layout.setColumnStretch(column, 0)
        visible = tuple(widget for widget in widgets if not widget.isHidden()) if visible_only else widgets
        for index, widget in enumerate(visible):
            layout.addWidget(widget, index // columns, index % columns)
        for column in range(max(1, columns)):
            layout.setColumnStretch(column, 1)

    def _reflow_header_actions(self, columns: int) -> None:
        for button in self.header_actions:
            self.header_actions_layout.removeWidget(button)
        for column in range(len(self.header_actions)):
            self.header_actions_layout.setColumnStretch(column, 0)
        visible = tuple(button for button in self.header_actions if not button.isHidden())
        for index, button in enumerate(visible):
            self.header_actions_layout.addWidget(button, index // columns, index % columns)
        for column in range(min(columns, max(1, len(visible)))):
            self.header_actions_layout.setColumnStretch(column, 1)

    def _apply_responsive_layout(self, width: int, height: int, *, force: bool = False) -> None:
        if not hasattr(self, "header_actions_layout"):
            return
        if width >= 1680:
            action_columns = len(self.header_actions)
            card_columns = 4
            header_mode = "inline"
        elif width >= 1280:
            action_columns = 4
            card_columns = 4
            header_mode = "inline"
        elif width >= 1000:
            action_columns = 4
            card_columns = 4
            header_mode = "stacked"
        else:
            action_columns = 2
            card_columns = 2
            header_mode = "stacked"
        action_visibility = tuple(not button.isHidden() for button in self.header_actions)
        session_visibility = tuple(not button.isHidden() for button in self.session_actions)
        mode = (action_columns, card_columns, header_mode, action_visibility, session_visibility, width < 1100)
        if force or mode != getattr(self, "_responsive_mode", None):
            self._responsive_mode = mode
            self._reflow_header_actions(action_columns)
            self._reflow_grid(self.cards_layout, self.metric_cards, card_columns)
            self._reflow_grid(
                self.session_actions_layout, self.session_actions, 2 if width < 1100 else 4,
                visible_only=True,
            )
            self.header_layout.removeWidget(self.brand_row)
            self.header_layout.removeWidget(self.header_actions_widget)
            if header_mode == "inline":
                self.header_layout.addWidget(self.brand_row, 0, 0)
                self.header_layout.addWidget(self.header_actions_widget, 0, 1)
                self.header_layout.setColumnStretch(0, 0)
                self.header_layout.setColumnStretch(1, 1)
            else:
                self.header_layout.addWidget(self.brand_row, 0, 0)
                self.header_layout.addWidget(self.header_actions_widget, 1, 0)
                self.header_layout.setColumnStretch(0, 1)
                self.header_layout.setColumnStretch(1, 0)

        compact = width < 1200
        if hasattr(self, "navigation"):
            compact_navigation = width < 1100
            self.navigation.set_compact(compact_navigation)
            wanted = Qt.Orientation.Vertical if compact_navigation else Qt.Orientation.Horizontal
            if self.workspace_splitter.orientation() != wanted:
                self.workspace_splitter.setOrientation(wanted)
                self.workspace_splitter.setSizes([58, 900] if compact_navigation else [176, 1160])
        for card in self.metric_cards:
            card.set_compact(compact)

        portrait_market = height > width
        orientation = Qt.Orientation.Vertical if portrait_market else Qt.Orientation.Horizontal
        market_mode = (
            "portrait"
            if portrait_market
            else "compact_landscape"
            if width < 1120
            else "landscape"
        )
        if market_mode != getattr(self, "_market_layout_mode", None):
            self._market_layout_mode = market_mode
            self.market_splitter.setOrientation(orientation)
            self.market_splitter.setSizes(
                [95, 135]
                if portrait_market
                else [420, 580]
                if market_mode == "compact_landscape"
                else [850, 500]
            )
        market_height = (
            230
            if portrait_market
            else 140
            if market_mode == "compact_landscape"
            else 170
        )
        self.market_splitter.setMinimumHeight(market_height)
        self.outer_layout.setContentsMargins(*(10, 9, 10, 9) if compact else (16, 14, 16, 14))
        self.outer_layout.setSpacing(8 if compact else 12)
        if hasattr(self, "sandbox_widget"):
            self.sandbox_widget.apply_responsive_layout(self.tabs.width(), self.tabs.height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width(), event.size().height())

    def _action(self, text: str, callback, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
            self.addAction(action)  # Shortcuts remain usable while advanced menus are hidden.
        return action

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)

        self.file_menu = menu_bar.addMenu("File")
        self.export_action = self._action("Export redacted support diagnostics…", self._export_diagnostics)
        self.file_menu.addAction(self.export_action)
        self.settings_action = self._action("Settings && Permissions…", self._open_settings, "Ctrl+,")
        self.file_menu.addAction(self.settings_action)
        self.file_menu.addSeparator()
        self.exit_action = self._action("Exit", self.close, "Ctrl+Q")
        self.file_menu.addAction(self.exit_action)

        self.view_menu = menu_bar.addMenu("View")
        self.theme_action = self._action("Dark mode", self._toggle_theme)
        self.theme_action.setCheckable(True)
        self.view_menu.addAction(self.theme_action)
        self.view_menu.addSeparator()
        destinations = (
            ("Overview", self.welcome_widget, "Ctrl+1"),
            ("Trading", self.broker_panel, "Ctrl+2"),
            ("Research", self.sandbox_widget, "Ctrl+3"),
            ("Activity", self.tabs.records, "Ctrl+4"),
            ("Readiness checks", self.activation_widget, "Ctrl+5"),
            ("Positions", self.positions_table, None),
            ("Orders", self.orders_table, None),
            ("Activity", self.activity_table, None),
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
            "Capital Planning Ledger",
            lambda _checked=False: self._show_tab(self.fund_widget),
            "Ctrl+6",
        )
        self.view_menu.addAction(self.fund_view_action)
        self.view_menu.addSeparator()
        self.reset_layout_action = self._action("Reset Window && Table Columns", self._reset_layout)
        self.view_menu.addAction(self.reset_layout_action)
        self.full_screen_action = self._action("Full Screen", self._toggle_full_screen, "F11")
        self.full_screen_action.setCheckable(True)
        self.view_menu.addAction(self.full_screen_action)

        self.broker_menu = menu_bar.addMenu("Broker")
        self.broker_connect_action = self._action(
            "Connect Robinhood…",
            lambda _checked=False: self._start_task("connection", self._connect()),
            "Ctrl+Shift+C",
        )
        self.broker_menu.addAction(self.broker_connect_action)
        self.refresh_action = self._action(
            "Refresh Account && Quotes",
            lambda _checked=False: self._start_task("broker-refresh", self._refresh_broker()),
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
        self.authorize_action = self._action("Authorize && Start Session…", self._authorize)
        self.safety_menu.addAction(self.authorize_action)
        self.start_strategy_action = self._action("Start Supervised Strategy", self._start_strategy)
        self.safety_menu.addAction(self.start_strategy_action)
        self.stop_cancel_action = self._action(
            "STOP + CANCEL Agentic Orders",
            lambda _checked=False: self._start_task("stop-and-cancel", self._stop_and_cancel()),
            "Ctrl+Shift+X",
        )
        self.safety_menu.addAction(self.stop_cancel_action)
        self.flatten_action = self._action(
            "Flatten Position…", lambda _checked=False: self._start_task("flatten", self._flatten())
        )
        self.safety_menu.addAction(self.flatten_action)
        self.safety_menu.addSeparator()
        self.safety_explainer_action = self._action("Explain Safety Locks…", self._show_safety_help)
        self.safety_menu.addAction(self.safety_explainer_action)

        self.help_menu = menu_bar.addMenu("Help")
        self.quickstart_action = self._action("Quick Start…", self._show_quickstart)
        self.help_menu.addAction(self.quickstart_action)
        self.activation_help_action = self._action(
            "Activation Checklist…", self._open_activation
        )
        self.help_menu.addAction(self.activation_help_action)
        self.glossary_action = self._action("Terminology && Glossary…", self._show_glossary, "F1")
        self.help_menu.addAction(self.glossary_action)
        self.account_scope_action = self._action("Account Scope && Privacy…", self._show_account_scope)
        self.help_menu.addAction(self.account_scope_action)
        self.plans_action = self._action("Plans && Upgrade…", self._show_plans)
        self.help_menu.addAction(self.plans_action)
        self.help_menu.addSeparator()
        self.about_action = self._action("About GRANDE Alpha", self._about)
        self.help_menu.addAction(self.about_action)

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        apply_table_header_help(table)
        configure_adjustable_columns(table, headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        return table

    def _show_glossary(self) -> None:
        GlossaryDialog(self).exec()

    def _show_plans(self) -> None:
        ProductPlansDialog(self).exec()

    def _open_sandbox(self) -> None:
        index = self.tabs.indexOf(self.sandbox_widget)
        if index >= 0:
            self.tabs.setCurrentIndex(index)

    def _open_activation(self) -> None:
        self.tabs.setCurrentWidget(self.activation_widget)

    def _show_tab(self, widget: QWidget) -> None:
        index = self.tabs.indexOf(widget)
        if index >= 0:
            self.tabs.setCurrentWidget(widget)

    def _workspace_changed(self, _index: int = 0) -> None:
        if not hasattr(self, "authorize_action"):
            return
        self._set_controls()
        self._update_visible_tables()
        if self.tabs.currentWidget() is self.broker_panel and self.market_details.toggle.isChecked():
            self.chart_curve.setData(list(self._chart_times), list(self._chart_prices))

    def _update_visible_tables(self) -> None:
        current = self.tabs.currentWidget()
        if current is self.broker_panel and self.market_details.toggle.isChecked():
            self._update_quotes(self._snapshot)
        elif current is self.tabs.overview and self.tabs.overview.currentWidget() is self.activation_widget:
            self._update_live_readiness()
        elif current is self.tabs.records:
            page = self.tabs.records.currentWidget()
            if page is self.positions_table:
                self._update_positions(self._snapshot)
            elif page is self.orders_table:
                self._update_orders(self._snapshot)

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
        self.workspace_splitter.setSizes([176, 1160])
        self.sandbox_widget.main_splitter.setSizes([480, 920])
        self.sandbox_widget.fill_splitter.setSizes([420, 190])
        self._apply_responsive_layout(1440, 900, force=True)
        for table in self.findChildren(QTableWidget):
            reset_column_widths(table)
        self.status.setText("Window and adjustable table-column widths reset to their defaults.")

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

    async def _run_safe_activation_checks(self) -> None:
        button = self.activation_widget.safe_checks_button
        button.setEnabled(False)
        button.setText("Checking read-only state…")
        self.authorize_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.flatten_button.setEnabled(False)
        self.authorize_action.setEnabled(False)
        self.start_strategy_action.setEnabled(False)
        self.flatten_action.setEnabled(False)
        try:
            if self.controller.risk.grant is not None or self._snapshot.strategy_running:
                self._update_live_readiness()
                self.status.setText(
                    "SAFE CHECKS REFUSED • Revoke the active live grant and stop the strategy first • "
                    "No broker refresh or order method was requested"
                )
                return
            if not self.config.broker_connection_enabled:
                self._update_live_readiness()
                self.status.setText(
                    "SAFE CHECKS STOPPED • Broker-data capability is off • Select Broker capability "
                    "in Readiness checks for the exact consent step"
                )
                return
            if not self._snapshot.connected:
                self._update_live_readiness()
                self.status.setText(
                    "SAFE CHECKS STOPPED • Robinhood is disconnected • Select Exact Agentic account "
                    "in Readiness checks to connect with browser consent"
                )
                return
            await self.controller.safe_read_only_refresh()
            self._update_live_readiness()
            self.status.setText(
                "ACCOUNT CHECKS COMPLETE • See readiness rows for separate quote/execution status • "
                "No order review, placement, or cancellation method was requested"
            )
        except Exception as exc:
            self._update_live_readiness()
            self._show_notice(f"Account check incomplete. No order operation was attempted. {exc}")
        finally:
            button.setText("Run safe checks")
            button.setEnabled(
                self.controller.risk.grant is None and not self._snapshot.strategy_running
            )
            self._set_controls()

    def _open_activation_next_action(self, gate: str) -> None:
        guidance = activation_guidance(gate)
        if guidance.destination == "settings":
            self._open_settings()
            return
        if guidance.destination == "evidence":
            self._show_research_tab(3)
            return
        if guidance.destination == "connect":
            if not self.config.broker_connection_enabled:
                self._open_settings()
            elif not self._snapshot.connected:
                self._start_task("connection", self._connect())
            else:
                self._start_task("safe-checks", self._run_safe_activation_checks())
            return
        if guidance.destination == "refresh":
            self._start_task("safe-checks", self._run_safe_activation_checks())
            return
        QMessageBox.information(
            self,
            f"Next step — {gate}",
            f"Owner: {guidance.owner}\n\n{guidance.explanation}\n\n"
            f"Exact next action:\n{guidance.next_action}\n\n"
            "GRANDE Alpha will not mark this complete from a checkbox or silently act for you.",
        )

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
            self._start_task("forget-credentials", self._forget_credentials())

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
            "1. Open Readiness checks for the current owner and exact next action for every condition.\n"
            "2. Click Run safe checks for connected read-only account and quote refreshes.\n"
            "3. Use Research Sandbox to build observed, after-cost evidence and inspect virtual fills.\n"
            "4. Complete every item marked YOU or EXTERNAL REVIEW yourself.\n\n"
            "Live shadow is virtual and cannot become live trading. No strategy is guaranteed profitable. "
            "Shared account, route, and capability checks can "
            "expose attended supervised review; passing every evidence and runtime condition only makes "
            "a separate autonomous authorize-and-start review available.",
        )

    def _show_safety_help(self) -> None:
        QMessageBox.information(
            self,
            "Why live controls are locked",
            "GRANDE Alpha defaults to research and shadow mode. The attended supervised path requires "
            "an enabled broker capability, the constrained supported route, an enabled ticket capability, "
            "a funded Agentic account, a short-lived bounded session, and a fresh confirmation for every "
            "exact reviewed order. The separate autonomous path also requires a current passing Evidence "
            "Lab certificate and runtime parity. Any mismatch fails closed; neither path implies profit.",
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
            live_evidence_checker=self.controller.config_evidence_ready,
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
                "strategy_name": updated.strategy_name,
                "market_hours": updated.market_hours,
                "order_type": updated.order_type,
                "time_in_force": updated.time_in_force,
                "limit_offset_bps": updated.limit_offset_bps,
            },
        )
        self._start_task(
            "permission-revocations",
            self._apply_permission_revocations(previous, updated, dialog.forget_credentials.isChecked()),
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
        index = self.tabs.records.indexOf(self.fund_widget)
        if self.config.personal_ledger_enabled and index < 0:
            self.tabs.add_record(self.fund_widget, "Capital ledger")
        elif not self.config.personal_ledger_enabled and index >= 0:
            self.tabs.records.removeTab(index)
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
            f"<h3>GRANDE Alpha {__version__}</h3>"
            "<p>Local-first leveraged-ETF strategy research and consent-gated execution workstation.</p>"
            "<p>The Community plan is free and functional. This release has no paid checkout or "
            "server-side Pro entitlement; planned Pro conveniences never replace safety controls.</p>"
            "<p>Independent community software. Not affiliated with or endorsed by Robinhood, "
            "ProShares, Nasdaq, or Yahoo. No telemetry. No investment, legal, or tax advice.</p>"
            '<p><a href="https://github.com/aaronjs99/grande-alpha/issues">Community support</a> · '
            '<a href="https://github.com/aaronjs99/grande-alpha/security/advisories/new">'
            "Private security advisory</a></p>"
            "<p><b>Never post credentials, account identifiers, balances, order details, or unredacted "
            "diagnostics in a public issue.</b></p>"
            "<p>Licensed under Apache-2.0. See README, PRIVACY.md, SECURITY.md, and docs/ for details.</p>",
        )

    def _start_task(self, name: str, awaitable) -> None:
        self.tasks.start(name, awaitable)

    def _on_task_error(self, name: str, exc: BaseException) -> None:
        message = f"Background operation '{name}' failed: {exc}"
        self.controller.log(message, "error", "runtime")
        self.status.setText(message)
        if not self.tasks.closing:
            self._show_notice(message)

    def _show_notice(self, message: str) -> None:
        """Recoverable operation feedback never steals focus or blocks navigation."""
        self.notice_text.setText(message)
        self.notice_bar.show()

    async def _connect(self) -> None:
        if self._connection_task is not None or self._close_requested:
            return
        self._connection_task = asyncio.current_task()
        self._on_busy(True)
        try:
            if self._snapshot.connected:
                await self._disconnect_for_user()
            else:
                await self.controller.connect()
        except Exception as exc:
            await self._stop_message("Robinhood connection", str(exc), error=True)
        finally:
            self._connection_task = None
            self._on_busy(False)

    async def _disconnect_for_user(self) -> bool:
        self._set_stop_status("Stopping automation and disconnecting Robinhood…")
        try:
            await self.controller.disconnect()
        except Exception as exc:
            self._set_stop_status("Automation stopped. Broker order cleanup is unverified.")
            accepted = await self._stop_message(
                "Disconnect without verified order cleanup?",
                f"{exc}\n\nLocal automation is stopped. Open orders may still fill in Robinhood. "
                "Disconnect will not cancel orders or sell positions. Order records remain for "
                "reconciliation on your next connection. Disconnect anyway?",
                question=True,
            )
            if not accepted:
                return False
            try:
                await self.controller.disconnect_without_order_cleanup(unverified=True)
            except Exception as transport_exc:
                await self._stop_message("Disconnect did not finish", str(transport_exc), error=True)
                return False
        self._set_stop_status("Disconnected. Local automation stopped; broker orders were not cancelled.")
        return True

    def _authorize(self) -> None:
        if not self._snapshot.account or not self._snapshot.portfolio:
            return
        evidence_gated = self.controller.live_evidence_ready()
        dialog = LiveGrantDialog(
            self._snapshot.account,
            self._snapshot.portfolio,
            self.config,
            self,
            strategy_fingerprint=self.controller.current_strategy_fingerprint(),
            evidence_gated=evidence_gated,
        )
        if dialog.exec() == dialog.DialogCode.Accepted:
            try:
                grant = dialog.grant()
                self._start_task("authorize", self._activate_reviewed_grant(grant, evidence_gated))
            except Exception as exc:
                QMessageBox.critical(self, "Session not authorized", str(exc))

    async def _activate_reviewed_grant(self, grant: LiveGrant, evidence_gated: bool) -> None:
        try:
            # Reviewing limits can outlast the account freshness interval. Refresh
            # after review without changing any of the approved scope or expiry.
            await self.controller.safe_read_only_refresh()
            if evidence_gated:
                if not self.controller.live_evidence_ready(grant):
                    raise RuntimeError("Evidence changed during review; reopen the session review.")
                self.controller.authorize_live(grant)
            else:
                self.controller.authorize_supervised_experimental(grant)
            if self.controller.entry_window_open():
                self.controller.start_strategy()
            else:
                QMessageBox.information(
                    self, "Session authorized — execution not started",
                    "Approval recorded. Start during the permitted market window with fresh "
                    "account data and quotes. The displayed expiry still applies; this does "
                    "not schedule an automatic start or extend approval to another day.",
                )
        except Exception as exc:
            self._show_notice(f"Session not started. {exc}")

    async def _confirm_strategy_order(
        self,
        request: OrderConfirmationRequest,
    ) -> OrderConfirmationDecision:
        """Present a non-blocking, safe-default decision for exactly one reviewed order."""

        dialog = OrderConfirmationDialog(request, self)
        loop = asyncio.get_running_loop()
        finished: asyncio.Future[bool] = loop.create_future()

        def resolve(result: int) -> None:
            if not finished.done():
                finished.set_result(result == int(dialog.DialogCode.Accepted))

        dialog.finished.connect(resolve)
        dialog.open()
        try:
            accepted = await finished
            return OrderConfirmationDecision(
                preview_id=request.preview_id,
                accepted=accepted,
                typed_phrase=dialog.confirmation.text() if accepted else "",
                confirmed_at=utc_now(),
            )
        finally:
            try:
                dialog.finished.disconnect(resolve)
            except (RuntimeError, TypeError):
                pass
            dialog.deleteLater()

    def _start_strategy(self) -> None:
        try:
            self.controller.start_strategy()
        except Exception as exc:
            self._show_notice(f"Strategy remains stopped. {exc}")

    def _pause_authority(self) -> None:
        try:
            self.controller.pause_live_authority()
        except Exception as exc:
            QMessageBox.warning(self, "Authority was not paused", str(exc))

    def _resume_authority(self) -> None:
        try:
            self.controller.resume_live_authority()
        except Exception as exc:
            QMessageBox.warning(self, "Authority was not resumed", str(exc))

    async def _revoke_authority(self) -> None:
        await self.controller.revoke_live_authority("Authority revoked by user")
        QMessageBox.information(
            self,
            "Authority revoked",
            "New orders are locked. Existing orders were not cancelled. Use STOP + CANCEL "
            "to review and explicitly confirm any GRANDE-owned cancellations.",
        )

    def _set_stop_status(self, text: str) -> None:
        self.stop_status.setText(text)
        self.stop_status.show()

    def _clear_stop_status_on_restart(self, _agent_snapshot=None) -> None:
        if not self._stop_cancel_busy and (
            self._snapshot.strategy_running
            or self._snapshot.shadow_running
            or self.controller.agent.snapshot.running
        ):
            self.stop_status.hide()

    async def _stop_message(
        self,
        title: str,
        text: str,
        *,
        question: bool = False,
        error: bool = False,
    ) -> bool:
        """Keep the asyncio/Qt event loop running while the operator reads a dialog."""

        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(text)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setIcon(
            QMessageBox.Icon.Question if question else
            QMessageBox.Icon.Critical if error else QMessageBox.Icon.Information
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            if question else QMessageBox.StandardButton.Ok
        )
        dialog.setDefaultButton(
            QMessageBox.StandardButton.No if question else QMessageBox.StandardButton.Ok
        )
        finished = asyncio.get_running_loop().create_future()

        def resolve(_result: int) -> None:
            if not finished.done():
                clicked = dialog.clickedButton()
                finished.set_result(
                    clicked is not None
                    and dialog.standardButton(clicked) == QMessageBox.StandardButton.Yes
                )

        dialog.finished.connect(resolve)
        dialog.open()
        try:
            return await finished
        finally:
            dialog.finished.disconnect(resolve)
            dialog.close()
            dialog.deleteLater()

    async def _stop_and_cancel(self) -> None:
        if self._stop_cancel_busy:
            return
        self._stop_cancel_busy = True
        self._sync_data_timers()
        self.kill_button.setText("STOPPING…")
        self.kill_button.setEnabled(False)
        self.stop_cancel_action.setEnabled(False)
        self._set_stop_status("Stopping automation and checking Robinhood open orders…")
        plan = None
        try:
            # Only the read-only preview is bounded here. Broker write outcomes must
            # continue through the controller's terminal verification, never be retried.
            try:
                plan = await asyncio.wait_for(
                    self.controller.prepare_cancel_plan(), timeout=STOP_PREVIEW_TIMEOUT_SECONDS
                )
            except TimeoutError:
                message = (
                    "Automation stopped; new orders are locked. Robinhood's order check timed out. "
                    "No cancellation was sent. Check open orders in Robinhood, then retry STOP + CANCEL."
                )
                self._set_stop_status(message)
                await self._stop_message("Order check timed out", message, error=True)
                return
            unrelated = (
                f" {len(plan.unrelated_order_ids)} unrelated open order(s) remain untouched."
                if plan.unrelated_order_ids else ""
            )
            if not plan.order_ids:
                message = (
                    "Automation stopped; new orders are locked. "
                    "No GRANDE-owned open orders to cancel."
                    f"{unrelated} Robinhood stays connected. Filled positions remain open."
                )
                self._set_stop_status(message)
                await self._stop_message("Stopped — no orders to cancel", message)
                return
            self._set_stop_status("Automation stopped. Waiting for your cancellation decision.")
            accepted = await self._stop_message(
                "Confirm GRANDE-owned order cancellation",
                f"Agentic account ••••{plan.account_number[-4:]}\n"
                f"Cancel exactly {len(plan.order_ids)} GRANDE-owned order(s):\n\n"
                + "\n".join(plan.order_summaries)
                + f"\n\n{unrelated.strip()}\nFilled positions remain open. Continue?",
                question=True,
            )
            if not accepted:
                self._set_stop_status(
                    "Automation stopped; new orders are locked. Cancellation declined; "
                    "no cancellation was sent. Robinhood stays connected."
                )
                return
            self.kill_button.setText("VERIFYING…")
            self._set_stop_status("Automation stopped. Cancelling reviewed orders and verifying their final state…")
            verified = await self.controller.execute_confirmed_cancel(plan)
            if verified:
                message = (
                    f"Automation stopped. All {len(plan.order_ids)} reviewed order(s) are verified terminal "
                    "(cancelled, filled, or otherwise closed)."
                    f"{unrelated} Robinhood stays connected. Filled positions remain open."
                )
                self._set_stop_status(message)
                await self._stop_message("Stop and cancellation check complete", message)
            else:
                message = (
                    "Automation stopped; new orders are locked. Cancellation could not be verified. "
                    "Check open orders and fills in Robinhood before retrying STOP + CANCEL."
                )
                self._set_stop_status(message)
                await self._stop_message("Cancellation not verified", message, error=True)
        except Exception as exc:
            message = (
                "STOP + CANCEL could not complete. Check open orders and fills in Robinhood. "
                f"Cancellation is not confirmed.\n\n{exc}"
            )
            self._set_stop_status(message)
            await self._stop_message("Stop and cancellation check failed", message, error=True)
        finally:
            if plan is not None:
                self.controller.discard_cancel_plan(plan)
            self._stop_cancel_busy = False
            self._sync_data_timers()
            self.kill_button.setText("STOP + CANCEL")
            self.kill_button.setEnabled(self._snapshot.connected)
            self.stop_cancel_action.setEnabled(self._snapshot.connected)

    def _toggle_shadow(self) -> None:
        try:
            if self._snapshot.shadow_running:
                self.controller.stop_shadow()
            else:
                self.controller.start_shadow()
        except ShadowRecoveryRequired as exc:
            answer = QMessageBox.question(
                self, "Start a separate simulation?",
                f"{exc}\n\nKeep the old ledger unchanged and start a separate simulation? "
                "The previous run will be recorded as interrupted, not completed. "
                "No real orders will be placed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                try:
                    self.controller.start_shadow(separate_run_from=exc.run_id)
                except Exception as recovery_error:
                    self._show_notice(f"Simulation not started. {recovery_error}")
        except Exception as exc:
            self._show_notice(f"Simulation unchanged. {exc}")

    def _plan_contribution(self) -> None:
        dialog = FundPlanDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            entry_id = self.controller.store.plan_research_contribution(**dialog.values())
            self.controller.log(
                f"Saved capital-ledger plan #{entry_id}; no money was transferred",
                category="research_fund",
            )
            self._refresh_fund()
        except Exception as exc:
            QMessageBox.critical(self, "Contribution plan not saved", str(exc))

    def _confirm_contribution(self) -> None:
        row = self.fund_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Capital Planning Ledger", "Select a planned contribution first.")
            return
        entry_id = int(self.fund_table.item(row, 0).text())
        amount = self.fund_table.item(row, 6).text()
        status = self.fund_table.item(row, 7).text().lower()
        if status == "confirmed":
            QMessageBox.information(self, "Capital Planning Ledger", "This contribution is already confirmed.")
            return
        reference, ok = QInputDialog.getText(
            self,
            "External transfer reference",
            "After independently completing the external transfer, enter its confirmation/reference:",
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
            self._on_event("info", "Capital-ledger confirmation declined; ledger unchanged")
            return
        try:
            self.controller.store.confirm_research_contribution(entry_id, reference)
            self.controller.log(
                f"Marked capital-ledger entry #{entry_id} confirmed at {amount}",
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
        disclosure_section = (
            f"Robinhood market-data disclosure (verbatim):\n{review.market_data_disclosure}\n\n"
            if review.market_data_disclosure is not None
            else ""
        )
        estimated_price = review.estimated_execution_price
        estimated_proceeds = review.estimated_notional
        phrase = f"SELL {intent.quantity:g} {intent.symbol}"
        text, ok = QInputDialog.getText(
            self,
            "Confirm real-money sell",
            "Robinhood reviewed this exact real-money order:\n\n"
            f"Symbol: {intent.symbol}\n"
            f"Side: {intent.side.upper()}\n"
            f"Order type: {intent.order_type}\n"
            f"Quantity: {intent.quantity:g} shares\n"
            f"Estimated sell price at reviewed bid: ${estimated_price:,.2f} per share\n"
            f"Estimated proceeds: ${estimated_proceeds:,.2f}\n"
            "This is an estimate from the reviewed bid, not a guaranteed fill.\n\n"
            f"{disclosure_section}"
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
        self._connection_busy = busy
        self._sync_data_timers()
        self.connect_button.setEnabled(not busy and self._connection_task is None)
        self.connect_button.setText(
            ("Disconnecting…" if self._snapshot.connected else "Connecting in browser…")
            if busy
            else ("Disconnect" if self._snapshot.connected else "Connect Robinhood")
        )
        self._set_controls()

    def _sync_data_timers(self) -> None:
        """Run broker timers only after connection/startup has fully settled.

        OAuth can spin a nested Qt event loop while a connection task is active.  Letting
        timer callbacks create quote/reconcile tasks during that hand-off triggers qasync
        task re-entry and leaves destroyed pending tasks after a failed reconnect.  The
        controller still owns call coalescing; this UI boundary prevents calls from being
        created before there is a stable connected snapshot.
        """

        should_run = (
            self._snapshot.connected and not self._connection_busy
            and self._connection_task is None and not self._close_requested
            and not self._stop_cancel_busy
        )
        for timer in (self.timer, self.reconcile_timer):
            if should_run and not timer.isActive():
                timer.start()
            elif not should_run and timer.isActive():
                timer.stop()

    def _schedule_quote_refresh(self) -> None:
        """Schedule one quote tick only while qasync owns a running event loop."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.tasks.start("quote-refresh", self.controller.refresh_quotes())

    def _schedule_reconcile(self) -> None:
        """Schedule one account tick only while qasync owns a running event loop."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.tasks.start("account-reconcile", self.controller.reconcile())

    def _on_snapshot(self, snapshot: TradingSnapshot) -> None:
        self._snapshot = snapshot
        self._clear_stop_status_on_restart()
        self.agent_widget.update_account(snapshot)
        self._sync_data_timers()
        if snapshot.account:
            account_type = snapshot.account.account_type.strip().upper() or "UNKNOWN"
            self.account_card.title.setText(f"Agentic account • {account_type}")
            self.account_card.value.setText(f"{snapshot.account.nickname} {snapshot.account.masked}")
            self.account_card.setToolTip(
                "GRANDE Alpha deliberately selects the active Agentic account for broker views and orders. "
                + (
                    "This is a cash account: broker-reported buying power reflects whether sale proceeds are settled."
                    if account_type == "CASH"
                    else "Account permissions and current broker buying power remain authoritative."
                )
            )
        else:
            self.account_card.title.setText("Agentic account")
            self.account_card.value.setText("Disconnected")
            self.account_card.setToolTip("Connect Robinhood to resolve the active Agentic account.")
        if snapshot.portfolio:
            self.value_card.value.setText(f"${snapshot.portfolio.total_value:,.2f}")
            self.buying_power_card.value.setText(f"${snapshot.portfolio.buying_power:,.2f}")
        else:
            self.value_card.value.setText("—")
            self.buying_power_card.value.setText("—")
        session = snapshot.live_status if self.config.live_trading_enabled else "DISABLED"
        session = {"LOCKED": "Not approved", "DISABLED": "Off", "LIVE": "Approved"}.get(session, session)
        if snapshot.live_status == "LIVE" and snapshot.session_expires_at:
            session = f"Until {snapshot.session_expires_at.astimezone().strftime('%I:%M %p')}"
        self.session_card.value.setText(session)
        set_widget_style(self.session_card.value, "color:#00e507" if snapshot.live_status == "LIVE" else "color:#8fa4b8")
        self.authority_controls.set_authority_state(
            snapshot.live_status,
            self.controller.risk.grant,
            daily_notional_used=self.controller.risk.daily_notional_used,
            submitted_orders=self.controller.risk.trades_today,
        )
        self.signal_card.value.setText(snapshot.signal.regime.value.upper())
        signal_color = {Regime.BULLISH: "#00e507", Regime.BEARISH: "#ff697d", Regime.FLAT: "#f2c14e"}
        set_widget_style(self.signal_card.value, f"color:{signal_color[snapshot.signal.regime]}")
        self.pair_action_card.value.setText(snapshot.pair_action_label)
        set_widget_style(self.pair_action_card.value, "color:#f2c14e" if snapshot.pair_action_id == 4 else "color:#65b9ff")
        self.drawdown_card.value.setText(f"${snapshot.drawdown:,.2f}")
        if snapshot.shadow_running:
            self.shadow_card.value.setText(
                f"${snapshot.shadow_pnl:+,.2f} • {snapshot.shadow_position or 'cash'}"
            )
            set_widget_style(self.shadow_card.value, "color:#65b9ff")
        else:
            self.shadow_card.value.setText("OFF")
            set_widget_style(self.shadow_card.value, "color:#8fa4b8")
        self.shadow_button.setText("Stop simulation" if snapshot.shadow_running else "Simulate with live data")
        self.connect_button.setText("Disconnect" if snapshot.connected else "Connect Robinhood")
        self._update_visible_tables()
        self._update_chart(snapshot)
        refreshed = (
            snapshot.last_refresh.astimezone().strftime("%I:%M:%S %p") if snapshot.last_refresh else "never"
        )
        if not self.config.broker_connection_enabled:
            self.status.setText(
                f"RUNTIME {STRATEGY_NAMES.get(self.config.strategy_name, self.config.strategy_name)} • "
                "RESEARCH MODE • Broker capability is off • Local sandbox and CSV import only • No telemetry"
            )
        else:
            self.status.setText(
                f"Runtime {STRATEGY_NAMES.get(self.config.strategy_name, self.config.strategy_name)} • "
                f"{session} • Strategy {'RUNNING' if snapshot.strategy_running else 'STOPPED'} • "
                f"Shadow {'RUNNING — NO ORDERS' if snapshot.shadow_running else 'OFF'} • "
                f"Route {self.controller.active_execution_profile.label} • "
                f"Action {snapshot.pair_action_label} every {self.config.trade_seconds}s nominal • "
                f"Orders {snapshot.trades_today} • Last broker refresh {refreshed} • {snapshot.signal.reason}"
            )
        self.status.setToolTip(self.status.text())
        self.status.setAccessibleDescription(self.status.text())
        self.status.setText(
            f"{'Connected' if snapshot.connected else 'Disconnected'}  ·  "
            f"{'Strategy running' if snapshot.strategy_running else 'Strategy stopped'}  ·  "
            f"{'Shadow running — virtual only' if snapshot.shadow_running else 'Shadow off'}  ·  Updated {refreshed}"
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
        supervised_available = (
            live_enabled
            and self.config.market_hours == "regular_hours"
            and self.config.order_type == "market"
            and self.config.time_in_force == "gfd"
            and self.config.settlement_model == "cash_t1"
        )
        # The checklist is a task-focused workspace. Its rows and exact actions need the full
        # vertical canvas; broker state is already represented in the checklist and remains one
        # click away on every other tab.
        self.connect_button.setVisible(broker_enabled)
        self.shadow_button.setVisible(broker_enabled)
        session_available = evidence_ready or supervised_available
        self.authorize_button.setVisible(session_available)
        self.start_button.setVisible(session_available and live and not self._snapshot.strategy_running)
        self.kill_button.setVisible(broker_enabled and connected)
        sellable = any(p.symbol in {"TQQQ", "SQQQ"} and p.quantity > 0 for p in self._snapshot.positions)
        self.flatten_button.setText("Sell holdings…")
        self.flatten_button.setToolTip("Review selling tracked TQQQ/SQQQ holdings to return to cash.")
        self.flatten_button.setVisible(broker_enabled and connected and sellable)
        self.authority_controls.setVisible(self.controller.risk.grant is not None)
        self.mode_badge.setText(
            "TRADING ACTIVE" if self._snapshot.strategy_running
            else "SIMULATING" if shadow else "CONNECTED" if connected else "OFFLINE"
        )
        if not connected:
            title, detail = "Not connected", "Connect Robinhood to see your account. No trading is active."
        elif shadow:
            title, detail = "Simulation running", "Uses live market observations and virtual money. No real orders."
        elif self._snapshot.strategy_running:
            title, detail = "Strategy running", "Orders remain subject to approved limits, market hours and data checks."
        elif live and not self.controller.entry_window_open():
            title, detail = "Approved — market window closed", "Start manually during the permitted window before approval expires. No automatic start is scheduled."
        elif live:
            title, detail = "Approved — not started", "Start when account data and quotes are fresh. Stop remains available throughout a session."
        elif not funded:
            title, detail = "No buying power available", "You can research and simulate. Real orders require available broker buying power."
        else:
            title, detail = "No trading active", "Simulate an idea or review a bounded trading session. Approval does not guarantee a trade or a profit."
        self.session_state_title.setText(title)
        self.session_state_detail.setText(detail)
        set_widget_style(self.mode_badge,
            "background:#4b2516;color:#ffc07a;border:1px solid #9a5328;border-radius:7px;padding:7px 10px;font-weight:700"
            if supervised_available or evidence_ready
            else "background:#15324a;color:#8fd3ff;border:1px solid #3478a4;border-radius:7px;padding:7px 10px;font-weight:700")
        authorize_label = (
            "Review evidence-gated session"
            if evidence_ready
            else "Review live session"
        )
        self.authorize_button.setText(authorize_label)
        self.authorize_action.setText(authorize_label + "…")
        self.start_strategy_action.setText("Start Strategy")
        self.authorize_button.setEnabled(session_available and connected and funded and not shadow)
        self.start_button.setEnabled(
            session_available and live and not self._snapshot.strategy_running and not shadow
        )
        self.shadow_button.setEnabled(connected and (shadow or not live))
        self.kill_button.setEnabled(connected and not self._stop_cancel_busy)
        self.flatten_button.setEnabled(bool(self._snapshot.positions))
        self.fund_view_action.setVisible(self.config.personal_ledger_enabled)
        self.broker_connect_action.setEnabled(broker_enabled)
        self.broker_connect_action.setText("Disconnect Robinhood" if connected else "Connect Robinhood…")
        self.refresh_action.setEnabled(broker_enabled and connected)
        self.shadow_action.setEnabled(broker_enabled and connected and (shadow or not live))
        self.shadow_action.setText("Stop Live Shadow" if shadow else "Start Live Shadow")
        self.forget_credentials_action.setEnabled(broker_enabled)
        self.authorize_action.setEnabled(session_available and connected and funded and not shadow)
        self.start_strategy_action.setEnabled(
            session_available and live and not self._snapshot.strategy_running and not shadow
        )
        self.stop_cancel_action.setEnabled(connected and not self._stop_cancel_busy)
        self.flatten_action.setEnabled(connected and bool(self._snapshot.positions))
        safe_checks_available = (
            self.controller.risk.grant is None and not self._snapshot.strategy_running
        )
        self.activation_widget.safe_checks_button.setEnabled(safe_checks_available)
        self.activation_widget.safe_checks_button.setToolTip(
            "Refresh broker account and quote truth only. This cannot review, place, or cancel an order."
            if safe_checks_available
            else "Unavailable while a live grant or strategy is active. Revoke authority and stop first."
        )
        busy = self._connection_busy or self._connection_task is not None or self._close_requested or self._stop_cancel_busy
        if busy:
            for control in (
                self.connect_button, self.broker_connect_action, self.authorize_button,
                self.authorize_action, self.start_button, self.start_strategy_action,
                self.shadow_button, self.shadow_action, self.flatten_button, self.flatten_action,
                self.kill_button, self.stop_cancel_action, self.refresh_action,
                self.settings_button, self.settings_action, self.forget_credentials_action,
                self.activation_widget.safe_checks_button,
            ):
                control.setEnabled(False)
        self._apply_responsive_layout(self.width(), self.height())

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

    def _update_live_readiness(self) -> None:
        self.activation_widget.update_rows(self.controller.live_readiness())

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
                    set_item_foreground(item, QColor("#00e507" if pnl >= 0 else "#ff697d"))
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
            if self.tabs.currentWidget() is self.broker_panel:
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
        self.fund_total_label.setText(f"Confirmed capital contributions: ${total:,.2f}")

    def _on_event(self, severity: str, summary: str) -> None:
        self.activity_table.insertRow(0)
        now = datetime.now().strftime("%I:%M:%S %p")
        for column, value in enumerate((now, severity.upper(), summary)):
            item = QTableWidgetItem(value)
            if severity in {"error", "critical"}:
                set_item_foreground(item, QColor("#ff697d"))
            elif severity == "warning":
                set_item_foreground(item, QColor("#f2c14e"))
            elif severity == "market":
                set_item_foreground(item, QColor("#65b9ff"))
            self.activity_table.setItem(0, column, item)
        if self.activity_table.rowCount() > 500:
            self.activity_table.removeRow(500)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing_after_cleanup:
            self.agent_widget.shutdown()
            self.research_mcp_timer.stop()
            self.timer.stop()
            self.reconcile_timer.stop()
            if self._tray_icon is not None:
                self._tray_icon.hide()
                QApplication.instance().quit()
            event.accept()
            return
        if self._close_requested:
            event.ignore()
            return
        if not self._snapshot.connected and self._connection_task is None:
            # A disconnected window owns no broker transport. Close synchronously so
            # initial/offline use never creates an orphan asyncio task just to exit.
            self.timer.stop()
            self.reconcile_timer.stop()
            self.agent_widget.shutdown()
            self.research_mcp_timer.stop()
            self.controller.stop_for_exit()
            self._closing_after_cleanup = True
            if self._tray_icon is not None:
                self._tray_icon.hide()
                QApplication.instance().quit()
            event.accept()
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Close GRANDE Alpha")
        dialog.setText("What should happen to trading when this window closes?")
        dialog.setInformativeText(
            "Stopping blocks new app orders. Broker orders and filled holdings can remain; "
            "check Robinhood for their final state. Closing succeeds even if the broker is disconnected."
        )
        keep = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            keep = dialog.addButton("Keep running in tray", QMessageBox.ButtonRole.ActionRole)
        stop = dialog.addButton("Stop trading and exit", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if keep is not None and dialog.clickedButton() is keep:
            if self._tray_icon is None:
                self._tray_icon = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
                self._tray_icon.setToolTip("GRANDE Alpha — trading status")
                self._tray_icon.activated.connect(lambda _reason: self._restore_from_tray())
            QApplication.instance().setQuitOnLastWindowClosed(False)
            self._tray_icon.show()
            self._tray_icon.showMessage("GRANDE Alpha", "Still running. Open this icon to return.")
            self.hide()
            event.ignore()
            return
        if dialog.clickedButton() is not stop:
            event.ignore()
            return
        event.ignore()
        self._close_requested = True
        self._sync_data_timers()
        self._set_controls()
        self._start_task("shutdown", self._shutdown_then_close())

    def _restore_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    async def _shutdown_then_close(self) -> None:
        self.timer.stop()
        self.reconcile_timer.stop()
        try:
            self.controller.stop_for_exit()
        except Exception as exc:
            # Closing the process still stops local execution if journal I/O fails.
            self.status.setText(f"Exit journal unavailable: {exc}; check Robinhood directly")
        await self.tasks.shutdown()
        try:
            async with asyncio.timeout(5):
                await self.controller.broker.disconnect()
        except Exception:
            pass  # Transport cleanup cannot trap the user in the application.
        self._closing_after_cleanup = True
        self.close()

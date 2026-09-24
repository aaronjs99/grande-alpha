from __future__ import annotations

import asyncio
import html
import json
import math
import sys
from collections import Counter, deque
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.agent_ledger import AgentBudget
from grande_alpha.agent_models import AgentSettings, AgentSnapshot, parse_symbols
from grande_alpha.agent_sources import FEEDS, safe_url
from grande_alpha.models import utc_now
from grande_alpha.ui.agent_card import AgentCard
from grande_alpha.ui.chatgpt_setup import ChatGPTSetupDialog
from grande_alpha.ui.table_layout import configure_adjustable_columns
from grande_alpha.ui.themes import color, set_item_foreground, theme_css

PACIFIC_TIME = ZoneInfo("America/Los_Angeles")


def label(text: str) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


DASHBOARD_STYLE = """
QScrollArea { background: #f1f3f5; border: none; }
QWidget#agentCanvas { background: #f1f3f5; }
QDialog, QMessageBox { background: #f1f3f5; }
QComboBox QAbstractItemView { background: #ffffff; color: #27343e; selection-background-color: #e0f2eb; selection-color: #17242c; }
QToolTip { background: #ffffff; color: #27343e; border: 1px solid #d4dfe4; }
QWidget { color: #27343e; font-family: 'Inter', 'Arial'; font-size: 10pt; }
QLabel { background: transparent; border: none; }
QLabel#dashboardTitle { font-family: 'Menlo', 'Consolas', monospace; font-size: 23pt; font-weight: 800; color: #19252e; }
QLabel#eyebrow { color: #63747f; font-family: 'Menlo', 'Consolas', monospace; font-size: 9pt; letter-spacing: 2px; }
QLabel#modeBadge { color: #98651b; background: #fff7e6; border: 1px solid #eddfbc; border-radius: 6px; padding: 8px 12px; font-size: 9pt; font-weight: 700; }
QLabel#dashboardNotice { color: #677780; font-size: 9pt; }
QFrame#dashboardPanel { background: #ffffff; border: 1px solid #e1e6e9; border-radius: 8px; }
QLabel#metricValue { font-family: 'Menlo', 'Consolas', monospace; font-size: 28pt; font-weight: 700; color: #17242c; }
QLabel#metricCaption { color: #627580; font-size: 9pt; }
QLabel#sectionTitle { color: #7a878f; font-family: 'Menlo', 'Consolas', monospace; font-size: 10pt; font-weight: 600; letter-spacing: 2px; }
QLabel#chartValue { color: #1ca97a; font-family: 'Menlo', 'Consolas', monospace; font-size: 17pt; font-weight: 700; }
QPushButton { background: #ffffff; color: #425460; border: 1px solid #dce3e7; border-radius: 6px; padding: 9px 14px; font-size: 9pt; font-weight: 600; min-height: 18px; }
QPushButton:hover, QPushButton:checked { background: #e9f5f0; border-color: #87c9ae; }
QPushButton:disabled { background: #f0f2f4; color: #929da4; border-color: #e2e7eb; }
QPushButton#primary { background: #159d71; border-color: #159d71; color: white; }
QPushButton#primary:disabled { background: #deebe5; color: #78938a; border-color: #deebe5; }
QGroupBox { background: #ffffff; border: 1px solid #dce3e7; border-radius: 7px; margin-top: 16px; padding: 16px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; color: #526771; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #f8fafb; color: #27343e; border: 1px solid #d4dfe4; border-radius: 4px; padding: 7px; min-height: 20px; }
QTableWidget { background: #ffffff; alternate-background-color: #f8fafb; color: #43535f; border: none; gridline-color: #edf0f2; selection-background-color: #e0f2eb; selection-color: #17242c; font-family: 'Menlo', 'Consolas', monospace; font-size: 9pt; }
QTableWidget::item { padding: 8px 5px; border-bottom: 1px solid #f0f3f5; }
QHeaderView::section { background: #f8fafb; color: #657782; border: none; padding: 9px 5px; font-size: 8pt; }
QScrollBar:vertical { background: #edf1f3; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #bcc9cf; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #edf1f3; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: #bcc9cf; min-width: 30px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


class AgentWidget(QScrollArea):
    """Broker-backed observability with a plainly labeled proposal/execution boundary."""

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self._connected = False
        self._connection_busy = False
        self._last_cycle = 0
        self._display_session = ""
        self._last_event_id = 0
        self._highlighted = set()
        self._dashboard_snapshot = controller.agent.snapshot
        self._run_error = ""
        self._balance_account: str | None = None
        self._budget_account: str | None = None
        self._balance_times: deque[float] = deque(maxlen=500)
        self._balances: deque[float] = deque(maxlen=500)
        self._scan_task: asyncio.Task | None = None
        self._chatgpt_help: ChatGPTSetupDialog | None = None
        self.setWidgetResizable(True)
        self.setStyleSheet(theme_css(DASHBOARD_STYLE, base="light"))
        content = QWidget()
        content.setObjectName("agentCanvas")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        self.header_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        title = label("GRANDE / AGENT DESK")
        title.setObjectName("dashboardTitle")
        self.header_layout.addWidget(title, 1)
        self.clock_label = label("ELAPSED 00:00  ·  UPDATE 0")
        self.clock_label.setObjectName("eyebrow")
        self.header_layout.addWidget(self.clock_label)
        self.mode = label("IDLE · PROPOSALS ONLY")
        self.mode.setObjectName("modeBadge")
        self.header_layout.addWidget(self.mode)
        layout.addLayout(self.header_layout)
        notice = label("STOCKS + CRYPTO  /  Research + paper trading · simulations use virtual money · live execution is unavailable")
        notice.setObjectName("dashboardNotice")
        layout.addWidget(notice)

        self.metrics = QGridLayout()
        self.metrics.setSpacing(16)
        self.metric_cards = []
        self.balance = label("—")
        self.buying_power = label("—")
        self.candidates = label("0")
        self.proposals = label("0")
        self.total_pnl = label("—")
        self.fills_metric = label("0")
        self.win_rate = label("—")
        self.metric_details = []
        for index, (name, value, caption) in enumerate((
            ("CURRENT BALANCE", self.balance, "Broker account · no paper session yet"),
            ("TOTAL PAPER P&L", self.total_pnl, "Start a virtual session to track performance"),
            ("SIMULATED FILLS", self.fills_metric, "No virtual orders filled yet"),
            ("PAPER WIN RATE", self.win_rate, "Closed virtual trades only"),
        )):
            card, card_layout = self._panel()
            heading = label(name)
            heading.setObjectName("eyebrow")
            value.setObjectName("metricValue")
            detail = label(caption)
            detail.setObjectName("metricCaption")
            card_layout.addWidget(heading)
            card_layout.addWidget(value)
            card_layout.addWidget(detail)
            self.metric_cards.append(card)
            self.metric_details.append(detail)
            self.metrics.addWidget(card, index // 2, index % 2)
        layout.addLayout(self.metrics)
        self.crypto_funds = label("Crypto buying power · unavailable")
        self.crypto_funds.setObjectName("dashboardNotice")
        self.journal_status = label("Execution journal · No managed orders recorded")
        self.journal_status.setObjectName("dashboardNotice")
        self.budget_toggle = QPushButton("Plan stock + crypto cash limits")
        self.budget_toggle.setCheckable(True)
        self.budget_box = QGroupBox("Cash limits · saving does not authorize trades")
        self.budget_box.setVisible(False)
        self.budget_toggle.toggled.connect(self.budget_box.setVisible)
        budget_form = QFormLayout(self.budget_box)
        budget_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.budget_inputs = {}
        for name, title in (
            ("max_order_cash", "Cash per buy order"),
            ("max_committed_cash", "Combined committed cash"),
            ("max_daily_buy_cash", "Daily buy-attempt budget (Eastern time)"),
            ("max_realized_loss", "Cumulative realized loss budget"),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(0, 1000000)
            spin.setDecimals(2)
            spin.setPrefix("$")
            self.budget_inputs[name] = spin
            budget_form.addRow(title, spin)
        budget_form.addRow(label(
            "Limits cover managed stock and crypto orders together. Committed cash includes pending buys "
            "and the recorded purchase cost of holdings. It is not their current market value. Daily buy "
            "attempts reset at midnight Eastern; realized losses remain recorded across days and restarts. "
            "Zero limits block new buys. Changing limits never clears orders or losses."
        ))
        self.save_budget = QPushButton("Save cash limits · trading remains off")
        self.save_budget.clicked.connect(self._save_budget)
        budget_form.addRow(self.save_budget)

        self.configure = QPushButton("Configure universe and AI")
        self.configure.setCheckable(True)
        self.settings_box = QGroupBox("Discovery and analysis")
        self.settings_box.setVisible(False)
        self.configure.toggled.connect(self.settings_box.setVisible)
        form = QFormLayout(self.settings_box)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.equities = QLineEdit("QQQ, TQQQ, SQQQ")
        self.equities.setPlaceholderText("Stock and ETF symbols, comma separated")
        form.addRow("Stock/ETF watchlist", self.equities)
        self.crypto = QLineEdit()
        self.crypto.setPlaceholderText("Blank = discover all Robinhood USD pairs; or BTC, ETH…")
        form.addRow("Crypto universe", self.crypto)
        scan_row = QWidget()
        scan_layout = QHBoxLayout(scan_row)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        self.scans = QComboBox()
        self.scans.addItem("Watchlist only", "")
        self.scans.setMinimumWidth(0)
        self.load_scans = QPushButton("Load saved scans")
        self.load_scans.clicked.connect(self._schedule_scans)
        scan_layout.addWidget(self.scans, 1)
        scan_layout.addWidget(self.load_scans)
        form.addRow("Add equity scanner", scan_row)
        self.export_contracts = QPushButton("Export broker compatibility report")
        self.export_contracts.clicked.connect(self._export_contracts)
        self.export_contracts.setToolTip("Save tool definitions without account data or order calls")
        form.addRow(self.export_contracts)
        self.interval = QSpinBox()
        self.interval.setRange(5, 300)
        self.interval.setValue(5)
        self.interval.setSuffix(" seconds (target)")
        form.addRow("Quote checks", self.interval)
        self.local_ai = QCheckBox("Use a local Ollama model for analysis")
        self.model = QLineEdit()
        self.model.setPlaceholderText("Installed model name")
        self.model.setEnabled(False)
        self.local_ai.toggled.connect(self.model.setEnabled)
        form.addRow(self.local_ai)
        form.addRow("Local model", self.model)
        form.addRow(
            label(
                "Optional AI sends symbols, numeric prices and enabled source excerpts to Ollama on this computer. "
                "No broker credentials or account balances are sent. Only use a locally installed model; "
                "your Ollama configuration controls any further processing. With AI off, the labeled "
                "rules baseline measures observed price changes. Neither mode is a validated strategy."
            )
        )

        self.save_setup = QPushButton("Save research setup")
        self.save_setup.clicked.connect(self._save_settings)
        form.addRow(self.save_setup)

        self.prompts_toggle = QPushButton("Prompts + AI connections")
        self.prompts_toggle.setCheckable(True)
        self.prompt_box = QGroupBox("Prompts + AI connections")
        self.prompt_box.setVisible(False)
        self.prompts_toggle.toggled.connect(self.prompt_box.setVisible)
        prompt_form = QFormLayout(self.prompt_box)
        prompt_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.briefs = {}
        for key, title, placeholder in (
            ("team", "Team brief", "Compare observations, account for spreads, explain uncertainty"),
            ("equity", "Equities worker", "Focus on stock momentum and quote quality"),
            ("crypto", "Crypto worker", "Focus on crypto volatility and spread changes"),
        ):
            editor = QLineEdit()
            editor.setMaxLength(2000)
            editor.setPlaceholderText(placeholder)
            self.briefs[key] = editor
            prompt_form.addRow(title, editor)
        self.apply_briefs = QPushButton("Apply prompts · next cycle")
        self.apply_briefs.clicked.connect(self._apply_briefs)
        prompt_form.addRow(self.apply_briefs)
        prompt_form.addRow(label(
            "Prompts guide local Ollama when enabled. With Rules baseline, they are context for a connected "
            "AI app and do not change the rules. Prompts cannot change trading permissions or risk limits. "
            "Do not include secrets; applied prompts are recorded locally with research cycles."
        ))
        prompt_form.addRow(label(
            "A connected AI app can read symbols, price observations and prompts, change research "
            "prompts/universes, start virtual paper sessions, and read simulated fills and P&L. "
            "Its provider may process that shared data. Real account balances, credentials, positions "
            "and order tools are excluded. Access starts off and "
            "ends on Stop agent, STOP + CANCEL, Disconnect or Exit."
        ))
        self.mcp_enabled = QCheckBox("Allow AI research access for this app session")
        self.mcp_enabled.toggled.connect(self._toggle_mcp)
        prompt_form.addRow(self.mcp_enabled)
        self.copy_mcp = QPushButton("Other AI apps (advanced): copy connection settings")
        self.copy_mcp.clicked.connect(self._copy_mcp_config)
        self.copy_mcp.setEnabled(not getattr(sys, "frozen", False))
        self.chatgpt_setup = QPushButton("ChatGPT Astra setup")
        self.chatgpt_setup.setToolTip("Connect in three guided steps")
        self.chatgpt_setup.clicked.connect(self._show_chatgpt_setup)
        prompt_form.addRow(self.chatgpt_setup)
        prompt_form.addRow(self.copy_mcp)
        self.mcp_status = label("AI research access is off · start with ChatGPT Astra setup")
        prompt_form.addRow(self.mcp_status)

        self._build_paper_panel()
        self.paper_toggle = QPushButton("Session setup")
        self.paper_toggle.setCheckable(True)
        self.paper_toggle.toggled.connect(self.paper_box.setVisible)
        controls = QHBoxLayout()
        self.start = QPushButton("Start agent analysis")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self._start)
        self.stop = QPushButton("Stop agent")
        self.stop.clicked.connect(lambda: controller.stop_agent())
        controls.addWidget(self.paper_start)
        controls.addWidget(self.stop)
        controls.addWidget(self.paper_toggle)
        controls.addWidget(self.prompts_toggle)
        layout.addLayout(controls)
        self.run_status = label("Choose a price source in Session setup to begin.")
        self.run_status.setObjectName("modeBadge")
        self.run_detail = label("")
        self.run_detail.setObjectName("metricCaption")
        layout.addWidget(self.run_status)
        layout.addWidget(self.run_detail)
        layout.addWidget(self.paper_box)
        layout.addWidget(self.prompt_box)

        self.scout_status = label("Waiting for discovery")
        self.analyst_status = label("Rules baseline")
        self.risk_status = label("Waiting for observations")
        self.execution_status = label("Live execution unavailable")
        self.equity_status = label("Waiting for stock observations")
        self.crypto_status = label("Waiting for crypto observations")
        self.market_status = label("Connect Robinhood, choose your universe, then start analysis.")

        self.observations_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.observations_layout.setSpacing(18)
        chart_panel, chart_layout = self._panel()
        chart_title = label("// BALANCE HISTORY")
        chart_title.setObjectName("sectionTitle")
        chart_layout.addWidget(chart_title)
        self.chart_value = label("Awaiting account data")
        self.chart_value.setObjectName("chartValue")
        chart_layout.addWidget(self.chart_value)
        self.chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        self.chart.setBackground("#ffffff")
        self.chart.setMinimumSize(0, 200)
        self.chart.setMaximumHeight(260)
        self.chart.setMouseEnabled(x=False, y=False)
        self.chart.setMenuEnabled(False)
        self.chart.hideButtons()
        self.chart.getAxis("left").enableAutoSIPrefix(False)
        self.chart.getAxis("left").setWidth(76)
        self.chart.showGrid(x=False, y=True, alpha=0.08)
        for name in ("left", "bottom"):
            axis = self.chart.getAxis(name)
            axis.setPen(pg.mkPen("#e6ecef"))
            axis.setTextPen(pg.mkPen("#657782"))
        self.curve = self.chart.plot(pen=pg.mkPen("#1ca97a", width=2), brush=pg.mkBrush(28, 169, 122, 18))
        chart_layout.addWidget(self.chart)
        self.chart_detail = label("Broker account value · UTC · includes deposits and withdrawals; not trading P&L")
        self.chart_detail.setObjectName("metricCaption")
        chart_layout.addWidget(self.chart_detail)
        activity_panel, activity_layout = self._panel()
        activity_title = label("// ACTIVITY LOG")
        activity_title.setObjectName("sectionTitle")
        activity_layout.addWidget(activity_title)
        self.activity = self._table(["Time (Pacific)", "Update", "Activity"])
        self.activity.setMinimumHeight(245)
        self.activity.setMaximumHeight(290)
        self.activity.setColumnWidth(0, 170)
        self.activity.setColumnWidth(1, 48)
        self.activity.setColumnWidth(2, 360)
        self.activity.setWordWrap(True)
        activity_layout.addWidget(self.activity)
        self.activity_hint = label("Waiting for the first completed analysis cycle. Full details appear in Receipts.")
        self.activity_hint.setObjectName("metricCaption")
        activity_layout.addWidget(self.activity_hint)
        self.observations_layout.addWidget(chart_panel, 1)
        self.observations_layout.addWidget(activity_panel, 1)
        layout.addLayout(self.observations_layout)

        status_panel, status_layout = self._panel()
        status_title = label("// AGENT COMMS")
        status_title.setObjectName("sectionTitle")
        status_layout.addWidget(status_title)
        self.comms = label("Start a session to see worker handoffs.")
        status_layout.addWidget(self.comms)
        layout.addWidget(status_panel)
        self.stages = QGridLayout()
        self.stages.setSpacing(12)
        self.stage_cards = []
        self.team_cards = {}
        for index, (name, role, accent, status) in enumerate((
            ("NOVA", "STOCKS", "#24b6c4", self.equity_status),
            ("ORIN", "CRYPTO", "#51b18a", self.crypto_status),
            ("VELA", "ANALYSIS", "#c4a340", self.analyst_status),
            ("KADE", "DATA CHECKS", "#d6864f", self.risk_status),
            ("RUNE", "PAPER FILLS", "#be72a2", self.execution_status),
            ("ZARA", "PORTFOLIO", "#9273c8", self.scout_status),
        )):
            card, card_layout = self._panel(accent)
            number = label(f"{index + 1:02d}  /  {role}")
            number.setObjectName("metricCaption")
            card_layout.addWidget(number)
            card_layout.addWidget(card.avatar, 0, Qt.AlignmentFlag.AlignHCenter)
            heading = label(name)
            heading.setObjectName("eyebrow")
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_layout.addWidget(heading)
            status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            status.setObjectName("metricCaption")
            status.setFixedHeight(44)
            card_layout.addWidget(status)
            card_layout.addStretch()
            self.stage_cards.append(card)
            self.team_cards[name] = (card, status, accent)
            self.stages.addWidget(card, index // 3, index % 3)
        layout.addLayout(self.stages)
        detail = label("NOVA and ORIN scan concurrently. VELA analyzes, KADE checks data, RUNE simulates fills, and ZARA tracks the portfolio. VELA uses rules or your configured local model.")
        detail.setObjectName("metricCaption")
        layout.addWidget(detail)
        paper_details = QGroupBox("Paper positions and fill history")
        paper_details_layout = QVBoxLayout(paper_details)
        paper_details_layout.addWidget(self.paper_status)
        paper_details_layout.addWidget(self.paper_summary)
        self.paper_evaluation = label("No paper performance observations yet.")
        paper_details_layout.addWidget(self.paper_evaluation)
        paper_details_layout.addWidget(self.paper_positions)
        paper_details_layout.addWidget(self.paper_fills)
        layout.addWidget(paper_details)

        sources_box = QGroupBox("News + trends · sources behind the analysis")
        sources_layout = QVBoxLayout(sources_box)
        self.sources_status = label("News research is off. Enable it in Session setup.")
        sources_layout.addWidget(self.sources_status)
        self.sources_table = self._table(["Source", "Published (UTC)", "Headline"])
        self.sources_table.setMinimumHeight(160)
        self.sources_table.setMaximumHeight(260)
        self.sources_table.setColumnWidth(0, 155)
        self.sources_table.setColumnWidth(1, 150)
        self.sources_table.setColumnWidth(2, 670)
        self.sources_table.cellDoubleClicked.connect(self._open_source)
        sources_layout.addWidget(self.sources_table)
        sources_layout.addWidget(label(
            "Double-click a headline to open its source. News checks require recent ticker-specific "
            "coverage from two publishers and no flagged risk words before a new buy. This experimental "
            "filter can miss context; it is not a profitability signal. Local AI can analyze the excerpts "
            "when enabled. Social posts are unverified context and never count as news confirmation."
        ))
        layout.addWidget(sources_box)
        self._sources_render_key = None

        candidates_panel, candidates_layout = self._panel()
        heading = label("// MARKET OBSERVATIONS")
        heading.setObjectName("sectionTitle")
        candidates_layout.addWidget(heading)
        self.table = self._table(["Market", "Symbol", "Midpoint", "Proposal", "Data checks", "Reason"])
        self.table.setMinimumHeight(210)
        self.table.setMaximumHeight(340)
        candidates_layout.addWidget(self.table)
        layout.addWidget(candidates_panel)
        layout.addWidget(self.market_status)
        layout.addWidget(self.crypto_funds)
        layout.addWidget(self.journal_status)
        self.tools_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.tools_layout.addWidget(self.configure)
        self.tools_layout.addWidget(self.budget_toggle)
        self.tools_layout.addWidget(self.start)
        layout.addLayout(self.tools_layout)
        layout.addWidget(self.settings_box)
        layout.addWidget(self.budget_box)
        self.setWidget(content)
        controller.agent_changed.connect(self.update_agent)
        controller.agent_mcp_changed.connect(self._mcp_changed)
        controller.agent_settings_changed.connect(self._sync_agent_settings)
        self.update_agent(controller.agent.snapshot)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()

    def _build_paper_panel(self) -> None:
        box = QGroupBox("Paper trading · virtual money")
        self.paper_box = box
        box.setVisible(False)
        form = QFormLayout(box)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.paper_source = QComboBox()
        self.paper_source.addItem("Offline demo · made-up prices · about 24 seconds", "demo")
        self.paper_source.addItem("Robinhood quotes · continuous monitoring · virtual money", "broker_quotes")
        self.paper_source.setCurrentIndex(1)
        self.paper_source.setMinimumWidth(0)
        self.paper_source.currentIndexChanged.connect(lambda: self._set_controls())
        form.addRow("Price source", self.paper_source)
        self.news_enabled = QCheckBox("Read news and apply headline checks to new buys")
        self.social_enabled = QCheckBox("Include public social context · Bluesky, when available")
        self.news_enabled.toggled.connect(self._news_toggled)
        form.addRow(self.news_enabled)
        form.addRow(self.social_enabled)
        form.addRow(label(
            "Reads BBC Business, CNBC, CoinDesk and Federal Reserve public feeds every 10 minutes. "
            "Optional social search sends up to eight watchlist symbols to Bluesky. No paid keys are "
            "required by this setup; inaccessible sources are reported. The offline demo skips all feeds."
        ))
        self.repeat_demo = QCheckBox("Repeat offline demo until I press Stop")
        form.addRow(self.repeat_demo)
        self.paper_cash = QDoubleSpinBox()
        self.paper_cash.setRange(1, 1000000)
        self.paper_cash.setPrefix("$")
        self.paper_cash.setValue(1000)
        self.paper_trade_cash = QDoubleSpinBox()
        self.paper_trade_cash.setRange(1, 1000000)
        self.paper_trade_cash.setPrefix("$")
        self.paper_trade_cash.setValue(100)
        form.addRow("Starting virtual cash", self.paper_cash)
        form.addRow("Virtual cash per buy", self.paper_trade_cash)
        self.paper_start = QPushButton("Start new paper session")
        self.paper_start.setObjectName("primary")
        self.paper_start.clicked.connect(self._start_paper)
        form.addRow(label(
            "Demo runs both workers on a fixed up/down price path without Robinhood or AI calls. "
            "Robinhood paper trading runs until Stop, targeting quote checks every 5 seconds by default. "
            "News and optional AI run in the background; provider response times can slow checks. "
            "It monitors your configured/discovered candidates, prioritizes open positions, and may produce only HOLD. "
            "Each new session starts a fresh virtual "
            "portfolio; older sessions stay archived locally. Stop agent ends either run."
        ))
        self.paper_status = label("No paper session yet · no real funds used")
        self.paper_summary = label("Virtual cash and profit/loss will appear here.")
        self.paper_positions = self._table(["Virtual holding", "Units", "Value at last bid", "Unrealized P&L", "Quote"])
        self.paper_positions.setMaximumHeight(155)
        self.paper_fills = self._table(["Simulated time", "Symbol", "Side", "Units", "Fill price", "Realized P&L"])
        self.paper_fills.setMaximumHeight(210)
        for table, widths in ((self.paper_positions, [200, 110, 160, 170, 140]),
                              (self.paper_fills, [130, 190, 70, 110, 120, 140])):
            table.setProperty("grandeDefaultColumnWidths", widths)
            for column, width in enumerate(widths):
                table.setColumnWidth(column, width)
        form.addRow(label(
            "Fill model: next eligible quote, buys at ask and sells at bid, plus 0.05% adverse slippage. "
            "Fractional units, immediate settlement, no fees or liquidity constraints. "
            "P&L uses the last eligible bid; old valuations are labeled. Demo results do not predict returns."
        ))

    def _start_paper(self) -> None:
        self._run_error = ""
        try:
            self.controller.start_agent_paper(self._read_settings(), self.paper_source.currentData(),
                                              self.paper_cash.value(), self.paper_trade_cash.value(),
                                              self.repeat_demo.isChecked() and self.paper_source.currentData() == "demo")
        except Exception as exc:
            self._run_error = f"Paper session did not start: {exc}"
            self.paper_status.setText(self._run_error)
        self._update_run_status()

    def _news_toggled(self, enabled: bool) -> None:
        if not enabled:
            self.social_enabled.setChecked(False)
        self._set_controls()

    def _open_source(self, row: int, _column: int) -> None:
        item = self.sources_table.item(row, 2)
        if item:
            try:
                url = safe_url(item.data(Qt.ItemDataRole.UserRole), tuple(h for f in FEEDS for h in f.hosts) + ("bsky.app",))
                QDesktopServices.openUrl(QUrl(url))
            except (ValueError, TypeError):
                self.sources_status.setText("Source link is unavailable.")

    def _update_sources(self, snapshot: AgentSnapshot) -> None:
        report = snapshot.research_sources
        signature = (json.dumps(report, sort_keys=True), self.controller.agent.paper_source, snapshot.sources_loading)
        if signature == self._sources_render_key:
            return
        self._sources_render_key = signature
        items = (report or {}).get("items", [])[:60]
        if not report:
            self.sources_status.setText("Loading news sources; quote checks continue." if snapshot.sources_loading else
                                        "Offline demo skips external feeds." if self.controller.agent.paper_source == "demo"
                                        else "News research is off or has not fetched yet. Enable it in Session setup.")
        else:
            health = " · ".join(f"{s['source']}: {s['status']} ({s['fresh_items']})" for s in report["sources"])
            self.sources_status.setText(f"Last source check {report['refreshed_at']} · publication window 48 hours\n{health}"
                                       + ("\nRefreshing in the background; quote checks continue." if snapshot.sources_loading else ""))
        self.sources_table.setRowCount(len(items))
        for row, source in enumerate(items):
            for column, value in enumerate((source["source"], source["published_at"][:16].replace("T", " "), source["title"])):
                item = QTableWidgetItem(value)
                tooltip = f"{source['excerpt']}\n{source['url']}\nFirst seen: {source['first_seen_at']}\nSource ID: {source['id']}"
                item.setToolTip("<qt>" + html.escape(tooltip).replace("\n", "<br>") + "</qt>")
                item.setData(Qt.ItemDataRole.UserRole, source["url"])
                self.sources_table.setItem(row, column, item)

    def _update_paper(self, paper: dict | None) -> None:
        self.paper_positions.setVisible(bool(paper and paper["positions"]))
        self.paper_fills.setVisible(bool(paper))
        if not paper:
            return
        source = "SYNTHETIC DEMO" if paper["source"] == "demo" else "ROBINHOOD QUOTES"
        self.balance.setText(f"${float(paper['equity']):,.2f}")
        self.total_pnl.setText(f"${float(paper['total_pnl']):+,.2f}")
        self.total_pnl.setStyleSheet(f"color: {color('#16845e' if float(paper['total_pnl']) >= 0 else '#b74b63', base='light')};")
        self.fills_metric.setText(str(paper["fill_count"]))
        rate = paper.get("win_rate")
        self.win_rate.setText(f"{rate:.1f}%" if rate is not None else "—")
        self.metric_details[0].setText(f"Virtual portfolio · started with ${float(paper['initial_cash']):,.2f}")
        self.metric_details[1].setText(f"{float(paper.get('return_pct', 0)):+.2f}% · realized + unrealized")
        self.metric_details[2].setText(f"{paper.get('closed_trades', 0)} closed trades · {paper['pending_count']} pending")
        self.metric_details[3].setText(f"{paper.get('wins', 0)} wins / {paper.get('losses', 0)} losses / {paper.get('breakeven', 0)} flat")
        history = paper.get("equity_history", [])
        times = [datetime.fromisoformat(point["at"]).timestamp() for point in history]
        values = [float(point["equity"]) for point in history]
        if values:
            self.curve.setFillLevel(min(values) - max(0.01, (max(values) - min(values)) * 0.1))
        self.curve.setData(times, values, symbol="o" if len(values) == 1 else None,
                           symbolSize=5, symbolBrush=color("#1ca97a", base="light"))
        self.chart_value.setText(f"${float(paper['equity']):,.2f} · virtual portfolio")
        old_marks = any(p["stale"] for p in paper["positions"])
        self.chart_detail.setText(
            f"{source} · {'running' if paper['active'] else 'saved session'} · "
            f"{'simulated clock' if paper['source'] == 'demo' else 'market clock'} (UTC) · "
            f"last {len(history)} observations · last eligible bid valuations"
            + (" · includes old marks" if old_marks else "")
        )
        self.paper_status.setText(f"{source} · {'RUNNING' if paper['active'] else 'STOPPED'} · "
                                 f"{paper['fill_count']} simulated fills · {paper['pending_count']} pending · virtual funds only")
        self.paper_summary.setText(
            f"Virtual cash ${float(paper['cash']):,.2f}  ·  Portfolio at last bids ${float(paper['equity']):,.2f}\n"
            f"Total P&L ${float(paper['total_pnl']):+,.2f}  ·  Realized ${float(paper['realized_pnl']):+,.2f}"
            f"  ·  Unrealized ${float(paper['unrealized_pnl']):+,.2f}"
        )
        expectancy = f"${float(paper['expectancy']):+,.2f}" if paper.get("expectancy") is not None else "—"
        factor = f"{float(paper['profit_factor']):.2f}" if paper.get("profit_factor") is not None else "— (no losses yet)"
        self.paper_evaluation.setText(
            f"Average closed trade {expectancy} · Profit factor {factor} · "
            f"Max observed drawdown {float(paper.get('max_drawdown_pct', 0)):.2f}%"
            + (" (partial older history)" if not paper.get("drawdown_complete", True) else "")
            + f"\n{paper.get('evaluation', '')} · Policy: {paper.get('strategy', {}).get('policy', 'price-only')}"
        )
        self.paper_positions.setRowCount(len(paper["positions"]))
        for row, p in enumerate(paper["positions"]):
            values = (p["key"], f"{float(p['quantity']):.8g}", f"${float(p['value']):,.2f}",
                      f"${float(p['unrealized_pnl']):+,.2f}", "Old / last known" if p["stale"] or not paper["active"] else "At latest cycle")
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(p["marked_at"] if col == 4 else value)
                self.paper_positions.setItem(row, col, item)
        fills = list(reversed(paper["fills"][-30:]))
        self.paper_fills.setRowCount(len(fills))
        for row, fill in enumerate(fills):
            values = (fill["filled_at"][11:19] + " UTC", fill["key"].split(":", 1)[-1], fill["side"].upper(),
                      f"{float(fill['quantity']):.8g}", f"${float(fill['price']):,.6g}",
                      f"${float(fill['realized_pnl']):+,.2f}" if fill["side"] == "sell" else "—")
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(fill["filled_at"] if col == 0 else value)
                self.paper_fills.setItem(row, col, item)

    def _update_clock(self) -> None:
        snapshot = self._dashboard_snapshot
        elapsed = snapshot.elapsed_seconds
        if snapshot.running and snapshot.started_at:
            elapsed = max(0, (utc_now() - snapshot.started_at).total_seconds())
        minutes, seconds = divmod(int(elapsed), 60)
        self.clock_label.setText(f"ELAPSED {minutes:02d}:{seconds:02d}  ·  UPDATE {snapshot.cycle}")
        self._update_run_status()

    def _update_run_status(self) -> None:
        snapshot = self._dashboard_snapshot
        detail = ""
        if self._run_error:
            text = self._run_error
        elif self.controller.shadow_only_runtime:
            text = "Scheduled shadow does not run the stock/crypto agent. Open a normal GRANDE session."
        elif snapshot.error:
            text = snapshot.error
        elif not snapshot.running:
            if self.paper_source.currentData() == "broker_quotes":
                text = ("Ready for continuous paper trading. Click Start continuous paper trading; funds stay virtual."
                        if self._connected else
                        "Connect Robinhood to start paper trading with current quotes. No real orders will be placed.")
            elif snapshot.phase == "Demo complete":
                text = "Offline demo complete. It repeats fixed prices; select Robinhood quotes in Session setup for current data."
            else:
                text = "Offline demo selected. Click Start offline demo, or choose Robinhood quotes in Session setup."
        else:
            mode = "Offline demo" if self.controller.agent.paper_source == "demo" else (
                "Continuous paper trading · Robinhood quotes" if self.controller.agent.paper_source else "Research only")
            prefix = f"{mode} · {snapshot.analyst}"
            if snapshot.phase == "Starting":
                text = prefix + " · Starting workers…"
            elif snapshot.phase == "Working":
                elapsed = max(0, int((utc_now() - snapshot.cycle_started_at).total_seconds())) if snapshot.cycle_started_at else 0
                text = prefix + f" · Checking quotes · update {snapshot.cycle} · {elapsed}s elapsed"
                detail = ("Reading news sources…" if snapshot.team_status.get("VELA") == "Reading news sources" else
                          " · ".join(f"{name}: {snapshot.worker_status.get(key, 'Queued')}"
                                     for key, name in (("equity", "Stocks"), ("crypto", "Crypto"))))
                waiting = any(s in {"Loading stock scan", "Loading crypto pairs", "Requesting quotes"}
                              for s in snapshot.worker_status.values())
                if elapsed >= 10 and waiting:
                    detail += "\nRobinhood data is delayed. Slow reads will time out and monitoring will try again."
                if elapsed >= 40:
                    detail += "\nThis update is overdue. If it does not resume, press Stop agent, then reconnect Robinhood."
            else:
                signals = Counter(d.action for d in snapshot.decisions)
                paper = snapshot.paper if self.controller.agent.paper_source else None
                fills = f" · {paper['fill_count']} simulated fills · {paper['pending_count']} pending" if paper else ""
                remaining = max(0, math.ceil((snapshot.next_cycle_at - utc_now()).total_seconds())) if snapshot.next_cycle_at else None
                wait = f" · Next quote check in {remaining}s" if remaining is not None else ""
                text = (f"{prefix} · Update {snapshot.cycle} · {signals['buy']} BUY / {signals['exit']} EXIT / "
                        f"{signals['hold']} HOLD{fills}{wait}")
                held = {p['key'] for p in paper['positions']} if paper else set()
                settings_ai = self.controller.agent.settings.local_ai_enabled

                def signal_reason(item):
                    if item.risk_status != "Data checks passed":
                        return item.reason
                    if item.action == "exit":
                        return ("EXIT signal; no virtual holding to sell" if paper and item.instrument.key not in held else
                                "EXIT signal; waiting for a later eligible quote" if paper else "EXIT proposal; research does not simulate fills")
                    if item.action == "buy" and paper and item.instrument.key in held:
                        return "Already holding; additional buys are disabled"
                    if not item.buy_allowed and item.source_context:
                        return "News blocks new buys: " + (", ".join(item.source_context['risk_terms']) or item.source_context['coverage'])
                    if item.action == "buy":
                        if paper and Decimal(paper['cash']) < Decimal(paper['trade_cash']):
                            return "Insufficient virtual cash for another buy"
                        return "BUY signal; waiting for a later eligible quote" if paper else "BUY proposal; research does not simulate fills"
                    return "HOLD: " + item.reason.split(" · Sources:", 1)[0][:160] if settings_ai else "HOLD: no entry or exit signal"

                reasons = []
                for key, name in (("equity", "Stocks"), ("crypto", "Crypto")):
                    status = snapshot.market_status.get(key, "")
                    items = [d for d in snapshot.decisions if d.instrument.asset_class.value == key]
                    if status.startswith("Unavailable"):
                        reason = status
                    elif not items:
                        reason = "No candidates returned. Check the watchlist and available broker data."
                    else:
                        counts = Counter(signal_reason(d) for d in items)
                        reason = "; ".join(f"{count}/{len(items)} {message}" for message, count in counts.most_common(2))
                    reasons.append(f"{name}: {reason}")
                detail = "\n".join(reasons)
            if snapshot.sources_loading:
                detail += "\nNews refreshing in the background."
            if snapshot.analysis_status:
                detail += "\nAI · " + " · ".join(f"{key}: {value}" for key, value in snapshot.analysis_status.items())
        self.run_status.setText(text)
        self.run_detail.setText(detail)
        self.run_detail.setVisible(bool(detail))

    def _add_activity(self, at: datetime, cycle: str, summary: str, kind: str) -> None:
        pacific = at.astimezone(PACIFIC_TIME)
        self.activity.insertRow(0)
        for column, value in enumerate((pacific.strftime("%I:%M:%S %p %Z"), cycle, summary)):
            item = QTableWidgetItem(value)
            item.setToolTip(pacific.strftime("%Y-%m-%d %I:%M:%S %p %Z (UTC%z)") if column == 0 else value)
            if column == 2:
                shade = {"RISK": "#a77924", "WARN": "#b74b63", "FILL": "#16845e",
                         "IDEA": "#16845e", "BOOK": "#9273c8"}.get(kind, "#566e7c")
                set_item_foreground(item, shade, base="light")
            self.activity.setItem(0, column, item)
        if self.activity.rowCount() > 200:
            self.activity.removeRow(200)

    def _update_handoffs(self, snapshot: AgentSnapshot) -> None:
        handoffs = set()
        session = snapshot.session_id or (snapshot.paper or {}).get("session_id", "")
        if session != self._display_session:
            self._display_session = session
            self._last_cycle = self._last_event_id = 0
            self.activity.setRowCount(0)
            for card in self.stage_cards:
                card.stop_motion()
            self.comms.setText("Waiting for worker handoffs.")
            # Persisted fills remain inspectable after reopening; do not invent handoffs.
            if not snapshot.team_events and not snapshot.running and snapshot.paper:
                for fill in snapshot.paper["fills"]:
                    self._add_activity(datetime.fromisoformat(fill["filled_at"]), "—",
                                       f"[FILL] Saved PAPER {fill['side'].upper()} {fill['key']} @ ${float(fill['price']):,.6g}", "FILL")
        for event in snapshot.team_events:
            if event["id"] <= self._last_event_id:
                continue
            self._last_event_id = event["id"]
            handoffs.update((event["from"], *event["to"].split(" + ")))
            summary = f"[{event['kind']}] {event['from']} → {event['to']} · {event['message']}"
            self._add_activity(datetime.fromisoformat(event["at"]), str(event["cycle"]), summary, event["kind"])
        latest = snapshot.team_events[-1] if snapshot.team_events else None
        self._highlighted = set()
        if latest:
            self.comms.setText(f"{latest['from']} → {latest['to']}  ·  {latest['message']}" +
                               ("  ·  last handoff, session stopped" if not snapshot.running else ""))
            if snapshot.running:
                self._highlighted = {latest["from"], *latest["to"].split(" + ")}
        for name, (card, status, _accent) in self.team_cards.items():
            if name in snapshot.team_status:
                status.setText(snapshot.team_status[name])
            ai_working = any(s.startswith("Analyzing") for s in snapshot.analysis_status.values())
            if name == "VELA" and snapshot.analysis_status:
                status.setText("AI analyzing · quotes continue" if ai_working else "Waiting for eligible quotes")
            status.setToolTip(status.text())
            if not snapshot.running:
                card.stop_motion()
            else:
                stage = snapshot.team_status.get(name, "")
                working = snapshot.phase == "Working" and (
                    stage in {"Scanning", "Loading stock scan", "Loading crypto pairs", "Requesting quotes",
                              "Analyzing", "Checking data", "Checking quotes and limits", "Reading news sources"}
                    or name == "VELA" and "Analyzing" in snapshot.worker_status.values()
                )
                working = working or name == "VELA" and ai_working
                card.set_activity(working, handoff=name in handoffs)

    def apply_theme(self) -> None:
        self.setStyleSheet(theme_css(DASHBOARD_STYLE, base="light"))
        self.chart.setBackground(color("#ffffff", base="light"))
        for name in ("left", "bottom"):
            axis = self.chart.getAxis(name)
            axis.setPen(pg.mkPen(color("#e6ecef", base="light")))
            axis.setTextPen(pg.mkPen(color("#657782", base="light")))
        accent = QColor(color("#1ca97a", base="light"))
        self.curve.setPen(pg.mkPen(accent, width=2))
        self.curve.setSymbolBrush(accent)
        accent.setAlpha(22)
        self.curve.setBrush(pg.mkBrush(accent))
        for card in self.stage_cards:
            card.update()
            card.avatar.update()
        self._update_paper(self._dashboard_snapshot.paper)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "metrics"):
            return
        wide = self.viewport().width() >= 1100
        self.header_layout.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
        self.tools_layout.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
        stage_columns = 6 if wide else (3 if self.viewport().width() >= 700 else 2)
        for column in range(6):
            self.stages.setColumnStretch(column, 1 if column < stage_columns else 0)
        for card in self.stage_cards:
            self.stages.removeWidget(card)
        for index, card in enumerate(self.stage_cards):
            self.stages.addWidget(card, index // stage_columns, index % stage_columns)
        columns = 4 if wide else 2
        for card in self.metric_cards:
            self.metrics.removeWidget(card)
        for index, card in enumerate(self.metric_cards):
            self.metrics.addWidget(card, index // columns, index % columns)
        self.observations_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        )

    @staticmethod
    def _panel(accent: str | None = None):
        panel = AgentCard(accent) if accent else QFrame()
        if not accent:
            panel.setObjectName("dashboardPanel")
        panel.setMinimumWidth(0)
        box = QVBoxLayout(panel)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)
        return panel, box

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(40)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        configure_adjustable_columns(table, headers)
        return table

    def _read_settings(self) -> AgentSettings:
        return AgentSettings(
            equity_symbols=parse_symbols(self.equities.text()),
            crypto_symbols=parse_symbols(self.crypto.text()),
            scan_id=str(self.scans.currentData() or ""),
            interval_seconds=self.interval.value(),
            local_ai_model=self.model.text().strip(), local_ai_enabled=self.local_ai.isChecked(),
            research_brief=self.briefs["team"].text(), equity_brief=self.briefs["equity"].text(),
            crypto_brief=self.briefs["crypto"].text(),
            news_enabled=self.news_enabled.isChecked(), social_enabled=self.social_enabled.isChecked(),
        )

    def _save_settings(self) -> None:
        try:
            settings = self._read_settings()
            settings.validate()
            if self.controller.agent.snapshot.running:
                raise ValueError("Stop research before changing its setup")
            self.controller.agent.settings = settings
            self.controller.agent_settings_changed.emit()
            self.market_status.setText("Research setup saved for this app session. Analysis starts only when requested.")
        except (ValueError, RuntimeError) as exc:
            self.market_status.setText(str(exc))

    def _start(self) -> None:
        self._run_error = ""
        try:
            self.controller.start_agent(self._read_settings())
        except Exception as exc:
            self._run_error = f"Research did not start: {exc}"
            QMessageBox.warning(self, "Agent could not start", str(exc))
        self._update_run_status()

    def _sync_agent_settings(self) -> None:
        settings = self.controller.agent.settings
        for market, value in (("team", settings.research_brief), ("equity", settings.equity_brief), ("crypto", settings.crypto_brief)):
            self.briefs[market].setText(value)
        self.equities.setText(", ".join(settings.equity_symbols))
        self.crypto.setText(", ".join(settings.crypto_symbols))
        self.scans.setCurrentIndex(max(0, self.scans.findData(settings.scan_id)))
        self.interval.setValue(settings.interval_seconds)
        self.local_ai.setChecked(settings.local_ai_enabled)
        self.model.setText(settings.local_ai_model)
        self.news_enabled.setChecked(settings.news_enabled)
        self.social_enabled.setChecked(settings.social_enabled)

    def _apply_briefs(self) -> None:
        values = {market: editor.text() for market, editor in self.briefs.items()}
        try:
            for market, value in values.items():
                self.controller.set_agent_brief(market, value)
            self.mcp_status.setText("Prompts applied for the next cycle; Rules baseline ignores prompts when local AI is off.")
        except (ValueError, RuntimeError) as exc:
            self.mcp_status.setText(str(exc))

    def _toggle_mcp(self, enabled: bool) -> None:
        try:
            self.controller.set_agent_mcp_enabled(enabled)
        except Exception:
            self._mcp_changed(False)
            self.mcp_status.setText("MCP could not start. Check that the app's data directory is writable.")

    def _mcp_changed(self, enabled: bool) -> None:
        self.mcp_enabled.blockSignals(True)
        self.mcp_enabled.setChecked(enabled)
        self.mcp_enabled.blockSignals(False)
        self.mcp_status.setText("AI research access is on · trading remains off" if enabled else "AI research access is off · enable it again to reconnect")
        self._set_controls()

    def _show_chatgpt_setup(self) -> None:
        if self._chatgpt_help is None:
            # Broker work disables the Agent page. The setup window must retain
            # Back/Close while guarding its permission action explicitly.
            self._chatgpt_help = ChatGPTSetupDialog(self.controller, self.window())
        self._chatgpt_help.set_operation_busy(self._connection_busy)
        self._chatgpt_help.show()
        self._chatgpt_help.raise_()
        self._chatgpt_help.activateWindow()

    def set_connection_busy(self, busy: bool) -> None:
        self._connection_busy = busy
        if self._chatgpt_help is not None:
            self._chatgpt_help.set_operation_busy(busy)

    def _copy_mcp_config(self) -> None:
        config = {"mcpServers": {"grande-alpha": {
            "command": sys.executable,
            "args": ["-m", "grande_alpha.agent_mcp", "--bridge", str(self.controller.agent_bridge.path.resolve())],
            "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        }}}
        QApplication.clipboard().setText(json.dumps(config, indent=2))
        self.mcp_status.setText("Configuration copied. Add it to a local stdio MCP client; keep GRANDE open and MCP enabled.")

    def _schedule_scans(self) -> None:
        if self._scan_task is not None and not self._scan_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._scan_task = loop.create_task(self._refresh_scans())

    def _export_contracts(self) -> None:
        try:
            report = self.controller.broker.agent_tool_contracts()
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export broker compatibility", "grande-agent-compatibility.json", "JSON (*.json)"
            )
            if filename:
                Path(filename).write_text(json.dumps(report, indent=2), encoding="utf-8")
                self.market_status.setText("Compatibility report saved: tool definitions only, no account data.")
        except Exception as exc:
            QMessageBox.warning(self, "Compatibility report unavailable", str(exc))

    async def _refresh_scans(self) -> None:
        self.load_scans.setEnabled(False)
        try:
            scans = await self.controller.broker.get_scans()
            current = self.scans.currentData()
            self.scans.clear()
            self.scans.addItem("Watchlist only", "")
            for scan_id, title in scans:
                self.scans.addItem(title, scan_id)
            index = self.scans.findData(current)
            self.scans.setCurrentIndex(max(index, 0))
        except Exception as exc:
            self.market_status.setText(f"Saved scans unavailable: {exc}")
        finally:
            self.load_scans.setEnabled(self._connected and not self.controller.agent.snapshot.running)

    def update_account(self, snapshot) -> None:
        was_connected = self._connected
        self._connected = bool(snapshot.connected and self.controller.config.broker_connection_enabled)
        account = snapshot.account.account_number if snapshot.account and self._connected else None
        budget = snapshot.agent_budget if account and snapshot.agent_budget and snapshot.agent_budget.get("account_number") == account else None
        if account != self._budget_account:
            self._budget_account = account if budget else None
            limits = budget.get("limits", {}) if budget else {}
            for key, spin in self.budget_inputs.items():
                spin.setValue(float(limits.get(key, 0)))
        if budget:
            self.journal_status.setText(
                f"Execution journal · ${budget['committed_cash']:,.2f} committed · "
                f"${budget['daily_buy_cash']:,.2f} daily buy usage · {budget['pending_orders']} pending · "
                f"{snapshot.agent_recovery_status}" +
                (f" · {budget['block_reason']}" if budget['block_reason'] else "")
            )
        else:
            self.journal_status.setText("Execution journal · Connect to load this account's saved limits and order state")
        if account != self._balance_account:
            self._balance_times.clear()
            self._balances.clear()
            self.curve.setData([], [])
            self._balance_account = account
        portfolio = snapshot.portfolio if self._connected else None
        self.balance.setText(f"${portfolio.total_value:,.2f}" if portfolio else "—")
        self.chart_value.setText(f"${portfolio.total_value:,.2f}" if portfolio else "Awaiting account data")
        self.buying_power.setText(f"${portfolio.buying_power:,.2f}" if portfolio else "—")
        crypto_bp = portfolio.crypto_buying_power if portfolio else None
        self.crypto_funds.setText(
            f"Crypto buying power · ${crypto_bp:,.2f} (broker reported)"
            if crypto_bp is not None else "Crypto buying power · unavailable"
        )
        timestamp = snapshot.last_reconcile_at
        if (
            portfolio
            and timestamp
            and (not self._balance_times or timestamp.timestamp() > self._balance_times[-1])
        ):
            self._balance_times.append(timestamp.timestamp())
            self._balances.append(portfolio.total_value)
            self.curve.setFillLevel(min(self._balances) - max(0.01, (max(self._balances) - min(self._balances)) * 0.1))
            self.curve.setData(list(self._balance_times), list(self._balances), symbol="o" if len(self._balances) == 1 else None, symbolSize=5, symbolBrush=color("#1ca97a", base="light"))
        if was_connected and not self._connected:
            if self._scan_task is not None:
                self._scan_task.cancel()
            self.controller.stop_agent("Agent stopped because the broker disconnected")
        self._update_paper(self.controller.agent.paper_context())
        self._set_controls()

    def _set_controls(self) -> None:
        running = self.controller.agent.snapshot.running
        enabled = self._connected and not self.controller.shadow_only_runtime
        self.save_budget.setEnabled(enabled and not running)
        self.budget_box.setEnabled(enabled and not running)
        self.start.setEnabled(enabled and not running)
        self.paper_start.setEnabled(not running and not self.controller.shadow_only_runtime
                                    and (self.paper_source.currentData() == "demo" or enabled))
        self.paper_start.setText("Start offline demo" if self.paper_source.currentData() == "demo" else "Start continuous paper trading")
        self.paper_start.setToolTip("Connect Robinhood to use current quotes with virtual funds."
                                   if self.paper_source.currentData() == "broker_quotes" and not enabled else "")
        self.repeat_demo.setEnabled(not running and self.paper_source.currentData() == "demo")
        self.news_enabled.setEnabled(not running and not self.controller.shadow_only_runtime)
        self.social_enabled.setEnabled(not running and self.news_enabled.isChecked() and not self.controller.shadow_only_runtime)
        for editor in (self.paper_source, self.paper_cash, self.paper_trade_cash):
            editor.setEnabled(not running)
        self.export_contracts.setEnabled(enabled and not running)
        self.stop.setEnabled(running or bool(self.controller.agent_bridge.session))
        self.mcp_enabled.setEnabled(not self.controller.shadow_only_runtime)
        self.configure.setEnabled(not running)
        self.settings_box.setEnabled(not running)
        self.load_scans.setEnabled(
            enabled and not running and (self._scan_task is None or self._scan_task.done())
        )
        self._update_run_status()

    def _save_budget(self) -> None:
        try:
            budget = AgentBudget(**{key: Decimal(f"{spin.value():.2f}") for key, spin in self.budget_inputs.items()})
            self.controller.save_agent_budget(budget)
            self.update_account(self.controller.snapshot)
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Cash limits not saved", str(exc))

    def update_agent(self, snapshot: AgentSnapshot) -> None:
        self._dashboard_snapshot = snapshot
        if snapshot.running:
            self._run_error = ""
        self._update_clock()
        if snapshot.running and self.configure.isChecked():
            self.configure.setChecked(False)
        if snapshot.running and self.paper_toggle.isChecked():
            self.paper_toggle.setChecked(False)
        paper_mode = bool(snapshot.paper and (self.controller.agent.paper_source or not snapshot.running))
        mode = ("DEMO · VIRTUAL MONEY" if snapshot.paper["source"] == "demo" else "PAPER · VIRTUAL MONEY") if paper_mode else "PROPOSALS ONLY"
        continuous = snapshot.running and self.controller.agent.continuous_paper
        phase = "WATCHING" if continuous and snapshot.phase == "Waiting" else snapshot.phase.upper()
        self.mode.setText(f"{'CONTINUOUS · ' if continuous else ''}{phase} · UPDATE {snapshot.cycle} · {mode}")
        self._update_paper(snapshot.paper)
        self._update_sources(snapshot)
        self.equity_status.setText(snapshot.worker_status.get("equity", "Idle") + " · " + snapshot.market_status.get("equity", "Waiting for stock observations"))
        self.crypto_status.setText(snapshot.worker_status.get("crypto", "Idle") + " · " + snapshot.market_status.get("crypto", "Waiting for crypto observations"))
        self.candidates.setText(str(len(snapshot.decisions)))
        self.proposals.setText(str(sum(item.action != "hold" for item in snapshot.decisions)))
        self.scout_status.setText(f"Scout · {snapshot.phase}")
        self.analyst_status.setText(f"Analyst · {snapshot.analyst}")
        self.risk_status.setText(
            f"Risk · {sum(item.risk_status == 'Blocked' for item in snapshot.decisions)} candidates blocked by data checks"
        )
        self.execution_status.setText(f"Execution · {snapshot.execution_status}")
        if snapshot.market_status:
            self.market_status.setText(
                "\n".join(f"{market.upper()} · {state}" for market, state in snapshot.market_status.items())
            )
        self.table.setRowCount(len(snapshot.decisions))
        for row, decision in enumerate(snapshot.decisions):
            values = (
                decision.instrument.asset_class.value.upper(),
                decision.instrument.symbol,
                f"${decision.quote.mid:,.6g}" if decision.quote else "—",
                decision.action.upper(),
                "Passed" if decision.risk_status == "Data checks passed" else decision.risk_status,
                decision.reason,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 3:
                    set_item_foreground(item, {"buy": "#16845e", "exit": "#b74b63", "hold": "#788b97"}[decision.action], base="light")
                self.table.setItem(row, column, item)
        if snapshot.cycle < self._last_cycle:
            self._last_cycle = 0
        if snapshot.observed_at and snapshot.cycle != self._last_cycle and snapshot.phase == "Waiting":
            self._last_cycle = snapshot.cycle
            summaries = [
                f"[{'RISK' if d.risk_status == 'Blocked' else 'IDEA' if d.action != 'hold' else 'SCAN'}] "
                f"{d.instrument.key} · {d.action.upper()} · {d.reason}" for d in snapshot.decisions
            ]
            summaries += [
                f"{market} · {status}"
                for market, status in snapshot.market_status.items()
                if status.startswith("Unavailable")
            ]
            for summary in summaries or ["No candidates returned"]:
                self._add_activity(snapshot.observed_at, str(snapshot.cycle),
                                   summary, "RISK" if summary.startswith("[RISK]") else "IDEA" if summary.startswith("[IDEA]") else "SCAN")
        self._update_handoffs(snapshot)
        demo = bool(snapshot.paper and snapshot.paper["source"] == "demo")
        self.activity_hint.setText(f"{self.activity.rowCount()} events shown · Pacific time (PST/PDT) · " +
                                   ("simulated demo clock · " if demo else "") +
                                   ("paper fills use virtual money" if snapshot.paper else "research proposals only"))
        self.activity.resizeRowsToContents()
        self._set_controls()

    def shutdown(self) -> None:
        self._clock_timer.stop()
        for card in self.stage_cards:
            card.stop_motion()
        if self._scan_task is not None:
            self._scan_task.cancel()
        self.controller.stop_agent("Agent stopped when the application closed")

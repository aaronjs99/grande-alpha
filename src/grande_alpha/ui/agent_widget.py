from __future__ import annotations

import asyncio
import json
import sys
from collections import deque
from decimal import Decimal
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
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
from grande_alpha.ui.table_layout import configure_adjustable_columns
from grande_alpha.ui.themes import color, set_item_foreground, theme_css


def label(text: str) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


class AgentAvatar(QWidget):
    """Small code-drawn status avatar; no external assets or network requests."""

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = QColor(accent)
        self.setFixedSize(56, 56)
        self.setAccessibleName("Agent module icon")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color("#e7ebee", base="light")), 1))
        painter.setBrush(QColor(color("#ffffff", base="light")))
        painter.drawEllipse(QRectF(3, 3, 50, 50))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("#25323a", base="light")))
        painter.drawRoundedRect(QRectF(19, 18, 5, 14), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(31, 18, 5, 14), 2.5, 2.5)
        painter.setPen(QPen(self.accent, 2.5))
        painter.drawLine(23, 39, 32, 39)


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
        self._last_cycle = 0
        self._balance_account: str | None = None
        self._budget_account: str | None = None
        self._balance_times: deque[float] = deque(maxlen=500)
        self._balances: deque[float] = deque(maxlen=500)
        self._scan_task: asyncio.Task | None = None
        self.setWidgetResizable(True)
        self.setStyleSheet(theme_css(DASHBOARD_STYLE, base="light"))
        content = QWidget()
        content.setObjectName("agentCanvas")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        self.header_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        title = label("GRANDE / AGENT DESK")
        title.setObjectName("dashboardTitle")
        self.header_layout.addWidget(title, 1)
        self.mode = label("IDLE · PROPOSALS ONLY")
        self.mode.setObjectName("modeBadge")
        self.header_layout.addWidget(self.mode)
        layout.addLayout(self.header_layout)
        notice = label("STOCKS + CRYPTO  /  Research mode · analysis is active only when started · live execution is unavailable")
        notice.setObjectName("dashboardNotice")
        layout.addWidget(notice)

        self.metrics = QGridLayout()
        self.metrics.setSpacing(16)
        self.metric_cards = []
        self.balance = label("—")
        self.buying_power = label("—")
        self.candidates = label("0")
        self.proposals = label("0")
        for index, (name, value, caption) in enumerate((
            ("ACCOUNT VALUE", self.balance, "Selected Agentic account · broker reported"),
            ("BUYING POWER", self.buying_power, "Equities · broker-reported buying power"),
            ("CANDIDATES", self.candidates, "Stocks + crypto observed this cycle"),
            ("PROPOSALS", self.proposals, "Research actions · not executed trades"),
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
        self.interval.setRange(15, 300)
        self.interval.setValue(30)
        self.interval.setSuffix(" seconds after each completed cycle")
        form.addRow("Cadence", self.interval)
        self.local_ai = QCheckBox("Use a local Ollama model for analysis")
        self.model = QLineEdit()
        self.model.setPlaceholderText("Installed model name")
        self.model.setEnabled(False)
        self.local_ai.toggled.connect(self.model.setEnabled)
        form.addRow(self.local_ai)
        form.addRow("Local model", self.model)
        form.addRow(
            label(
                "Optional AI sends symbols and numeric price observations to Ollama on this computer. "
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
        self.prompt_box = QGroupBox("Worker prompts and MCP")
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
            "MCP lets a compatible local AI app read symbols, price observations and prompts, change research "
            "prompts/universes, and start or stop analysis. Its provider may process that shared data. "
            "Account balances, credentials, positions and order tools are excluded. Access starts off and "
            "ends on Stop agent, STOP + CANCEL, Disconnect or Exit."
        ))
        self.mcp_enabled = QCheckBox("Enable research MCP for this app session")
        self.mcp_enabled.toggled.connect(self._toggle_mcp)
        prompt_form.addRow(self.mcp_enabled)
        self.copy_mcp = QPushButton("Copy MCP client configuration")
        self.copy_mcp.clicked.connect(self._copy_mcp_config)
        self.copy_mcp.setEnabled(not getattr(sys, "frozen", False))
        prompt_form.addRow(self.copy_mcp)
        self.mcp_status = label("MCP is off · supports local stdio clients · live orders unavailable")
        prompt_form.addRow(self.mcp_status)

        controls = QHBoxLayout()
        self.start = QPushButton("Start agent analysis")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self._start)
        self.stop = QPushButton("Stop agent")
        self.stop.clicked.connect(lambda: controller.stop_agent())
        controls.addWidget(self.start)
        controls.addWidget(self.stop)
        controls.addWidget(self.prompts_toggle)
        layout.addLayout(controls)
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
        self.chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.chart.setBackground("#ffffff")
        self.chart.setMinimumSize(0, 180)
        self.chart.setMaximumHeight(220)
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
        detail = label("Account value this session · includes deposits and withdrawals; not trading P&L")
        detail.setObjectName("metricCaption")
        chart_layout.addWidget(detail)
        activity_panel, activity_layout = self._panel()
        activity_title = label("// ACTIVITY LOG")
        activity_title.setObjectName("sectionTitle")
        activity_layout.addWidget(activity_title)
        self.activity = self._table(["Time", "Cycle", "Research activity"])
        self.activity.setMinimumHeight(225)
        self.activity.setMaximumHeight(250)
        self.activity.setColumnWidth(0, 80)
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
        status_title = label("// AGENT STATUS")
        status_title.setObjectName("sectionTitle")
        status_layout.addWidget(status_title)
        status_layout.addWidget(self.market_status)
        layout.addWidget(status_panel)
        self.stages = QGridLayout()
        self.stages.setSpacing(12)
        self.stage_cards = []
        for index, (name, accent, status) in enumerate((
            ("SCOUT", "#24b6c4", self.scout_status),
            ("ANALYST", "#51b18a", self.analyst_status),
            ("RISK", "#c4a340", self.risk_status),
            ("EXECUTION", "#d6864f", self.execution_status),
            ("EQUITIES", "#be72a2", self.equity_status),
            ("CRYPTO", "#9273c8", self.crypto_status),
        )):
            card, card_layout = self._panel()
            card.setStyleSheet(f"QFrame#dashboardPanel {{ border-top: 3px solid {accent}; }}")
            number = label(f"{index + 1:02d}  /  MODULE")
            number.setObjectName("metricCaption")
            card_layout.addWidget(number)
            card_layout.addWidget(AgentAvatar(accent), 0, Qt.AlignmentFlag.AlignHCenter)
            heading = label(name)
            heading.setObjectName("eyebrow")
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_layout.addWidget(heading)
            status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            status.setObjectName("metricCaption")
            card_layout.addWidget(status)
            card_layout.addStretch()
            self.stage_cards.append(card)
            self.stages.addWidget(card, index // 3, index % 3)
        layout.addLayout(self.stages)
        detail = label("Equities and Crypto work concurrently. Scout, Analyst and Risk show their shared workflow; Execution remains locked.")
        detail.setObjectName("metricCaption")
        layout.addWidget(detail)

        candidates_panel, candidates_layout = self._panel()
        heading = label("// MARKET OBSERVATIONS")
        heading.setObjectName("sectionTitle")
        candidates_layout.addWidget(heading)
        self.table = self._table(["Market", "Symbol", "Midpoint", "Proposal", "Data checks", "Reason"])
        self.table.setMinimumHeight(210)
        self.table.setMaximumHeight(340)
        candidates_layout.addWidget(self.table)
        layout.addWidget(candidates_panel)
        layout.addWidget(self.crypto_funds)
        layout.addWidget(self.journal_status)
        self.tools_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.tools_layout.addWidget(self.configure)
        self.tools_layout.addWidget(self.budget_toggle)
        layout.addLayout(self.tools_layout)
        layout.addWidget(self.settings_box)
        layout.addWidget(self.budget_box)
        self.setWidget(content)
        controller.agent_changed.connect(self.update_agent)
        controller.agent_mcp_changed.connect(self._mcp_changed)
        controller.agent_settings_changed.connect(self._sync_agent_settings)
        self.update_agent(controller.agent.snapshot)

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
        for avatar in self.findChildren(AgentAvatar):
            avatar.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "metrics"):
            return
        wide = self.viewport().width() >= 1100
        self.header_layout.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
        self.tools_layout.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
        stage_columns = 6 if wide else (3 if self.viewport().width() >= 700 else 2)
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
    def _panel():
        panel = QFrame()
        panel.setObjectName("dashboardPanel")
        panel.setMinimumWidth(0)
        box = QVBoxLayout(panel)
        box.setContentsMargins(18, 18, 18, 18)
        box.setSpacing(10)
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
        try:
            self.controller.start_agent(self._read_settings())
        except Exception as exc:
            QMessageBox.warning(self, "Agent could not start", str(exc))

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
        self.mcp_status.setText("MCP enabled · research access only" if enabled else "MCP is off · enable it again to allow AI access")
        self._set_controls()

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
        self._set_controls()

    def _set_controls(self) -> None:
        running = self.controller.agent.snapshot.running
        enabled = self._connected and not self.controller.shadow_only_runtime
        self.save_budget.setEnabled(enabled and not running)
        self.budget_box.setEnabled(enabled and not running)
        self.start.setEnabled(enabled and not running)
        self.export_contracts.setEnabled(enabled and not running)
        self.stop.setEnabled(running or bool(self.controller.agent_bridge.session))
        self.mcp_enabled.setEnabled(not self.controller.shadow_only_runtime)
        self.configure.setEnabled(not running)
        self.settings_box.setEnabled(not running)
        self.load_scans.setEnabled(
            enabled and not running and (self._scan_task is None or self._scan_task.done())
        )

    def _save_budget(self) -> None:
        try:
            budget = AgentBudget(**{key: Decimal(f"{spin.value():.2f}") for key, spin in self.budget_inputs.items()})
            self.controller.save_agent_budget(budget)
            self.update_account(self.controller.snapshot)
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Cash limits not saved", str(exc))

    def update_agent(self, snapshot: AgentSnapshot) -> None:
        if snapshot.running and self.configure.isChecked():
            self.configure.setChecked(False)
        self.mode.setText(f"{snapshot.phase.upper()} · CYCLE {snapshot.cycle} · PROPOSALS ONLY")
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
                self.activity.insertRow(0)
                for column, value in enumerate(
                    (snapshot.observed_at.astimezone().strftime("%H:%M:%S"), str(snapshot.cycle), summary)
                ):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column == 2:
                        set_item_foreground(item, "#a77924" if summary.startswith("[RISK]") else "#16845e" if summary.startswith("[IDEA]") else "#566e7c", base="light")
                    self.activity.setItem(0, column, item)
                if self.activity.rowCount() > 200:
                    self.activity.removeRow(200)
        if self.activity.rowCount():
            self.activity_hint.setText(f"{self.activity.rowCount()} research events shown · proposals are not broker fills")
            self.activity.resizeRowsToContents()
        self._set_controls()

    def shutdown(self) -> None:
        if self._scan_task is not None:
            self._scan_task.cancel()
        self.controller.stop_agent("Agent stopped when the application closed")

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
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

from grande_alpha.agent_models import AgentSettings, AgentSnapshot, parse_symbols
from grande_alpha.ui.table_layout import configure_adjustable_columns


def label(text: str) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


class AgentWidget(QScrollArea):
    """Broker-backed observability with a plainly labeled proposal/execution boundary."""

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self._connected = False
        self._last_cycle = 0
        self._balance_account: str | None = None
        self._balance_times: deque[float] = deque(maxlen=500)
        self._balances: deque[float] = deque(maxlen=500)
        self._scan_task: asyncio.Task | None = None
        self.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        title = label("Agent workspace · Stocks + crypto")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.mode = label("IDLE · PROPOSALS ONLY")
        self.mode.setStyleSheet("color:#65b9ff;font-weight:700")
        layout.addWidget(self.mode)
        notice = label(
            "Research proposals only. Live multi-market trading awaits validated account routing, "
            "execution, risk limits, and strategy evidence."
        )
        notice.setObjectName("validationWarning")
        layout.addWidget(notice)

        self.metrics = QGridLayout()
        self.metric_cards = []
        self.balance = label("—")
        self.buying_power = label("—")
        self.candidates = label("0")
        self.proposals = label("0")
        for index, (name, value) in enumerate(
            (
                ("Selected Agentic account value", self.balance),
                ("Broker buying power", self.buying_power),
                ("Candidates this cycle", self.candidates),
                ("Action proposals · not fills", self.proposals),
            )
        ):
            card = QGroupBox(name)
            card_layout = QVBoxLayout(card)
            value.setStyleSheet("font-size:20pt;font-weight:650")
            card_layout.addWidget(value)
            self.metric_cards.append(card)
            self.metrics.addWidget(card, index // 2, index % 2)
        layout.addLayout(self.metrics)

        self.configure = QPushButton("Configure universe and AI")
        self.configure.setCheckable(True)
        layout.addWidget(self.configure)
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
        layout.addWidget(self.settings_box)

        controls = QHBoxLayout()
        self.start = QPushButton("Start agent analysis")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self._start)
        self.stop = QPushButton("Stop agent")
        self.stop.clicked.connect(lambda: controller.agent.stop())
        controls.addWidget(self.start)
        controls.addWidget(self.stop)
        layout.addLayout(controls)

        self.scout_status = label("Scout · Waiting to discover stocks and crypto")
        self.analyst_status = label("Analyst · Rules baseline")
        self.risk_status = label("Risk · Waiting for fresh observations")
        self.execution_status = label("Execution · No multi-market orders can be submitted")
        stages = QGridLayout()
        for index, widget in enumerate(
            (self.scout_status, self.analyst_status, self.risk_status, self.execution_status)
        ):
            stages.addWidget(widget, index // 2, index % 2)
        layout.addLayout(stages)
        self.market_status = label("Connect Robinhood, choose the universe, then start analysis.")
        layout.addWidget(self.market_status)
        self.table = self._table(["Market", "Symbol", "Midpoint", "Proposal", "Data checks", "Reason"])
        self.table.setMinimumHeight(220)
        self.table.setMaximumHeight(360)

        self.observations_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        chart_panel = QWidget()
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.addWidget(label("Account value · this app session, not trading returns"))
        self.chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.chart.setBackground("#0e1720")
        self.chart.setMinimumSize(0, 160)
        self.chart.setMaximumHeight(220)
        self.chart.setLabel("left", "Account value", units="USD")
        self.chart.showGrid(x=True, y=True, alpha=0.15)
        self.curve = self.chart.plot(pen=pg.mkPen("#00c805", width=2))
        chart_layout.addWidget(self.chart)
        self.observations_layout.addWidget(chart_panel, 2)
        self.observations_layout.addWidget(self.table, 3)
        layout.addLayout(self.observations_layout)
        layout.addWidget(label("Agent activity · full cycle details are saved locally in Receipts"))
        self.activity = self._table(["Observed at", "Cycle", "Activity"])
        self.activity.setMinimumHeight(170)
        layout.addWidget(self.activity)
        self.setWidget(content)
        controller.agent_changed.connect(self.update_agent)
        self.update_agent(controller.agent.snapshot)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "metrics"):
            return
        wide = self.viewport().width() >= 1100
        columns = 4 if wide else 2
        for card in self.metric_cards:
            self.metrics.removeWidget(card)
        for index, card in enumerate(self.metric_cards):
            self.metrics.addWidget(card, index // columns, index % columns)
        self.observations_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        )

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        configure_adjustable_columns(table, headers)
        return table

    def _start(self) -> None:
        try:
            self.controller.start_agent(
                AgentSettings(
                    equity_symbols=parse_symbols(self.equities.text()),
                    crypto_symbols=parse_symbols(self.crypto.text()),
                    scan_id=str(self.scans.currentData() or ""),
                    interval_seconds=self.interval.value(),
                    local_ai_model=self.model.text().strip(),
                    local_ai_enabled=self.local_ai.isChecked(),
                )
            )
        except Exception as exc:
            QMessageBox.warning(self, "Agent could not start", str(exc))

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
        self._connected = bool(snapshot.connected and self.controller.config.broker_connection_enabled)
        account = snapshot.account.account_number if snapshot.account and self._connected else None
        if account != self._balance_account:
            self._balance_times.clear()
            self._balances.clear()
            self.curve.setData([], [])
            self._balance_account = account
        portfolio = snapshot.portfolio if self._connected else None
        self.balance.setText(f"${portfolio.total_value:,.2f}" if portfolio else "—")
        self.buying_power.setText(f"${portfolio.buying_power:,.2f}" if portfolio else "—")
        timestamp = snapshot.last_reconcile_at
        if (
            portfolio
            and timestamp
            and (not self._balance_times or timestamp.timestamp() > self._balance_times[-1])
        ):
            self._balance_times.append(timestamp.timestamp())
            self._balances.append(portfolio.total_value)
            self.curve.setData(list(self._balance_times), list(self._balances))
        if not self._connected:
            if self._scan_task is not None:
                self._scan_task.cancel()
            self.controller.agent.stop("Agent stopped because the broker disconnected")
        self._set_controls()

    def _set_controls(self) -> None:
        running = self.controller.agent.snapshot.running
        enabled = self._connected and not self.controller.shadow_only_runtime
        self.start.setEnabled(enabled and not running)
        self.export_contracts.setEnabled(enabled and not running)
        self.stop.setEnabled(running)
        self.configure.setEnabled(not running)
        self.settings_box.setEnabled(not running)
        self.load_scans.setEnabled(
            enabled and not running and (self._scan_task is None or self._scan_task.done())
        )

    def update_agent(self, snapshot: AgentSnapshot) -> None:
        if snapshot.running and self.configure.isChecked():
            self.configure.setChecked(False)
        self.mode.setText(f"{snapshot.phase.upper()} · CYCLE {snapshot.cycle} · PROPOSALS ONLY")
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
                    item.setForeground(
                        QColor({"buy": "#00e507", "exit": "#ff697d", "hold": "#8fa4b8"}[decision.action])
                    )
                self.table.setItem(row, column, item)
        if snapshot.cycle < self._last_cycle:
            self._last_cycle = 0
        if snapshot.observed_at and snapshot.cycle != self._last_cycle and snapshot.phase == "Waiting":
            self._last_cycle = snapshot.cycle
            summaries = [f"{d.instrument.key} · {d.action.upper()} · {d.reason}" for d in snapshot.decisions]
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
                    self.activity.setItem(0, column, QTableWidgetItem(value))
                if self.activity.rowCount() > 200:
                    self.activity.removeRow(200)
        self._set_controls()

    def shutdown(self) -> None:
        if self._scan_task is not None:
            self._scan_task.cancel()
        self.controller.agent.stop("Agent stopped when the application closed")

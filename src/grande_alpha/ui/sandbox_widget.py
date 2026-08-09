from __future__ import annotations

import asyncio
from dataclasses import asdict

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.historical import HistoricalDataProvider, deterministic_demo
from grande_alpha.sandbox import (
    SandboxConfig,
    SandboxReplayEngine,
    SandboxResult,
    load_sandbox_config,
    save_sandbox_config,
)
from grande_alpha.storage import AuditStore

PRESETS = {
    "Live defaults": SandboxConfig(),
    "Fast / reactive": SandboxConfig(
        warmup_bars=15,
        fast_ema=3,
        slow_ema=10,
        trend_threshold_bps=2.0,
        momentum_bars=1,
        hard_stop_pct=0.006,
        take_profit_pct=0.01,
        max_hold_minutes=20,
        max_entries_per_day=10,
    ),
    "Slow / selective": SandboxConfig(
        warmup_bars=60,
        fast_ema=12,
        slow_ema=40,
        trend_threshold_bps=8.0,
        momentum_bars=5,
        hard_stop_pct=0.012,
        take_profit_pct=0.025,
        max_hold_minutes=90,
        max_entries_per_day=3,
    ),
}


class SandboxWidget(QWidget):
    """Historical virtual replay surface. It intentionally receives no Broker instance."""

    def __init__(self, store: AuditStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._build_ui()
        self._apply_config(load_sandbox_config())

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        banner = QLabel(
            "SANDBOX ONLY — TQQQS and SQQQS are fictional aliases backed by historical TQQQ/SQQQ "
            "prices. This tab has no Robinhood broker object and cannot submit, review, or cancel real orders."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "background:#15324a;color:#8fd3ff;border:1px solid #3478a4;border-radius:7px;padding:9px;font-weight:650"
        )
        outer.addWidget(banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        config_panel = QWidget()
        config_layout = QVBoxLayout(config_panel)

        data_group = QGroupBox("Dataset and preset")
        data_form = QFormLayout(data_group)
        self.source = QComboBox()
        self.source.addItem("Historical one-minute data", "historical")
        self.source.addItem("Offline deterministic scenario", "demo")
        self.preset = QComboBox()
        self.preset.addItems(PRESETS)
        apply_preset = QPushButton("Apply preset")
        apply_preset.clicked.connect(self._apply_selected_preset)
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.preset)
        preset_row.addWidget(apply_preset)
        self.lookback = self._integer(1, 7)
        self.lookback.setSuffix(" days")
        data_form.addRow("Data source", self.source)
        data_form.addRow("Preset", preset_row)
        data_form.addRow("Calendar lookback", self.lookback)
        config_layout.addWidget(data_group)

        portfolio_group = QGroupBox("Virtual execution")
        portfolio_form = QFormLayout(portfolio_group)
        self.initial_cash = self._decimal(1.0, 1_000_000.0, "$", 2)
        self.order_notional = self._decimal(0.01, 1_000_000.0, "$", 2)
        self.slippage = self._decimal(0.0, 200.0, "", 1, " bps")
        self.commission = self._decimal(0.0, 100.0, "$", 2)
        portfolio_form.addRow("Starting virtual cash", self.initial_cash)
        portfolio_form.addRow("Virtual order notional", self.order_notional)
        portfolio_form.addRow("Slippage per side", self.slippage)
        portfolio_form.addRow("Commission per order", self.commission)
        config_layout.addWidget(portfolio_group)

        signal_group = QGroupBox("Signal")
        signal_form = QFormLayout(signal_group)
        self.warmup = self._integer(5, 500)
        self.fast_ema = self._integer(1, 200)
        self.slow_ema = self._integer(2, 500)
        self.threshold = self._decimal(0.1, 100.0, "", 1, " bps")
        self.momentum = self._integer(1, 60)
        self.momentum.setSuffix(" bars")
        signal_form.addRow("Warm-up", self.warmup)
        signal_form.addRow("Fast EMA", self.fast_ema)
        signal_form.addRow("Slow EMA", self.slow_ema)
        signal_form.addRow("Trend threshold", self.threshold)
        signal_form.addRow("Momentum horizon", self.momentum)
        config_layout.addWidget(signal_group)

        exit_group = QGroupBox("Exit and frequency limits")
        exit_form = QFormLayout(exit_group)
        self.stop = self._decimal(0.1, 50.0, "", 2, "%")
        self.take_profit = self._decimal(0.1, 100.0, "", 2, "%")
        self.max_hold = self._integer(1, 390)
        self.max_hold.setSuffix(" min")
        self.max_entries = self._integer(1, 100)
        self.max_entries.setSuffix(" / day")
        self.no_trade_open = self._integer(0, 120)
        self.no_trade_open.setSuffix(" min")
        self.no_trade_close = self._integer(0, 120)
        self.no_trade_close.setSuffix(" min")
        exit_form.addRow("Hard stop", self.stop)
        exit_form.addRow("Take-profit", self.take_profit)
        exit_form.addRow("Maximum hold", self.max_hold)
        exit_form.addRow("Maximum entries", self.max_entries)
        exit_form.addRow("Skip after open", self.no_trade_open)
        exit_form.addRow("Skip before close", self.no_trade_close)
        config_layout.addWidget(exit_group)

        self.run_button = QPushButton("Run isolated sandbox")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(lambda: asyncio.create_task(self._run()))
        config_layout.addWidget(self.run_button)
        config_layout.addStretch()
        config_scroll.setWidget(config_panel)
        splitter.addWidget(config_scroll)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        metrics = QGridLayout()
        self.metric_labels: dict[str, QLabel] = {}
        metric_names = [
            ("final", "Final virtual equity"),
            ("pnl", "Net virtual P/L"),
            ("return", "Replay return"),
            ("drawdown", "Maximum drawdown"),
            ("trades", "Completed round trips"),
            ("win_rate", "Win rate"),
            ("tqqqs_hold", "TQQQS buy-and-hold"),
            ("sqqqs_hold", "SQQQS buy-and-hold"),
        ]
        for index, (key, title) in enumerate(metric_names):
            card = QGroupBox(title)
            card_layout = QVBoxLayout(card)
            value = QLabel("—")
            value.setStyleSheet("font-size:16pt;font-weight:650")
            card_layout.addWidget(value)
            metrics.addWidget(card, index // 4, index % 4)
            self.metric_labels[key] = value
        results_layout.addLayout(metrics)

        self.chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.chart.setBackground("#0e1720")
        self.chart.showGrid(x=True, y=True, alpha=0.18)
        self.chart.setLabel("left", "Virtual equity", units="$")
        self.equity_curve = self.chart.plot(pen=pg.mkPen("#8fd3ff", width=2))
        results_layout.addWidget(self.chart, 3)

        self.fills_table = QTableWidget(0, 8)
        self.fills_table.setHorizontalHeaderLabels(
            ["Time", "Alias", "Side", "Quantity", "Fill", "Cost", "Realized P/L", "Reason"]
        )
        self.fills_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.fills_table.verticalHeader().setVisible(False)
        self.fills_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fills_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.fills_table.setAlternatingRowColors(True)
        results_layout.addWidget(self.fills_table, 2)
        self.status = QLabel("Configure a replay and select Run. Robinhood connection is not required.")
        self.status.setWordWrap(True)
        results_layout.addWidget(self.status)
        splitter.addWidget(results)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        outer.addWidget(splitter)

    def _integer(self, minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        return widget

    def _decimal(
        self,
        minimum: float,
        maximum: float,
        prefix: str = "",
        decimals: int = 2,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setPrefix(prefix)
        widget.setSuffix(suffix)
        return widget

    def _apply_selected_preset(self) -> None:
        self._apply_config(PRESETS[self.preset.currentText()])

    def _apply_config(self, config: SandboxConfig) -> None:
        self.lookback.setValue(config.lookback_days)
        self.initial_cash.setValue(config.initial_cash)
        self.order_notional.setValue(config.order_notional)
        self.slippage.setValue(config.slippage_bps)
        self.commission.setValue(config.commission_per_order)
        self.warmup.setValue(config.warmup_bars)
        self.fast_ema.setValue(config.fast_ema)
        self.slow_ema.setValue(config.slow_ema)
        self.threshold.setValue(config.trend_threshold_bps)
        self.momentum.setValue(config.momentum_bars)
        self.stop.setValue(config.hard_stop_pct * 100.0)
        self.take_profit.setValue(config.take_profit_pct * 100.0)
        self.max_hold.setValue(config.max_hold_minutes)
        self.max_entries.setValue(config.max_entries_per_day)
        self.no_trade_open.setValue(config.no_trade_open_minutes)
        self.no_trade_close.setValue(config.no_trade_close_minutes)

    def _config(self) -> SandboxConfig:
        return SandboxConfig(
            lookback_days=self.lookback.value(),
            initial_cash=self.initial_cash.value(),
            order_notional=self.order_notional.value(),
            slippage_bps=self.slippage.value(),
            commission_per_order=self.commission.value(),
            warmup_bars=self.warmup.value(),
            fast_ema=self.fast_ema.value(),
            slow_ema=self.slow_ema.value(),
            trend_threshold_bps=self.threshold.value(),
            momentum_bars=self.momentum.value(),
            hard_stop_pct=self.stop.value() / 100.0,
            take_profit_pct=self.take_profit.value() / 100.0,
            max_hold_minutes=self.max_hold.value(),
            max_entries_per_day=self.max_entries.value(),
            no_trade_open_minutes=self.no_trade_open.value(),
            no_trade_close_minutes=self.no_trade_close.value(),
        )

    async def _run(self) -> None:
        self.run_button.setEnabled(False)
        self.run_button.setText("Loading and replaying…")
        try:
            config = self._config()
            config.validate()
            save_sandbox_config(config)
            if self.source.currentData() == "historical":
                self.status.setText("Downloading aligned QQQ/TQQQ/SQQQ one-minute history…")
                bundle = await HistoricalDataProvider().fetch(config.lookback_days)
            else:
                bundle = deterministic_demo(config.lookback_days)
            result = SandboxReplayEngine(config).run(bundle)
            self.store.record_sandbox_run(
                result.run_id,
                result.source,
                result.start.isoformat(),
                result.end.isoformat(),
                asdict(config),
                result.metrics(),
                [fill.as_dict() for fill in result.fills],
            )
            self._show_result(result, len(bundle.frames))
        except Exception as exc:
            self.status.setText(
                f"Sandbox did not run: {exc}. If historical download failed, select the offline scenario."
            )
            QMessageBox.warning(self, "Sandbox replay did not run", str(exc))
        finally:
            self.run_button.setEnabled(True)
            self.run_button.setText("Run isolated sandbox")

    def _show_result(self, result: SandboxResult, frame_count: int) -> None:
        values = {
            "final": f"${result.final_equity:,.2f}",
            "pnl": f"${result.net_pnl:+,.2f}",
            "return": f"{result.return_pct:+.2f}%",
            "drawdown": f"{result.max_drawdown_pct:.2f}%",
            "trades": str(result.round_trips),
            "win_rate": f"{result.win_rate:.1f}%",
            "tqqqs_hold": f"{result.tqqqs_buy_hold_pct:+.2f}%",
            "sqqqs_hold": f"{result.sqqqs_buy_hold_pct:+.2f}%",
        }
        color_values = {
            "pnl": result.net_pnl,
            "return": result.return_pct,
            "tqqqs_hold": result.tqqqs_buy_hold_pct,
            "sqqqs_hold": result.sqqqs_buy_hold_pct,
        }
        for key, value in values.items():
            label = self.metric_labels[key]
            label.setText(value)
            if key in color_values:
                label.setStyleSheet(
                    f"font-size:16pt;font-weight:650;color:"
                    f"{'#00e507' if color_values[key] >= 0 else '#ff697d'}"
                )
        self.equity_curve.setData(
            [point.timestamp.timestamp() for point in result.equity_curve],
            [point.equity for point in result.equity_curve],
        )
        self.fills_table.setRowCount(len(result.fills))
        for row, fill in enumerate(result.fills):
            display = [
                fill.timestamp.astimezone().strftime("%m/%d %I:%M %p"),
                fill.symbol,
                fill.side.upper(),
                f"{fill.quantity:.6f}",
                f"${fill.price:,.2f}",
                f"${fill.commission:,.2f}",
                f"${fill.realized_pnl:+,.2f}" if fill.realized_pnl is not None else "—",
                fill.reason,
            ]
            for column, value in enumerate(display):
                item = QTableWidgetItem(value)
                if column == 6 and fill.realized_pnl is not None:
                    item.setForeground(QColor("#00e507" if fill.realized_pnl >= 0 else "#ff697d"))
                self.fills_table.setItem(row, column, item)
        date_range = (
            f"{result.start.astimezone().strftime('%b %d %I:%M %p')} to "
            f"{result.end.astimezone().strftime('%b %d %I:%M %p')}"
        )
        self.status.setText(
            f"Saved run {result.run_id[:8]} • {frame_count:,} aligned one-minute candles • {date_range} • "
            f"{result.source}. {' '.join(result.warnings)}"
        )

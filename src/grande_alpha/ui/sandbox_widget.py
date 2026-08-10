from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.action_lab import (
    ALL_PAIR_ACTIONS,
    OfflineTrainingConfig,
    evaluate_daily_benchmarks,
    train_offline_action_policy,
)
from grande_alpha.evidence import (
    candidate_grid,
    compare_configs,
    cost_stress,
    parameter_sweep,
    promotion_report,
    random_entry_control,
    strategy_fingerprint,
    tested_risk_envelope,
    walk_forward,
)
from grande_alpha.historical import (
    HistoricalBundle,
    HistoricalDataProvider,
    deterministic_demo,
    full_history_calendar_days,
    load_csv_history,
)
from grande_alpha.sandbox import (
    SandboxConfig,
    SandboxReplayEngine,
    SandboxResult,
    load_sandbox_config,
    save_sandbox_config,
)
from grande_alpha.storage import AuditStore
from grande_alpha.strategy import STRATEGY_NAMES

PRESETS = {
    "Balanced research": SandboxConfig(),
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
    "Closing momentum research": SandboxConfig(
        strategy_name="close_momentum",
        close_momentum_bps=15.0,
        no_trade_open_minutes=0,
        no_trade_close_minutes=5,
        max_hold_minutes=30,
        max_entries_per_day=2,
    ),
    "First-half-hour momentum research": SandboxConfig(
        strategy_name="first_half_hour_momentum",
        close_momentum_bps=15.0,
        no_trade_open_minutes=0,
        no_trade_close_minutes=5,
        max_hold_minutes=30,
        max_entries_per_day=1,
    ),
    "Multi-horizon trend research": SandboxConfig(
        strategy_name="multi_horizon_trend",
        trend_short_bars=3,
        trend_medium_bars=12,
        trend_long_bars=36,
        max_hold_minutes=60,
    ),
    "Opening breakout research": SandboxConfig(
        strategy_name="opening_breakout",
        opening_range_minutes=30,
        breakout_buffer_bps=3.0,
        no_trade_open_minutes=30,
        max_hold_minutes=90,
    ),
    "Agreement ensemble research": SandboxConfig(
        strategy_name="conservative_ensemble",
        ensemble_min_votes=2,
        max_hold_minutes=45,
    ),
}


class SandboxWidget(QWidget):
    """Historical virtual research surface. It deliberately receives no Broker."""

    def __init__(self, store: AuditStore, allow_remote_data: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.allow_remote_data = allow_remote_data
        self._remote_acknowledged = False
        self.bundle: HistoricalBundle | None = None
        self.result: SandboxResult | None = None
        self.csv_path: Path | None = None
        self._replay_index = 0
        self._replay_timer = QTimer(self)
        self._replay_timer.timeout.connect(self._advance_replay)
        self._build_ui()
        self._apply_config(load_sandbox_config())
        self.set_remote_data_allowed(allow_remote_data)
        self._source_changed()
        self._refresh_saved_runs()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        banner = QLabel(
            "SANDBOX ONLY — TQQQS/SQQQS are fictional aliases. Every fill is virtual; this tab has "
            "no broker object and cannot submit, review, cancel, or modify a Robinhood order."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "background:#15324a;color:#8fd3ff;border:1px solid #3478a4;border-radius:7px;"
            "padding:9px;font-weight:650"
        )
        outer.addWidget(banner)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        config_scroll.setMinimumWidth(470)
        panel = QWidget()
        layout = QVBoxLayout(panel)

        data = QGroupBox("Dataset and provenance")
        form = QFormLayout(data)
        self.source = QComboBox()
        self.source.addItem("Community remote: 1-minute (max 7 days)", ("yahoo", "1m", 7))
        self.source.addItem("Community remote: 5-minute (max 60 days)", ("yahoo", "5m", 60))
        self.source.addItem("Community remote: hourly (max 730 days)", ("yahoo", "60m", 730))
        self.source.addItem(
            "Community remote: full shared history (daily)",
            ("yahoo-full", "1d", full_history_calendar_days()),
        )
        self.source.addItem("Import aligned CSV", ("csv", "1m", 3650))
        self.source.addItem("Offline deterministic scenario", ("demo", "1m", 3650))
        self.source.currentIndexChanged.connect(self._source_changed)
        self.lookback = self._integer(1, 7)
        self.lookback.setSuffix(" days")
        self.csv_button = QPushButton("Choose CSV…")
        self.csv_button.clicked.connect(self._choose_csv)
        self.csv_label = QLabel("No file selected")
        self.csv_label.setWordWrap(True)
        csv_row = QHBoxLayout()
        csv_row.addWidget(self.csv_button)
        csv_row.addWidget(self.csv_label, 1)
        self.quality = QLabel("Run a replay to calculate data quality and SHA-256 provenance.")
        self.quality.setWordWrap(True)
        form.addRow("Source", self.source)
        form.addRow("Calendar lookback", self.lookback)
        form.addRow("Long-history CSV", csv_row)
        form.addRow("Integrity", self.quality)
        layout.addWidget(data)

        preset_group = QGroupBox("Experiment")
        preset_form = QFormLayout(preset_group)
        self.preset = QComboBox()
        self.preset.addItems(PRESETS)
        apply_preset = QPushButton("Apply")
        apply_preset.clicked.connect(lambda: self._apply_config(PRESETS[self.preset.currentText()]))
        row = QHBoxLayout()
        row.addWidget(self.preset)
        row.addWidget(apply_preset)
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Hypothesis / run note (saved with receipt)")
        self.saved_runs = QComboBox()
        load_saved = QPushButton("Load config")
        load_saved.clicked.connect(self._load_saved_config)
        saved_row = QHBoxLayout()
        saved_row.addWidget(self.saved_runs)
        saved_row.addWidget(load_saved)
        preset_form.addRow("Preset", row)
        preset_form.addRow("Run note", self.notes)
        preset_form.addRow("Saved runs", saved_row)
        layout.addWidget(preset_group)

        execution = QGroupBox("Virtual execution model")
        execution_form = QFormLayout(execution)
        self.initial_cash = self._decimal(1, 1_000_000, "$", 2)
        self.order_notional = self._decimal(0.01, 1_000_000, "$", 2)
        self.slippage = self._decimal(0, 200, decimals=1, suffix=" bps")
        self.base_spread = self._decimal(0, 200, decimals=1, suffix=" bps")
        self.vol_spread = self._decimal(0, 10, decimals=2, suffix=" × range")
        self.commission = self._decimal(0, 100, "$", 2)
        self.latency = self._integer(0, 20)
        self.latency.setSuffix(" bars")
        self.fill_fraction = self._decimal(1, 100, decimals=1, suffix=" %")
        self.rejection = self._decimal(0, 100, decimals=1, suffix=" %")
        self.volume_participation = self._decimal(0.01, 100, decimals=2, suffix=" %")
        for title, widget in (
            ("Starting cash", self.initial_cash),
            ("Order cap", self.order_notional),
            ("Slippage / side", self.slippage),
            ("Base spread", self.base_spread),
            ("Volatility spread", self.vol_spread),
            ("Commission / order", self.commission),
            ("Extra latency", self.latency),
            ("Fill fraction", self.fill_fraction),
            ("Rejection probability", self.rejection),
            ("Max volume participation", self.volume_participation),
        ):
            execution_form.addRow(title, widget)
        layout.addWidget(execution)

        signal = QGroupBox("Signal policy")
        signal_form = QFormLayout(signal)
        self.strategy_name = QComboBox()
        for key, label in STRATEGY_NAMES.items():
            self.strategy_name.addItem(label, key)
        self.warmup = self._integer(5, 1000)
        self.fast_ema = self._integer(1, 500)
        self.slow_ema = self._integer(2, 1000)
        self.threshold = self._decimal(0.1, 200, decimals=1, suffix=" bps")
        self.momentum = self._integer(1, 200)
        self.momentum.setSuffix(" bars")
        self.trend_short = self._integer(1, 200)
        self.trend_medium = self._integer(2, 500)
        self.trend_long = self._integer(3, 1000)
        self.close_momentum = self._decimal(0.1, 500, decimals=1, suffix=" bps")
        self.opening_range = self._integer(5, 120)
        self.opening_range.setSuffix(" min")
        self.breakout_buffer = self._decimal(0, 200, decimals=1, suffix=" bps")
        self.ensemble_votes = self._integer(1, 4)
        for title, widget in (
            ("Research strategy", self.strategy_name),
            ("Warm-up", self.warmup),
            ("Fast EMA", self.fast_ema),
            ("Slow EMA", self.slow_ema),
            ("Trend threshold", self.threshold),
            ("Momentum horizon", self.momentum),
            ("Trend short horizon", self.trend_short),
            ("Trend medium horizon", self.trend_medium),
            ("Trend long horizon", self.trend_long),
            ("Close momentum", self.close_momentum),
            ("Opening range", self.opening_range),
            ("Breakout buffer", self.breakout_buffer),
            ("Ensemble votes", self.ensemble_votes),
        ):
            signal_form.addRow(title, widget)
        layout.addWidget(signal)

        risk = QGroupBox("Exits and risk budget")
        risk_form = QFormLayout(risk)
        self.stop = self._decimal(0.01, 50, decimals=2, suffix=" %")
        self.take_profit = self._decimal(0.01, 100, decimals=2, suffix=" %")
        self.max_hold = self._integer(1, 10000)
        self.max_hold.setSuffix(" min")
        self.max_entries = self._integer(1, 100)
        self.max_entries.setSuffix(" / day")
        self.no_trade_open = self._integer(0, 120)
        self.no_trade_close = self._integer(0, 120)
        self.risk_budget = self._decimal(0.01, 100, decimals=2, suffix=" % equity")
        self.max_exposure = self._decimal(0.01, 100, decimals=2, suffix=" % equity")
        self.daily_loss = self._decimal(0.01, 100, decimals=2, suffix=" %")
        self.loss_pause = self._integer(1, 20)
        self.vol_target = self._decimal(0, 500, decimals=1, suffix=" % annualized")
        self.force_flat = QCheckBox("Close virtual positions at every session end")
        for title, widget in (
            ("Hard stop", self.stop),
            ("Take-profit", self.take_profit),
            ("Maximum hold", self.max_hold),
            ("Maximum entries", self.max_entries),
            ("Skip after open", self.no_trade_open),
            ("Skip before close", self.no_trade_close),
            ("Risk budget", self.risk_budget),
            ("Maximum exposure", self.max_exposure),
            ("Daily loss pause", self.daily_loss),
            ("Pause after losses", self.loss_pause),
            ("Volatility target", self.vol_target),
            ("End handling", self.force_flat),
        ):
            risk_form.addRow(title, widget)
        layout.addWidget(risk)

        buttons = QGridLayout()
        self.run_button = QPushButton("Run sandbox")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(lambda: asyncio.create_task(self._run()))
        self.compare_button = QPushButton("Compare presets")
        self.compare_button.clicked.connect(lambda: asyncio.create_task(self._compare()))
        self.evidence_button = QPushButton("Run full evidence lab")
        self.evidence_button.clicked.connect(lambda: asyncio.create_task(self._evidence()))
        self.action_lab_button = QPushButton("Train 9-action lab")
        self.action_lab_button.clicked.connect(lambda: asyncio.create_task(self._action_lab()))
        self.export_button = QPushButton("Export fills CSV")
        self.export_button.clicked.connect(self._export)
        buttons.addWidget(self.run_button, 0, 0)
        buttons.addWidget(self.compare_button, 0, 1)
        buttons.addWidget(self.evidence_button, 1, 0)
        buttons.addWidget(self.export_button, 1, 1)
        buttons.addWidget(self.action_lab_button, 2, 0, 1, 2)
        layout.addLayout(buttons)
        layout.addStretch()
        config_scroll.setWidget(panel)
        splitter.addWidget(config_scroll)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        metrics = QGridLayout()
        self.metric_labels: dict[str, QLabel] = {}
        names = [
            ("final", "Final equity"),
            ("pnl", "Net P/L"),
            ("return", "Return"),
            ("drawdown", "Max drawdown"),
            ("trades", "Round trips"),
            ("win_rate", "Win rate"),
            ("pf", "Profit factor"),
            ("expectancy", "Expectancy"),
            ("sharpe", "Sharpe"),
            ("sortino", "Sortino"),
            ("exposure", "Exposure"),
            ("cost", "Execution cost"),
        ]
        for index, (key, title) in enumerate(names):
            card = QGroupBox(title)
            card_layout = QVBoxLayout(card)
            label = QLabel("—")
            label.setStyleSheet("font-size:13pt;font-weight:650")
            card_layout.addWidget(label)
            metrics.addWidget(card, index // 6, index % 6)
            self.metric_labels[key] = label
        results_layout.addLayout(metrics)

        self.tabs = QTabWidget()
        replay = QWidget()
        replay_layout = QVBoxLayout(replay)
        self.chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.chart.setBackground("#0e1720")
        self.chart.showGrid(x=True, y=True, alpha=0.18)
        self.chart.setLabel("left", "Virtual equity", units="$")
        self.equity_curve = self.chart.plot(pen=pg.mkPen("#8fd3ff", width=2))
        self.cursor_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#f2c14e"))
        self.chart.addItem(self.cursor_line, ignoreBounds=True)
        replay_layout.addWidget(self.chart, 3)

        self.trade_chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.trade_chart.setBackground("#0e1720")
        self.trade_chart.showGrid(x=True, y=True, alpha=0.18)
        self.trade_chart.setLabel("left", "Virtual market price", units="$")
        self.trade_chart.setXLink(self.chart)
        self.trade_chart.addLegend(offset=(8, 8))
        self.tqqqs_price_curve = self.trade_chart.plot(name="TQQQS price", pen=pg.mkPen("#8fd3ff", width=1.7))
        self.sqqqs_price_curve = self.trade_chart.plot(
            name="SQQQS price",
            pen=pg.mkPen("#b9c2cc", width=1.5, style=Qt.PenStyle.DashLine),
        )
        for curve in (self.tqqqs_price_curve, self.sqqqs_price_curve):
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method="peak")

        def marker_tip(_x, _y, data):
            return data.get("tooltip", "Virtual fill") if data else "Virtual fill"

        self.buy_markers = pg.ScatterPlotItem(
            name="Buy",
            symbol="t1",
            size=13,
            pen=pg.mkPen("#d9f1ff", width=1.5),
            brush=pg.mkBrush("#198fbd"),
            hoverable=True,
            tip=marker_tip,
        )
        self.profitable_sale_markers = pg.ScatterPlotItem(
            name="Profitable sale",
            symbol="t",
            size=13,
            pen=pg.mkPen("#fff1b8", width=1.5),
            brush=pg.mkBrush("#f2c14e"),
            hoverable=True,
            tip=marker_tip,
        )
        self.losing_sale_markers = pg.ScatterPlotItem(
            name="Losing sale",
            symbol="x",
            size=13,
            pen=pg.mkPen("#ff9eb5", width=2.2),
            brush=None,
            hoverable=True,
            tip=marker_tip,
        )
        self.flat_sale_markers = pg.ScatterPlotItem(
            name="Flat sale",
            symbol="s",
            size=10,
            pen=pg.mkPen("#f3f5f7", width=1.5),
            brush=None,
            hoverable=True,
            tip=marker_tip,
        )
        for markers in (
            self.buy_markers,
            self.profitable_sale_markers,
            self.losing_sale_markers,
            self.flat_sale_markers,
        ):
            markers.sigClicked.connect(self._trade_marker_clicked)
            self.trade_chart.addItem(markers)
        self.trade_cursor_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#f2c14e"))
        self.trade_chart.addItem(self.trade_cursor_line, ignoreBounds=True)
        self.sales_summary = QLabel(
            "Run a replay to plot virtual purchases and sales. Hover or click any marker for details."
        )
        self.sales_summary.setWordWrap(True)
        replay_layout.addWidget(self.trade_chart, 3)
        replay_layout.addWidget(self.sales_summary)
        controls = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.clicked.connect(self._toggle_replay)
        self.speed = QComboBox()
        self.speed.addItem("1×", 100)
        self.speed.addItem("5×", 30)
        self.speed.addItem("20×", 5)
        self.replay_slider = QSlider(Qt.Orientation.Horizontal)
        self.replay_slider.valueChanged.connect(self._seek_replay)
        controls.addWidget(self.play)
        controls.addWidget(self.speed)
        controls.addWidget(self.replay_slider, 1)
        replay_layout.addLayout(controls)
        fill_split = QSplitter(Qt.Orientation.Horizontal)
        self.fills_table = self._table(
            ["Time", "Alias", "Side", "Qty", "Fill", "Filled", "Cost", "Realized P/L", "Reason"]
        )
        self.fills_table.itemSelectionChanged.connect(self._inspect_fill)
        self.inspector = QPlainTextEdit()
        self.inspector.setReadOnly(True)
        self.inspector.setPlaceholderText("Select a virtual fill to inspect its assumptions.")
        fill_split.addWidget(self.fills_table)
        fill_split.addWidget(self.inspector)
        fill_split.setStretchFactor(0, 3)
        fill_split.setStretchFactor(1, 1)
        replay_layout.addWidget(fill_split, 2)
        self.tabs.addTab(replay, "Replay")

        self.compare_table = self._table(
            ["Configuration", "Return", "Drawdown", "Profit factor", "Trades", "Exposure", "Costs"]
        )
        self.tabs.addTab(self.compare_table, "Comparison")
        sensitivity = QWidget()
        sensitivity_layout = QVBoxLayout(sensitivity)
        self.sensitivity_table = self._table(["Strategy", "Candidate", "Return", "Drawdown", "PF", "Trades"])
        sensitivity_layout.addWidget(self.sensitivity_table)
        self.random_label = QLabel("Random-entry control not run.")
        sensitivity_layout.addWidget(self.random_label)
        self.tabs.addTab(sensitivity, "Sensitivity")
        validation = QWidget()
        validation_layout = QVBoxLayout(validation)
        self.walk_table = self._table(
            [
                "Train",
                "Test",
                "Selected strategy",
                "Train return",
                "Test return",
                "Test DD",
                "Test PF",
                "Test trades",
                "Test expectancy",
            ]
        )
        self.gates_table = self._table(["Gate", "Status", "Observed", "Requirement"])
        self.promotion_label = QLabel("PROMOTION: SHADOW_ONLY until every evidence gate passes.")
        self.promotion_label.setStyleSheet("font-size:14pt;font-weight:700;color:#f2c14e")
        validation_layout.addWidget(self.promotion_label)
        validation_layout.addWidget(self.walk_table)
        validation_layout.addWidget(self.gates_table)
        self.tabs.addTab(validation, "Walk-forward & gates")

        action_lab = QWidget()
        action_layout = QVBoxLayout(action_lab)
        action_intro = QLabel(
            "Offline research only. The action vector is (T,S), each in {-1,0,+1}: sell, hold, buy. "
            "State-dependent masks prevent a long-only sandbox from selling inventory it does not own."
        )
        action_intro.setWordWrap(True)
        action_layout.addWidget(action_intro)
        self.action_matrix = QTableWidget(3, 3)
        self.action_matrix.setHorizontalHeaderLabels(["S = -1", "S = 0", "S = +1"])
        self.action_matrix.setVerticalHeaderLabels(["T = -1", "T = 0", "T = +1"])
        self.action_matrix.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.action_matrix.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.action_matrix.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for action in ALL_PAIR_ACTIONS:
            row, column = int(action.t) + 1, int(action.s) + 1
            item = QTableWidgetItem(f"ID {action.action_id}\n{action.label}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.action_matrix.setItem(row, column, item)
        self.action_matrix.setMinimumHeight(210)
        action_layout.addWidget(self.action_matrix)
        self.action_result_label = QLabel(
            "Select the full shared-history daily source, then run the nine-action lab. "
            "Training and holdout periods remain chronologically separated."
        )
        self.action_result_label.setWordWrap(True)
        action_layout.addWidget(self.action_result_label)
        self.daily_benchmark_table = self._table(
            ["Daily research baseline", "Return", "CAGR", "Max DD", "Sharpe", "Avg exposure", "Turnover"]
        )
        action_layout.addWidget(self.daily_benchmark_table)
        action_split = QSplitter(Qt.Orientation.Vertical)
        self.action_summary_table = self._table(["Action", "Commands", "Directional impulse", "Count"])
        self.action_audit_table = self._table(
            ["Time", "Action", "Before T/S", "After T/S", "Reward", "Equity"]
        )
        action_split.addWidget(self.action_summary_table)
        action_split.addWidget(self.action_audit_table)
        action_split.setStretchFactor(0, 1)
        action_split.setStretchFactor(1, 3)
        action_layout.addWidget(action_split, 1)
        self.action_tab_index = self.tabs.addTab(action_lab, "9-action lab")
        results_layout.addWidget(self.tabs, 1)
        self.status = QLabel("Configure a virtual replay. Robinhood is not required.")
        self.status.setWordWrap(True)
        results_layout.addWidget(self.status)
        splitter.addWidget(results)
        splitter.setCollapsible(0, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 920])
        outer.addWidget(splitter)

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        return table

    def _integer(self, minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        return widget

    def _decimal(
        self, minimum: float, maximum: float, prefix: str = "", decimals: int = 2, suffix: str = ""
    ) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setPrefix(prefix)
        widget.setSuffix(suffix)
        return widget

    def _source_changed(self) -> None:
        kind, _, maximum = self.source.currentData()
        self.lookback.setMaximum(maximum)
        self.lookback.setEnabled(kind != "yahoo-full")
        self.lookback.setValue(maximum if kind == "yahoo-full" else min(self.lookback.value(), maximum))
        self.csv_button.setEnabled(kind == "csv")

    def set_remote_data_allowed(self, allowed: bool) -> None:
        self.allow_remote_data = allowed
        for index in range(self.source.count()):
            item = self.source.model().item(index)
            if item is not None and str(self.source.itemData(index)[0]).startswith("yahoo"):
                item.setEnabled(allowed)
        if not allowed and str(self.source.currentData()[0]).startswith("yahoo"):
            for index in range(self.source.count()):
                if self.source.itemData(index)[0] == "demo":
                    self.source.setCurrentIndex(index)
                    break
        if not allowed:
            self.quality.setText(
                "Remote community data is disabled. Use the deterministic scenario or import your own lawful CSV; "
                "enable remote data deliberately in Settings."
            )

    def _choose_csv(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Aligned QQQ/TQQQ/SQQQ history", "", "CSV (*.csv)")
        if filename:
            self.csv_path = Path(filename)
            self.csv_label.setText(self.csv_path.name)

    def _apply_config(self, config: SandboxConfig) -> None:
        strategy_index = self.strategy_name.findData(config.strategy_name)
        if strategy_index >= 0:
            self.strategy_name.setCurrentIndex(strategy_index)
        values = {
            self.lookback: config.lookback_days,
            self.initial_cash: config.initial_cash,
            self.order_notional: config.order_notional,
            self.slippage: config.slippage_bps,
            self.base_spread: config.base_spread_bps,
            self.vol_spread: config.spread_volatility_multiplier,
            self.commission: config.commission_per_order,
            self.latency: config.latency_bars,
            self.fill_fraction: config.fill_fraction_pct,
            self.rejection: config.rejection_rate_pct,
            self.volume_participation: config.max_volume_participation_pct,
            self.warmup: config.warmup_bars,
            self.fast_ema: config.fast_ema,
            self.slow_ema: config.slow_ema,
            self.threshold: config.trend_threshold_bps,
            self.momentum: config.momentum_bars,
            self.trend_short: config.trend_short_bars,
            self.trend_medium: config.trend_medium_bars,
            self.trend_long: config.trend_long_bars,
            self.close_momentum: config.close_momentum_bps,
            self.opening_range: config.opening_range_minutes,
            self.breakout_buffer: config.breakout_buffer_bps,
            self.ensemble_votes: config.ensemble_min_votes,
            self.stop: config.hard_stop_pct * 100,
            self.take_profit: config.take_profit_pct * 100,
            self.max_hold: config.max_hold_minutes,
            self.max_entries: config.max_entries_per_day,
            self.no_trade_open: config.no_trade_open_minutes,
            self.no_trade_close: config.no_trade_close_minutes,
            self.risk_budget: config.risk_budget_pct * 100,
            self.max_exposure: config.max_exposure_pct * 100,
            self.daily_loss: config.max_daily_loss_pct * 100,
            self.loss_pause: config.max_consecutive_losses,
            self.vol_target: config.volatility_target_pct * 100,
        }
        for widget, value in values.items():
            widget.setValue(value)
        self.force_flat.setChecked(config.force_flat_at_end)

    def _config(self) -> SandboxConfig:
        return SandboxConfig(
            lookback_days=self.lookback.value(),
            initial_cash=self.initial_cash.value(),
            order_notional=self.order_notional.value(),
            slippage_bps=self.slippage.value(),
            base_spread_bps=self.base_spread.value(),
            spread_volatility_multiplier=self.vol_spread.value(),
            commission_per_order=self.commission.value(),
            latency_bars=self.latency.value(),
            fill_fraction_pct=self.fill_fraction.value(),
            rejection_rate_pct=self.rejection.value(),
            max_volume_participation_pct=self.volume_participation.value(),
            strategy_name=self.strategy_name.currentData(),
            warmup_bars=self.warmup.value(),
            fast_ema=self.fast_ema.value(),
            slow_ema=self.slow_ema.value(),
            trend_threshold_bps=self.threshold.value(),
            momentum_bars=self.momentum.value(),
            trend_short_bars=self.trend_short.value(),
            trend_medium_bars=self.trend_medium.value(),
            trend_long_bars=self.trend_long.value(),
            close_momentum_bps=self.close_momentum.value(),
            opening_range_minutes=self.opening_range.value(),
            breakout_buffer_bps=self.breakout_buffer.value(),
            ensemble_min_votes=self.ensemble_votes.value(),
            hard_stop_pct=self.stop.value() / 100,
            take_profit_pct=self.take_profit.value() / 100,
            max_hold_minutes=self.max_hold.value(),
            max_entries_per_day=self.max_entries.value(),
            no_trade_open_minutes=self.no_trade_open.value(),
            no_trade_close_minutes=self.no_trade_close.value(),
            risk_budget_pct=self.risk_budget.value() / 100,
            max_exposure_pct=self.max_exposure.value() / 100,
            max_daily_loss_pct=self.daily_loss.value() / 100,
            max_consecutive_losses=self.loss_pause.value(),
            volatility_target_pct=self.vol_target.value() / 100,
            force_flat_at_end=self.force_flat.isChecked(),
        )

    async def _load_bundle(self, config: SandboxConfig) -> HistoricalBundle:
        kind, interval, _ = self.source.currentData()
        if kind in {"yahoo", "yahoo-full"}:
            if not self.allow_remote_data:
                raise RuntimeError("Community remote market data is disabled in Settings")
            if not self._remote_acknowledged:
                answer = QMessageBox.question(
                    self,
                    "Use unsupported community market data?",
                    "This research adapter contacts Yahoo's unsupported chart endpoint and sends only the "
                    "requested symbols, dates, and interval. No broker or account data is sent. Availability, "
                    "licensing, schema, and accuracy are not guaranteed. Continue for this app session?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    raise RuntimeError("Remote market-data request declined; no network request was made")
                self._remote_acknowledged = True
            provider = HistoricalDataProvider()
            return (
                await provider.fetch_full_daily()
                if kind == "yahoo-full"
                else await provider.fetch(config.lookback_days, interval)
            )
        if kind == "csv":
            if not self.csv_path:
                raise ValueError("Choose a combined QQQ/TQQQ/SQQQ CSV first")
            return await asyncio.to_thread(load_csv_history, self.csv_path, interval)
        return await asyncio.to_thread(deterministic_demo, config.lookback_days)

    async def _run(self) -> None:
        self._busy(True, "Loading and replaying…")
        try:
            config = self._config()
            config.validate()
            save_sandbox_config(config)
            self.bundle = await self._load_bundle(config)
            self.result = await asyncio.to_thread(SandboxReplayEngine(config).run, self.bundle)
            self._save_result(config, self.result)
            self._show_result(self.result)
            self.tabs.setCurrentIndex(0)
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    async def _compare(self) -> None:
        self._busy(True, "Comparing presets on one identical dataset…")
        try:
            config = self._config()
            config.validate()
            self.bundle = self.bundle or await self._load_bundle(config)
            rows = await asyncio.to_thread(compare_configs, self.bundle, PRESETS)
            self.compare_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                values = [
                    row.name,
                    f"{row.return_pct:+.2f}%",
                    f"{row.max_drawdown_pct:.2f}%",
                    f"{row.profit_factor:.2f}",
                    str(row.round_trips),
                    f"{row.exposure_pct:.1f}%",
                    f"${row.total_cost:.2f}",
                ]
                self._set_row(self.compare_table, row_index, values, row.return_pct)
            self.tabs.setCurrentIndex(1)
            self.status.setText(
                "Preset comparison uses the exact same immutable dataset hash: " + self.bundle.dataset_hash
            )
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    async def _evidence(self) -> None:
        self._busy(True, "Running sensitivity, costs, random control, and walk-forward…")
        try:
            config = self._config()
            config.validate()
            self.bundle = self.bundle or await self._load_bundle(config)
            base = await asyncio.to_thread(SandboxReplayEngine(config).run, self.bundle)
            candidates = candidate_grid(config)
            points = await asyncio.to_thread(parameter_sweep, self.bundle, candidates)
            self.store.record_research_trials(
                self.bundle.dataset_hash,
                [
                    {
                        "trial_fingerprint": strategy_fingerprint(candidate, self.bundle.interval),
                        "config": asdict(candidate),
                        "metrics": asdict(point),
                    }
                    for candidate, point in zip(candidates, points, strict=True)
                ],
            )
            total_trial_count = self.store.research_trial_count(self.bundle.dataset_hash)
            stressed = await asyncio.to_thread(cost_stress, self.bundle, config)
            random_control = await asyncio.to_thread(
                random_entry_control, self.bundle, config, base.return_pct
            )
            sessions = self.bundle.quality.sessions if self.bundle.quality else 0
            walk = None
            if sessions >= 15:
                test_sessions = max(1, min(5, sessions // 7))
                train_sessions = max(5, min(20, sessions - 5 * test_sessions))
                if train_sessions + test_sessions <= sessions:
                    walk = await asyncio.to_thread(
                        walk_forward, self.bundle, candidates, train_sessions, test_sessions, test_sessions
                    )
            report = promotion_report(
                self.bundle,
                config,
                base,
                points,
                stressed,
                walk,
                random_control,
                total_trial_count=total_trial_count,
            )
            self.store.record_research_promotion(
                dataset_hash=report.dataset_hash,
                strategy_fingerprint=report.strategy_fingerprint,
                policy_version=report.policy_version,
                status=report.status,
                source=self.bundle.source,
                replay_end=self.bundle.end.isoformat(),
                gates=[asdict(gate) for gate in report.gates],
                risk_envelope=tested_risk_envelope(config),
            )
            self._show_evidence(points, walk, report, random_control)
            self.store.receipt(
                "sandbox_evidence",
                f"Evidence lab status: {report.status}",
                {
                    "dataset_hash": report.dataset_hash,
                    "gates": [asdict(g) for g in report.gates],
                    "note": self.notes.text().strip(),
                    "registered_trial_count": total_trial_count,
                },
                "warning" if not report.passed else "info",
            )
            self.tabs.setCurrentIndex(3)
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    async def _action_lab(self) -> None:
        self._busy(True, "Training the nine-action policy on a chronological daily split…")
        try:
            config = self._config()
            config.validate()
            self.bundle = await self._load_bundle(config)
            if self.bundle.interval != "1d":
                raise ValueError("Select Community remote: full shared history (daily) for the action lab")
            training_config = OfflineTrainingConfig()
            result = await asyncio.to_thread(train_offline_action_policy, self.bundle, training_config)
            benchmarks = await asyncio.to_thread(evaluate_daily_benchmarks, self.bundle)
            self.action_result_label.setText(
                f"Chronological holdout {result.test_start} → {result.test_end} • "
                f"return {result.test_return_pct:+.2f}% • max drawdown {result.test_max_drawdown_pct:.2f}% • "
                f"{result.training_rows:,} training transitions • {result.test_rows:,} unseen transitions • "
                f"{result.state_count} learned states. Holdout benchmarks: QQQ {result.qqq_holdout_pct:+.1f}%, "
                f"TQQQ {result.tqqq_holdout_pct:+.1f}%, SQQQ {result.sqqq_holdout_pct:+.1f}%. "
                "Research only; no live mode was enabled."
            )
            self.daily_benchmark_table.setRowCount(len(benchmarks))
            for row, benchmark in enumerate(benchmarks):
                self._set_row(
                    self.daily_benchmark_table,
                    row,
                    [
                        benchmark.name,
                        f"{benchmark.return_pct:+.1f}%",
                        f"{benchmark.cagr_pct:+.1f}%",
                        f"{benchmark.max_drawdown_pct:.1f}%",
                        f"{benchmark.sharpe:.2f}",
                        f"{benchmark.average_exposure_pct:.1f}%",
                        f"{benchmark.turnover:.1f}×",
                    ],
                    benchmark.return_pct,
                )
            self.action_summary_table.setRowCount(9)
            for row, action in enumerate(ALL_PAIR_ACTIONS):
                self._set_row(
                    self.action_summary_table,
                    row,
                    [
                        f"ID {action.action_id} {action.label}",
                        f"T {action.t.name.lower()} / S {action.s.name.lower()}",
                        f"{action.net_directional_impulse:+d}",
                        str(result.action_counts.get(action.action_id, 0)),
                    ],
                )
            rows = result.audit_rows[-500:]
            self.action_audit_table.setRowCount(len(rows))
            for row, event in enumerate(rows):
                self._set_row(
                    self.action_audit_table,
                    row,
                    [
                        event.timestamp[:16],
                        f"ID {event.action_id} ({event.action_t:+d},{event.action_s:+d})",
                        f"{event.before_t}/{event.before_s}",
                        f"{event.after_t}/{event.after_s}",
                        f"{event.reward_pct:+.3f}%",
                        f"{event.equity:.4f}×",
                    ],
                    event.reward_pct,
                )
            self.store.receipt(
                "sandbox_action_lab",
                "Nine-action offline research completed",
                {
                    "dataset_hash": result.dataset_hash,
                    "training_rows": result.training_rows,
                    "test_rows": result.test_rows,
                    "test_return_pct": result.test_return_pct,
                    "test_max_drawdown_pct": result.test_max_drawdown_pct,
                    "holdout_benchmarks_pct": {
                        "QQQ": result.qqq_holdout_pct,
                        "TQQQ": result.tqqq_holdout_pct,
                        "SQQQ": result.sqqq_holdout_pct,
                    },
                    "action_counts": result.action_counts,
                    "training_config": asdict(training_config),
                    "policy": result.policy,
                    "daily_benchmarks": [asdict(benchmark) for benchmark in benchmarks],
                    "warnings": result.warnings,
                },
                "warning",
            )
            self.tabs.setCurrentIndex(self.action_tab_index)
            self.status.setText("Nine-action research receipt saved. This result cannot unlock live trading.")
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    def _show_evidence(self, points, walk, report, control) -> None:
        ordered = sorted(points, key=lambda item: (item.strategy_name, item.candidate))
        self.sensitivity_table.setRowCount(len(ordered))
        for row, point in enumerate(ordered):
            values = [
                STRATEGY_NAMES.get(point.strategy_name, point.strategy_name),
                point.candidate,
                f"{point.return_pct:+.2f}%",
                f"{point.max_drawdown_pct:.2f}%",
                f"{point.profit_factor:.2f}",
                str(point.round_trips),
            ]
            self._set_row(self.sensitivity_table, row, values, point.return_pct)
        self.random_label.setText(
            f"Random-entry control ({control.trials} trials): median {control.median_return_pct:+.2f}%, "
            f"10–90% {control.percentile_10:+.2f}% to {control.percentile_90:+.2f}%; "
            f"strategy at percentile {control.strategy_percentile:.1f}."
        )
        folds = walk.folds if walk else []
        self.walk_table.setRowCount(len(folds))
        for row, fold in enumerate(folds):
            values = [
                f"{fold.train_start} → {fold.train_end}",
                f"{fold.test_start} → {fold.test_end}",
                STRATEGY_NAMES.get(fold.selected.strategy_name, fold.selected.strategy_name),
                f"{fold.train_return_pct:+.2f}%",
                f"{fold.test_return_pct:+.2f}%",
                f"{fold.test_drawdown_pct:.2f}%",
                f"{fold.test_profit_factor:.2f}",
                str(fold.test_round_trips),
                f"${fold.test_expectancy:+.4f}",
            ]
            self._set_row(self.walk_table, row, values, fold.test_return_pct)
        self.gates_table.setRowCount(len(report.gates))
        for row, gate in enumerate(report.gates):
            self._set_row(
                self.gates_table,
                row,
                [gate.name, "PASS" if gate.passed else "FAIL", gate.observed, gate.requirement],
                1 if gate.passed else -1,
            )
        self.promotion_label.setText(f"PROMOTION: {report.status} — no mode is automatically enabled.")
        self.promotion_label.setStyleSheet(
            "font-size:14pt;font-weight:700;color:" + ("#00e507" if report.passed else "#f2c14e")
        )
        self.status.setText(
            f"Evidence receipt saved • dataset {report.dataset_hash[:16]}… • "
            f"{sum(g.passed for g in report.gates)}/{len(report.gates)} gates passed."
        )

    def _save_result(self, config: SandboxConfig, result: SandboxResult) -> None:
        self.store.record_sandbox_run(
            result.run_id,
            result.source,
            result.start.isoformat(),
            result.end.isoformat(),
            {
                **asdict(config),
                "note": self.notes.text().strip(),
                "dataset_hash": self.bundle.dataset_hash if self.bundle else "",
            },
            result.metrics(),
            [fill.as_dict() for fill in result.fills],
            [event.as_dict() for event in result.execution_events],
        )
        self._refresh_saved_runs()

    def _show_result(self, result: SandboxResult) -> None:
        values = {
            "final": f"${result.final_equity:,.2f}",
            "pnl": f"${result.net_pnl:+,.2f}",
            "return": f"{result.return_pct:+.2f}%",
            "drawdown": f"{result.max_drawdown_pct:.2f}%",
            "trades": str(result.round_trips),
            "win_rate": f"{result.win_rate:.1f}%",
            "pf": f"{result.profit_factor:.2f}",
            "expectancy": f"${result.expectancy:+.2f}",
            "sharpe": f"{result.sharpe:+.2f}",
            "sortino": f"{result.sortino:+.2f}",
            "exposure": f"{result.exposure_pct:.1f}%",
            "cost": f"${result.total_execution_cost:.2f}",
        }
        for key, value in values.items():
            self.metric_labels[key].setText(value)
        points = result.equity_curve
        self.equity_curve.setData([p.timestamp.timestamp() for p in points], [p.equity for p in points])
        self._show_trade_timeline(result)
        self.replay_slider.setRange(0, max(0, len(points) - 1))
        self.replay_slider.setValue(0)
        if points:
            self._seek_replay(0)
        self.fills_table.setRowCount(len(result.fills))
        for row, fill in enumerate(result.fills):
            values = [
                fill.timestamp.astimezone().strftime("%m/%d %I:%M %p"),
                fill.symbol,
                fill.side.upper(),
                f"{fill.requested_quantity:.6f}",
                f"${fill.price:,.2f}",
                f"{fill.fill_fraction:.0%}",
                f"${fill.execution_cost:,.4f}",
                f"${fill.realized_pnl:+,.2f}" if fill.realized_pnl is not None else "—",
                fill.reason,
            ]
            self._set_row(self.fills_table, row, values, fill.realized_pnl)
        quality = self.bundle.quality if self.bundle else None
        if quality:
            self.quality.setText(
                f"{quality.aligned_bars:,} aligned {quality.interval} bars • {quality.sessions} sessions • "
                f"{quality.missing_intervals} missing • {quality.duplicate_timestamps} duplicates • "
                f"{quality.zero_volume_bars} zero-volume • SHA-256 {quality.dataset_hash}"
            )
        self.status.setText(
            f"Saved virtual run {result.run_id[:8]} • ending position {result.ending_position or 'cash'} • "
            f"{result.source}. {' '.join(result.warnings)}"
        )

    def _show_trade_timeline(self, result: SandboxResult) -> None:
        frames = self.bundle.frames if self.bundle else []

        def session_broken_prices(field: str) -> tuple[list[float], list[float]]:
            timestamps = []
            prices = []
            previous_date = None
            for frame in frames:
                session_date = frame.start.date()
                if previous_date is not None and session_date != previous_date:
                    timestamps.append(frame.start.timestamp())
                    prices.append(float("nan"))
                timestamps.append(frame.start.timestamp())
                prices.append(getattr(frame, field).close)
                previous_date = session_date
            return timestamps, prices

        tqqqs_time, tqqqs_price = session_broken_prices("tqqq")
        sqqqs_time, sqqqs_price = session_broken_prices("sqqq")
        self.tqqqs_price_curve.setData(tqqqs_time, tqqqs_price, connect="finite")
        self.sqqqs_price_curve.setData(sqqqs_time, sqqqs_price, connect="finite")
        groups = {"buy": [], "profit": [], "loss": [], "flat": []}
        sell_fills = []
        for row, fill in enumerate(result.fills):
            realized = fill.realized_pnl
            outcome = ""
            if fill.side == "sell":
                sell_fills.append(fill)
                outcome = f" • realized P/L ${realized:+,.2f}" if realized is not None else ""
            tooltip = (
                f"{fill.side.upper()} {fill.symbol} • {fill.timestamp.astimezone():%b %d %I:%M %p}"
                f" • ${fill.price:,.2f}{outcome}\n{fill.reason}"
            )
            spot = {
                "pos": (fill.timestamp.timestamp(), fill.price),
                "data": {"row": row, "tooltip": tooltip},
            }
            if fill.side == "buy":
                groups["buy"].append(spot)
            elif realized is not None and realized > 1e-9:
                groups["profit"].append(spot)
            elif realized is not None and realized < -1e-9:
                groups["loss"].append(spot)
            else:
                groups["flat"].append(spot)
        self.buy_markers.setData(groups["buy"])
        self.profitable_sale_markers.setData(groups["profit"])
        self.losing_sale_markers.setData(groups["loss"])
        self.flat_sale_markers.setData(groups["flat"])
        realized_total = sum(fill.realized_pnl or 0.0 for fill in sell_fills)
        self.sales_summary.setText(
            f"Sell fills: {len(sell_fills)} • profitable: {len(groups['profit'])} • "
            f"losing: {len(groups['loss'])} • flat: {len(groups['flat'])} • "
            f"total realized P/L ${realized_total:+,.2f}. "
            "Markers show executed virtual prices; click one to open its exact reason and assumptions."
        )

    def _trade_marker_clicked(self, _plot, points, _event) -> None:
        if not points:
            return
        payload = points[0].data()
        if isinstance(payload, dict) and isinstance(payload.get("row"), int):
            self.fills_table.selectRow(payload["row"])

    def _set_row(self, table: QTableWidget, row: int, values: list[str], score=None) -> None:
        color = None if score is None else QColor("#00e507" if score >= 0 else "#ff697d")
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if color and (column == 0 or "+" in value or "PASS" in value or "FAIL" in value):
                item.setForeground(color)
            table.setItem(row, column, item)

    def _seek_replay(self, index: int) -> None:
        self._replay_index = index
        if not self.result or not self.result.equity_curve:
            return
        point = self.result.equity_curve[index]
        self.cursor_line.setValue(point.timestamp.timestamp())
        self.trade_cursor_line.setValue(point.timestamp.timestamp())
        self.status.setText(
            f"Replay {index + 1}/{len(self.result.equity_curve)} • {point.timestamp.astimezone():%b %d %I:%M %p} • "
            f"equity ${point.equity:,.2f} • {point.position_symbol or 'cash'}"
        )

    def _toggle_replay(self) -> None:
        if self._replay_timer.isActive():
            self._replay_timer.stop()
            self.play.setText("Play")
        elif self.result:
            self._replay_timer.start(self.speed.currentData())
            self.play.setText("Pause")

    def _advance_replay(self) -> None:
        maximum = self.replay_slider.maximum()
        if self._replay_index >= maximum:
            self._replay_timer.stop()
            self.play.setText("Play")
            return
        self.replay_slider.setValue(self._replay_index + 1)

    def _inspect_fill(self) -> None:
        row = self.fills_table.currentRow()
        if row < 0 or not self.result or row >= len(self.result.fills):
            return
        fill = self.result.fills[row]
        self.inspector.setPlainText(json.dumps(fill.as_dict(), indent=2, default=str))
        times = [p.timestamp for p in self.result.equity_curve]
        nearest = min(
            range(len(times)), key=lambda index: abs((times[index] - fill.timestamp).total_seconds())
        )
        self.replay_slider.setValue(nearest)

    def _export(self) -> None:
        if not self.result:
            QMessageBox.information(self, "Export", "Run a sandbox replay first.")
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export virtual fills", "sandbox-fills.csv", "CSV (*.csv)"
        )
        if not filename:
            return
        fields = list(self.result.fills[0].as_dict()) if self.result.fills else []
        import csv

        with Path(filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(fill.as_dict() for fill in self.result.fills)
        self.status.setText(f"Exported {len(self.result.fills)} virtual fills to {filename}")

    def _refresh_saved_runs(self) -> None:
        selected = self.saved_runs.currentData() if self.saved_runs.count() else None
        self.saved_runs.clear()
        for row in self.store.recent_sandbox_runs(30):
            metrics = json.loads(row["metrics_json"])
            label = f"{row['run_id'][:8]} • {metrics.get('return_pct', 0):+.2f}% • {row['created_at'][:16]}"
            self.saved_runs.addItem(label, row)
        if selected:
            index = self.saved_runs.findData(selected)
            if index >= 0:
                self.saved_runs.setCurrentIndex(index)

    def _load_saved_config(self) -> None:
        row = self.saved_runs.currentData()
        if not row:
            return
        raw = json.loads(row["config_json"])
        note = str(raw.pop("note", ""))
        raw.pop("dataset_hash", None)
        allowed = SandboxConfig.__dataclass_fields__
        self._apply_config(SandboxConfig(**{key: value for key, value in raw.items() if key in allowed}))
        self.notes.setText(note)

    def _busy(self, busy: bool, message: str = "") -> None:
        for button in (self.run_button, self.compare_button, self.evidence_button, self.action_lab_button):
            button.setEnabled(not busy)
        if message:
            self.status.setText(message)

    def _error(self, exc: Exception) -> None:
        self.status.setText(f"Research operation did not run: {exc}")
        QMessageBox.warning(self, "Sandbox research did not run", str(exc))

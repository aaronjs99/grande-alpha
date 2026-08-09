from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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

from momentum_trader.config import AppConfig
from momentum_trader.controller import TradingController, TradingSnapshot
from momentum_trader.models import Regime
from momentum_trader.ui.dialogs import LiveGrantDialog

STYLESHEET = """
QWidget { background: #0b1118; color: #e9f0f6; font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow { background: #081018; }
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
        self.setWindowTitle("Momentum Trader — Robinhood Agentic")
        self.resize(1440, 900)
        QApplication.instance().setStyleSheet(STYLESHEET)
        self._build_ui()

        controller.snapshot_changed.connect(self._on_snapshot)
        controller.event.connect(self._on_event)
        controller.connection_busy.connect(self._on_busy)
        self.timer = QTimer(self)
        self.timer.setInterval(int(config.poll_seconds * 1000))
        self.timer.timeout.connect(lambda: asyncio.create_task(self.controller.refresh()))
        self.timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        header = QHBoxLayout()
        brand = QLabel("MOMENTUM TRADER")
        font = QFont("Segoe UI", 18)
        font.setBold(True)
        brand.setFont(font)
        header.addWidget(brand)
        header.addStretch()
        self.connect_button = QPushButton("Connect Robinhood")
        self.connect_button.clicked.connect(lambda: asyncio.create_task(self._connect()))
        self.authorize_button = QPushButton("Authorize Live Session")
        self.authorize_button.setObjectName("primary")
        self.authorize_button.clicked.connect(self._authorize)
        self.start_button = QPushButton("Start Strategy")
        self.start_button.clicked.connect(self._start_strategy)
        self.kill_button = QPushButton("STOP + CANCEL")
        self.kill_button.setObjectName("danger")
        self.kill_button.clicked.connect(lambda: asyncio.create_task(self.controller.stop_and_cancel()))
        self.flatten_button = QPushButton("Flatten Position")
        self.flatten_button.setObjectName("flatten")
        self.flatten_button.clicked.connect(lambda: asyncio.create_task(self._flatten()))
        for button in (
            self.connect_button,
            self.authorize_button,
            self.start_button,
            self.kill_button,
            self.flatten_button,
        ):
            header.addWidget(button)
        outer.addLayout(header)

        cards = QGridLayout()
        self.account_card = MetricCard("Agentic account", "Disconnected")
        self.value_card = MetricCard("Account value", "—")
        self.buying_power_card = MetricCard("Buying power", "—")
        self.session_card = MetricCard("Live authority", "LOCKED")
        self.signal_card = MetricCard("QQQ regime", "FLAT")
        self.drawdown_card = MetricCard("Session drawdown", "$0.00")
        for column, card in enumerate(
            (
                self.account_card,
                self.value_card,
                self.buying_power_card,
                self.session_card,
                self.signal_card,
                self.drawdown_card,
            )
        ):
            cards.addWidget(card, 0, column)
        outer.addLayout(cards)

        splitter = QSplitter(Qt.Orientation.Vertical)
        top = QSplitter(Qt.Orientation.Horizontal)
        self.chart = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.chart.setBackground("#0e1720")
        self.chart.showGrid(x=True, y=True, alpha=0.18)
        self.chart.setLabel("left", "QQQ midpoint", units="$" )
        self.chart_curve = self.chart.plot(pen=pg.mkPen("#00d407", width=2))
        top.addWidget(self.chart)

        self.quotes_table = self._table(["Symbol", "Bid", "Ask", "Last", "Spread", "Age"])
        top.addWidget(self.quotes_table)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)
        splitter.addWidget(top)

        tabs = QTabWidget()
        self.positions_table = self._table(["Symbol", "Quantity", "Sellable", "Average", "Mark", "P/L"])
        self.orders_table = self._table(["Time", "Symbol", "Side", "State", "Quantity/$", "Fill", "Order ID"])
        self.activity_table = self._table(["Time", "Severity", "Event"])
        tabs.addTab(self.positions_table, "Positions")
        tabs.addTab(self.orders_table, "Orders")
        tabs.addTab(self.activity_table, "Receipts")
        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter)

        self.status = QLabel(
            "LOCKED • Connect Robinhood, verify broker values, then authorize a bounded live session."
        )
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.setCentralWidget(root)
        self._set_controls()

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        return table

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
        self.connect_button.setText("Connecting in browser…" if busy else ("Disconnect" if self._snapshot.connected else "Connect Robinhood"))

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
        session = snapshot.live_status
        if session == "LIVE" and snapshot.session_expires_at:
            session = f"LIVE to {snapshot.session_expires_at.astimezone().strftime('%I:%M %p')}"
        self.session_card.value.setText(session)
        self.session_card.value.setStyleSheet("color:#00e507" if snapshot.live_status == "LIVE" else "color:#ff697d")
        self.signal_card.value.setText(snapshot.signal.regime.value.upper())
        signal_color = {Regime.BULLISH: "#00e507", Regime.BEARISH: "#ff697d", Regime.FLAT: "#f2c14e"}
        self.signal_card.value.setStyleSheet(f"color:{signal_color[snapshot.signal.regime]}")
        self.drawdown_card.value.setText(f"${snapshot.drawdown:,.2f}")
        self.connect_button.setText("Disconnect" if snapshot.connected else "Connect Robinhood")
        self._update_quotes(snapshot)
        self._update_positions(snapshot)
        self._update_orders(snapshot)
        self._update_chart(snapshot)
        refreshed = snapshot.last_refresh.astimezone().strftime("%I:%M:%S %p") if snapshot.last_refresh else "never"
        self.status.setText(
            f"{snapshot.live_status} • Strategy {'RUNNING' if snapshot.strategy_running else 'STOPPED'} • "
            f"Orders {snapshot.trades_today} • Last broker refresh {refreshed} • {snapshot.signal.reason}"
        )
        self._set_controls()

    def _set_controls(self) -> None:
        connected = self._snapshot.connected
        funded = bool(self._snapshot.portfolio and self._snapshot.portfolio.buying_power > 0)
        live = self._snapshot.live_status == "LIVE"
        self.authorize_button.setEnabled(connected and funded)
        self.start_button.setEnabled(live and not self._snapshot.strategy_running)
        self.kill_button.setEnabled(connected)
        self.flatten_button.setEnabled(bool(self._snapshot.positions))

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
            amount = f"{order.quantity:g} sh" if order.quantity is not None else f"${order.dollar_amount:,.2f}"
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
                "Exit Momentum Trader",
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


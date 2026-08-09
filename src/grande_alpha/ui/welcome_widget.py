from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from grande_alpha.config import AppConfig


class WelcomeWidget(QWidget):
    open_sandbox = Signal()
    open_settings = Signal()

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        title = QLabel("Research before authority")
        title.setStyleSheet("font-size:22pt;font-weight:700")
        layout.addWidget(title)
        self.mode = QLabel()
        self.mode.setWordWrap(True)
        layout.addWidget(self.mode)

        cards = QHBoxLayout()
        research = QGroupBox("1 · Establish evidence")
        research_layout = QVBoxLayout(research)
        research_text = QLabel(
            "Run deterministic scenarios or import lawful QQQ/TQQQ/SQQQ history. Inspect timing, costs, "
            "parameter sensitivity, random controls, and walk-forward folds."
        )
        research_text.setWordWrap(True)
        research_layout.addWidget(research_text)
        sandbox = QPushButton("Open research sandbox")
        sandbox.setObjectName("primary")
        sandbox.clicked.connect(self.open_sandbox)
        research_layout.addWidget(sandbox)
        cards.addWidget(research)

        permissions = QGroupBox("2 · Add only what you need")
        permissions_layout = QVBoxLayout(permissions)
        permissions_text = QLabel(
            "Broker access, community market data, the personal ledger, and real orders are independent "
            "permissions. Every one starts off and remains immediately revocable."
        )
        permissions_text.setWordWrap(True)
        permissions_layout.addWidget(permissions_text)
        settings = QPushButton("Review capabilities")
        settings.clicked.connect(self.open_settings)
        permissions_layout.addWidget(settings)
        cards.addWidget(permissions)

        monitor = QGroupBox("3 · Monitor independently")
        monitor_layout = QVBoxLayout(monitor)
        monitor_text = QLabel(
            "Shadow results and backtests are not promises. If live controls are deliberately unlocked, "
            "keep the broker app available, use a bounded session, and verify every receipt."
        )
        monitor_text.setWordWrap(True)
        monitor_layout.addWidget(monitor_text)
        monitor_layout.addStretch()
        cards.addWidget(monitor)
        layout.addLayout(cards)

        disclosure = QLabel(
            "Independent community software · Not affiliated with or endorsed by Robinhood, ProShares, "
            "Nasdaq, or Yahoo · No telemetry · No investment advice"
        )
        disclosure.setWordWrap(True)
        disclosure.setStyleSheet("color:#8fa4b8;padding:10px")
        layout.addWidget(disclosure)
        layout.addStretch()
        self.update_config(config)

    def update_config(self, config: AppConfig) -> None:
        enabled = []
        if config.broker_connection_enabled:
            enabled.append("broker connection")
        if config.remote_market_data_enabled:
            enabled.append("community remote data")
        if config.live_trading_enabled:
            enabled.append("real-order controls")
        suffix = ", ".join(enabled) if enabled else "no optional capabilities"
        self.mode.setText(f"RESEARCH MODE · {suffix}. Local sandbox and CSV import remain available.")

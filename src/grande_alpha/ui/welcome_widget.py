from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from grande_alpha.config import AppConfig
from grande_alpha.strategy import STRATEGY_NAMES


class WelcomeWidget(QWidget):
    open_activation = Signal()
    open_sandbox = Signal()
    open_settings = Signal()

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        title = QLabel("Know exactly what remains")
        title.setStyleSheet("font-size:22pt;font-weight:700")
        layout.addWidget(title)
        self.mode = QLabel()
        self.mode.setWordWrap(True)
        layout.addWidget(self.mode)

        cards = QHBoxLayout()
        activation = QGroupBox("1 · Follow the activation checklist")
        activation_layout = QVBoxLayout(activation)
        activation_text = QLabel(
            "See every blocking condition, who owns it, and the exact next action. Safe read-only checks "
            "can be rerun by the app; money decisions and external approvals stay with you."
        )
        activation_text.setWordWrap(True)
        activation_layout.addWidget(activation_text)
        activation_button = QPushButton("Open activation checklist")
        activation_button.setObjectName("primary")
        activation_button.clicked.connect(self.open_activation)
        activation_layout.addWidget(activation_button)
        cards.addWidget(activation)

        research = QGroupBox("2 · Establish evidence")
        research_layout = QVBoxLayout(research)
        research_text = QLabel(
            "Run deterministic scenarios or import lawful QQQ/TQQQ/SQQQ history. Inspect timing, costs, "
            "parameter sensitivity, random controls, and walk-forward folds."
        )
        research_text.setWordWrap(True)
        research_layout.addWidget(research_text)
        sandbox = QPushButton("Open research sandbox")
        sandbox.clicked.connect(self.open_sandbox)
        research_layout.addWidget(sandbox)
        cards.addWidget(research)

        permissions = QGroupBox("3 · Add only what you need")
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
        layout.addLayout(cards)

        monitor_text = QLabel(
            "Scheduled auto-shadow is structurally read-only: it can collect observations and virtual fills, "
            "but it cannot authorize, review, place, or cancel an order. A normal live session remains a "
            "separate same-day review after every condition passes."
        )
        monitor_text.setObjectName("validationWarning")
        monitor_text.setWordWrap(True)
        layout.addWidget(monitor_text)

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
        champion = STRATEGY_NAMES.get(config.strategy_name, config.strategy_name)
        self.mode.setText(
            f"RUNTIME CHAMPION · {champion}. RESEARCH MODE · {suffix}. "
            "Local sandbox and CSV import remain available; no strategy is guaranteed profitable."
        )

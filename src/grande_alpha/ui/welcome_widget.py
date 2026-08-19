from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.config import AppConfig
from grande_alpha.strategy import STRATEGY_NAMES


class _ResponsiveScrollArea(QScrollArea):
    """Pin the scroll document to the live viewport width while allowing vertical overflow."""

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        content = self.widget()
        if content is not None and self.viewport().width() > 0:
            content.setFixedWidth(self.viewport().width())


class WelcomeWidget(QWidget):
    open_activation = Signal()
    open_sandbox = Signal()
    open_settings = Signal()

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = _ResponsiveScrollArea()
        self.scroll.setObjectName("gettingStartedScroll")
        self.scroll.setAccessibleName("Getting Started guidance")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(self.scroll)

        self.content = QWidget()
        self.content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.scroll.setWidget(self.content)
        layout = QVBoxLayout(self.content)
        title = QLabel("Know exactly what remains")
        title.setStyleSheet("font-size:22pt;font-weight:700")
        layout.addWidget(title)
        self.mode = QLabel()
        self.mode.setWordWrap(True)
        layout.addWidget(self.mode)

        cards = QGridLayout()
        self.cards_layout = cards
        cards.setHorizontalSpacing(10)
        cards.setVerticalSpacing(10)
        activation = QGroupBox("1 · Follow the activation checklist")
        activation_layout = QVBoxLayout(activation)
        activation_text = QLabel(
            "See every blocking condition, who owns it, and the exact next action. Safe read-only checks "
            "can be rerun by the app; money decisions and external approvals stay with you."
        )
        activation_text.setWordWrap(True)
        activation_layout.addWidget(activation_text)
        self.activation_button = QPushButton("Open activation checklist")
        self.activation_button.setObjectName("primary")
        self.activation_button.clicked.connect(self.open_activation)
        activation_layout.addWidget(self.activation_button)

        research = QGroupBox("2 · Establish evidence")
        research_layout = QVBoxLayout(research)
        research_text = QLabel(
            "Run deterministic scenarios or import lawful QQQ/TQQQ/SQQQ history. Inspect timing, costs, "
            "parameter sensitivity, random controls, and walk-forward folds."
        )
        research_text.setWordWrap(True)
        research_layout.addWidget(research_text)
        self.sandbox_button = QPushButton("Open research sandbox")
        self.sandbox_button.clicked.connect(self.open_sandbox)
        research_layout.addWidget(self.sandbox_button)

        permissions = QGroupBox("3 · Add only what you need")
        permissions_layout = QVBoxLayout(permissions)
        permissions_text = QLabel(
            "Broker access, community market data, the capital planning ledger, and real orders are independent "
            "permissions. Every one starts off and remains immediately revocable."
        )
        permissions_text.setWordWrap(True)
        permissions_layout.addWidget(permissions_text)
        self.settings_button = QPushButton("Review capabilities")
        self.settings_button.clicked.connect(self.open_settings)
        permissions_layout.addWidget(self.settings_button)
        self.cards = (activation, research, permissions)
        layout.addLayout(cards)

        self.monitor_text = QLabel(
            "Scheduled auto-shadow is structurally read-only: it can collect observations and virtual fills, "
            "but it cannot authorize, review, place, or cancel an order. Normal GRANDE Alpha separately offers "
            "attended supervised review after shared account, route, and capability checks; autonomous review "
            "still requires every evidence and runtime condition."
        )
        self.monitor_text.setObjectName("validationWarning")
        self.monitor_text.setWordWrap(True)
        layout.addWidget(self.monitor_text)

        self.disclosure = QLabel(
            "Independent community software · Not affiliated with or endorsed by Robinhood, ProShares, "
            "Nasdaq, or Yahoo · No telemetry · No investment advice"
        )
        self.disclosure.setWordWrap(True)
        self.disclosure.setStyleSheet("color:#8fa4b8;padding:10px")
        layout.addWidget(self.disclosure)
        for label in (
            self.mode,
            activation_text,
            research_text,
            permissions_text,
            self.monitor_text,
            self.disclosure,
        ):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for card in self.cards:
            card.setMinimumWidth(0)
            card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        layout.addStretch()
        self.update_config(config)
        self._card_columns = 0
        self._apply_responsive_layout(self.width())

    def _apply_responsive_layout(self, width: int) -> None:
        columns = 1 if width < 920 else 3
        if columns == self._card_columns:
            return
        self._card_columns = columns
        for card in self.cards:
            self.cards_layout.removeWidget(card)
        for index, card in enumerate(self.cards):
            self.cards_layout.addWidget(card, index // columns, index % columns)
            card.setMinimumHeight(112 if columns == 3 else 84)
        for column in range(columns):
            self.cards_layout.setColumnStretch(column, 1)
        # QScrollArea must be free to narrow its content; only the vertical reading flow
        # needs a floor. A SetMinimumSize layout constraint would instead preserve the
        # labels' unwrapped width and create invisible horizontal overflow.
        self.content.setMinimumHeight(340 if columns == 3 else 500)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

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

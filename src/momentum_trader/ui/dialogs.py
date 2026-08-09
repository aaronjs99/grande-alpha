from __future__ import annotations

from datetime import timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from momentum_trader.config import AppConfig
from momentum_trader.models import Account, LiveGrant, Portfolio, utc_now


class LiveGrantDialog(QDialog):
    def __init__(self, account: Account, portfolio: Portfolio, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.account = account
        self.portfolio = portfolio
        self.config = config
        self.phrase = f"LIVE {account.account_number[-4:]}"
        self.setWindowTitle("Authorize bounded live trading")
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        title = QLabel("Real-money automatic orders")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        details = QLabel(
            f"Account: {account.nickname} {account.masked} ({account.account_type})\n"
            f"Broker value: ${portfolio.total_value:,.2f}   Buying power: ${portfolio.buying_power:,.2f}\n\n"
            "Within this session the strategy may place TQQQ/SQQQ orders without asking again. "
            "The numeric limits below are enforced outside the strategy and expire automatically."
        )
        details.setWordWrap(True)
        layout.addWidget(details)

        form = QFormLayout()
        self.minutes = QSpinBox()
        self.minutes.setRange(5, 240)
        self.minutes.setValue(config.default_session_minutes)
        self.max_order = self._money(config.default_max_order_notional, max(1.0, portfolio.buying_power))
        self.max_exposure = self._money(config.default_max_total_exposure, max(1.0, portfolio.buying_power))
        self.max_loss = self._money(config.default_max_daily_loss, max(1.0, portfolio.total_value))
        self.max_trades = QSpinBox()
        self.max_trades.setRange(1, 50)
        self.max_trades.setValue(config.default_max_trades)
        self.orders_per_minute = QSpinBox()
        self.orders_per_minute.setRange(1, 5)
        self.orders_per_minute.setValue(config.default_max_orders_per_minute)
        self.max_spread = QDoubleSpinBox()
        self.max_spread.setRange(1.0, 100.0)
        self.max_spread.setDecimals(1)
        self.max_spread.setValue(config.default_max_spread_bps)
        self.max_spread.setSuffix(" bps")
        form.addRow("Session duration", self.minutes)
        form.addRow("Max order notional", self.max_order)
        form.addRow("Max total exposure", self.max_exposure)
        form.addRow("Max session loss", self.max_loss)
        form.addRow("Max submitted orders", self.max_trades)
        form.addRow("Max orders per minute", self.orders_per_minute)
        form.addRow("Maximum spread", self.max_spread)
        layout.addLayout(form)

        self.attest = QCheckBox(
            "I am trading only my own account and have obtained the immigration/tax guidance I need."
        )
        layout.addWidget(self.attest)

        prompt = QLabel(f"Type {self.phrase} to grant live authority for this session:")
        prompt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(prompt)
        self.confirmation = QLineEdit()
        self.confirmation.setPlaceholderText(self.phrase)
        layout.addWidget(self.confirmation)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Authorize live session")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.confirmation.textChanged.connect(self._validate)
        self.attest.stateChanged.connect(self._validate)
        layout.addWidget(self.buttons)

    def _money(self, value: float, maximum: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(1.0, max(1.0, maximum))
        widget.setDecimals(2)
        widget.setPrefix("$")
        widget.setValue(min(value, maximum))
        return widget

    def _validate(self) -> None:
        valid = self.attest.isChecked() and self.confirmation.text().strip() == self.phrase
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)

    def grant(self) -> LiveGrant:
        starts = utc_now()
        return LiveGrant(
            account_number=self.account.account_number,
            starts_at=starts,
            expires_at=starts + timedelta(minutes=self.minutes.value()),
            max_order_notional=self.max_order.value(),
            max_total_exposure=self.max_exposure.value(),
            max_daily_loss=self.max_loss.value(),
            max_trades=self.max_trades.value(),
            max_orders_per_minute=self.orders_per_minute.value(),
            max_spread_bps=self.max_spread.value(),
            max_quote_age_seconds=self.config.default_max_quote_age_seconds,
        )

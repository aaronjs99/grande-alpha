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
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from grande_alpha.config import AppConfig
from grande_alpha.models import Account, LiveGrant, Portfolio, utc_now


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


class FundPlanDialog(QDialog):
    """Create a ledger plan; this dialog never initiates a money transfer."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plan GRANDE Research Fund contribution")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        title = QLabel("Personal contribution ledger")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        explanation = QLabel(
            "Enter realized brokerage results and a reserve. GRANDE Alpha calculates a planned "
            "personal contribution only; it cannot transfer funds. Mark the entry confirmed only "
            "after you independently complete and verify a transfer."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        self.period = QLineEdit(utc_now().strftime("%Y-%m"))
        self.realized_profit = self._money(-1_000_000, 1_000_000)
        self.fees = self._money(0, 1_000_000)
        self.tax_reserve = self._money(0, 1_000_000)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 100)
        self.rate.setDecimals(1)
        self.rate.setSuffix("%")
        self.rate.setValue(25.0)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(80)
        self.eligible = QLabel("$0.00")
        self.eligible.setObjectName("cardValue")
        form.addRow("Period (YYYY-MM)", self.period)
        form.addRow("Realized profit", self.realized_profit)
        form.addRow("Broker fees", self.fees)
        form.addRow("Tax reserve", self.tax_reserve)
        form.addRow("Contribution rate", self.rate)
        form.addRow("Eligible contribution", self.eligible)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save contribution plan")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for widget in (self.realized_profit, self.fees, self.tax_reserve, self.rate):
            widget.valueChanged.connect(self._update_preview)
        self._update_preview()

    def _money(self, minimum: float, maximum: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(2)
        widget.setPrefix("$")
        return widget

    def _update_preview(self) -> None:
        distributable = max(
            0.0,
            self.realized_profit.value() - self.fees.value() - self.tax_reserve.value(),
        )
        self.eligible.setText(f"${distributable * self.rate.value() / 100:,.2f}")

    def values(self) -> dict[str, object]:
        return {
            "period": self.period.text().strip(),
            "realized_profit": self.realized_profit.value(),
            "fees": self.fees.value(),
            "tax_reserve": self.tax_reserve.value(),
            "contribution_rate": self.rate.value() / 100,
            "notes": self.notes.toPlainText().strip(),
        }

from __future__ import annotations

from datetime import timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from grande_alpha.config import AppConfig
from grande_alpha.execution import MARKET_HOURS_LABELS, ORDER_TYPE_LABELS, TIME_IN_FORCE_LABELS
from grande_alpha.models import Account, LiveGrant, Portfolio, utc_now
from grande_alpha.ui.glossary import add_explained_row, apply_help, help_hint


class LiveGrantDialog(QDialog):
    def __init__(self, account: Account, portfolio: Portfolio, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.account = account
        self.portfolio = portfolio
        self.config = config
        self.phrase = f"LIVE {account.account_number[-4:]}"
        self.setWindowTitle("Authorize bounded live trading")
        self.setModal(True)
        self.setMinimumSize(700, 620)
        self.resize(720, 650)
        self.setSizeGripEnabled(True)

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
        if account.account_type.lower() == "cash":
            cash_notice = QLabel(
                "Cash-account constraint: sale proceeds generally do not restore spendable buying "
                "power until settlement. A small cash account cannot continuously recycle the same "
                "dollars through intraday entries; Robinhood-reported buying power remains authoritative."
            )
            cash_notice.setObjectName("validationWarning")
            cash_notice.setWordWrap(True)
            apply_help(
                cash_notice,
                "Cash-account settlement",
                "U.S. equity sales normally settle on the next trading day. Until then, those proceeds "
                "may be unavailable for another purchase in a cash account.",
            )
            layout.addWidget(cash_notice)
        layout.addWidget(help_hint())

        risk_group = QGroupBox("Session risk limits")
        form = QFormLayout(risk_group)
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
        add_explained_row(form, "Session duration", self.minutes)
        add_explained_row(form, "Max order notional", self.max_order)
        add_explained_row(form, "Max total exposure", self.max_exposure)
        add_explained_row(form, "Max session loss", self.max_loss)
        add_explained_row(form, "Max submitted orders", self.max_trades)
        add_explained_row(form, "Max orders per minute", self.orders_per_minute)
        add_explained_row(form, "Maximum spread", self.max_spread)
        layout.addWidget(risk_group)

        route_group = QGroupBox("Order routing")
        route = QFormLayout(route_group)
        self.market_hours = QComboBox()
        for value, label in MARKET_HOURS_LABELS.items():
            self.market_hours.addItem(label, value)
        self.market_hours.setCurrentIndex(max(0, self.market_hours.findData(config.market_hours)))
        self.order_type = QComboBox()
        for value, label in ORDER_TYPE_LABELS.items():
            self.order_type.addItem(label, value)
        self.order_type.setCurrentIndex(max(0, self.order_type.findData(config.order_type)))
        self.time_in_force = QComboBox()
        for value, label in TIME_IN_FORCE_LABELS.items():
            self.time_in_force.addItem(label, value)
        self.time_in_force.setCurrentIndex(max(0, self.time_in_force.findData(config.time_in_force)))
        self.limit_offset = QDoubleSpinBox()
        self.limit_offset.setRange(0, 100)
        self.limit_offset.setDecimals(1)
        self.limit_offset.setValue(config.limit_offset_bps)
        self.limit_offset.setSuffix(" bps")
        add_explained_row(route, "Authorized session", self.market_hours)
        add_explained_row(route, "Authorized order type", self.order_type)
        add_explained_row(route, "Authorized time in force", self.time_in_force)
        add_explained_row(route, "Limit offset", self.limit_offset)
        layout.addWidget(route_group)
        self.route_note = QLabel("")
        self.route_note.setWordWrap(True)
        layout.addWidget(self.route_note)

        self.attest = QCheckBox(
            "I am trading only my own account and have obtained the immigration/tax guidance\n"
            "needed for my circumstances."
        )
        apply_help(
            self.attest,
            "Live-session attestation",
            "Confirm this only when every statement is true. It is a consent checkpoint, not immigration or tax advice.",
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
        self.market_hours.currentIndexChanged.connect(self._route_changed)
        self.order_type.currentIndexChanged.connect(self._route_changed)
        self.time_in_force.currentIndexChanged.connect(self._route_changed)
        self._route_changed()
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
        authorize = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        authorize.setEnabled(valid)
        authorize.setToolTip(
            "Authorize this bounded session."
            if valid
            else f"Check the attestation and type {self.phrase} exactly."
        )

    def _route_changed(self, _value: int | None = None) -> None:
        outside_regular = self.market_hours.currentData() != "regular_hours"
        market_index = self.order_type.findData("market")
        market_item = self.order_type.model().item(market_index)
        if market_item is not None:
            market_item.setEnabled(not outside_regular)
        if outside_regular and self.order_type.currentData() != "limit":
            self.order_type.setCurrentIndex(self.order_type.findData("limit"))
        is_market = self.order_type.currentData() == "market"
        if is_market and self.time_in_force.currentData() != "gfd":
            self.time_in_force.setCurrentIndex(self.time_in_force.findData("gfd"))
        self.time_in_force.setEnabled(not is_market)
        self.limit_offset.setEnabled(not is_market)
        if is_market:
            note = "Dollar-based regular-hours market route; fractional fills are possible and price is not guaranteed."
        else:
            note = (
                "Whole-share limit route. The order can partially fill or not fill. GTC can remain working at "
                "Robinhood for up to 90 days, including if GRANDE Alpha is closed unexpectedly."
            )
        if self.market_hours.currentData() == "all_day_hours":
            note += " TQQQ/SQQQ 24-hour eligibility is checked again before submission."
        self.route_note.setText(note)
        self._validate()

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
            market_hours=self.market_hours.currentData(),
            order_type=self.order_type.currentData(),
            time_in_force=self.time_in_force.currentData(),
            limit_offset_bps=self.limit_offset.value(),
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
        add_explained_row(form, "Period (YYYY-MM)", self.period)
        add_explained_row(form, "Realized profit", self.realized_profit)
        add_explained_row(form, "Broker fees", self.fees)
        add_explained_row(form, "Tax reserve", self.tax_reserve)
        add_explained_row(form, "Contribution rate", self.rate)
        add_explained_row(form, "Eligible contribution", self.eligible)
        add_explained_row(form, "Notes", self.notes)
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

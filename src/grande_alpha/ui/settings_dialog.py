from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.config import AppConfig

LIVE_PHRASE = "ENABLE LIVE ORDERS"


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, live_evidence_ready: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.original = config
        self.live_evidence_ready = live_evidence_ready
        self.setWindowTitle("GRANDE Alpha settings and permissions")
        self.setMinimumSize(780, 650)
        self.resize(840, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("Settings & permissions")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Review what GRANDE Alpha may access, what remains locked, and what is retained locally. "
            "Saving capability changes creates an audit receipt."
        )
        subtitle.setObjectName("settingsDescription")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("settingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(2, 2, 8, 2)
        body_layout.setSpacing(12)

        permissions = QGroupBox("Broker && account access")
        permissions_layout = QVBoxLayout(permissions)
        permissions_layout.setSpacing(7)
        self.broker = QCheckBox("Connect Robinhood broker data")
        self.broker.setAccessibleName("Connect Robinhood broker data")
        self.broker.setChecked(config.broker_connection_enabled)
        permissions_layout.addWidget(self.broker)
        self.broker_note = self._description(
            "Robinhood's consent may expose metadata and read data across connected accounts. GRANDE Alpha "
            "selects the active Agentic account for portfolio, positions, and orders; the regular investing "
            "account is not selected for those app views. Trading is provider-restricted to the Agentic account."
        )
        permissions_layout.addWidget(self.broker_note)

        self.account_scope_status = QLabel("APP VIEW SCOPE  •  ACTIVE AGENTIC ACCOUNT")
        self.account_scope_status.setObjectName("settingsStatus")
        self.account_scope_status.setStyleSheet(
            "background:#142b3d;color:#8fd3ff;border:1px solid #315b78;"
        )
        permissions_layout.addWidget(self.account_scope_status)

        self.live = QCheckBox("Allow real-order automation for TQQQ/SQQQ")
        self.live.setAccessibleName("Allow real-order automation")
        self.live.setChecked(config.live_trading_enabled)
        permissions_layout.addWidget(self.live)
        self.evidence_status = QLabel(
            "EVIDENCE GATE  •  READY FOR SEPARATE SESSION REVIEW"
            if live_evidence_ready
            else "EVIDENCE GATE  •  LOCKED — SHADOW ONLY"
        )
        self.evidence_status.setObjectName("settingsStatus")
        self.evidence_status.setStyleSheet(
            "background:#17301f;color:#80e899;border:1px solid #376d45;"
            if live_evidence_ready
            else "background:#2b2315;color:#ffd27a;border:1px solid #6f5727;"
        )
        permissions_layout.addWidget(self.evidence_status)
        self.live_phrase = QLineEdit()
        self.live_phrase.setPlaceholderText(LIVE_PHRASE)
        self.live_phrase.setAccessibleName("Live-order capability confirmation phrase")
        self.live_phrase.setVisible(not config.live_trading_enabled)
        permissions_layout.addWidget(self.live_phrase)
        self.live_note = self._description(
            "This grants no standing authority. Every launch remains locked. Each live session still requires "
            "a current matching evidence certificate, account-specific limits, expiry, attestation, and typed "
            "confirmation."
        )
        permissions_layout.addWidget(self.live_note)
        body_layout.addWidget(permissions)

        optional = QGroupBox("Optional research features")
        optional_layout = QVBoxLayout(optional)
        optional_layout.setSpacing(7)
        self.remote_data = QCheckBox("Use community remote market data")
        self.remote_data.setAccessibleName("Use community remote market data")
        self.remote_data.setChecked(config.remote_market_data_enabled)
        optional_layout.addWidget(self.remote_data)
        self.remote_data_note = self._description(
            "Contacts an unsupported Yahoo chart endpoint only for requested symbols, intervals, and time "
            "ranges. No broker or account data is included."
        )
        optional_layout.addWidget(self.remote_data_note)
        self.personal_ledger = QCheckBox("Show the personal research-fund ledger")
        self.personal_ledger.setAccessibleName("Show the personal research-fund ledger")
        self.personal_ledger.setChecked(config.personal_ledger_enabled)
        optional_layout.addWidget(self.personal_ledger)
        self.personal_ledger_note = self._description(
            "A local planning ledger only. It never transfers brokerage, university, grant, or laboratory funds."
        )
        optional_layout.addWidget(self.personal_ledger_note)
        body_layout.addWidget(optional)

        privacy = QGroupBox("Local privacy && credentials")
        privacy_form = QFormLayout(privacy)
        privacy_form.setVerticalSpacing(9)
        self.retention = QSpinBox()
        self.retention.setAccessibleName("Market history retention in days")
        self.retention.setRange(7, 3650)
        self.retention.setSuffix(" days")
        self.retention.setValue(config.market_history_retention_days)
        self.forget_credentials = QCheckBox("Forget stored OAuth credentials when I save")
        self.forget_credentials.setAccessibleName("Forget stored OAuth credentials when saving")
        privacy_form.addRow("Quote, bar, and signal history", self.retention)
        privacy_form.addRow("Local credential", self.forget_credentials)
        self.credential_note = self._description(
            "This disconnects GRANDE Alpha and removes its Windows-stored credential. Reconnect in the browser "
            "to restore access. It does not revoke the connection inside Robinhood."
        )
        privacy_form.addRow(self.credential_note)
        body_layout.addWidget(privacy)

        cadence = QGroupBox("Research cadence — advanced")
        cadence_form = QFormLayout(cadence)
        cadence_form.setVerticalSpacing(9)
        self.quote_poll = QDoubleSpinBox()
        self.quote_poll.setAccessibleName("Quote request target")
        self.quote_poll.setRange(0.25, 5.0)
        self.quote_poll.setDecimals(2)
        self.quote_poll.setSingleStep(0.25)
        self.quote_poll.setSuffix(" s")
        self.quote_poll.setValue(config.poll_seconds)
        self.reconcile = QDoubleSpinBox()
        self.reconcile.setAccessibleName("Account reconciliation interval")
        self.reconcile.setRange(2.0, 60.0)
        self.reconcile.setDecimals(1)
        self.reconcile.setSuffix(" s")
        self.reconcile.setValue(config.reconcile_seconds)
        self.bar_seconds = QSpinBox()
        self.bar_seconds.setAccessibleName("Completed analysis bar interval")
        self.bar_seconds.setRange(1, 300)
        self.bar_seconds.setSuffix(" s")
        self.bar_seconds.setValue(config.bar_seconds)
        self.trade_every_bars = QSpinBox()
        self.trade_every_bars.setAccessibleName("Trade decision interval in analysis bars")
        self.trade_every_bars.setRange(2, 120)
        self.trade_every_bars.setSuffix(" bars")
        self.trade_every_bars.setValue(config.trade_every_bars)
        cadence_form.addRow("Quote request target", self.quote_poll)
        cadence_form.addRow("Account reconciliation", self.reconcile)
        cadence_form.addRow("Completed analysis bar", self.bar_seconds)
        cadence_form.addRow("Trade decision every", self.trade_every_bars)
        self.cadence_note = self._description("")
        self._update_cadence_note()
        cadence_form.addRow(self.cadence_note)
        body_layout.addWidget(cadence)
        body_layout.addStretch()
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)

        self.validation = QLabel("")
        self.validation.setObjectName("validationWarning")
        self.validation.setWordWrap(True)
        self.validation.setVisible(False)
        layout.addWidget(self.validation)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.broker.stateChanged.connect(self._validate)
        self.live.stateChanged.connect(self._validate)
        self.live_phrase.textChanged.connect(self._validate)
        self.bar_seconds.valueChanged.connect(self._update_cadence_note)
        self.trade_every_bars.valueChanged.connect(self._update_cadence_note)
        self._validate()

    @staticmethod
    def _description(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("settingsDescription")
        label.setWordWrap(True)
        label.setContentsMargins(25, 0, 6, 5)
        return label

    def _update_cadence_note(self, _value: int | None = None) -> None:
        analysis = self.bar_seconds.value()
        stride = self.trade_every_bars.value()
        self.cadence_note.setText(
            f"t_analysis = {analysis}s and t_trade = {analysis * stride}s ({stride} completed analysis bars). "
            "Quote requests are single-flight and execution remains subject to independent broker and risk gates."
        )

    def _validate(self) -> None:
        enabling_live = self.live.isChecked() and not self.original.live_trading_enabled
        self.live_phrase.setVisible(enabling_live)
        valid = True
        message = ""
        if self.live.isChecked() and not self.broker.isChecked():
            valid = False
            message = "Real-order automation requires the broker connection capability."
        elif self.live.isChecked() and not self.live_evidence_ready:
            valid = False
            message = (
                "Real-order automation remains shadow-only: run the full Evidence Lab on eligible recent "
                "market history until every gate passes for this exact strategy."
            )
        elif enabling_live and self.live_phrase.text().strip() != LIVE_PHRASE:
            valid = False
            message = f"Type {LIVE_PHRASE} exactly to unlock real-order controls."
        self.validation.setText(message)
        self.validation.setVisible(bool(message))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(valid)

    def updated_config(self) -> AppConfig:
        return replace(
            self.original,
            broker_connection_enabled=self.broker.isChecked(),
            live_trading_enabled=self.live.isChecked(),
            remote_market_data_enabled=self.remote_data.isChecked(),
            personal_ledger_enabled=self.personal_ledger.isChecked(),
            market_history_retention_days=self.retention.value(),
            poll_seconds=self.quote_poll.value(),
            reconcile_seconds=self.reconcile.value(),
            bar_seconds=self.bar_seconds.value(),
            trade_every_bars=self.trade_every_bars.value(),
        )

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from grande_alpha.config import AppConfig

LIVE_PHRASE = "ENABLE LIVE ORDERS"


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.original = config
        self.setWindowTitle("GRANDE Alpha settings and permissions")
        self.setMinimumWidth(690)
        layout = QVBoxLayout(self)

        permissions = QGroupBox("Capabilities")
        permissions_layout = QVBoxLayout(permissions)
        self.broker = QCheckBox(
            "Broker connection: READ all account, balance, position, transaction, order, watchlist, and scan data exposed by the provider"
        )
        self.broker.setChecked(config.broker_connection_enabled)
        permissions_layout.addWidget(self.broker)
        broker_note = QLabel(
            "WRITE access is structurally limited by the provider to the dedicated Agentic account. "
            "GRANDE Alpha still keeps real-order automation disabled unless you unlock it below."
        )
        broker_note.setWordWrap(True)
        permissions_layout.addWidget(broker_note)
        self.live = QCheckBox("Real-order automation: allow the app to request reviewed TQQQ/SQQQ orders")
        self.live.setChecked(config.live_trading_enabled)
        permissions_layout.addWidget(self.live)
        self.live_phrase = QLineEdit()
        self.live_phrase.setPlaceholderText(LIVE_PHRASE)
        self.live_phrase.setVisible(not config.live_trading_enabled)
        permissions_layout.addWidget(self.live_phrase)
        live_note = QLabel(
            "Unlocking this feature grants no standing session. Every launch remains locked, and each live "
            "session requires account-specific limits, an expiry, an attestation, and typed confirmation."
        )
        live_note.setWordWrap(True)
        permissions_layout.addWidget(live_note)
        self.remote_data = QCheckBox(
            "Community remote market data: contact the unsupported Yahoo chart endpoint for requested symbols and intervals"
        )
        self.remote_data.setChecked(config.remote_market_data_enabled)
        permissions_layout.addWidget(self.remote_data)
        self.personal_ledger = QCheckBox("Show the optional local personal research-fund ledger")
        self.personal_ledger.setChecked(config.personal_ledger_enabled)
        permissions_layout.addWidget(self.personal_ledger)
        layout.addWidget(permissions)

        privacy = QGroupBox("Privacy and retention")
        privacy_form = QFormLayout(privacy)
        self.retention = QSpinBox()
        self.retention.setRange(7, 3650)
        self.retention.setSuffix(" days")
        self.retention.setValue(config.market_history_retention_days)
        self.forget_credentials = QCheckBox(
            "Forget stored broker OAuth credentials when these settings are saved (reconnect to restore access)"
        )
        privacy_form.addRow("Quote, bar, and signal history", self.retention)
        privacy_form.addRow("Credential revocation", self.forget_credentials)
        layout.addWidget(privacy)

        self.validation = QLabel("")
        self.validation.setWordWrap(True)
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
        self._validate()

    def _validate(self) -> None:
        enabling_live = self.live.isChecked() and not self.original.live_trading_enabled
        self.live_phrase.setVisible(enabling_live)
        valid = True
        message = ""
        if self.live.isChecked() and not self.broker.isChecked():
            valid = False
            message = "Real-order automation requires the broker connection capability."
        elif enabling_live and self.live_phrase.text().strip() != LIVE_PHRASE:
            valid = False
            message = f"Type {LIVE_PHRASE} exactly to unlock real-order controls."
        self.validation.setText(message)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(valid)

    def updated_config(self) -> AppConfig:
        return replace(
            self.original,
            broker_connection_enabled=self.broker.isChecked(),
            live_trading_enabled=self.live.isChecked(),
            remote_market_data_enabled=self.remote_data.isChecked(),
            personal_ledger_enabled=self.personal_ledger.isChecked(),
            market_history_retention_days=self.retention.value(),
        )

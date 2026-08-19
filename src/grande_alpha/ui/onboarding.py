from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from grande_alpha.config import DISCLOSURE_VERSION, ONBOARDING_VERSION, AppConfig


class _AcknowledgementPage(QWizardPage):
    def __init__(self, title: str, body: str, acknowledgement: str) -> None:
        super().__init__()
        self.setTitle(title)
        layout = QVBoxLayout(self)
        text = QLabel(body)
        text.setWordWrap(True)
        layout.addWidget(text)
        self.acknowledgement = QCheckBox(acknowledgement)
        self.acknowledgement.stateChanged.connect(self.completeChanged)
        layout.addWidget(self.acknowledgement)
        layout.addStretch()

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        return self.acknowledgement.isChecked()


class OnboardingWizard(QWizard):
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Welcome to GRANDE Alpha")
        self.setMinimumSize(720, 520)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        welcome = QWizardPage()
        welcome.setTitle("Research before authority")
        welcome_layout = QVBoxLayout(welcome)
        intro = QLabel(
            "GRANDE Alpha is a local-first strategy research workstation. It starts in Research "
            "Mode: no broker connection, no remote market-data download, and no real orders.\n\n"
            "Use the sandbox, import your own lawful data, inspect assumptions, and preserve "
            "out-of-sample evidence before enabling any optional integration."
        )
        intro.setWordWrap(True)
        welcome_layout.addWidget(intro)
        welcome_layout.addStretch()
        self.addPage(welcome)

        features = QWizardPage()
        features.setTitle("Choose optional capabilities")
        features_layout = QVBoxLayout(features)
        explanation = QLabel(
            "Every capability below is off by default. Enabling a capability only makes its controls "
            "available; connecting or trading still requires separate, explicit consent."
        )
        explanation.setWordWrap(True)
        features_layout.addWidget(explanation)
        self.broker = QCheckBox(
            "Broker connection — READ account/position/order data after OAuth; WRITE is limited to the dedicated Agentic account"
        )
        self.remote_data = QCheckBox(
            "Community remote market data — sends requested symbols, dates, and intervals to an unsupported third-party endpoint"
        )
        self.personal_ledger = QCheckBox(
            "Capital planning ledger — optional local records only; never transfers money"
        )
        for checkbox in (self.broker, self.remote_data, self.personal_ledger):
            checkbox.setChecked(False)
            checkbox.setWordWrap(True) if hasattr(checkbox, "setWordWrap") else None
            features_layout.addWidget(checkbox)
        note = QLabel(
            "Real-order automation is never enabled during onboarding. It can be unlocked later in Settings "
            "with an additional warning and typed confirmation."
        )
        note.setWordWrap(True)
        features_layout.addWidget(note)
        features_layout.addStretch()
        self.addPage(features)

        privacy = _AcknowledgementPage(
            "Local data and network boundaries",
            "GRANDE Alpha has no telemetry service. Configuration, receipts, imported datasets, and OAuth "
            "material stay on this PC. OAuth material is stored through Windows Credential Manager. When an "
            "optional integration is used, data is sent directly to that provider under its terms. Diagnostic "
            "exports are created only when you request one and should be reviewed before sharing.",
            "I understand what remains local and when data leaves this computer.",
        )
        self.addPage(privacy)

        risk = _AcknowledgementPage(
            "Risk and responsibility",
            "This software is an engineering and research tool, not individualized investment, tax, or legal "
            "advice. Backtests and shadow results are not promises. Leveraged and inverse ETFs target daily "
            "objectives, can be highly volatile, and can lose principal. Local stops cannot act while the app, "
            "network, broker, or computer is unavailable.",
            "I understand that I am responsible for data, configuration, monitoring, and any order I authorize.",
        )
        self.addPage(risk)

    def updated_config(self) -> AppConfig:
        return replace(
            self.config,
            onboarding_version=ONBOARDING_VERSION,
            disclosure_version=DISCLOSURE_VERSION,
            broker_connection_enabled=self.broker.isChecked(),
            live_trading_enabled=False,
            remote_market_data_enabled=self.remote_data.isChecked(),
            personal_ledger_enabled=self.personal_ledger.isChecked(),
        )

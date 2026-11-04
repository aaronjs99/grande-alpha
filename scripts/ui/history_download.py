"""User-started historical download with masked keys and explicit network consent."""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from grande_alpha.research.alpaca_history import AlpacaHistoricalDataProvider, save_alpaca_credentials


class HistoryDownloadDialog(QDialog):
    history_ready = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._task = None
        self._closed = False
        self.setWindowTitle("GRANDE · Download history")
        self.setMinimumWidth(350)
        self.resize(520, 420)
        box = QVBoxLayout(self)
        intro = QLabel("Alpaca's free historical feed · QQQ, TQQQ and SQQQ\n"
                       "Completed regular sessions; the latest 15 minutes are excluded.")
        intro.setWordWrap(True)
        box.addWidget(intro)
        signup = QLabel('<a href="https://app.alpaca.markets/">Create a free account or manage Alpaca API keys</a>')
        signup.setOpenExternalLinks(True)
        signup.setWordWrap(True)
        box.addWidget(signup)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.interval = QComboBox()
        self.interval.addItem("5 minutes · less memory", "5m")
        self.interval.addItem("1 minute", "1m")
        self.days = QSpinBox()
        self.days.setRange(1, 60)
        self.days.setValue(60)
        self.key = QLineEdit()
        self.secret = QLineEdit()
        for field in (self.key, self.secret):
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setMaxLength(512)
            field.setPlaceholderText("Leave blank to use saved keys")
        form.addRow("Bar size", self.interval)
        form.addRow("Calendar days", self.days)
        form.addRow("API key", self.key)
        form.addRow("API secret", self.secret)
        box.addLayout(form)
        self.remember = QCheckBox("Remember keys securely on this PC")
        self.remember.setChecked(True)
        box.addWidget(self.remember)
        self.allow_download = QCheckBox("Allow this market-data download")
        box.addWidget(self.allow_download)
        self.status = QLabel("60 days usually covers the 30 sessions needed by Compare.\n"
                             "Existing history is reused. No trading connection is created.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        row = QHBoxLayout()
        self.download_button = QPushButton("Download")
        self.download_button.setObjectName("primary")
        self.download_button.setEnabled(False)
        self.allow_download.toggled.connect(self.download_button.setEnabled)
        self.download_button.clicked.connect(self._begin)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self.download_button)
        row.addStretch()
        row.addWidget(close)
        box.addLayout(row)

    def _begin(self):
        if self._task is None or self._task.done():
            self._task = self.owner.owner._spawn(self._download())

    async def _download(self):
        if not self.allow_download.isChecked():
            return
        self.download_button.setEnabled(False)
        for widget in (self.interval, self.days, self.key, self.secret, self.remember, self.allow_download):
            widget.setEnabled(False)
        credentials = (self.key.text(), self.secret.text()) if self.key.text() or self.secret.text() else None
        self.key.clear()
        self.secret.clear()
        try:
            provider = AlpacaHistoricalDataProvider(credentials=credentials,
                                                  cache_dir=self.owner.owner.data_dir / "sandbox_cache")
            self.status.setText("Downloading small pages one at a time… Close cancels the download.")
            # A newly typed key must succeed against the provider before it can replace
            # remembered credentials; an existing cache cannot validate new credentials.
            bundle = await provider.fetch(self.days.value(), self.interval.currentData(), use_cache=credentials is None)
            if self._closed:
                return
            message = ""
            if credentials is not None and self.remember.isChecked():
                try:
                    await asyncio.to_thread(save_alpaca_credentials, credentials)
                except RuntimeError:
                    message = " History was saved, but keys could not be remembered."
            if not self._closed:
                self.status.setText(f"Saved {bundle.quality.sessions} sessions.{message}")
                self.history_ready.emit(provider.cache_path)
                if not message:
                    self._closed = True
                    self.accept()
        except asyncio.CancelledError:
            raise
        except (ValueError, RuntimeError) as error:
            if not self._closed:
                self.status.setText(str(error))
        except Exception:
            if not self._closed:
                self.status.setText("History could not be downloaded or saved. Check your connection and data-folder access.")
        finally:
            credentials = None
            if not self._closed:
                for widget in (self.interval, self.days, self.key, self.secret, self.remember, self.allow_download):
                    widget.setEnabled(True)
                self.download_button.setEnabled(self.allow_download.isChecked())

    def reject(self):
        self._closed = True
        self.key.clear()
        self.secret.clear()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        super().reject()

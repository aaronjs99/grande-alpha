"""A desktop-only X credential form; no token is exposed to MCP or app settings."""
from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGroupBox, QLabel, QLineEdit, QPushButton, QVBoxLayout


class XConnection(QGroupBox):
    connection_changed = Signal(bool)
    busy_changed = Signal()

    def __init__(self, credentials, parent=None):
        super().__init__('Connect X / Twitter', parent)
        self.credentials = credentials
        self.task = None
        layout = QVBoxLayout(self)
        info = QLabel('1. Open the X developer console and create an app with recent-search access.\n'
                      '2. Under Keys and tokens, copy its Bearer Token and paste it below.\n'
                      '3. Save the key, then start paper trading. This key is separate from your ChatGPT/OpenAI key.\n\n'
                      'X API usage is billed by X; check access and credits in your X console first. '
                      'GRANDE makes one search of up to 10 posts every 10 minutes while enabled. '
                      'Your watched symbols are sent to X. This is a sample, not all X activity.')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.console = QPushButton('Open X developer console')
        self.console.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://console.x.com/')))
        layout.addWidget(self.console)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setMaxLength(1000)
        self.token.setPlaceholderText('Paste the X API Bearer Token here')
        layout.addWidget(self.token)
        self.save = QPushButton('Save X key')
        self.save.clicked.connect(lambda: self._begin(False))
        layout.addWidget(self.save)
        self.remove = QPushButton('Remove saved X key')
        self.remove.clicked.connect(lambda: self._begin(True))
        layout.addWidget(self.remove)
        self.status = QLabel('Keys are stored in your system keychain. Never paste the key into chat. '
                             'A saved key is checked when monitoring starts.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def _begin(self, remove):
        if self.busy:
            return
        token = self.token.text()
        self.token.clear()
        self.save.setEnabled(False)
        self.remove.setEnabled(False)
        self.status.setText('Updating the system keychain…')
        self.task = asyncio.create_task(self._store(remove, token))
        self.busy_changed.emit()

    async def _store(self, remove, token):
        try:
            if remove:
                await self.credentials.remove()
            else:
                await self.credentials.save(token)
            self.status.setText('X key removed; monitoring is off.' if remove else
                                'X key saved; monitoring is selected. Start paper trading to check access and collect posts.')
            self.connection_changed.emit(not remove)
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))
        finally:
            self.task = None
            self.save.setEnabled(True)
            self.remove.setEnabled(True)
            self.busy_changed.emit()

    def shutdown(self):
        if self.task is not None:
            self.task.cancel()

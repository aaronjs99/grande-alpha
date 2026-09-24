"""Modeless desktop-first setup; opening the guide has no side effects."""
from __future__ import annotations

import os
import shlex
import sqlite3
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.chatgpt_connection import (
    ConnectionSetupError,
    add_connection,
    config_path,
    connection_settings,
    remove_connection,
)
from grande_alpha.ui.themes import set_widget_style

DESKTOP_GUIDE = "https://learn.chatgpt.com/docs/extend/mcp"
TUNNEL_GUIDE = "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels"
CHATGPT_GUIDE = "https://developers.openai.com/plugins/deploy/connect-chatgpt"
MODEL_GUIDE = "https://learn.chatgpt.com/docs/models"
TEST_PROMPT = (
    "Use the grande-alpha connection's get_research_context tool. Report the worker states, "
    "observation timestamps and research-only status. Do not start analysis or change settings. "
    "If the tool is unavailable, say so rather than guessing."
)


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class ChatGPTSetupDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.settings_path = config_path()
        self.saved = False
        self._operation_busy = False
        self.setWindowTitle("ChatGPT Astra setup")
        self.setModal(False)
        set_widget_style(self, "QPushButton#primary:disabled { background: #121b24; border-color: #2c4155; color: #596b7a; }")
        self.resize(720, 640)
        if screen := self.screen():
            area = screen.availableGeometry()
            self.resize(min(720, area.width() - 60), min(640, area.height() - 80))
        layout = QVBoxLayout(self)
        title = _label("Use Astra with GRANDE")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        layout.addWidget(_label("Recommended: ChatGPT desktop on this computer. No Terminal or API key needed."))
        self.progress = _label("")
        layout.addWidget(self.progress)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)

        first = self._page("1. Add GRANDE to ChatGPT")
        first.addWidget(_label(
            "Install and sign in to the ChatGPT desktop app first. Close its Settings window, "
            "then add the connection below. This works with local tools in the desktop app (Codex)."
        ))
        first.addWidget(_label(
            "This saves a GRANDE connection in ChatGPT's local settings and backs up any existing "
            "settings. Your model and permissions stay as you set them."
        ))
        self.add = QPushButton("Add GRANDE connection")
        self.add.setObjectName("primary")
        self.add.clicked.connect(self._add)
        first.addWidget(self.add)
        self.setup_status = _label("Ready to add the desktop connection")
        first.addWidget(self.setup_status)
        first.addStretch()

        second = self._page("2. Allow research access")
        second.addWidget(_label(
            "ChatGPT can read your watchlist, prices and research prompts, edit those prompts and "
            "watchlists, and start or stop research. OpenAI may process the information you share. "
            "Broker passwords, account balances, positions and order controls are excluded."
        ))
        self.permission_status = _label("Click the button below to allow research access and go to the test message.")
        second.addWidget(self.permission_status)
        self.allow = QPushButton("Allow research access and continue")
        self.allow.setObjectName("primary")
        self.allow.setMinimumHeight(44)
        self.allow.clicked.connect(self._continue)
        second.addWidget(self.allow)
        self.revoke = QPushButton("Turn research access off")
        self.revoke.clicked.connect(lambda: self.controller.set_agent_mcp_enabled(False))
        second.addWidget(self.revoke)
        second.addWidget(_label(
            "Keep GRANDE open. Stop agent, STOP + CANCEL, Disconnect or Exit turns access off. "
            "Turn it on again for a new session. You can test before connecting Robinhood."
        ))
        second.addStretch()

        third = self._page("3. Try Astra")
        third.addWidget(_label(
            "Restart ChatGPT desktop. Start a local chat in Codex on this computer, and select Astra "
            "in the model picker if your account offers it. Copy the test message below and paste it into that chat."
        ))
        self.copy_prompt = QPushButton("Copy test message")
        self.copy_prompt.setObjectName("primary")
        self.copy_prompt.clicked.connect(lambda: self._copy(TEST_PROMPT, "Test message copied. Paste it into ChatGPT."))
        third.addWidget(self.copy_prompt)
        self.request_status = _label("")
        third.addWidget(self.request_status)
        third.addWidget(_label(
            "Look for a tool result with the current worker status in ChatGPT. Astra can help with "
            "research when you ask; this does not become the continuous analyst or place trades."
        ))
        third.addStretch()
        self.access_status = _label("")
        layout.addWidget(self.access_status)
        navigation = QHBoxLayout()
        self.back = QPushButton("Back")
        self.next = QPushButton("Next")
        self.next.setObjectName("primary")
        self.back.clicked.connect(lambda: self._step(self.pages.currentIndex() - 1))
        self.next.clicked.connect(self._continue)
        navigation.addWidget(self.back)
        navigation.addStretch()
        navigation.addWidget(self.next)
        layout.addLayout(navigation)

        self.advanced = QPushButton("Advanced / browser setup / troubleshooting")
        self.advanced.setCheckable(True)
        layout.addWidget(self.advanced)
        self.details = QWidget()
        self.details.setVisible(False)
        self.advanced.toggled.connect(self.details.setVisible)
        details = QVBoxLayout(self.details)
        self.instructions = QTextBrowser()
        self.instructions.setOpenExternalLinks(True)
        self.instructions.setHtml(f"""
<p><b>Desktop setup</b> uses ChatGPT's local Codex host. Restart ChatGPT after adding or
removing the connection. Under <i>Settings → MCP servers</i>, check <i>grande-alpha</i>.
Workspace policy and project settings can limit access; this wizard does not override them.
If Astra is absent, check <a href="{MODEL_GUIDE}">model availability</a>.
<a href="{DESKTOP_GUIDE}">Official desktop instructions</a>.</p>
<p><b>Manual desktop setup:</b> choose Add server, name it <i>grande-alpha</i>, choose
STDIO and use the copied command. GRANDE must be installed in that Python environment.</p>
<p><b>Using ChatGPT in a browser?</b> The desktop shortcut does not connect a hosted chat.
Follow the <a href="{TUNNEL_GUIDE}">Secure MCP Tunnel guide</a> to install tunnel-client,
create a tunnel ID, supply its runtime key and associate your workspace.
Use the copied command for its local stdio setup, run its doctor check and keep the tunnel running.
Enable ChatGPT Developer mode under Settings → Security and login if available.
In Plugins → +, name it GRANDE Research, choose Tunnel and select its ID.
Review the tools, attach it in a new chat and use the test message.
<a href="{CHATGPT_GUIDE}">Official browser instructions</a>. Never paste keys into chat.</p>
<p><b>Settings:</b> only GRANDE's connection is added. A private backup named
config.toml.grande-backup-* is kept beside the settings file when it changes.
Remove saved connection only removes the unchanged entry this wizard manages.
Other local clients sharing these settings can also see it; research access still requires
your session opt-in. Revoking access cannot retract information already shared.</p>
<p><b>Packaged app?</b> Use a Python source installation for this connection. In the project
folder run <code>.venv/bin/python -m pip install -e .</code> on macOS/Linux
(or <code>.venv\\Scripts\\python.exe -m pip install -e .</code> on Windows), then open GRANDE there.</p>
<p>Official instructions checked September 24, 2026.</p>
""")
        details.addWidget(self.instructions)
        self.command = QTextBrowser()
        self.command.setMaximumHeight(65)
        self.command.setAccessibleName("GRANDE server command")
        self.settings = None
        self.server_command = ""
        if not getattr(sys, "frozen", False):
            self.settings = connection_settings(controller.agent_bridge.path)
            args = [self.settings["command"], *self.settings["args"]]
            self.server_command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
        self.command.setPlainText(self.server_command or "Use GRANDE's Python installation for this connection.")
        details.addWidget(self.command)
        self.copy_command = QPushButton("Copy command for manual setup")
        self.copy_command.setEnabled(self.settings is not None)
        self.copy_command.clicked.connect(lambda: self._copy(self.server_command, "Manual setup command copied"))
        details.addWidget(self.copy_command)
        self.remove = QPushButton("Remove saved connection and turn access off")
        self.remove.setEnabled(self.settings is not None)
        self.remove.clicked.connect(self._remove)
        details.addWidget(self.remove)
        layout.addWidget(self.details)
        self.status = _label("")
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.add.setEnabled(self.settings is not None)
        if self.settings is None:
            self.setup_status.setText("This packaged app needs a Python source installation for the connection. See Advanced.")
        controller.agent_mcp_changed.connect(self._access_changed)
        controller.agent_mcp_context_requested.connect(self._request_received)
        # Enter must not unexpectedly activate a permission button on a different page.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._access_changed(bool(controller.agent_bridge.session))
        self.set_operation_busy(False)
        self._step(0)

    def _page(self, title: str) -> QVBoxLayout:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)
        heading = _label(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        self.pages.addWidget(page)
        return layout

    def _step(self, index: int) -> None:
        self.pages.setCurrentIndex(max(0, min(2, index)))
        index = self.pages.currentIndex()
        self.progress.setText(f"STEP {index + 1} OF 3")
        self.back.setEnabled(index > 0)
        self.next.setVisible(index == 0)
        self.next.setEnabled(self.saved and not self._operation_busy)
        self.allow.setEnabled(not self._blocked_reason())

    def _blocked_reason(self) -> str:
        if self._operation_busy:
            return "GRANDE is finishing a broker action. Wait for it to finish, then try again. Back and Close still work."
        if self.controller.shadow_only_runtime:
            return "Research access is unavailable in scheduled shadow. Open the regular GRANDE app to continue."
        return ""

    def set_operation_busy(self, busy: bool) -> None:
        changed = self._operation_busy != busy
        self._operation_busy = busy
        reason = self._blocked_reason()
        if reason or changed:
            self.permission_status.setText(reason or "Ready. Click Allow research access and continue.")
        self.add.setEnabled(self.settings is not None and not busy)
        self._step(self.pages.currentIndex())

    def _continue(self) -> None:
        if self.pages.currentIndex() == 0:
            if self.saved and not self._operation_busy:
                self._step(1)
            return
        if self.pages.currentIndex() != 1:
            return
        if reason := self._blocked_reason():
            self.permission_status.setText(reason)
            return
        self.permission_status.setText("Starting research access…")
        try:
            self.controller.set_agent_mcp_enabled(True)
        except PermissionError:
            self.permission_status.setText("GRANDE cannot write its research connection file. Check access to the app's data folder, then click the button to retry.")
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xff
            if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                self.permission_status.setText("The research connection file is busy. Close any other GRANDE windows, then click the button to retry.")
            else:
                self.permission_status.setText("GRANDE cannot open its research connection file. Check that the app's data folder is writable, then retry.")
        except Exception:
            self.permission_status.setText("Research access could not start. Close other GRANDE windows and try again. Access has not been granted.")
        else:
            if self.controller.agent_bridge.session:
                self.permission_status.setText("Research access is on.")
                self.status.setText("")
                self._step(2)
            else:
                self.permission_status.setText("Research access is still off. Click the button to retry.")

    def _add(self) -> None:
        if self.settings is None:
            return
        try:
            add_connection(self.settings_path, self.settings)
        except (OSError, ConnectionSetupError) as exc:
            self.saved = False
            self.setup_status.setText(str(exc) if isinstance(exc, ConnectionSetupError) else "Could not save settings. Check folder access and try again.")
        else:
            self.saved = True
            self.setup_status.setText("Connection saved. Click Next to choose research access.")
        self._step(self.pages.currentIndex())

    def _remove(self) -> None:
        self.controller.set_agent_mcp_enabled(False)
        try:
            remove_connection(self.settings_path, self.settings)
        except (OSError, ConnectionSetupError) as exc:
            self.status.setText(str(exc) if isinstance(exc, ConnectionSetupError) else "Could not update settings. Access is off; remove GRANDE in ChatGPT settings.")
        else:
            self.saved = False
            self.setup_status.setText("Connection removed. Restart ChatGPT to refresh its tools.")
            self.status.setText("Research access is off. Research already running can be stopped from the Agent desk.")
            self._step(0)

    def _access_changed(self, enabled: bool) -> None:
        self.allow.setText("Continue to test message" if enabled else "Allow research access and continue")
        self.revoke.setVisible(enabled)
        self.permission_status.setText(self._blocked_reason() or (
            "Research access is on. Continue to the test message or turn access off below." if enabled else
            "Click Allow research access and continue to go to the test message."
        ))
        self.access_status.setText("Research access ON · trading remains off" if enabled else "Research access OFF")
        self.request_status.setText(
            "Waiting for a research request. Paste the test message into ChatGPT." if enabled else
            "Access is off. Return to step 2 to allow a new connection test."
        )
        self._step(self.pages.currentIndex())

    def _request_received(self) -> None:
        self.request_status.setText("A research request reached GRANDE. Check ChatGPT for its tool result; the requesting app/model is not identified here.")

    def _copy(self, text: str, status: str) -> None:
        QApplication.clipboard().setText(text)
        self.status.setText(status)

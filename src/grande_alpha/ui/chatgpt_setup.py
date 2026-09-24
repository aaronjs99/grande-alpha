"""Offline setup help. Opening it never connects, installs, or changes permissions."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

TUNNEL_GUIDE = "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels"
CHATGPT_GUIDE = "https://developers.openai.com/plugins/deploy/connect-chatgpt"
MODEL_GUIDE = "https://learn.chatgpt.com/docs/models"
TEST_PROMPT = (
    "Use GRANDE Research's get_research_context tool. Report the worker states, "
    "observation timestamps and research-only status. Do not start analysis or change settings."
)


class ChatGPTSetupDialog(QDialog):
    def __init__(self, bridge_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ChatGPT Astra setup")
        self.setModal(False)
        self.resize(780, 700)
        screen = self.screen()
        if screen:
            area = screen.availableGeometry()
            self.resize(min(780, area.width() - 60), min(700, area.height() - 80))
        layout = QVBoxLayout(self)
        title = QLabel("Connect GRANDE research to ChatGPT Astra")
        title.setWordWrap(True)
        layout.addWidget(title)
        source_install = not getattr(sys, "frozen", False)
        args = [sys.executable, "-m", "grande_alpha.agent_mcp", "--bridge", str(bridge_path.resolve())]
        self.server_command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
        command_help = (
            "Use the Python environment where GRANDE is installed. In the project folder, install "
            "it with <code>.venv/bin/python -m pip install -e .</code> on macOS/Linux "
            "(<code>.venv\\Scripts\\python.exe -m pip install -e .</code> on Windows). "
            "The command below uses this running app's environment."
            if source_install else
            "This packaged desktop cannot provide a Python command. Install GRANDE in a source Python "
            "environment and reopen this guide there to copy its server command."
        )
        self.instructions = QTextBrowser()
        self.instructions.setOpenExternalLinks(True)
        self.instructions.setHtml(f"""
<p><b>Setup guide · checked September 24, 2026</b></p>
<p>GRANDE supplies a local MCP server. For ChatGPT's web connection, this guide uses
OpenAI's Secure MCP Tunnel. The existing <i>Copy MCP client configuration</i> JSON
is for local stdio clients; it is not a ChatGPT server URL.</p>
<ol>
<li><b>Prepare GRANDE.</b> Save your research setup, connect Robinhood, then enable
<i>research MCP for this app session</i> under <i>Prompts + AI connections</i>.
Keep GRANDE open. {command_help}</li>
<li><b>Check OpenAI access.</b> Enable Developer mode in ChatGPT's
<i>Settings → Security and login</i>, if available. Platform tunnel permissions are separate;
ask your administrator if the controls are missing. Select Astra in ChatGPT's model picker
when your account offers it; GRANDE cannot select or unlock the model.</li>
<li><b>Create the tunnel.</b> Follow the <a href="{TUNNEL_GUIDE}">official tunnel guide</a>
to obtain a tunnel ID and runtime API key, associate your ChatGPT workspace,
and install <code>tunnel-client</code> on the computer running GRANDE.
Keep the key in the tunnel client's environment, outside GRANDE and chat messages.</li>
<li><b>Point the tunnel at GRANDE.</b> In the guide's local stdio setup, use
<code>sample_mcp_stdio_local</code> and supply the copied server command as the
<code>--mcp-command</code> value. Substitute your tunnel ID. Run the guide's
<code>doctor</code> check, then keep <code>tunnel-client run</code> active.</li>
<li><b>Add it to ChatGPT.</b> Open <i>Plugins → +</i>. Name it <i>GRANDE Research</i>.
Choose <i>Connection → Tunnel</i> and select your tunnel or enter its ID.
Create the connection and review its discovered research tools.
See <a href="{CHATGPT_GUIDE}">OpenAI's connection instructions</a>.</li>
<li><b>Verify with Astra.</b> Start a conversation, select Astra if available,
and attach GRANDE Research from the tools menu. Use <i>Copy test prompt</i> below.
A response from <code>get_research_context</code> confirms tool access;
opening this guide or selecting a model does not.</li>
</ol>
<p><b>If it does not connect:</b> check that GRANDE is open, research MCP is enabled,
the tunnel is running, and its workspace association matches ChatGPT. Missing developer
or tunnel controls require the relevant account/workspace access.
<a href="{MODEL_GUIDE}">Model availability and selection</a> may vary by account.</p>
<p><b>What this enables:</b> ChatGPT can inspect research and request research actions.
It may process shared prompts and observations. This does not replace the continuous
Ollama analyst or enable real-money orders. STOP, Disconnect and Exit revoke GRANDE's MCP
access; stop the separate tunnel client when finished.</p>
""")
        layout.addWidget(self.instructions, 1)
        self.command = QTextBrowser()
        self.command.setMaximumHeight(86)
        self.command.setPlainText(self.server_command if source_install else "Server command unavailable in packaged desktop")
        self.command.setAccessibleName("GRANDE MCP server command")
        layout.addWidget(self.command)
        self.copy_command = QPushButton("Copy server command")
        self.copy_command.setEnabled(source_install)
        self.copy_command.clicked.connect(lambda: self._copy(self.server_command, "Server command copied"))
        self.copy_instructions = QPushButton("Copy instructions")
        self.copy_instructions.clicked.connect(lambda: self._copy(
            self.instructions.toPlainText() + "\n\nServer command:\n" + self.command.toPlainText()
            + "\n\nOfficial sources:\n" + "\n".join((TUNNEL_GUIDE, CHATGPT_GUIDE, MODEL_GUIDE)),
            "Instructions copied",
        ))
        self.copy_prompt = QPushButton("Copy test prompt")
        self.copy_prompt.clicked.connect(lambda: self._copy(TEST_PROMPT, "Read-only test prompt copied"))
        for button in (self.copy_command, self.copy_instructions, self.copy_prompt):
            layout.addWidget(button)
        self.status = QLabel("Instructions only · no connection or permission changed")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def _copy(self, text: str, status: str) -> None:
        QApplication.clipboard().setText(text)
        self.status.setText(status)

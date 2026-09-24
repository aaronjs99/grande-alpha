"""Conversational local AI panel with explicit, bounded paper-setting proposals."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import httpx
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.agent_chat import LocalAgentChat, chat_context


def text_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class AgentChat(QWidget):
    configure_model = Signal()

    def __init__(self, controller, save_current, parent=None):
        super().__init__(parent)
        self.controller, self.save_current = controller, save_current
        self.client = LocalAgentChat()
        self.messages = []
        self._task = None
        self._proposal = None
        self._base_settings = None
        self._syncing = False
        self._closed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(text_label(
            "Talk to the local AI about your paper portfolio and give research directions. "
            "It uses the current paper results and recent observations; it has no live web search. "
            "Replies are suggestions. Review the changes below before applying them."
        ))
        self.model_status = text_label("")
        layout.addWidget(self.model_status)
        self.choose_model = QPushButton("Choose local AI model")
        self.choose_model.clicked.connect(self.configure_model.emit)
        layout.addWidget(self.choose_model)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setMaximumBlockCount(120)
        self.transcript.setMinimumHeight(190)
        self.transcript.setMaximumHeight(260)
        self.transcript.setPlaceholderText("Ask: Why is this paper session losing money? What should we examine?")
        layout.addWidget(self.transcript)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Give directions or ask a question…")
        self.input.setFixedHeight(80)
        layout.addWidget(self.input)
        actions = QHBoxLayout()
        self.send = QPushButton("Send to local AI")
        self.send.setObjectName("primary")
        self.send.clicked.connect(self._send)
        self.cancel = QPushButton("Cancel reply")
        self.cancel.clicked.connect(self.cancel_reply)
        self.cancel.setEnabled(False)
        self.clear = QPushButton("Clear chat")
        self.clear.clicked.connect(self._clear)
        for button in (self.send, self.cancel, self.clear):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = text_label("Conversation lasts until you close the app. Applied directions and controls are saved.")
        layout.addWidget(self.status)
        self.preview = text_label("No suggested changes to apply.")
        layout.addWidget(self.preview)
        self.apply = QPushButton("Apply suggested changes")
        self.apply.setEnabled(False)
        self.apply.clicked.connect(self._apply)
        layout.addWidget(self.apply)

        controls = QFormLayout()
        self.paused = QCheckBox("Pause new paper buys · continue managing exits")
        self.paused.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #8ea3af; "
                                 "border-radius: 3px; } QCheckBox::indicator:checked { background: #159e7b; }")
        controls.addRow(self.paused)
        self.max_positions = QSpinBox()
        self.max_positions.setRange(1, 4)
        self.max_positions.setMaximumWidth(180)
        self.max_exposure = QSpinBox()
        self.max_exposure.setRange(5, 40)
        self.max_exposure.setSuffix("%")
        self.max_exposure.setMaximumWidth(180)
        controls.addRow("Adaptive: maximum positions", self.max_positions)
        controls.addRow("Adaptive: maximum virtual exposure", self.max_exposure)
        layout.addLayout(controls)
        layout.addWidget(text_label(
            "These controls save automatically and affect new entries. Lower limits do not sell existing holdings. "
            "Exposure is measured against starting virtual cash. Research prompts guide the AI; "
            "adaptive trades still follow the price rules. No setting guarantees a profit."
        ))
        self.paused.toggled.connect(self._save_controls)
        self.max_positions.valueChanged.connect(self._save_controls)
        self.max_exposure.valueChanged.connect(self._save_controls)
        controller.agent_settings_changed.connect(self.sync_settings)
        controller.agent_chat_cancel_requested.connect(self.cancel_reply)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.sync_settings()

    def sync_settings(self):
        self._syncing = True
        settings = self.controller.agent.settings
        self.paused.setChecked(settings.paper_entries_paused)
        self.max_positions.setValue(settings.paper_max_positions)
        self.max_exposure.setValue(settings.paper_max_exposure_pct)
        self.model_status.setText("Local Ollama model: " + (settings.local_ai_model or "Choose an installed model to chat"))
        self._syncing = False

    def _save_controls(self):
        if self._syncing or self._closed:
            return
        values = dict(paper_entries_paused=self.paused.isChecked(), paper_max_positions=self.max_positions.value(),
                      paper_max_exposure_pct=self.max_exposure.value())
        try:
            # A pause must remain available even if another setup editor has an invalid draft.
            self.controller.save_agent_preferences(replace(self.controller.agent.settings, **values), notify=False)
            self.sync_settings()
            self.status.setText("Paper controls saved. They affect new entries; existing holdings keep their exit rules.")
        except (OSError, ValueError, RuntimeError):
            self.status.setText("Paper controls could not be saved. No new controls applied.")
            self.sync_settings()

    def _tick(self):
        self.status.setText(f"Local AI is replying · {int(time.monotonic() - self._started)}s · Cancel is available")

    def _send(self):
        if self._task is not None and not self._task.done():
            return
        message = self.input.toPlainText().strip()
        if not message or len(message) > 2000:
            self.status.setText("Enter a message of 1–2,000 characters.")
            return
        if not self.save_current():
            self.status.setText("Correct the unsaved setup fields before sending.")
            return
        if not self.controller.agent.settings.local_ai_model:
            self.status.setText("Choose your installed Ollama model first, for example qwen2.5:3b.")
            self.configure_model.emit()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.status.setText("The app event loop is unavailable. Restart GRANDE to use chat.")
            return
        self._proposal = None
        self.apply.setEnabled(False)
        self.preview.setText("Waiting for a reply. No changes applied.")
        self._base_settings = self.controller.agent.settings
        self.messages.append({"role": "user", "content": message})
        self.messages = self.messages[-16:]
        self.transcript.appendPlainText("You: " + message)
        self.input.clear()
        self.send.setEnabled(False)
        self.cancel.setEnabled(True)
        self._started = time.monotonic()
        self._tick()
        self._timer.start()
        self._task = loop.create_task(self._reply())

    async def _reply(self):
        try:
            response = await self.client.reply(self._base_settings.local_ai_model, list(self.messages),
                                               chat_context(self.controller.agent))
            if self._closed or asyncio.current_task().cancelling():
                return
            self.messages.append({"role": "assistant", "content": response.reply})
            self.transcript.appendPlainText("Local AI: " + response.reply)
            self._proposal = response.changes
            labels = {"research_brief": "Team research directions", "paper_entries_paused": "Pause new paper buys",
                      "paper_max_positions": "Adaptive maximum positions", "paper_max_exposure_pct": "Adaptive exposure (%)"}
            self.preview.setText("Suggested changes (not applied):\n" + "\n".join(
                f"{labels[key]}: {getattr(self._base_settings, key)} → {value}" for key, value in response.changes.items()
            ) if response.changes else "The AI did not propose any setting changes.")
            self.apply.setEnabled(bool(response.changes))
            self.status.setText("Reply received. Review any proposed changes, then click Apply.")
        except asyncio.CancelledError:
            pass
        except (TimeoutError, httpx.TimeoutException):
            self.status.setText("Local AI took too long. No changes applied. Try a shorter question or retry when analysis is idle.")
        except httpx.HTTPStatusError:
            self.status.setText("Ollama rejected the request. Check that the selected model is installed, then retry.")
        except httpx.RequestError:
            self.status.setText("Cannot reach Ollama. Open Ollama on this Mac, then try Send again.")
        except Exception:
            self.status.setText("The AI returned an invalid reply. No changes applied. Please try again.")
        finally:
            if not self._closed:
                self._timer.stop()
                self.send.setEnabled(True)
                self.cancel.setEnabled(False)

    def _apply(self):
        if not self._proposal or not self.save_current():
            return
        if self.controller.agent.settings != self._base_settings:
            self.status.setText("Settings changed since this reply. Ask again so the AI uses your current configuration.")
            self.apply.setEnabled(False)
            return
        try:
            self.controller.save_agent_preferences(replace(self.controller.agent.settings, **self._proposal))
        except (ValueError, RuntimeError, OSError):
            self.status.setText("Suggested changes could not be saved. No changes applied.")
            return
        self.transcript.appendPlainText("GRANDE: Suggested settings applied and saved. No broker orders sent.")
        self.preview.setText("Changes applied and saved. Paper controls affect new entries; briefs guide future AI analysis.")
        self.status.setText("Applied. The running session keeps its existing portfolio and recorded P&L.")
        self._proposal = None
        self.apply.setEnabled(False)

    def cancel_reply(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self.status.setText("Reply canceled. No suggested changes applied.")
        self._timer.stop()
        self._proposal = None
        self.apply.setEnabled(False)
        self.preview.setText("No suggested changes to apply.")

    def _clear(self):
        self.cancel_reply()
        self.messages = []
        self.transcript.clear()
        self.status.setText("Chat cleared. Your saved settings and applied directions are unchanged.")

    def shutdown(self):
        self.cancel_reply()
        self._closed = True

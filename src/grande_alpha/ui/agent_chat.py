"""Conversational local AI panel with explicit, bounded paper-setting proposals."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import httpx
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.agent_chat import LocalAgentChat, chat_context
from grande_alpha.ui.agent_desk import ChatTranscript


def text_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class AgentChat(QWidget):
    configure_model = Signal()
    controls_changed = Signal()
    connections_requested = Signal()

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
        self.setObjectName("agentChat")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)
        title = text_label("TALK TO YOUR AI TEAM")
        title.setObjectName("chatTitle")
        layout.addWidget(title)
        model_row = QHBoxLayout()
        self.model_status = text_label("")
        self.model_status.setObjectName("metricCaption")
        model_row.addWidget(self.model_status, 1)
        self.choose_model = QPushButton("Model")
        self.choose_model.setToolTip("Choose your installed local AI model")
        self.choose_model.clicked.connect(self.configure_model.emit)
        model_row.addWidget(self.choose_model)
        layout.addLayout(model_row)
        self.transcript = ChatTranscript()
        layout.addWidget(self.transcript, 1)
        self.review_box = QFrame()
        self.review_box.setObjectName("chatProposal")
        review = QVBoxLayout(self.review_box)
        review.setContentsMargins(12, 12, 12, 12)
        review.addWidget(text_label("PROPOSED CHANGES"))
        self.preview = text_label("No suggested changes to apply.")
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        review.addWidget(self.preview)
        self.apply = QPushButton("Apply changes")
        self.apply.setObjectName("primary")
        self.apply.setEnabled(False)
        self.apply.clicked.connect(self._apply)
        review.addWidget(self.apply)
        self.transcript.attach_review(self.review_box)
        self.review_box.hide()
        self.status = text_label("Paper research · replies use your latest snapshot. Applied directions are saved.")
        self.status.setObjectName("metricCaption")
        layout.addWidget(self.status)
        quick = QHBoxLayout()
        self.explain = QPushButton("Explain last trade")
        self.explain.clicked.connect(lambda: self._prompt("Explain the latest paper trade, including costs and why it was entered or exited."))
        self.review_risk = QPushButton("Review risk")
        self.review_risk.clicked.connect(lambda: self._prompt("Review our current paper losses and exposure. What do the observations support changing?"))
        quick.addWidget(self.explain)
        quick.addWidget(self.review_risk)
        layout.addLayout(quick)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Give your team directions…")
        self.input.setFixedHeight(76)
        layout.addWidget(self.input)
        actions = QHBoxLayout()
        self.send = QPushButton("Send")
        self.send.setToolTip("Send your question and current paper snapshot to the selected local model")
        self.send.setObjectName("primary")
        self.send.clicked.connect(self._send)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.cancel_reply)
        self.cancel.setEnabled(False)
        self.clear = QPushButton("Clear")
        self.clear.clicked.connect(self._clear)
        for button in (self.send, self.cancel, self.clear):
            actions.addWidget(button)
        layout.addLayout(actions)
        footer = QHBoxLayout()
        self.controls_toggle = QPushButton("Paper controls")
        self.connections = QPushButton("AI connections")
        self.connections.clicked.connect(self.connections_requested.emit)
        footer.addWidget(self.controls_toggle)
        footer.addWidget(self.connections)
        layout.addLayout(footer)
        self.controls_box = QDialog(self)
        self.controls_box.setWindowTitle("Paper controls · saved automatically")
        self.controls_box.setMinimumWidth(330)
        self.controls_toggle.clicked.connect(self.controls_box.show)
        controls = QFormLayout(self.controls_box)
        controls.setContentsMargins(18, 18, 18, 18)
        controls.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.paused = QCheckBox("Pause new paper buys")
        self.paused.setToolTip("Continue managing existing holdings with their normal exit rules")
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
        controls.addRow("Max positions", self.max_positions)
        controls.addRow("Max virtual exposure", self.max_exposure)
        controls.addRow(text_label(
            "Limits apply to adaptive new entries and save automatically. Existing holdings retain their exit rules."
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

    def _prompt(self, message):
        self.input.setPlainText(message)
        self.input.setFocus()

    def sync_settings(self):
        self._syncing = True
        settings = self.controller.agent.settings
        self.paused.setChecked(settings.paper_entries_paused)
        self.max_positions.setValue(settings.paper_max_positions)
        self.max_exposure.setValue(settings.paper_max_exposure_pct)
        self.model_status.setText((settings.local_ai_model + " · Local AI") if settings.local_ai_model else "No local model selected")
        self._syncing = False
        self.controls_changed.emit()

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
        self.review_box.hide()
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
            self.review_box.setVisible(bool(response.changes))
            QTimer.singleShot(0, self.transcript._scroll_to_latest)
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
        self.review_box.hide()
        self.apply.setEnabled(False)
        self.preview.setText("No suggested changes to apply.")

    def _clear(self):
        self.cancel_reply()
        self.messages = []
        self.transcript.clear()
        self.status.setText("Chat cleared. Your saved settings and applied directions are unchanged.")

    def shutdown(self):
        self.cancel_reply()
        self.controls_box.close()
        self._closed = True

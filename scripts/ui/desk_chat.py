"""A local, read-only conversation using Antonio's plain-text chat bubbles."""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from grande_alpha.research.agent_chat import LocalAgentChat, chat_context
from grande_alpha.ui.agent_desk import ChatTranscript


class DeskChatDialog(QDialog):
    def __init__(self, owner, *, service=None) -> None:
        super().__init__(owner)
        self.owner = owner
        self.service = service or LocalAgentChat()
        self.messages = []
        self._task: asyncio.Task | None = None
        self.setWindowTitle("GRANDE · AI chat")
        self.setMinimumSize(340, 400)
        self.resize(540, 640)
        box = QVBoxLayout(self)
        heading = QLabel("AI CHAT")
        heading.setObjectName("chatTitle")
        box.addWidget(heading)
        note = QLabel("Local Ollama · advisory only")
        note.setToolTip("Sends a bounded paper snapshot and your last six messages to 127.0.0.1 only. "
                        "No account identifiers or broker orders. It cannot change settings or start sessions.")
        box.addWidget(note)
        self.model = QLineEdit()
        self.model.setPlaceholderText("Installed Ollama model name")
        self.model.setAccessibleName("Installed local Ollama chat model")
        box.addWidget(self.model)
        self.transcript = ChatTranscript()
        self.transcript.empty.setText("Ask about the latest paper run.")
        box.addWidget(self.transcript, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Ask your team…")
        self.entry.setAccessibleName("Message to local research assistant")
        self.entry.setMaxLength(2000)
        self.entry.returnPressed.connect(self.send)
        box.addWidget(self.entry)
        row = QHBoxLayout()
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
        row.addWidget(self.clear_button)
        row.addStretch()
        self.cancel_button = QPushButton("Cancel reply")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send)
        row.addWidget(self.send_button)
        box.addLayout(row)

    def send(self) -> None:
        if self._task is not None and not self._task.done():
            return
        text, model = self.entry.text().strip(), self.model.text().strip()
        if not text or not model:
            self.status.setText("Enter an installed model name and a message.")
            return
        self.messages.append({"role": "user", "content": text})
        self.messages = self.messages[-6:]
        self.transcript.appendPlainText("You: " + text)
        self.entry.clear()
        self._task = self.owner._spawn(self._reply(model))

    async def _reply(self, model: str) -> None:
        self._set_busy(True)
        self.status.setText("Thinking locally…")
        research = self.owner._status.get("research")
        reachable = self.owner._status.get("worker_reachable") is True
        if not reachable:
            research = self.owner._last_worker_status.get("research")
        try:
            reply = await self.service.reply(model, list(self.messages), chat_context(research, reachable=reachable))
            self.messages.append({"role": "assistant", "content": reply})
            self.messages = self.messages[-6:]
            self.transcript.appendPlainText("Team: " + reply)
            self.status.setText("Advisory reply · review it against the paper data")
        except asyncio.CancelledError:
            self.status.setText("Reply cancelled")
        except Exception as error:
            self.status.setText(f"Local chat unavailable: {error}")
        finally:
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.entry, self.model, self.send_button, self.clear_button):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def clear(self) -> None:
        self.messages.clear()
        self.transcript.clear()
        self.status.clear()

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def reject(self) -> None:
        self.cancel()
        super().reject()

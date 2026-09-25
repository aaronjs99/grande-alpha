"""Reusable presentation components for the local session window."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def display_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def review_text(review: dict[str, Any]) -> str:
    """Render reviewed terms using only the masked account value."""
    lines = [
        f"Account: {review.get('account_masked') or 'Not reported'}",
        f"Symbols: {', '.join(review.get('allowed_symbols') or ()) or 'None'}",
        f"Starts: {review.get('starts_at') or 'Not reported'}",
        f"Expires: {review.get('expires_at') or 'No expiry reported'}",
        "",
        "Risk limits",
    ]
    lines.extend(
        f"  {key.removeprefix('max_').replace('_', ' ')}: {display_value(value)}"
        for key, value in sorted((review.get("limits") or {}).items())
    )
    lines.extend(("", "Allocation policy"))
    lines.extend(
        f"  {key.replace('_', ' ')}: {display_value(value)}"
        for key, value in sorted((review.get("allocation_policy") or {}).items())
    )
    lines.extend(("", "Earnings thresholds"))
    lines.extend(
        f"  {key.replace('_', ' ')}: {display_value(value)}"
        for key, value in sorted((review.get("earnings_thresholds") or {}).items())
    )
    lines.extend(("", f"Scope: {review.get('scope_digest') or 'Missing'}"))
    return "\n".join(lines)


class PreciseDoubleSpinBox(QDoubleSpinBox):
    """Show supported precision without trailing zeroes."""

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt API
        return f"{value:.8f}".rstrip("0").rstrip(".")


class StatusCard(QFrame):
    """Responsive status tile with selectable value text."""

    def __init__(self, title: str, value: str = "—") -> None:
        super().__init__()
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(78)
        self.setMaximumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.value = QLabel(value)
        self.value.setObjectName("cardValue")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.value.setMinimumWidth(0)
        self.value.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(self.value)


class StartApprovalDialog(QDialog):
    """Request typed approval for one exact, worker-reviewed session scope."""

    def __init__(self, parent: QWidget, review: dict[str, Any]) -> None:
        super().__init__(parent)
        self.review = review
        self.phrase = f"AUTHORIZE {review['scope_digest']}"
        self.setWindowTitle("Approve this exact session")
        self.setModal(True)
        self.resize(660, 420)
        layout = QVBoxLayout(self)

        title = QLabel("Final review before real-money automation")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        summary = QPlainTextEdit(review_text(review))
        summary.setReadOnly(True)
        summary.setAccessibleName("Complete worker-reviewed session terms")
        summary.setMinimumHeight(190)
        layout.addWidget(summary, 1)
        warning = QLabel(
            "The worker rechecks this exact account, candidate, permit, market session, and risk "
            "envelope. Stop blocks new local submissions; already-sent broker orders can still fill."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        prompt = QLabel(f"Type this exact phrase to continue:\n{self.phrase}")
        prompt.setWordWrap(True)
        prompt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(prompt)
        self.entry = QLineEdit()
        self.entry.setAccessibleName("Exact session approval phrase")
        self.entry.setPlaceholderText("No approval is created until the phrase matches")
        layout.addWidget(self.entry)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.approve_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.approve_button.setText("Approve and start")
        self.approve_button.setObjectName("primary")
        self.approve_button.setEnabled(False)
        self.entry.textChanged.connect(
            lambda text: self.approve_button.setEnabled(text.strip() == self.phrase)
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

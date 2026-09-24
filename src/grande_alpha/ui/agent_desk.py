"""Presentation components for the Agent desk; no trading or network actions."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.ui.themes import color

PACIFIC = ZoneInfo("America/Los_Angeles")


class ActivityDelegate(QStyledItemDelegate):
    """Compact event badges; the model and tooltip retain the full event text."""

    def paint(self, painter, option, index):
        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        text = opts.text
        match = re.match(r"^\[([A-Z]+)\]\s*(.*)", text, re.DOTALL)
        if not match:
            return super().paint(painter, option, index)
        tag, summary = match.groups()
        opts.text = ""
        style = opts.widget.style() if opts.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opts, painter, opts.widget)
        shade = color({"RISK": "#a77924", "WARN": "#b74b63", "FILL": "#16845e", "IDEA": "#16845e",
                       "BOOK": "#9273c8"}.get(tag, "#566e7c"), base="light")
        painter.save()
        badge = QRectF(option.rect.left() + 5, option.rect.top() + 5, 46, 17)
        tint = QColor(shade)
        tint.setAlpha(28)
        painter.setPen(QColor(shade))
        painter.setBrush(tint)
        painter.drawRoundedRect(badge, 4, 4)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, tag)
        first, separator, rest = summary.partition(" · ")
        first_rect = option.rect.adjusted(59, 1, -5, -19)
        painter.setPen(QColor(color("#27343e", base="light")))
        painter.drawText(first_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(first, Qt.TextElideMode.ElideRight, first_rect.width()))
        detail = rest if separator else summary
        second_rect = option.rect.adjusted(5, 23, -5, -1)
        painter.setPen(QColor(color("#627580", base="light")))
        painter.drawText(second_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, second_rect.width()))
        painter.restore()


class PacificAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        result = []
        for value in values:
            try:
                at = datetime.fromtimestamp(value, PACIFIC)
                result.append(at.strftime("%m/%d\n%I:%M %p" if spacing >= 86400 else
                                          "%I:%M:%S" if spacing < 60 else "%I:%M %p"))
            except (ValueError, OverflowError, OSError):
                result.append("")
        return result


class ChatTranscript(QScrollArea):
    """Bounded, selectable plain-text bubbles with independent scrolling."""

    def __init__(self):
        super().__init__()
        self.setObjectName("chatTranscript")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumSize(0, 100)
        self.content = QWidget()
        self.content.setObjectName("chatMessages")
        self.rows = QVBoxLayout(self.content)
        self.rows.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.rows.setContentsMargins(0, 8, 0, 8)
        self.rows.setSpacing(14)
        self.rows.addStretch()
        self.setWidget(self.content)
        self._entries = []
        self.review = None
        self.empty = QLabel("Ask your team\n\nReview the latest paper trades, explore a decision,\nor give research directions.")
        self.empty.setWordWrap(True)
        self.empty.setTextFormat(Qt.TextFormat.PlainText)
        self.empty.setObjectName("chatEmpty")
        self.rows.insertWidget(0, self.empty)

    def attach_review(self, widget):
        self.review = widget
        self.rows.insertWidget(self.rows.count() - 1, widget)

    def appendPlainText(self, text):
        sender, _, message = text.partition(": ")
        row = QWidget()
        row.setObjectName("chatMessageRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        bubble = QFrame()
        bubble.setObjectName("chatBubble")
        bubble.setProperty("speaker", "user" if sender == "You" else "assistant")
        box = QVBoxLayout(bubble)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(6)
        heading = QLabel(sender.upper())
        heading.setObjectName("chatSpeaker")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        body = QLabel(message or text)
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setWordWrap(True)
        body.setMinimumWidth(0)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(heading)
        box.addWidget(body)
        if sender == "You":
            line.addSpacing(22)
        line.addWidget(bubble, 1)
        if sender != "You":
            line.addSpacing(10)
        bar = self.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 30
        self.empty.hide()
        self.rows.insertWidget(self.rows.indexOf(self.review) if self.review else self.rows.count() - 1, row)
        self._entries.append((row, text))
        if len(self._entries) > 60:
            old, _ = self._entries.pop(0)
            self.rows.removeWidget(old)
            old.deleteLater()
        if follow or sender == "You":
            QTimer.singleShot(0, self._scroll_to_latest)

    def _scroll_to_latest(self):
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def toPlainText(self):
        return "\n\n".join(text for _, text in self._entries)

    def clear(self):
        for row, _ in self._entries:
            self.rows.removeWidget(row)
            row.deleteLater()
        self._entries.clear()
        self.empty.show()

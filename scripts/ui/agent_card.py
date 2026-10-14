"""Paint-only agent motion; never changes layout, worker state, or trading cadence."""

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QWidget

from grande_alpha.ui.themes import color


class AgentAvatar(QWidget):
    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = QColor(accent)
        self.level = self.pulse = 0.0
        self.setFixedSize(56, 56)
        self.setAccessibleName("Agent module icon")

    def set_motion(self, level: float, pulse: float) -> None:
        self.level, self.pulse = level, pulse
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Transform the drawing, keeping the widget and surrounding text still.
        breath = self.level * self.pulse
        painter.translate(28, 28 - 1.2 * breath)
        painter.scale(1 + 0.035 * breath, 1 + 0.035 * breath)
        painter.translate(-28, -28)
        painter.setPen(QPen(QColor(color("#e7ebee", base="light")), 1))
        painter.setBrush(QColor(color("#ffffff", base="light")))
        painter.drawEllipse(QRectF(3, 3, 50, 50))
        ring = QColor(self.accent)
        ring.setAlphaF(0.6 * self.level)
        painter.setPen(QPen(ring, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(3, 3, 50, 50))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("#25323a", base="light")))
        painter.drawRoundedRect(QRectF(19, 18, 5, 14), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(31, 18, 5, 14), 2.5, 2.5)
        painter.setPen(QPen(self.accent, 2.5))
        painter.drawLine(23, 39, 32, 39)


class AgentCard(QFrame):
    """Eased highlights driven by working stages and new, bounded handoff pulses."""

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.setObjectName("agentCard")
        self.setStyleSheet("QFrame#agentCard { background: transparent; border: none; }")
        self.accent = QColor(accent)
        self.avatar = AgentAvatar(accent)
        self._level = self._pulse = self._target = 0.0
        self._working = False
        self._fade = QVariantAnimation(self)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fade.valueChanged.connect(self._set_level)
        self._fade.finished.connect(self._settled)
        self._breath = QVariantAnimation(self)
        self._breath.setDuration(2400)
        self._breath.setStartValue(0.0)
        self._breath.setKeyValueAt(0.5, 1.0)
        self._breath.setEndValue(0.0)
        self._breath.setLoopCount(-1)
        self._breath.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._breath.valueChanged.connect(self._set_pulse)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(700)
        self._hold.timeout.connect(lambda: self._transition(1.0 if self._working else 0.0))

    def set_activity(self, working: bool, *, handoff: bool = False) -> None:
        self._working = working
        if not self.isVisible():
            return  # Hidden pages do not animate or accumulate a playback queue.
        if handoff:
            self._hold.start()
        self._transition(1.0 if working or self._hold.isActive() else 0.0)

    def _transition(self, target: float) -> None:
        if target == self._target:
            return  # Ordinary snapshot refreshes must not restart an easing curve.
        self._target = target
        self._fade.stop()
        self._fade.setStartValue(self._level)
        self._fade.setEndValue(target)
        self._fade.setDuration(360 if target else 650)
        if self._breath.state() != QVariantAnimation.State.Running:
            self._breath.start()
        self._fade.start()

    def _set_level(self, value: float) -> None:
        self._level = float(value)
        self.avatar.set_motion(self._level, self._pulse)
        self.update()

    def _set_pulse(self, value: float) -> None:
        self._pulse = float(value)
        self.avatar.set_motion(self._level, self._pulse)
        self.update()

    def _settled(self) -> None:
        if not self._target:
            self._breath.stop()
            self._set_pulse(0.0)

    def stop_motion(self) -> None:
        self._working = False
        self._clear_motion()

    def _clear_motion(self) -> None:
        self._hold.stop()
        self._fade.stop()
        self._breath.stop()
        self._target = self._pulse = 0.0
        self._set_level(0.0)

    def hideEvent(self, event) -> None:
        self._clear_motion()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.set_activity(self._working)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        energy = self._level * (0.8 + 0.2 * self._pulse)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for width, opacity in ((10, 0.035), (7, 0.06), (4, 0.12)):
            glow = QColor(self.accent)
            glow.setAlphaF(opacity * energy)
            painter.setPen(QPen(glow, width))
            painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("#ffffff", base="light")))
        painter.drawRoundedRect(rect, 8, 8)
        tint = QColor(self.accent)
        tint.setAlphaF(0.06 * energy)
        painter.setBrush(tint)
        painter.drawRoundedRect(rect, 8, 8)
        border = QColor(color("#e1e6e9", base="light"))
        border = QColor.fromRgbF(*(a + (b - a) * self._level for a, b in zip(
            border.getRgbF()[:3], self.accent.getRgbF()[:3], strict=True)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.2))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QPen(self.accent, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(rect.left() + 8, rect.top()), QPointF(rect.right() - 8, rect.top()))

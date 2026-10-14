"""Antonio's visual desk connected to the shared worker's read-only projection."""

from __future__ import annotations

import math
from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetricsF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.ui.agent_card import AgentCard
from grande_alpha.ui.agent_desk import ActivityDelegate, PacificAxis
from grande_alpha.ui.themes import color

ROLES = (("NOVA", "STOCKS", "#24b6c4"), ("ORIN", "CRYPTO", "#51b18a"),
         ("VELA", "ANALYSIS", "#c4a340"), ("KADE", "DATA CHECKS", "#d6864f"),
         ("RUNE", "PAPER FILLS", "#be72a2"), ("ZARA", "PORTFOLIO", "#9273c8"))
WORKING_STAGES = {"Scanning", "Loading stock scan", "Loading crypto pairs", "Requesting quotes",
                  "Fetching quotes", "Analyzing", "Checking data", "Checking quotes and limits",
                  "Reading news sources", "Coordinating cycle"}


def label(text: str, name: str = "") -> QLabel:
    result = QLabel(text)
    result.setObjectName(name)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    result.setMinimumWidth(0)
    return result


def panel() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("dashboardPanel")
    frame.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    return frame, layout


def money(value, *, signed=False) -> str:
    try:
        number = float(value)
        if math.isfinite(number):
            return f"{'+' if signed and number > 0 else '-' if number < 0 else ''}${abs(number):,.2f}"
    except (ValueError, TypeError, OverflowError):
        pass
    return "—"


class ResearchDesk(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("agentCanvas")
        self._session = None
        self._last_event = 0
        self._reachable = False
        self._times, self._values = [], []
        self._range = "all"
        self._desk_width = 1100
        self.cards, self.stages = {}, {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.metrics = QGridLayout()
        self.metrics.setSpacing(10)
        self.equity, self.pnl, self.fills, self.win_rate = [label("—", "metricValue") for _ in range(4)]
        self.metric_cards = []
        for title, value in zip(("VIRTUAL EQUITY", "PAPER P&L", "SIMULATED FILLS", "WIN RATE"),
                                (self.equity, self.pnl, self.fills, self.win_rate), strict=True):
            card, box = panel()
            box.addWidget(label(title, "eyebrow"))
            box.addWidget(value)
            self.metric_cards.append(card)
        self.win_rate.setToolTip("Closed virtual trades only. — means no closed trades.")
        layout.addLayout(self.metrics)

        self.middle = QGridLayout()
        self.middle.setSpacing(12)
        self.chart_panel, chart_box = panel()
        chart_heading = QHBoxLayout()
        chart_heading.addWidget(label("VIRTUAL EQUITY", "sectionTitle"), 1)
        self.range_group = QButtonGroup(self)
        for text, key in (("1H", "1h"), ("1D", "1d"), ("ALL", "all")):
            button = QPushButton(text)
            button.setObjectName("rangeButton")
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.clicked.connect(lambda _checked=False, selected=key: self._set_range(selected))
            self.range_group.addButton(button)
            chart_heading.addWidget(button)
        chart_box.addLayout(chart_heading)
        self.source = label("Paper · not started", "metricCaption")
        chart_box.addWidget(self.source)
        self.chart = pg.PlotWidget(axisItems={"bottom": PacificAxis(orientation="bottom")})
        self.chart.setMinimumSize(0, 170)
        self.chart.setMaximumHeight(190)
        self.chart.setMouseEnabled(x=False, y=False)
        self.chart.setMenuEnabled(False)
        self.chart.hideButtons()
        self.chart.showGrid(x=False, y=True, alpha=.12)
        self.chart.getAxis("left").setWidth(65)
        self.curve = self.chart.plot([], [])
        self.empty_chart = label("Start paper to see virtual equity", "chatEmpty")
        self.empty_chart.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_chart.setMinimumHeight(170)
        chart_box.addWidget(self.empty_chart)
        chart_box.addWidget(self.chart)
        self.chart.hide()
        self.activity_panel, activity_box = panel()
        activity_box.addWidget(label("ACTIVITY", "sectionTitle"))
        self.activity = QTableWidget(0, 2)
        self.activity.setHorizontalHeaderLabels(("TIME", "UPDATE"))
        self.activity.verticalHeader().hide()
        self.activity.setShowGrid(False)
        self.activity.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.activity.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.activity.setItemDelegateForColumn(1, ActivityDelegate(self.activity))
        self.activity.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.activity.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.activity.setColumnWidth(0, 70)
        self.activity.setMinimumSize(0, 220)
        self.activity.setFixedHeight(220)
        self.activity.setAccessibleName("Research handoffs and local controls")
        self.activity.horizontalHeader().setMinimumSectionSize(0)
        activity_box.addWidget(self.activity)
        layout.addLayout(self.middle)

        layout.addWidget(label("YOUR TEAM", "sectionTitle"))
        self.team_grid = QGridLayout()
        self.team_grid.setSpacing(4)
        for name, role, accent in ROLES:
            card = AgentCard(accent)
            card.setMinimumSize(0, 160)
            card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 14, 14, 12)
            box.setSpacing(4)
            box.addWidget(card.avatar, 0, Qt.AlignmentFlag.AlignHCenter)
            for text, object_name in ((role, "eyebrow"), (name, "sectionTitle")):
                heading = label(text, object_name)
                heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
                box.addWidget(heading)
            stage = label("Idle", "metricCaption")
            stage.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stage.setFixedHeight(34)
            box.addWidget(stage)
            self.cards[name], self.stages[name] = card, stage
        layout.addLayout(self.team_grid)
        layout.addStretch()
        self.reflow(1100)
        self.apply_theme()

    def reflow(self, width: int) -> None:
        self._desk_width = width
        columns = 6 if width >= 1000 else 3 if width >= 620 else 2
        for widgets, grid, count in ((self.metric_cards, self.metrics, 4 if width >= 750 else 2),
                                     (list(self.cards.values()), self.team_grid, columns)):
            for widget in widgets:
                grid.removeWidget(widget)
            for index in range(len(widgets)):
                grid.setColumnStretch(index, 1 if index < count else 0)
            for index, widget in enumerate(widgets):
                grid.addWidget(widget, index // count, index % count)
        self.middle.removeWidget(self.chart_panel)
        self.middle.removeWidget(self.activity_panel)
        self.middle.addWidget(self.chart_panel, 0, 0)
        self.middle.addWidget(self.activity_panel, 0 if width >= 850 else 1, 1 if width >= 850 else 0)
        self.middle.setColumnStretch(0, 3)
        self.middle.setColumnStretch(1, 2 if width >= 850 else 0)
        self._fit_metrics()

    def _fit_metrics(self) -> None:
        columns = 4 if self._desk_width >= 750 else 2
        available = (self._desk_width - 10 * (columns - 1)) / columns - 36
        for value in (self.equity, self.pnl, self.fills, self.win_rate):
            font = value.font()
            font.setPointSize(25)
            extent = max(1, QFontMetricsF(font).horizontalAdvance(value.text()))
            points = max(11, min(25, int(25 * available / extent)))
            value.setStyleSheet(f"font-size: {points}pt;")
            value.setToolTip(value.text())

    def record_event(self, kind: str, text: str, at: str = "") -> None:
        row = 0
        self.activity.insertRow(row)
        try:
            instant = datetime.fromisoformat(at.replace("Z", "+00:00")) if at else datetime.now().astimezone()
            stamp = instant.astimezone().strftime("%I:%M %p")
        except (ValueError, OverflowError):
            stamp = "—"
        clock = QTableWidgetItem(stamp)
        clock.setToolTip(at)
        item = QTableWidgetItem(f"[{kind}] {text}")
        item.setToolTip(text)
        self.activity.setItem(row, 0, clock)
        self.activity.setItem(row, 1, item)
        self.activity.setRowHeight(row, 47)
        while self.activity.rowCount() > 80:
            self.activity.removeRow(self.activity.rowCount() - 1)

    def render(self, research: dict | None, *, reachable: bool) -> None:
        research = research or {}
        paper, desk = research.get("paper") or {}, research.get("desk") or {}
        session = (desk.get("session_id"), paper.get("session_id"))
        baseline = not self._reachable or session != self._session
        if session != self._session:
            self.activity.setRowCount(0)
            self._last_event = 0
        handoffs = set()
        events = desk.get("team_events") or []
        for event in events:
            if event.get("id", 0) > self._last_event:
                self.record_event(str(event.get("kind", "SCAN")),
                                  f"{event.get('from', '')} → {event.get('to', '')} · {event.get('message', '')}",
                                  str(event.get("at", "")))
                if not baseline and reachable:
                    recipients = {part.strip() for part in str(event.get("to", "")).split("+")}
                    handoffs.update(name for name in self.cards if name == event.get("from") or name in recipients)
        self._last_event = max([self._last_event, *(item.get("id", 0) for item in events)])
        self._session, self._reachable = session, reachable
        self.equity.setText(money(paper.get("equity")))
        self.pnl.setText(money(paper.get("total_pnl"), signed=True))
        self.fills.setText(str(paper.get("fill_count", "—")))
        win_rate = paper.get("win_rate")
        try:
            rate = float(win_rate)
            self.win_rate.setText(f"{rate:.0f}%" if math.isfinite(rate) and 0 <= rate <= 100 else "—")
        except (ValueError, TypeError, OverflowError):
            self.win_rate.setText("—")
        self._fit_metrics()
        source = {"demo": "Demo · synthetic prices", "broker_quotes": "Broker quotes · virtual fills"}.get(paper.get("source"), "Paper · not started")
        prefix = "Last reported · " if not reachable and paper else "Saved · " if paper and not paper.get("active") else ""
        self.source.setText(prefix + source
                            + (" · stale marks" if paper.get("stale_marks") else ""))
        self.source.setToolTip("Recent marks, up to 160 saved points. Paper results are not real returns. "
                               + str(paper.get("observed_at", "")))
        points = []
        for item in paper.get("equity_history") or []:
            try:
                stamp = item.get("at")
                if not isinstance(stamp, str):
                    continue
                at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                value = float(item["equity"])
                if at.tzinfo and math.isfinite(value):
                    points.append((at.timestamp(), value))
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
        self._times, self._values = [p[0] for p in points], [p[1] for p in points]
        self._render_history()
        self.empty_chart.setText("Waiting for first cycle" if paper else "Start paper to see virtual equity")
        running = reachable and research.get("running") is True
        statuses = desk.get("team_status") or {}
        ai_working = any(str(s).startswith("Analyzing") for s in (desk.get("analysis_status") or {}).values())
        for name, card in self.cards.items():
            stage = str(statuses.get(name, "Idle"))
            shown = "Unavailable" if not reachable else stage
            if name == "VELA" and running and ai_working:
                shown = "AI analyzing"
            self.stages[name].setText(shown)
            self.stages[name].setToolTip(stage if reachable else "Worker unreachable; previous research is unverified")
            working = running and ((desk.get("phase") == "Working" and stage in WORKING_STAGES)
                                   or (name == "VELA" and ai_working))
            if not reachable:
                card.stop_motion()
            else:
                card.set_activity(working, handoff=running and name in handoffs)

    def _set_range(self, selected: str) -> None:
        self._range = selected
        self._render_history()

    def _render_history(self) -> None:
        pairs = list(zip(self._times, self._values, strict=True))
        if pairs and self._range != "all":
            start = self._times[-1] - (3600 if self._range == "1h" else 86400)
            pairs = [(at, value) for at, value in pairs if at >= start]
        times, values = [p[0] for p in pairs], [p[1] for p in pairs]
        if values:
            self.curve.setFillLevel(min(values) - max(.01, (max(values) - min(values)) * .1))
        self.curve.setData(times, values, symbol="o" if len(values) == 1 else None,
                           symbolSize=5, symbolBrush=color("#1ca97a", base="light"))
        self.chart.setVisible(bool(values))
        self.empty_chart.setVisible(not values)

    def apply_theme(self) -> None:
        self.chart.setBackground(color("#ffffff", base="light"))
        for name in ("left", "bottom"):
            axis = self.chart.getAxis(name)
            axis.setPen(pg.mkPen(color("#e6ecef", base="light")))
            axis.setTextPen(pg.mkPen(color("#657782", base="light")))
        accent = QColor(color("#1ca97a", base="light"))
        self.curve.setPen(pg.mkPen(accent, width=2))
        accent.setAlpha(25)
        self.curve.setBrush(pg.mkBrush(accent))

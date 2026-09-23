"""Native workspace navigation and progressive-disclosure building blocks."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout, QFrame, QPushButton, QTabWidget, QToolButton, QVBoxLayout, QWidget


class WorkspaceTabs(QTabWidget):
    """Four primary destinations with nested overview and activity pages."""

    def __init__(self):
        super().__init__()
        self.tabBar().hide()
        self.overview = QTabWidget()
        self.overview.setDocumentMode(True)
        self.overview.setAccessibleName("Summary and readiness checks")
        self.records = QTabWidget()
        self.records.setDocumentMode(True)
        self.records.setAccessibleName("Positions, orders, and activity")

    def add_record(self, widget: QWidget, title: str) -> None:
        self.records.addTab(widget, title)

    def indexOf(self, widget):  # noqa: N802
        if widget is not self.overview and self.overview.indexOf(widget) >= 0:
            return super().indexOf(self.overview)
        if widget is not self.records and self.records.indexOf(widget) >= 0:
            return super().indexOf(self.records)
        return super().indexOf(widget)

    def setCurrentWidget(self, widget):  # noqa: N802
        if widget is not self.overview and self.overview.indexOf(widget) >= 0:
            self.overview.setCurrentWidget(widget)
            super().setCurrentWidget(self.overview)
        elif widget is not self.records and self.records.indexOf(widget) >= 0:
            self.records.setCurrentWidget(widget)
            super().setCurrentWidget(self.records)
        else:
            super().setCurrentWidget(widget)


class WorkspaceNavigation(QFrame):
    def __init__(self, tabs: WorkspaceTabs):
        super().__init__()
        self.setObjectName("workspaceNavigation")
        self.setAccessibleName("Main navigation")
        self.tabs = tabs
        self.row = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self.row.setContentsMargins(10, 12, 10, 12)
        self.row.setSpacing(6)
        self.buttons = []
        for index in range(tabs.count()):
            button = QPushButton(tabs.tabText(index))
            button.setObjectName("navigationItem")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setMinimumWidth(0)
            button.setAccessibleName(f"Open {tabs.tabText(index)}")
            button.clicked.connect(lambda _checked=False, page=index: tabs.setCurrentIndex(page))
            self.row.addWidget(button)
            self.buttons.append(button)
        self.row.addStretch()
        tabs.currentChanged.connect(self.sync)
        self.sync(tabs.currentIndex())
        self._compact = None

    def sync(self, current: int) -> None:
        for index, button in enumerate(self.buttons):
            button.setChecked(index == current)

    def set_compact(self, compact: bool) -> None:
        if self._compact == compact:
            return
        self._compact = compact
        self.row.setDirection(
            QBoxLayout.Direction.LeftToRight if compact else QBoxLayout.Direction.TopToBottom
        )
        self.setMinimumWidth(0 if compact else 166)
        self.setMaximumWidth(16777215 if compact else 184)
        self.setMaximumHeight(58 if compact else 16777215)
        for button in self.buttons:
            button.setMinimumHeight(34 if compact else 42)


class DisclosureSection(QFrame):
    def __init__(self, title: str, content: QWidget, *, expanded: bool = False):
        super().__init__()
        self.setObjectName("disclosureSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setAccessibleName(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.content = content
        layout.addWidget(self.toggle)
        layout.addWidget(content)
        self.toggle.toggled.connect(self.set_expanded)
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(expanded)
        self.content.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

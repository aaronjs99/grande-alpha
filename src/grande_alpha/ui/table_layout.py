from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableWidget

COLUMN_WIDTHS: dict[str, int] = {
    "ID": 54,
    "Age": 72,
    "Alias": 78,
    "Ask": 92,
    "Average": 98,
    "Bid": 92,
    "Commands": 170,
    "Configuration": 210,
    "Confirmed": 150,
    "Count": 76,
    "Daily research baseline": 220,
    "Event": 440,
    "Fill": 100,
    "Filled": 84,
    "Gate": 150,
    "Last": 92,
    "Observed": 180,
    "Order ID": 220,
    "Quantity": 100,
    "Quantity/$": 112,
    "Reason": 390,
    "Requirement": 350,
    "S = -1": 170,
    "S = 0": 170,
    "S = +1": 170,
    "Selected strategy": 220,
    "Severity": 88,
    "Side": 70,
    "Spread": 84,
    "State": 94,
    "Status": 64,
    "Strategy": 170,
    "Symbol": 76,
    "Time": 154,
    "Train": 176,
    "Test": 176,
}


def _default_width(title: str) -> int:
    if title in COLUMN_WIDTHS:
        return COLUMN_WIDTHS[title]
    return max(76, min(220, len(title) * 9 + 32))


def reset_column_widths(table: QTableWidget) -> None:
    widths = table.property("grandeDefaultColumnWidths") or []
    header = table.horizontalHeader()
    for column, width in enumerate(widths):
        header.resizeSection(column, int(width))


def fit_columns_to_contents(table: QTableWidget) -> None:
    table.resizeColumnsToContents()
    header = table.horizontalHeader()
    for column in range(table.columnCount()):
        width = max(header.minimumSectionSize(), min(480, header.sectionSize(column) + 16))
        header.resizeSection(column, width)


def _show_header_menu(table: QTableWidget, position: QPoint) -> None:
    header = table.horizontalHeader()
    column = header.logicalIndexAt(position)
    menu = QMenu(table)
    fit_one = menu.addAction("Fit this column")
    fit_all = menu.addAction("Fit all columns")
    reset = menu.addAction("Reset column widths")
    chosen = menu.exec(header.mapToGlobal(position))
    if chosen is fit_one and column >= 0:
        table.resizeColumnToContents(column)
        header.resizeSection(column, min(480, header.sectionSize(column) + 16))
    elif chosen is fit_all:
        fit_columns_to_contents(table)
    elif chosen is reset:
        reset_column_widths(table)


def configure_adjustable_columns(table: QTableWidget, headers: list[str]) -> None:
    """Make every horizontal section manually resizable with useful initial widths."""

    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(True)
    header.setSectionsMovable(False)
    header.setMinimumSectionSize(42)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
    header.setToolTip(
        "Drag a header boundary to resize a column. Right-click the header to fit or reset widths."
    )
    header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    header.customContextMenuRequested.connect(
        lambda position, target=table: _show_header_menu(target, position)
    )
    widths = [_default_width(title) for title in headers]
    table.setProperty("grandeDefaultColumnWidths", widths)
    reset_column_widths(table)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setToolTip(
        "Columns are adjustable. Drag header boundaries, or right-click a header for sizing options."
    )

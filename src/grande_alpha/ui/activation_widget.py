from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.activation_guidance import activation_summary, decorate_readiness
from grande_alpha.ui.table_layout import configure_adjustable_columns


class ActivationChecklistWidget(QWidget):
    run_safe_checks = Signal()
    open_next_action = Signal(str)

    HEADERS = ["Condition", "Owner", "Status", "Current result", "Exact next action"]

    def __init__(self, *, shadow_only: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.shadow_only = shadow_only
        self._rows: list[dict[str, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Activation checklist")
        title.setStyleSheet("font-size:17pt;font-weight:700")
        title_box.addWidget(title)
        self.summary = QLabel("Waiting for the first readiness check.")
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName("Activation checklist summary")
        title_box.addWidget(self.summary)
        header.addLayout(title_box, 1)

        self.safe_checks_button = QPushButton("Run safe checks")
        self.safe_checks_button.setAccessibleName("Run read-only activation checks")
        self.safe_checks_button.setToolTip(
            "Refresh broker account and quote truth only. This cannot review, place, or cancel an order."
        )
        self.safe_checks_button.clicked.connect(self.run_safe_checks)
        header.addWidget(self.safe_checks_button)
        self.next_button = QPushButton("Open selected next step")
        self.next_button.setObjectName("primary")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self._emit_selected_action)
        header.addWidget(self.next_button)
        layout.addLayout(header)

        self.mode_notice = QLabel()
        self.mode_notice.setObjectName("validationWarning")
        self.mode_notice.setWordWrap(True)
        self.mode_notice.setText(
            "AUTO-SHADOW PROCESS — STRUCTURALLY READ-ONLY. This process cannot authorize, review, "
            "place, or cancel an order. Close it and launch normal GRANDE Alpha only after every "
            "required condition is independently satisfied."
            if shadow_only
            else "SCHEDULED AUTO-SHADOW IS STRUCTURALLY READ-ONLY. It can collect observations and "
            "virtual fills, but it cannot turn itself into live trading or authorize, review, place, "
            "or cancel an order."
        )
        layout.addWidget(self.mode_notice)

        ownership = QFrame()
        ownership.setObjectName("card")
        ownership_layout = QHBoxLayout(ownership)
        ownership_layout.setContentsMargins(12, 8, 12, 8)
        ownership_label = QLabel(
            "APP CHECK = safe read-only automation   •   APP GATE = cannot be bypassed   •   "
            "YOU = deliberate account/settings action   •   RESEARCH = new defensible evidence   •   "
            "EXTERNAL REVIEW = decision outside GRANDE Alpha"
        )
        ownership_label.setWordWrap(True)
        ownership_label.setAccessibleName("Activation checklist owner legend")
        ownership_layout.addWidget(ownership_label)
        layout.addWidget(ownership)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self._emit_selected_action())
        configure_adjustable_columns(self.table, self.HEADERS)
        self.table.setAccessibleName("Activation conditions and exact next actions")
        layout.addWidget(self.table, 1)

        self.detail = QLabel("Select a blocked row to see who owns it and open its exact next step.")
        self.detail.setWordWrap(True)
        self.detail.setObjectName("settingsDescription")
        layout.addWidget(self.detail)

        external = QLabel(
            'External links: <a href="https://internationalcenter.ucla.edu/contact-us">UCLA Dashew '
            'Center</a> • <a href="https://robinhood.com/us/en/support/articles/third-party-connections/">'
            "Robinhood third-party guidance</a>"
        )
        external.setOpenExternalLinks(True)
        external.setAccessibleName("External activation guidance links")
        layout.addWidget(external)

    def update_rows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        previous_gate = self.selected_gate()
        self._rows = decorate_readiness(rows)
        self.table.setRowCount(len(self._rows))
        selected_row = -1
        for row_index, row in enumerate(self._rows):
            values = [
                row["gate"],
                row["owner"],
                row["status"],
                row["observed"],
                row["action"],
            ]
            tooltip = f"{row['explanation']}\n\nExact next action: {row['action']}"
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tooltip)
                if column == 1:
                    item.setForeground(
                        QColor(
                            "#ffca7a"
                            if row["owner"] in {"YOU", "APP + YOU", "EXTERNAL REVIEW"}
                            else "#65b9ff"
                        )
                    )
                elif column == 2:
                    item.setForeground(
                        QColor(
                            "#00e507"
                            if value == "PASS"
                            else "#f2c14e"
                            if value == "USER ACTION"
                            else "#ff697d"
                        )
                    )
                self.table.setItem(row_index, column, item)
            if row["gate"] == previous_gate:
                selected_row = row_index

        self.summary.setText(activation_summary(self._rows, shadow_only=self.shadow_only))
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        else:
            first_blocked = next(
                (index for index, row in enumerate(self._rows) if row["status"] != "PASS"),
                -1,
            )
            if first_blocked >= 0:
                self.table.selectRow(first_blocked)
            else:
                self._selection_changed()

    def selected_gate(self) -> str:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]["gate"]
        return ""

    def _selection_changed(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self._rows):
            self.next_button.setEnabled(False)
            return
        value = self._rows[row]
        self.next_button.setEnabled(value["status"] != "PASS")
        self.next_button.setText(
            "Condition already verified" if value["status"] == "PASS" else "Open selected next step"
        )
        self.detail.setText(
            f"{value['owner']} • {value['explanation']}\nNext: {value['action']}"
        )

    def _emit_selected_action(self) -> None:
        gate = self.selected_gate()
        if gate:
            self.open_next_action.emit(gate)

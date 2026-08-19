from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.activation_guidance import activation_summary, decorate_readiness
from grande_alpha.external_guidance import ExternalGuidanceLink, external_guidance_links
from grande_alpha.ui.table_layout import configure_adjustable_columns


class _ResponsiveScrollArea(QScrollArea):
    """Keep a vertically scrolling task document pinned to the viewport width."""

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        content = self.widget()
        if content is not None and self.viewport().width() > 0:
            content.setFixedWidth(self.viewport().width())


class ActivationChecklistWidget(QWidget):
    run_safe_checks = Signal()
    open_next_action = Signal(str)

    HEADERS = ["Condition", "Owner", "Status", "Current result", "Exact next action"]

    def __init__(
        self,
        *,
        shadow_only: bool = False,
        external_resources: Iterable[ExternalGuidanceLink] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.shadow_only = shadow_only
        self._rows: list[dict[str, str]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = _ResponsiveScrollArea()
        self.scroll.setObjectName("activationChecklistScroll")
        self.scroll.setAccessibleName("Activation checklist content")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(self.scroll)

        self.content = QWidget()
        self.scroll.setWidget(self.content)
        layout = QVBoxLayout(self.content)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)

        self.header = QWidget()
        self.header_layout = QGridLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setHorizontalSpacing(10)
        self.header_layout.setVerticalSpacing(8)
        self.title_widget = QWidget()
        self.title_layout = QVBoxLayout(self.title_widget)
        self.title_layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("Activation checklist")
        self.title.setStyleSheet("font-size:17pt;font-weight:700")
        self.title_layout.addWidget(self.title)
        self.summary = QLabel("Waiting for the first readiness check.")
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName("Activation checklist summary")
        self.title_layout.addWidget(self.summary)
        self.actions_widget = QWidget()
        actions = QHBoxLayout(self.actions_widget)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.safe_checks_button = QPushButton("Run safe checks")
        self.safe_checks_button.setAccessibleName("Run read-only activation checks")
        self.safe_checks_button.setToolTip(
            "Refresh broker account and quote truth only. This cannot review, place, or cancel an order."
        )
        self.safe_checks_button.clicked.connect(self.run_safe_checks)
        actions.addWidget(self.safe_checks_button)
        self.next_button = QPushButton("Open selected next step")
        self.next_button.setObjectName("primary")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self._emit_selected_action)
        actions.addWidget(self.next_button)
        layout.addWidget(self.header)

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
        self.ownership_label = QLabel(
            "APP CHECK = safe read-only automation   •   APP GATE = cannot be bypassed   •   "
            "YOU = deliberate account/settings action   •   RESEARCH = new defensible evidence   •   "
            "EXTERNAL REVIEW = decision outside GRANDE Alpha"
        )
        self.ownership_label.setWordWrap(True)
        self.ownership_label.setAccessibleName("Activation checklist owner legend")
        ownership_layout.addWidget(self.ownership_label)
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
        # A constrained task page still needs room for the selected-row explanation and
        # external guidance below the table. Eighty-eight pixels preserves a header, one
        # complete row, and the horizontal scrollbar; taller pages grow toward the row cap.
        self.table.setMinimumHeight(88)
        self.table.setMaximumHeight(520)
        # Grow toward the exact row-content cap on tall screens; constrained windows may
        # still shrink this pane and expose its own scrollbar without hiding the guidance.
        layout.addWidget(self.table, 1)

        self.detail = QLabel("Select a blocked row to see who owns it and open its exact next step.")
        self.detail.setWordWrap(True)
        self.detail.setObjectName("settingsDescription")
        layout.addWidget(self.detail)

        resources = (
            external_guidance_links()
            if external_resources is None
            else tuple(external_resources)
        )
        resource_links = " &bull; ".join(
            f'<a href="{escape(resource.url, quote=True)}">{escape(resource.label)}</a>'
            for resource in resources
        )
        resource_body = resource_links or escape(
            "None. Consult appropriately qualified local professionals."
        )
        self.external_resources = QLabel()
        self.external_resources.setTextFormat(Qt.TextFormat.RichText)
        self.external_resources.setText(
            "OUTSIDE THE APP &bull; GRANDE Alpha does not collect or certify jurisdiction, account "
            "eligibility, legal, tax, employment, residency, or business status. Review applicable "
            "requirements with the broker and appropriately qualified professionals.<br>"
            f"Resources configured for this distribution: {resource_body}"
        )
        self.external_resources.setObjectName("settingsDescription")
        self.external_resources.setOpenExternalLinks(True)
        self.external_resources.setAccessibleName("External activation guidance links")
        self.external_resources.setWordWrap(True)
        layout.addWidget(self.external_resources)
        layout.addStretch(1)
        self._header_mode: str | None = None
        self._apply_responsive_layout(self.width())

    def _apply_responsive_layout(self, width: int) -> None:
        mode = "stacked" if width < 900 else "compact" if width < 1120 else "wide"
        if mode != self._header_mode:
            self._header_mode = mode
            self.header_layout.removeWidget(self.title_widget)
            self.header_layout.removeWidget(self.actions_widget)
            self.header_layout.removeWidget(self.summary)
            self.title_layout.removeWidget(self.summary)
            if mode == "stacked":
                self.header_layout.addWidget(self.title_widget, 0, 0)
                self.header_layout.addWidget(self.actions_widget, 1, 0)
                self.header_layout.addWidget(self.summary, 2, 0)
            elif mode == "compact":
                self.header_layout.addWidget(self.title_widget, 0, 0)
                self.header_layout.addWidget(self.actions_widget, 0, 1)
                self.header_layout.addWidget(self.summary, 1, 0, 1, 2)
                self.header_layout.setColumnStretch(0, 1)
            else:
                self.title_layout.addWidget(self.summary)
                self.header_layout.addWidget(self.title_widget, 0, 0)
                self.header_layout.addWidget(self.actions_widget, 0, 1)
                self.header_layout.setColumnStretch(0, 1)
        self._reserve_wrapped_text_height(width, mode=mode)

    def _reserve_wrapped_text_height(
        self,
        width: int | None = None,
        *,
        mode: str | None = None,
    ) -> None:
        """Prevent critical wrapped copy from being compressed below its rendered height."""

        available_width = max(1, (self.width() if width is None else width) - 24)
        current_mode = self._header_mode if mode is None else mode
        summary_width = (
            available_width
            if current_mode != "wide"
            else max(1, available_width - self.actions_widget.sizeHint().width() - 10)
        )
        wrapped_labels = (
            (self.summary, summary_width),
            (self.mode_notice, available_width),
            (self.ownership_label, available_width - 24),
            (self.detail, available_width),
            (self.external_resources, available_width),
        )
        for label, label_width in wrapped_labels:
            required_height = label.heightForWidth(max(1, label_width))
            if required_height >= 0:
                label.setMinimumHeight(required_height)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

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
        self._reserve_wrapped_text_height()
        row_height = max(22, self.table.verticalHeader().defaultSectionSize())
        header_height = max(24, self.table.horizontalHeader().sizeHint().height())
        scrollbar_height = self.table.horizontalScrollBar().sizeHint().height()
        content_height = (
            header_height
            + (row_height * max(1, len(self._rows)))
            + scrollbar_height
            + 6
        )
        self.table.setMaximumHeight(min(520, max(220, content_height)))
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
        self._reserve_wrapped_text_height()

    def _emit_selected_action(self) -> None:
        gate = self.selected_gate()
        if gate:
            self.open_next_action.emit(gate)

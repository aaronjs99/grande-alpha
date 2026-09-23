from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QTableWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.terminology import TERM_HELP
from grande_alpha.ui.themes import set_widget_style

TABLE_HEADER_HELP: dict[str, str] = {
    "Age": "Seconds since the timestamp of the latest quote received by GRANDE Alpha.",
    "Ask": "Lowest displayed price currently offered by a seller.",
    "Bid": "Highest displayed price currently offered by a buyer.",
    "Gate": "Independent condition that must pass before a live-review certificate can be created.",
    "Observed": "Value measured in this exact evidence run.",
    "Requirement": "Minimum rule the observed value must satisfy.",
    "Spread": "Difference between ask and bid, shown in basis points.",
    "Status": "PASS satisfies this gate; FAIL keeps the result shadow-only.",
    "Test": "Later chronological data used only for evaluation in this fold.",
    "Train": "Earlier chronological data used to choose a candidate in this fold.",
}




def _tooltip(term: str, explanation: str) -> str:
    return f"<b>{escape(term)}</b><br>{escape(explanation)}"


class ExplainedLabel(QLabel):
    """A discoverable glossary term with mouse and assistive-technology help."""

    def __init__(
        self,
        term: str,
        explanation: str | None = None,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        explanation = explanation or TERM_HELP.get(term)
        if not explanation:
            raise KeyError(f"No glossary explanation registered for {term!r}")
        super().__init__(term, parent)
        self.term = term
        self.explanation = explanation
        self.setObjectName("explainedTerm")
        self.setToolTip(_tooltip(term, explanation))
        self.setToolTipDuration(30_000)
        self.setWhatsThis(explanation)
        self.setStatusTip(explanation)
        self.setAccessibleName(term)
        self.setAccessibleDescription(explanation)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        compact_style = "color:#8fa4b8;font-size:9pt;" if compact else ""
        set_widget_style(self, "QLabel {"
            f"{compact_style}border:0;border-bottom:1px dashed #6688a3;padding-bottom:1px;"
            "}"
            "QLabel:hover {color:#8fd3ff;border-bottom-color:#8fd3ff;}")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            QToolTip.showText(event.globalPosition().toPoint(), self.toolTip(), self)
        super().mousePressEvent(event)


def add_explained_row(
    form: QFormLayout,
    term: str,
    field: QWidget | QLayout,
    explanation: str | None = None,
) -> ExplainedLabel:
    label = ExplainedLabel(term, explanation)
    form.addRow(label, field)
    return label


def apply_help(widget: QWidget, term: str, explanation: str | None = None) -> None:
    explanation = explanation or TERM_HELP.get(term)
    if not explanation:
        raise KeyError(f"No glossary explanation registered for {term!r}")
    widget.setToolTip(_tooltip(term, explanation))
    widget.setToolTipDuration(30_000)
    widget.setWhatsThis(explanation)
    widget.setAccessibleDescription(explanation)


def help_hint() -> QLabel:
    label = QLabel(
        "Tip: dashed-underlined terms explain themselves on hover or click. Press F1 for the glossary."
    )
    label.setObjectName("settingsDescription")
    label.setWordWrap(True)
    label.setAccessibleName("Glossary help tip")
    return label


def apply_table_header_help(table: QTableWidget) -> None:
    for column in range(table.columnCount()):
        item = table.horizontalHeaderItem(column)
        if item is None:
            continue
        explanation = TABLE_HEADER_HELP.get(item.text()) or TERM_HELP.get(item.text())
        if explanation:
            item.setToolTip(_tooltip(item.text(), explanation))


class GlossaryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GRANDE Alpha terminology and glossary")
        self.setMinimumSize(680, 520)
        layout = QVBoxLayout(self)
        title = QLabel("Terminology & glossary")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Search the same plain-language definitions used by dashed-underlined labels throughout the app."
        )
        intro.setObjectName("settingsDescription")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search a term or definition…")
        self.search.setAccessibleName("Search glossary")
        layout.addWidget(self.search)
        self.terms = QListWidget()
        self.terms.setAccessibleName("Glossary terms")
        for term in sorted(TERM_HELP, key=str.casefold):
            self.terms.addItem(term)
        layout.addWidget(self.terms, 2)
        self.definition = QLabel()
        self.definition.setWordWrap(True)
        self.definition.setObjectName("validationWarning")
        self.definition.setAccessibleName("Selected glossary definition")
        layout.addWidget(self.definition, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.terms.currentTextChanged.connect(self._show_definition)
        if self.terms.count():
            self.terms.setCurrentRow(0)

    def _filter(self, query: str) -> None:
        needle = query.strip().casefold()
        first_visible = None
        for row in range(self.terms.count()):
            item = self.terms.item(row)
            haystack = f"{item.text()} {TERM_HELP[item.text()]}".casefold()
            hidden = bool(needle) and needle not in haystack
            item.setHidden(hidden)
            if not hidden and first_visible is None:
                first_visible = item
        current = self.terms.currentItem()
        if first_visible is not None and (current is None or current.isHidden()):
            self.terms.setCurrentItem(first_visible)
        elif first_visible is None:
            self.definition.setText("No glossary terms match that search.")

    def _show_definition(self, term: str) -> None:
        if term in TERM_HELP:
            self.definition.setText(f"{term}\n\n{TERM_HELP[term]}")

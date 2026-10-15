"""Prepare local session candidates; this dialog has no worker or broker access."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.data.earnings import _number
from grande_alpha.domain.loss_recovery import validate_recovery
from grande_alpha.execution.candidate_profile import (
    candidate_template,
    check_candidate_shape,
    save_candidate_document,
    validate_candidate,
)
from grande_alpha.execution.equity_execution import symbol_valid
from grande_alpha.strategy.mixed_portfolio import AllocationPolicy

# A field in an accepted 64 KiB candidate must fit without silent truncation.
MAX_FIELD_CHARS = 65_536

SESSION_FIELDS = (
    ("account_number", "Robinhood account number", "text"),
    ("allowed_symbols", "Allowed stock / ETF symbols", "symbols"),
    ("starts_at", "Starts at (with time zone)", "time"),
    ("expires_at", "Expires at (optional)", "time"),
    ("max_order_usd", "Dollars per order", "number"),
    ("max_exposure_usd", "Total exposure ($)", "number"),
    ("max_daily_notional_usd", "Daily traded amount ($)", "number"),
    ("max_daily_loss_usd", "Daily loss stop ($)", "number"),
    ("max_orders", "Orders per day", "integer"),
    ("max_orders_per_minute", "Orders per minute", "integer"),
    ("max_spread_bps", "Maximum spread (basis points)", "number"),
    ("max_quote_age_seconds", "Maximum quote age (seconds)", "number"),
)

POLICY_LABELS = {
    "max_invested_fraction": "Maximum invested fraction",
    "max_stock_fraction": "Maximum stock fraction",
    "max_etf_fraction": "Maximum ETF fraction",
    "max_single_stock_fraction": "Maximum single-stock fraction",
    "max_sector_fraction": "Maximum sector fraction",
    "max_gross_leverage_proxy": "Maximum leverage proxy",
    "volatility_floor": "Volatility floor",
    "max_daily_volatility": "Maximum daily volatility",
    "min_regime_confidence": "Minimum regime confidence",
    "max_spread_bps": "Maximum strategy spread (basis points)",
    "max_signal_age_seconds": "Maximum signal age (seconds)",
    "max_risk_age_days": "Maximum risk-data age (days)",
    "max_stock_holding_days": "Maximum stock holding (days)",
    "max_etf_holding_days": "Maximum ETF holding (days)",
    "rebalance_band_fraction": "Rebalance band fraction",
}

EARNINGS_LABELS = {
    "min_surprise_bps": "Minimum earnings surprise (basis points)",
    "min_momentum_bps": "Minimum post-earnings momentum (basis points)",
    "max_spread_bps": "Maximum earnings spread (basis points)",
    "max_event_age_days": "Maximum earnings age (days)",
    "max_quote_age_seconds": "Maximum earnings quote age (seconds)",
}


def _numeric(text: str, label: str, *, integer=False):
    if not text.strip():
        return None
    try:
        if integer:
            if not re.fullmatch(r"[0-9]+", text.strip()):
                raise ValueError()
            value = int(text)
            if value <= 0:
                raise ValueError()
        else:
            value = float(text)
            _number(value, label, positive=True)
        return value
    except (ValueError, OverflowError) as error:
        raise ValueError(
            f"{label}: enter a positive {'whole number' if integer else 'finite number'}"
        ) from error


class CandidateEditor(QDialog):
    def __init__(self, data_dir: Path, parent=None, *, document=None, source_path=None, example=False):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.source_path = Path(source_path).resolve() if source_path else None
        self.saved_path: Path | None = None
        self.original = copy.deepcopy(document if document is not None else candidate_template())
        check_candidate_shape(self.original)
        self.setWindowTitle("GRANDE · Prepare broker session")
        self.setMinimumSize(340, 440)
        self.resize(610, 730)
        layout = QVBoxLayout(self)
        note = QLabel("Prepare a candidate. Review and Start happen separately.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._forms = []
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.inputs = {}
        form = self._page("Session")
        scope = self.original["scope"]
        for key, label, kind in SESSION_FIELDS:
            control = QLineEdit()
            control.setAccessibleName(label)
            control.setMaxLength(MAX_FIELD_CHARS)
            raw = scope[key]
            control.setText(
                ", ".join(raw)
                if kind == "symbols" and isinstance(raw, list)
                else ""
                if raw is None
                else str(raw)
            )
            control.setPlaceholderText(
                "Optional; blank means no expiry"
                if key == "expires_at"
                else "YYYY-MM-DDTHH:MM:SS±HH:MM"
                if kind == "time"
                else "Required"
            )
            self.inputs[key] = control
            self._add_field(form, label, control)
        self.recovery_unit = QComboBox()
        self.recovery_unit.addItems(("manual", "minutes", "hours", "days", "weeks", "months", "years"))
        if self.recovery_unit.findText(scope["loss_recovery_unit"]) < 0:
            raise ValueError("Unknown loss recovery unit")
        self.recovery_unit.setCurrentText(scope["loss_recovery_unit"])
        self.recovery_delay = QLineEdit(
            "" if scope["loss_recovery_delay"] is None else str(scope["loss_recovery_delay"])
        )
        self.recovery_delay.setMaxLength(MAX_FIELD_CHARS)
        self.recovery_delay.setAccessibleName("Loss recovery delay")
        self.recovery_delay.setPlaceholderText("Positive whole number for a timed delay")
        self.recovery_delay.setEnabled(self.recovery_unit.currentText() != "manual")
        self.recovery_unit.currentTextChanged.connect(
            lambda unit: self.recovery_delay.setEnabled(unit != "manual")
        )
        form.addRow("Loss recovery", self.recovery_unit)
        form.addRow("Recovery delay", self.recovery_delay)

        rules = self._page("Strategy")
        policy_note = QLabel("Existing research starting points. These are not optimized settings.")
        policy_note.setWordWrap(True)
        rules.addRow(policy_note)
        self.policy_inputs, self.earnings_inputs = {}, {}
        for group, labels, controls in (
            ("allocation_policy", POLICY_LABELS, self.policy_inputs),
            ("earnings_thresholds", EARNINGS_LABELS, self.earnings_inputs),
        ):
            if group == "earnings_thresholds":
                heading = QLabel("Earnings entry conditions")
                heading.setWordWrap(True)
                rules.addRow(heading)
            for key, label in labels.items():
                control = QLineEdit()
                raw = self.original[group][key]
                control.setText("" if raw is None else str(raw))
                control.setPlaceholderText("Required")
                control.setAccessibleName(label)
                control.setMaxLength(MAX_FIELD_CHARS)
                controls[key] = control
                self._add_field(rules, label, control)
        self.status = QLabel("Unfilled candidates are saved as drafts. Complete them before Review.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.example_button = QPushButton("Fill example")
        self.example_button.setAutoDefault(False)
        self.example_button.setDefault(False)
        self.example_button.clicked.connect(self.apply_example)
        row.addWidget(self.example_button)
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.save_button = QPushButton("Save candidate…")
        self.save_button.setObjectName("primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._choose_save)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        if example:
            self.apply_example()

    def _page(self, title):
        page = QWidget()
        form = QFormLayout(page)
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._forms.append(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        return form

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        for form in getattr(self, "_forms", ()):
            form.setRowWrapPolicy(
                QFormLayout.RowWrapPolicy.WrapAllRows
                if self.width() < 480
                else QFormLayout.RowWrapPolicy.WrapLongRows
            )
            # Preserve the styled field height when rows wrap after a resize.
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                if item is not None and item.widget() is not None:
                    control = item.widget()
                    control.ensurePolished()
                    control.setMinimumHeight(control.sizeHint().height())
        super().resizeEvent(event)

    @staticmethod
    def _add_field(form, text, control):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setBuddy(control)
        form.addRow(label, control)

    def apply_example(self):
        """Visible illustrative values, never an account choice or an approval."""
        now = datetime.now().astimezone().replace(microsecond=0)
        values = {
            "starts_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "max_order_usd": "10",
            "max_exposure_usd": "100",
            "max_daily_notional_usd": "200",
            "max_daily_loss_usd": "5",
            "max_orders": "5",
            "max_orders_per_minute": "1",
            "max_spread_bps": "20",
            "max_quote_age_seconds": "8",
        }
        for key, value in values.items():
            self.inputs[key].setText(value)
        for key, value in {
            "min_surprise_bps": "100",
            "min_momentum_bps": "20",
            "max_spread_bps": "20",
            "max_event_age_days": "3",
            "max_quote_age_seconds": "8",
        }.items():
            self.earnings_inputs[key].setText(value)
        self.recovery_unit.setCurrentText("manual")
        self.recovery_delay.clear()
        self.status.setText(
            "Example: $10 per order · $100 exposure · $5 daily loss stop · 1 hour. "
            "Choose your account and symbols, then check every setting in Review."
        )

    def _document(self):
        document = copy.deepcopy(self.original)
        scope = document["scope"]
        for key, label, kind in SESSION_FIELDS:
            text = self.inputs[key].text().strip()
            if kind in {"number", "integer"}:
                scope[key] = _numeric(text, label, integer=kind == "integer")
            elif kind == "symbols":
                scope[key] = [item.upper() for item in re.split(r"[\s,]+", text) if item]
                if (
                    len(scope[key]) > 100
                    or any(not symbol_valid(item) for item in scope[key])
                    or len(set(scope[key])) != len(scope[key])
                ):
                    raise ValueError("Enter up to 100 unique stock / ETF symbols")
            elif kind == "time" and text:
                try:
                    instant = datetime.fromisoformat(text)
                    if instant.tzinfo is None or instant.utcoffset() is None:
                        raise ValueError()
                    scope[key] = instant.isoformat()
                except ValueError as error:
                    raise ValueError(f"{label}: include a valid time zone offset") from error
            else:
                scope[key] = text or None
        scope["loss_recovery_unit"] = self.recovery_unit.currentText()
        scope["loss_recovery_delay"] = (
            None
            if scope["loss_recovery_unit"] == "manual"
            else _numeric(self.recovery_delay.text(), "Recovery delay", integer=True)
        )
        validate_recovery(scope["loss_recovery_delay"], scope["loss_recovery_unit"])
        if (
            scope["starts_at"]
            and scope["expires_at"]
            and datetime.fromisoformat(scope["expires_at"]) <= datetime.fromisoformat(scope["starts_at"])
        ):
            raise ValueError("Expiry must be after the start")
        if scope["max_order_usd"] is not None and any(
            scope[key] is not None and scope["max_order_usd"] > scope[key]
            for key in ("max_exposure_usd", "max_daily_notional_usd")
        ):
            raise ValueError("Per-order limit cannot exceed exposure or daily traded amount")
        for group, labels, controls in (
            ("allocation_policy", POLICY_LABELS, self.policy_inputs),
            ("earnings_thresholds", EARNINGS_LABELS, self.earnings_inputs),
        ):
            document[group] = {
                key: _numeric(control.text(), labels[key]) for key, control in controls.items()
            }
        if all(value is not None for value in document["allocation_policy"].values()):
            AllocationPolicy(**document["allocation_policy"]).validate()
        if (
            all(
                value is not None
                for key, value in scope.items()
                if key not in {"expires_at", "loss_recovery_delay"}
            )
            and scope["allowed_symbols"]
            and all(value is not None for value in document["earnings_thresholds"].values())
            and all(value is not None for value in document["allocation_policy"].values())
        ):
            validate_candidate(document)
        return document

    def save_to(self, target: Path, *, overwrite=False):
        document = self._document()
        self.saved_path = save_candidate_document(
            target, document, source_path=self.source_path, overwrite=overwrite
        )

    def _choose_save(self):
        default = self.data_dir / "session-candidate.json"
        if self.source_path is not None:
            default = self.source_path.with_name(f"{self.source_path.stem}-edited.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save session candidate", str(default), "JSON files (*.json)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return
        target = Path(path)
        overwrite = False
        if target.exists() and (self.source_path is None or target.resolve() != self.source_path):
            overwrite = (
                QMessageBox.question(
                    self,
                    "Replace candidate copy?",
                    f"Replace {target.name}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            )
            if not overwrite:
                return
        try:
            self.save_to(target, overwrite=overwrite)
        except (OSError, TypeError, ValueError) as error:
            self.status.setText(f"Not saved: {error}")
            return
        self.accept()

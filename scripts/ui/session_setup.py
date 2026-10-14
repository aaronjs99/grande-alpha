"""Candidate, limit, and local authorization-file setup for the desktop."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from grande_alpha.configuration.json_inputs import load_json
from grande_alpha.execution.candidate_profile import (
    check_candidate_shape,
    save_candidate_document,
    validate_candidate,
)
from grande_alpha.ui.candidate_editor import CandidateEditor


class SessionSetup:
    def _edit_candidate(self, *, new: bool) -> None:
        if self._candidate_editor is not None and self._candidate_editor.isVisible():
            self._candidate_editor.raise_()
            return
        source = self.candidate_edit.text().strip()
        try:
            dialog = CandidateEditor(
                self.data_dir, self,
                document=None if new else self._candidate_document,
                source_path=None if new or not source else Path(source),
                example=new,
            )
        except (TypeError, ValueError) as error:
            self._show_error(f"Could not open the session editor: {error}")
            return
        self._candidate_editor = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(lambda: self._load_candidate(dialog.saved_path) if dialog.saved_path else None)
        def finished(_result):
            if self._candidate_editor is dialog:
                self._candidate_editor = None
        dialog.finished.connect(finished)
        dialog.open()

    def _choose_candidate(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose strategy and limits candidate", str(self.data_dir), "JSON files (*.json)"
        )
        if path:
            self.candidate_edit.setText(path)
            self._load_candidate(Path(path))

    def _candidate_path_entered(self) -> None:
        text = self.candidate_edit.text().strip()
        if text:
            self._load_candidate(Path(text))
        else:
            self._candidate_document = None
            self._candidate_ready = False
            self._limits_dirty = False
            self._invalidate_review("Candidate cleared")
            self._refresh_controls()

    def _load_candidate(self, path: Path) -> None:
        self._invalidate_review("Candidate changed")
        self._candidate_ready = False
        try:
            if not path.is_file() or path.stat().st_size > 65_536:
                raise ValueError("Choose an existing candidate no larger than 64 KiB")
            document = load_json(path, max_bytes=65_536)
            check_candidate_shape(document)
            scope = document.get("scope") if isinstance(document, dict) else None
            if not isinstance(scope, dict):
                raise ValueError("The candidate has no execution scope")
            missing = [key for key, _label, _kind in self.LIMIT_FIELDS if key not in scope]
            if missing:
                raise ValueError(f"The candidate is missing {', '.join(missing)}")
            self._loading_limits = True
            for key, _label, kind in self.LIMIT_FIELDS:
                raw_value = scope[key]
                if raw_value is None:
                    self.limit_inputs[key].clear()
                    self.limit_inputs[key].setEnabled(False)
                    continue
                if kind == "integer":
                    if type(raw_value) is not int or raw_value <= 0:
                        raise ValueError(f"{key} must be a positive whole number")
                    if not self.limit_inputs[key].minimum() <= raw_value <= self.limit_inputs[key].maximum():
                        raise ValueError(f"{key} exceeds this visual editor's range")
                    self.limit_inputs[key].setValue(raw_value)
                else:
                    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                        raise ValueError(f"{key} must be numeric")
                    numeric = float(raw_value)
                    self.limit_inputs[key].setValue(numeric)
                    if self.limit_inputs[key].value() != numeric:
                        raise ValueError(
                            f"{key} uses precision or range this visual editor cannot represent"
                        )
                self.limit_inputs[key].setEnabled(True)
            self._candidate_document = document
            self._limits_dirty = False
            self.candidate_edit.setText(str(path.resolve()))
            symbols = ", ".join(scope.get("allowed_symbols") or ()) or "no symbols"
            try:
                validate_candidate(document)
                self._candidate_ready = True
                self.limit_state.setText(f"Loaded {symbols}. Ready for Review after connecting.")
            except ValueError as error:
                self.limit_state.setText(f"Draft · Edit setup to complete this candidate. {error}")
            self._invalidate_review("Candidate loaded")
            self._clear_banner()
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, OverflowError) as error:
            self._candidate_document = None
            self._candidate_ready = False
            self._limits_dirty = False
            for control in self.limit_inputs.values():
                control.setEnabled(False)
            self.limit_state.setText("Candidate not ready.")
            self._show_error(f"Could not load the candidate: {error}")
        finally:
            self._loading_limits = False
            self._refresh_controls()

    def _limit_changed(self, *_args) -> None:
        if self._loading_limits or self._candidate_document is None:
            return
        self._limits_dirty = True
        self.limit_state.setText("Limits changed. Save a new candidate before Review.")
        self._invalidate_review("Limits changed")
        self._refresh_controls()

    def _review_input_changed(self, *_args) -> None:
        self._invalidate_review("Session inputs changed")
        self._refresh_controls()

    def _choose_limit_destination(self) -> None:
        source = Path(self.candidate_edit.text().strip()) if self.candidate_edit.text().strip() else None
        default = self.data_dir / "session-candidate.json"
        if source is not None:
            default = source.with_name(f"{source.stem}-bounded.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save bounded candidate", str(default), "JSON files (*.json)"
        )
        if path:
            target = Path(path)
            source = Path(self.candidate_edit.text().strip()).resolve()
            if target.resolve() == source:
                self._show_error("Choose a different file. The loaded source candidate is never overwritten.")
                return
            overwrite = False
            if target.exists():
                answer = QMessageBox.question(
                    self,
                    "Replace bounded candidate?",
                    f"{target.name} already exists. Replace that copy? The loaded source remains unchanged.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                overwrite = True
            try:
                self._save_limits(target, overwrite=overwrite)
            except (OSError, TypeError, ValueError) as error:
                self._show_error(f"Limits were not saved: {error}")

    def _save_limits(self, target: Path, *, overwrite: bool = False) -> None:
        if self._candidate_document is None:
            raise ValueError("Load a candidate first")
        values = {key: control.value() for key, control in self.limit_inputs.items()}
        for key in ("max_orders", "max_orders_per_minute"):
            values[key] = int(values[key])
        if values["max_order_usd"] > min(
            values["max_exposure_usd"], values["max_daily_notional_usd"]
        ):
            raise ValueError("Per-order limit cannot exceed exposure or daily traded amount")
        document = copy.deepcopy(self._candidate_document)
        document["scope"].update(values)
        validate_candidate(document)
        source = Path(self.candidate_edit.text().strip()).resolve()
        target = save_candidate_document(target, document, source_path=source, overwrite=overwrite)
        self._candidate_document = document
        self._candidate_ready = True
        self.candidate_edit.setText(str(target))
        self._limits_dirty = False
        self.limit_state.setText(f"Saved bounded candidate: {target.name}")
        self._invalidate_review("Bounded candidate saved")
        self._clear_banner()
        self._log("info", f"Saved a new candidate copy: {target.name}")
        self._refresh_controls()

    def _choose_authorization(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose local permit file",
            self.authorization_edit.text() or str(self.data_dir / "mixed-authorization.json"),
            "JSON files (*.json)",
        )
        if path:
            self.authorization_edit.setText(path)

    def _choose_earnings_database(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose earnings observations",
            self.earnings_edit.text() or str(self.data_dir),
            "SQLite databases (*.db *.sqlite);;All files (*)",
        )
        if path:
            self.earnings_edit.setText(path)

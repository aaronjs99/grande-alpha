"""Candidate, limit, and local authorization-file setup for the desktop."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog

from grande_alpha.configuration.json_inputs import load_json
from grande_alpha.execution.candidate_profile import (
    check_candidate_shape,
    validate_candidate,
)
from grande_alpha.ui.candidate_editor import SESSION_FIELDS, CandidateEditor


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

    def _load_candidate(self, path: Path) -> None:
        self._invalidate_review("Candidate changed")
        self._candidate_ready = False
        try:
            if not path.is_file() or path.stat().st_size > 65_536:
                raise ValueError("Choose an existing candidate no larger than 64 KiB")
            document = load_json(path, max_bytes=65_536)
            check_candidate_shape(document)
            scope = document["scope"]
            self._candidate_document = document
            self.candidate_edit.setText(str(path.resolve()))
            self.candidate_limits.setPlainText("\n".join(
                f"{label}: {json.dumps(scope[key], allow_nan=False) if scope[key] is not None else 'Not set'}"
                for key, label, _kind in SESSION_FIELDS if key.startswith("max_")
            ))
            symbols = ", ".join(scope.get("allowed_symbols") or ()) or "no symbols"
            try:
                validate_candidate(document)
                self._candidate_ready = True
                self.candidate_state.setText(f"Loaded {symbols}. Ready for Review after connecting. Edit setup saves a new copy.")
            except ValueError as error:
                self.candidate_state.setText(f"Draft · Edit setup to complete this candidate. {error}")
            self._invalidate_review("Candidate loaded")
            self._clear_banner()
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, OverflowError) as error:
            self._candidate_document = None
            self._candidate_ready = False
            self.candidate_limits.clear()
            self.candidate_state.setText("Candidate not ready.")
            self._show_error(f"Could not load the candidate: {error}")
        finally:
            self._refresh_controls()

    def _review_input_changed(self, *_args) -> None:
        self._invalidate_review("Session inputs changed")
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

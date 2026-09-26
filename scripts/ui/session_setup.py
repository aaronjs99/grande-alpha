"""Candidate, limit, and local authorization-file setup for the desktop."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox


class SessionSetup:
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
            self._limits_dirty = False
            self._invalidate_review("Candidate cleared")
            self._refresh_controls()

    def _load_candidate(self, path: Path) -> None:
        try:
            if not path.is_file() or path.stat().st_size > 65_536:
                raise ValueError("Choose an existing candidate no larger than 64 KiB")
            document = json.loads(path.read_text(encoding="utf-8"))
            scope = document.get("scope") if isinstance(document, dict) else None
            if not isinstance(scope, dict):
                raise ValueError("The candidate has no execution scope")
            missing = [key for key, _label, _kind in self.LIMIT_FIELDS if key not in scope]
            if missing:
                raise ValueError(f"The candidate is missing {', '.join(missing)}")
            self._loading_limits = True
            for key, _label, kind in self.LIMIT_FIELDS:
                raw_value = scope[key]
                if kind == "integer":
                    if type(raw_value) is not int or raw_value <= 0:
                        raise ValueError(f"{key} must be a positive whole number")
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
            self.limit_state.setText(
                f"Loaded {symbols}. Adjust the risk envelope only, or continue with the unchanged candidate."
            )
            self._invalidate_review("Candidate loaded")
            self._clear_banner()
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            self._candidate_document = None
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
        target = target.resolve()
        source = Path(self.candidate_edit.text().strip()).resolve()
        if target == source:
            raise ValueError("The loaded source candidate is never overwritten; choose a new file")
        if target.exists() and not overwrite:
            raise ValueError("The destination already exists; explicit overwrite confirmation is required")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(target)
        self._candidate_document = document
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

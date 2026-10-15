"""First-use broker setup acceptance; disposable files and no broker calls."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFormLayout

from grande_alpha.execution.candidate_profile import candidate_template, load_candidate, validate_candidate
from grande_alpha.execution.session_runtime import BrokerSessionRuntime
from grande_alpha.ui.candidate_editor import CandidateEditor
from grande_alpha.ui.session_components import review_text
from grande_alpha.ui.session_window import SessionWindow


def candidate_fixture():
    document = candidate_template()
    document["scope"].update(
        account_number="fixture-account",
        allowed_symbols=["QQQ"],
        starts_at="2026-10-14T08:00:00-07:00",
        expires_at="2026-10-14T16:00:00-07:00",
        max_order_usd=10,
        max_exposure_usd=100,
        max_daily_notional_usd=200,
        max_daily_loss_usd=5,
        max_orders=5,
        max_orders_per_minute=1,
        max_quote_age_seconds=8,
        max_spread_bps=20,
    )
    document["earnings_thresholds"] = {key: 1 for key in document["earnings_thresholds"]}
    return document


class SessionSetupAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for filename in ("segoeui.ttf", "segoeuib.ttf"):
            path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        cls.app.setFont(QFont("Segoe UI", 10))

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="grande-candidate-check-")
        self.path = Path(self.scratch.name)
        self.window = SessionWindow(self.path, auto_probe=False)
        self.window.status_timer.stop()

    def tearDown(self):
        self.window._closing = True
        self.window.close()
        self.app.processEvents()
        self.scratch.cleanup()

    def test_first_use_draft_has_no_financial_defaults_or_worker_side_effects(self):
        dialog = CandidateEditor(self.path, self.window)
        for name in ("account_number", "allowed_symbols", "max_order_usd", "max_orders"):
            self.assertEqual(dialog.inputs[name].text(), "")
        for control in dialog.earnings_inputs.values():
            self.assertEqual(control.text(), "")
        target = self.path / "draft.json"
        dialog.save_to(target)
        self.window._load_candidate(target)
        self.assertFalse(self.window._candidate_ready)
        self.assertIsNotNone(self.window._candidate_document)
        self.assertFalse(self.window.review_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertTrue(self.window.new_candidate_button.isEnabled())
        self.assertTrue(self.window.edit_candidate_button.isEnabled())
        self.assertEqual({item.name for item in self.path.iterdir()}, {"draft.json"})
        dialog.close()

    def test_complete_form_roundtrips_exactly_but_never_approves_or_starts(self):
        document = candidate_fixture()
        dialog = CandidateEditor(self.path, self.window, document=document)
        target = self.path / "ready.json"
        dialog.save_to(target)
        scope, policy, thresholds = load_candidate(target)
        self.assertEqual(scope.account_number, "fixture-account")
        self.assertEqual(scope.allowed_symbols, ("QQQ",))
        self.assertEqual(scope.max_order_usd, 10)
        self.assertEqual(thresholds, document["earnings_thresholds"])
        self.assertEqual(policy.max_invested_fraction, document["allocation_policy"]["max_invested_fraction"])
        self.window._load_candidate(target)
        self.assertTrue(self.window._candidate_ready)
        self.assertFalse(self.window.review_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertIsNone(self.window._review_result)
        self.assertEqual(self.window._authorized_scope, "")
        self.assertEqual({item.name for item in self.path.iterdir()}, {"ready.json"})
        dialog.close()

    def test_invalid_numbers_and_scope_cannot_be_saved_as_ready(self):
        dialog = CandidateEditor(self.path, self.window, document=candidate_fixture())
        for text in ("NaN", "inf", "-1", "0", "ten"):
            dialog.inputs["max_order_usd"].setText(text)
            with self.assertRaises(ValueError):
                dialog.save_to(self.path / "invalid.json")
            self.assertFalse((self.path / "invalid.json").exists())
        dialog.inputs["max_order_usd"].setText("1000")
        with self.assertRaises(ValueError):
            dialog.save_to(self.path / "invalid.json")
        dialog.close()

    def test_source_and_unconfirmed_existing_destination_are_preserved(self):
        source = self.path / "source.json"
        source.write_text(json.dumps(candidate_fixture()), encoding="utf-8")
        before = source.read_bytes()
        dialog = CandidateEditor(self.path, self.window, document=candidate_fixture(), source_path=source)
        with self.assertRaises(ValueError):
            dialog.save_to(source, overwrite=True)
        destination = self.path / "other.json"
        destination.write_text("keep me", encoding="utf-8")
        with self.assertRaises((ValueError, FileExistsError)):
            dialog.save_to(destination)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(destination.read_text(encoding="utf-8"), "keep me")
        dialog.save_to(destination, overwrite=True)
        load_candidate(destination)
        dialog.close()

    def test_draft_can_be_reopened_completed_and_reviewed_only_when_connected(self):
        draft = candidate_template()
        dialog = CandidateEditor(self.path, self.window, document=draft)
        for key, value in candidate_fixture()["scope"].items():
            if key in dialog.inputs and value is not None:
                dialog.inputs[key].setText(", ".join(value) if isinstance(value, list) else str(value))
        for control in dialog.earnings_inputs.values():
            control.setText("1")
        target = self.path / "completed.json"
        dialog.save_to(target)
        self.window._load_candidate(target)
        self.assertFalse(self.window._can_review())
        self.window._apply_status({"connected": True, "running": False, "generation": 0, "phase": "Stopped"})
        self.assertTrue(self.window._can_review())
        self.assertFalse(self.window.start_button.isEnabled())
        dialog.close()

    def test_partial_and_ready_files_both_enforce_exact_schema_and_integer_types(self):
        for value in (True, "1", 2):
            bad = copy.deepcopy(candidate_fixture())
            bad["schema_version"] = value
            with self.assertRaises(ValueError):
                validate_candidate(bad)
        bad = candidate_fixture()
        bad["scope"]["max_orders"] = 1.5
        with self.assertRaises(ValueError):
            validate_candidate(bad)

    def test_example_is_visible_bounded_and_still_needs_an_account_and_symbols(self):
        dialog = CandidateEditor(self.path, self.window, example=True)
        self.assertIn("Example", dialog.status.text())
        self.assertEqual(dialog.inputs["max_order_usd"].text(), "10")
        self.assertEqual(dialog.inputs["account_number"].text(), "")
        self.assertEqual(dialog.inputs["allowed_symbols"].text(), "")
        target = self.path / "example.json"
        dialog.save_to(target)
        self.window._load_candidate(target)
        self.assertFalse(self.window._candidate_ready)
        self.assertFalse(self.window.start_button.isEnabled())
        dialog.close()

    def test_enter_in_an_editor_field_never_resets_entered_settings_to_example(self):
        document = candidate_fixture()
        document["scope"].update(max_order_usd=25, loss_recovery_unit="hours", loss_recovery_delay=2)
        dialog = CandidateEditor(self.path, self.window, document=document)
        dialog.show()
        dialog.inputs["account_number"].setFocus()
        self.app.processEvents()
        with patch("grande_alpha.ui.candidate_editor.QFileDialog.getSaveFileName", return_value=("", "")):
            QTest.keyClick(dialog.inputs["account_number"], Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(dialog._document(), document)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(list(self.path.iterdir()), [])
        dialog.close()

    def test_focusing_and_copying_loaded_path_never_reloads_or_invalidates_review(self):
        source = self.path / "source.json"
        source.write_text(json.dumps(candidate_fixture()), encoding="utf-8")
        self.window._load_candidate(source)
        self.window.show()
        self.window._show_setup("broker")
        self.app.processEvents()
        reviewed = {"scope_digest": "fixture-scope", "generation": 1}
        self.window._review_result = reviewed
        self.window._authorized_scope = "fixture-scope"
        self.window.candidate_edit.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.window.candidate_edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(self.window.candidate_edit, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertIs(self.window._review_result, reviewed)
        self.assertEqual(self.window._authorized_scope, "fixture-scope")
        self.assertEqual(self.window._candidate_document, candidate_fixture())
        self.window.setup_dialog.reject()

    def test_session_and_strategy_pages_fit_a_narrow_window(self):
        dialog = CandidateEditor(self.path, self.window, example=True)
        dialog.show()
        self.app.processEvents()
        dialog.resize(370, 650)
        for index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.app.processEvents()
            self.app.processEvents()
            scroll = dialog.tabs.widget(index)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertTrue(dialog.save_button.isVisible())
            form = scroll.widget().layout()
            for row in range(form.rowCount()):
                label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                if label_item and field_item:
                    self.assertFalse(label_item.widget().geometry().intersects(field_item.widget().geometry()))
                if row and label_item:
                    previous = form.itemAt(row - 1, QFormLayout.ItemRole.FieldRole)
                    if previous:
                        self.assertFalse(label_item.widget().geometry().intersects(previous.widget().geometry()))
        dialog.close()

    def test_full_editor_saves_changed_copy_without_touching_source_or_retaining_review(self):
        source = self.path / ".copy.json.tmp"
        source.write_text(json.dumps(candidate_fixture()), encoding="utf-8")
        before = source.read_bytes()
        self.window._load_candidate(source)
        self.window._review_result = {"scope_digest": "fixture-scope", "generation": 1}
        self.window._authorized_scope = "fixture-scope"
        self.window._edit_candidate(new=False)
        dialog = self.window._candidate_editor
        dialog.inputs["max_order_usd"].setText("9")
        dialog.save_to(self.path / "copy.json")
        dialog.accept()
        self.app.processEvents()
        self.assertEqual(source.read_bytes(), before)
        scope, _policy, _thresholds = load_candidate(self.path / "copy.json")
        self.assertEqual(scope.max_order_usd, 9)
        self.assertEqual(self.window._candidate_document["scope"]["max_order_usd"], 9)
        self.assertEqual(Path(self.window.candidate_edit.text()), self.path / "copy.json")
        self.assertIsNone(self.window._review_result)
        self.assertEqual(self.window._authorized_scope, "")
        self.assertFalse(self.window.start_button.isEnabled())

    def test_valid_candidate_precision_and_large_counts_survive_loading_and_full_editing(self):
        for count in (2_000_000_000, 10**50):
            document = candidate_fixture()
            document["scope"]["max_orders"] = count
            document["scope"]["max_order_usd"] = 0.123456789123
            path = self.path / "large.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            before = path.read_bytes()
            self.window._load_candidate(path)
            self.assertTrue(self.window._candidate_ready)
            self.assertEqual(self.window._candidate_document, document)
            self.assertIn(str(count), self.window.candidate_limits.toPlainText())
            self.assertIn("0.123456789123", self.window.candidate_limits.toPlainText())
            self.window._edit_candidate(new=False)
            dialog = self.window._candidate_editor
            self.assertEqual(dialog.inputs["max_orders"].text(), str(count))
            destination = self.path / f"edited-{count}.json"
            dialog.save_to(destination)
            dialog.accept()
            self.app.processEvents()
            scope, _policy, _thresholds = load_candidate(destination)
            self.assertEqual(scope.max_orders, count)
            self.assertEqual(scope.max_order_usd, 0.123456789123)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(self.window.start_button.isEnabled())

    def test_cancelling_full_editor_preserves_loaded_document_review_and_source(self):
        source = self.path / "source.json"
        document = candidate_fixture()
        source.write_text(json.dumps(document), encoding="utf-8")
        before = source.read_bytes()
        self.window._load_candidate(source)
        reviewed = {"scope_digest": "fixture-scope", "generation": 1}
        self.window._review_result = reviewed
        self.window._authorized_scope = "fixture-scope"
        self.window._edit_candidate(new=False)
        self.window._candidate_editor.inputs["max_order_usd"].setText("9")
        self.window._candidate_editor.reject()
        self.app.processEvents()
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(self.window._candidate_document, document)
        self.assertIs(self.window._review_result, reviewed)
        self.assertEqual(self.window._authorized_scope, "fixture-scope")
        self.window._edit_candidate(new=False)
        self.assertEqual(self.window._candidate_editor.inputs["max_order_usd"].text(), "10")
        self.window._candidate_editor.reject()
        self.app.processEvents()

    def test_unchanged_accepted_candidates_preserve_large_integer_and_text_fields(self):
        for exponent in (200, 1000):
            with self.subTest(exponent=exponent):
                document = candidate_fixture()
                document["scope"].update(
                    account_number="fixture-account-" + "A" * 256,
                    max_orders=10**exponent,
                    max_orders_per_minute=10**(exponent + 1),
                    loss_recovery_unit="minutes",
                    loss_recovery_delay=10**(exponent - 1),
                )
                validate_candidate(document)
                source = self.path / f"source-{exponent}.json"
                source.write_text(json.dumps(document), encoding="utf-8")
                before = source.read_bytes()
                dialog = CandidateEditor(self.path, self.window, document=document, source_path=source)
                try:
                    for name in ("max_orders", "max_orders_per_minute", "account_number"):
                        self.assertEqual(dialog.inputs[name].text(), str(document["scope"][name]))
                    destination = self.path / f"copy-{exponent}.json"
                    dialog.save_to(destination)
                    saved, _policy, _thresholds = load_candidate(destination)
                    self.assertEqual(saved.account_number, document["scope"]["account_number"])
                    self.assertEqual(saved.max_orders, document["scope"]["max_orders"])
                    self.assertEqual(saved.max_orders_per_minute, document["scope"]["max_orders_per_minute"])
                    self.assertEqual(saved.loss_recovery_delay, document["scope"]["loss_recovery_delay"])
                    self.assertEqual(source.read_bytes(), before)
                finally:
                    dialog.close()

    def test_loading_an_invalid_file_invalidates_a_previous_review(self):
        path = self.path / "invalid.json"
        path.write_text("{}", encoding="utf-8")
        self.window._review_result = {"scope_digest": "fixture-scope", "generation": 1}
        self.window._authorized_scope = "fixture-scope"
        self.window._load_candidate(path)
        self.assertIsNone(self.window._review_result)
        self.assertEqual(self.window._authorized_scope, "")
        self.assertFalse(self.window.start_button.isEnabled())

    def test_duplicate_json_fields_are_rejected_consistently_with_the_worker(self):
        path = self.path / "duplicate.json"
        raw = json.dumps(candidate_fixture()).replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        )
        path.write_text(raw, encoding="utf-8")
        self.window._load_candidate(path)
        self.assertFalse(self.window._candidate_ready)
        self.assertIsNone(self.window._candidate_document)
        self.assertFalse(self.window.review_button.isEnabled())
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            load_candidate(path)

    def test_review_includes_the_exact_loss_recovery_terms_without_order_calls(self):
        document = candidate_fixture()
        document["scope"].update(loss_recovery_unit="minutes", loss_recovery_delay=15)
        path = self.path / "timed.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        class Broker:
            connected = True
            async def get_accounts(self):
                return [SimpleNamespace(account_number="fixture-account", agentic_allowed=True, masked="fixture · 1234")]
        runtime = BrokerSessionRuntime(self.path)
        runtime.broker = Broker()
        result = asyncio.run(runtime.review({"candidate_path": str(path)}))
        self.assertEqual(result["loss_recovery"], {"unit": "minutes", "delay": 15})
        self.assertIn("15 minutes", review_text(result))
        self.assertFalse(result["orders_submitted"])
        bad = candidate_fixture()
        bad["scope"]["allowed_symbols"] = ["QQQ", "QQQ"]
        with self.assertRaises(ValueError):
            validate_candidate(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)

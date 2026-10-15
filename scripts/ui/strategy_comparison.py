"""Compact offline research comparison; never applies a strategy to an account."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from grande_alpha.research.strategy_selection import compare_strategies, load_strategy_dataset


class StrategyComparisonDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.path, self.report = owner, None, None
        self._task = None
        self._cancel = Event()
        self._closed = False
        self.setWindowTitle("GRANDE · Compare strategies")
        self.setMinimumSize(350, 460)
        self.resize(920, 620)
        box = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        box.addWidget(self.summary)
        row = QHBoxLayout()
        self.choose = QPushButton("Choose history…")
        self.choose.clicked.connect(self._choose)
        self.run_button = QPushButton("Compare")
        self.run_button.setObjectName("primary")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.start)
        row.addWidget(self.choose)
        row.addWidget(self.run_button)
        box.addLayout(row)
        self.source = QLabel("Local cached JSON · QQQ, TQQQ and SQQQ · 1m or 5m · at least 30 sessions")
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setWordWrap(True)
        box.addWidget(self.source)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Strategy", "Net return", "Drawdown", "Trades", "Worst block", "3× costs"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 200)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAccessibleName("Development-period strategy comparison")
        box.addWidget(self.table, 1)
        self.status = QLabel("Virtual research only. The current paper and broker settings stay under their existing controls.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        footer = QHBoxLayout()
        self.save = QPushButton("Save report…")
        self.save.setEnabled(False)
        self.save.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        footer.addWidget(self.save)
        footer.addStretch()
        footer.addWidget(close)
        box.addLayout(footer)
        self._reset_results()

    def _reset_results(self):
        self.report = None
        self.table.setRowCount(0)
        self.save.setEnabled(False)
        self.summary.setText("Compare five fixed rules after costs. Final test dates stay separate from selection.")

    def showEvent(self, event):
        self._closed = False
        busy = self._task is not None and not self._task.done()
        self.choose.setEnabled(not busy)
        self.run_button.setEnabled(not busy and self.path is not None)
        super().showEvent(event)

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose local historical dataset",
                                             str(self.owner.data_dir / "sandbox_cache"), "JSON history (*.json)")
        if path:
            self.path = Path(path)
            self._reset_results()
            self.source.setText(self.path.name)
            self.status.setText("Ready · uses illustrative $1,000 virtual cash and at most $100 per entry; no model needed.")
            self.run_button.setEnabled(True)

    def start(self):
        if self.path is None or (self._task is not None and not self._task.done()):
            return
        self._cancel = Event()
        self._task = self.owner._spawn(self._run(self.path, self._cancel))

    async def _run(self, path, cancel):
        self.choose.setEnabled(False)
        self.run_button.setEnabled(False)
        self._reset_results()
        self.status.setText("Comparing locally…")
        try:
            def compute():
                return compare_strategies(load_strategy_dataset(path), cancelled=cancel.is_set)
            report = await asyncio.to_thread(compute)
            if not self._closed and not cancel.is_set():
                self.render(report)
        except asyncio.CancelledError:
            cancel.set()
            raise
        except Exception as error:
            if not self._closed:
                self.status.setText(f"Comparison unavailable: {error}")
        finally:
            if not self._closed:
                self.choose.setEnabled(True)
                self.run_button.setEnabled(self.path is not None)

    def render(self, report):
        self.report = report
        self.table.setRowCount(len(report["candidates"]))
        for index, row in enumerate(report["candidates"]):
            values = (row["label"], f"{row['return_pct']:+.2f}%", f"{row['max_drawdown_pct']:.2f}%",
                      str(row["round_trips"]), f"{row['worst_block_return_pct']:+.2f}%",
                      f"{row['cost_stress']['3']['return_pct']:+.2f}%")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                explanation = "Zero-return cash reference" if row["strategy"] == "cash" else (
                    "\n".join(row["rejection_reasons"]) or "Passed development screen")
                item.setToolTip(explanation)
                self.table.setItem(index, column, item)
        self.table.resizeRowsToContents()
        dataset, split = report["dataset"], report["split"]
        self.summary.setText(f"{dataset['sessions']} sessions · {dataset['interval']} · development {split['development_start'][:10]} to {split['development_end'][:10]}")
        selected = report["selected_for_holdout"]
        result = report["holdout"]["base"]["return_pct"]
        outcome = "No active strategy passed development; cash retained" if selected == "cash" else (
            "Final screen passed; further validation required" if report["holdout"]["passed"] else
            "Final screen failed; cash retained")
        self.status.setText(f"{outcome}. Tested: {selected.replace('_', ' ')} · final test {result:+.2f}%. "
                            "No trading approval. " + " · ".join(report["blockers"][:2]))
        self.save.setEnabled(True)

    def _save(self):
        if self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save strategy comparison", "strategy-comparison.json", "JSON report (*.json)")
        if path:
            try:
                target = Path(path)
                if self.path is not None and (target.resolve() == self.path.resolve() or
                                              (target.exists() and target.samefile(self.path))):
                    raise ValueError("Choose a separate report file; the source history is preserved")
                encoded = json.dumps(self.report, indent=2, allow_nan=False) + "\n"
                descriptor, temporary = tempfile.mkstemp(prefix=".strategy-report-", dir=target.parent)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                        output.write(encoded)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                self.status.setText(f"Saved {target.name}. This report does not approve trading.")
            except (OSError, ValueError) as error:
                self.status.setText(f"Report was not saved: {error}")

    def reject(self):
        self._closed = True
        self._cancel.set()
        super().reject()

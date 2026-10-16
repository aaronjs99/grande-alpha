"""Inspect saved virtual fills without starting a worker or connecting a broker."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from grande_alpha.research.paper.journal import read_journal, save_journal_csv


def _label(text=''):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


class PaperJournalDialog(QDialog):
    def __init__(self, owner, *, loader=None):
        super().__init__(owner)
        self.owner, self._loader = owner, loader or read_journal
        self._path = owner.data_dir / 'agent-paper.db'
        self._snapshot = None
        self._task = None
        self._epoch = 0
        self._closed = False
        self.setWindowTitle('GRANDE · Paper journal')
        self.setMinimumSize(350, 440)
        self.resize(920, 590)
        box = QVBoxLayout(self)
        box.addWidget(_label('Saved virtual trades · no broker or AI connection needed'))
        row = QHBoxLayout()
        self.sessions = QComboBox()
        self.sessions.setAccessibleName('Saved paper session')
        self.sessions.setMinimumContentsLength(10)
        self.sessions.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sessions.activated.connect(self._selected)
        row.addWidget(self.sessions, 1)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        box.addLayout(row)
        self.summary = _label()
        box.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(('Fill', 'Time', 'Instrument', 'Side', 'Quantity', 'Price', 'Realized P/L'))
        self.table.setAccessibleName('Saved virtual fills, newest first')
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for index, width in enumerate((50, 170, 150, 65, 110, 115, 120)):
            self.table.setColumnWidth(index, width)
        box.addWidget(self.table, 1)
        self.status = _label('No journal loaded')
        box.addWidget(self.status)
        footer = QHBoxLayout()
        self.older_button = QPushButton('Older fills')
        self.older_button.clicked.connect(self._older)
        footer.addWidget(self.older_button)
        self.export_button = QPushButton('Export page…')
        self.export_button.clicked.connect(self._export)
        footer.addWidget(self.export_button)
        footer.addStretch()
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        box.addLayout(footer)
        self._set_busy(False)

    def showEvent(self, event):  # noqa: N802 - Qt API
        super().showEvent(event)
        self._closed = False
        self.refresh()

    def refresh(self):
        self._reload(self.sessions.currentData(), None)

    def _selected(self, _index):
        self.refresh()

    def _older(self):
        if self._snapshot is not None and self._snapshot['next_before'] is not None:
            self._reload(self._snapshot['session']['session_id'], self._snapshot['next_before'])

    def _reload(self, session_id, before):
        if self._task is not None and not self._task.done():
            return
        self._epoch += 1
        self._task = self.owner._spawn(self._load(session_id, before, self._epoch))

    async def _load(self, session_id, before, epoch):
        self._set_busy(True)
        self.status.setText('Reading saved virtual records…')
        try:
            result = await asyncio.to_thread(self._loader, self._path, session_id=session_id, before=before, limit=100)
            if not self._closed and epoch == self._epoch:
                self._render(result)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self._closed and epoch == self._epoch:
                self._snapshot = None
                self.table.setRowCount(0)
                self.summary.clear()
                self.status.setText(f'Journal unavailable: {str(error)[:1000]}')
        finally:
            if not self._closed and epoch == self._epoch:
                self._set_busy(False)

    def _render(self, result):
        self._snapshot = deepcopy(result)
        self.sessions.clear()
        for session in result['sessions']:
            at = datetime.fromisoformat(session['created_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            source = 'Demo' if session['source'] == 'demo' else 'Broker quotes'
            self.sessions.addItem(f'{at} · {source}', session['session_id'])
        selected = result['session']
        demo = selected is not None and selected['source'] == 'demo'
        self.table.horizontalHeaderItem(1).setText('Demo time' if demo else 'Time (local)')
        self.table.horizontalHeaderItem(1).setToolTip(
            'Fixed simulated clock; the session picker shows when this run was started.' if demo else
            'Quote timestamp in your local timezone.'
        )
        self.table.setRowCount(len(result['fills']))
        if selected is None:
            self.summary.clear()
            self.status.setText('No saved paper sessions. Start paper to create virtual records.')
            return
        self.sessions.setCurrentIndex(self.sessions.findData(selected['session_id']))
        for row, fill in enumerate(result['fills']):
            at = datetime.fromisoformat(fill['filled_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            values = (str(fill['number']), at, fill['key'], fill['side'].upper(),
                      fill['quantity'], fill['price'], fill['realized_pnl'])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(fill['filled_at'] if column == 1 else value)
                self.table.setItem(row, column, item)
        equity = selected.get('equity')
        value = f'${Decimal(equity):,.2f}' if equity is not None else 'unavailable'
        self.summary.setText(f"Saved equity {value} · {result['fill_count']} simulated fills")
        count = len(result['fills'])
        pages = f'Showing {count} of {result["fill_count"]} fills, newest first. Export saves this page. '
        if len(result['sessions']) >= 100:
            pages += 'Session picker shows the latest 100 sessions. '
        self.status.setText(pages + selected.get('limitations', 'Saved virtual marks; no live performance claim.'))

    def _set_busy(self, busy):
        self.sessions.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.older_button.setEnabled(not busy and bool(self._snapshot and self._snapshot['next_before'] is not None))
        self.export_button.setEnabled(not busy and bool(self._snapshot and self._snapshot['fills']))

    def _export(self):
        if self._snapshot is None or not self._snapshot['fills']:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export displayed virtual fills', 'paper-fills.csv',
                                             'CSV files (*.csv)', options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix('.csv')
        overwrite = target.exists()
        if overwrite and QMessageBox.question(
            self, 'Replace CSV export?', f'Replace {target.name}?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            save_journal_csv(self._snapshot, target, overwrite=overwrite)
            self.status.setText(f'Exported {len(self._snapshot["fills"])} displayed virtual fills to {target.name}.')
        except (OSError, ValueError) as error:
            self.status.setText(f'Export was not saved: {str(error)[:1000]}')

    def _cancel_load(self):
        self._closed = True
        self._epoch += 1
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()

    def reject(self):
        self._cancel_load()
        super().reject()

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self._cancel_load()
        super().closeEvent(event)

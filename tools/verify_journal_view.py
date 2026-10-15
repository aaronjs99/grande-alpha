"""Offline paper journal interactions with disposable data and fake read services."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from grande_alpha.ui.paper_journal import PaperJournalDialog


def snapshot(session='first', number=3):
    return {'_journal_path': 'unused-offline.db',
            'sessions': [{'session_id': session, 'source': 'demo', 'created_at': '2026-10-14T12:00:00+00:00'}],
            'session': {'session_id': session, 'source': 'demo', 'created_at': '2026-10-14T12:00:00+00:00',
                        'equity': '1000', 'realized_pnl': '0', 'limitations': 'Saved synthetic marks only'},
            'fills': [{'number': number, 'key': 'equity:DEMO', 'side': 'buy', 'quantity': '0.12345678',
                       'price': '101.00000000000001', 'realized_pnl': '0', 'filled_at': '2026-10-14T12:00:00+00:00'}],
            'fill_count': 3, 'next_before': number if number > 1 else None}


class Owner(QWidget):
    def __init__(self, directory):
        super().__init__()
        self.data_dir = directory
    def _spawn(self, coroutine):
        return asyncio.create_task(coroutine)


class JournalViewChecks(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    async def asyncSetUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.owner = Owner(Path(self.scratch.name))
        self.dialog = None

    async def asyncTearDown(self):
        if self.dialog is not None:
            task = self.dialog._task
            self.dialog.reject()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        self.owner.close()
        self.app.processEvents()
        self.scratch.cleanup()

    async def open(self, loader=None):
        self.dialog = PaperJournalDialog(self.owner, loader=loader) if loader else PaperJournalDialog(self.owner)
        self.dialog.show()
        self.app.processEvents()
        await self.dialog._task
        self.app.processEvents()
        return self.dialog

    async def test_missing_journal_is_empty_and_creates_no_runtime_files(self):
        dialog = await self.open()
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(dialog.export_button.isEnabled())
        self.assertFalse(dialog.older_button.isEnabled())
        self.assertEqual(list(Path(self.scratch.name).iterdir()), [])

    async def test_pagination_uses_exclusive_cursor_and_refresh_returns_to_latest(self):
        calls = []
        def load(path, **options):
            calls.append(options)
            return snapshot(number=options['before'] - 1 if options.get('before') else 3)
        dialog = await self.open(load)
        self.assertEqual(dialog.table.item(0, 4).text(), '0.12345678')
        self.assertEqual(dialog.table.item(0, 5).text(), '101.00000000000001')
        dialog.older_button.click()
        await dialog._task
        self.assertEqual(calls[-1]['before'], 3)
        self.assertEqual(dialog.table.item(0, 0).text(), '2')
        dialog.older_button.click()
        await dialog._task
        self.assertEqual(dialog.table.item(0, 0).text(), '1')
        self.assertFalse(dialog.older_button.isEnabled())
        dialog.refresh_button.click()
        await dialog._task
        self.assertIsNone(calls[-1]['before'])
        self.assertEqual(dialog.table.item(0, 0).text(), '3')
        self.assertTrue(dialog.older_button.isEnabled())

    async def test_failed_read_clears_previous_results_and_export(self):
        def load(path, **options):
            if self.dialog and self.dialog.table.rowCount():
                raise ValueError('Fixture journal is unreadable')
            return snapshot()
        dialog = await self.open(load)
        self.assertTrue(dialog.export_button.isEnabled())
        with patch.object(dialog, '_loader', side_effect=ValueError('Fixture journal is unreadable')):
            dialog.refresh_button.click()
            await dialog._task
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(dialog.export_button.isEnabled())
        self.assertIn('unreadable', dialog.status.text())
        dialog.refresh_button.click()
        await dialog._task
        self.assertTrue(dialog.export_button.isEnabled())
        self.assertEqual(dialog.table.rowCount(), 1)

    async def test_selecting_another_session_resets_the_page(self):
        calls = []
        def load(path, **options):
            calls.append(options)
            result = snapshot(options.get('session_id') or 'first')
            result['sessions'] = [snapshot(item)['sessions'][0] for item in ('first', 'second')]
            return result
        dialog = await self.open(load)
        dialog.sessions.setCurrentIndex(1)
        dialog.sessions.activated.emit(1)
        await dialog._task
        self.assertEqual(calls[-1]['session_id'], 'second')
        self.assertIsNone(calls[-1]['before'])
        self.assertEqual(dialog._snapshot['session']['session_id'], 'second')

    async def test_close_during_read_and_reopen_ignore_the_late_snapshot(self):
        started, release = Event(), Event()
        def slow(path, **options):
            started.set()
            release.wait(3)
            return snapshot('stale')
        self.dialog = PaperJournalDialog(self.owner, loader=slow)
        self.dialog.show()
        self.app.processEvents()
        await asyncio.to_thread(started.wait, 2)
        old_task = self.dialog._task
        self.dialog.reject()
        await asyncio.gather(old_task, return_exceptions=True)
        self.dialog._loader = lambda *a, **k: snapshot('fresh')
        self.dialog.show()
        self.app.processEvents()
        await self.dialog._task
        release.set()
        await asyncio.sleep(.02)
        self.assertEqual(self.dialog._snapshot['session']['session_id'], 'fresh')
        self.assertTrue(self.dialog.export_button.isEnabled())

    async def test_export_uses_the_loaded_page_without_rereading_the_database(self):
        dialog = await self.open(lambda *a, **k: snapshot())
        target = Path(self.scratch.name) / 'page.csv'
        with patch('grande_alpha.ui.paper_journal.QFileDialog.getSaveFileName', return_value=(str(target), 'CSV')):
            with patch('grande_alpha.ui.paper_journal.save_journal_csv') as save:
                with patch.object(dialog, '_loader', side_effect=AssertionError('Export reread changing records')):
                    dialog.export_button.click()
                    save.assert_called_once()
                    self.assertEqual(save.call_args.args[0]['fills'][0]['number'], 3)

    async def test_declining_csv_replacement_preserves_the_existing_export(self):
        dialog = await self.open(lambda *a, **k: snapshot())
        target = Path(self.scratch.name) / 'page.csv'
        target.write_text('original export', encoding='utf-8')
        with patch('grande_alpha.ui.paper_journal.QFileDialog.getSaveFileName', return_value=(str(target), 'CSV')):
            with patch('grande_alpha.ui.paper_journal.QMessageBox.question', return_value=QMessageBox.StandardButton.No) as confirm:
                with patch('grande_alpha.ui.paper_journal.save_journal_csv') as save:
                    dialog.export_button.click()
                    save.assert_not_called()
                    self.assertEqual(confirm.call_args.args[-1], QMessageBox.StandardButton.No)
        self.assertEqual(target.read_text(encoding='utf-8'), 'original export')


if __name__ == '__main__':
    unittest.main(verbosity=2)

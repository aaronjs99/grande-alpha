"""Disposable command-line journeys; no broker, real profile, or model access."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from grande_alpha.cli import build_parser, main
from grande_alpha.interfaces.cli import research_commands


class CliWorkflows(unittest.TestCase):
    def invoke(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(arguments)
        self.assertEqual(code, 0, errors.getvalue())
        return json.loads(output.getvalue())

    def test_every_command_has_a_handler_and_usable_help(self):
        def visit(parser, prefix):
            groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
            if not groups:
                self.assertTrue(callable(parser.get_default('func')), prefix)
            for group in groups:
                for name, child in group.choices.items():
                    with self.subTest(command=[*prefix, name]):
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            with self.assertRaises(SystemExit) as stopped:
                                child.parse_args(['--help'])
                        self.assertEqual(stopped.exception.code, 0)
                        visit(child, [*prefix, name])
        visit(build_parser(), [])

    def test_missing_config_show_does_not_write_defaults(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'missing' / 'config.json'
            value = self.invoke(['config', 'show', '--path', str(path), '--json'])
            self.assertEqual(value['settings']['strategy']['strategy_name'], 'cash')
            self.assertFalse(path.parent.exists())

    def test_unsaved_demo_never_opens_a_records_database(self):
        with tempfile.TemporaryDirectory() as scratch:
            with patch('grande_alpha.research.sandbox.sandbox_config_path', return_value=Path(scratch) / 'sandbox.json'):
                with patch.object(research_commands, 'AuditStore', side_effect=AssertionError('Unsaved replay opened a database')) as store:
                    value = self.invoke(['research', 'sandbox', 'run', '--source', 'demo', '--days', '7',
                                         '--strategy', 'cash', '--no-save', '--json'])
                    store.assert_not_called()
            self.assertFalse(value['saved'])
            self.assertIn('not historical market data', value['source'].lower())
            self.assertEqual(value['fills'], [])
            self.assertEqual(list(Path(scratch).iterdir()), [])

    def test_saved_demo_round_trips_through_disposable_records(self):
        real_store = research_commands.AuditStore
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'records.db'
            with patch('grande_alpha.research.sandbox.sandbox_config_path', return_value=Path(scratch) / 'sandbox.json'):
                with patch.object(research_commands, 'AuditStore', side_effect=lambda: real_store(path)):
                    value = self.invoke(['research', 'sandbox', 'run', '--source', 'demo', '--days', '7',
                                         '--strategy', 'cash', '--json'])
            store = real_store(path)
            try:
                saved = store.sandbox_run(value['run_id'])
                self.assertTrue(value['saved'])
                self.assertIsNotNone(saved)
                self.assertEqual(saved['metrics'], value['metrics'])
            finally:
                store.close()


class VerificationRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('grande_offline_checks', Path(__file__).with_name('verify.py'))
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def test_shared_checklist_propagates_failure_and_stops_before_next_suite(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            names = ('first.py', 'failed.py', 'never.py')
            for name in names:
                (root / name).write_text('', encoding='utf8')
            with patch.object(self.runner, 'TOOLS', root), patch.object(self.runner, 'VALIDATORS', names):
                with patch.object(self.runner.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=7)]) as run:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(self.runner.main(), 7)
                    self.assertEqual(run.call_count, 2)
                    self.assertEqual(Path(run.call_args.args[0][-1]).name, 'failed.py')

    def test_missing_validator_cannot_silently_reduce_coverage(self):
        with tempfile.TemporaryDirectory() as scratch:
            with patch.object(self.runner, 'TOOLS', Path(scratch)), patch.object(self.runner, 'VALIDATORS', ('missing.py',)):
                with patch.object(self.runner.subprocess, 'run') as run:
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(self.runner.main(), 2)
                    run.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)

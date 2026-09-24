from __future__ import annotations

import json
from pathlib import Path

import pytest

from grande_alpha.cli import build_parser
from grande_alpha.execution.equity_ledger import EquityLedger
from grande_alpha.execution.process_lock import ProcessLock
from grande_alpha.interfaces.cli.config_cli import command_execution_store_upgrade
from grande_alpha.persistence.store import AuditStore


def _arguments(tmp_path):
    return build_parser().parse_args([
        "records", "upgrade-execution-store",
        "--audit", str(tmp_path / "audit.db"),
        "--legacy-equity", str(tmp_path / "equity.db"),
        "--backup-dir", str(tmp_path / "backups"),
        "--json",
    ])


def test_upgrade_command_requires_explicit_paths(tmp_path):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["records", "upgrade-execution-store"])
    arguments = _arguments(tmp_path)
    assert arguments.func is command_execution_store_upgrade
    with pytest.raises(FileNotFoundError):
        arguments.func(arguments)
    assert not (tmp_path / "audit.db").exists()
    assert not (tmp_path / "equity.db").exists()


def test_upgrade_command_refuses_active_worker_lock(tmp_path, monkeypatch):
    (tmp_path / "audit.db").touch()
    (tmp_path / "equity.db").touch()
    called = []
    monkeypatch.setattr("grande_alpha.interfaces.cli.config_cli.upgrade_execution_store",
                        lambda *_args, **_kwargs: called.append(True))
    lock = ProcessLock(tmp_path / "app.lock")
    assert lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="Stop the local worker"):
            command_execution_store_upgrade(_arguments(tmp_path))
    finally:
        lock.release()
    assert called == []
    assert not (tmp_path / "backups").exists()


def test_upgrade_command_invokes_offline_api_and_reports_backups(tmp_path, capsys):
    audit = AuditStore(tmp_path / "audit.db")
    audit.close()
    legacy = EquityLedger(tmp_path / "equity.db")
    legacy.close()
    assert command_execution_store_upgrade(_arguments(tmp_path)) == 0
    report = json.loads(capsys.readouterr().out)
    assert (tmp_path / "backups").exists()
    assert Path(report["audit_backup"]).is_file()
    assert Path(report["equity_backup"]).is_file()
    assert report["schema_version"] == 1

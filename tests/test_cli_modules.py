from __future__ import annotations

from argparse import Namespace

import pytest

from grande_alpha.app import main as desktop_main
from grande_alpha.cli import build_parser, command_session_run
from grande_alpha.interfaces.cli.cli_table import format_table
from grande_alpha.interfaces.cli.config_cli import (
    command_config_import_legacy,
    command_config_show,
    command_config_upgrade,
)
from grande_alpha.interfaces.cli.worker_cli import command_research_mcp


def test_table_renderer_wraps_content_and_normalizes_cells() -> None:
    rendered = format_table(["Setting", "Value"], [["mode", "shadow\nonly"]], width=54)
    assert "shadow only" in rendered
    assert "Setting" in rendered
    assert format_table([], []) == ""


def test_config_commands_remain_registered_after_extraction() -> None:
    parser = build_parser()
    assert parser.parse_args(["config", "show"]).func is command_config_show
    assert parser.parse_args(["config", "upgrade"]).func is command_config_upgrade
    assert (
        parser.parse_args(["config", "import-legacy", "--source", "C:/old-data"]).func
        is command_config_import_legacy
    )


def test_config_show_does_not_create_missing_file(tmp_path, capsys) -> None:
    path = tmp_path / "not-created.json"
    result = command_config_show(Namespace(path=path, json=True, width=None))
    output = capsys.readouterr().out
    assert result == 0
    assert '"settings"' in output
    assert not path.exists()


def test_public_cli_has_six_groups_and_one_session_runner(monkeypatch) -> None:
    parser = build_parser()
    for group in ("config", "broker", "data", "research", "session", "records"):
        assert group in parser.format_help()
    with pytest.raises(SystemExit):
        parser.parse_args(["engine", "run-autonomous"])
    args = parser.parse_args(["session", "run", "--candidate", "candidate.json",
                              "--authorization", "permit.json", "--earnings-database", "earnings.db"])
    assert args.func is command_session_run
    called = []
    monkeypatch.setattr("grande_alpha.interfaces.cli.worker_cli.command_worker_run",
                        lambda received: called.append(received) or 0)
    assert command_session_run(args) == 0
    assert called == [args]
    with pytest.raises(SystemExit):
        parser.parse_args(["session", "run", "--mode", "autonomous"])
    with pytest.raises(SystemExit):
        parser.parse_args(["session", "run", "--mode", "shadow", "--strategy", "etf"])


def test_research_mcp_stays_in_research_command_group() -> None:
    parser = build_parser()
    assert parser.parse_args(["research", "mcp", "enable"]).func is command_research_mcp
    assert parser.parse_args(["research", "mcp", "disable"]).func is command_research_mcp


def test_windowed_version_check_needs_no_console_or_desktop_import(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["GRANDEAlpha.exe", "--version"])
    monkeypatch.setattr("sys.stdout", None)
    assert desktop_main() == 0

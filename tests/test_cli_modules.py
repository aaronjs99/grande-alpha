from __future__ import annotations

from argparse import Namespace

from grande_alpha.cli import build_parser
from grande_alpha.cli_table import format_table
from grande_alpha.config_cli import (
    command_config_import_legacy,
    command_config_show,
    command_config_upgrade,
)


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

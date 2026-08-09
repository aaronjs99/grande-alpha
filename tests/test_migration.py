from pathlib import Path

from grande_alpha.config import migrate_legacy_data


def test_legacy_data_is_copied_without_deleting_originals(tmp_path: Path) -> None:
    legacy = tmp_path / "MomentumTrader"
    destination = tmp_path / "GRANDEAlpha"
    legacy.mkdir()
    (legacy / "config.json").write_text('{"warmup_bars": 30}', encoding="utf-8")
    (legacy / "momentum_trader.db").write_bytes(b"legacy-database")
    (legacy / "momentum_trader.log").write_text("legacy log", encoding="utf-8")

    copied = migrate_legacy_data(legacy, destination)

    assert {path.name for path in copied} == {
        "config.json",
        "grande_alpha.db",
        "legacy_momentum_trader.log",
    }
    assert (destination / "grande_alpha.db").read_bytes() == b"legacy-database"
    assert (legacy / "momentum_trader.db").exists()
    assert migrate_legacy_data(legacy, destination) == []


def test_source_has_no_legacy_python_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "from momentum_trader" not in source
    assert "import momentum_trader" not in source

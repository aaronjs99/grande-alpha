from pathlib import Path

from grande_alpha.config import migrate_config_payload, migrate_legacy_data


def test_pre_cadence_config_is_upgraded_to_retail_low_latency_defaults() -> None:
    upgraded = migrate_config_payload({"poll_seconds": 2.0, "bar_seconds": 60})
    assert upgraded["cadence_version"] == 1
    assert upgraded["poll_seconds"] == 1.0
    assert upgraded["reconcile_seconds"] == 5.0
    assert upgraded["bar_seconds"] == 5


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

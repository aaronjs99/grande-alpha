from pathlib import Path

import pytest

from grande_alpha.config import AppConfig, migrate_config_payload, migrate_legacy_data


def test_pre_cadence_config_is_upgraded_to_retail_low_latency_defaults() -> None:
    upgraded = migrate_config_payload({"poll_seconds": 2.0, "bar_seconds": 60})
    assert upgraded["cadence_version"] == 5
    assert upgraded["poll_seconds"] == 1.0
    assert upgraded["reconcile_seconds"] == 5.0
    assert upgraded["bar_seconds"] == 5
    assert upgraded["trade_every_bars"] == 3
    assert upgraded["market_hours"] == "regular_hours"
    assert upgraded["order_type"] == "market"
    assert upgraded["time_in_force"] == "gfd"
    assert upgraded["limit_offset_bps"] == 10.0
    assert upgraded["settlement_model"] == "cash_t1"
    assert upgraded["strategy_name"] == "cash"


def test_v1_cadence_preserves_existing_clocks_and_adds_trade_stride() -> None:
    upgraded = migrate_config_payload(
        {
            "cadence_version": 1,
            "poll_seconds": 0.5,
            "reconcile_seconds": 10.0,
            "bar_seconds": 2,
        }
    )
    assert upgraded["poll_seconds"] == 0.5
    assert upgraded["reconcile_seconds"] == 10.0
    assert upgraded["bar_seconds"] == 2
    assert upgraded["trade_every_bars"] == 3
    assert upgraded["cadence_version"] == 5
    assert upgraded["settlement_model"] == "cash_t1"
    assert upgraded["strategy_name"] == "cash"


def test_v4_config_without_runtime_strategy_migrates_to_cash() -> None:
    upgraded = migrate_config_payload({"cadence_version": 4, "fast_ema": 5})

    assert upgraded["cadence_version"] == 5
    assert upgraded["strategy_name"] == "cash"
    assert upgraded["fast_ema"] == 5


def test_runtime_strategy_name_must_be_supported() -> None:
    with pytest.raises(ValueError, match="Unknown runtime strategy"):
        AppConfig(strategy_name="unregistered").validate_cadence()


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

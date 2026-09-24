from __future__ import annotations

import json
from dataclasses import fields

import pytest

from grande_alpha.configuration import config as config_module
from grande_alpha.configuration.config import (
    CONFIG_SECTIONS,
    AppConfig,
    BrokerConfig,
    ConfigUpgradeRequired,
    DataConfig,
    DesktopConfig,
    ExecutionConfig,
    RiskConfig,
    StorageConfig,
    StrategyConfig,
    config_document,
    flat_config_values,
    load_config,
    save_config,
    upgrade_config,
)


def test_every_setting_has_one_saved_section_and_round_trips(tmp_path):
    names = [name for section in CONFIG_SECTIONS.values() for name in section]
    assert len(names) == len(set(names))
    assert set(names) == set(flat_config_values(AppConfig())) - {"cadence_version"}
    assert {item.name for item in fields(AppConfig)} == {"cadence_version", *CONFIG_SECTIONS}

    path = tmp_path / "config.json"
    config = AppConfig(broker_connection_enabled=True, live_trading_enabled=True,
                       default_max_daily_loss=7.5)
    save_config(config, path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document) == {"schema_version", *CONFIG_SECTIONS}
    assert document["risk"]["default_max_daily_loss"] == 7.5
    assert document["broker"]["live_trading_enabled"] is True
    assert load_config(path) == config
    assert config_document(config) == document


@pytest.mark.parametrize("location", ["section", "setting"])
def test_unknown_configuration_fields_are_rejected(tmp_path, location):
    path = tmp_path / "config.json"
    document = config_document(AppConfig())
    if location == "section":
        document["surprise"] = {}
    else:
        document["risk"]["surprise"] = 1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown"):
        load_config(path)


def test_old_flat_configuration_needs_explicit_backed_up_upgrade(tmp_path):
    path = tmp_path / "config.json"
    original = flat_config_values(AppConfig(broker_connection_enabled=True, live_trading_enabled=True))
    path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ConfigUpgradeRequired):
        load_config(path)
    with pytest.raises(ConfigUpgradeRequired):
        save_config(AppConfig(), path)
    assert not list(tmp_path.glob("*.backup.json"))

    backup = upgrade_config(path)
    assert backup is not None and json.loads(backup.read_text(encoding="utf-8")) == original
    assert load_config(path).live_trading_enabled is True
    assert upgrade_config(path) is None


def test_interrupted_upgrade_preserves_original_and_backup(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    original = json.dumps(flat_config_values(AppConfig()))
    path.write_text(original, encoding="utf-8")

    def interrupt(_config, _path, **_kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr(config_module, "save_config", interrupt)
    with pytest.raises(OSError, match="interruption"):
        upgrade_config(path)
    assert path.read_text(encoding="utf-8") == original
    assert len(list(tmp_path.glob("*.backup.json"))) == 1


def test_missing_configuration_lookup_is_read_only(tmp_path):
    path = tmp_path / "nonexistent" / "config.json"
    assert load_config(path) == AppConfig()
    assert not path.parent.exists()


def test_typed_sections_are_authoritative_and_compatibility_updates_are_independent():
    config = AppConfig()
    assert isinstance(config.broker, BrokerConfig)
    assert isinstance(config.data, DataConfig)
    assert isinstance(config.strategy, StrategyConfig)
    assert isinstance(config.execution, ExecutionConfig)
    assert isinstance(config.risk, RiskConfig)
    assert isinstance(config.storage, StorageConfig)
    assert isinstance(config.desktop, DesktopConfig)
    config.risk.default_max_daily_loss = 3.5
    assert config.default_max_daily_loss == 3.5
    updated = config.with_flat_updates(default_max_daily_loss=4.5)
    assert updated.risk.default_max_daily_loss == 4.5
    assert config.risk.default_max_daily_loss == 3.5


def test_invalid_grouped_field_error_omits_value(tmp_path):
    path = tmp_path / "config.json"
    document = config_document(AppConfig())
    document["risk"]["default_max_daily_loss"] = "secret-value"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="default_max_daily_loss") as error:
        load_config(path)
    assert "secret-value" not in str(error.value)


@pytest.mark.parametrize(
    ("setting", "value"),
    [("live_trading_enabled", "yes"), ("default_max_daily_loss", -1),
     ("default_max_order_notional", float("nan")), ("default_max_trades", 1.5),
     ("poll_seconds", True), ("no_trade_open_minutes", -1)],
)
def test_invalid_settings_fail_before_save(tmp_path, setting, value):
    config = AppConfig()
    setattr(config, setting, value)
    with pytest.raises(ValueError, match=setting):
        save_config(config, tmp_path / "config.json")
    assert not (tmp_path / "config.json").exists()

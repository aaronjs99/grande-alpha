from grande_alpha.configuration.config import AppConfig
from grande_alpha.configuration.privacy import public_config


def test_public_config_reports_allowed_typed_settings_without_private_data() -> None:
    config = AppConfig(poll_seconds=3.0, live_trading_enabled=True)
    shown = public_config(config)
    assert shown["poll_seconds"] == 3.0
    assert shown["live_trading_enabled"] is True
    assert "broker" not in shown
    assert "authorization" not in shown

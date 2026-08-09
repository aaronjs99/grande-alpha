import json

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from grande_alpha import __version__
from grande_alpha.broker.base import Broker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.privacy import export_diagnostics
from grande_alpha.storage import AuditStore
from grande_alpha.ui.settings_dialog import LIVE_PHRASE, SettingsDialog


class DisabledBroker(Broker):
    async def connect(self):
        raise AssertionError("Disabled broker must not be called")

    async def disconnect(self):
        return None

    async def get_accounts(self):
        return []

    async def get_portfolio(self, account_number):
        raise AssertionError

    async def get_quotes(self, symbols):
        raise AssertionError

    async def get_positions(self, account_number):
        raise AssertionError

    async def get_orders(self, account_number):
        raise AssertionError

    async def review_order(self, account_number, intent):
        raise AssertionError

    async def place_order(self, account_number, intent):
        raise AssertionError

    async def cancel_order(self, account_number, order_id):
        raise AssertionError


def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_public_defaults_are_research_only() -> None:
    config = AppConfig()
    assert not config.broker_connection_enabled
    assert not config.live_trading_enabled
    assert not config.remote_market_data_enabled
    assert not config.personal_ledger_enabled
    assert __version__ == "0.4.0"


@pytest.mark.asyncio
async def test_disabled_broker_is_blocked_before_adapter_call(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    with pytest.raises(RuntimeError, match="disabled"):
        await controller.connect()
    store.close()


def test_live_setting_requires_broker_and_exact_phrase() -> None:
    qt_app()
    dialog = SettingsDialog(AppConfig())
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    dialog.live.setChecked(True)
    assert not save.isEnabled()
    dialog.broker.setChecked(True)
    assert not save.isEnabled()
    dialog.live_phrase.setText(LIVE_PHRASE)
    assert save.isEnabled()
    updated = dialog.updated_config()
    assert updated.broker_connection_enabled and updated.live_trading_enabled


def test_diagnostic_export_redacts_identifiers(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    secret = "0123456789abcdef0123456789abcdef"
    store.receipt(
        "test",
        f"Account ending 8900 order {secret}",
        {"account_number": "123456789", "token": "secret-token", "safe": "visible"},
    )
    destination = tmp_path / "diagnostics.json"
    export_diagnostics(AppConfig(), store, destination)
    document = json.loads(destination.read_text(encoding="utf-8"))
    encoded = json.dumps(document)
    assert "123456789" not in encoded
    assert "secret-token" not in encoded
    assert secret not in encoded
    assert "visible" in encoded
    store.close()


def test_market_history_pruning_never_deletes_receipts(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    store.receipt("test", "retain this audit record")
    store.prune_market_history(90)
    assert store.recent_receipts(1)[0]["summary"] == "retain this audit record"
    store.close()

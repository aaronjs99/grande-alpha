from __future__ import annotations

from dataclasses import asdict

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from fake_broker import DisabledBroker
from grande_alpha.configuration.config import AppConfig
from grande_alpha.desktop.controller import TradingController
from grande_alpha.persistence.store import AuditStore
from grande_alpha.ui import main_window, themes
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.settings_dialog import SettingsDialog


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'appearance.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(themes, 'appearance_settings', lambda: settings)
    monkeypatch.setattr(main_window, 'appearance_settings', lambda: settings)
    store = AuditStore(tmp_path / 'theme.db')
    config = AppConfig()
    controller = TradingController(DisabledBroker(), config, store)
    window = MainWindow(controller, config)
    yield app, settings, window, controller
    window.close()
    store.close()
    themes.apply_application_theme('light')


def test_theme_persists_and_updates_whole_window_without_trading_changes(setup):
    app, settings, window, controller = setup
    original = asdict(controller.config)
    window.chart_curve.setData([1, 2], [10, 11])
    for dark in (True, False, True):
        window.theme_button.click()
        app.processEvents()
        expected = 'dark' if dark else 'light'
        assert themes.current_theme() == expected
        assert settings.value('theme') == expected
        assert window.theme_action.isChecked() is dark
        assert ('#0b1118' if dark else '#f1f3f5') in app.styleSheet()
        assert window.chart.backgroundBrush().color().name() == ('#0e1720' if dark else '#ffffff')
        assert window.sandbox_widget.chart.backgroundBrush().color().name() == ('#0e1720' if dark else '#ffffff')
        assert window.agent_widget.chart.backgroundBrush().color().name() == ('#14212c' if dark else '#ffffff')
        assert window.chart_curve.getData()[1].tolist() == [10, 11]
        assert asdict(controller.config) == original
        assert controller.risk.grant is None
    reopened = MainWindow(controller, controller.config)
    assert reopened.theme_action.isChecked()
    assert themes.current_theme() == 'dark'
    reopened.close()


def test_menu_toggle_recolors_existing_and_future_statuses_and_dialogs(setup):
    app, _, window, _ = setup
    table = window.activity_table
    table.setRowCount(1)
    item = QTableWidgetItem('Risk event')
    themes.set_item_foreground(item, '#ff697d')
    table.setItem(0, 0, item)
    assert item.foreground().color().name() == '#b6314c'
    dialog = SettingsDialog(window.config, live_evidence_ready=False, parent=window)
    assert '#e5f0f8' in dialog.account_scope_status.styleSheet()
    window.theme_action.trigger()
    app.processEvents()
    assert item.foreground().color().name() == '#ff697d'
    assert '#142b3d' in dialog.account_scope_status.styleSheet()
    later = SettingsDialog(window.config, live_evidence_ready=False, parent=window)
    assert '#142b3d' in later.account_scope_status.styleSheet()
    window.theme_action.trigger()
    assert '#e5f0f8' in later.account_scope_status.styleSheet()
    assert item.foreground().color().name() == '#b6314c'
    later.close()
    dialog.close()


def test_invalid_appearance_preference_falls_back_without_changing_config(setup):
    _, settings, _, controller = setup
    settings.setValue('theme', 'unrecognized')
    assert themes.saved_theme() == 'light'
    assert not controller.config.live_trading_enabled

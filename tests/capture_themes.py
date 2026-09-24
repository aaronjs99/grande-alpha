"""Capture application themes with a disabled broker and temporary audit data."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from fake_broker import DisabledBroker
from grande_alpha.configuration.config import AppConfig
from grande_alpha.desktop.controller import TradingController
from grande_alpha.persistence.store import AuditStore
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.settings_dialog import SettingsDialog
from grande_alpha.ui.themes import apply_application_theme


def capture(directory: Path):
    app = QApplication.instance() or QApplication([])
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        store = AuditStore(Path(temporary) / 'themes.db')
        config = AppConfig()
        controller = TradingController(DisabledBroker(), config, store)
        window = MainWindow(controller, config)
        window.resize(1440, 1000)
        window.show()
        for theme in ('light', 'dark'):
            apply_application_theme(theme)
            window._apply_theme_widgets()
            for name, page in (('home', window.welcome_widget), ('sandbox', window.sandbox_widget), ('readiness', window.activation_widget)):
                window.tabs.setCurrentWidget(page)
                app.processEvents()
                assert window.grab().save(str(directory / f'{name}-{theme}.png'))
            dialog = SettingsDialog(config, live_evidence_ready=False, parent=window)
            dialog.resize(1000, 850)
            dialog.show()
            app.processEvents()
            assert dialog.grab().save(str(directory / f'settings-{theme}.png'))
            dialog.close()
        window.close()
        store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    capture(parser.parse_args().output)

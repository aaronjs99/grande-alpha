"""Reproducible screenshots for the responsive desktop UI audit.

This is a manual audit helper, not a test module. For release evidence on Windows, run it with
QT_QPA_PLATFORM=windows so the native font and widget geometry are represented.
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import timedelta
from pathlib import Path

from PySide6.QtWidgets import QApplication

from grande_alpha.broker.base import Broker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController, TradingSnapshot
from grande_alpha.models import (
    Account,
    OrderConfirmationRequest,
    OrderIntent,
    OrderReview,
    Portfolio,
    Quote,
    Regime,
    Signal,
    utc_now,
)
from grande_alpha.storage import AuditStore
from grande_alpha.ui.dialogs import LiveGrantDialog, OrderConfirmationDialog
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.settings_dialog import SettingsDialog


class CaptureBroker(Broker):
    async def connect(self):
        raise AssertionError("Screenshot capture must not use a broker")

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


def _capture(widget, path: Path, app: QApplication) -> None:
    widget.show()
    app.processEvents()
    image = widget.grab()
    if not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    widget.hide()
    app.processEvents()


def _snapshot() -> TradingSnapshot:
    now = utc_now()
    quotes = {
        "QQQ": Quote("QQQ", 721.13, 721.41, 721.20, now, now, now),
        "TQQQ": Quote("TQQQ", 73.84, 73.88, 73.82, now, now, now),
        "SQQQ": Quote("SQQQ", 37.69, 37.75, 37.69, now, now, now),
    }
    return TradingSnapshot(
        connected=True,
        account=Account("0000123456", "Agentic", "cash", True, "active"),
        portfolio=Portfolio(50.0, 50.0, 50.0),
        quotes=quotes,
        signal=Signal(Regime.FLAT, 0.34, "Awaiting confirmed trend"),
        shadow_running=True,
        shadow_equity=49.98,
        shadow_pnl=-0.02,
        shadow_position=None,
        shadow_fills=4,
        last_refresh=now,
    )


def capture_set(output_dir: Path, phase: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    config = AppConfig(broker_connection_enabled=True)
    with tempfile.TemporaryDirectory(prefix="grande-ui-audit-") as temp_dir:
        store = AuditStore(Path(temp_dir) / "audit.db")
        controller = TradingController(CaptureBroker(), config, store)
        window = MainWindow(controller, config)
        snapshot = _snapshot()
        controller.snapshot = snapshot
        window._on_snapshot(snapshot)
        window.timer.stop()
        window.reconcile_timer.stop()

        sizes = ((900, 1200), (1366, 768), (1920, 1080), (1024, 700))
        for index, (width, height) in enumerate(sizes, start=1):
            window.resize(width, height)
            app.processEvents()
            _capture(
                window,
                output_dir / f"{phase}-{index:02d}-main-{width}x{height}.png",
                app,
            )

        window.resize(1366, 768)
        window.tabs.setCurrentWidget(window.sandbox_widget)
        app.processEvents()
        _capture(window, output_dir / f"{phase}-05-sandbox-1366x768.png", app)

        settings = SettingsDialog(config, live_evidence_ready=False)
        settings.resize(840, 680)
        _capture(settings, output_dir / f"{phase}-06-settings-840x680.png", app)

        account = snapshot.account
        portfolio = snapshot.portfolio
        assert account is not None and portfolio is not None
        grant = LiveGrantDialog(
            account,
            portfolio,
            config,
            strategy_fingerprint="a" * 64,
        )
        grant.resize(720, 650)
        _capture(grant, output_dir / f"{phase}-07-session-720x650.png", app)

        now = utc_now()
        intent = OrderIntent(
            ref_id="audit-preview",
            symbol="TQQQ",
            side="buy",
            reason="EMA momentum candidate after risk checks",
            dollar_amount=10.0,
            created_at=now,
        )
        review = OrderReview(
            intent,
            "Market data is informational and execution prices are not guaranteed.",
            {},
            snapshot.quotes["TQQQ"],
            {},
        )
        request = OrderConfirmationRequest(
            account.account_number,
            account.masked,
            intent,
            review,
            "audit-authority",
            "a" * 64,
            now,
            now + timedelta(seconds=30),
        )
        confirmation = OrderConfirmationDialog(request)
        confirmation.resize(1024, 700)
        _capture(confirmation, output_dir / f"{phase}-08-order-confirmation-1024x700.png", app)

        window.resize(900, 1200)
        window.tabs.setCurrentWidget(window.sandbox_widget)
        app.processEvents()
        _capture(window, output_dir / f"{phase}-09-sandbox-portrait-900x1200.png", app)

        window.resize(1024, 700)
        window.tabs.setCurrentWidget(window.activation_widget)
        app.processEvents()
        _capture(window, output_dir / f"{phase}-10-live-readiness-1024x700.png", app)

        window.activation_widget.scroll.ensureWidgetVisible(
            window.activation_widget.external_resources,
            0,
            8,
        )
        app.processEvents()
        _capture(
            window,
            output_dir / f"{phase}-12-live-readiness-resources-1024x700.png",
            app,
        )
        window.activation_widget.scroll.verticalScrollBar().setValue(0)

        window.resize(1066, 1888)
        window.tabs.setCurrentWidget(window.activation_widget)
        app.processEvents()
        _capture(window, output_dir / f"{phase}-11-live-readiness-1066x1888.png", app)

        window._closing_after_cleanup = True
        window.close()
        settings.close()
        grant.close()
        confirmation.close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("responsive-before", "responsive-after"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/images/audit"),
    )
    args = parser.parse_args()
    capture_set(args.output_dir, args.phase)

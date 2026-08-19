from __future__ import annotations

from datetime import timedelta

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication

from grande_alpha.broker.base import Broker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.external_guidance import ExternalGuidanceLink
from grande_alpha.models import (
    Account,
    OrderConfirmationRequest,
    OrderIntent,
    OrderReview,
    Portfolio,
    Quote,
    utc_now,
)
from grande_alpha.storage import AuditStore
from grande_alpha.ui.activation_widget import ActivationChecklistWidget
from grande_alpha.ui.dialogs import LiveGrantDialog, OrderConfirmationDialog
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.settings_dialog import SettingsDialog


class DisabledBroker(Broker):
    async def connect(self):
        raise AssertionError("Responsive UI tests must not use a broker")

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


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dialog_rect(widget, dialog) -> QRect:
    return QRect(widget.mapTo(dialog, QPoint(0, 0)), widget.size())


def _assert_inside(widget, parent) -> None:
    rect = _dialog_rect(widget, parent)
    bounds = parent.rect()
    assert rect.left() >= bounds.left()
    assert rect.top() >= bounds.top()
    assert rect.right() <= bounds.right()
    assert rect.bottom() <= bounds.bottom()


def _assert_above(first, second, parent) -> None:
    first_rect = _dialog_rect(first, parent)
    second_rect = _dialog_rect(second, parent)
    assert first_rect.bottom() < second_rect.top()


def _grid_columns(layout, widgets) -> set[int]:
    columns: set[int] = set()
    for widget in widgets:
        index = layout.indexOf(widget)
        assert index >= 0
        _row, column, _row_span, _column_span = layout.getItemPosition(index)
        columns.add(column)
    return columns


def _quiesce_broker_timers(window: MainWindow) -> None:
    """Keep geometry-only tests from scheduling broker coroutines."""

    window.timer.stop()
    window.reconcile_timer.stop()
    window.timer.timeout.disconnect()
    window.reconcile_timer.timeout.disconnect()


def test_timer_callbacks_require_a_running_async_event_loop(
    tmp_path, qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AuditStore(tmp_path / "timer-loop-boundary.db")
    config = AppConfig(broker_connection_enabled=True)
    controller = TradingController(DisabledBroker(), config, store)
    window = MainWindow(controller, config)
    calls: list[str] = []
    monkeypatch.setattr(controller, "refresh_quotes", lambda: calls.append("quote"))
    monkeypatch.setattr(controller, "reconcile", lambda: calls.append("reconcile"))

    window.timer.timeout.emit()
    window.reconcile_timer.timeout.emit()

    assert calls == []
    window.close()
    store.close()


def test_accepted_window_close_stops_active_broker_timers(
    tmp_path, qt_app: QApplication
) -> None:
    store = AuditStore(tmp_path / "timer-close-boundary.db")
    config = AppConfig(broker_connection_enabled=True)
    controller = TradingController(DisabledBroker(), config, store)
    controller.snapshot.connected = True
    window = MainWindow(controller, config)
    window._on_snapshot(controller.snapshot)
    assert window.timer.isActive()
    assert window.reconcile_timer.isActive()

    window._closing_after_cleanup = True
    window.close()

    assert not window.timer.isActive()
    assert not window.reconcile_timer.isActive()
    store.close()


@pytest.mark.parametrize(
    ("width", "height", "card_columns", "market_orientation"),
    [
        (900, 1200, 2, Qt.Orientation.Vertical),
        (1366, 768, 8, Qt.Orientation.Horizontal),
        (1920, 1080, 8, Qt.Orientation.Horizontal),
        (1024, 700, 4, Qt.Orientation.Horizontal),
    ],
)
def test_main_window_reflows_at_supported_viewports(
    tmp_path,
    qt_app: QApplication,
    width: int,
    height: int,
    card_columns: int,
    market_orientation: Qt.Orientation,
) -> None:
    store = AuditStore(tmp_path / f"responsive-{width}x{height}.db")
    config = AppConfig(broker_connection_enabled=True, live_trading_enabled=True)
    controller = TradingController(DisabledBroker(), config, store)
    window = MainWindow(controller, config)
    _quiesce_broker_timers(window)
    window.resize(width, height)
    window.show()
    qt_app.processEvents()

    assert window.size().width() == width
    assert window.size().height() == height
    assert window.market_splitter.orientation() == market_orientation
    assert len(_grid_columns(window.cards_layout, window.metric_cards)) == card_columns
    assert window.workspace_splitter.count() == 2
    assert not window.workspace_splitter.childrenCollapsible()

    for card in window.metric_cards:
        assert card.value.isVisible()
        assert card.value.height() >= card.value.fontMetrics().lineSpacing()
        _assert_inside(card.value, card)

    window.quotes_table.setRowCount(3)
    qt_app.processEvents()
    last_quote_bottom = (
        window.quotes_table.rowViewportPosition(2) + window.quotes_table.rowHeight(2)
    )
    assert last_quote_bottom <= window.quotes_table.viewport().height()

    visible_actions = [button for button in window.header_actions if button.isVisible()]
    assert visible_actions
    action_rects = []
    for button in visible_actions:
        _assert_inside(button, window)
        action_rects.append(_dialog_rect(button, window))
    for index, first in enumerate(action_rects):
        assert all(not first.intersects(second) for second in action_rects[index + 1 :])

    _assert_inside(window.tabs, window)
    _assert_inside(window.status, window)
    window._closing_after_cleanup = True
    window.close()
    store.close()


def test_getting_started_scroll_keeps_every_action_reachable_at_1024x700(
    tmp_path, qt_app: QApplication
) -> None:
    store = AuditStore(tmp_path / "getting-started-scroll.db")
    config = AppConfig(broker_connection_enabled=True)
    controller = TradingController(DisabledBroker(), config, store)
    window = MainWindow(controller, config)
    _quiesce_broker_timers(window)
    window.resize(1024, 700)
    window.tabs.setCurrentWidget(window.welcome_widget)
    window.show()
    qt_app.processEvents()

    welcome = window.welcome_widget
    assert welcome.scroll.verticalScrollBar().maximum() > 0
    for widget in (
        welcome.activation_button,
        welcome.sandbox_button,
        welcome.settings_button,
        welcome.monitor_text,
        welcome.disclosure,
    ):
        welcome.scroll.ensureWidgetVisible(widget, 0, 8)
        qt_app.processEvents()
        _assert_inside(widget, welcome.scroll.viewport())

    window._closing_after_cleanup = True
    window.close()
    store.close()


def test_live_readiness_resources_render_safe_clickable_rich_text(
    qt_app: QApplication,
) -> None:
    widget = ActivationChecklistWidget(
        external_resources=(
            ExternalGuidanceLink(
                "Broker & qualified <review>",
                "https://example.test/guidance?a=1&b=2",
            ),
        )
    )
    widget.show()
    qt_app.processEvents()

    label = widget.external_resources
    assert label.textFormat() == Qt.TextFormat.RichText
    assert label.openExternalLinks()
    assert "<br>" in label.text()
    assert "&lt;review&gt;" in label.text()
    assert 'href="https://example.test/guidance?a=1&amp;b=2"' in label.text()
    document = QTextDocument()
    document.setHtml(label.text())
    assert "Broker & qualified <review>" in document.toPlainText()
    assert "<a href" not in document.toPlainText()
    widget.close()


def test_research_sandbox_switches_between_portrait_and_landscape(
    tmp_path, qt_app: QApplication
) -> None:
    store = AuditStore(tmp_path / "sandbox-responsive.db")
    config = AppConfig()
    controller = TradingController(DisabledBroker(), config, store)
    window = MainWindow(controller, config)
    _quiesce_broker_timers(window)
    sandbox = window.sandbox_widget

    window.resize(900, 1200)
    window.tabs.setCurrentWidget(sandbox)
    window.show()
    qt_app.processEvents()
    sandbox.apply_responsive_layout(900, 1200)
    assert sandbox.main_splitter.orientation() == Qt.Orientation.Vertical
    assert sandbox.fill_splitter.count() == 2

    window.resize(1366, 768)
    qt_app.processEvents()
    sandbox.apply_responsive_layout(1366, 768)
    assert sandbox.main_splitter.orientation() == Qt.Orientation.Horizontal
    assert sandbox.config_scroll.minimumWidth() <= 360
    assert len(_grid_columns(sandbox.metrics_layout, sandbox.metric_cards)) >= 4

    window._closing_after_cleanup = True
    window.close()
    store.close()


def test_settings_keeps_save_controls_visible_when_constrained(qt_app: QApplication) -> None:
    dialog = SettingsDialog(AppConfig(), live_evidence_ready=False)
    dialog.resize(640, 520)
    dialog.show()
    qt_app.processEvents()

    assert dialog.size().width() == 640
    assert dialog.size().height() == 520
    _assert_inside(dialog.buttons, dialog)
    _assert_inside(dialog.scroll, dialog)
    save = dialog.buttons.button(dialog.buttons.StandardButton.Save)
    cancel = dialog.buttons.button(dialog.buttons.StandardButton.Cancel)
    assert save is not None and cancel is not None
    assert save.isVisible() and cancel.isVisible()
    dialog.close()


def _confirmation_request() -> OrderConfirmationRequest:
    now = utc_now()
    intent = OrderIntent(
        ref_id="responsive-preview",
        symbol="TQQQ",
        side="buy",
        reason="Responsive supervised preview",
        dollar_amount=10.0,
        created_at=now,
    )
    quote = Quote("TQQQ", 73.84, 73.88, 73.86, now, now, now)
    review = OrderReview(
        intent,
        "Market data is informational and execution prices are not guaranteed.",
        {},
        quote,
        {},
    )
    return OrderConfirmationRequest(
        "0000123456",
        "••••3456",
        intent,
        review,
        "responsive-authority",
        "a" * 64,
        now,
        now + timedelta(seconds=30),
    )


def test_supervised_confirmation_stays_actionable_at_1024x700(
    qt_app: QApplication,
) -> None:
    dialog = OrderConfirmationDialog(_confirmation_request())
    dialog.resize(1024, 700)
    dialog.show()
    qt_app.processEvents()

    assert dialog.size().width() == 1024
    assert dialog.size().height() == 700
    for widget in (dialog.confirmation, dialog.buttons, dialog.cancel_button, dialog.confirm_button):
        assert widget.isVisible()
        _assert_inside(widget, dialog)
    assert dialog.review_scroll.height() > 0
    assert dialog.cancel_button.isDefault()
    assert not dialog.confirm_button.isEnabled()
    dialog.close()


def test_supervised_session_dialog_scrolls_without_hiding_confirmation(
    qt_app: QApplication,
) -> None:
    account = Account("0000123456", "Agentic", "cash", True, "active")
    dialog = LiveGrantDialog(
        account,
        Portfolio(50.0, 50.0, 50.0),
        AppConfig(),
        strategy_fingerprint="a" * 64,
    )
    dialog.resize(620, 520)
    dialog.show()
    qt_app.processEvents()

    for widget in (dialog.scroll, dialog.attest, dialog.confirmation, dialog.buttons):
        assert widget.isVisible()
        _assert_inside(widget, dialog)
    assert dialog.scroll.verticalScrollBar().maximum() > 0
    dialog.close()


def test_live_readiness_table_is_content_bounded_on_tall_portrait(
    tmp_path, qt_app: QApplication
) -> None:
    store = AuditStore(tmp_path / "readiness-tall-portrait.db")
    config = AppConfig()
    controller = TradingController(DisabledBroker(), config, store)
    window = MainWindow(controller, config)
    _quiesce_broker_timers(window)
    window.resize(1066, 1888)
    window.tabs.setCurrentWidget(window.activation_widget)
    window.activation_widget.update_rows(controller.live_readiness())
    window.show()
    qt_app.processEvents()

    assert window.activation_widget.table.height() <= 520
    assert window.activation_widget.table.maximumHeight() <= 520
    last_row = window.activation_widget.table.rowCount() - 1
    assert last_row >= 0
    last_row_bottom = (
        window.activation_widget.table.rowViewportPosition(last_row)
        + window.activation_widget.table.rowHeight(last_row)
    )
    assert last_row_bottom <= window.activation_widget.table.viewport().height()
    assert window.activation_widget.table.verticalScrollBar().maximum() == 0
    assert window.activation_widget.detail.isVisible()
    assert window.activation_widget.external_resources.isVisible()
    _assert_inside(window.activation_widget.detail, window)
    _assert_inside(window.activation_widget.external_resources, window)
    _assert_above(
        window.activation_widget.table,
        window.activation_widget.detail,
        window.activation_widget,
    )
    _assert_above(
        window.activation_widget.detail,
        window.activation_widget.external_resources,
        window.activation_widget,
    )

    window.resize(1024, 700)
    qt_app.processEvents()
    assert window.activation_widget.scroll.verticalScrollBar().maximum() > 0
    assert window.activation_widget.table.height() < window.activation_widget.table.maximumHeight()
    assert window.activation_widget.table.verticalScrollBar().maximum() > 0
    assert "Next:" in window.activation_widget.detail.text()
    assert "Resources configured for this distribution:" in (
        window.activation_widget.external_resources.text()
    )
    assert window.activation_widget.detail.height() >= (
        window.activation_widget.detail.heightForWidth(window.activation_widget.detail.width())
    )
    assert window.activation_widget.external_resources.height() >= (
        window.activation_widget.external_resources.heightForWidth(
            window.activation_widget.external_resources.width()
        )
    )
    assert window.activation_widget.summary.text().endswith(
        "Neither path guarantees profit or grants standing authority."
    )
    assert window.activation_widget._header_mode == "compact"
    assert window.activation_widget.summary.parentWidget() is window.activation_widget.header
    assert window.activation_widget.summary.width() >= window.activation_widget.header.width() - 2
    _assert_inside(window.activation_widget.summary, window.activation_widget.header)
    window.activation_widget.scroll.ensureWidgetVisible(window.activation_widget.summary, 0, 4)
    qt_app.processEvents()
    _assert_inside(window.activation_widget.summary, window.activation_widget.scroll.viewport())
    for label in (
        window.activation_widget.summary,
        window.activation_widget.mode_notice,
        window.activation_widget.ownership_label,
    ):
        assert label.height() >= label.heightForWidth(label.width())
    _assert_above(
        window.activation_widget.table,
        window.activation_widget.detail,
        window.activation_widget,
    )
    _assert_above(
        window.activation_widget.detail,
        window.activation_widget.external_resources,
        window.activation_widget,
    )
    _assert_inside(window.activation_widget.table.horizontalScrollBar(), window.activation_widget.table)
    for widget in (
        window.activation_widget.detail,
        window.activation_widget.external_resources,
    ):
        window.activation_widget.scroll.ensureWidgetVisible(widget, 0, 4)
        qt_app.processEvents()
        _assert_inside(widget, window.activation_widget.scroll.viewport())

    window._closing_after_cleanup = True
    window.close()
    store.close()

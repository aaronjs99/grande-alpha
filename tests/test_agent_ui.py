from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication
from test_responsive_ui import DisabledBroker

from grande_alpha.agent_models import AgentSettings, AgentSnapshot
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController, TradingSnapshot
from grande_alpha.models import Account, Portfolio, utc_now
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_agent_page_is_available_without_grant_and_shows_only_broker_account_value(tmp_path, app):
    store = AuditStore(tmp_path / "agent-ui.db")
    controller = TradingController(DisabledBroker(), AppConfig(broker_connection_enabled=True), store)
    window = MainWindow(controller, controller.config)
    widget = window.agent_widget
    window.tabs.setCurrentWidget(widget)
    window.show()
    app.processEvents()
    assert not window.broker_panel.isVisible()
    assert not widget.start.isEnabled()
    assert widget.balance.text() == "—"
    account = Account("mock-account", "Agentic", "cash", True, "active")
    snapshot = TradingSnapshot(
        connected=True, account=account, portfolio=Portfolio(10.18, 10.18, 10.18), last_reconcile_at=utc_now()
    )
    widget.update_account(snapshot)
    assert widget.start.isEnabled()
    assert widget.balance.text() == "$10.18"
    assert len(widget._balances) == 1
    widget.update_account(snapshot)
    assert len(widget._balances) == 1
    widget.update_account(replace(snapshot, account=replace(account, account_number="other")))
    assert len(widget._balances) == 1
    widget.update_account(TradingSnapshot())
    assert len(widget._balances) == 0
    assert widget.balance.text() == "—"
    assert "PROPOSALS ONLY" in widget.mode.text()
    assert "not available" in widget.execution_status.text()
    window.close()
    store.close()


def test_agent_readiness_never_inherits_an_etf_live_status(tmp_path, app):
    store = AuditStore(tmp_path / "agent-boundary.db")
    controller = TradingController(
        DisabledBroker(), AppConfig(broker_connection_enabled=True, live_trading_enabled=True), store
    )
    window = MainWindow(controller, controller.config)
    window.agent_widget.update_account(TradingSnapshot(connected=True, live_status="LIVE"))
    window.agent_widget.update_agent(AgentSnapshot(running=True, cycle=3))
    assert "not available" in window.agent_widget.execution_status.text()
    assert controller.risk.grant is None
    controller.shadow_only_runtime = True
    with pytest.raises(RuntimeError, match="Scheduled"):
        controller.start_agent(AgentSettings())
    window.close()
    store.close()

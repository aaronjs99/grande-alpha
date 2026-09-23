from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication

from fake_broker import DisabledBroker
from grande_alpha.agent_ledger import AgentBudget
from grande_alpha.agent_models import AgentSnapshot
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
    assert "unavailable" in widget.crypto_funds.text()
    widget.update_account(replace(snapshot, portfolio=replace(snapshot.portfolio, crypto_buying_power=3.25)))
    assert "$3.25" in widget.crypto_funds.text()
    assert widget.buying_power.text() == "$10.18"
    assert len(widget._balances) == 1
    widget.update_account(snapshot)
    assert len(widget._balances) == 1
    widget.update_account(replace(snapshot, account=replace(account, account_number="other")))
    assert len(widget._balances) == 1
    widget.update_account(TradingSnapshot())
    assert len(widget._balances) == 0
    assert widget.balance.text() == "—"
    assert "unavailable" in widget.crypto_funds.text()
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
    window.close()
    store.close()


@pytest.mark.asyncio
async def test_agent_crypto_quotes_use_selected_rhs_account_only(tmp_path, app):
    class ScopedBroker(DisabledBroker):
        async def get_crypto_quotes(self, instruments, *, rhs_account_number=""):
            assert rhs_account_number == "12345678"
            return {}

    store = AuditStore(tmp_path / "agent-scope.db")
    controller = TradingController(ScopedBroker(), AppConfig(broker_connection_enabled=True), store)
    controller.snapshot.account = Account("SYNTHETIC", "Agentic", "cash", True, "active", "12345678", "RHC-FIXTURE")
    assert await controller._agent_crypto_quotes([]) == {}
    controller.snapshot.account = replace(controller.snapshot.account, rhc_account_number="")
    with pytest.raises(RuntimeError, match="linked crypto"):
        await controller._agent_crypto_quotes([])
    store.close()


def test_saved_cash_limits_reload_without_creating_live_authority(tmp_path, app):
    path = tmp_path / "persistent-budget.db"
    account = Account("fixture-budget", "Agentic", "cash", True, "active")
    store = AuditStore(path)
    controller = TradingController(DisabledBroker(), AppConfig(broker_connection_enabled=True), store)
    controller.snapshot.connected = True
    controller.snapshot.account = account
    window = MainWindow(controller, controller.config)
    widget = window.agent_widget
    widget.update_account(controller.snapshot)
    for name, value in (("max_order_cash", 5), ("max_committed_cash", 10), ("max_daily_buy_cash", 12), ("max_realized_loss", 1)):
        widget.budget_inputs[name].setValue(value)
    widget._save_budget()
    assert controller.risk.grant is None
    assert controller.agent_executor._authorize(None) is False
    assert controller.store.agent_ledger.status(account.account_number).limits.max_committed_cash == Decimal(10)
    assert controller.store.agent_ledger.records(account.account_number) == []
    window._closing_after_cleanup = True
    window.close()
    store.close()

    reopened = AuditStore(path)
    other = TradingController(DisabledBroker(), AppConfig(broker_connection_enabled=True), reopened)
    other.snapshot.connected = True
    other.snapshot.account = account
    new_window = MainWindow(other, other.config)
    widget = new_window.agent_widget
    # Quotes can update the UI before the slower account/budget reconciliation.
    widget.update_account(other.snapshot)
    other.snapshot.agent_budget = other.agent_executor.status_payload(account.account_number)
    widget.update_account(other.snapshot)
    assert widget.budget_inputs["max_committed_cash"].value() == 10
    assert "0 pending" in widget.journal_status.text()
    assert "PROPOSALS ONLY" in widget.mode.text()
    other.snapshot.account = replace(account, account_number="different-account")
    widget.update_account(other.snapshot)
    assert widget.budget_inputs["max_committed_cash"].value() == 0
    new_window._closing_after_cleanup = True
    new_window.close()
    reopened.close()


@pytest.mark.asyncio
async def test_controller_recovery_status_and_etf_boundary_are_connected(tmp_path, app):
    from test_agent_ledger import NOW, dispatch, ticket

    store = AuditStore(tmp_path / "recovery-ui.db")
    controller = TradingController(DisabledBroker(), AppConfig(broker_connection_enabled=True), store)
    controller.snapshot.connected = True
    controller.snapshot.account = Account("fixture", "Agentic", "cash", True, "active")
    controller.save_agent_budget(AgentBudget(Decimal(6), Decimal(10), Decimal(12), Decimal(1)))
    dispatch(store.agent_ledger, ticket(), NOW)
    with pytest.raises(RuntimeError, match="Managed stock/crypto commitments"):
        controller._assert_agent_route_clear()
    await controller._recover_agent_execution()
    assert controller.snapshot.agent_recovery_status.startswith("Recovery blocked:")
    assert controller.snapshot.agent_budget["unresolved_orders"] == 1
    assert controller.risk.grant is None
    store.close()


@pytest.mark.parametrize('width,height', [(1600, 1200), (900, 1100)])
def test_dashboard_reflows_and_saved_limit_controls_remain_reachable(tmp_path, app, width, height):
    from PySide6.QtCore import QPoint

    store = AuditStore(tmp_path / 'dashboard-layout.db')
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    window = MainWindow(controller, controller.config)
    widget = window.agent_widget
    window.resize(width, height)
    window.tabs.setCurrentWidget(widget)
    window.show()
    app.processEvents()
    assert widget.horizontalScrollBar().maximum() == 0
    assert len(widget.stage_cards) == 6
    if width >= 1100:
        assert widget.stage_cards[0].y() == widget.stage_cards[-1].y()
        assert widget.chart.mapTo(widget.widget(), QPoint()).y() < widget.activity.mapTo(widget.widget(), QPoint()).y() + 100
    else:
        assert widget.stage_cards[-1].y() > widget.stage_cards[0].y()
        assert widget.activity.mapTo(widget.widget(), QPoint()).y() > widget.chart.mapTo(widget.widget(), QPoint()).y()
    widget.budget_toggle.setChecked(True)
    app.processEvents()
    widget.ensureWidgetVisible(widget.save_budget)
    app.processEvents()
    position = widget.save_budget.mapTo(widget.viewport(), widget.save_budget.rect().center())
    assert widget.viewport().rect().contains(position)
    assert not widget.save_budget.isEnabled()
    window.close()
    store.close()

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication
from test_responsive_ui import DisabledBroker

from grande_alpha.agent_ledger import AgentBudget
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
    controller.shadow_only_runtime = True
    with pytest.raises(RuntimeError, match="Scheduled"):
        controller.start_agent(AgentSettings())
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
    controller.shadow_only_runtime = True
    with pytest.raises(RuntimeError, match="Scheduled"):
        controller.save_agent_budget(AgentBudget())
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


@pytest.mark.parametrize('timestamp,expected,date', [
    ('2026-09-24T04:30:12+00:00', '09:30:12 PM PDT', '2026-09-23'),
    ('2026-01-24T04:30:12+00:00', '08:30:12 PM PST', '2026-01-23'),
    ('2026-11-01T08:30:00+00:00', '01:30:00 AM PDT', '2026-11-01'),
    ('2026-11-01T09:30:00+00:00', '01:30:00 AM PST', '2026-11-01'),
])
def test_activity_uses_pacific_offset_and_date_without_changing_event_time(tmp_path, app, timestamp, expected, date):
    store = AuditStore(tmp_path / 'pacific.db')
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    window = MainWindow(controller, controller.config)
    widget = window.agent_widget
    event = {'id': 1, 'from': 'NOVA', 'to': 'VELA', 'message': 'Fixture quote',
             'kind': 'SCAN', 'cycle': 1, 'at': timestamp}
    widget.update_agent(AgentSnapshot(session_id='test', team_events=(event,)))
    assert widget.activity.horizontalHeaderItem(0).text() == 'Time (Pacific)'
    assert widget.activity.item(0, 0).text() == expected
    assert date in widget.activity.item(0, 0).toolTip()
    assert 'Pacific time' in widget.activity_hint.text()
    assert event['at'] == timestamp
    assert datetime.fromisoformat(event['at']).utcoffset() == timedelta(0)
    window.close()
    store.close()


def test_top_status_explains_start_failure_waiting_and_missing_news(tmp_path, app, monkeypatch):
    from test_agent_paper import decision
    from test_agent_runtime import NOW

    store = AuditStore(tmp_path / 'status.db')
    controller = TradingController(DisabledBroker(), AppConfig(broker_connection_enabled=True), store)
    window = MainWindow(controller, controller.config)
    widget = window.agent_widget
    widget.paper_source.setCurrentIndex(1)
    assert not widget.paper_start.isEnabled()
    assert 'Connect Robinhood' in widget.run_status.text()
    widget._start_paper()
    assert 'did not start' in widget.run_status.text()
    monkeypatch.setattr('grande_alpha.ui.agent_widget.utc_now', lambda: NOW)
    warming = replace(decision(), action='hold', risk_status='Warming up', reason='Collecting at least 4 distinct quotes spanning 60 seconds')
    snapshot = AgentSnapshot(running=True, cycle=1, phase='Waiting', observed_at=NOW,
                             decisions=(warming,), next_cycle_at=NOW + timedelta(seconds=15))
    widget.update_agent(snapshot)
    assert 'Next quote check in 15s' in widget.run_status.text()
    assert 'Collecting at least 4 distinct quotes' in widget.run_detail.text()
    no_news = replace(decision(), action='hold', buy_allowed=False, source_context={
        'risk_terms': [], 'coverage': 'Insufficient fresh ticker-specific coverage'})
    snapshot = replace(snapshot, decisions=(no_news,), market_status={'crypto': 'Unavailable: Missing crypto capability'})
    widget.update_agent(snapshot)
    assert 'News blocks new buys' in widget.run_detail.text()
    assert 'Missing crypto capability' in widget.run_detail.text()
    assert '0 BUY' in widget.run_status.text()
    controller.agent.paper.start('broker_quotes', 1000, 100)
    controller.agent.paper_source = 'broker_quotes'
    snapshot = replace(snapshot, decisions=(replace(no_news, action='exit'),), paper=controller.agent.paper_context())
    widget.update_agent(snapshot)
    assert 'EXIT signal; no virtual holding to sell' in widget.run_detail.text()
    widget.update_agent(replace(snapshot, running=False, phase='Error', error='Could not save paper results.'))
    assert widget.run_status.text() == 'Could not save paper results.'
    window.close()
    store.close()


def test_delayed_quote_status_updates_with_the_clock_and_recovers(tmp_path, app, monkeypatch):
    from test_agent_runtime import NOW

    store = AuditStore(tmp_path / 'delayed-status.db')
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    window = MainWindow(controller, controller.config)
    widget = window.agent_widget
    current = NOW
    monkeypatch.setattr('grande_alpha.ui.agent_widget.utc_now', lambda: current)
    snapshot = AgentSnapshot(running=True, cycle=1, phase='Working', started_at=NOW, cycle_started_at=NOW,
                             worker_status={'equity': 'Requesting quotes', 'crypto': 'Loading crypto pairs'})
    widget.update_agent(snapshot)
    assert '0s elapsed' in widget.run_status.text()
    current += timedelta(seconds=12)
    widget._update_clock()  # A slow provider does not need to publish another snapshot.
    assert '12s elapsed' in widget.run_status.text()
    assert 'Robinhood data is delayed' in widget.run_detail.text()
    assert 'Loading crypto pairs' in widget.run_detail.text()
    current += timedelta(seconds=30)
    widget._update_clock()
    assert 'Stop agent' in widget.run_detail.text()
    widget.update_agent(replace(snapshot, phase='Waiting', observed_at=current,
                                next_cycle_at=current + timedelta(seconds=5)))
    assert 'Next quote check in 5s' in widget.run_status.text()
    assert 'delayed' not in widget.run_detail.text()
    window.close()
    store.close()


def test_hold_diagnostics_show_actual_threshold_and_do_not_blame_news_for_an_ai_veto(tmp_path, app):
    from test_agent_paper import decision

    store = AuditStore(tmp_path / 'hold-reasons.db')
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    window = MainWindow(controller, controller.config)
    widget = window.agent_widget
    item = replace(decision('hold'), reason='Observed midpoint change +3.0 bps; research threshold 20.0 bps')
    snapshot = AgentSnapshot(running=True, cycle=2, phase='Waiting', decisions=(item,))
    widget.update_agent(snapshot)
    assert '+3.0 bps' in widget.run_detail.text() and '20.0 bps' in widget.run_detail.text()
    for required, supported in ((True, True), (False, False)):
        item = replace(item, buy_allowed=False, reason='AI result expired; requesting a fresh analysis',
                       source_context={'news_required': required, 'buy_supported': supported, 'risk_terms': [], 'coverage': 'Fixture'})
        widget.update_agent(replace(snapshot, decisions=(item,)))
        assert 'AI result expired' in widget.run_detail.text()
        assert 'News blocks' not in widget.run_detail.text()
    window.close()
    store.close()


@pytest.mark.asyncio
async def test_x_connection_form_masks_saves_and_removes_key_without_enabling_news(tmp_path, app, monkeypatch):
    import asyncio

    from PySide6.QtWidgets import QLineEdit

    from grande_alpha.agent_x import X_SERVICE

    vault = {}
    monkeypatch.setattr('keyring.set_password', lambda service, user, value: vault.update({(service, user): value}))
    monkeypatch.setattr('keyring.delete_password', lambda service, user: vault.pop((service, user)))
    store = AuditStore(tmp_path / 'x-ui.db')
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    window = MainWindow(controller, controller.config)
    widget, token = window.agent_widget, 'synthetic_X_token_12345'
    form = widget.x_connection
    assert not widget.twitter_enabled.isChecked()
    assert form.token.echoMode() == QLineEdit.EchoMode.Password
    form.token.setText(token)
    form.save.click()
    task = form.task
    assert form.token.text() == '' and not widget.paper_start.isEnabled()
    await asyncio.wait_for(task, 2)
    assert vault[(X_SERVICE, 'bearer')] == token
    assert widget.twitter_enabled.isChecked() and not widget.news_enabled.isChecked()
    assert token not in form.status.text() and token not in repr(widget._read_settings())
    form.remove.click()
    await asyncio.wait_for(form.task, 2)
    assert not vault and not widget.twitter_enabled.isChecked()
    widget.update_agent(AgentSnapshot(research_sources={'items': [], 'sources': [], 'refreshed_at': None, 'twitter': {
        'status': 'OK', 'trends': [{'symbol': 'AAPL', 'sample_posts': 2, 'sample_change': 1}],
        'notice': 'Fixture sampled posts; not total X volume',
    }}))
    assert 'AAPL: 2 posts (+1 vs previous sample)' in widget.twitter_trends.text()
    window.close()
    store.close()

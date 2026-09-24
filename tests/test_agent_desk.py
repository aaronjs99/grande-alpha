from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from test_agent_paper import decision
from test_responsive_ui import DisabledBroker

from grande_alpha.agent_models import AgentSnapshot
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.models import utc_now
from grande_alpha.storage import AuditStore
from grande_alpha.ui.agent_desk import ChatTranscript, PacificAxis
from grande_alpha.ui.agent_widget import AgentWidget


@pytest.fixture
def desk(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    appearance = QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("grande_alpha.ui.agent_widget.appearance_settings", lambda: appearance)
    store = AuditStore(tmp_path / "desk.db")
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    widget = AgentWidget(controller)
    yield app, controller, widget, appearance
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    app.processEvents()
    store.close()


def test_chat_stays_visible_when_dashboard_scrolls_and_bots_reflow(desk):
    app, _, widget, _ = desk
    widget.resize(1600, 850)
    widget.show()
    app.processEvents()
    assert widget.chat.isVisible() and widget.dashboard_scroll.isVisible()
    assert not widget.compact_tabs.isVisible()
    assert {widget.stages.getItemPosition(widget.stages.indexOf(card))[0]
            for card in widget.stage_cards} == {0}
    position = widget.chat.send.mapTo(widget.viewport(), widget.chat.send.rect().center())
    assert widget.viewport().rect().contains(position)
    widget.dashboard_scroll.verticalScrollBar().setValue(widget.dashboard_scroll.verticalScrollBar().maximum())
    app.processEvents()
    assert widget.chat.send.mapTo(widget.viewport(), widget.chat.send.rect().center()) == position
    assert widget.chat.isVisible()


def test_compact_chat_keeps_composer_accessible_with_long_reply_and_proposal(desk):
    app, _, widget, _ = desk
    widget.resize(900, 610)
    widget.show()
    widget.chat_tab.click()
    chat = widget.chat
    chat.transcript.appendPlainText("Local AI: " + "A long explanation of paper observations. " * 120)
    chat.preview.setText("Team directions: " + "Review the data and costs. " * 80)
    chat.review_box.show()
    QTest.qWait(50)
    assert chat.isVisible() and not widget.dashboard_scroll.isVisible()
    assert widget.chat_tab.isChecked() and not widget.desk_tab.isChecked()
    for control in (chat.input, chat.send, chat.cancel, chat.controls_toggle, chat.connections):
        assert control.isVisible()
        assert widget.viewport().rect().contains(control.mapTo(widget.viewport(), control.rect().bottomRight()))
    chat.transcript.ensureWidgetVisible(chat.apply)
    app.processEvents()
    assert chat.transcript.viewport().rect().contains(
        chat.apply.mapTo(chat.transcript.viewport(), chat.apply.rect().center()))
    chat.controls_toggle.click()
    app.processEvents()
    assert chat.controls_box.isVisible()
    chat.controls_box.close()
    chat.connections.click()
    app.processEvents()
    assert widget.dashboard_scroll.isVisible() and not chat.isVisible()
    assert widget.prompt_box.isVisible()


def test_chat_bubbles_are_bounded_plain_text_and_quick_prompts_do_not_send(desk):
    app, _, widget, _ = desk
    chat = widget.chat
    chat.explain.click()
    assert "latest paper trade" in chat.input.toPlainText()
    chat.review_risk.click()
    assert "exposure" in chat.input.toPlainText()
    assert chat._task is None and chat.messages == []
    transcript = ChatTranscript()
    for i in range(61):
        transcript.appendPlainText(f"Local AI: {i} <b>untrusted</b>")
    app.processEvents()
    assert len(transcript._entries) == 60
    assert transcript.toPlainText().startswith("Local AI: 1 ")
    assert "<b>untrusted</b>" in transcript.toPlainText()
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in transcript.findChildren(QLabel))
    transcript.clear()
    assert transcript.toPlainText() == ""
    transcript.deleteLater()


def test_pause_toolbar_uses_persisted_control_and_preserves_paper_ledger(desk):
    _, controller, widget, _ = desk
    now = utc_now()
    controller.agent.paper.start("broker_quotes", 1000, 100)
    controller.agent.paper.consume([decision(at=now)], now, 15)
    before = controller.agent.paper_context()
    assert controller.agent.paper.state["pending"]
    widget.pause_buys.click()
    assert controller.agent.settings.paper_entries_paused
    assert widget.chat.paused.isChecked() and widget.pause_buys.text() == "Resume new buys"
    assert not controller.agent.paper.state["pending"]
    assert controller.agent.paper_context()["session_id"] == before["session_id"]
    assert controller.agent.paper_context()["total_pnl"] == before["total_pnl"]
    reopened = AgentWidget(controller)
    assert reopened.pause_buys.isChecked() and reopened.pause_buys.text() == "Resume new buys"
    reopened.shutdown()
    reopened.deleteLater()
    widget.chat.paused.setChecked(False)
    assert not widget.pause_buys.isChecked() and widget.pause_buys.text() == "Pause new buys"
    assert controller.agent_preferences.settings.paper_entries_paused is False


def test_chart_ranges_filter_retained_samples_without_changing_data(desk):
    _, controller, widget, appearance = desk
    start = datetime(2026, 9, 23, tzinfo=UTC).timestamp()
    times = [start, start + 86400, start + 90000, start + 93600]
    values = [1000, 995, 996, 994]
    before = controller.agent.paper_context()
    widget._plot_history(times, values)
    by_text = {button.text(): button for button in widget.chart_ranges.buttons()}
    by_text["1H"].click()
    assert list(widget.curve.getData()[0]) == times[-2:]
    assert appearance.value("agent_chart_range") == "1h"
    by_text["1D"].click()
    assert list(widget.curve.getData()[0]) == times[1:]
    by_text["ALL"].click()
    assert list(widget.curve.getData()[1]) == values
    assert widget._chart_times == times and widget._chart_values == values
    assert controller.agent.paper_context() == before


def test_pacific_chart_labels_follow_daylight_saving_time(desk):
    axis = PacificAxis(orientation="bottom")
    winter = datetime(2026, 1, 15, 17, tzinfo=UTC).timestamp()
    summer = datetime(2026, 7, 15, 17, tzinfo=UTC).timestamp()
    assert axis.tickStrings([winter, summer], 1, 60) == ["09:00 AM", "10:00 AM"]
    assert axis.tickStrings([winter], 1, 5) == ["09:00:00"]
    assert axis.tickStrings([float("nan")], 1, 60) == [""]


def test_source_badges_distinguish_fresh_overdue_off_and_stopped(desk):
    _, controller, widget, _ = desk
    now = utc_now()
    controller.agent.settings = replace(controller.agent.settings, news_enabled=True, twitter_enabled=True)
    report = {"refreshed_at": now.isoformat(), "sources": [
        {"source": "BBC Business", "status": "OK", "fresh_items": 1},
        {"source": "Bluesky", "status": "OK", "fresh_items": 1}], "items": [],
        "twitter": {"status": "OK", "notice": "Synthetic source fixture"}}
    snapshot = AgentSnapshot(running=True, cycle=1, observed_at=now, decisions=(decision(at=now),), research_sources=report)
    widget.update_agent(snapshot)
    assert widget.news_pill.text() == "News · 1 feed available"
    assert "1/1 fresh" in widget.quotes_pill.text()
    old = replace(snapshot, research_sources={**report, "refreshed_at": (now - timedelta(hours=1)).isoformat()},
                  decisions=(decision(at=now - timedelta(minutes=1)),))
    widget.update_agent(old)
    assert "overdue" in widget.news_pill.text() and "overdue" in widget.x_pill.text()
    assert "0/1 fresh" in widget.quotes_pill.text()
    controller.agent.settings = replace(controller.agent.settings, news_enabled=False, twitter_enabled=False)
    widget.update_agent(replace(old, running=False))
    assert widget.news_pill.text() == "News · Off" and widget.x_pill.text() == "X / Twitter · Off"
    assert widget.quotes_pill.text() == "Quotes · Stopped"


def test_monitor_errors_remain_visible_when_diagnostics_are_collapsed(desk):
    app, _, widget, _ = desk
    widget.resize(1400, 800)
    widget.show()
    widget.update_agent(AgentSnapshot(error="Quote provider is unavailable"))
    app.processEvents()
    assert not widget.diagnostics_box.isVisible()
    assert widget.heartbeat.isVisible()
    assert "Quote provider is unavailable" in widget.heartbeat.text()
    assert widget.heartbeat.property("attention") is True
    widget.update_agent(AgentSnapshot())
    assert widget.heartbeat.property("attention") is False

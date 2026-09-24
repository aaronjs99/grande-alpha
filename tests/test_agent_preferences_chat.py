from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_agent_paper import decision
from test_agent_runtime import NOW, ReadMarket
from test_responsive_ui import DisabledBroker

from grande_alpha.agent_chat import CHANGE_FIELDS, ChatReply, LocalAgentChat, chat_context, parse_chat_reply
from grande_alpha.agent_models import AgentSettings, AgentSnapshot
from grande_alpha.agent_preferences import AgentPreferences, PaperSetup, load_preferences
from grande_alpha.agent_strategy import limit_entries, pause_entries
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.storage import AuditStore
from grande_alpha.ui.agent_widget import AgentWidget


@pytest.fixture
def desktop(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = AuditStore(tmp_path / "desk.db")
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    widget = AgentWidget(controller)
    yield app, controller, widget
    widget.shutdown()
    widget.deleteLater()
    app.processEvents()
    store.close()


def test_preferences_autosave_and_restore_all_agent_choices_without_authority(desktop):
    app, controller, widget = desktop
    widget.equities.setText("AAPL, MSFT")
    widget.crypto.setText("BTC, ETH")
    widget.scans.addItem("My saved scan", "scan-123")
    widget.scans.setCurrentIndex(1)
    widget.interval.setValue(10)
    widget.local_ai.setChecked(True)
    widget.model.setText("qwen2.5:3b")
    widget.news_enabled.setChecked(True)
    widget.social_enabled.setChecked(True)
    widget.twitter_enabled.setChecked(True)
    widget.briefs["team"].setText("Explain losses and compare costs")
    widget.briefs["equity"].setText("Examine company news")
    widget.briefs["crypto"].setText("Flag unreliable social claims")
    widget.paper_strategy.setCurrentIndex(widget.paper_strategy.findData("legacy"))
    widget.paper_source.setCurrentIndex(widget.paper_source.findData("demo"))
    widget.paper_cash.setValue(2000)
    widget.paper_trade_cash.setValue(50)
    widget.repeat_demo.setChecked(True)
    QTest.qWait(650)
    assert not widget._preferences_dirty
    widget.chat.max_positions.setValue(2)
    widget.chat.max_exposure.setValue(20)
    widget.chat.paused.setChecked(True)
    saved = controller.agent_preferences
    assert saved.settings.equity_symbols == ("AAPL", "MSFT")
    assert saved.settings.research_brief == "Explain losses and compare costs"
    assert saved.settings.paper_entries_paused
    assert saved.paper == PaperSetup("demo", 2000, 50, True)
    restored = TradingController(DisabledBroker(), AppConfig(), controller.store)
    again = AgentWidget(restored)
    try:
        assert restored.agent_preferences == saved
        assert again._read_settings() == saved.settings
        assert again.scans.currentData() == "scan-123"
        assert again.paper_cash.value() == 2000 and again.paper_trade_cash.value() == 50
        assert again.repeat_demo.isChecked() and again.chat.paused.isChecked()
        assert not restored.snapshot.connected and not restored.agent.snapshot.running
        assert not restored.agent_bridge.session and restored.risk.grant is None
        payload = controller._agent_preferences_path.read_text()
        assert not any(secret in payload for secret in ("account_number", "api_key", "authority", "mcp_enabled"))
    finally:
        again.shutdown()
        again.deleteLater()
        app.processEvents()


def test_invalid_draft_preserves_saved_settings_and_pause_still_works(desktop):
    _, controller, widget = desktop
    widget.model.setText("qwen2.5:3b")
    widget.local_ai.setChecked(True)
    assert widget._save_preferences()
    before = controller._agent_preferences_path.read_bytes()
    widget.model.clear()
    assert not widget._save_preferences()
    assert controller._agent_preferences_path.read_bytes() == before
    widget.chat.paused.setChecked(True)
    assert controller.agent.settings.paper_entries_paused
    assert controller.agent.settings.local_ai_model == "qwen2.5:3b"
    assert widget.model.text() == ""  # keep the unsaved draft visible


def test_close_flushes_debounce_and_failed_save_leaves_previous_config(desktop, monkeypatch):
    _, controller, widget = desktop
    widget.equities.setText("AAPL")
    widget.shutdown()
    assert load_preferences(controller._agent_preferences_path)[0].settings.equity_symbols == ("AAPL",)
    original = controller.agent.settings
    def fail(*_args):
        raise OSError("Fixture read-only disk")
    monkeypatch.setattr(AgentPreferences, "save", fail)
    with pytest.raises(OSError):
        controller.save_agent_preferences(replace(original, equity_symbols=("MSFT",)))
    assert controller.agent.settings == original


@pytest.mark.parametrize("payload", ["not JSON", '{"version":2}',
                                    '{"version":1,"settings":{"local_ai_enabled":"true"},"paper":{}}',
                                    '{"version":1,"settings":{"paper_max_positions":99},"paper":{}}',
                                    '{"version":1,"settings":{},"paper":{"initial_cash":"1000"}}'])
def test_corrupt_or_unsupported_preferences_preserved(tmp_path, payload):
    path = tmp_path / "settings.json"
    path.write_text(payload)
    preferences, error = load_preferences(path)
    assert error and preferences == AgentPreferences()
    assert path.read_text() == payload


def encoded_reply(changes=None):
    return json.dumps({"reply": "The paper position has an unrealized loss. Check spread and slippage.",
                       "changes": {**dict.fromkeys(CHANGE_FIELDS), **(changes or {})}})


@pytest.mark.parametrize("changes", [{"live_trading_enabled": True}, {"paper_max_positions": 5},
                                     {"paper_max_exposure_pct": 90}, {"paper_entries_paused": "false"},
                                     {"paper_max_positions": True}, {"research_brief": "x" * 2001}])
def test_invalid_ai_settings_cannot_be_applied(changes):
    with pytest.raises(ValueError):
        parse_chat_reply(encoded_reply(changes))


def test_chat_parser_rejects_duplicate_and_non_json_output():
    for raw in ('{"reply":"one","reply":"two","changes":{}}', "```json\n{}\n```", "[]"):
        with pytest.raises(ValueError):
            parse_chat_reply(raw)


@pytest.mark.asyncio
async def test_local_chat_transport_is_bounded_and_cannot_request_tools(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"done": True, "done_reason": "stop",
                                        "message": {"content": encoded_reply({"paper_entries_paused": True})}})
    client_type = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return client_type(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr("grande_alpha.agent_chat.httpx.AsyncClient", client)
    response = await LocalAgentChat().reply("qwen2.5:3b", [{"role": "user", "content": "Pause new buys"}], {"paper": None})
    assert response.changes == {"paper_entries_paused": True}
    assert str(requests[0].url) == "http://127.0.0.1:11434/api/chat"
    body = json.loads(requests[0].content)
    assert body["stream"] is False and "tools" not in body
    assert body["messages"][0]["role"] == "system" and body["format"]["additionalProperties"] is False


def test_chat_context_uses_actual_paper_loss_and_no_private_account_data(desktop):
    _, controller, _ = desktop
    controller.snapshot.agent_budget = {"account_number": "PRIVATE-ACCOUNT"}
    controller.agent.paper.start("broker_quotes", 1000, 100)
    controller.agent.paper.consume([decision()], NOW, 15)
    later = NOW + timedelta(seconds=5)
    controller.agent.paper.consume([decision(at=later)], later, 15)
    context = chat_context(controller.agent)
    assert float(context["paper"]["total_pnl"]) < 0
    assert context["paper"]["recent_fills"][0]["side"] == "buy"
    assert "PRIVATE-ACCOUNT" not in json.dumps(context)
    assert "account_number" not in json.dumps(context)


@pytest.mark.asyncio
async def test_chat_requires_apply_and_keeps_portfolio_and_layout(desktop):
    _, controller, widget = desktop
    widget.model.setText("qwen2.5:3b")
    assert widget._save_preferences()
    chat = widget.chat
    captured = []
    class Client:
        async def reply(self, model, messages, context):
            captured.append((model, messages, context))
            return ChatReply("We can pause new entries while examining losses.",
                             {"paper_entries_paused": True, "paper_max_positions": 2,
                              "research_brief": "Compare transaction costs before new entries"})
    chat.client = Client()
    controller.agent.paper.start("broker_quotes", 1000, 100)
    controller.agent.paper.consume([decision()], NOW, 15)
    before = controller.agent.paper_context()
    chat.input.setPlainText("Help reduce the risk of more paper losses")
    chat.send.click()
    await chat._task
    assert captured[0][0] == "qwen2.5:3b" and captured[0][1][-1]["role"] == "user"
    assert not controller.agent.settings.paper_entries_paused
    assert controller.agent.paper.state["pending"]
    assert chat.apply.isEnabled() and "not applied" in chat.preview.text()
    controller.agent.snapshot = AgentSnapshot(running=True)
    chat.apply.click()
    assert controller.agent.settings.paper_entries_paused
    assert controller.agent.settings.paper_max_positions == 2
    assert not controller.agent.paper.state["pending"]
    assert controller.agent.paper_context()["session_id"] == before["session_id"]
    assert controller.agent.paper_context()["total_pnl"] == before["total_pnl"]
    assert widget.briefs["team"].text() == "Compare transaction costs before new entries"
    assert len(widget.team_cards) == 6 and widget.activity is not None and widget.curve is not None
    assert not controller.agent_bridge.session and controller.risk.grant is None


@pytest.mark.asyncio
async def test_changed_settings_invalidate_proposal_and_stop_cancels_slow_chat(desktop):
    _, controller, widget = desktop
    widget.model.setText("qwen2.5:3b")
    widget._save_preferences()
    chat = widget.chat
    class Fast:
        async def reply(self, *_args):
            return ChatReply("Pause", {"paper_entries_paused": True})
    chat.client = Fast()
    chat.input.setPlainText("Pause new buys")
    chat.send.click()
    await chat._task
    controller.set_agent_brief("team", "New direction")
    chat.apply.click()
    assert "Settings changed" in chat.status.text() and not controller.agent.settings.paper_entries_paused
    class Slow:
        async def reply(self, *_args):
            await asyncio.Event().wait()
    chat.client = Slow()
    chat.input.setPlainText("Explain the paper session")
    chat.send.click()
    await asyncio.sleep(0)
    controller.stop_agent()
    await asyncio.wait_for(chat._task, 1)
    assert "canceled" in chat.status.text() and chat.send.isEnabled()
    assert not chat.apply.isEnabled()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), ValueError("bad reply"), httpx.ConnectError("offline")])
async def test_chat_failure_is_visible_and_does_not_change_settings(desktop, failure):
    _, controller, widget = desktop
    widget.model.setText("qwen2.5:3b")
    widget._save_preferences()
    before = controller.agent.settings
    class Failing:
        async def reply(self, *_args):
            raise failure
    widget.chat.client = Failing()
    widget.chat.input.setPlainText("Explain the losses")
    widget.chat.send.click()
    await widget.chat._task
    assert widget.chat.send.isEnabled() and not widget.chat.apply.isEnabled()
    assert controller.agent.settings == before
    assert "reply received" not in widget.chat.status.text().lower()


def test_pause_cancels_pending_entry_but_valid_exit_still_fills():
    market = ReadMarket()
    agent = market.runtime()
    agent.paper.start("broker_quotes", 1000, 100)
    agent.paper_source = "broker_quotes"
    original = AgentSettings(paper_strategy="adaptive")
    agent.settings = original
    agent._consume_paper([decision()], original)
    assert agent.paper.state["pending"]
    agent.settings = replace(original, paper_entries_paused=True)
    market.now = NOW + timedelta(seconds=5)
    # A completed old cycle must obey the current pause before any fill.
    agent._consume_paper([decision(at=NOW + timedelta(seconds=5))], original)
    assert not agent.paper.state["pending"] and agent.paper.state["fill_count"] == 0
    book = agent.paper
    book.start("broker_quotes", 1000, 100)
    book.consume([decision()], NOW, 15)
    later = NOW + timedelta(seconds=5)
    book.consume([decision(at=later)], later, 15)
    for side in ("exit", "hold"):
        later += timedelta(seconds=5)
        book.consume(pause_entries([decision(side, later, 98, 99)], True), later, 15)
    assert book.state["fill_count"] == 2 and not book.state["positions"]
    assert float(book.state["realized_pnl"]) < 0


def test_user_limits_reserve_entries_and_keep_exits_available():
    agent = ReadMarket().runtime()
    agent.paper.start("broker_quotes", 1000, 100)
    rows = [decision(key="AAPL"), decision(key="MSFT"), decision("exit", key="NVDA")]
    for settings in (AgentSettings(paper_max_positions=1), AgentSettings(paper_max_exposure_pct=10)):
        results = limit_entries(rows, agent.paper.state, settings)
        assert [item.action for item in results] == ["buy", "hold", "exit"]
        assert not results[1].buy_allowed

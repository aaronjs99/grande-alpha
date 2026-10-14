"""Offline acceptance for Antonio's desk port; disposable data and no broker/network."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QVariantAnimation
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from grande_alpha.execution.session_runtime import BrokerSessionRuntime
from grande_alpha.execution.session_worker import SessionWorker
from grande_alpha.execution.worker_control import WorkerControlStore
from grande_alpha.execution.worker_ipc import LocalControlClient, LocalControlServer
from grande_alpha.execution.worker_process import launch_worker, serve
from grande_alpha.execution.worker_status import disconnected_worker_status
from grande_alpha.research.agent_chat import LocalAgentChat, chat_context, parse_chat_reply
from grande_alpha.research.agent_models import AgentSnapshot
from grande_alpha.research.desk_snapshot import desk_snapshot, paper_summary
from grande_alpha.research.local_ollama import LocalOllamaClient
from grande_alpha.ui.session_window import SessionWindow


def research_fixture() -> dict:
    return {
        "enabled": False, "running": True, "phase": "Working",
        "paper": {"source": "demo", "active": True, "session_id": "paper-1",
                  "equity": "1012.50", "total_pnl": "12.50", "fill_count": 3,
                  "win_rate": None, "stale_marks": False, "observed_at": "2026-10-14T18:00:00+00:00",
                  "equity_history": [{"at": "2026-10-14T18:00:00+00:00", "equity": "1000"},
                                     {"at": "2026-10-14T18:01:00+00:00", "equity": "1012.50"}]},
        "desk": {"session_id": "research-1", "cycle": 2, "running": True, "phase": "Working",
                 "team_status": {"NOVA": "Fetching quotes", "ORIN": "Unavailable", "VELA": "Waiting for quotes"},
                 "analysis_status": {}, "team_events": [{"id": 1, "from": "NOVA", "to": "VELA",
                                                          "message": "Two quotes", "kind": "SCAN"}]},
    }


def worker_status(research=None) -> dict:
    return {"connected": False, "running": False, "generation": 0, "phase": "Stopped",
            "research": research, "last_cycle": {}}


class DeskAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ("segoeui.ttf", "segoeuib.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        cls.app.setFont(QFont("Segoe UI", 10))

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="grande-desk-check-")
        self.path = Path(self.scratch.name)
        self.window = SessionWindow(self.path, auto_probe=False)
        self.window.status_timer.stop()

    def tearDown(self):
        self.window._closing = True
        self.window.close()
        self.app.processEvents()
        self.scratch.cleanup()

    def test_default_desk_and_setup_are_separate_with_stop_above_scroll(self):
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.desk.isVisible())
        self.assertFalse(self.window.flow_panel.isVisible())
        for width, height in ((1120, 780), (390, 650)):
            self.window.resize(width, height)
            self.app.processEvents()
            self.window.desk_scroll.verticalScrollBar().setValue(10000)
            self.app.processEvents()
            self.assertTrue(self.window.stop_button.isVisible())
            self.assertLess(self.window.stop_button.mapTo(self.window, self.window.stop_button.rect().bottomRight()).y(), height)
            self.assertEqual(self.window.desk_scroll.horizontalScrollBar().maximum(), 0)
            for metric in (self.window.desk.equity, self.window.desk.pnl, self.window.desk.fills, self.window.desk.win_rate):
                self.assertLessEqual(metric.fontMetrics().horizontalAdvance(metric.text()), metric.width())
        self.window._show_setup("paper")
        self.app.processEvents()
        self.assertTrue(self.window.paper_start_button.isVisible())
        self.assertFalse(self.window.flow_panel.isVisible())
        self.window.setup_dialog.reject()

    def test_real_paper_chart_and_cards_freeze_when_worker_is_unreachable(self):
        self.window.show()
        self.window._apply_status(worker_status(research_fixture()))
        self.app.processEvents()
        desk = self.window.desk
        self.assertEqual(desk.equity.text(), "$1,012.50")
        self.assertEqual(desk.win_rate.text(), "—")
        self.assertEqual(len(desk.curve.getData()[0]), 2)
        self.assertIn("Demo", desk.source.text())
        self.assertTrue(desk.cards["NOVA"]._working)
        self.assertFalse(desk.cards["ORIN"]._working)
        self.window._apply_disconnected_status(disconnected_worker_status(self.path))
        self.assertIn("Last reported", desk.source.text())
        self.assertEqual(len(desk.curve.getData()[0]), 2)
        for card in desk.cards.values():
            self.assertFalse(card._working)
            self.assertEqual(card._breath.state(), QVariantAnimation.State.Stopped)

    def test_new_session_and_no_paper_clear_previous_metrics_and_chart(self):
        self.window._apply_status(worker_status(research_fixture()))
        self.window._apply_status(worker_status())
        self.assertEqual(self.window.desk.equity.text(), "—")
        self.assertIsNone(self.window.desk.curve.getData()[0])
        self.assertEqual(self.window.desk.empty_chart.text(), "Start paper to see virtual equity")

    def test_handoffs_are_deduplicated_and_not_replayed_after_reconnect(self):
        self.window.show()
        self.window._apply_status(worker_status(research_fixture()))
        desk = self.window.desk
        self.assertFalse(desk.cards["VELA"]._hold.isActive())

        self.window._apply_status(worker_status(research_fixture()))
        self.assertEqual(desk.activity.rowCount(), 1)
        newer = research_fixture()
        newer["desk"]["team_events"].append({"id": 2, "from": "NOVA", "to": "VELA", "message": "New quotes", "kind": "SCAN"})
        self.window._apply_status(worker_status(newer))
        self.assertTrue(desk.cards["VELA"]._hold.isActive())
        self.window._apply_disconnected_status(disconnected_worker_status(self.path))
        self.window._apply_status(worker_status(newer))
        self.assertFalse(desk.cards["VELA"]._hold.isActive())

    def test_research_without_active_paper_is_labelled_research(self):
        research = research_fixture()
        research["paper"]["active"] = False
        self.window._apply_status(worker_status(research))
        self.assertIn("Research", self.window.status_notice.text())
        self.assertIn("Saved", self.window.desk.source.text())

    def test_invalid_saved_marks_and_win_rate_show_an_empty_view(self):
        research = research_fixture()
        research["paper"]["win_rate"] = "not a rate"
        research["paper"]["equity_history"] = [{"at": None, "equity": "1000"},
                                                {"at": "not a date", "equity": "1000"}]
        self.window._apply_status(worker_status(research))
        self.assertEqual(self.window.desk.win_rate.text(), "—")
        self.assertIsNone(self.window.desk.curve.getData()[0])
        self.assertEqual(self.window.desk.empty_chart.text(), "Waiting for first cycle")

    def test_cold_demo_launches_only_worker_and_never_connects_broker(self):
        calls = []
        self.window._apply_disconnected_status(disconnected_worker_status(self.path))
        def launch(_path, **_kwargs):
            self.assertIs(_kwargs.get("recover_existing"), False)
            calls.append("launch")
            return object()
        async def request(operation, payload, **kwargs):
            calls.append(operation)
            if operation == "research_paper_start":
                self.assertEqual(payload["source"], "demo")
                return research_fixture()
            if operation == "status":
                return worker_status(research_fixture())
            raise AssertionError(operation)
        self.window._launcher = launch
        self.window._request = request
        asyncio.run(self.window._start_paper())
        self.assertEqual(calls, ["launch", "research_paper_start", "status"])

    def test_stop_during_cold_launch_prevents_paper_start(self):
        async def scenario():
            self.window._apply_disconnected_status(disconnected_worker_status(self.path))
            started, release = asyncio.Event(), asyncio.Event()
            loop = asyncio.get_running_loop()
            def launch(_path, **kwargs):
                loop.call_soon_threadsafe(started.set)
                import threading
                while not release.is_set():
                    threading.Event().wait(.005)
                return object()
            calls = []
            async def request(operation, payload, **kwargs):
                calls.append(operation)
                return worker_status()
            self.window._launcher = launch
            self.window._request = request
            task = asyncio.create_task(self.window._start_paper())
            try:
                await started.wait()
                await self.window._stop()
            finally:
                release.set()
                await task
            self.assertNotIn("research_paper_start", calls)
        asyncio.run(scenario())

    def test_unknown_saved_intent_cannot_launch_demo(self):
        self.window._status = {"worker_reachable": False, "control_record": "unavailable"}
        with patch.object(self.window, "_launcher", side_effect=AssertionError("must not launch")):
            asyncio.run(self.window._start_paper())
        self.assertFalse(self.window.paper_start_button.isEnabled())

    def test_non_recovering_serve_refuses_new_saved_live_intent_without_touching_it(self):
        self.window._apply_disconnected_status(disconnected_worker_status(self.path))
        control = WorkerControlStore(self.path / "grande_alpha.db")
        state = control.review(account="fixture", scope_digest="scope", candidate_path="c",
                               authorization_path="a", earnings_database="e", poll_seconds=5)
        state = control.start("scope", expected_generation=state.generation)
        try:
            with patch("grande_alpha.execution.worker_process.BrokerSessionRuntime", side_effect=AssertionError("broker constructed")), \
                 patch("grande_alpha.execution.worker_process.LocalControlServer", side_effect=AssertionError("credentials created")):
                self.assertEqual(asyncio.run(serve(self.path, recover_existing=False)), 3)
            self.assertTrue(control.current().running)
            self.assertEqual(control.current().generation, state.generation)
        finally:
            control.close()

    def test_paper_launch_propagates_non_recovery_to_the_hidden_process(self):
        class Client:
            requests = 0
            def __init__(self, _path): pass
            def request(self, *args, **kwargs):
                self.requests += 1
                if self.requests == 1:
                    raise OSError("not running")
                return {}
        with patch("grande_alpha.execution.worker_process.LocalControlClient", Client), \
             patch("grande_alpha.execution.worker_process.subprocess.Popen") as process:
            launch_worker(self.path, recover_existing=False)
            self.assertIn("--no-recovery", process.call_args.args[0])

    def test_launch_waits_for_the_owner_when_a_competing_child_exits(self):
        class Client:
            requests = 0
            def __init__(self, _path): pass
            def request(self, *args, **kwargs):
                self.requests += 1
                if self.requests < 3:
                    raise OSError("owner is starting")
                return {}
        with patch("grande_alpha.execution.worker_process.LocalControlClient", Client), \
             patch("grande_alpha.execution.worker_process.subprocess.Popen") as process:
            process.return_value.poll.return_value = 2
            client = launch_worker(self.path, recover_existing=False)
            self.assertEqual(client.requests, 3)

    def test_pending_status_cannot_overwrite_a_completed_paper_stop(self):
        async def scenario():
            self.window._apply_status(worker_status(research_fixture()))
            entered, release = asyncio.Event(), asyncio.Event()
            stopped = research_fixture()
            stopped["running"] = False
            stopped["paper"]["active"] = False
            stopped["desk"]["running"] = False
            async def request(operation, payload, **kwargs):
                if operation == "status":
                    entered.set()
                    await release.wait()
                    return worker_status(research_fixture())
                if operation == "research_paper_stop":
                    return stopped
                raise AssertionError(operation)
            self.window._request = request
            probe = asyncio.create_task(self.window._refresh_status(show_errors=False))
            try:
                await entered.wait()
                await self.window._stop_paper()
                release.set()
                await probe
                self.assertFalse(self.window._status["research"]["running"])
                self.assertNotIn("● Paper", self.window.status_notice.text())
                self.assertFalse(self.window.desk_stop_button.isEnabled())
            finally:
                release.set()
                await probe
        asyncio.run(scenario())

    def test_real_local_channel_demo_runs_persists_and_stops_without_a_broker(self):
        async def scenario():
            control = WorkerControlStore(self.path / "grande_alpha.db")
            runtime = BrokerSessionRuntime(self.path, control=control,
                                           broker_factory=lambda *_: self.fail("Demo attempted a broker connection"))
            worker = SessionWorker(control, runtime)
            server = LocalControlServer(self.path, worker.handle)
            with patch("grande_alpha.execution.worker_ipc._token", return_value="d" * 64), \
                 patch("grande_alpha.research.agent_runtime.DEMO_INTERVAL_SECONDS", .005):
                try:
                    await server.start()
                    self.window._client = LocalControlClient(self.path)
                    await self.window._refresh_status(show_errors=False)
                    await self.window._start_paper()
                    await asyncio.wait_for(runtime.research.agent._task, timeout=8)
                    await self.window._refresh_status(show_errors=False)
                    paper = self.window._status["research"]["paper"]
                    self.assertEqual(paper["source"], "demo")
                    self.assertFalse(paper["active"])
                    self.assertGreater(paper["fill_count"], 0)
                    self.assertGreater(len(self.window.desk.curve.getData()[0]), 1)
                    self.assertFalse(runtime.connected)
                    self.assertFalse(control.current().running)
                    self.window.paper_loop_demo.setChecked(True)
                    await self.window._start_paper()
                    await asyncio.sleep(.025)
                    await self.window._stop()
                    self.assertFalse(runtime.research.agent.snapshot.running)
                    self.assertFalse(control.current().running)
                    self.assertFalse(self.window.desk.cards["NOVA"]._working)
                    self.assertTrue((self.path / "agent-paper.db").is_file())
                finally:
                    await server.close()
                    await worker.close()
                    control.close()
        asyncio.run(scenario())

    def test_chat_error_cancel_retry_and_clear_never_use_worker_commands(self):
        async def scenario():
            dialog = self.window._chat_dialog
            self.window._request = lambda *args, **kwargs: self.fail("Chat attempted a worker command")
            entered = asyncio.Event()
            class Service:
                count = 0
                async def reply(self, *args):
                    self.count += 1
                    if self.count == 1:
                        raise RuntimeError("Test model unavailable")
                    if self.count == 2:
                        entered.set()
                        await asyncio.Event().wait()
                    return "Synthetic demo; advisory only."
            dialog.service = Service()
            dialog.model.setText("test-model")
            for message in ("Explain", "Retry", "Explain again"):
                dialog.entry.setText(message)
                dialog.send()
                if message == "Retry":
                    await entered.wait()
                    dialog.cancel()
                await dialog._task
            self.assertIn("advisory only", dialog.transcript.toPlainText())
            self.assertTrue(dialog.send_button.isEnabled())
            dialog.clear()
            self.assertEqual(dialog.messages, [])
            self.assertEqual(dialog.transcript.toPlainText(), "")
        asyncio.run(scenario())


class ProjectionAcceptance(unittest.TestCase):
    def test_status_is_bounded_and_omits_private_and_heavy_fields(self):
        event = {"id": 5, "from": "NOVA", "to": "VELA", "kind": "NEWS", "message": "A" * 10000,
                 "at": "2026-10-14T18:00:00+00:00", "secret": "PRIVATE"}
        snapshot = replace(AgentSnapshot(), session_id="s", team_events=tuple(dict(event, id=i) for i in range(120)),
                           team_status={"NOVA": "A" * 10000, "PRIVATE": "account"}, diagnostics="PRIVATE" * 10000)
        paper = {"equity": "1000", "total_pnl": "0", "source": "demo", "positions": [{"stale": True, "secret": "PRIVATE"}],
                 "fills": [{"secret": "PRIVATE"}] * 200,
                 "equity_history": [{"at": "2026-10-14T18:00:00+00:00", "equity": "1000"}] * 500}
        payload = {"desk": desk_snapshot(snapshot), "paper": paper_summary(paper)}
        encoded = json.dumps(payload).encode()
        self.assertLess(len(encoded), 32000)
        self.assertNotIn(b"PRIVATE", encoded)
        self.assertTrue(payload["paper"]["stale_marks"])
        self.assertNotIn("positions", payload["paper"])

    def test_chat_context_is_allowlisted_even_if_status_contains_account_data(self):
        context = chat_context({**research_fixture(), "account": "PRIVATE", "bridge_path": "PRIVATE", "diagnostics": "PRIVATE"},
                               reachable=False)
        self.assertNotIn("PRIVATE", json.dumps(context))
        self.assertIs(context["worker_reachable"], False)
        self.assertIn("synthetic", context["limitations"])

    def test_chat_rejects_tool_commands_extra_duplicate_and_invalid_fields(self):
        for raw in ('{"reply":"ok","changes":{"start":true}}', '{"reply":"a","reply":"b"}',
                    '{"reply":""}', '{"reply":"\\u0000"}'):
            with self.assertRaises(ValueError):
                parse_chat_reply(raw)
        self.assertEqual(parse_chat_reply('{"reply":"Advisory only"}'), "Advisory only")

    def test_chat_uses_only_loopback_and_rejects_tool_calls(self):
        captured = {}
        class Response:
            def __init__(self, data):
                self.data = data
                self.content = json.dumps(data).encode()
            def raise_for_status(self): pass
            def json(self): return self.data
        class Client:
            def __init__(self, **kwargs): captured.update(kwargs)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def get(self, url, **kwargs):
                return Response({"models": [] if url.endswith("/ps") else [{"name": "test-model", "size": 1024 ** 3}]})
            async def post(self, url, **kwargs):
                captured["url"] = url
                if url.endswith("/show"):
                    return Response({"capabilities": ["completion"]})
                return Response({"done": True, "message": {"tool_calls": [{"start": True}], "content": '{"reply":"ok"}'}})
        with tempfile.TemporaryDirectory(prefix="grande-chat-check-") as scratch:
            with patch("grande_alpha.research.local_ollama.httpx.AsyncClient", Client):
                client = LocalOllamaClient(lock_path=Path(scratch) / "ai.lock", memory_available=lambda: 16 * 1024 ** 3)
                with self.assertRaisesRegex(ValueError, "unsupported tools"):
                    asyncio.run(LocalAgentChat(client=client).reply("test-model", [{"role": "user", "content": "Explain"}], {}))
        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertIs(captured["trust_env"], False)
        self.assertIs(captured["follow_redirects"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Offline worker recovery acceptance checks; no broker, credentials or user data.

Run from the checkout after installing the desktop extra:
    python tools/verify_worker_recovery.py
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QCloseEvent, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from grande_alpha import cli
from grande_alpha.domain.account_models import Account
from grande_alpha.execution.candidate_profile import candidate_template
from grande_alpha.execution.session_runtime import BrokerSessionRuntime
from grande_alpha.execution.session_worker import SessionWorker
from grande_alpha.execution.worker_control import WorkerControlStore
from grande_alpha.execution.worker_ipc import LocalControlClient, LocalControlError, LocalControlServer
from grande_alpha.execution.worker_status import disconnected_worker_status, stop_existing_worker
from grande_alpha.interfaces.cli import worker_cli
from grande_alpha.ui.session_window import SessionWindow


class UnreachableClient:
    def __init__(self, _data_dir: Path) -> None:
        pass

    def request(self, *_args, **_kwargs) -> dict:
        raise LocalControlError("Test worker is unreachable")


def recorded_session(path: Path, *, running: bool = True) -> WorkerControlStore:
    control = WorkerControlStore(path / "grande_alpha.db")
    state = control.review(
        account="fixture-account", scope_digest="fixture-scope",
        candidate_path="fixture-candidate.json", authorization_path="fixture-permit.json",
        earnings_database="fixture-earnings.db", poll_seconds=5,
    )
    if running:
        control.start("fixture-scope", expected_generation=state.generation)
    return control


def live_status() -> dict:
    return {
        "running": True, "phase": "Running", "account_masked": "fixture · 1234",
        "account": "fixture-account", "scope_digest": "fixture-scope", "connected": True,
        "data_coverage": {"market_history_ready": True, "stock_events": 1, "missing": []},
        "last_data_at": "2026-10-14T18:00:00+00:00", "last_cycle": {},
        "research": {"enabled": True, "running": True, "phase": "Researching"},
        "error": "", "generation": 2,
    }


def write_candidate(path: Path) -> None:
    document = candidate_template()
    document["scope"].update(account_number="fixture-account", allowed_symbols=["QQQ"],
                             starts_at="2026-10-14T08:00:00-07:00", expires_at="2026-10-14T16:00:00-07:00",
                             max_order_usd=10, max_exposure_usd=100, max_daily_notional_usd=200,
                             max_daily_loss_usd=5, max_orders=5, max_orders_per_minute=1,
                             max_quote_age_seconds=8, max_spread_bps=20)
    document["earnings_thresholds"] = {key: 1 for key in document["earnings_thresholds"]}
    path.write_text(json.dumps(document), encoding="utf-8")


class CleanupRuntime:
    """Only the external cleanup await is held; real worker transitions still run."""

    def __init__(self, *, block_stop: bool = False) -> None:
        self.block_stop = block_stop
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()

    def permit(self, _state) -> bool:
        return True

    async def review(self, _payload) -> dict:
        return {"account": "fixture-account", "account_masked": "fixture · 1234",
                "scope_digest": "fixture-scope", "allowed_symbols": ["QQQ"]}

    def authorize(self, _state) -> dict:
        return {"authorized": True}

    async def stop_research(self) -> None:
        if self.block_stop and not self.cleanup_started.is_set():
            self.cleanup_started.set()
            await self.cleanup_release.wait()

    async def cleanup_report(self, _state) -> dict:
        self.cleanup_started.set()
        await self.cleanup_release.wait()
        return {"broker_verified": False}

    async def run(self, _state, *, recover: bool) -> None:
        await asyncio.Event().wait()

    async def close(self) -> None:
        pass


class BlockedAccountBroker:
    """Hold only the account read; market data and order operations stay unavailable."""

    def __init__(self) -> None:
        self.connected = True
        self.read_started = asyncio.Event()
        self.read_release = asyncio.Event()

    async def get_accounts(self) -> list[Account]:
        self.read_started.set()
        await self.read_release.wait()
        return [Account("fixture-account", "Fixture", "individual", True, "active")]

    async def disconnect(self) -> None:
        self.connected = False

    async def get_quotes(self, _symbols):
        return {}

    async def discover_crypto(self):
        return []

    async def discover_equities(self, _scan_id):
        return []


class TerminalRuntime(CleanupRuntime):
    def __init__(self, outcome: str) -> None:
        super().__init__()
        self.outcome = outcome
        self.run_started = asyncio.Event()
        self.run_release = asyncio.Event()

    async def run(self, _state, *, recover: bool) -> None:
        self.run_started.set()
        await self.run_release.wait()
        if self.outcome == "error":
            raise RuntimeError("Fixture runner failed")


class TeardownBroker:
    """Model an owner that remains alive after connect and disconnect failures."""

    def __init__(self, *, failure: str = "") -> None:
        self.failure = failure
        self.cleanup_allowed = not failure
        self.connected = False
        self.owner_active = False
        self.disconnect_attempts = 0

    async def connect(self) -> None:
        self.connected = True
        self.owner_active = True
        if self.failure == "connect":
            raise RuntimeError("Fixture connect failed")

    async def disconnect(self) -> None:
        self.disconnect_attempts += 1
        if not self.cleanup_allowed:
            raise RuntimeError("Fixture transport is still closing")
        self.connected = False
        self.owner_active = False

    async def get_accounts(self) -> list[Account]:
        if self.failure == "accounts":
            raise RuntimeError("Fixture accounts failed")
        return [Account("fixture-account", "Fixture", "individual", True, "active")]


class PendingConnectBroker(TeardownBroker):
    def __init__(self, *, ignore_cancel: bool = False) -> None:
        super().__init__()
        self.ignore_cancel = ignore_cancel
        self.connect_started = asyncio.Event()
        self.connect_cancelled = asyncio.Event()
        self.connect_release = asyncio.Event()

    async def connect(self) -> None:
        self.owner_active = True
        self.connect_started.set()
        try:
            await self.connect_release.wait()
        except asyncio.CancelledError:
            self.connect_cancelled.set()
            if not self.ignore_cancel:
                raise
            await self.connect_release.wait()
        self.connected = True


class LifecycleAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="grande-lifecycle-check-")
        self.path = Path(self.scratch.name)

    def tearDown(self) -> None:
        self.scratch.cleanup()

    async def _paper_start_invalidation(self, action: str) -> None:
        control = recorded_session(self.path / action, running=False)
        broker = BlockedAccountBroker()
        runtime = BrokerSessionRuntime(self.path / action, control=control,
                                       broker_factory=lambda *_: self.fail("Unexpected broker creation"))
        runtime.broker = broker
        worker = SessionWorker(control, runtime)
        payload = {"source": "broker_quotes", "initial_cash": 1000, "trade_cash": 100,
                   "loop_demo": False, "news_enabled": False, "social_enabled": False,
                   "local_ai_enabled": False, "local_ai_model": ""}
        starting = asyncio.create_task(worker.handle("research_paper_start", payload))
        old_research = None
        try:
            await asyncio.wait_for(broker.read_started.wait(), timeout=1)
            old_research = runtime.research
            if action == "worker_stop":
                await worker.stop()
            elif action == "durable_stop":
                other = WorkerControlStore(self.path / action / "grande_alpha.db")
                try:
                    other.stop()
                finally:
                    other.close()
            elif action == "paper_stop":
                await runtime.stop_paper_research()
            elif action == "research_stop":
                await runtime.stop_research()
            elif action == "research_disable":
                runtime.disable_research()
            elif action == "service_disable":
                old_research.disable()
            elif action == "service_replace":
                runtime.research = None
                runtime._research_service()
            elif action == "service_close":
                await runtime._close_research()
            elif action == "broker_replace":
                runtime.broker = BlockedAccountBroker()
            else:
                raise AssertionError("Unknown lifecycle fixture")
            broker.read_release.set()
            with self.assertRaises(RuntimeError):
                await starting
            self.assertFalse(runtime.research_status()["running"])
            self.assertFalse(old_research.status()["running"])
            self.assertFalse(control.current().running)
        finally:
            broker.read_release.set()
            await asyncio.gather(starting, return_exceptions=True)
            await worker.close()
            if old_research is not None and old_research is not runtime.research:
                await old_research.close()
            control.close()

    async def test_broker_quote_paper_start_cannot_outlive_worker_stop(self) -> None:
        await self._paper_start_invalidation("worker_stop")

    async def test_broker_quote_paper_start_observes_another_clients_stop(self) -> None:
        await self._paper_start_invalidation("durable_stop")

    async def test_broker_quote_paper_start_cannot_restart_stopped_or_replaced_research(self) -> None:
        for action in ("paper_stop", "research_stop", "research_disable", "service_disable",
                       "service_replace", "service_close", "broker_replace"):
            with self.subTest(action=action):
                await self._paper_start_invalidation(action)

    async def test_uninterrupted_broker_quote_paper_start_still_starts_virtual_research(self) -> None:
        control = recorded_session(self.path, running=False)
        broker = BlockedAccountBroker()
        broker.read_release.set()
        runtime = BrokerSessionRuntime(self.path, control=control)
        runtime.broker = broker
        try:
            result = await runtime.start_paper_research(control.current(), {
                "source": "broker_quotes", "initial_cash": 1000, "trade_cash": 100,
                "loop_demo": False, "news_enabled": False, "social_enabled": False,
                "local_ai_enabled": False, "local_ai_model": "",
            })
            self.assertTrue(result["running"])
            self.assertFalse(result["orders_available"])
            self.assertFalse(control.current().running)
        finally:
            await runtime.close()
            control.close()

    async def test_unexpected_runner_cancellation_clears_saved_recovery_intent(self) -> None:
        for before_entry in (False, True):
            with self.subTest(before_entry=before_entry):
                control = recorded_session(self.path / str(before_entry))
                runtime = TerminalRuntime("return")
                worker = SessionWorker(control, runtime)
                state = control.current()
                try:
                    await worker.recover()
                    task = worker._task
                    if not before_entry:
                        await asyncio.wait_for(runtime.run_started.wait(), timeout=1)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    self.assertIsNone(worker._task)
                    self.assertFalse(worker.status()["running"])
                    self.assertEqual(control.current().generation, state.generation + 1)
                    restarted = SessionWorker(control, TerminalRuntime("return"))
                    await restarted.recover()
                    self.assertIsNone(restarted._task)
                finally:
                    await worker.close()
                    control.close()

    async def test_terminal_outcomes_fence_their_own_running_generation(self) -> None:
        for outcome in ("return", "error"):
            with self.subTest(outcome=outcome):
                control = recorded_session(self.path / outcome)
                runtime = TerminalRuntime(outcome)
                worker = SessionWorker(control, runtime)
                state = control.current()
                try:
                    await worker.recover()
                    task = worker._task
                    runtime.run_release.set()
                    await asyncio.gather(task, return_exceptions=True)
                    self.assertFalse(control.current().running)
                    self.assertEqual(control.current().generation, state.generation + 1)
                    self.assertEqual(worker.status()["phase"], "Error" if outcome == "error" else "Stopped")
                finally:
                    await worker.close()
                    control.close()

    async def test_explicit_stop_cancellation_does_not_advance_generation_twice(self) -> None:
        control = recorded_session(self.path)
        runtime = TerminalRuntime("return")
        worker = SessionWorker(control, runtime)
        try:
            await worker.recover()
            await asyncio.wait_for(runtime.run_started.wait(), timeout=1)
            generation = control.current().generation
            await worker.stop()
            self.assertFalse(control.current().running)
            self.assertEqual(control.current().generation, generation + 1)
            self.assertIsNone(worker._task)
        finally:
            await worker.close()
            control.close()

    async def test_stale_terminal_cleanup_cannot_fence_a_newer_running_generation(self) -> None:
        for outcome in ("return", "error", "cancel"):
            with self.subTest(outcome=outcome):
                control = recorded_session(self.path / outcome)
                runtime = TerminalRuntime(outcome)
                worker = SessionWorker(control, runtime)
                try:
                    await worker.recover()
                    task = worker._task
                    await asyncio.wait_for(runtime.run_started.wait(), timeout=1)
                    control.stop()
                    newer = control.start("fixture-scope", expected_generation=control.current().generation)
                    if outcome == "cancel":
                        task.cancel()
                    else:
                        runtime.run_release.set()
                    await asyncio.gather(task, return_exceptions=True)
                    self.assertEqual(control.current(), newer)
                finally:
                    await worker.close()
                    control.close()

    async def test_terminal_fence_is_atomic_with_another_clients_new_start(self) -> None:
        control = recorded_session(self.path)
        other = WorkerControlStore(self.path / "grande_alpha.db")
        runtime = TerminalRuntime("return")
        worker = SessionWorker(control, runtime)
        newer = None

        def replace_before_write(statement: str) -> None:
            nonlocal newer
            if statement.strip().upper() == "BEGIN" and newer is None:
                other.stop()
                newer = other.start("fixture-scope", expected_generation=other.current().generation)

        try:
            await worker.recover()
            task = worker._task
            await asyncio.wait_for(runtime.run_started.wait(), timeout=1)
            control._db.set_trace_callback(replace_before_write)
            runtime.run_release.set()
            await asyncio.gather(task, return_exceptions=True)
            self.assertIsNotNone(newer)
            self.assertEqual(other.current(), newer)
        finally:
            control._db.set_trace_callback(None)
            await worker.close()
            other.close()
            control.close()

    async def test_failed_connect_retains_its_owner_until_teardown_succeeds(self) -> None:
        for failure in ("connect", "contract", "accounts"):
            with self.subTest(failure=failure):
                first, second = TeardownBroker(failure=failure), TeardownBroker()
                created = []

                def factory(_interactive, *, brokers=(first, second), created=created):
                    broker = brokers[len(created)]
                    self.assertFalse(any(item.owner_active for item in created), "Two broker owners would overlap")
                    created.append(broker)
                    return broker

                def validate(broker, *, first=first, failure=failure):
                    if broker is first and failure == "contract":
                        raise RuntimeError("Fixture contract failed")

                runtime = BrokerSessionRuntime(self.path, broker_factory=factory, contract_validator=validate)
                try:
                    with self.assertRaisesRegex(RuntimeError, f"Fixture {failure} failed"):
                        await runtime.connect(False)
                    self.assertTrue(first.connected)
                    self.assertIs(runtime.broker, first)
                    self.assertFalse(runtime.connected)
                    with self.assertRaisesRegex(RuntimeError, "still closing"):
                        await runtime.connect(False)
                    self.assertEqual(len(created), 1)
                    self.assertIs(runtime.broker, first)
                    self.assertEqual(first.disconnect_attempts, 2)
                    first.cleanup_allowed = True
                    result = await runtime.connect(False)
                    self.assertTrue(result["connected"])
                    self.assertEqual(len(created), 2)
                    self.assertFalse(first.owner_active)
                    self.assertIs(runtime.broker, second)
                finally:
                    first.cleanup_allowed = True
                    await runtime.close()

    async def test_failed_close_keeps_owner_available_for_a_later_cleanup_retry(self) -> None:
        broker = TeardownBroker(failure="connect")
        broker.connected = True
        broker.owner_active = True
        runtime = BrokerSessionRuntime(self.path)
        runtime.broker = broker
        try:
            await runtime.close()
            self.assertIs(runtime.broker, broker)
            self.assertFalse(runtime.connected)
            broker.cleanup_allowed = True
            await runtime.close()
            self.assertIsNone(runtime.broker)
            self.assertFalse(broker.owner_active)
        finally:
            broker.cleanup_allowed = True
            await runtime.close()

    async def test_shutdown_during_interactive_connect_closes_without_waiting_for_login(self) -> None:
        control = recorded_session(self.path, running=False)
        broker = PendingConnectBroker()
        created = []

        def factory(_interactive):
            self.assertFalse(created, "Shutdown must not create a replacement broker")
            created.append(broker)
            return broker

        runtime = BrokerSessionRuntime(self.path, control=control,
                                       broker_factory=factory, contract_validator=lambda _broker: None)
        worker = SessionWorker(control, runtime)
        connecting = asyncio.create_task(worker.handle("connect", {}))
        queued = None
        try:
            await asyncio.wait_for(broker.connect_started.wait(), timeout=1)
            queued = asyncio.create_task(runtime.connect())
            await asyncio.sleep(0)
            result = await worker.handle("shutdown", {})
            self.assertTrue(result["process_exiting"])
            await asyncio.wait_for(worker.close(), timeout=1)
            self.assertTrue(connecting.done())
            self.assertTrue(broker.connect_cancelled.is_set())
            self.assertGreater(broker.disconnect_attempts, 0)
            self.assertFalse(broker.owner_active)
            self.assertFalse(runtime.connected)
            self.assertIsNone(runtime.broker)
            queued_result, = await asyncio.gather(queued, return_exceptions=True)
            self.assertIsInstance(queued_result, RuntimeError)
            self.assertEqual(len(created), 1)
            with self.assertRaises(RuntimeError):
                await runtime.connect()
        finally:
            broker.connect_release.set()
            await asyncio.gather(connecting, *([queued] if queued is not None else []), return_exceptions=True)
            await worker.close()
            control.close()

    async def test_close_is_bounded_when_connect_ignores_cancellation_and_rejects_late_success(self) -> None:
        broker = PendingConnectBroker(ignore_cancel=True)
        created = []

        def factory(_interactive):
            created.append(broker)
            return broker

        runtime = BrokerSessionRuntime(self.path, broker_factory=factory, contract_validator=lambda _broker: None)
        connecting = asyncio.create_task(runtime.connect())
        closing = None
        try:
            await asyncio.wait_for(broker.connect_started.wait(), timeout=1)
            with patch("grande_alpha.execution.session_runtime.CONNECT_CLOSE_SECONDS", .02, create=True):
                closing = asyncio.create_task(runtime.close())
                done, _ = await asyncio.wait({closing}, timeout=1)
                self.assertIn(closing, done, "Close must not wait indefinitely for a cancelled Connect")
                with self.assertRaisesRegex(RuntimeError, "still closing"):
                    await closing
            self.assertTrue(broker.connect_cancelled.is_set())
            self.assertIs(runtime.broker, broker)
            self.assertFalse(runtime.connected)
            with self.assertRaises(RuntimeError):
                await runtime.connect()
            broker.connect_release.set()
            result, = await asyncio.gather(connecting, return_exceptions=True)
            self.assertIsInstance(result, RuntimeError)
            await runtime.close()
            self.assertEqual(len(created), 1)
            self.assertFalse(broker.owner_active)
            self.assertFalse(runtime.connected)
            self.assertIsNone(runtime.broker)
        finally:
            if closing is not None and not closing.done():
                closing.cancel()
            broker.connect_release.set()
            await asyncio.gather(connecting, *([closing] if closing is not None else []), return_exceptions=True)
            await runtime.close()


class RecoveryAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        for name in ("segoeui.ttf", "segoeuib.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        cls.app.setFont(QFont("Segoe UI", 10))

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="grande-recovery-check-")
        self.path = Path(self.scratch.name)
        self.window = SessionWindow(self.path, client_factory=UnreachableClient, auto_probe=False)
        self.window.status_timer.stop()

    def tearDown(self) -> None:
        self.window._closing = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.scratch.cleanup()

    def test_timeout_keeps_activity_unknown_and_controls_locked(self) -> None:
        control = recorded_session(self.path)
        try:
            self.window._apply_status(live_status())
            asyncio.run(self.window._refresh_status(show_errors=False))
            self.assertIn("Unknown", self.window.session_card.value.text())
            self.assertEqual(self.window.worker_card.value.text(), "OFFLINE")
            self.assertFalse(self.window.start_button.isEnabled())
            self.assertFalse(self.window.review_button.isEnabled())
            self.assertFalse(self.window.connect_button.isEnabled())
            self.assertNotIn("fixture", self.window.account_card.value.text())
            self.assertFalse(self.window.monitor_panel.isHidden())
            self.assertIn("unverified", self.window.research_status.text().lower())
        finally:
            control.close()

    def test_stop_does_not_create_control_in_unrelated_database(self) -> None:
        database = self.path / "grande_alpha.db"
        with contextlib.closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.execute("INSERT INTO unrelated VALUES ('preserve me')")
            connection.commit()
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        with self.assertRaises(RuntimeError):
            self.window._write_durable_stop_fence()
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_cli_unreachable_status_is_structured_and_read_only(self) -> None:
        control = recorded_session(self.path)
        control.close()
        database = self.path / "grande_alpha.db"
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        output = io.StringIO()
        with patch.object(worker_cli, "data_dir", return_value=self.path), \
             patch.object(worker_cli, "LocalControlClient", UnreachableClient), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(["session", "status"])
        self.assertEqual(result, 1)
        status = json.loads(output.getvalue())
        self.assertIs(status["running"], None)
        self.assertIs(status["worker_reachable"], False)
        self.assertIs(status["recorded_running"], True)
        self.assertIs(status["broker_verified"], False)
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_fallback_stop_remains_offline_and_rejects_stale_approval(self) -> None:
        control = recorded_session(self.path)
        try:
            self.window._apply_status(live_status())
            self.window._review_result = {"scope_digest": "fixture-scope", "generation": 2}
            asyncio.run(self.window._stop())
            self.assertFalse(control.current().running)
            self.assertEqual(self.window.worker_card.value.text(), "OFFLINE")
            self.assertFalse(self.window.start_button.isEnabled())
            self.assertIsNone(self.window._review_result)
            asyncio.run(self.window._stop())
            self.assertFalse(control.current().running)
        finally:
            control.close()

    def test_another_client_stop_wins_over_in_progress_start(self) -> None:
        starter = recorded_session(self.path, running=False)
        stopper = WorkerControlStore(self.path / "grande_alpha.db")
        generation = starter.current().generation
        interrupted = False

        def stop_before_write(statement: str) -> None:
            nonlocal interrupted
            # SQLite begins the write transaction after Start's SELECT has returned.
            if statement.strip().upper() == "BEGIN" and not interrupted:
                interrupted = True
                stopper.stop()

        try:
            starter._db.set_trace_callback(stop_before_write)
            with self.assertRaises(RuntimeError):
                starter.start("fixture-scope", expected_generation=generation)
            self.assertTrue(interrupted)
            self.assertFalse(stopper.current().running)
        finally:
            starter._db.set_trace_callback(None)
            starter.close()
            stopper.close()

    def test_start_is_rejected_during_stop_cleanup(self) -> None:
        async def scenario() -> None:
            control = recorded_session(self.path, running=False)
            runtime = CleanupRuntime(block_stop=True)
            worker = SessionWorker(control, runtime)
            stopping = asyncio.create_task(worker.handle("stop", {}))
            try:
                await runtime.cleanup_started.wait()
                with self.assertRaises(RuntimeError):
                    await worker.handle("start", {
                        "scope_digest": "fixture-scope",
                        "expected_generation": control.current().generation,
                    })
                self.assertFalse(control.current().running)
            finally:
                runtime.cleanup_release.set()
                await stopping
                await worker.close()
                control.close()
        asyncio.run(scenario())

    def test_start_is_rejected_during_shutdown_cleanup(self) -> None:
        async def scenario() -> None:
            control = recorded_session(self.path, running=False)
            runtime = CleanupRuntime()
            worker = SessionWorker(control, runtime)
            shutdown = asyncio.create_task(worker.handle("shutdown", {}))
            try:
                await runtime.cleanup_started.wait()
                with self.assertRaises(RuntimeError):
                    await worker.handle("start", {
                        "scope_digest": "fixture-scope",
                        "expected_generation": control.current().generation,
                    })
                self.assertFalse(control.current().running)
            finally:
                runtime.cleanup_release.set()
                await shutdown
                await worker.close()
                control.close()
        asyncio.run(scenario())

    def test_missing_status_never_creates_a_database(self) -> None:
        status = disconnected_worker_status(self.path)
        self.assertEqual(status["control_record"], "missing")
        self.assertIsNone(status["running"])
        self.assertFalse((self.path / "grande_alpha.db").exists())
        self.assertEqual(list(self.path.iterdir()), [])

    def test_invalid_or_missing_record_is_unverified_and_preserved(self) -> None:
        database = self.path / "grande_alpha.db"
        cases = (
            "CREATE TABLE unrelated (value TEXT)",
            "CREATE TABLE worker_control (id INTEGER, generation INTEGER, running INTEGER)",
            "CREATE TABLE worker_control (id INTEGER, generation INTEGER, running INTEGER);"
            "INSERT INTO worker_control VALUES (1, 1, 2)",
        )
        for statement in cases:
            with self.subTest(statement=statement):
                database.unlink(missing_ok=True)
                with contextlib.closing(sqlite3.connect(database)) as connection:
                    connection.executescript(statement)
                    connection.commit()
                before = database.read_bytes()
                status = disconnected_worker_status(self.path)
                self.assertEqual(status["control_record"], "unavailable")
                self.assertIsNone(status["recorded_running"])
                self.assertFalse(status["local_stop_recorded"])
                with self.assertRaises(RuntimeError):
                    stop_existing_worker(self.path)
                self.assertEqual(database.read_bytes(), before)

    def test_restart_recognizes_saved_stop_without_claiming_worker_contact(self) -> None:
        control = recorded_session(self.path)
        control.close()
        stopped = stop_existing_worker(self.path)
        status = disconnected_worker_status(self.path)
        self.assertEqual(status["generation"], stopped["generation"])
        self.assertIs(status["recorded_running"], False)
        self.assertIsNone(status["running"])
        self.window._apply_disconnected_status(status)
        self.assertEqual(self.window.worker_card.value.text(), "OFFLINE")
        self.assertTrue(self.window.connect_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())

    def test_offline_connect_handler_cannot_launch_saved_running_intent(self) -> None:
        control = recorded_session(self.path)
        try:
            self.window._apply_disconnected_status(disconnected_worker_status(self.path))
            def unsafe_launcher(*_args, **_kwargs):
                self.fail("Disconnected Connect must not launch a worker with running intent")
            self.window._launcher = unsafe_launcher
            asyncio.run(self.window._connect())
            self.assertTrue(control.current().running)
            self.assertFalse(self.window._connect_action.isEnabled())
        finally:
            control.close()

    def test_cold_offline_close_offers_stop_for_existing_running_record(self) -> None:
        control = recorded_session(self.path)
        try:
            asyncio.run(self.window._refresh_status(show_errors=False))
            self.window._ask_exit_action = lambda: "cancel"
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertTrue(control.current().running)
        finally:
            control.close()

    def test_cold_offline_exit_writes_fence_and_warns_broker_unverified(self) -> None:
        control = recorded_session(self.path)
        try:
            asyncio.run(self.window._refresh_status(show_errors=False))
            warnings = []
            self.window._show_exit_warning_then_close = lambda message, **options: warnings.append((message, options))
            asyncio.run(self.window._shutdown_then_close())
            self.assertFalse(control.current().running)
            self.assertTrue(warnings)
            self.assertTrue(warnings[0][1]["stop_confirmed"])
            self.assertIn("not verified", warnings[0][0])
        finally:
            control.close()

    def test_exit_failure_never_claims_a_confirmed_stop(self) -> None:
        (self.path / "grande_alpha.db").write_bytes(b"invalid database")
        asyncio.run(self.window._refresh_status(show_errors=False))
        warnings = []
        self.window._show_exit_warning_then_close = lambda message, **options: warnings.append((message, options))
        asyncio.run(self.window._shutdown_then_close())
        self.assertFalse(warnings[0][1]["stop_confirmed"])
        self.assertIn("unavailable", warnings[0][0])

    def test_probe_started_before_stop_cannot_replace_stop_projection(self) -> None:
        async def scenario() -> None:
            control = recorded_session(self.path)
            self.window._apply_status(live_status())
            entered, release = asyncio.Event(), asyncio.Event()
            original_request = self.window._request
            async def delayed_status(operation, payload, *, timeout_seconds=10):
                if operation == "status":
                    entered.set()
                    await release.wait()
                    return live_status()
                return await original_request(operation, payload, timeout_seconds=timeout_seconds)
            self.window._request = delayed_status
            probe = asyncio.create_task(self.window._refresh_status(show_errors=False))
            try:
                await entered.wait()
                await self.window._stop()
                release.set()
                await probe
                self.assertIs(self.window._status["local_stop_recorded"], True)
                self.assertIs(self.window._status["worker_reachable"], False)
                self.assertFalse(control.current().running)
            finally:
                release.set()
                await probe
                control.close()
        asyncio.run(scenario())

    def test_reconnect_requires_new_review_and_rejects_older_generations(self) -> None:
        control = recorded_session(self.path)
        try:
            self.window._apply_status(live_status())
            self.window._review_result = {"scope_digest": "fixture-scope", "generation": 2}
            self.window._apply_disconnected_status(stop_existing_worker(self.path))
            with self.assertRaises(RuntimeError):
                self.window._apply_status(live_status())
            recovered = {**live_status(), "generation": 3, "running": False, "phase": "Stopped"}
            self.window._apply_status(recovered)
            self.assertEqual(self.window.worker_card.value.text(), "READY")
            self.assertEqual(self.window.session_card.value.accessibleDescription(), "Stopped")
            self.assertEqual(self.window.session_card.value.toolTip(), "Stopped")
            self.assertIsNone(self.window._review_result)
            self.assertFalse(self.window.start_button.isEnabled())
            self.assertTrue(self.window.connect_button.isEnabled())
            self.assertNotIn("unverified", self.window.research_status.text().lower())
        finally:
            control.close()

    def test_actual_local_channel_reconnects_after_a_bounded_timeout(self) -> None:
        async def scenario() -> None:
            control = recorded_session(self.path, running=False)
            runtime = CleanupRuntime()
            worker = SessionWorker(control, runtime)
            stalled = False
            async def handler(operation: str, payload: dict) -> dict:
                if stalled and operation == "status":
                    await asyncio.Event().wait()
                return await worker.handle(operation, payload)
            server = LocalControlServer(self.path, handler)
            # The isolated test channel uses an in-memory token; credential stores remain untouched.
            with patch("grande_alpha.execution.worker_ipc._token", return_value="a" * 64):
                try:
                    await server.start()
                    self.window._client = LocalControlClient(self.path)
                    await self.window._refresh_status(show_errors=False)
                    self.assertIs(self.window._status["worker_reachable"], True)
                    stalled = True
                    await self.window._refresh_status(show_errors=False)
                    self.assertIs(self.window._status["worker_reachable"], False)
                    self.assertIsNone(self.window._status["running"])
                    stalled = False
                    await self.window._refresh_status(show_errors=False)
                    self.assertIs(self.window._status["worker_reachable"], True)
                    await self.window._stop()
                    self.assertFalse(control.current().running)
                finally:
                    await server.close()
                    await worker.close()
                    control.close()
        asyncio.run(scenario())

    def test_status_poll_cannot_erase_a_newer_completed_review(self) -> None:
        async def scenario() -> None:
            self.window._apply_status({**live_status(), "running": False, "generation": 1})
            candidate = self.path / "candidate.json"
            write_candidate(candidate)
            self.window._load_candidate(candidate)
            entered, release = asyncio.Event(), asyncio.Event()
            async def request(operation, payload, *, timeout_seconds=10):
                if operation == "status":
                    entered.set()
                    await release.wait()
                    return {**live_status(), "running": False, "generation": 1}
                if operation == "review":
                    return {"scope_digest": "fixture-scope", "generation": 2}
                raise AssertionError("Unexpected operation")
            self.window._request = request
            probe = asyncio.create_task(self.window._refresh_status(show_errors=False))
            try:
                await entered.wait()
                await self.window._review()
                self.assertIsNotNone(self.window._review_result)
                release.set()
                await probe
                self.assertIsNotNone(self.window._review_result)
                self.assertEqual(self.window._review_result["generation"], 2)
            finally:
                release.set()
                await probe
        asyncio.run(scenario())

    def test_cancelled_shutdown_still_requests_exit(self) -> None:
        async def scenario() -> None:
            control = recorded_session(self.path, running=False)
            runtime = CleanupRuntime(block_stop=True)
            worker = SessionWorker(control, runtime)
            shutdown = asyncio.create_task(worker.handle("shutdown", {}))
            try:
                await runtime.cleanup_started.wait()
                shutdown.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await shutdown
                self.assertTrue(worker.shutdown_requested.is_set())
                self.assertFalse(control.current().running)
            finally:
                runtime.cleanup_release.set()
                await worker.close()
                control.close()
        asyncio.run(scenario())

    def test_read_only_status_observes_current_wal_intent(self) -> None:
        control = recorded_session(self.path, running=False)
        try:
            control._db.execute("PRAGMA journal_mode=WAL")
            state = control.start("fixture-scope", expected_generation=control.current().generation)
            database = self.path / "grande_alpha.db"
            before = database.read_bytes()
            status = disconnected_worker_status(self.path)
            self.assertEqual(status["generation"], state.generation)
            self.assertIs(status["recorded_running"], True)
            self.assertIsNone(status["connected"])
            self.assertEqual(database.read_bytes(), before)
            stop_existing_worker(self.path)
            self.assertFalse(control.current().running)
        finally:
            control.close()

    def test_shutdown_fence_failure_never_returns_stopped_success(self) -> None:
        async def scenario() -> None:
            control = recorded_session(self.path)
            control._db.execute("PRAGMA busy_timeout=25")
            locker = sqlite3.connect(self.path / "grande_alpha.db")
            locker.execute("BEGIN IMMEDIATE")
            worker = SessionWorker(control, CleanupRuntime())
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    await worker.handle("shutdown", {})
                self.assertTrue(worker.shutdown_requested.is_set())
                self.assertTrue(control.current().running)
            finally:
                locker.rollback()
                locker.close()
                worker.runtime.cleanup_release.set()
                await worker.close()
                control.close()
        asyncio.run(scenario())

    def test_narrow_recovery_uses_full_width_and_shows_complete_status(self) -> None:
        control = recorded_session(self.path)
        try:
            self.window._apply_status(live_status())
            asyncio.run(self.window._refresh_status(show_errors=False))
            self.window.show()
            self.window.resize(1120, 780)
            self.app.processEvents()
            self.window.resize(390, 650)
            self.app.processEvents()
            self.assertEqual(self.window.desk_scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreaterEqual(
                self.window.desk.width(), self.window.desk_scroll.viewport().width() - 40
            )
            value = self.window.status_notice
            text_height = value.fontMetrics().boundingRect(
                QRect(0, 0, value.width(), 1000), Qt.TextFlag.TextWordWrap, value.text()
            ).height()
            self.assertLessEqual(text_height, value.height())
        finally:
            control.close()

    def test_review_approval_start_and_stop_through_real_local_channel(self) -> None:
        async def scenario() -> None:
            control = WorkerControlStore(self.path / "grande_alpha.db")
            runtime = CleanupRuntime()
            runtime.connected = True
            worker = SessionWorker(control, runtime)
            server = LocalControlServer(self.path, worker.handle)
            candidate = self.path / "candidate.json"
            write_candidate(candidate)
            self.window._load_candidate(candidate)
            with patch("grande_alpha.execution.worker_ipc._token", return_value="b" * 64):
                try:
                    await server.start()
                    self.window._client = LocalControlClient(self.path)
                    await self.window._refresh_status(show_errors=False)
                    await self.window._review()
                    self.assertTrue(self.window.start_button.isEnabled())
                    await self.window._authorize_and_start("AUTHORIZE fixture-scope")
                    self.assertTrue(control.current().running)
                    self.assertEqual(self.window.worker_card.value.text(), "RUNNING")
                    self.assertFalse(self.window.start_button.isEnabled())
                    await self.window._stop()
                    self.assertFalse(control.current().running)
                    self.assertTrue(self.window.review_button.isEnabled())
                    self.assertFalse(self.window.start_button.isEnabled())
                finally:
                    await server.close()
                    await worker.close()
                    control.close()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)

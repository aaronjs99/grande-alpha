from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from grande_alpha.execution.worker_control import WorkerControlStore
from grande_alpha.ui.session_window import SessionWindow


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = {
            "running": False,
            "phase": "Idle",
            "account_masked": "",
            "account": "",
            "scope_digest": "",
            "connected": False,
            "research": {
                "enabled": False,
                "running": False,
                "phase": "Off",
                "bridge_path": None,
                "orders_available": False,
            },
            "data_coverage": {},
            "error": "",
            "generation": 0,
        }

    def request(self, operation: str, payload: dict, timeout_seconds: float = 10) -> dict:
        del timeout_seconds
        self.calls.append((operation, payload))
        if operation == "status":
            return dict(self.status)
        if operation == "connect":
            self.status.update(connected=True, phase="Connected")
            return {
                "connected": True,
                "accounts": [{"masked": "Agentic ••••8900", "agentic_allowed": True}],
            }
        if operation == "review":
            self.status.update(
                phase="Reviewed", account="account-1", account_masked="Agentic ••••8900",
                scope_digest="scope-123", generation=1,
            )
            return {
                "account": "account-1",
                "account_masked": "Agentic ••••8900",
                "scope_digest": "scope-123",
                "generation": 1,
                "allowed_symbols": ["TQQQ", "SQQQ"],
                "limits": {
                    "max_order_usd": 10.0,
                    "max_exposure_usd": 20.0,
                    "max_daily_notional_usd": 30.0,
                    "max_daily_loss_usd": 2.0,
                    "max_orders": 4,
                    "max_orders_per_minute": 1,
                    "max_quote_age_seconds": 5.0,
                    "max_spread_bps": 15.0,
                },
                "allocation_policy": {
                    "max_invested_fraction": 0.8,
                    "max_etf_fraction": 0.2,
                },
                "earnings_thresholds": {"min_surprise_bps": 100.0},
                "starts_at": "2026-09-24T09:30:00-04:00",
                "expires_at": "2026-09-24T16:00:00-04:00",
                "orders_submitted": False,
            }
        if operation == "authorize":
            assert payload == {"phrase": "AUTHORIZE scope-123"}
            self.status["phase"] = "Authorized"
            return {"authorized": True, "scope_digest": "scope-123"}
        if operation == "start":
            assert payload == {"scope_digest": "scope-123", "expected_generation": 1}
            self.status.update(running=True, phase="Running", generation=2)
            return dict(self.status)
        if operation == "stop":
            self.status.update(running=False, phase="Stopped", generation=2)
            self.status["research"] = {
                "enabled": False, "running": False, "phase": "Off",
                "bridge_path": None, "orders_available": False,
            }
            return dict(self.status)
        if operation == "research_enable":
            self.status["research"] = {
                "enabled": True,
                "running": True,
                "phase": "Running",
                "bridge_path": "local-research-bridge",
                "orders_available": False,
            }
            return dict(self.status["research"])
        if operation == "research_disable":
            self.status["research"] = {
                "enabled": False,
                "running": False,
                "phase": "Off",
                "bridge_path": None,
                "orders_available": False,
            }
            return dict(self.status["research"])
        if operation == "shutdown":
            self.status.update(running=False, phase="Stopped", generation=3)
            return {
                "stopped": True,
                "process_exiting": True,
                "broker_verified": True,
                "open_order_ids": [],
                "positions": [],
                "local_unresolved_references": [],
            }
        raise AssertionError(operation)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


async def _wait_until(predicate, timeout: float = 2) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("Timed out waiting for Qt action")
        QApplication.processEvents()
        await asyncio.sleep(0.01)


def _candidate(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": {
                    "account_number": "account-1",
                    "allowed_symbols": ["TQQQ", "SQQQ"],
                    "starts_at": "2026-09-24T09:30:00-04:00",
                    "expires_at": "2026-09-24T16:00:00-04:00",
                    "max_order_usd": 10.0,
                    "max_exposure_usd": 20.0,
                    "max_daily_notional_usd": 30.0,
                    "max_daily_loss_usd": 2.0,
                    "max_orders": 4,
                    "max_quote_age_seconds": 5.0,
                    "max_spread_bps": 15.0,
                    "max_orders_per_minute": 1,
                    "loss_recovery_delay": None,
                    "loss_recovery_unit": "manual",
                },
                "allocation_policy": {},
                "earnings_thresholds": {},
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_guided_worker_only_flow_uses_exact_local_control_operations(tmp_path: Path) -> None:
    _app()
    client = FakeClient()
    launch_calls = []

    def launcher(path, **kwargs):
        launch_calls.append((path, kwargs))
        return client

    window = SessionWindow(
        tmp_path, launcher=launcher, client_factory=lambda _path: client, auto_probe=False
    )
    try:
        window.connect_button.click()
        await _wait_until(lambda: window._status.get("connected") is True)
        candidate = _candidate(tmp_path / "candidate.json")
        window._load_candidate(candidate)
        assert window.review_button.isEnabled()

        window.review_button.click()
        await _wait_until(lambda: window._review_result is not None and not window._busy)
        assert window.start_button.isEnabled()
        assert "Allocation policy" in window.review_summary.toPlainText()
        assert "Earnings thresholds" in window.review_summary.toPlainText()
        window.start_button.click()
        QApplication.processEvents()
        assert window._approval_dialog is not None
        window._approval_dialog.entry.setText("AUTHORIZE scope-123")
        window._approval_dialog.approve_button.click()
        await _wait_until(lambda: window._status.get("running") is True)

        assert launch_calls == [(tmp_path, {"timeout_seconds": 12})]
        operations = [name for name, _payload in client.calls]
        assert operations == ["connect", "status", "review", "status", "authorize", "start"]
        assert window.session_card.value.text() == "Running"
        assert window.stop_button.isVisible() is False  # The window was not shown in this off-screen test.
        assert not window.stop_button.isHidden()
    finally:
        window._closing = True
        window.close()


@pytest.mark.asyncio
async def test_stop_uses_durable_local_fence_when_ipc_is_unavailable(tmp_path: Path) -> None:
    _app()
    control = WorkerControlStore(tmp_path / "grande_alpha.db")
    try:
        reviewed = control.review(
            account="account-1",
            scope_digest="scope-1",
            candidate_path="candidate.json",
            authorization_path="authorization.json",
            earnings_database="earnings.db",
            poll_seconds=5.0,
        )
        control.start("scope-1", expected_generation=reviewed.generation)
    finally:
        control.close()

    class UnavailableClient:
        def request(self, *_args, **_kwargs):
            raise OSError("pipe unavailable")

    window = SessionWindow(
        tmp_path, client_factory=lambda _path: UnavailableClient(), auto_probe=False
    )
    try:
        await window._stop()
        check = WorkerControlStore(tmp_path / "grande_alpha.db")
        try:
            assert check.current().running is False
        finally:
            check.close()
        assert "durable local stop fence was written" in window.activity.item(0).text()
    finally:
        window._closing = True
        window.close()


@pytest.mark.asyncio
async def test_optional_research_bridge_is_worker_hosted_and_orderless(tmp_path: Path) -> None:
    _app()
    client = FakeClient()
    client.status.update(connected=True, account="account-1", phase="Reviewed")
    window = SessionWindow(tmp_path, client_factory=lambda _path: client, auto_probe=False)
    try:
        window._apply_status(dict(client.status))
        window.research_disclosure.click()
        assert window.research_panel.isHidden() is False
        await window._toggle_research()
        assert ("research_enable", {}) in client.calls
        assert "orders unavailable" in window.research_status.text()
        assert window._status["research"]["orders_available"] is False
        await window._toggle_research()
        assert ("research_disable", {}) in client.calls
    finally:
        window._closing = True
        window.close()


def test_running_view_reports_cycle_and_data_freshness_without_claiming_a_fill(tmp_path: Path) -> None:
    _app()
    client = FakeClient()
    window = SessionWindow(tmp_path, client_factory=lambda _path: client, auto_probe=False)
    try:
        window._apply_status({
            **client.status,
            "running": True,
            "phase": "Running",
            "last_data_at": (datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
            "last_cycle": {
                "status": "RESPONSE_RECORDED",
                "submitted": True,
                "at": (datetime.now(UTC) - timedelta(seconds=8)).isoformat(),
            },
        })

        assert window.flow_panel.isHidden()
        assert not window.monitor_panel.isHidden()
        assert window.monitor_cycle.text() == "Broker response recorded"
        assert "not a fill confirmation" in window.monitor_cycle_time.text()
        assert "seconds ago" in window.monitor_data.text()

        window._apply_responsive_layout(520, 900, force=True)
        assert window.content_layout.getItemPosition(
            window.content_layout.indexOf(window.monitor_panel)
        ) == (0, 0, 1, 1)
        assert window.content_layout.getItemPosition(
            window.content_layout.indexOf(window.activity_panel)
        ) == (1, 0, 1, 1)

        window._apply_status(dict(client.status))
        assert window.flow_panel.isHidden() is False
        assert window.monitor_panel.isHidden() is True
    finally:
        window._closing = True
        window.close()


@pytest.mark.asyncio
async def test_exit_falls_back_to_durable_fence_and_never_vetoes_close(tmp_path: Path) -> None:
    _app()
    control = WorkerControlStore(tmp_path / "grande_alpha.db")
    try:
        reviewed = control.review(
            account="account-1",
            scope_digest="scope-1",
            candidate_path="candidate.json",
            authorization_path="authorization.json",
            earnings_database="earnings.db",
            poll_seconds=5.0,
        )
        control.start("scope-1", expected_generation=reviewed.generation)
    finally:
        control.close()

    class UnavailableClient:
        def request(self, *_args, **_kwargs):
            raise OSError("pipe unavailable")

    window = SessionWindow(
        tmp_path, client_factory=lambda _path: UnavailableClient(), auto_probe=False
    )
    warnings = []
    window._worker_seen = True
    window._show_exit_warning_then_close = warnings.append
    try:
        await window._shutdown_then_close()
        assert warnings and "durable local stop fence" in warnings[0]
        check = WorkerControlStore(tmp_path / "grande_alpha.db")
        try:
            assert check.current().running is False
        finally:
            check.close()
        assert window._exit_in_flight is False
    finally:
        window._closing = True
        window.close()


@pytest.mark.asyncio
async def test_prominent_stop_does_not_launch_or_create_authority(tmp_path: Path) -> None:
    _app()
    client = FakeClient()

    def forbidden_launcher(*_args, **_kwargs):
        raise AssertionError("Stop must never launch a worker")

    window = SessionWindow(
        tmp_path,
        launcher=forbidden_launcher,
        client_factory=lambda _path: client,
        auto_probe=False,
    )
    try:
        await window._stop()
        assert client.calls == [("stop", {})]
        assert "New local submissions are fenced" in window.activity.item(0).text()
    finally:
        window._closing = True
        window.close()


@pytest.mark.asyncio
async def test_stop_wins_if_start_pipe_request_was_already_in_flight(tmp_path: Path) -> None:
    _app()

    class RacingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.start_entered = threading.Event()
            self.release_start = threading.Event()

        def request(self, operation: str, payload: dict, timeout_seconds: float = 10) -> dict:
            if operation == "start":
                self.calls.append((operation, payload))
                self.start_entered.set()
                assert self.release_start.wait(timeout=3)
                self.status.update(running=True, phase="Running", generation=1)
                return dict(self.status)
            return super().request(operation, payload, timeout_seconds)

    client = RacingClient()
    client.status.update(connected=True, phase="Reviewed", scope_digest="scope-123")
    window = SessionWindow(tmp_path, client_factory=lambda _path: client, auto_probe=False)
    window._status = dict(client.status)
    window._review_result = {"scope_digest": "scope-123", "generation": 0}
    try:
        start_task = asyncio.create_task(window._authorize_and_start("AUTHORIZE scope-123"))
        assert await asyncio.to_thread(client.start_entered.wait, 2)
        assert window.stop_button.isEnabled()
        await window._stop()
        client.release_start.set()
        await start_task

        assert client.status["running"] is False
        assert [name for name, _payload in client.calls] == [
            "authorize", "start", "stop", "stop"
        ]
    finally:
        client.release_start.set()
        window._closing = True
        window.close()


def test_limits_save_as_preserves_source_and_invalidates_prior_review(tmp_path: Path) -> None:
    _app()
    client = FakeClient()
    source = _candidate(tmp_path / "candidate.json")
    original = source.read_text(encoding="utf-8")
    target = tmp_path / "bounded.json"
    window = SessionWindow(tmp_path, client_factory=lambda _path: client, auto_probe=False)
    try:
        window._load_candidate(source)
        window.limit_inputs["max_order_usd"].setValue(7.0)
        assert window._limits_dirty is True
        window._review_result = {"scope_digest": "old"}
        window._save_limits(target)

        assert source.read_text(encoding="utf-8") == original
        assert json.loads(target.read_text(encoding="utf-8"))["scope"]["max_order_usd"] == 7.0
        assert window._review_result is None
        assert window.candidate_edit.text() == str(target.resolve())

        with pytest.raises(ValueError, match="source candidate is never overwritten"):
            window._save_limits(target)
        existing = tmp_path / "existing.json"
        existing.write_text("keep", encoding="utf-8")
        with pytest.raises(ValueError, match="explicit overwrite confirmation"):
            window._save_limits(existing)
        assert existing.read_text(encoding="utf-8") == "keep"
    finally:
        window._closing = True
        window.close()


def test_layout_reflows_for_landscape_and_portrait(tmp_path: Path) -> None:
    _app()
    client = FakeClient()
    window = SessionWindow(tmp_path, client_factory=lambda _path: client, auto_probe=False)
    try:
        window._apply_responsive_layout(1200, 700, force=True)
        assert window._layout_mode == "landscape"
        assert window.content_layout.getItemPosition(window.content_layout.indexOf(window.activity_panel)) == (
            0, 1, 1, 1
        )

        window._apply_responsive_layout(520, 900, force=True)
        assert window._layout_mode == "portrait"
        assert window.content_layout.getItemPosition(window.content_layout.indexOf(window.activity_panel)) == (
            1, 0, 1, 1
        )
        assert window.status_layout.columnCount() >= 2
    finally:
        window._closing = True
        window.close()


def test_frozen_worker_marker_dispatches_before_desktop_start(monkeypatch) -> None:
    from grande_alpha import app as app_module
    from grande_alpha.execution import worker_process

    calls = []
    monkeypatch.setattr(worker_process, "main", lambda arguments: calls.append(arguments) or 17)
    monkeypatch.setattr(
        sys,
        "argv",
        ["GRANDEAlpha.exe", "--worker", "--data-dir", r"C:\worker-data"],
    )

    assert app_module.main() == 17
    assert calls == [["--data-dir", r"C:\worker-data"]]

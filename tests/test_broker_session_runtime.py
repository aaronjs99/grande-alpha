"""Shared session review never creates an order or changes a permit."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grande_alpha.domain.models import Account
from grande_alpha.execution.equity_ledger import ExecutionLeaseBusy
from grande_alpha.execution.session_runtime import BrokerSessionRuntime
from grande_alpha.execution.worker_control import WorkerControlStore
from grande_alpha.interfaces.cli.autonomous_cli import candidate_template


@pytest.mark.asyncio
async def test_reviewed_candidate_requires_explicit_authorization(tmp_path: Path) -> None:
    candidate = candidate_template()
    candidate["scope"].update({
        "account_number": "agentic-123", "allowed_symbols": ["TQQQ", "SQQQ"],
        "starts_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "expires_at": None, "max_order_usd": 10.0, "max_exposure_usd": 100.0,
        "max_daily_notional_usd": 100.0, "max_daily_loss_usd": 25.0,
        "max_orders": 5, "max_quote_age_seconds": 8.0,
        "max_spread_bps": 20.0, "max_orders_per_minute": 2,
        "loss_recovery_delay": None, "loss_recovery_unit": "manual",
    })
    candidate["earnings_thresholds"] = dict.fromkeys(candidate["earnings_thresholds"], 1.0)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    class Broker:
        connected = False
        placements = 0

        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.connected = False

        async def get_accounts(self):
            return [Account("agentic-123", "Agentic", "cash", True, "active")]

    broker = Broker()
    runtime = BrokerSessionRuntime(
        tmp_path, broker_factory=lambda _interactive: broker,
        contract_validator=lambda _broker: None,
    )
    control = WorkerControlStore(tmp_path / "control.db")
    try:
        summary = await runtime.review({
            "candidate_path": str(candidate_path),
            "authorization_path": str(tmp_path / "authorization.json"),
            "earnings_database": str(tmp_path / "earnings.db"),
            "poll_seconds": 5.0,
        })
        assert summary["account"] == "agentic-123"
        assert summary["scope_digest"]
        assert summary["limits"]["max_daily_loss_usd"] == 25.0
        assert broker.placements == 0
        assert not (tmp_path / "authorization.json").exists()
        state = control.review(
            account=summary["account"], scope_digest=summary["scope_digest"],
            candidate_path=str(candidate_path),
            authorization_path=str(tmp_path / "authorization.json"),
            earnings_database=str(tmp_path / "earnings.db"), poll_seconds=5.0,
        )
        with pytest.raises(FileNotFoundError):
            runtime.permit(state)
        assert runtime.authorize(state)["authorized"] is True
        assert runtime.permit(state) is True
    finally:
        control.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_crash_recovery_waits_for_old_lease_without_restarting_stopped_session(tmp_path, monkeypatch):
    control = WorkerControlStore(tmp_path / "control.db")
    state = control.review(
        account="agentic-123", scope_digest="scope-1", candidate_path="candidate.json",
        authorization_path="permit.json", earnings_database="earnings.db", poll_seconds=5,
    )
    state = control.start("scope-1", expected_generation=state.generation)
    runtime = BrokerSessionRuntime(tmp_path, control=control)
    monkeypatch.setattr(runtime, "permit", lambda _state: True)
    calls = []

    class Engine:
        def recover(self, authority_id):
            calls.append(authority_id)
            if len(calls) == 1:
                raise ExecutionLeaseBusy("Old process lease")

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("grande_alpha.execution.session_runtime.asyncio.sleep", no_wait)
    try:
        await runtime._recover_engine(Engine(), "authority-1", state)
        assert calls == ["authority-1", "authority-1"]
        assert control.current().running is True
        control.stop()
        with pytest.raises(RuntimeError, match="stopped"):
            await runtime._recover_engine(Engine(), "authority-1", state)
    finally:
        control.close()


@pytest.mark.asyncio
async def test_concurrent_connect_uses_one_broker_transport(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    created = []

    class Broker:
        connected = False

        async def connect(self):
            entered.set()
            await release.wait()
            self.connected = True

        async def get_accounts(self):
            return [Account("agentic-123", "Agentic", "cash", True, "active")]

        async def disconnect(self):
            self.connected = False

    def factory(_interactive):
        broker = Broker()
        created.append(broker)
        return broker

    runtime = BrokerSessionRuntime(tmp_path, broker_factory=factory,
                                   contract_validator=lambda _broker: None)
    try:
        first = asyncio.create_task(runtime.connect(False))
        await entered.wait()
        second = asyncio.create_task(runtime.connect(False))
        release.set()
        results = await asyncio.gather(first, second)
        assert all(result["connected"] for result in results)
        assert len(created) == 1
    finally:
        await runtime.close()

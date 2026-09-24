import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict

import httpx
import pytest

from grande_alpha.agent_models import AgentSettings
from grande_alpha.broker.base import BrokerError
from grande_alpha.models import Account
from grande_alpha.paper_worker import PaperQuoteBroker, PaperWorker, instance_lock


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-death integration test")
def test_supervisor_restarts_killed_worker_without_resetting_money(tmp_path):
    from grande_alpha.paper_experiment import ExperimentLedger
    from grande_alpha.paper_worker import child_env, read_status

    book = ExperimentLedger(tmp_path / "experiment.db")
    book.initialize()
    book.add_expense("1", "Power", "power")
    ident = book.state["session_id"]
    book.close()
    (tmp_path / "enabled").touch()
    env = {**child_env(), "XDG_DATA_HOME": str(tmp_path / "isolated-app-data")}
    # On macOS platformdirs ignores XDG; force the read permission off in the child
    # without reading any real config or keychain. The supervisor itself is unchanged.
    wrapper = """
import sys
from pathlib import Path
import grande_alpha.paper_worker as w
original=w.child_command
def command(action,directory):
    if action=='serve':
        return [sys.executable,'-c',"import asyncio,sys; from pathlib import Path; import grande_alpha.paper_worker as w; asyncio.run(w.PaperWorker(Path(sys.argv[1]),permission=lambda:False).run())",str(directory)]
    return original(action,directory)
w.child_command=command
w.supervise(Path(sys.argv[1]))
"""
    process = subprocess.Popen([sys.executable, "-c", wrapper, str(tmp_path)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_for(predicate):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = read_status(tmp_path)
            if state.get("online") and predicate(state):
                return state
            if process.poll() is not None:
                pytest.fail("Supervisor exited unexpectedly")
            time.sleep(.05)
        pytest.fail("Worker did not recover before the deadline")

    try:
        first = wait_for(lambda s: True)
        os.kill(first["worker_pid"], signal.SIGKILL)
        second = wait_for(lambda s: s.get("instance") != first["instance"])
        assert second["paper"]["session_id"] == ident
        assert second["paper"]["equity"] == "99"
        assert second["paper"]["operating_expenses"] == "1"
    finally:
        (tmp_path / "enabled").unlink(missing_ok=True)
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


class OfflineBroker:
    connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def get_accounts(self):
        return [Account("1", "Test", "cash", True, "active", "2", "3", "INDIVIDUAL")]

    async def get_quotes(self, symbols):
        return {}

    async def discover_crypto(self):
        return []

    async def get_crypto_quotes(self, instruments, **kwargs):
        return {}

    async def discover_equities(self, scan_id):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["place_equity_order", "place_crypto_order", "cancel_crypto_order", "preview_crypto_order", "get_portfolio"])
async def test_worker_broker_cannot_dispatch_mutations_or_account_balances(name):
    with pytest.raises(BrokerError, match="market-data reads only"):
        await PaperQuoteBroker()._call(name, {})


def test_single_instance_lock_releases_without_deleting_file(tmp_path):
    path = tmp_path / "lock"
    with instance_lock(path):
        with pytest.raises(RuntimeError):
            with instance_lock(path):
                pytest.fail("Second worker acquired ownership")
    with instance_lock(path):
        assert path.exists()


@pytest.mark.asyncio
async def test_loopback_dashboard_expense_idempotency_auth_and_stop(tmp_path, monkeypatch):
    import grande_alpha.paper_worker as module
    monkeypatch.setattr(module, "PORT", 18767)
    monkeypatch.setattr(module, "URL", "http://127.0.0.1:18767")
    worker = PaperWorker(tmp_path, broker=OfflineBroker(), permission=lambda: False)
    tmp_path.joinpath("enabled").touch()
    task = asyncio.create_task(worker.run())
    try:
        async with httpx.AsyncClient(base_url=module.URL, trust_env=False) as client:
            for _ in range(50):
                try:
                    response = await client.get("/api/status")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(.01)
            assert response.json()["paper"]["initial_cash"] == "100"
            html = await client.get("/")
            assert "Your $100 experiment" in html.text
            assert "frame-ancestors 'none'" in html.headers["content-security-policy"]
            body = {"amount": "10", "note": "Electricity", "id": "e"}
            assert (await client.post("/api/expense", json=body)).status_code == 403
            headers = {"X-Experiment-Token": worker.token, "Origin": "https://other.example"}
            assert (await client.post("/api/expense", json=body, headers=headers)).status_code == 403
            headers["Origin"] = module.URL
            assert (await client.post("/api/expense", json=body, headers=headers)).status_code == 200
            assert (await client.post("/api/expense", json=body, headers=headers)).status_code == 200
            data = (await client.get("/api/status")).json()
            assert data["paper"]["loss_locked"]
            assert data["paper"]["operating_expenses"] == "10"
            assert "account_number" not in json.dumps(data)
            assert (await client.post("/api/resume", json={}, headers=headers)).status_code == 200
            assert worker.book.state["loss_locked"]
            assert (await client.post("/api/stop", json={}, headers=headers)).status_code == 200
        await asyncio.wait_for(task, 3)
        assert json.loads(tmp_path.joinpath("status.json").read_text())["heartbeat"] == 0
    finally:
        tmp_path.joinpath("enabled").unlink(missing_ok=True)
        await asyncio.wait_for(task, 3)


@pytest.mark.asyncio
async def test_reconnect_reuses_same_experiment_and_rejects_different_account(tmp_path):
    broker = OfflineBroker()
    worker = PaperWorker(tmp_path, broker=broker, permission=lambda: True)
    worker.book.add_expense("1.25", "Power", "power")
    ident = worker.book.state["session_id"]
    assert await worker.connect()
    original = worker.book.state["account_fingerprint"]
    worker.book.close()
    worker = PaperWorker(tmp_path, broker=broker, permission=lambda: True)
    assert await worker.connect()
    assert worker.book.state["session_id"] == ident
    assert worker.book.state["operating_expenses"] == "1.25"
    assert worker.book.state["account_fingerprint"] == original

    async def other():
        return [Account("changed", "Test", "cash", True, "active", "2", "3", "INDIVIDUAL")]

    broker.get_accounts = other
    assert not await worker.connect()
    assert worker.phase == "Account changed" and not broker.connected
    worker.book.close()


@pytest.mark.asyncio
async def test_permission_off_never_connects_and_revocation_stops_reads(tmp_path, monkeypatch):
    import grande_alpha.paper_worker as module
    monkeypatch.setattr(module, "PORT", 18768)
    broker = OfflineBroker()
    allowed = False
    calls = []
    original = broker.connect

    async def connect():
        calls.append("connect")
        await original()

    broker.connect = connect
    worker = PaperWorker(tmp_path, broker=broker, permission=lambda: allowed)
    worker.book.state["settings"] = asdict(AgentSettings(equity_symbols=(), crypto_symbols=("BTC-USD",), paper_strategy="adaptive"))
    tmp_path.joinpath("enabled").touch()
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.sleep(.05)
        assert not calls
        allowed = True
        for _ in range(80):
            if worker.runtime.snapshot.running:
                break
            await asyncio.sleep(.05)
        assert calls and worker.runtime.snapshot.running
        allowed = False
        for _ in range(40):
            if not broker.connected and not worker.runtime.snapshot.running:
                break
            await asyncio.sleep(.05)
        assert not broker.connected and not worker.runtime.snapshot.running
    finally:
        tmp_path.joinpath("enabled").unlink(missing_ok=True)
        await asyncio.wait_for(task, 3)

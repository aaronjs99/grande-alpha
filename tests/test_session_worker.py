"""The worker keeps local stop state ahead of broker cleanup."""

import asyncio
from pathlib import Path

import pytest

from grande_alpha.execution.session_worker import SessionWorker
from grande_alpha.execution.worker_control import WorkerControlStore


@pytest.mark.asyncio
async def test_stop_is_durable_before_runner_is_cancelled(tmp_path: Path) -> None:
    control = WorkerControlStore(tmp_path / "execution.db")
    entered = asyncio.Event()
    cancelled_with_permission: list[bool] = []

    class Runtime:
        async def review(self, _payload):
            return {"account": "agentic-123", "scope_digest": "scope-1", "account_masked": "••••0123"}

        def permit(self, _state):
            return True

        async def run(self, state, *, recover: bool):
            assert recover is False
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled_with_permission.append(control.may_submit(state.generation, state.scope_digest))
                raise

        async def close(self):
            return None

    worker = SessionWorker(control, Runtime())
    try:
        await worker.handle("review", {
            "candidate_path": "candidate.json", "authorization_path": "permit.json",
            "earnings_database": "earnings.db", "poll_seconds": 5.0,
        })
        await worker.handle("start", {"scope_digest": "scope-1",
                                      "expected_generation": control.current().generation})
        await asyncio.wait_for(entered.wait(), 1)
        result = await worker.handle("stop", {})
        assert result["running"] is False
        assert cancelled_with_permission == [False]
        assert control.current().running is False
    finally:
        await worker.close()
        control.close()


@pytest.mark.asyncio
async def test_reviewed_scope_must_have_user_permit_before_start(tmp_path: Path) -> None:
    control = WorkerControlStore(tmp_path / "execution.db")

    class Runtime:
        async def review(self, _payload):
            return {"account": "agentic-123", "scope_digest": "scope-1", "account_masked": "••••0123"}

        def permit(self, _state):
            raise RuntimeError("User permit missing")

        async def close(self):
            return None

    worker = SessionWorker(control, Runtime())
    try:
        await worker.handle("review", {
            "candidate_path": "candidate.json", "authorization_path": "permit.json",
            "earnings_database": "earnings.db", "poll_seconds": 5.0,
        })
        with pytest.raises(RuntimeError, match="permit"):
            await worker.handle("start", {"scope_digest": "scope-1",
                                          "expected_generation": control.current().generation})
        assert control.current().running is False
    finally:
        await worker.close()
        control.close()


@pytest.mark.asyncio
async def test_stale_start_cannot_reverse_another_clients_stop(tmp_path: Path) -> None:
    control = WorkerControlStore(tmp_path / "execution.db")

    class Runtime:
        async def review(self, _payload):
            return {"account": "agentic-123", "scope_digest": "scope-1"}

        def permit(self, _state):
            return True

        async def close(self):
            return None

    worker = SessionWorker(control, Runtime())
    try:
        review = await worker.handle("review", {
            "candidate_path": "candidate.json", "authorization_path": "permit.json",
            "earnings_database": "earnings.db", "poll_seconds": 5.0,
        })
        await worker.handle("stop", {})
        with pytest.raises(RuntimeError, match="changed since review"):
            await worker.handle("start", {"scope_digest": "scope-1",
                                          "expected_generation": review["generation"]})
        assert control.current().running is False
    finally:
        await worker.close()
        control.close()


@pytest.mark.asyncio
async def test_shutdown_finishes_when_cleanup_report_fails(tmp_path: Path) -> None:
    control = WorkerControlStore(tmp_path / "execution.db")

    class Runtime:
        async def cleanup_report(self, _state):
            raise ConnectionError("broker disconnected")

        async def close(self):
            return None

    worker = SessionWorker(control, Runtime())
    try:
        result = await worker.handle("shutdown", {})
        assert result["stopped"] is True
        assert result["broker_verified"] is False
        assert worker.shutdown_requested.is_set()
    finally:
        await worker.close()
        control.close()


@pytest.mark.asyncio
async def test_stop_revokes_queued_research_connection_first(tmp_path: Path) -> None:
    control = WorkerControlStore(tmp_path / "execution.db")
    observed: list[bool] = []

    class Runtime:
        def disable_research(self):
            observed.append(control.current().running)
            return {"enabled": False}

        async def close(self):
            return None

    worker = SessionWorker(control, Runtime())
    try:
        await worker.handle("stop", {})
        assert observed == [False]
    finally:
        await worker.close()
        control.close()

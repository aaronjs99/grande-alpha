"""One user-started session controller shared by local desktop and CLI clients."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from grande_alpha.execution.worker_control import WorkerControlStore, WorkerState


class SessionWorker:
    """Serialize live session commands while a durable stop fence remains independent.

    The runtime owns broker resources. Stop updates SQLite first, then cancels
    its task, so a stalled broker read cannot leave a valid local start intent.
    """

    def __init__(self, control: WorkerControlStore, runtime: Any) -> None:
        self.control = control
        self.runtime = runtime
        self._task: asyncio.Task | None = None
        self._phase = "Idle"
        self._last_error = ""
        self.shutdown_requested = asyncio.Event()

    def status(self) -> dict:
        state = self.control.current()
        return {
            "running": state.running,
            "phase": self._phase if self._phase in {"Error", "Recovery blocked", "Reviewed", "Authorized", "Stopped"} else getattr(self.runtime, "phase", self._phase),
            "account_masked": getattr(self.runtime, "account_masked", ""),
            "account": state.account,
            "scope_digest": state.scope_digest,
            "connected": bool(getattr(self.runtime, "connected", False)),
            "data_coverage": getattr(self.runtime, "coverage", {}),
            "last_data_at": getattr(self.runtime, "last_data_at", None),
            "research": self.runtime.research_status() if hasattr(self.runtime, "research_status") else {},
            "error": self._last_error,
            "generation": state.generation,
        }

    async def handle(self, operation: str, payload: dict) -> dict:
        if operation == "status":
            return self.status()
        if operation == "connect":
            if payload and set(payload) != {"authenticate"}:
                raise ValueError("Connect accepts only authenticate")
            result = await self.runtime.connect(bool(payload.get("authenticate", True)))
            self._phase = "Connected"
            return result
        if operation == "review":
            required = {"candidate_path", "authorization_path", "earnings_database", "poll_seconds"}
            if set(payload) != required:
                raise ValueError("Review requires exact candidate, permit, data and interval fields")
            if self.control.current().running:
                raise RuntimeError("Stop trading before reviewing another candidate")
            if hasattr(self.runtime, "stop_research"):
                await self.runtime.stop_research()
            elif hasattr(self.runtime, "disable_research"):
                self.runtime.disable_research()
            summary = await self.runtime.review(payload)
            state = self.control.review(
                account=summary["account"], scope_digest=summary["scope_digest"],
                candidate_path=payload["candidate_path"],
                authorization_path=payload["authorization_path"],
                earnings_database=payload["earnings_database"],
                poll_seconds=payload["poll_seconds"],
            )
            self._phase = "Reviewed"
            return {**summary, "generation": state.generation}
        if operation == "authorize":
            if set(payload) != {"phrase"}:
                raise ValueError("Authorization requires an exact approval phrase")
            state = self.control.current()
            if state.running or not state.scope_digest:
                raise RuntimeError("Review a stopped session before authorizing")
            if payload["phrase"] != f"AUTHORIZE {state.scope_digest}":
                raise RuntimeError("Exact-scope authorization was declined")
            result = self.runtime.authorize(state)
            self._phase = "Authorized"
            return {**result, "generation": state.generation}
        if operation == "start":
            if set(payload) != {"scope_digest", "expected_generation"}:
                raise ValueError("Start requires the reviewed scope and control generation")
            if self._task is not None and not self._task.done():
                raise RuntimeError("Wait for the previous runner to stop before starting again")
            state = self.control.current()
            if payload["scope_digest"] != state.scope_digest:
                raise RuntimeError("Start scope differs from the reviewed scope")
            if type(payload["expected_generation"]) is not int or payload["expected_generation"] != state.generation:
                raise RuntimeError("Worker control changed since review; review and approve again")
            if self.runtime.permit(state) is not True:
                raise RuntimeError("Exact user permit was not verified")
            if hasattr(self.runtime, "stop_research"):
                await self.runtime.stop_research()
            state = self.control.start(state.scope_digest, expected_generation=payload["expected_generation"])
            self._launch(state, recover=False)
            return self.status()
        if operation == "stop":
            if payload:
                raise ValueError("Stop does not accept extra fields")
            return await self.stop()
        if operation == "revoke":
            if set(payload) != {"phrase"}:
                raise ValueError("Revocation requires the exact account phrase")
            state = self.control.current()
            if payload["phrase"] != f"REVOKE {state.account}":
                raise RuntimeError("Exact-account revocation was declined")
            await self.stop()
            self.runtime.revoke(state)
            return {"revoked": True, "orders_cancelled": False}
        if operation == "recovery_ack":
            if payload:
                raise ValueError("Recovery acknowledgement does not accept extra fields")
            return self.runtime.recovery_ack(self.control.current())
        if operation == "research_enable":
            if payload:
                raise ValueError("Research enable does not accept extra fields")
            return await self.runtime.enable_research(self.control.current())
        if operation == "research_disable":
            if payload:
                raise ValueError("Research disable does not accept extra fields")
            if hasattr(self.runtime, "stop_research"):
                return await self.runtime.stop_research()
            return self.runtime.disable_research()
        if operation == "research_paper_start":
            required = {
                "source", "initial_cash", "trade_cash", "loop_demo", "news_enabled",
                "social_enabled", "local_ai_enabled", "local_ai_model",
            }
            if set(payload) != required:
                raise ValueError("Paper start requires source and explicit virtual cash values")
            return await self.runtime.start_paper_research(self.control.current(), payload)
        if operation == "research_paper_stop":
            if payload:
                raise ValueError("Paper stop does not accept extra fields")
            return await self.runtime.stop_paper_research()
        if operation == "shutdown":
            if payload:
                raise ValueError("Shutdown does not accept extra fields")
            await self.stop()
            try:
                report = await self.runtime.cleanup_report(self.control.current())
            except Exception:
                report = {"broker_verified": False, "orders_cancelled": False,
                          "warning": "Broker cleanup status could not be verified"}
            finally:
                self.shutdown_requested.set()
            return {"stopped": True, "process_exiting": True, **report}
        raise ValueError("Unsupported worker operation")

    def _launch(self, state: WorkerState, *, recover: bool) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError("A live runner already owns this worker")
        self._phase = "Recovering" if recover else "Starting"
        self._last_error = ""
        self._task = asyncio.create_task(self.runtime.run(state, recover=recover))
        self._task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        if task is not self._task:
            return
        self._task = None
        if task.cancelled():
            self._phase = "Stopped"
            return
        error = task.exception()
        if error is not None:
            self._last_error = f"{type(error).__name__}: {error}"
            self._phase = "Error"
        else:
            self._phase = "Stopped"
        if self.control.current().running:
            self.control.stop()
        if hasattr(self.runtime, "disable_research"):
            with contextlib.suppress(Exception):
                self.runtime.disable_research()

    async def recover(self) -> None:
        state = self.control.current()
        if not state.running:
            return
        try:
            if self.runtime.permit(state) is not True:
                raise RuntimeError("Recovered user permit was not verified")
            self._launch(state, recover=True)
        except Exception as error:
            self.control.stop()
            self._phase = "Recovery blocked"
            self._last_error = str(error)

    async def stop(self) -> dict:
        self.control.stop()
        if hasattr(self.runtime, "stop_research"):
            with contextlib.suppress(Exception):
                await self.runtime.stop_research()
        elif hasattr(self.runtime, "disable_research"):
            with contextlib.suppress(Exception):
                self.runtime.disable_research()
        self._phase = "Stopped"
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.CancelledError, TimeoutError):
                pass
        return self.status()

    async def close(self) -> None:
        await self.stop()
        await self.runtime.close()

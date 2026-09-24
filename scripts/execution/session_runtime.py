"""Broker-backed mixed session used by the local worker."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict
from pathlib import Path

from grande_alpha.broker import RobinhoodMCPBroker
from grande_alpha.broker.base import order_is_terminal
from grande_alpha.configuration.config import data_dir
from grande_alpha.data.earnings_feed import AlphaVantageEarningsClient, EarningsObservationStore, load_api_key
from grande_alpha.data.live_data import LiveDataService
from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.domain.models import utc_now
from grande_alpha.execution.authorization import (
    UserAuthorizationGate,
    create_authorization,
    revoke_authorization,
)
from grande_alpha.execution.candidate_profile import load_candidate
from grande_alpha.execution.equity_ledger import EquityLedger, ExecutionLeaseBusy
from grande_alpha.execution.mixed_engine import mixed_candidate_digest, run_cycles
from grande_alpha.execution.production import build_production_engine
from grande_alpha.execution.read_retry import read_with_backoff
from grande_alpha.execution.standing import validate_contract
from grande_alpha.execution.worker_control import WorkerControlStore, WorkerPermission, WorkerState
from grande_alpha.persistence.store import AuditStore
from grande_alpha.research.portfolio_replay import EASTERN
from grande_alpha.research.worker_research import WorkerResearch


class BrokerSessionRuntime:
    """Own the one broker transport used for review and autonomous execution."""

    def __init__(
        self, runtime_data_dir: Path | None = None, *,
        control: WorkerControlStore | None = None,
        broker_factory=None, contract_validator=validate_contract,
    ) -> None:
        self.data_dir = runtime_data_dir or data_dir()
        self.control = control
        self._broker_factory = broker_factory or (
            lambda interactive: RobinhoodMCPBroker(allow_interactive_auth=interactive)
        )
        self._contract_validator = contract_validator
        self.broker = None
        self.account_masked = ""
        self.coverage: dict = {}
        self.last_data_at: str | None = None
        self.research: WorkerResearch | None = None
        self._running = False
        self._connect_lock = asyncio.Lock()
        self._research_enable_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return bool(self.broker is not None and self.broker.connected)

    @property
    def phase(self) -> str:
        if self._running:
            now = utc_now().astimezone(EASTERN)
            hours = regular_session_times(now.date())
            if hours is None or not hours[0] <= now.time().replace(tzinfo=None) < hours[1]:
                return "Waiting for market"
            return "Running"
        return "Connected" if self.connected else "Idle"

    async def connect(self, authenticate: bool = True) -> dict:
        async with self._connect_lock:
            return await self._connect_once(authenticate)

    async def _connect_once(self, authenticate: bool) -> dict:
        if self.connected:
            return {"connected": True, "accounts": await self._accounts()}
        await self._close_research()
        self.broker = self._broker_factory(authenticate)
        try:
            async with asyncio.timeout(300 if authenticate else 30):
                await self.broker.connect()
            self._contract_validator(self.broker)
            return {"connected": True, "accounts": await self._accounts()}
        except BaseException:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(8):
                    await self.broker.disconnect()
            self.broker = None
            raise

    async def _accounts(self) -> list[dict]:
        accounts = await read_with_backoff(self.broker.get_accounts)
        return [{"masked": account.masked, "agentic_allowed": account.agentic_allowed is True}
                for account in accounts]

    async def review(self, payload: dict) -> dict:
        scope, policy, thresholds = load_candidate(Path(payload["candidate_path"]))
        if not self.connected:
            await self.connect(True)
        accounts = [account for account in await read_with_backoff(self.broker.get_accounts)
                    if account.account_number == scope.account_number]
        if len(accounts) != 1 or accounts[0].agentic_allowed is not True:
            raise RuntimeError("The exact Agentic account or broker permission was not verified")
        self.account_masked = accounts[0].masked
        return {
            "account": scope.account_number,
            "account_masked": accounts[0].masked,
            "scope_digest": mixed_candidate_digest(scope, policy, thresholds),
            "allowed_symbols": list(scope.allowed_symbols),
            "limits": {key: value for key, value in asdict(scope).items()
                       if key.startswith("max_")},
            "allocation_policy": asdict(policy),
            "earnings_thresholds": thresholds,
            "starts_at": scope.starts_at.isoformat(),
            "expires_at": scope.expires_at.isoformat() if scope.expires_at else None,
            "orders_submitted": False,
        }

    def permit(self, state: WorkerState) -> bool:
        scope, policy, thresholds = load_candidate(Path(state.candidate_path))
        if scope.account_number != state.account or mixed_candidate_digest(scope, policy, thresholds) != state.scope_digest:
            raise RuntimeError("Reviewed candidate or account changed")
        return UserAuthorizationGate(Path(state.authorization_path), state.account)(state.scope_digest)

    def authorize(self, state: WorkerState) -> dict:
        try:
            if self.permit(state) is True:
                return {"authorized": True, "scope_digest": state.scope_digest,
                        "already_approved": True}
        except FileNotFoundError:
            pass
        create_authorization(Path(state.authorization_path), state.account, state.scope_digest)
        self.permit(state)
        return {"authorized": True, "scope_digest": state.scope_digest,
                "already_approved": False}

    def revoke(self, state: WorkerState) -> None:
        if not state.account:
            raise RuntimeError("No reviewed account to revoke")
        revoke_authorization(Path(state.authorization_path))

    def research_status(self) -> dict:
        return self.research.status() if self.research is not None else {
            "enabled": False, "running": False, "phase": "Off",
            "bridge_path": None, "orders_available": False, "paper": None,
        }

    async def enable_research(self, state: WorkerState) -> dict:
        async with self._research_enable_lock:
            return await self._enable_research_once(state)

    async def _enable_research_once(self, state: WorkerState) -> dict:
        if self._running or bool(self.control and self.control.current().running):
            raise RuntimeError("Research connections are unavailable during a live trading session")
        if not state.account or not self.connected:
            raise RuntimeError("Review a connected Agentic account before enabling research")
        return await self._research_service().enable(state.account)

    def _research_service(self) -> WorkerResearch:
        if self.research is None:
            def record(summary: str, severity: str = "info", category: str = "agent_research",
                       payload=None) -> None:
                audit = AuditStore(self.data_dir / "grande_alpha.db")
                try:
                    audit.receipt(category, summary, payload, severity)
                finally:
                    audit.close()

            self.research = WorkerResearch(
                self.broker, self.data_dir / "agent-mcp.db", log=record,
                execution_active=lambda: self._running or bool(self.control and self.control.current().running),
            )
        return self.research

    async def start_paper_research(self, state: WorkerState, payload: dict) -> dict:
        if self._running or bool(self.control and self.control.current().running):
            raise RuntimeError("Paper research cannot run alongside the live trading session")
        research = self._research_service()
        if payload["source"] == "demo":
            research.prepare(None)
            return research.start_paper(payload)
        if not state.account or not self.connected:
            raise RuntimeError("Connect and review an account before using broker quotes")
        accounts = [
            account for account in await read_with_backoff(self.broker.get_accounts)
            if account.account_number == state.account
        ]
        if len(accounts) != 1:
            raise RuntimeError("The reviewed account could not be freshly reconciled")
        research.prepare(accounts[0])
        return research.start_paper(payload)

    async def stop_paper_research(self) -> dict:
        return await self.research.stop_agent() if self.research is not None else self.research_status()

    def disable_research(self) -> dict:
        return self.research.disable() if self.research is not None else self.research_status()

    async def stop_research(self) -> dict:
        return await self.research.stop() if self.research is not None else self.research_status()

    async def _close_research(self) -> None:
        research, self.research = self.research, None
        if research is not None:
            await research.close()

    def recovery_ack(self, state: WorkerState) -> dict:
        if not state.account:
            raise RuntimeError("No reviewed account")
        audit = AuditStore(self.data_dir / "grande_alpha.db")
        try:
            changed = audit.acknowledge_loss_recovery(state.account)
        finally:
            audit.close()
        return {"recovery_pause_cleared": changed, "daily_loss_reset": False}

    async def cleanup_report(self, state: WorkerState) -> dict:
        """Report what is still known after a stop without claiming cancellation."""
        report = {
            "broker_verified": False,
            "orders_cancelled": False,
            "open_order_ids": [],
            "positions": [],
            "local_unresolved_references": [],
            "warning": "Broker orders and holdings require direct confirmation",
        }
        if not state.account:
            return report
        try:
            audit = AuditStore(self.data_dir / "grande_alpha.db")
            try:
                ledger = EquityLedger(audit)
                report["local_unresolved_references"] = [
                    row["ref"] for row in ledger.unresolved(state.account)
                ]
            finally:
                audit.close()
        except (OSError, RuntimeError, ValueError) as error:
            report["warning"] = f"Local execution records need review: {error}"
        if not self.connected:
            return report
        try:
            async with asyncio.timeout(6):
                orders = await self.broker.get_orders(state.account)
                positions = await self.broker.get_positions(state.account)
            report["open_order_ids"] = [order.order_id for order in orders if not order_is_terminal(order)]
            report["positions"] = [
                {"symbol": position.symbol, "quantity": position.quantity}
                for position in positions if position.quantity > 0
            ]
            report["broker_verified"] = True
            report["warning"] = (
                "Open orders or holdings remain at the broker" if report["open_order_ids"] or report["positions"]
                else "No open orders or holdings were observed at the broker"
            )
        except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError):
            pass
        return report

    async def _recover_engine(self, engine, authority_id: str, state: WorkerState) -> None:
        """Wait out only the stale lease left by a prior process crash."""
        deadline = asyncio.get_running_loop().time() + 35
        while True:
            current = self.control.current()
            if not current.running or current.generation != state.generation:
                raise RuntimeError("Recovery was stopped before the old lease expired")
            self.permit(current)
            try:
                engine.recover(authority_id)
                return
            except ExecutionLeaseBusy:
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError("Account lease did not expire during recovery") from None
                await asyncio.sleep(0.5)

    async def run(self, state: WorkerState, *, recover: bool) -> None:
        if self.control is None:
            raise RuntimeError("The worker runtime requires a durable control store")
        await self.stop_research()
        if not self.connected:
            await self.connect(False if recover else True)
        self.permit(state)
        scope, policy, thresholds = load_candidate(Path(state.candidate_path))
        accounts = [account for account in await read_with_backoff(self.broker.get_accounts)
                    if account.account_number == scope.account_number]
        if len(accounts) != 1 or accounts[0].agentic_allowed is not True:
            raise RuntimeError("The exact Agentic account or broker permission was not verified")
        self.account_masked = accounts[0].masked
        with contextlib.ExitStack() as resources:
            audit = AuditStore(self.data_dir / "grande_alpha.db")
            resources.callback(audit.close)
            ledger = EquityLedger(audit)
            resources.callback(ledger.close)
            earnings = EarningsObservationStore(Path(state.earnings_database))
            resources.callback(earnings.close)
            try:
                key, _storage = load_api_key()
            except RuntimeError:
                earnings_client = None
            else:
                earnings_client = AlphaVantageEarningsClient(key)
            source = LiveDataService(
                self.data_dir / "market_v1.db", self.broker, earnings, scope, policy,
                thresholds, earnings_client=earnings_client,
            )
            resources.callback(source.close)
            engine = build_production_engine(
                self.broker, ledger, audit, scope, policy, thresholds,
                authorization_permit=Path(state.authorization_path), earnings_store=earnings,
            )
            engine.authorization_gate = WorkerPermission(
                self.control, state.generation, engine.authorization_gate,
            )
            authority_id = audit.active_standing_for_scope(state.scope_digest)
            if recover:
                if authority_id is None:
                    raise RuntimeError("Previously running authority is unavailable for recovery")
                await self._recover_engine(engine, authority_id, state)
            else:
                if authority_id is not None:
                    audit.stop_standing(authority_id)
                engine.arm()
            self._running = True
            try:
                async def observed_snapshot() -> dict:
                    snapshot = await source.snapshot()
                    self.coverage = snapshot["coverage"]
                    self.last_data_at = utc_now().isoformat()
                    return snapshot

                await run_cycles(engine, observed_snapshot, poll_seconds=state.poll_seconds)
            finally:
                self._running = False

    async def close(self) -> None:
        await self._close_research()
        if self.broker is not None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(8):
                    await self.broker.disconnect()
            self.broker = None

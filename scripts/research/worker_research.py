"""Opt-in research desk hosted by the same broker-owning process as trading."""

from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from pathlib import Path

from grande_alpha.domain.models import utc_now
from grande_alpha.research.agent_bridge import AgentBridge
from grande_alpha.research.agent_models import AgentSettings
from grande_alpha.research.agent_runtime import AgentRuntime


class WorkerResearch:
    """Keep AI prompts and research commands outside the execution path."""

    def __init__(self, broker, bridge_path: Path, *, log) -> None:
        self.broker = broker
        self.bridge = AgentBridge(bridge_path)
        self.log = log
        self.account = None
        self.agent: AgentRuntime | None = None
        self._poll_task: asyncio.Task | None = None
        self._revision = 0

    async def enable(self, account_number: str) -> dict:
        if not self.broker or not self.broker.connected:
            raise RuntimeError("Connect the broker before enabling research MCP")
        if self.bridge.session:
            return self.status()
        revision = self._revision
        accounts = [item for item in await self.broker.get_accounts()
                    if item.account_number == account_number and item.agentic_allowed is True]
        if revision != self._revision:
            raise RuntimeError("Research enable was cancelled by Stop or Revoke")
        if len(accounts) != 1:
            raise RuntimeError("The reviewed Agentic account is unavailable for research")
        self.account = accounts[0]

        async def crypto_quotes(instruments):
            if not self.account.rhs_account_number or not self.account.rhc_account_number:
                raise RuntimeError("Linked crypto research account is unavailable")
            return await self.broker.get_crypto_quotes(
                instruments, rhs_account_number=self.account.rhs_account_number,
            )

        self.agent = AgentRuntime(
            equity_quotes=self.broker.get_quotes,
            crypto_pairs=self.broker.discover_crypto,
            crypto_quotes=crypto_quotes,
            equity_scan=self.broker.discover_equities,
            connected=lambda: bool(self.broker.connected),
            changed=lambda _snapshot: None,
            log=self.log,
            crypto_account_type=lambda: self.account.brokerage_account_type if self.account else "",
        )
        self.bridge.enable()
        self._poll_task = asyncio.create_task(self._poll(), name="grande-research-mcp")
        return self.status()

    def disable(self) -> dict:
        self._revision += 1
        self.bridge.disable()
        if self.agent is not None:
            self.agent.stop("Research connection revoked")
        task, self._poll_task = self._poll_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        return self.status()

    def status(self) -> dict:
        snapshot = self.agent.snapshot if self.agent is not None else None
        return {
            "enabled": bool(self.bridge.session),
            "running": bool(snapshot and snapshot.running),
            "phase": snapshot.phase if snapshot else "Off",
            "bridge_path": str(self.bridge.path) if self.bridge.session else None,
            "orders_available": False,
        }

    async def _poll(self) -> None:
        try:
            while self.bridge.session:
                self.bridge.poll(self._command)
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            pass
        except Exception:
            self.disable()

    def _command(self, command: str, payload: dict) -> dict:
        if not self.bridge.session or self.agent is None or not self.broker.connected:
            raise RuntimeError("Research MCP is not enabled")
        fields = {
            "context": set(), "start": set(), "stop": set(),
            "brief": {"market", "brief"},
            "universe": {"equity_symbols", "crypto_symbols"},
        }
        if command not in fields or set(payload) != fields[command]:
            raise ValueError("Invalid research command fields")
        if command == "brief":
            self.agent.set_brief(payload["market"], payload["brief"])
            return {"status": "Research brief saved for the next cycle; trading authority unchanged"}
        if command == "universe":
            if self.agent.snapshot.running:
                raise ValueError("Stop research before changing its universe")
            if any(not isinstance(value, list) or any(not isinstance(symbol, str) for symbol in value)
                   for value in payload.values()):
                raise ValueError("Research symbols must be lists of strings")
            settings = replace(
                self.agent.settings,
                equity_symbols=tuple(payload["equity_symbols"]),
                crypto_symbols=tuple(payload["crypto_symbols"]),
                scan_id="",
            )
            settings.validate()
            self.agent.settings = settings
            return {"status": "Research universe saved; trading scope unchanged"}
        if command == "start":
            self.agent.start(self.agent.settings)
            return {"status": "Research workers started; no orders authorized"}
        if command == "stop":
            self.agent.stop("Research workers stopped from MCP")
            return {"status": "Research workers stopped; trading state unchanged"}
        snapshot = self.agent.snapshot
        observations = []
        for item in snapshot.decisions:
            quote = item.quote
            if quote is None:
                continue
            try:
                quote.validate()
                numbers = (quote.bid, quote.ask, quote.spread_bps, item.change_bps or 0)
                if not all(math.isfinite(number) for number in numbers):
                    continue
                observations.append({
                    "key": item.instrument.key,
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "spread_bps": quote.spread_bps,
                    "quote_at": quote.timestamp.isoformat(),
                    "age_seconds": quote.age_seconds(utc_now()),
                    "samples": item.samples,
                    "change_bps": item.change_bps,
                    "proposal": item.action,
                    "data_checks": item.risk_status,
                })
            except (ValueError, TypeError, OverflowError):
                continue
        settings: AgentSettings = self.agent.settings
        return {
            "mode": "research_only", "orders_available": False,
            "running": snapshot.running, "cycle": snapshot.cycle, "phase": snapshot.phase,
            "workers": snapshot.worker_status,
            "observed_at": snapshot.observed_at.isoformat() if snapshot.observed_at else None,
            "briefs": {"team": settings.research_brief,
                       "equity": settings.equity_brief, "crypto": settings.crypto_brief},
            "local_ai_enabled": settings.local_ai_enabled,
            "universe": {"equity": settings.equity_symbols,
                         "crypto": settings.crypto_symbols},
            "observations": observations,
        }

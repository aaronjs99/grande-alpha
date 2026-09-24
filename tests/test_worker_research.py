"""Research MCP is session-scoped and has no order tool or authority path."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from grande_alpha.research.agent_bridge import AgentBridge, BridgeUnavailable
from grande_alpha.research.worker_research import WorkerResearch


@pytest.mark.asyncio
async def test_research_bridge_is_opt_in_and_revoked_without_trading(tmp_path: Path) -> None:
    class Broker:
        connected = True

        async def get_accounts(self):
            return [SimpleNamespace(
                account_number="agentic-123", agentic_allowed=True,
                rhs_account_number="", rhc_account_number="", brokerage_account_type="cash",
            )]

        async def get_quotes(self, _symbols):
            return {}

        async def discover_crypto(self):
            return []

        async def discover_equities(self, _scan_id):
            return []

    desk = WorkerResearch(Broker(), tmp_path / "agent-mcp.db", log=lambda *_args, **_kwargs: None)
    bridge = AgentBridge(tmp_path / "agent-mcp.db")
    assert desk.status()["enabled"] is False
    try:
        assert (await desk.enable("agentic-123"))["enabled"] is True
        context = await bridge.request("context")
        assert context["orders_available"] is False
        assert context["mode"] == "research_only"
        await bridge.request("brief", {"market": "team", "brief": "Observe, do not trade"})
        assert desk.agent.settings.research_brief == "Observe, do not trade"
        request = asyncio.create_task(bridge.request("context"))
        await asyncio.sleep(0)
        desk.disable()
        with pytest.raises(BridgeUnavailable):
            await request
    finally:
        desk.disable()


@pytest.mark.asyncio
async def test_stop_during_account_read_cannot_enable_research(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Broker:
        connected = True

        async def get_accounts(self):
            entered.set()
            await release.wait()
            return [SimpleNamespace(account_number="agentic-123", agentic_allowed=True)]

    desk = WorkerResearch(Broker(), tmp_path / "agent-mcp.db", log=lambda *_args: None)
    pending = asyncio.create_task(desk.enable("agentic-123"))
    await entered.wait()
    desk.disable()
    release.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        await pending
    assert desk.status()["enabled"] is False

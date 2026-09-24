from __future__ import annotations

import asyncio
import sqlite3

import pytest

from grande_alpha.research.agent_bridge import AgentBridge, BridgeUnavailable


@pytest.mark.asyncio
async def test_bridge_requires_live_explicit_session_and_discards_revoked_request(tmp_path):
    bridge = AgentBridge(tmp_path / "research.db")
    with pytest.raises(BridgeUnavailable):
        await bridge.request("context")
    bridge.enable()
    pending = asyncio.create_task(AgentBridge(bridge.path).request("start"))
    await asyncio.sleep(0)
    bridge.disable()
    bridge.enable()
    bridge.poll(lambda command, payload: {"started": True})
    with pytest.raises(BridgeUnavailable):
        await pending
    bridge.disable()


@pytest.mark.asyncio
async def test_bridge_rejects_trade_command_and_stale_desktop_lease(tmp_path):
    bridge = AgentBridge(tmp_path / "research.db")
    bridge.enable()
    with pytest.raises(ValueError, match="Unsupported research command"):
        await bridge.request("place_order")
    with sqlite3.connect(bridge.path) as db:
        db.execute("UPDATE session SET heartbeat = 0")
    with pytest.raises(BridgeUnavailable):
        await bridge.request("context")
    bridge.disable()

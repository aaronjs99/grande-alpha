from __future__ import annotations

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from grande_alpha.research.agent_bridge import AgentBridge
from grande_alpha.research.agent_mcp import create_server


@pytest.mark.asyncio
async def test_mcp_exposes_research_tools_only_and_needs_opt_in(tmp_path):
    bridge = AgentBridge(tmp_path / "research.db")
    server = create_server(AgentBridge(bridge.path))
    async with create_connected_server_and_client_session(server) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "get_research_context", "set_research_brief", "configure_research_universe",
            "start_research", "start_paper_trading", "stop_research",
        }
        assert (await client.call_tool("get_research_context")).isError
        bridge.enable()

        async def service():
            while bridge.session:
                bridge.poll(lambda command, payload: {"command": command, "payload": payload})
                await asyncio.sleep(0.01)

        task = asyncio.create_task(service())
        try:
            result = await client.call_tool("set_research_brief", {"brief": "Compare spreads", "market": "crypto"})
            assert not result.isError
            payload = json.loads(result.content[0].text)
            assert payload == {"command": "brief", "payload": {"market": "crypto", "brief": "Compare spreads"}}
        finally:
            bridge.disable()
            await task

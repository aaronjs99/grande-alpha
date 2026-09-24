from __future__ import annotations

import json

import httpx
import pytest

from grande_alpha.agent_analyst import OllamaAnalyst


@pytest.mark.asyncio
async def test_optional_local_ai_uses_loopback_structured_output_without_credentials(monkeypatch):
    real_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        assert str(request.url) == "http://127.0.0.1:11434/api/chat"
        assert "authorization" not in request.headers
        assert body["stream"] is False
        assert body["format"]["additionalProperties"] is False
        assert "tools" not in body
        user_message = json.loads(body["messages"][1]["content"])
        assert user_message["research_brief"] == "Compare spread costs"
        assert user_message["market_brief"] == "Stocks only"
        assert user_message["observations"][0]["key"] == "equity:AAPL"
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "content": json.dumps(
                        {
                            "decisions": [
                                {"key": "equity:AAPL", "action": "hold", "reason": "Insufficient evidence"}
                            ]
                        }
                    )
                },
            },
        )

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    result = await OllamaAnalyst().analyze("installed-model", [{"key": "equity:AAPL", "samples": 4}],
                                          research_brief="Compare spread costs", market_brief="Stocks only")
    assert result == {"equity:AAPL": ("hold", "Insufficient evidence")}
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"done": False, "message": {"content": "{}"}},
        {"done": True, "message": {"content": "{}", "tool_calls": [{"function": {"name": "place_order"}}]}},
        {"done": True, "message": {"content": "not JSON"}},
    ],
)
async def test_incomplete_or_tool_calling_model_output_is_rejected(monkeypatch, payload):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(
            **kw, transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload))
        ),
    )
    with pytest.raises(ValueError):
        await OllamaAnalyst().analyze("installed-model", [{"key": "equity:AAPL"}])

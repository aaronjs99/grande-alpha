from __future__ import annotations

import json

import httpx
import pytest

from grande_alpha.agent_analyst import AnalystResponseError, OllamaAnalyst, parse_decisions


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
                            "decisions": {"equity:AAPL": {"action": "hold", "reason": "Insufficient evidence"}}
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


@pytest.mark.asyncio
async def test_per_instrument_contract_handles_empty_news_without_inventing_citations(monkeypatch):
    """The QQQ report has valid quotes but zero news; [] must be a valid HOLD."""
    observations = [
        {"key": "equity:QQQ", "source_context": {"news_required": True, "buy_supported": False, "articles": []}},
        {"key": "equity:AAPL", "source_context": {"news_required": True, "buy_supported": True, "articles": [
            {"id": "apple-news", "kind": "news", "scope": "direct"}]}},
        {"key": "crypto:BTC-USD", "source_context": {"news_required": False, "buy_supported": False, "articles": []}},
    ]
    response = {"decisions": {
        "equity:QQQ": {"action": "hold", "reason": "News coverage is insufficient", "source_ids": []},
        "equity:AAPL": {"action": "buy", "reason": "Fixture numeric evidence", "source_ids": ["apple-news"]},
        "crypto:BTC-USD": {"action": "buy", "reason": "Fixture numeric evidence", "source_ids": []},
    }}

    def handle(request):
        body = json.loads(request.content)
        contract = body["format"]["properties"]["decisions"]
        assert contract["type"] == "object" and not contract["additionalProperties"]
        assert set(contract["required"]) == {item["key"] for item in observations}
        fields = {key: value["properties"] for key, value in contract["properties"].items()}
        assert fields["equity:QQQ"]["source_ids"]["maxItems"] == 0
        assert fields["equity:QQQ"]["action"]["enum"] == ["hold", "exit"]
        assert fields["equity:AAPL"]["source_ids"]["items"]["enum"] == ["apple-news"]
        assert "buy" in fields["equity:AAPL"]["action"]["enum"]
        assert "buy" in fields["crypto:BTC-USD"]["action"]["enum"]
        assert "Use source_ids: []" in body["messages"][0]["content"]
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(response)}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(**kw, transport=httpx.MockTransport(handle)))
    result = await OllamaAnalyst().analyze("fixture", observations)
    assert result["equity:QQQ"] == ("hold", "News coverage is insufficient · Sources: none")
    assert result["equity:AAPL"][0] == result["crypto:BTC-USD"][0] == "buy"


@pytest.mark.parametrize("change,code", [
    ({"action": "BUY"}, "invalid_action"),
    ({"reason": " "}, "invalid_reason"),
    ({"unexpected": "PRIVATE_TEXT"}, "decision_fields"),
    ({"source_ids": "PRIVATE_TEXT"}, "citation_format"),
    ({"source_ids": ["PRIVATE_OTHER_SYMBOL_ARTICLE"]}, "unknown_source"),
    ({"source_ids": ["social"]}, "missing_news_citation"),
    ({"source_ids": []}, "missing_news_citation"),
])
@pytest.mark.parametrize("keyed", [False, True])
def test_response_errors_are_specific_and_safe_in_both_protocols(change, code, keyed):
    key = "equity:AAPL"
    item = {"action": "buy", "reason": "PRIVATE_TEXT", "source_ids": ["news"], **change}
    data = {"decisions": {key: item} if keyed else [{"key": key, **item}]}
    with pytest.raises(AnalystResponseError) as caught:
        parse_decisions(json.dumps(data), {key}, {key: {"news", "social"}}, {key: {"news"}})
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("payload,code", [
    ("PRIVATE_NOT_JSON", "invalid_json"),
    ('{"decisions": {"equity:PRIVATE": {}}}', "instrument_keys"),
    ('{"decisions": {}}', "instrument_keys"),
    ('{"decisions": [], "decisions": []}', "duplicate_fields"),
    ('{"decisions": {"equity:AAPL": {"action":"buy", "action":"hold"}}}', "duplicate_fields"),
    ('{"decisions": {"equity:AAPL": {}, "equity:AAPL": {}}}', "duplicate_fields"),
    ('{"decisions": null}', "response_shape"),
])
def test_malformed_json_and_instrument_identity_are_not_silently_repaired(payload, code):
    with pytest.raises(AnalystResponseError) as caught:
        parse_decisions(payload, {"equity:AAPL"})
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    ([], "incomplete_response"),
    ({"done": False, "message": {"content": "PRIVATE_TEXT"}}, "incomplete_response"),
    ({"done": True, "message": None}, "incomplete_response"),
    ({"done": True, "done_reason": "length", "message": {"content": "PRIVATE_TEXT"}}, "output_limit"),
    ({"done": True, "message": {"tool_calls": [{"name": "PRIVATE_TEXT"}]}}, "tool_request"),
])
async def test_ollama_completion_failures_have_specific_safe_codes(monkeypatch, payload, code):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload))))
    with pytest.raises(AnalystResponseError) as caught:
        await OllamaAnalyst().analyze("fixture", [{"key": "equity:AAPL"}])
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)

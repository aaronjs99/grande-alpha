"""Optional local AI analyst. It receives observations and can only return proposals."""

from __future__ import annotations

import json

import httpx

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "action": {"type": "string", "enum": ["buy", "hold", "exit"]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                "required": ["key", "action", "reason"],
            },
        },
    },
    "required": ["decisions"],
}


def parse_decisions(payload: str, expected_keys: set[str]) -> dict[str, tuple[str, str]]:
    if not isinstance(payload, str) or len(payload) > 50_000:
        raise ValueError("Local analyst returned an invalid response size")
    data = json.loads(payload)
    if not isinstance(data, dict) or set(data) != {"decisions"} or not isinstance(data["decisions"], list):
        raise ValueError("Local analyst response must contain only a decisions array")
    decisions: dict[str, tuple[str, str]] = {}
    for item in data["decisions"]:
        if not isinstance(item, dict) or set(item) != {"key", "action", "reason"}:
            raise ValueError("Local analyst returned unexpected decision fields")
        key, action, reason = item["key"], item["action"], item["reason"]
        if not isinstance(key, str) or key not in expected_keys or key in decisions:
            raise ValueError("Local analyst changed or duplicated an instrument")
        if (
            action not in ("buy", "hold", "exit")
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 500
        ):
            raise ValueError("Local analyst returned an invalid action or reason")
        decisions[key] = (action, reason.strip())
    if set(decisions) != expected_keys:
        raise ValueError("Local analyst omitted an instrument")
    return decisions


class OllamaAnalyst:
    async def analyze(
        self, model: str, observations: list[dict], *, research_brief: str = "", market_brief: str = ""
    ) -> dict[str, tuple[str, str]]:
        # Fixed loopback endpoint, no proxy inheritance, redirects or broker credentials.
        async with httpx.AsyncClient(timeout=25.0, trust_env=False, follow_redirects=False) as client:
            response = await client.post(
                "http://127.0.0.1:11434/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": DECISION_SCHEMA,
                    "options": {"temperature": 0, "num_predict": 4096},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Evaluate the supplied numeric observations as research proposals. "
                                "Return one decision per exact key using buy, hold, or exit. Exit means "
                                "a candidate for reducing a long holding, never a short sale. You have "
                                "no portfolio, news, fundamental data, or order authority. Explain only "
                                "what the observations support; do not invent facts, fills or profits. "
                                "Prefer hold when evidence is insufficient. Costs include at least "
                                "the bid/ask spread. The response schema is " + json.dumps(DECISION_SCHEMA)
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps({
                                "research_brief": research_brief,
                                "market_brief": market_brief,
                                "observations": observations,
                            }, allow_nan=False),
                        },
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
            if data.get("done") is not True or data.get("message", {}).get("tool_calls"):
                raise ValueError("Local analyst response is incomplete or requests tools")
            return parse_decisions(
                data.get("message", {}).get("content"), {item["key"] for item in observations}
            )

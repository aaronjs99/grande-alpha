"""Versioned desktop preferences, separate from broker permissions and run state."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from grande_alpha.agent_models import AgentSettings
from grande_alpha.agent_paper import validate_paper_settings


@dataclass(frozen=True)
class PaperSetup:
    source: str = "broker_quotes"
    initial_cash: float = 1000
    trade_cash: float = 100
    loop_demo: bool = False

    def validate(self):
        if any(type(value) not in (int, float) for value in (self.initial_cash, self.trade_cash)):
            raise ValueError("Virtual cash settings must be numbers")
        validate_paper_settings(self.source, self.initial_cash, self.trade_cash)
        if type(self.loop_demo) is not bool:
            raise ValueError("Repeat demo must be true or false")


@dataclass(frozen=True)
class AgentPreferences:
    settings: AgentSettings = field(default_factory=lambda: AgentSettings(paper_strategy="adaptive"))
    paper: PaperSetup = field(default_factory=PaperSetup)

    def save(self, path: Path):
        self.settings.validate()
        self.paper.validate()
        payload = json.dumps({"version": 1, **asdict(self)}, indent=2, allow_nan=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(".pending")
        pending.write_text(payload, encoding="utf-8")
        pending.replace(path)


def load_preferences(path: Path) -> tuple[AgentPreferences, str]:
    if not path.exists():
        return AgentPreferences(), ""
    try:
        if path.stat().st_size > 64_000:
            raise ValueError("Preferences too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["version"] != 1:
            raise ValueError("Unsupported preferences version")
        values = data["settings"]
        for name in ("equity_symbols", "crypto_symbols"):
            if name in values:
                if not isinstance(values[name], list) or any(not isinstance(s, str) for s in values[name]):
                    raise ValueError("Invalid watchlist")
                values[name] = tuple(values[name])
        result = AgentPreferences(AgentSettings(**values), PaperSetup(**data["paper"]))
        result.settings.validate()
        result.paper.validate()
        return result, ""
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return AgentPreferences(), "Saved Agent settings could not be loaded. Defaults shown; the saved file was kept."

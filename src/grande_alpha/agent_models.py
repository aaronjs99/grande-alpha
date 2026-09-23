"""Research-agent contracts. Proposals are deliberately distinct from broker orders."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from grande_alpha.models import Quote


class AssetClass(StrEnum):
    EQUITY = "equity"
    CRYPTO = "crypto"


@dataclass(frozen=True)
class Instrument:
    asset_class: AssetClass
    symbol: str
    provider_id: str = ""
    source: str = "watchlist"

    @property
    def key(self) -> str:
        return f"{self.asset_class.value}:{self.symbol}"


def parse_symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(dict.fromkeys(item.upper() for item in re.split(r"[\s,]+", value.strip()) if item))
    if len(symbols) > 40 or any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-/]{0,19}", s) for s in symbols):
        raise ValueError("Use at most 40 ticker symbols separated by commas or spaces")
    return symbols


@dataclass(frozen=True)
class AgentSettings:
    equity_symbols: tuple[str, ...] = ("QQQ", "TQQQ", "SQQQ")
    # Empty means discover Robinhood's supported USD pairs, then rotate bounded batches.
    crypto_symbols: tuple[str, ...] = ()
    scan_id: str = ""
    interval_seconds: int = 30
    local_ai_model: str = ""
    local_ai_enabled: bool = False
    max_quote_age_seconds: float = 15.0
    equity_max_spread_bps: float = 20.0
    crypto_max_spread_bps: float = 100.0

    def validate(self) -> None:
        if type(self.interval_seconds) is not int or not 15 <= self.interval_seconds <= 300:
            raise ValueError("Agent cycles must be 15–300 seconds apart")
        for symbols in (self.equity_symbols, self.crypto_symbols):
            if parse_symbols(",".join(symbols)) != symbols:
                raise ValueError("Agent symbols must be unique uppercase tickers")
        if len(self.scan_id) > 128 or any(c.isspace() for c in self.scan_id):
            raise ValueError("Saved scan ID must contain at most 128 characters without whitespace")
        if self.local_ai_enabled and not re.fullmatch(r"[\w./:\-]{1,120}", self.local_ai_model):
            raise ValueError("Enter the name of an installed local Ollama model")
        for value in (self.max_quote_age_seconds, self.equity_max_spread_bps, self.crypto_max_spread_bps):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1000:
                raise ValueError("Agent quote limits must be finite and positive")


@dataclass(frozen=True)
class AgentDecision:
    instrument: Instrument
    quote: Quote | None
    action: str
    reason: str
    risk_status: str
    samples: int = 0
    change_bps: float | None = None
    analyst: str = "Rules baseline"
    execution_status: str = "Not submitted — multi-market execution is not validated"


@dataclass(frozen=True)
class AgentSnapshot:
    running: bool = False
    cycle: int = 0
    phase: str = "Idle"
    analyst: str = "Rules baseline"
    observed_at: datetime | None = None
    decisions: tuple[AgentDecision, ...] = ()
    market_status: dict[str, str] = field(default_factory=dict)
    execution_status: str = "Live stocks/crypto agent execution is not available in this build"

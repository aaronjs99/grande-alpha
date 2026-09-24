"""Research-agent contracts. Proposals are deliberately distinct from broker orders."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from grande_alpha.crypto_models import CryptoPairRules
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
    crypto_rules: CryptoPairRules | None = None

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
    interval_seconds: int = 5
    local_ai_model: str = ""
    local_ai_enabled: bool = False
    research_brief: str = ""
    equity_brief: str = ""
    crypto_brief: str = ""
    max_quote_age_seconds: float = 15.0
    equity_max_spread_bps: float = 20.0
    crypto_max_spread_bps: float = 100.0
    news_enabled: bool = False
    social_enabled: bool = False
    twitter_enabled: bool = False
    paper_strategy: str = "legacy"
    paper_entries_paused: bool = False
    paper_max_positions: int = 4
    paper_max_exposure_pct: int = 40

    def validate(self) -> None:
        if type(self.paper_entries_paused) is not bool or type(self.local_ai_enabled) is not bool:
            raise ValueError("AI and paper pause settings must be true or false")
        if type(self.paper_max_positions) is not int or not 1 <= self.paper_max_positions <= 4:
            raise ValueError("Adaptive paper positions must be between 1 and 4")
        if type(self.paper_max_exposure_pct) is not int or not 5 <= self.paper_max_exposure_pct <= 40:
            raise ValueError("Adaptive paper exposure must be between 5% and 40%")
        if not isinstance(self.local_ai_model, str) or len(self.local_ai_model) > 120 or "\x00" in self.local_ai_model:
            raise ValueError("Use an Ollama model name of at most 120 characters")
        if self.paper_strategy not in {"legacy", "adaptive"}:
            raise ValueError("Choose the legacy or adaptive paper strategy")
        if any(type(value) is not bool for value in (self.news_enabled, self.social_enabled, self.twitter_enabled)):
            raise ValueError("News and social settings must be true or false")
        if self.social_enabled and not self.news_enabled:
            raise ValueError("Enable news research before adding social context")
        for brief in (self.research_brief, self.equity_brief, self.crypto_brief):
            if not isinstance(brief, str) or len(brief) > 2000 or "\x00" in brief:
                raise ValueError("Each research prompt must be at most 2,000 characters without NUL")
        if type(self.interval_seconds) is not int or not 5 <= self.interval_seconds <= 300:
            raise ValueError("Quote checks must be 5–300 seconds apart")
        for symbols in (self.equity_symbols, self.crypto_symbols):
            if parse_symbols(",".join(symbols)) != symbols:
                raise ValueError("Agent symbols must be unique uppercase tickers")
        if not isinstance(self.scan_id, str) or len(self.scan_id) > 128 or any(c.isspace() for c in self.scan_id):
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
    buy_allowed: bool = True
    source_context: dict | None = None
    strategy_context: dict | None = None


@dataclass(frozen=True)
class AgentSnapshot:
    running: bool = False
    cycle: int = 0
    phase: str = "Idle"
    analyst: str = "Rules baseline"
    observed_at: datetime | None = None
    decisions: tuple[AgentDecision, ...] = ()
    market_status: dict[str, str] = field(default_factory=dict)
    worker_status: dict[str, str] = field(default_factory=dict)
    execution_status: str = "Live stocks/crypto agent execution is not available in this build"
    paper: dict | None = None
    session_id: str = ""
    started_at: datetime | None = None
    elapsed_seconds: float = 0
    team_status: dict[str, str] = field(default_factory=dict)
    team_events: tuple[dict, ...] = ()
    research_sources: dict | None = None
    next_cycle_at: datetime | None = None
    error: str = ""
    analysis_status: dict[str, str] = field(default_factory=dict)
    analysis_last_result: dict[str, str] = field(default_factory=dict)
    sources_loading: bool = False
    cycle_started_at: datetime | None = None
    diagnostics: str = ""

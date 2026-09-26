"""Build mixed-strategy inputs from broker quotes and locally observed earnings facts.

Price history is collected only while the user-started process runs. Missing or
stale inputs produce no candidate; they never get replaced with estimated facts.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grande_alpha.domain.clock import utc_now
from grande_alpha.execution.read_retry import read_with_backoff

ETFS = {"TQQQ", "SQQQ"}


class LiveDataService:
    def __init__(self, path: Path, broker, earnings, scope, policy, thresholds,
                 *, earnings_client=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS quote_samples ("
                            "symbol TEXT NOT NULL, quote_at TEXT NOT NULL, mid REAL NOT NULL, "
                            "spread_bps REAL NOT NULL, PRIMARY KEY(symbol,quote_at))")
        self.broker, self.earnings = broker, earnings
        self.scope, self.policy, self.thresholds = scope, policy, thresholds
        self.earnings_client = earnings_client

    def close(self) -> None:
        self.db.close()

    def _history(self, symbol: str, now: datetime) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT quote_at,mid FROM quote_samples WHERE symbol=? AND quote_at>=? "
            "ORDER BY quote_at",
            (symbol, (now.astimezone(UTC) - timedelta(hours=2)).isoformat()),
        ).fetchall()

    def _volatility(self, rows: list[sqlite3.Row]) -> float | None:
        if len(rows) < 6:
            return None
        first, last = (datetime.fromisoformat(rows[index]["quote_at"]) for index in (0, -1))
        span_minutes = (last - first).total_seconds() / 60
        if span_minutes < 30:
            return None
        gaps = [(datetime.fromisoformat(right["quote_at"]) -
                 datetime.fromisoformat(left["quote_at"])).total_seconds() / 60
                for left, right in zip(rows, rows[1:], strict=False)]
        if max(gaps) > 15:
            return None
        squared_returns = sum(math.log(right["mid"] / left["mid"]) ** 2
                              for left, right in zip(rows, rows[1:], strict=False))
        return max(self.policy.volatility_floor,
                   math.sqrt(squared_returns * 390 / span_minutes))

    async def snapshot(self) -> dict:
        now = utc_now()
        symbols = sorted(set(self.scope.allowed_symbols) | {"QQQ"})
        quotes = await read_with_backoff(lambda: self.broker.get_quotes(symbols))
        fresh = {}
        for symbol, quote in quotes.items():
            quote.validate()
            if (symbol in symbols and quote.latest_book_timestamp is not None
                    and 0 <= quote.age_seconds(now) <= self.scope.max_quote_age_seconds):
                fresh[symbol] = quote
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO quote_samples VALUES(?,?,?,?)",
                                    (symbol, quote.latest_book_timestamp.astimezone(UTC).isoformat(),
                                     quote.mid, quote.spread_bps))
        qqq = fresh.get("QQQ")
        qqq_rows = self._history("QQQ", now) if qqq else []
        market_vol = self._volatility(qqq_rows)
        momentum = qqq_rows[-1]["mid"] / qqq_rows[0]["mid"] - 1 if market_vol is not None else 0.0
        regime = "bull" if momentum >= .003 else "bear" if momentum <= -.003 else "neutral"
        confidence = min(1.0, abs(momentum) / .005) if market_vol is not None else 0.0
        stock_symbols = set(self.scope.allowed_symbols) - ETFS - {"QQQ"}
        if self.earnings_client is None:
            observation_coverage = {
                symbol: {dataset: {"status": "unconfigured", "quarterly_facts": 0}
                         for dataset in ("EARNINGS", "EARNINGS_ESTIMATES")}
                for symbol in sorted(stock_symbols)
            }
        else:
            observation_coverage = await self.earnings.refresh_observations(
                stock_symbols, self.earnings_client, now=now)
        events = []
        missing = []
        for event in self.earnings.events_for_symbols(stock_symbols):
            symbol = event["symbol"]
            quote = fresh.get(symbol)
            if quote is None or quote.latest_book_timestamp <= datetime.fromisoformat(event["post_at"]):
                missing.append(symbol)
                continue
            refreshed = dict(event)
            refreshed.update({"bid": quote.bid, "ask": quote.ask,
                              "quote_at": quote.latest_book_timestamp.astimezone(UTC).isoformat(),
                              "quote_available_at": now.isoformat()})
            events.append(refreshed)
        event_symbols = {event["symbol"] for event in events}
        missing.extend(sorted(stock_symbols - event_symbols))
        risk = []
        for symbol in sorted(event_symbols):
            history = self._history(symbol, now)
            vol = self._volatility(history)
            if vol is None:
                missing.append(f"{symbol}:price_history")
                continue
            # An unknown sector shares one conservative bucket with all other
            # unclassified stocks; it cannot receive diversified-sector credit.
            risk.append({"symbol": symbol, "sector": "Unclassified", "daily_volatility": vol,
                         "available_at": fresh[symbol].latest_book_timestamp.astimezone(UTC).isoformat()})
        request = {
            "capital_usd": 1.0,  # Replaced by the reconciled broker value inside the engine.
            "policy": asdict(self.policy),
            "earnings": {"schema_version": 1, "as_of": now.isoformat(),
                         "thresholds": dict(self.thresholds), "events": events},
            "market": {"observed_at": (qqq.latest_book_timestamp if qqq else now).astimezone(UTC).isoformat(),
                       "available_at": now.isoformat(), "regime": regime,
                       "confidence": confidence, "daily_volatility": market_vol or self.policy.volatility_floor,
                       "spread_bps": qqq.spread_bps if qqq else self.policy.max_spread_bps + 1},
            "stock_risk": risk,
            "holdings": [],  # Replaced with reconciled inventory by the engine.
        }
        return {"request": request,
                "theses": {symbol: symbol in event_symbols for symbol in self.scope.allowed_symbols
                           if symbol not in ETFS} | {"TQQQ": True, "SQQQ": True},
                "coverage": {"market_history_ready": market_vol is not None,
                             "stock_events": len(events), "stock_risk": len(risk),
                             "earnings_observations": observation_coverage,
                             "missing": sorted(set(missing))}}

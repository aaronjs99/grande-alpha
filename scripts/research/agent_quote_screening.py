"""Continuous stocks/crypto discovery, analysis and independent data-risk screening.

This runtime has read callbacks only. It cannot inherit an ETF session grant or
turn a model proposal into an order. Multi-market execution needs its own verified
contracts, account mapping, risk ledger, and strategy evidence before that changes.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime

from grande_alpha.domain.market_models import Quote
from grande_alpha.domain.policy import market_session_allowed
from grande_alpha.research.agent_models import (
    AgentDecision,
    AgentSettings,
    AssetClass,
    Instrument,
)
from grande_alpha.strategy.paper import (
    HISTORY_SECONDS,
)

PAPER_FOCUS_SECONDS = 120


class AgentQuoteScreening:
    @staticmethod
    def _batch(items: list[Instrument], cycle: int) -> list[Instrument]:
        if not items:
            return []
        start = ((cycle - 1) * 20) % len(items)
        return (items + items)[start : start + min(20, len(items))]

    def _paper_batch(self, items: list[Instrument], cycle: int) -> list[Instrument]:
        if self.adaptive_paper:
            return self._adaptive_batch(items, cycle)
        state = self.paper.state or {}
        keys = set(state.get('positions', {})) | set(state.get('pending', {}))
        priority = self._batch([i for i in items if i.key in keys], cycle)
        job = self._ai_jobs.get(items[0].asset_class) if items else None
        analyzing = {o['key'] for o in job['observations']} if job else set()
        followup = [i for i in items if i.key in analyzing and i.key not in keys]
        candidates = self._batch([i for i in items if i.key not in keys | analyzing], cycle)
        return (priority + followup + candidates)[:20]

    def _adaptive_batch(self, items: list[Instrument], cycle: int) -> list[Instrument]:
        """Keep a bounded group on every poll long enough to observe a price signal.

        Rotating 20 names every poll can leave each name more than 30 seconds
        between observations, making the adaptive breakout condition impossible.
        Holdings and pending intents always take precedence over discovery.
        """
        if not items:
            return []
        market, now = items[0].asset_class, self._now()
        by_key = {item.key: item for item in items}
        state = self.paper.state or {}
        held = set(state.get("positions", {})) | set(state.get("pending", {}))
        priority = self._batch([item for key, item in by_key.items() if key in held], cycle)
        slots = max(0, 20 - len(priority))
        started, previous = self._paper_focus.get(market, (now, ()))
        focus = [key for key in previous if key in by_key and key not in held]
        if not focus or not 0 <= (now - started).total_seconds() < PAPER_FOCUS_SECONDS:
            candidates = [key for key in sorted(by_key) if key not in held]
            after = self._paper_scan_after.get(market, "")
            start = next((i for i, key in enumerate(candidates) if key > after), 0)
            focus = (candidates[start:] + candidates[:start])[:slots]
            self._paper_focus[market] = (now, tuple(focus))
            if focus:
                self._paper_scan_after[market] = focus[-1]
        # Resolve the saved names to this poll's metadata so new halts or
        # restrictions are still checked, including for a pending virtual buy.
        return priority + [by_key[key] for key in focus[:slots]]

    def _inspect(
        self, instrument: Instrument, quote: Quote | None, now: datetime, settings: AgentSettings | None = None
    ) -> AgentDecision:
        settings = settings or self.settings
        if instrument.key not in self._history and len(self._history) >= 400:
            # Discovery universes can change on every scan; cap long-running memory.
            del self._history[next(iter(self._history))]
        # Keep enough distinct quotes to span 60s even at the fastest supported cadence.
        window = HISTORY_SECONDS if self.adaptive_paper else 120
        history_size = 12 if self.paper_source == "demo" else max(12, math.ceil(window / settings.interval_seconds) + 1)
        history = self._history.setdefault(instrument.key, deque(maxlen=history_size))
        reason = ""
        if instrument.crypto_rules is not None:
            # A research buy proposal cannot override current pair restrictions.
            reason = instrument.crypto_rules.restriction("buy", self._crypto_account_type())
        if reason:
            history.clear()
            return AgentDecision(instrument, quote, "hold", reason, "Blocked", analyst=self.snapshot.analyst)
        if quote is None:
            reason = "Broker omitted the requested quote"
        else:
            try:
                quote.validate()
                if not math.isfinite(quote.mid) or not math.isfinite(quote.spread_bps):
                    raise ValueError("Quote midpoint and spread must be finite")
                if quote.symbol != instrument.symbol:
                    raise ValueError("Quote identity does not match the candidate")
                if instrument.asset_class == AssetClass.CRYPTO:
                    clocks = [t for t in (quote.timestamp, quote.bid_timestamp, quote.ask_timestamp) if t is not None]
                    timestamp, newest = min(clocks), max(clocks)
                else:
                    timestamp = quote.book_timestamp or quote.timestamp
                    newest = quote.latest_book_timestamp or quote.timestamp
                age = (now - timestamp).total_seconds()
                if (newest - now).total_seconds() > 2:
                    reason = "Quote timestamp is in the future"
                elif age > settings.max_quote_age_seconds:
                    reason = "Quote is stale"
                elif quote.spread_bps > (
                    settings.equity_max_spread_bps
                    if instrument.asset_class == AssetClass.EQUITY
                    else settings.crypto_max_spread_bps
                ):
                    limit = (settings.equity_max_spread_bps if instrument.asset_class == AssetClass.EQUITY
                             else settings.crypto_max_spread_bps)
                    reason = (f"Spread {quote.spread_bps / 100:.3f}% exceeds {limit / 100:.3f}% limit "
                              f"(bid {quote.bid:.10g}; ask {quote.ask:.10g})")
                elif instrument.asset_class == AssetClass.EQUITY and not market_session_allowed(
                    now, 0, 0, "regular_hours"
                ):
                    reason = "Equity regular session is closed"
                elif history and timestamp <= history[-1][0]:
                    reason = "Waiting for a newer broker quote"
                else:
                    if history and (timestamp - history[-1][0]).total_seconds() > 600:
                        history.clear()
                    history.append((timestamp, quote.mid))
                    if self.adaptive_paper:
                        while len(history) > 1 and (timestamp - history[0][0]).total_seconds() > HISTORY_SECONDS:
                            history.popleft()
            except (ValueError, TypeError, OverflowError) as exc:
                reason = str(exc)
        if reason:
            # Invalid observations break the contiguous evidence window. Repeats don't add samples.
            if reason != "Waiting for a newer broker quote":
                history.clear()
            return AgentDecision(
                instrument, quote, "hold", reason, "Blocked", len(history), analyst=self.snapshot.analyst
            )
        if len(history) < 4 or (history[-1][0] - history[0][0]).total_seconds() < 60:
            return AgentDecision(
                instrument,
                quote,
                "hold",
                f"Collecting at least 4 distinct quotes spanning 60 seconds "
                f"({len(history)} quotes, {(history[-1][0] - history[0][0]).total_seconds():.1f}s so far)",
                "Warming up",
                len(history),
                analyst=self.snapshot.analyst,
            )
        change = (history[-1][1] / history[0][1] - 1) * 10_000
        if not math.isfinite(change):
            history.clear()
            return AgentDecision(
                instrument,
                quote,
                "hold",
                "Nonfinite observed price change",
                "Blocked",
                analyst=self.snapshot.analyst,
            )
        threshold = max(20, 2 * quote.spread_bps)
        action = "buy" if change > threshold else "exit" if change < -threshold else "hold"
        return AgentDecision(
            instrument,
            quote,
            action,
            f"Observed midpoint change {change:+.1f} bps; research threshold {threshold:.1f} bps",
            "Data checks passed",
            len(history),
            change,
            self.snapshot.analyst,
        )

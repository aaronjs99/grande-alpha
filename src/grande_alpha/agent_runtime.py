"""Continuous stocks/crypto discovery, analysis and independent data-risk screening.

This runtime has read callbacks only. It cannot inherit an ETF session grant or
turn a model proposal into an order. Multi-market execution needs its own verified
contracts, account mapping, risk ledger, and strategy evidence before that changes.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from datetime import datetime

from grande_alpha.agent_analyst import OllamaAnalyst
from grande_alpha.agent_models import (
    AgentDecision,
    AgentSettings,
    AgentSnapshot,
    AssetClass,
    Instrument,
)
from grande_alpha.models import Quote, utc_now
from grande_alpha.policy import market_session_allowed


class AgentRuntime:
    def __init__(
        self,
        *,
        equity_quotes: Callable[[list[str]], Awaitable[dict[str, Quote]]],
        crypto_pairs: Callable[[], Awaitable[list[Instrument]]],
        crypto_quotes: Callable[[list[Instrument]], Awaitable[dict[str, Quote]]],
        equity_scan: Callable[[str], Awaitable[list[Instrument]]],
        connected: Callable[[], bool],
        changed: Callable[[AgentSnapshot], None],
        log: Callable,
        clock: Callable[[], datetime] = utc_now,
        analyst: OllamaAnalyst | None = None,
    ) -> None:
        self._equity_quotes = equity_quotes
        self._crypto_pairs = crypto_pairs
        self._crypto_quotes = crypto_quotes
        self._equity_scan = equity_scan
        self._connected = connected
        self._changed = changed
        self._log = log
        self._clock = clock
        self._analyst = analyst or OllamaAnalyst()
        self._task: asyncio.Task | None = None
        self._cycle_lock = asyncio.Lock()
        self._history: dict[str, deque[tuple[datetime, float]]] = {}
        self._pairs: list[Instrument] | None = None
        self._session_id = ""
        self.settings = AgentSettings()
        self.snapshot = AgentSnapshot()

    def _publish(self, **values) -> None:
        self.snapshot = replace(self.snapshot, **values)
        self._changed(self.snapshot)

    def start(self, settings: AgentSettings) -> None:
        settings.validate()
        if not self._connected():
            raise ValueError("Connect the consented Robinhood account before starting the agent")
        if self.snapshot.running:
            raise ValueError("Stop the current agent run before changing its settings")
        loop = asyncio.get_running_loop()
        self.settings = settings
        self._history.clear()
        self._pairs = None
        self._session_id = str(uuid.uuid4())
        self.snapshot = AgentSnapshot()
        self._publish(
            running=True,
            phase="Starting",
            analyst=f"Local AI · {settings.local_ai_model}"
            if settings.local_ai_enabled
            else "Rules baseline",
        )
        self._log("Agent started: stocks/ETFs + crypto; proposals only", category="agent_session")
        self._task = loop.create_task(self._run(), name="grande-multi-market-agent")

    def stop(self, reason: str = "Agent stopped") -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        if self.snapshot.running:
            self._publish(running=False, phase="Stopped")
            self._log(reason, category="agent_session")

    async def _run(self) -> None:
        try:
            while self.snapshot.running:
                if not self._connected():
                    self._publish(running=False, phase="Disconnected")
                    break
                await self.cycle()
                await asyncio.sleep(self.settings.interval_seconds)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._publish(running=False, phase="Error")
            self._log(f"Agent stopped: {type(exc).__name__}", "error", "agent_session")

    @staticmethod
    def _batch(items: list[Instrument], cycle: int) -> list[Instrument]:
        if not items:
            return []
        start = ((cycle - 1) * 20) % len(items)
        return (items + items)[start : start + min(20, len(items))]

    def _inspect(self, instrument: Instrument, quote: Quote | None, now: datetime) -> AgentDecision:
        settings = self.settings
        if instrument.key not in self._history and len(self._history) >= 400:
            # Discovery universes can change on every scan; cap long-running memory.
            del self._history[next(iter(self._history))]
        history = self._history.setdefault(instrument.key, deque(maxlen=12))
        reason = ""
        if quote is None:
            reason = "Broker omitted the requested quote"
        else:
            try:
                quote.validate()
                if not math.isfinite(quote.mid) or not math.isfinite(quote.spread_bps):
                    raise ValueError("Quote midpoint and spread must be finite")
                if quote.symbol != instrument.symbol:
                    raise ValueError("Quote identity does not match the candidate")
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
                    reason = "Spread exceeds this market's research limit"
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
                "Collecting at least 4 distinct quotes spanning 60 seconds",
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

    async def cycle(self) -> None:
        """One completion-gated read cycle; concurrent calls are coalesced."""
        if self._cycle_lock.locked():
            return
        async with self._cycle_lock:
            if not self._connected():
                return
            cycle = self.snapshot.cycle + 1
            self._publish(phase="Scanning", cycle=cycle, decisions=(), observed_at=None)
            decisions: list[AgentDecision] = []
            status: dict[str, str] = {}
            for asset_class in (AssetClass.EQUITY, AssetClass.CRYPTO):
                try:
                    if asset_class == AssetClass.EQUITY:
                        instruments = [
                            Instrument(asset_class, symbol) for symbol in self.settings.equity_symbols
                        ]
                        if self.settings.scan_id:
                            instruments += await self._equity_scan(self.settings.scan_id)
                        instruments = list({item.key: item for item in instruments}.values())
                    else:
                        # Recheck supported pairs periodically without flooding the provider.
                        if self._pairs is None or cycle % 20 == 0:
                            self._pairs = await self._crypto_pairs()
                        wanted = {
                            s.replace("/", "-") if "-" in s or "/" in s else f"{s}-USD"
                            for s in self.settings.crypto_symbols
                        }
                        instruments = [item for item in self._pairs if not wanted or item.symbol in wanted]
                        missing = wanted - {item.symbol for item in instruments}
                        if missing:
                            raise ValueError("Pairs not returned by Robinhood: " + ", ".join(sorted(missing)))
                    if any(item.asset_class != asset_class for item in instruments):
                        raise ValueError("Discovery returned a different asset class")
                    batch = self._batch(instruments, cycle)
                    quotes = (
                        await self._equity_quotes([item.symbol for item in batch])
                        if asset_class == AssetClass.EQUITY and batch
                        else await self._crypto_quotes(batch)
                        if batch
                        else {}
                    )
                    now = self._clock()
                    decisions += [self._inspect(item, quotes.get(item.symbol), now) for item in batch]
                    status[asset_class.value] = f"Observed {len(batch)} of {len(instruments)} candidates"
                except Exception as exc:
                    # A crypto failure must not stop equity discovery (or vice versa).
                    for key in list(self._history):
                        if key.startswith(f"{asset_class.value}:"):
                            del self._history[key]
                    status[asset_class.value] = f"Unavailable: {str(exc)[:240]}"
            self._publish(phase="Analyzing", market_status=status)
            eligible = [item for item in decisions if item.risk_status == "Data checks passed"]
            if self.settings.local_ai_enabled and eligible:
                observations = [
                    {
                        "key": item.instrument.key,
                        "bid": item.quote.bid,
                        "ask": item.quote.ask,
                        "spread_bps": item.quote.spread_bps,
                        "change_bps": item.change_bps,
                        "samples": item.samples,
                        "observations": [
                            {"at": at.isoformat(), "mid": mid}
                            for at, mid in self._history[item.instrument.key]
                        ],
                    }
                    for item in eligible
                ]
                try:
                    proposals = await self._analyst.analyze(self.settings.local_ai_model, observations)
                    decisions = [
                        replace(
                            item,
                            action=proposals[item.instrument.key][0],
                            reason=proposals[item.instrument.key][1],
                        )
                        if item in eligible
                        else item
                        for item in decisions
                    ]
                except Exception:
                    decisions = [
                        replace(
                            item,
                            action="hold",
                            reason="Local AI unavailable or invalid response; no proposal accepted",
                            risk_status="Blocked",
                        )
                        if item in eligible
                        else item
                        for item in decisions
                    ]
            now = self._clock()
            if not self._connected():
                self._publish(running=False, phase="Disconnected", decisions=())
                return
            # Model latency can age observations. Recheck independently after analysis.
            decisions = [
                replace(item, action="hold", risk_status="Blocked", reason="Quote expired during analysis")
                if item.risk_status == "Data checks passed"
                and item.quote.age_seconds(now) > self.settings.max_quote_age_seconds
                else item
                for item in decisions
            ]
            decisions = [
                replace(
                    item, action="hold", risk_status="Blocked", reason="Equity session closed during analysis"
                )
                if item.risk_status == "Data checks passed"
                and item.instrument.asset_class == AssetClass.EQUITY
                and not market_session_allowed(now, 0, 0, "regular_hours")
                else item
                for item in decisions
            ]
            self._publish(phase="Waiting", observed_at=now, decisions=tuple(decisions), market_status=status)
            self._log(
                f"Agent cycle {cycle}: {len(decisions)} candidates; "
                f"{sum(item.action != 'hold' for item in decisions)} proposals; no orders submitted",
                category="agent_cycle",
                payload={
                    "session_id": self._session_id,
                    "cycle": cycle,
                    "analyst": self.snapshot.analyst,
                    "markets": status,
                    "decisions": [asdict(item) for item in decisions],
                },
            )

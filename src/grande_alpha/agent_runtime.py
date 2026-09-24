"""Continuous stocks/crypto discovery, analysis and independent data-risk screening.

This runtime has read callbacks only. It cannot inherit an ETF session grant or
turn a model proposal into an order. Multi-market execution needs its own verified
contracts, account mapping, risk ledger, and strategy evidence before that changes.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from datetime import datetime, timedelta

import httpx

from grande_alpha.agent_analyst import (
    AI_MAX_ANALYSIS_AGE_SECONDS,
    AI_MAX_PRICE_DRIFT_BPS,
    AI_REQUEST_TIMEOUT_SECONDS,
    AnalystResponseError,
    OllamaAnalyst,
)
from grande_alpha.agent_diagnostics import completed_check_report
from grande_alpha.agent_models import (
    AgentDecision,
    AgentSettings,
    AgentSnapshot,
    AssetClass,
    Instrument,
)
from grande_alpha.agent_paper import DEMO_CYCLES, PaperLedger, demo_market, demo_time, validate_paper_settings
from grande_alpha.agent_sources import NEWS_POLICY, REFRESH_SECONDS, ResearchSources
from grande_alpha.agent_strategy import (
    ADAPTIVE_POLICY,
    HISTORY_SECONDS,
    adaptive_decision,
    limit_entries,
    pause_entries,
)
from grande_alpha.models import Quote, utc_now
from grande_alpha.policy import market_session_allowed

MARKET_WORKER_TIMEOUT_SECONDS = 35.0
DEMO_INTERVAL_SECONDS = 1.0
AI_TIMEOUT_SECONDS = AI_REQUEST_TIMEOUT_SECONDS


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
        crypto_account_type: Callable[[], str] = lambda: "",
        paper: PaperLedger | None = None,
        sources: ResearchSources | None = None,
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
        self.sources = sources or ResearchSources(clock=clock)
        self._crypto_account_type = crypto_account_type
        self._task: asyncio.Task | None = None
        self._cycle_lock = asyncio.Lock()
        self._history: dict[str, deque[tuple[datetime, float]]] = {}
        self._pairs: list[Instrument] | None = None
        self._session_id = ""
        self._generation = 0
        self.settings = AgentSettings(paper_strategy="adaptive")
        self.paper = paper or PaperLedger()
        self.paper_source: str | None = None
        self.loop_demo = False
        self._event_sequence = 0
        self._background_tasks: set[asyncio.Task] = set()
        self._ai_jobs: dict[AssetClass, dict] = {}
        self._ai_due: dict[AssetClass, datetime] = {}
        self._source_task: asyncio.Task | None = None
        self._source_due: datetime | None = None
        self._source_failed = False
        self.snapshot = AgentSnapshot(paper=self.paper.summary())

    @property
    def continuous_paper(self) -> bool:
        return self.paper_source == "broker_quotes"

    @property
    def adaptive_paper(self) -> bool:
        return self.continuous_paper and self.settings.paper_strategy == "adaptive"

    def _source_context(self, instrument, now, settings):
        return {**self.sources.context(instrument, now),
                "news_required": settings.news_enabled and not self.adaptive_paper,
                "usage": "context and headline-risk checks" if self.adaptive_paper else "entry confirmation"}

    def _background(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        owned = self._background_tasks
        owned.add(task)
        task.add_done_callback(owned.discard)
        return task

    async def _refresh_sources(self, symbols, settings) -> bool:
        try:
            await self.sources.refresh(symbols, social=settings.social_enabled,
                                       news=settings.news_enabled, twitter=settings.twitter_enabled)
            return True
        except Exception:
            return False

    def _finish_sources(self, task, generation) -> None:
        if generation != self._generation or task is not self._source_task or not self.snapshot.running or not self._available():
            return
        self._source_failed = task.cancelled() or not task.result()
        self._source_task = None
        report = self.sources.summary()
        refreshed = datetime.fromisoformat(report['refreshed_at']) if report.get('refreshed_at') and not self._source_failed else self._now()
        self._source_due = max(refreshed + timedelta(seconds=REFRESH_SECONDS), self._now() + timedelta(seconds=1))
        if self._source_failed:
            report = {**report, "items": [], "sources": [{"source": "Research reader", "status": "Unavailable", "fresh_items": 0}]}
        self._publish(research_sources=report, sources_loading=False)
        self._handoff("VELA", "KADE", f"{len(report['items'])} dated items · "
                      f"{sum(s['status'] == 'OK' for s in report['sources'])}/{len(report['sources'])} feeds available", "NEWS")

    def _poll_sources(self, symbols, settings) -> None:
        if self._source_task is None and (self._source_due is None or self._now() >= self._source_due):
            self._source_due = self._now() + timedelta(seconds=REFRESH_SECONDS)
            self._source_task = self._background(self._refresh_sources(symbols, settings))
            generation = self._generation
            self._source_task.add_done_callback(lambda task: self._finish_sources(task, generation))
            self._publish(sources_loading=True)

    async def _ask_analyst(self, settings, observations, prompts):
        started = asyncio.get_running_loop().time()
        proposals, error = None, ""
        try:
            async with asyncio.timeout(AI_TIMEOUT_SECONDS):
                proposals = await self._analyst.analyze(settings.local_ai_model, observations, **prompts)
            if set(proposals) != {o['key'] for o in observations} or any(
                not isinstance(p, (tuple, list)) or len(p) != 2 or p[0] not in {'buy', 'hold', 'exit'}
                or not isinstance(p[1], str) or not p[1].strip() for p in proposals.values()
            ):
                raise ValueError("Unexpected model proposals")
        except (TimeoutError, httpx.TimeoutException):
            error = f"AI request timed out ({AI_TIMEOUT_SECONDS:g}s maximum)"
        except httpx.ConnectError:
            error = "Ollama is unreachable; open the Ollama app"
        except httpx.HTTPStatusError as exc:
            error = f"Ollama returned HTTP {exc.response.status_code}; check the model and Ollama server log"
        except AnalystResponseError as exc:
            error = str(exc)
        except (ValueError, TypeError, KeyError):
            error = "AI returned an invalid decision format or source citation"
        except Exception:
            error = "AI request failed; check the Ollama server log"
        return {"proposals": None if error else proposals, "error": error,
                "seconds": asyncio.get_running_loop().time() - started}

    def _analysis_blocker(self, original, item, job, settings):
        """Revalidate a bounded, older analysis against the independently checked live book."""
        now = self._now()
        anchor = datetime.fromisoformat(original['observations'][-1]['at'])
        age = (now - anchor).total_seconds()
        elapsed = (now - job['started_at']).total_seconds()
        if not 0 <= age <= AI_MAX_ANALYSIS_AGE_SECONDS or not 0 <= elapsed <= AI_MAX_ANALYSIS_AGE_SECONDS:
            return f"AI result expired (analysis limit {AI_MAX_ANALYSIS_AGE_SECONDS:g}s)"
        if item.instrument != job['instruments'].get(item.instrument.key):
            return "Instrument details changed during analysis"
        if item.quote is None or item.quote.age_seconds(now) > settings.max_quote_age_seconds:
            return "Current quote is stale; analysis cannot be used"
        # The current candidate already passed _inspect, including halts, spread,
        # all book clocks and market hours. Require continuity and a newer book.
        history = self._history.get(item.instrument.key, ())
        if not any(at == anchor for at, _ in history) or history[-1][0] <= anchor:
            return "Quote history changed during analysis; fresh analysis required"
        input_ids = {a['id'] for a in (original.get('source_context') or {}).get('articles', [])}
        current_ids = {a['id'] for a in (item.source_context or {}).get('articles', [])}
        if not input_ids <= current_ids:
            return "Source context changed during analysis; fresh analysis required"
        mid = original['observations'][-1]['mid']
        moves = [abs(value / mid - 1) * 10_000 for at, value in history if at >= anchor]
        moves += [abs(item.quote.bid / original['bid'] - 1) * 10_000,
                  abs(item.quote.ask / original['ask'] - 1) * 10_000]
        drift = max(moves)
        if not math.isfinite(drift) or drift > AI_MAX_PRICE_DRIFT_BPS:
            return (f"Price moved {drift:.1f} bps during analysis "
                    f"(limit {AI_MAX_PRICE_DRIFT_BPS:g} bps); fresh analysis required")
        return ""

    def _continuous_analysis(self, market, decisions, eligible, settings, observations, prompts):
        job = self._ai_jobs.get(market)
        proposals, inputs, result = None, {}, "Waiting for enough eligible quotes"
        rejected, failure = False, ""
        completed = False
        outcome = None
        accepted = []
        blockers = []
        if job and job['settings'] != settings:
            job['task'].cancel()
            self._ai_jobs.pop(market)
            job = None
        if job and job['task'].done():
            completed = True
            outcome = ({'proposals': None, 'error': 'AI request cancelled', 'seconds': 0.0}
                       if job['task'].cancelled() else job['task'].result())
            proposals, failure = outcome['proposals'], outcome['error']
            inputs = {o['key']: o for o in job['observations']}
            rejected = proposals is None
            result = failure if rejected else "AI result received"
            self._ai_jobs.pop(market)
        output = []
        for item in decisions:
            if item not in eligible:
                if completed and item.instrument.key in inputs:
                    blockers.append(item.reason)
                output.append(item)
                continue
            original = inputs.get(item.instrument.key)
            action, reason, buy_allowed = "hold", "AI is analyzing; quote monitoring continues", not rejected
            if rejected:
                reason = f"AI unavailable: {failure}; quote monitoring continues"
            if proposals is not None and original:
                blocker = self._analysis_blocker(original, item, job, settings)
                if not blocker:
                    action, reason = proposals[item.instrument.key]
                    accepted.append(action)
                    reason += (" · Analysis quote: " + original['observations'][-1]['at']
                               + " · Rechecked against current quote: " + item.quote.timestamp.isoformat())
                else:
                    buy_allowed = False
                    reason = blocker
                    blockers.append(blocker)
            if self.adaptive_paper:
                # The selected price strategy acts every quote. AI proposals are
                # separately labeled context, never a pending/failure trade gate.
                if proposals is not None and original and not blocker:
                    item = replace(item, reason=item.reason + f" · AI context ({action}): " + reason)
                output.append(item)
            else:
                output.append(replace(item, action=action, reason=reason, buy_allowed=buy_allowed))
        if completed:
            if not rejected:
                result = (f"AI reply rechecked: {len(accepted)}/{len(inputs)} usable proposals "
                          f"({accepted.count('buy')} buy, {accepted.count('hold')} hold, {accepted.count('exit')} exit); "
                          "news and paper-fill checks still apply")
                if blockers:
                    result += " · " + blockers[0]
                if self.adaptive_paper:
                    result += " · Advisory only; adaptive price strategy decides paper trades"
            last = (f"{self._now().isoformat()} · {settings.local_ai_model} · "
                    f"request {outcome['seconds']:.1f}s · {result}")
            self._publish(analysis_last_result={**self.snapshot.analysis_last_result, market.value: last})
            job = None
        # Rotate discovery after consuming this batch; immediately re-analyzing
        # the same follow-up quotes would pin the scan to these symbols forever.
        due = self._ai_due.get(market)
        if job is None and eligible and not completed and (not self.adaptive_paper or due is None or self._now() >= due):
            task = self._background(self._ask_analyst(settings, observations, prompts))
            if self.adaptive_paper:
                self._ai_due[market] = self._now() + timedelta(seconds=60)
            self._ai_jobs[market] = {'task': task, 'settings': settings, 'observations': observations,
                                    'started_at': self._now(),
                                    'instruments': {item.instrument.key: item.instrument for item in eligible}}
            result = f"Analyzing latest quotes · 0s / {AI_TIMEOUT_SECONDS:g}s maximum"
        elif job:
            elapsed = max(0, (self._now() - job['started_at']).total_seconds())
            result = f"Analyzing latest quotes · {elapsed:.0f}s / {AI_TIMEOUT_SECONDS:g}s maximum"
        elif self.adaptive_paper and due and not completed and eligible:
            result = f"Next AI context in {max(0, math.ceil((due - self._now()).total_seconds()))}s; price strategy continues"
        self._publish(analysis_status={**self.snapshot.analysis_status, market.value: result})
        return output

    def _now(self) -> datetime:
        return demo_time(self.snapshot.cycle) if self.paper_source == "demo" else self._clock()

    def _available(self) -> bool:
        return self.paper_source == "demo" or self._connected()

    def paper_context(self) -> dict | None:
        return self.paper.summary(active=bool(self.paper_source and self.snapshot.running),
                                  now=self._now(), max_age=self.settings.max_quote_age_seconds)

    def _publish(self, **values) -> None:
        self.snapshot = replace(self.snapshot, **values)
        if self.snapshot.started_at:
            self.snapshot = replace(self.snapshot, elapsed_seconds=max(0, (utc_now() - self.snapshot.started_at).total_seconds()))
        self.snapshot = replace(self.snapshot, paper=self.paper_context())
        self._changed(self.snapshot)

    def _handoff(self, sender: str, recipient: str, message: str, kind: str = "SCAN") -> None:
        self._event_sequence += 1
        event = {"id": self._event_sequence, "from": sender, "to": recipient, "message": message,
                 "kind": kind, "cycle": self.snapshot.cycle, "at": self._now().isoformat()}
        self._publish(team_events=(*self.snapshot.team_events[-119:], event))

    def start(self, settings: AgentSettings) -> None:
        self._start(settings)

    def start_paper(self, settings: AgentSettings, source: str = "demo", initial_cash: float = 1000,
                    trade_cash: float = 100, loop_demo: bool = False) -> None:
        validate_paper_settings(source, initial_cash, trade_cash)
        if type(loop_demo) is not bool or (loop_demo and source != "demo"):
            raise ValueError("Repeat is available only for the offline demo")
        self._start(settings, source, initial_cash, trade_cash, loop_demo)

    def _start(self, settings: AgentSettings, source: str | None = None, initial_cash=1000, trade_cash=100,
               loop_demo: bool = False) -> None:
        settings.validate()
        if source != "demo" and not self._connected():
            raise ValueError("Connect the consented Robinhood account before starting the agent")
        if self.snapshot.running:
            raise ValueError("Stop the current agent run before changing its settings")
        loop = asyncio.get_running_loop()
        if source:
            self.paper.start(source, initial_cash, trade_cash)
            adaptive = source == "broker_quotes" and settings.paper_strategy == "adaptive"
            self.paper.set_strategy({"policy": ADAPTIVE_POLICY if adaptive else NEWS_POLICY if settings.news_enabled and source != "demo" else "price-only",
                                     "paper_strategy": settings.paper_strategy if source == "broker_quotes" else "legacy",
                                     "ai_role": ("advisory" if adaptive else "decision") if settings.local_ai_enabled and source != "demo" else "off",
                                     "news_role": "context and headline-risk checks" if adaptive and settings.news_enabled else
                                                  "two-publisher entry filter" if settings.news_enabled and source != "demo" else "off",
                                     "model": settings.local_ai_model if settings.local_ai_enabled and source != "demo" else "rules",
                                     "social_context": settings.social_enabled and source != "demo",
                                     "twitter_context": settings.twitter_enabled and source != "demo",
                                     "quote_interval_seconds": settings.interval_seconds,
                                     "paper_max_positions": settings.paper_max_positions,
                                     "paper_max_exposure_pct": settings.paper_max_exposure_pct,
                                     "paper_entries_paused": settings.paper_entries_paused,
                                     "continuous": source == "broker_quotes"})
        self.paper_source = source
        self._background_tasks = set()
        self._ai_jobs = {}
        self._ai_due = {}
        self._source_task = None
        self._source_due = None
        self._source_failed = False
        self.loop_demo = loop_demo
        self._event_sequence = 0
        self._generation += 1
        self.settings = settings
        self._history.clear()
        self._pairs = None
        self._session_id = str(uuid.uuid4())
        self.snapshot = AgentSnapshot(session_id=self._session_id, started_at=utc_now())
        self._publish(
            running=True,
            phase="Starting",
            analyst="Demo rules baseline" if source == "demo" else
            ("Adaptive paper trend" + (f" · AI context: {settings.local_ai_model}" if settings.local_ai_enabled else ""))
            if self.adaptive_paper else f"Local AI · {settings.local_ai_model}"
            if settings.local_ai_enabled
            else "Rules baseline",
        )
        if source:
            self._publish(execution_status="Paper simulation only · virtual cash · no broker orders")
        self._log(f"Agent started: stocks/ETFs + crypto; {'paper simulation · ' + source if source else 'proposals only'}", category="agent_session")
        self._task = loop.create_task(self._run(), name="grande-multi-market-agent")
        generation = self._generation
        self._task.add_done_callback(lambda task: self._run_finished(task, generation))

    def _run_finished(self, task: asyncio.Task, generation: int) -> None:
        # Cancellation can occur before _run enters its try/finally. Always
        # retrieve errors, but never let cleanup from an older run stop a new one.
        if not task.cancelled():
            task.exception()
        if task is not self._task or generation != self._generation or not self.snapshot.running:
            return
        self._task = None
        for background in tuple(self._background_tasks):
            background.cancel()
        self._discard_paper_intents()
        self._publish(running=False, phase="Error", next_cycle_at=None, decisions=(),
                      analysis_status={}, sources_loading=False,
                      error="Monitoring was interrupted. Restart monitoring, or reconnect Robinhood if data is unavailable.",
                      worker_status={"equity": "Stopped", "crypto": "Stopped"},
                      team_status={name: "Stopped" for name in self.snapshot.team_status})

    def stop(self, reason: str = "Agent stopped") -> None:
        self._generation += 1
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        for background in tuple(self._background_tasks):
            background.cancel()
        self._discard_paper_intents()
        if self.snapshot.running:
            self._publish(running=False, phase="Stopped", decisions=(), observed_at=None, next_cycle_at=None,
                          analysis_status={}, sources_loading=False,
                          worker_status={"equity": "Stopped", "crypto": "Stopped"},
                          team_status={name: "Stopped" for name in self.snapshot.team_status})
            self._log(reason, category="agent_session")

    def _discard_paper_intents(self) -> None:
        try:
            self.paper.cancel_pending()
        except sqlite3.Error:
            # Paper storage must never obstruct the app's broker STOP/Disconnect.
            self._log("Paper simulation stopped; pending-intent cleanup could not be saved", "warning", "agent_session")

    def _poll_delay(self, elapsed: float, failures: int) -> float:
        delay = max(1.0, self.settings.interval_seconds - elapsed)
        if failures:
            delay = max(delay, min(60, self.settings.interval_seconds * 2 ** min(failures, 4)))
        return delay

    async def _run(self) -> None:
        owned_background = self._background_tasks
        failures = 0
        try:
            while self.snapshot.running:
                if not self._available():
                    self._discard_paper_intents()
                    self._publish(running=False, phase="Disconnected", next_cycle_at=None,
                                  analysis_status={}, sources_loading=False)
                    break
                started = asyncio.get_running_loop().time()
                await self.cycle()
                if not self.snapshot.running:
                    break
                if self.paper_source == "demo" and not self.loop_demo and self.snapshot.cycle >= DEMO_CYCLES:
                    self._discard_paper_intents()
                    self._publish(running=False, phase="Demo complete", next_cycle_at=None,
                                  worker_status={"equity": "Demo complete", "crypto": "Demo complete"},
                                  team_status={name: "Demo complete" for name in self.snapshot.team_status})
                    break
                interval = DEMO_INTERVAL_SECONDS if self.paper_source == "demo" else self.settings.interval_seconds
                if self.continuous_paper:
                    failures = failures + 1 if self.snapshot.market_status and all(
                        s.startswith('Unavailable') for s in self.snapshot.market_status.values()) else 0
                    # Skip missed polls; never queue catch-up scans or overlap a market request.
                    interval = self._poll_delay(asyncio.get_running_loop().time() - started, failures)
                self._publish(next_cycle_at=utc_now() + timedelta(seconds=interval))
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._discard_paper_intents()
            error = ("Could not save session results. Check available disk space and access to the app's data folder."
                     if isinstance(exc, sqlite3.Error) else
                     f"Agent stopped ({type(exc).__name__}). Open Receipts to inspect the last completed step.")
            self._publish(running=False, phase="Error", next_cycle_at=None, error=error,
                          analysis_status={}, sources_loading=False)
            self._log(f"Agent stopped: {type(exc).__name__}", "error", "agent_session")
        finally:
            tasks = tuple(owned_background)
            for background in tasks:
                background.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _batch(items: list[Instrument], cycle: int) -> list[Instrument]:
        if not items:
            return []
        start = ((cycle - 1) * 20) % len(items)
        return (items + items)[start : start + min(20, len(items))]

    def _paper_batch(self, items: list[Instrument], cycle: int) -> list[Instrument]:
        state = self.paper.state or {}
        keys = set(state.get('positions', {})) | set(state.get('pending', {}))
        priority = self._batch([i for i in items if i.key in keys], cycle)
        job = self._ai_jobs.get(items[0].asset_class) if items else None
        analyzing = {o['key'] for o in job['observations']} if job else set()
        followup = [i for i in items if i.key in analyzing and i.key not in keys]
        candidates = self._batch([i for i in items if i.key not in keys | analyzing], cycle)
        return (priority + followup + candidates)[:20]

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

    def set_brief(self, market: str, brief: str) -> None:
        field = {"team": "research_brief", "equity": "equity_brief", "crypto": "crypto_brief"}.get(market)
        if field is None:
            raise ValueError("Choose team, equity, or crypto")
        settings = replace(self.settings, **{field: brief})
        settings.validate()
        self.settings = settings

    async def _market_cycle(self, asset_class: AssetClass, cycle: int, settings: AgentSettings, progress, current):
        progress("Scanning")
        if self.paper_source == "demo":
            instruments, quotes = demo_market(asset_class, cycle)
        elif asset_class == AssetClass.EQUITY:
            instruments = [Instrument(asset_class, symbol) for symbol in settings.equity_symbols]
            if settings.scan_id:
                progress("Loading stock scan")
                instruments += await self._equity_scan(settings.scan_id)
            instruments = list({item.key: item for item in instruments}.values())
        else:
            # Pair halts and restrictions are refreshed on every cycle.
            progress("Loading crypto pairs")
            pairs = await self._crypto_pairs()
            wanted = {
                s.replace("/", "-") if "-" in s or "/" in s else f"{s}-USD"
                for s in settings.crypto_symbols
            }
            instruments = [item for item in pairs if not wanted or item.symbol in wanted]
            missing = wanted - {item.symbol for item in instruments}
            if missing:
                raise ValueError("Pairs not returned by Robinhood: " + ", ".join(sorted(missing)))
        if any(item.asset_class != asset_class for item in instruments):
            raise ValueError("Discovery returned a different asset class")
        batch = self._paper_batch(instruments, cycle) if self.continuous_paper else self._batch(instruments, cycle)
        progress("Requesting quotes")
        quotes = quotes if self.paper_source == "demo" else (
            await self._equity_quotes([item.symbol for item in batch])
            if asset_class == AssetClass.EQUITY and batch
            else await self._crypto_quotes(batch) if batch else {}
        )
        if not current():
            raise asyncio.CancelledError
        name = "NOVA" if asset_class == AssetClass.EQUITY else "ORIN"
        self._handoff(name, "VELA", f"{len(quotes)} {asset_class.value} quotes ready")
        now = self._now()
        decisions = [self._inspect(item, quotes.get(item.symbol), now, settings) for item in batch]
        if self.adaptive_paper:
            decisions = [adaptive_decision(item, self._history.get(item.instrument.key, ()), self.paper.state, now)
                         for item in decisions]
        if (settings.news_enabled or settings.twitter_enabled) and self.paper_source != "demo":
            decisions = [replace(item, source_context=self._source_context(item.instrument, now, settings)) for item in decisions]
        progress("Analyzing")
        eligible = [item for item in decisions if item.risk_status == "Data checks passed"]
        if settings.local_ai_enabled and (eligible or self.continuous_paper) and self.paper_source != "demo":
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
                        for at, mid in list(self._history[item.instrument.key])[-25:]
                    ],
                    **({"price_strategy": item.strategy_context, "ai_role": "advisory"} if self.adaptive_paper else {}),
                    **({"source_context": item.source_context} if item.source_context is not None else {}),
                }
                for item in eligible
            ]
            prompts = {}
            market_brief = settings.equity_brief if asset_class == AssetClass.EQUITY else settings.crypto_brief
            if settings.research_brief or market_brief:
                prompts = {"research_brief": settings.research_brief, "market_brief": market_brief}
            try:
                if self.continuous_paper:
                    decisions = self._continuous_analysis(asset_class, decisions, eligible, settings, observations, prompts)
                else:
                    proposals = await self._analyst.analyze(settings.local_ai_model, observations, **prompts)
                    decisions = [
                        replace(item, action=proposals[item.instrument.key][0], reason=proposals[item.instrument.key][1])
                        if item in eligible else item for item in decisions
                    ]
            except Exception:
                decisions = decisions if self.adaptive_paper else [
                    replace(item, action="hold", reason="Local AI unavailable or invalid response; no proposal accepted", risk_status="Blocked")
                    if item in eligible else item for item in decisions
                ]
        if not current():
            raise asyncio.CancelledError
        progress("Checking data")
        self._handoff("VELA", "KADE", f"{asset_class.value}: {sum(d.risk_status == 'Blocked' for d in decisions)} blocked, "
                      f"{sum(d.risk_status == 'Warming up' for d in decisions)} warming up", "RISK")
        return decisions, f"Observed {len(batch)} of {len(instruments)} candidates"

    def _recheck(self, decisions, settings):
        now = self._now()
        result = []
        for item in decisions:
            if item.risk_status == "Data checks passed":
                if item.quote.age_seconds(now) > settings.max_quote_age_seconds:
                    item = replace(item, action="hold", risk_status="Blocked", reason="Quote expired during analysis")
                elif item.instrument.asset_class == AssetClass.EQUITY and not market_session_allowed(now, 0, 0, "regular_hours"):
                    item = replace(item, action="hold", risk_status="Blocked", reason="Equity session closed during analysis")
            if (settings.news_enabled or settings.twitter_enabled) and self.paper_source != "demo":
                context = self._source_context(item.instrument, now, settings)
                if settings.news_enabled and self.continuous_paper and self._source_failed:
                    context.update(buy_supported=False, coverage="News refresh unavailable")
                supported = not settings.news_enabled or (not context["risk_terms"] if self.adaptive_paper else context["buy_supported"])
                reason = item.reason
                if not supported and item.action == "buy":
                    reason = "News filter: " + ("headline risk terms: " + ", ".join(context["risk_terms"])
                                               if context["risk_terms"] else context["coverage"])
                item = replace(item, buy_allowed=item.buy_allowed and supported, source_context=context,
                               action="hold" if item.action == "buy" and not supported else item.action, reason=reason)
            result.append(item)
        result = limit_entries(result, self.paper.state, self.settings) if self.adaptive_paper else result
        return pause_entries(result, self.settings.paper_entries_paused) if self.paper_source else result

    def _consume_paper(self, decisions, settings):
        # Use current controls even if this quote cycle started before the user applied them.
        if self.adaptive_paper:
            decisions = limit_entries(decisions, self.paper.state, self.settings)
        decisions = pause_entries(decisions, self.settings.paper_entries_paused)
        before = self.paper.state['fill_count']
        self.paper.consume(decisions, self._now(), settings.max_quote_age_seconds)
        for fill in self.paper.state['fills']:
            if fill['number'] > before:
                self._handoff("RUNE", "ZARA", f"PAPER {fill['side'].upper()} {fill['key']} · "
                              f"{float(fill['quantity']):.6g} units @ ${float(fill['price']):,.6g}", "FILL")

    async def cycle(self) -> None:
        """Two concurrent market workers; cycles coalesce and cancellation joins both."""
        if self._cycle_lock.locked():
            return
        async with self._cycle_lock:
            if not self._available():
                return
            generation = self._generation
            settings = self.settings  # Prompt changes apply to the next complete cycle.
            cycle = self.snapshot.cycle + 1
            fills_before = self.paper.state['fill_count'] if self.paper.state else 0
            results = {}
            status = {}
            workers = {"equity": "Queued", "crypto": "Queued"}
            self._publish(phase="Working", cycle=cycle, cycle_started_at=utc_now(), decisions=(), observed_at=None, next_cycle_at=None,
                          market_status={}, worker_status=dict(workers),
                          team_status={"NOVA": "Queued", "ORIN": "Queued", "VELA": "Waiting for quotes",
                                       "KADE": "Waiting for analysis", "RUNE": "Waiting for eligible signals",
                                       "ZARA": "Coordinating cycle"})

            def current():
                return generation == self._generation and self._available()

            if (settings.news_enabled or settings.twitter_enabled) and self.paper_source != "demo":
                symbols = settings.equity_symbols + tuple(s.split("-")[0].split("/")[0] for s in (settings.crypto_symbols or ("BTC", "ETH")))
                if self.continuous_paper:
                    self._poll_sources(symbols, settings)
                else:
                    self._publish(team_status={**self.snapshot.team_status, "VELA": "Reading news sources"})
                    await self.sources.refresh(symbols, social=settings.social_enabled,
                                               news=settings.news_enabled, twitter=settings.twitter_enabled)
                    if not current():
                        return
                    source_report = self.sources.summary()
                    self._publish(research_sources=source_report)
                    self._handoff("VELA", "KADE", f"{len(source_report['items'])} dated source items · "
                                  f"{sum(s['status'] == 'OK' for s in source_report['sources'])}/{len(source_report['sources'])} feeds available", "NEWS")

            def combined():
                return self._recheck([item for market in AssetClass for item in results.get(market, [])], settings)

            async def worker(market):
                def progress(phase):
                    workers[market.value] = phase
                    if current():
                        team = dict(self.snapshot.team_status)
                        team["NOVA" if market == AssetClass.EQUITY else "ORIN"] = phase
                        if phase == "Analyzing":
                            team["VELA"] = self.snapshot.analyst
                        if phase == "Checking data":
                            team["KADE"] = "Checking quotes and limits"
                        self._publish(worker_status=dict(workers), team_status=team)
                try:
                    async with asyncio.timeout(MARKET_WORKER_TIMEOUT_SECONDS):
                        decisions, summary = await self._market_cycle(market, cycle, settings, progress, current)
                    results[market] = decisions
                    status[market.value] = summary
                    progress("Cycle complete")
                except Exception as exc:
                    if not current():
                        return
                    if self.continuous_paper and market in self._ai_jobs:
                        self._ai_jobs.pop(market)['task'].cancel()
                        self._publish(analysis_status={**self.snapshot.analysis_status, market.value: "Paused: quotes unavailable"})
                    for key in list(self._history):
                        if key.startswith(f"{market.value}:"):
                            del self._history[key]
                    status[market.value] = "Unavailable: worker timed out" if isinstance(exc, TimeoutError) else f"Unavailable: {str(exc)[:240]}"
                    progress("Unavailable")
                    self._handoff("NOVA" if market == AssetClass.EQUITY else "ORIN", "ZARA",
                                  f"{market.value} worker unavailable; inspect local status", "WARN")
                if current():
                    if self.continuous_paper and self.snapshot.running and market in results:
                        self._consume_paper(self._recheck(results[market], settings), settings)
                    self._publish(decisions=tuple(combined()), market_status=dict(status))

            async with asyncio.TaskGroup() as group:
                for market in AssetClass:
                    group.create_task(worker(market), name=f"grande-agent-{market.value}")
            if generation != self._generation:
                return
            if not self._available():
                self._discard_paper_intents()
                self._publish(running=False, phase="Disconnected", decisions=(), next_cycle_at=None,
                              analysis_status={}, sources_loading=False)
                return
            decisions = combined()
            eligible = sum(item.risk_status == "Data checks passed" for item in decisions)
            self._handoff("KADE", "RUNE", f"{eligible} of {len(decisions)} candidates passed data checks", "RISK")
            if self.paper_source and self.snapshot.running:
                if not self.continuous_paper:
                    self._consume_paper(decisions, settings)
                report = self.paper_context()
                self._handoff("ZARA", "NOVA + ORIN", f"Virtual equity ${float(report['equity']):,.2f} · "
                              f"P&L ${float(report['total_pnl']):+,.2f} · {report['pending_count']} pending", "BOOK")
            else:
                self._handoff("RUNE", "ZARA", "Research cycle recorded; paper trading is off", "BOOK")
            team = dict(self.snapshot.team_status)
            team.update(VELA=self.snapshot.analyst, KADE=f"{eligible}/{len(decisions)} data checks passed",
                        RUNE=(f"{self.paper.state['fill_count'] - fills_before} paper fills this cycle" if self.paper_source else "Research only"),
                        ZARA="Portfolio updated" if self.paper_source else "Cycle recorded")
            observed_at = self._now()
            diagnostics = completed_check_report(cycle=cycle, at=observed_at, decisions=decisions, settings=settings,
                                                 source=self.paper_source, paper=self.paper_context() if self.paper_source else None,
                                                 markets=status, analysis=self.snapshot.analysis_status, history=self._history,
                                                 last_analysis=self.snapshot.analysis_last_result)
            self._publish(phase="Waiting", observed_at=observed_at, decisions=tuple(decisions),
                          market_status=status, worker_status=workers, team_status=team, diagnostics=diagnostics)
            self._log(
                f"Agent cycle {cycle}: {len(decisions)} candidates; "
                f"{sum(item.action != 'hold' for item in decisions)} proposals; no orders submitted",
                category="agent_cycle",
                payload={
                    "session_id": self._session_id, "cycle": cycle,
                    "analyst": self.snapshot.analyst, "markets": status,
                    "research_brief": settings.research_brief,
                    "equity_brief": settings.equity_brief, "crypto_brief": settings.crypto_brief,
                    "decisions": [asdict(item) for item in decisions],
                    "source_policy": NEWS_POLICY if settings.news_enabled and self.paper_source != "demo" else "off",
                    "source_health": (self.snapshot.research_sources or {}).get("sources", []),
                },
            )

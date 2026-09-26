"""Continuous stocks/crypto discovery, analysis and independent data-risk screening.

This runtime has read callbacks only. It cannot inherit an ETF session grant or
turn a model proposal into an order. Multi-market execution needs its own verified
contracts, account mapping, risk ledger, and strategy evidence before that changes.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from datetime import datetime, timedelta

from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.market_models import Quote
from grande_alpha.domain.policy import market_session_allowed
from grande_alpha.research.agent_analyst import (
    OllamaAnalyst,
)
from grande_alpha.research.agent_diagnostics import completed_check_report
from grande_alpha.research.agent_models import (
    AgentSettings,
    AgentSnapshot,
    AssetClass,
    Instrument,
)
from grande_alpha.research.agent_quote_screening import AgentQuoteScreening
from grande_alpha.research.agent_source_analysis import AgentSourceAnalysis
from grande_alpha.research.agent_sources import NEWS_POLICY, ResearchSources
from grande_alpha.research.paper.ledger import (
    DEMO_CYCLES,
    PaperLedger,
    demo_market,
    demo_time,
    validate_paper_settings,
)
from grande_alpha.strategy.paper import (
    ADAPTIVE_POLICY,
    adaptive_decision,
    limit_entries,
    pause_entries,
)

MARKET_WORKER_TIMEOUT_SECONDS = 35.0
DEMO_INTERVAL_SECONDS = 1.0
PAPER_SAMPLING_POLICY = "focused-quotes-v1"


class AgentRuntime(AgentSourceAnalysis, AgentQuoteScreening):
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
        self._paper_focus: dict[AssetClass, tuple[datetime, tuple[str, ...]]] = {}
        self._paper_scan_after: dict[AssetClass, str] = {}
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
               loop_demo: bool = False, *, resume: bool = False) -> None:
        settings.validate()
        if source != "demo" and not self._connected():
            raise ValueError("Connect the consented Robinhood account before starting the agent")
        if self.snapshot.running:
            raise ValueError("Stop the current agent run before changing its settings")
        if self._task is not None and not self._task.done():
            raise ValueError("Wait for the previous research cycle to stop before starting another")
        loop = asyncio.get_running_loop()
        if source and not resume:
            self.paper.start(source, initial_cash, trade_cash)
            adaptive = source == "broker_quotes" and settings.paper_strategy == "adaptive"
            self.paper.set_strategy({"policy": ADAPTIVE_POLICY if adaptive else NEWS_POLICY if settings.news_enabled and source != "demo" else "price-only",
                                     "paper_strategy": settings.paper_strategy if source == "broker_quotes" else "legacy",
                                     "ai_role": ("advisory" if adaptive else "decision") if settings.local_ai_enabled and source != "demo" else "off",
                                     "news_role": "context and headline-risk checks" if adaptive and settings.news_enabled else
                                                  "two-publisher entry filter" if settings.news_enabled and source != "demo" else "off",
                                     "model": settings.local_ai_model if settings.local_ai_enabled and source != "demo" else "rules",
                                     "social_context": settings.social_enabled and source != "demo",
                                     "quote_interval_seconds": settings.interval_seconds,
                                     "sampling_policy": PAPER_SAMPLING_POLICY if adaptive else "rotating-quotes-v1",
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
        self._paper_focus.clear()
        self._paper_scan_after.clear()
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

    def resume_paper(self, settings: AgentSettings) -> None:
        """Explicit background-worker resume; retain money, holdings, costs and risk history."""
        if not self.paper.state or self.paper.state.get("source") != "broker_quotes":
            raise ValueError("A saved broker-quote paper experiment is required")
        if self.snapshot.running:
            raise ValueError("Stop the running paper worker before resuming")
        self.paper.cancel_pending()  # Never replay pre-interruption intents.
        self._start(settings, "broker_quotes", resume=True)

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

    async def close(self) -> None:
        """Stop and join all research tasks before releasing the local paper journal."""
        await self.stop_and_wait("Research runtime closed")
        self.paper.close()

    async def stop_and_wait(self, reason: str = "Agent stopped") -> None:
        task = self._task
        background = tuple(self._background_tasks)
        self.stop(reason)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if background:
            await asyncio.gather(*background, return_exceptions=True)

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
        if (settings.news_enabled or settings.social_enabled) and self.paper_source != "demo":
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
        description = f"Observed {len(batch)} of {len(instruments)} candidates"
        if self.adaptive_paper and len(instruments) > 20:
            description += " · focused quote group; discovery rotates every 120s; holdings checked each poll"
        return decisions, description

    def _recheck(self, decisions, settings):
        now = self._now()
        result = []
        for item in decisions:
            if item.risk_status == "Data checks passed":
                if item.quote.age_seconds(now) > settings.max_quote_age_seconds:
                    item = replace(item, action="hold", risk_status="Blocked", reason="Quote expired during analysis")
                elif item.instrument.asset_class == AssetClass.EQUITY and not market_session_allowed(now, 0, 0, "regular_hours"):
                    item = replace(item, action="hold", risk_status="Blocked", reason="Equity session closed during analysis")
            if (settings.news_enabled or settings.social_enabled) and self.paper_source != "demo":
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

            if (settings.news_enabled or settings.social_enabled) and self.paper_source != "demo":
                symbols = settings.equity_symbols + tuple(s.split("-")[0].split("/")[0] for s in (settings.crypto_symbols or ("BTC", "ETH")))
                if self.continuous_paper:
                    self._poll_sources(symbols, settings)
                else:
                    self._publish(team_status={**self.snapshot.team_status, "VELA": "Reading news sources"})
                    await self.sources.refresh(symbols, social=settings.social_enabled,
                                               news=settings.news_enabled)
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

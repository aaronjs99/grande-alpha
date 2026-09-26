"""Continuous stocks/crypto discovery, analysis and independent data-risk screening.

This runtime has read callbacks only. It cannot inherit an ETF session grant or
turn a model proposal into an order. Multi-market execution needs its own verified
contracts, account mapping, risk ledger, and strategy evidence before that changes.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from datetime import datetime, timedelta

import httpx

from grande_alpha.research.agent_analyst import (
    AI_MAX_ANALYSIS_AGE_SECONDS,
    AI_MAX_PRICE_DRIFT_BPS,
    AI_REQUEST_TIMEOUT_SECONDS,
    AnalystResponseError,
)
from grande_alpha.research.agent_sources import REFRESH_SECONDS

AI_TIMEOUT_SECONDS = AI_REQUEST_TIMEOUT_SECONDS


class AgentSourceAnalysis:
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
                                       news=settings.news_enabled)
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

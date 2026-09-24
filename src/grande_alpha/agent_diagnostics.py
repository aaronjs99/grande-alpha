"""Readable, bounded diagnostics for a completed research/paper observation batch."""
from __future__ import annotations

import math
from collections import Counter
from zoneinfo import ZoneInfo

from grande_alpha.agent_models import AssetClass


def completed_check_report(*, cycle, at, decisions, settings, source, paper, markets, analysis, history):
    """Select public observations and virtual counts, never serialize account/settings objects."""
    stamp = at.astimezone(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d %I:%M:%S %p %Z')
    lines = [f'Last completed check {cycle} · {stamp}']
    for market, name in ((AssetClass.EQUITY, 'Stocks'), (AssetClass.CRYPTO, 'Crypto')):
        items = [d for d in decisions if d.instrument.asset_class == market]
        if markets.get(market.value, '').startswith('Unavailable'):
            # Raw provider exceptions may contain request details. Keep them out of copyable reports.
            summary = 'Data unavailable; inspect the worker status in the app.'
        elif not items:
            summary = 'No candidates in this check.'
        else:
            reasons = Counter(d.reason.split(' · Sources:', 1)[0][:220] for d in items)
            summary = '; '.join(f'{count}/{len(items)} {reason}' for reason, count in reasons.most_common(2))
        lines.append(f'{name}: {summary}')
    mode = 'Offline demo' if source == 'demo' else 'Continuous paper trading' if source else 'Research only'
    lines += ['', f'Mode: {mode} · no real orders']
    if paper:
        lines.append(f"Virtual fills: {paper['fill_count']} · pending: {paper['pending_count']} · "
                     f"open virtual positions: {len(paper['positions'])}")
    else:
        lines.append('Paper trading is off. Research-only proposals do not produce simulated fills.')
    ai = settings.local_ai_enabled and source != 'demo'
    lines.append('Analyst: local AI enabled' if ai else
                 'Analyst: rules baseline · continuous AI is OFF. A connected ChatGPT chat is not a background analyst.')
    if ai:
        lines.extend(f'AI {market.value}: {analysis.get(market.value, "Waiting for eligible observations")}' for market in AssetClass)
    news = settings.news_enabled and source != 'demo'
    lines += [f'News entry filter: {"ON (two matching news publishers required)" if news else "OFF"}',
              f'X monitoring: {"ON" if settings.twitter_enabled and source != "demo" else "OFF"}',
              f'Quote checks: {settings.interval_seconds}s target · maximum quote age: {settings.max_quote_age_seconds:g}s',
              'Rules baseline: BUY above +max(0.20%, twice the spread); EXIT below the negative threshold.',
              'Signals need at least 4 distinct quotes spanning 60s. Invalid quotes restart this window.',
              'A BUY queues a virtual entry; a later eligible quote fills it. EXIT needs a virtual holding.',
              'The conditions can remain unmet indefinitely. Changing the watchlist does not ensure a trade.',
              '', 'Observed candidates (this batch only):']
    for item in decisions[:40]:
        lines += [f'{item.instrument.key}: {item.action.upper()} · {item.risk_status}', f'  Reason: {item.reason[:700]}']
        context = item.source_context or {}
        if news:
            lines.append(f'  News: {context.get("coverage", "No current coverage")} · new buys allowed: {item.buy_allowed}')
        quote = item.quote
        try:
            if quote is None:
                lines.append('  Quote: missing from broker response')
                continue
            quote.validate()
            spread = quote.spread_bps
            if not math.isfinite(spread):
                raise ValueError('Nonfinite spread')
            limit = settings.crypto_max_spread_bps if item.instrument.asset_class == AssetClass.CRYPTO else settings.equity_max_spread_bps
            lines += [f'  Bid {quote.bid:.10g} · ask {quote.ask:.10g} · spread {spread / 100:.3f}% · limit {limit / 100:.3f}%',
                      f'  Quote age at this check: {quote.age_seconds(at):.1f}s (limit {settings.max_quote_age_seconds:g}s)']
            samples = history.get(item.instrument.key, ())
            span = max(0, (samples[-1][0] - samples[0][0]).total_seconds()) if samples else 0
            lines.append(f'  Warm-up: {item.samples} distinct quotes (need 4); span {span:.1f}s (need 60s)')
            change = f'{item.change_bps / 100:+.3f}%' if item.change_bps is not None else 'not ready'
            lines.append(f'  Observed price change: {change} · rules entry threshold: >+{max(20, 2 * spread) / 100:.3f}%')
        except (ValueError, TypeError, OverflowError):
            lines.append('  Quote values are invalid; no signal or simulated fill is eligible.')
    return '\n'.join(lines)

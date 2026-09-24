"""Optional, bounded X recent-search reads. Credentials never enter research context."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from datetime import timedelta

import httpx
import keyring

X_ENDPOINT = "https://api.x.com/2/tweets/search/recent"
X_SERVICE = "GRANDEAlpha.XResearch"
X_SOURCE = "X / Twitter · unverified"
X_MAX_AGE = timedelta(hours=1)


class XCredentials:
    async def read(self) -> str:
        try:
            return await asyncio.to_thread(keyring.get_password, X_SERVICE, "bearer") or ""
        except Exception:
            raise RuntimeError("Could not read the X key from the system keychain") from None

    async def save(self, token: str) -> None:
        token = token.strip()
        if not re.fullmatch(r"[A-Za-z0-9%._~+/=-]{20,1000}", token):
            raise ValueError("Paste the X API Bearer Token only, without quotes or the word Bearer")
        try:
            await asyncio.to_thread(keyring.set_password, X_SERVICE, "bearer", token)
        except Exception:
            raise RuntimeError("Could not save the X key in the system keychain") from None

    async def remove(self) -> None:
        try:
            await asyncio.to_thread(keyring.delete_password, X_SERVICE, "bearer")
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            raise RuntimeError("Could not remove the X key from the system keychain") from None


def parse_x(body: bytes, seen):
    from grande_alpha.agent_sources import MAX_BYTES, article

    if len(body) > MAX_BYTES:
        raise ValueError("X response exceeds size limit")
    data = json.loads(body)
    if not isinstance(data, dict) or data.get('errors'):
        raise ValueError("Invalid X response")
    rows = data.get('data')
    if rows is None and isinstance(data.get('meta'), dict) and data['meta'].get('result_count') == 0:
        return []
    if not isinstance(rows, list):
        raise ValueError("Invalid X posts")
    items, seen_ids = [], set()
    for post in rows[:10]:
        try:
            ident, text, date = post['id'], post['text'], post['created_at']
            if not isinstance(ident, str) or not re.fullmatch(r'[0-9]{1,30}', ident) or ident in seen_ids:
                continue
            if not isinstance(text, str) or not isinstance(date, str):
                continue
            item = article(X_SOURCE, 'social', text, '', f'https://x.com/i/web/status/{ident}', date, seen)
            from grande_alpha.agent_sources import timestamp
            if seen - X_MAX_AGE <= timestamp(item.published_at) <= seen:
                items.append(item)
                seen_ids.add(ident)
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    return items


class XMonitor:
    def __init__(self, *, clock, credentials=None):
        self._clock = clock
        self.credentials = credentials or XCredentials()
        self._retry_at = None
        self._previous = None
        self._report = {}

    async def fetch(self, client, read, symbols):
        from grande_alpha.agent_models import AssetClass, Instrument
        from grande_alpha.agent_sources import matches

        now = self._clock()
        symbols = tuple(s for s in dict.fromkeys(symbols) if re.fullmatch(r'[A-Z0-9]{1,12}', s))[:8]
        items, status = [], 'Not connected · open Connect X / Twitter'
        try:
            if self._retry_at and now < self._retry_at:
                status = 'Rate limited · waiting before retry'
            elif not symbols:
                status = 'No supported watchlist symbols'
            else:
                token = await self.credentials.read()
                if token:
                    body = await read(client, X_ENDPOINT, headers={'Authorization': 'Bearer ' + token}, params={
                        'query': '(' + ' OR '.join('$' + s for s in symbols) + ') lang:en -is:retweet',
                        'max_results': 10, 'tweet.fields': 'created_at', 'sort_order': 'recency',
                        'start_time': (now - X_MAX_AGE).isoformat().replace('+00:00', 'Z'),
                    })
                    items = parse_x(body, self._clock())
                    status = 'OK'
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            status = {401: 'X key rejected · reconnect X', 402: 'X API credit/access required',
                      403: 'X search access denied · check the X developer console',
                      429: 'Rate limited · waiting before retry'}.get(code, f'Unavailable · HTTP {code}')
            if code == 429:
                delay = 900
                try:
                    reset = float(exc.response.headers.get('x-rate-limit-reset', now.timestamp() + delay)) - now.timestamp()
                    retry = float(exc.response.headers.get('retry-after', delay))
                    delay = max(900, reset, retry)
                except (ValueError, OverflowError):
                    pass
                self._retry_at = now + timedelta(seconds=min(86400, delay))
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, RuntimeError):
            # Never serialize an exception containing request headers or a token.
            status = 'Unavailable · check the X keychain connection or network'
        trends = []
        if status == 'OK':
            counts = {s: sum(matches(item, Instrument(AssetClass.EQUITY, s)) for item in items) for s in symbols}
            previous = self._previous[1] if self._previous and self._previous[0] == symbols else None
            trends = [{'symbol': s, 'sample_posts': counts[s],
                       'previous_sample_posts': previous[s] if previous is not None else None,
                       'sample_change': counts[s] - previous[s] if previous is not None else None} for s in symbols]
            self._previous = (symbols, counts)
        else:
            self._previous = None  # Unavailable data is not a zero-volume trend.
        self._report = {'checked_at': self._clock().isoformat(), 'status': status,
                        'symbols': list(symbols), 'trends': trends,
                        'posts': [asdict(i) for i in items],
                        'notice': 'Latest sample: at most 10 posts from the last hour. Changes compare samples, not total X volume or sentiment.'}
        return items, {'source': X_SOURCE, 'status': status, 'fresh_items': len(items)}

    def summary(self):
        return self._report

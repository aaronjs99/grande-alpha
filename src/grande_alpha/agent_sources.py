"""Bounded public research feeds. Content is evidence to inspect, never instructions."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from grande_alpha.agent_models import AssetClass, Instrument
from grande_alpha.agent_x import X_MAX_AGE, X_SOURCE, XMonitor
from grande_alpha.models import utc_now

REFRESH_SECONDS = 600
MAX_BYTES = 512_000
MAX_NEWS_AGE = timedelta(hours=48)
NEWS_POLICY = "price-news-v1"


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    hosts: tuple[str, ...]
    kind: str = "news"


FEEDS = (
    Feed("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", ("bbc.com", "bbc.co.uk")),
    Feed("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", ("cnbc.com",)),
    Feed("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss", ("coindesk.com",)),
    Feed("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml", ("federalreserve.gov",), "official"),
)
SOCIAL_ENDPOINT = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
# Conservative name matching. Unknown and ambiguous short tickers require a cashtag.
EQUITY_NAMES = {"AAPL": ("apple",), "MSFT": ("microsoft",), "NVDA": ("nvidia",),
                "AMZN": ("amazon",), "GOOG": ("alphabet", "google"), "GOOGL": ("alphabet", "google"),
                "TSLA": ("tesla",), "META": ("meta platforms", "facebook"), "COIN": ("coinbase",),
                "MSTR": ("microstrategy",), "QQQ": ("invesco qqq",), "SPY": ("spdr s&p 500",)}
CRYPTO_NAMES = {"BTC": ("bitcoin",), "ETH": ("ethereum", "ether"), "SOL": ("solana",),
                "DOGE": ("dogecoin",), "XRP": ("xrp",), "ADA": ("cardano",), "LTC": ("litecoin",)}
RISK_TERMS = re.compile(r"\b(bankrupt(?:cy)?|fraud|hacked|exploit|cuts? (?:its )?(?:guidance|forecast)|misses (?:earnings|estimates))\b", re.I)


def plain(value: str, limit: int) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).replace("\x00", "").split())[:limit]


def safe_url(value: str, hosts: tuple[str, ...]) -> str:
    if len(value) > 2000 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid source link")
    p = urlsplit(value.strip())
    if (p.scheme not in {"https", "http"} or p.username or p.password or p.port not in (None, 80, 443)
            or not any(p.hostname == h or (p.hostname or "").endswith("." + h) for h in hosts)):
        raise ValueError("Unexpected source link")
    return urlunsplit(("https", p.hostname, p.path, "", ""))


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        result = parsedate_to_datetime(value)
    if result.tzinfo is None:
        raise ValueError("Source date needs a timezone")
    return result.astimezone(UTC)


@dataclass(frozen=True)
class SourceItem:
    id: str
    source: str
    kind: str
    title: str
    excerpt: str
    url: str
    published_at: str
    first_seen_at: str


def article(source: str, kind: str, title: str, excerpt: str, url: str, published: str, seen: datetime) -> SourceItem:
    title, excerpt = plain(title, 250), plain(excerpt, 400)
    date = timestamp(published)
    if not title or not seen - MAX_NEWS_AGE <= date <= seen:
        raise ValueError("Undated, future, or old item")
    identity = hashlib.sha256((url + title + excerpt + date.isoformat()).encode()).hexdigest()[:20]
    return SourceItem(identity, source, kind, title, excerpt, url, date.isoformat(), seen.isoformat())


def parse_feed(body: bytes, feed: Feed, seen: datetime) -> list[SourceItem]:
    if len(body) > MAX_BYTES or b"\x00" in body or re.search(br"<!\s*(?:DOCTYPE|ENTITY)", body, re.I):
        raise ValueError("Unsupported feed document")
    root = ET.fromstring(body)
    if root.tag not in {"rss", "{http://www.w3.org/2005/Atom}feed"}:
        raise ValueError("Response is not RSS or Atom")
    result = []
    atom = "{http://www.w3.org/2005/Atom}"
    nodes = root.findall("./channel/item") if root.tag == "rss" else root.findall(f"{atom}entry")
    for item in nodes[:100]:
        try:
            if root.tag == "rss":
                values = (item.findtext("title", ""), item.findtext("description", ""),
                          item.findtext("link", ""), item.findtext("pubDate", ""))
            else:
                links = [e.get("href", "") for e in item.findall(f"{atom}link") if e.get("rel", "alternate") == "alternate"]
                values = (item.findtext(f"{atom}title", ""), item.findtext(f"{atom}summary", ""),
                          links[0] if links else "", item.findtext(f"{atom}published") or item.findtext(f"{atom}updated", ""))
            title, excerpt, url, published = values
            result.append(article(feed.name, feed.kind, title, excerpt, safe_url(url, feed.hosts), published, seen))
        except (ValueError, TypeError, OverflowError):
            continue
    return result


def parse_social(body: bytes, seen: datetime) -> list[SourceItem]:
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
        raise ValueError("Invalid public social response")
    result = []
    for post in data["posts"][:30]:
        try:
            match = re.fullmatch(r"at://(did:[a-z]+:[A-Za-z0-9._:%-]+)/app\.bsky\.feed\.post/([a-zA-Z0-9]+)", post["uri"])
            if not match:
                continue
            record = post["record"]
            text = record["text"]
            result.append(article("Bluesky · unverified", "social", text, "",
                                  f"https://bsky.app/profile/{match[1]}/post/{match[2]}", record["createdAt"], seen))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return result


def matches(item: SourceItem, instrument: Instrument) -> bool:
    symbol = instrument.symbol.split("-")[0] if instrument.asset_class == AssetClass.CRYPTO else instrument.symbol
    names = (CRYPTO_NAMES if instrument.asset_class == AssetClass.CRYPTO else EQUITY_NAMES).get(symbol, ())
    text = item.title
    if any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text, re.I) for n in names):
        return True
    return bool(re.search(r"(?<!\w)\$" + re.escape(symbol) + r"(?!\w)", text)
                or len(symbol) >= 3 and symbol not in {"ALL", "NOW", "FOR", "NEW", "ONE", "TWO"}
                and re.search(r"(?<!\w)" + re.escape(symbol) + r"(?!\w)", text))


class ResearchSources:
    def __init__(self, *, clock=utc_now) -> None:
        self._clock = clock
        self._items: tuple[SourceItem, ...] = ()
        self._refreshed_at: datetime | None = None
        self._statuses: tuple[dict, ...] = ()
        self._cache_key = None
        self._lock = asyncio.Lock()
        self.twitter = XMonitor(clock=clock)

    async def _read(self, client, url, **kwargs) -> bytes:
        async with asyncio.timeout(8):
            async with client.stream("GET", url, **kwargs) as response:
                response.raise_for_status()
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data) > MAX_BYTES:
                        raise ValueError("Feed exceeds size limit")
                return bytes(data)

    async def refresh(self, symbols: tuple[str, ...], *, social: bool = False, news: bool = True, twitter: bool = False) -> None:
        # A single bounded search covers up to eight configured symbols; no firehose claims.
        symbols = tuple(dict.fromkeys(symbols))[:8]
        key = (symbols, social, news, twitter)
        async with self._lock:
            if self._cache_key == key and self._refreshed_at and 0 <= (self._clock() - self._refreshed_at).total_seconds() < REFRESH_SECONDS:
                return
            async with httpx.AsyncClient(timeout=6, trust_env=False, follow_redirects=False,
                                         headers={"User-Agent": "GRANDEAlphaResearch/0.16 RSS reader"}) as client:
                async def fetch(feed: Feed | None):
                    name = feed.name if feed else "Bluesky · unverified"
                    try:
                        if feed:
                            body = await self._read(client, feed.url)
                            items = parse_feed(body, feed, self._clock())
                        else:
                            body = await self._read(client, SOCIAL_ENDPOINT, params={
                                "q": " OR ".join(f'"${s}"' for s in symbols), "sort": "latest", "limit": 30,
                                "since": (self._clock() - MAX_NEWS_AGE).isoformat(), "lang": "en"})
                            items = parse_social(body, self._clock())
                        return items, {"source": name, "status": "OK", "fresh_items": len(items)}
                    except httpx.HTTPStatusError as exc:
                        return [], {"source": name, "status": f"Unavailable · HTTP {exc.response.status_code}", "fresh_items": 0}
                    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, ET.ParseError):
                        return [], {"source": name, "status": "Unavailable · network or invalid feed", "fresh_items": 0}
                results = await asyncio.gather(*(fetch(f) for f in FEEDS if news),
                                              *([fetch(None)] if social and symbols else []),
                                              *([self.twitter.fetch(client, self._read, symbols)] if twitter else []))
            old = {item.id: item for item in self._items}
            unique, titles = {}, set()
            for items, _status in results:
                for item in items:
                    # Exact duplicated headlines across outlets are not independent corroboration.
                    title_key = re.sub(r"\W+", "", item.title.casefold())
                    if item.url in unique or title_key in titles:
                        continue
                    unique[item.url] = old.get(item.id, item)
                    titles.add(title_key)
            self._items = tuple(sorted(unique.values(), key=lambda i: i.published_at, reverse=True)[:200])
            self._statuses = tuple(status for _, status in results)
            self._refreshed_at, self._cache_key = self._clock(), key

    def context(self, instrument: Instrument, now: datetime) -> dict:
        fresh = [i for i in self._items if now - MAX_NEWS_AGE <= timestamp(i.published_at) <= now
                 and timestamp(i.first_seen_at) <= now
                 and (i.source != X_SOURCE or now - X_MAX_AGE <= timestamp(i.published_at))]
        direct = [i for i in fresh if i.kind == "news" and matches(i, instrument)][:6]
        social = [i for i in fresh if i.kind == "social" and matches(i, instrument)][:4]
        macro = [i for i in fresh if i.kind == "official"][:2]
        sources = sorted({i.source for i in direct})
        flags = sorted({m.group().lower() for i in direct for m in RISK_TERMS.finditer(i.title)})
        current = bool(self._refreshed_at and 0 <= (now - self._refreshed_at).total_seconds() <= REFRESH_SECONDS + 60)
        return {"policy": NEWS_POLICY, "news_sources": sources, "risk_terms": flags,
                "buy_supported": current and len(sources) >= 2 and not flags,
                "coverage": "Fresh coverage from multiple publishers" if current and len(sources) >= 2 else
                            f"Insufficient fresh ticker-specific coverage ({len(sources)}/2 matching news publishers" + ("; feed refresh overdue)" if not current else ")"),
                "articles": [{**asdict(i), "scope": "direct" if i in direct else "unverified social" if i in social else "macro context"}
                             for i in (*direct, *social, *macro)]}

    def summary(self) -> dict:
        return {"refreshed_at": self._refreshed_at.isoformat() if self._refreshed_at else None,
                "sources": list(self._statuses), "items": [asdict(i) for i in self._items],
                "policy": NEWS_POLICY, "refresh_seconds": REFRESH_SECONDS,
                "social_symbols": list(self._cache_key[0]) if self._cache_key and self._cache_key[1] else [],
                "twitter": self.twitter.summary() if self._cache_key and self._cache_key[3] else None,
                "notice": "External content is untrusted data. Coverage and keyword checks are experimental, not proof of an edge."}

"""Bounded, read-only Alpaca SIP history. No trading endpoints or SDK are used."""

from __future__ import annotations

import asyncio
import json
import math
import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from threading import Event

import httpx

from grande_alpha.configuration.config import data_dir
from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.domain.market_models import Bar
from grande_alpha.domain.policy import EASTERN
from grande_alpha.research.historical import align_bars, assess_quality
from grande_alpha.research.historical_models import DataProvenance, HistoricalBundle
from grande_alpha.research.historical_storage import load_bundle, save_bundle

BAR_URL = "https://data.alpaca.markets/v2/stocks/bars"
SYMBOLS = ("QQQ", "TQQQ", "SQQQ")
KEY_SERVICE = "GRANDEAlpha.AlpacaMarketData"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_BARS = 150_000
PAGE_LIMIT = 2000


def _validate_credentials(credentials: tuple[str, str]) -> tuple[str, str]:
    if len(credentials) != 2 or any(not isinstance(value, str) or not value.strip()
                                   or len(value) > 512 or any(ord(char) < 32 for char in value)
                                   for value in credentials):
        raise ValueError("Enter both the Alpaca API key and secret")
    return credentials[0].strip(), credentials[1].strip()


def _vault():
    if os.name == "nt":
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()
    import keyring
    return keyring.get_keyring()


def load_alpaca_credentials() -> tuple[str, str]:
    key, secret = os.environ.get("APCA_API_KEY_ID", ""), os.environ.get("APCA_API_SECRET_KEY", "")
    if key or secret:
        return _validate_credentials((key, secret))
    try:
        saved = _vault().get_password(KEY_SERVICE, "history")
        if saved:
            values = json.loads(saved)
            return _validate_credentials((values["key"], values["secret"]))
    except Exception:
        raise RuntimeError("Could not read saved Alpaca keys; enter them again in Download history") from None
    raise RuntimeError("Add your free Alpaca account's API key and secret in Download history")


def save_alpaca_credentials(credentials: tuple[str, str]) -> None:
    key, secret = _validate_credentials(credentials)
    try:
        _vault().set_password(KEY_SERVICE, "history", json.dumps({"key": key, "secret": secret}))
    except Exception:
        raise RuntimeError("Alpaca keys could not be saved to the system credential store") from None


def history_window(days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    if type(days) is not int or not 1 <= days <= 60:
        raise ValueError("Choose between 1 and 60 calendar days to keep history memory use bounded")
    cutoff = (now or utc_now()).astimezone(UTC) - timedelta(minutes=16)
    day = cutoff.astimezone(EASTERN).date()
    while True:
        hours = regular_session_times(day)
        if hours is not None:
            closed = datetime.combine(day, hours[1], EASTERN).astimezone(UTC)
            if closed <= cutoff:
                start_day = day - timedelta(days=days - 1)
                return (datetime.combine(start_day, time.min, EASTERN).astimezone(UTC),
                        closed - timedelta(seconds=1))
        day -= timedelta(days=1)


class AlpacaHistoricalDataProvider:
    def __init__(self, *, credentials: tuple[str, str] | None = None, cache_dir: Path | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._credentials = _validate_credentials(credentials) if credentials is not None else None
        self._transport = transport
        self.cache_dir = Path(cache_dir) if cache_dir is not None else data_dir() / "sandbox_cache"
        self.cache_path: Path | None = None

    async def _page(self, client: httpx.AsyncClient, params: dict) -> dict:
        for attempt in range(3):
            try:
                async with client.stream("GET", BAR_URL, params=params) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(1 + attempt)
                            continue
                    if response.status_code != 200:
                        detail = ("Check your Alpaca API keys and historical-data access."
                                  if response.status_code in (401, 403) else "Try the history download again later.")
                        raise RuntimeError(f"Alpaca history unavailable (HTTP {response.status_code}). {detail}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_BODY_BYTES:
                            raise RuntimeError("Alpaca returned an oversized history page; nothing was saved")
                        body.extend(chunk)
                payload = json.loads(body)
                if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
                    raise RuntimeError("Alpaca returned an invalid history page; nothing was saved")
                return payload
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 2:
                    raise RuntimeError("Alpaca history request timed out or could not connect; try again later") from None
                await asyncio.sleep(1 + attempt)
            except (ValueError, TypeError, KeyError):
                raise RuntimeError("Alpaca returned invalid history data; nothing was saved") from None
        raise RuntimeError("Alpaca history request did not complete")

    async def fetch(self, days: int = 60, interval: str = "5m", use_cache: bool = True,
                    market_hours: str = "regular_hours") -> HistoricalBundle:
        if interval not in {"1m", "5m"} or market_hours != "regular_hours":
            raise ValueError("Alpaca history supports regular-session 1m or 5m bars")
        start, end = history_window(days)
        self.cache_path = self.cache_dir / f"alpaca_sip_regular_hours_{interval}_{days}d_{end.date().isoformat()}.json"
        if use_cache and self.cache_path.exists():
            if self.cache_path.stat().st_size > 32 * 1024 * 1024:
                raise RuntimeError("Saved history exceeds the memory limit; choose a smaller local dataset")
            bundle = await asyncio.to_thread(load_bundle, self.cache_path)
            if (bundle.interval != interval or bundle.market_hours != market_hours or bundle.provenance is None
                    or bundle.provenance.provider != "Alpaca"):
                raise RuntimeError("Saved Alpaca history does not match this request; choose a fresh download")
            return bundle
        credentials = self._credentials or await asyncio.to_thread(load_alpaca_credentials)
        params = {"symbols": ",".join(SYMBOLS), "timeframe": "1Min" if interval == "1m" else "5Min",
                  "start": start.isoformat(), "end": end.isoformat(), "feed": "sip", "adjustment": "raw",
                  "sort": "asc", "limit": str(PAGE_LIMIT)}
        headers = {"APCA-API-KEY-ID": credentials[0], "APCA-API-SECRET-KEY": credentials[1]}
        series: dict[str, list[Bar]] = {symbol: [] for symbol in SYMBOLS}
        tokens: set[str] = set()
        count = 0
        try:
            async with asyncio.timeout(240), httpx.AsyncClient(
                headers=headers, timeout=20, follow_redirects=False, transport=self._transport,
            ) as client:
                while True:
                    payload = await self._page(client, params)
                    for symbol, rows in payload["bars"].items():
                        if symbol not in series or not isinstance(rows, list):
                            raise RuntimeError("Alpaca returned unexpected history symbols or rows")
                        for raw in rows:
                            count += 1
                            if count > MAX_BARS:
                                raise RuntimeError("History exceeded the memory limit; choose fewer days")
                            bar = self._bar(symbol, raw, start, end, interval)
                            if bar is None:
                                continue
                            if series[symbol] and bar.start <= series[symbol][-1].start:
                                raise RuntimeError("Alpaca history contains duplicate or unordered bars")
                            series[symbol].append(bar)
                    token = payload.get("next_page_token")
                    if token is None:
                        break
                    if not isinstance(token, str) or not token or len(token) > 4096 or token in tokens or len(tokens) >= 100:
                        raise RuntimeError("Alpaca history pagination did not complete; nothing was saved")
                    tokens.add(token)
                    params["page_token"] = token
        except TimeoutError:
            raise RuntimeError("History download exceeded four minutes; try fewer days") from None
        if any(not rows for rows in series.values()):
            raise RuntimeError("Alpaca did not return history for all three ETFs; nothing was saved")
        frames = align_bars(*(series[symbol] for symbol in SYMBOLS))
        if not frames or any(len(rows) != len(frames) for rows in series.values()):
            raise RuntimeError("The ETF histories do not align; incomplete history was not saved")
        quality = assess_quality(frames, interval, market_hours)
        bundle = HistoricalBundle(
            source=f"Alpaca SIP stock bars ({interval}, raw prices, completed regular sessions)",
            downloaded_at=utc_now(), frames=frames, interval=interval, dataset_hash=quality.dataset_hash,
            quality=quality, market_hours=market_hours,
            provenance=DataProvenance(
                source_kind="provider_api_observation", provider="Alpaca", provider_product="SIP stock bars (raw)",
                acquisition_method="Alpaca historical bars REST API; delayed completed sessions; paginated",
                observed_data=True, synthetic_or_interpolated=False, construction_method="provider_native",
                source_resolution_seconds=60 if interval == "1m" else 300, bar_interval=interval,
                market_hours=market_hours, canonical_dataset_hash=quality.dataset_hash,
            ),
        )
        cancelled = Event()
        try:
            await asyncio.to_thread(save_bundle, bundle, self.cache_path, cancelled=cancelled)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return bundle

    @staticmethod
    def _bar(symbol: str, raw: dict, start: datetime, end: datetime, interval: str) -> Bar | None:
        try:
            stamp = datetime.fromisoformat(raw["t"].replace("Z", "+00:00"))
            if stamp.utcoffset() is None or not start <= stamp <= end:
                raise ValueError
            values = [raw[name] for name in ("o", "h", "l", "c", "v")]
            if any(isinstance(value, bool) for value in values):
                raise ValueError
            opened, high, low, close, volume = map(float, values)
            if (any(not math.isfinite(value) for value in (opened, high, low, close, volume))
                    or min(opened, high, low, close) <= 0 or volume < 0
                    or low > min(opened, close) or high < max(opened, close) or low > high
                    or stamp.second or stamp.microsecond or stamp.minute % (1 if interval == "1m" else 5)):
                raise ValueError
            local = stamp.astimezone(EASTERN)
            hours = regular_session_times(local.date())
            if hours is None or not hours[0] <= local.time() < hours[1]:
                return None
            return Bar(symbol, stamp.astimezone(UTC), opened, high, low, close, 1, volume)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise RuntimeError("Alpaca returned invalid OHLCV history; nothing was saved") from None

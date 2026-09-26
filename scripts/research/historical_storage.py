from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grande_alpha.domain.market_models import Bar, Quote
from grande_alpha.research.historical import assess_quality
from grande_alpha.research.historical_models import (
    RUNTIME_PROVENANCE_FIELDS,
    DataProvenance,
    HistoricalBundle,
    ReplayFrame,
)


def _bar_dict(bar: Bar) -> dict[str, Any]:
    return {
        "symbol": bar.symbol,
        "start": bar.start.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "samples": bar.samples,
        "volume": bar.volume,
    }


def _bar_from_dict(raw: dict[str, Any]) -> Bar:
    return Bar(
        symbol=str(raw["symbol"]),
        start=datetime.fromisoformat(str(raw["start"])).astimezone(UTC),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        samples=int(raw.get("samples", 1)),
        volume=float(raw.get("volume", 0.0)),
    )


def _quote_dict(quote: Quote | None) -> dict[str, Any] | None:
    if quote is None:
        return None
    return {
        "symbol": quote.symbol,
        "bid": quote.bid,
        "ask": quote.ask,
        "last": quote.last,
        "timestamp": quote.timestamp.isoformat(),
        "bid_timestamp": (
            quote.bid_timestamp.isoformat() if quote.bid_timestamp is not None else None
        ),
        "ask_timestamp": (
            quote.ask_timestamp.isoformat() if quote.ask_timestamp is not None else None
        ),
    }


def _quote_from_dict(raw: dict[str, Any] | None) -> Quote | None:
    if raw is None:
        return None
    quote = Quote(
        symbol=str(raw["symbol"]),
        bid=float(raw["bid"]),
        ask=float(raw["ask"]),
        last=float(raw["last"]),
        timestamp=datetime.fromisoformat(str(raw["timestamp"])).astimezone(UTC),
        bid_timestamp=(
            datetime.fromisoformat(str(raw["bid_timestamp"])).astimezone(UTC)
            if raw.get("bid_timestamp") is not None
            else None
        ),
        ask_timestamp=(
            datetime.fromisoformat(str(raw["ask_timestamp"])).astimezone(UTC)
            if raw.get("ask_timestamp") is not None
            else None
        ),
    )
    quote.validate()
    return quote


def save_bundle(bundle: HistoricalBundle, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": bundle.source,
        "downloaded_at": bundle.downloaded_at.isoformat(),
        "interval": bundle.interval,
        "dataset_hash": bundle.dataset_hash,
        "market_hours": bundle.market_hours,
        "provenance": bundle.provenance.as_dict() if bundle.provenance is not None else None,
        "frames": [
            {
                "start": frame.start.isoformat(),
                "qqq": _bar_dict(frame.qqq),
                "tqqq": _bar_dict(frame.tqqq),
                "sqqq": _bar_dict(frame.sqqq),
                "causal_timestamp": (
                    frame.causal_timestamp.isoformat() if frame.causal_timestamp is not None else None
                ),
                "qqq_quote": _quote_dict(frame.qqq_quote),
                "tqqq_quote": _quote_dict(frame.tqqq_quote),
                "sqqq_quote": _quote_dict(frame.sqqq_quote),
                "stream_id": frame.stream_id,
            }
            for frame in bundle.frames
        ],
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def load_bundle(path: Path) -> HistoricalBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    frames = [
        ReplayFrame(
            datetime.fromisoformat(row["start"]).astimezone(UTC),
            _bar_from_dict(row["qqq"]),
            _bar_from_dict(row["tqqq"]),
            _bar_from_dict(row["sqqq"]),
            (
                datetime.fromisoformat(row["causal_timestamp"]).astimezone(UTC)
                if row.get("causal_timestamp")
                else None
            ),
            _quote_from_dict(row.get("qqq_quote")),
            _quote_from_dict(row.get("tqqq_quote")),
            _quote_from_dict(row.get("sqqq_quote")),
            str(row.get("stream_id") or ""),
        )
        for row in payload["frames"]
    ]
    interval = str(payload.get("interval", "1m"))
    market_hours = str(payload.get("market_hours", "regular_hours"))
    quality = assess_quality(frames, interval, market_hours)
    expected_hash = str(payload.get("dataset_hash") or quality.dataset_hash)
    if expected_hash != quality.dataset_hash:
        raise ValueError("Cached historical dataset hash does not match its contents")
    raw_provenance = payload.get("provenance")
    provenance = None
    if isinstance(raw_provenance, dict):
        stored_digest = str(raw_provenance.get("digest") or "")
        if not stored_digest:
            raise ValueError("Cached historical provenance is missing its required digest")
        allowed = DataProvenance.__dataclass_fields__.keys()
        provenance = DataProvenance(
            **{key: value for key, value in raw_provenance.items() if key in allowed}
        )
        if stored_digest != provenance.digest:
            legacy_payload = {
                key: raw_provenance[key]
                for key in allowed
                if key not in RUNTIME_PROVENANCE_FIELDS and key in raw_provenance
            }
            legacy_digest = hashlib.sha256(
                json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if RUNTIME_PROVENANCE_FIELDS.intersection(raw_provenance) or stored_digest != legacy_digest:
                raise ValueError("Cached historical provenance hash does not match its contents")
    return HistoricalBundle(
        source=str(payload["source"]),
        downloaded_at=datetime.fromisoformat(payload["downloaded_at"]).astimezone(UTC),
        frames=frames,
        interval=interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours=market_hours,
        provenance=provenance,
    )

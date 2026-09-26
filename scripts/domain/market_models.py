from __future__ import annotations

import math
from dataclasses import field
from datetime import datetime
from enum import StrEnum
from numbers import Real

from grande_alpha.domain.clock import utc_now

EXACT_QUOTE_VALIDATOR_VERSION = 2


class Regime(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    FLAT = "flat"


class Quote:
    symbol: str
    bid: float
    ask: float
    last: float
    timestamp: datetime
    bid_timestamp: datetime | None = None
    ask_timestamp: datetime | None = None

    def validate(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, Real)
            for value in (self.bid, self.ask, self.last)
        ):
            raise ValueError("Quote prices must be numeric")
        try:
            prices = (float(self.bid), float(self.ask), float(self.last))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Quote prices must be numeric") from exc
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise ValueError("Quote prices must be finite and positive")
        if prices[1] < prices[0]:
            raise ValueError("Quote ask cannot be below bid")
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError("Quote timestamp must be timezone-aware")
        for label, value in (
            ("bid timestamp", self.bid_timestamp),
            ("ask timestamp", self.ask_timestamp),
        ):
            if value is not None and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"Quote {label} must be timezone-aware")
        if (self.bid_timestamp is None) != (self.ask_timestamp is None):
            raise ValueError("Quote bid and ask timestamps must be provided together")

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def spread_bps(self) -> float:
        if self.bid <= 0 or self.ask <= 0 or self.mid <= 0:
            return float("inf")
        return (self.ask - self.bid) / self.mid * 10_000

    def age_seconds(self, now: datetime | None = None) -> float:
        reference = now or utc_now()
        timestamp = self.book_timestamp or self.timestamp
        return max(0.0, (reference - timestamp).total_seconds())

    @property
    def book_timestamp(self) -> datetime | None:
        """Conservative executable-book clock; the older side defines freshness."""

        if self.bid_timestamp is None or self.ask_timestamp is None:
            return None
        return min(self.bid_timestamp, self.ask_timestamp)

    @property
    def latest_book_timestamp(self) -> datetime | None:
        if self.bid_timestamp is None or self.ask_timestamp is None:
            return None
        return max(self.bid_timestamp, self.ask_timestamp)


class Bar:
    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    samples: int
    volume: float = 0.0


class Signal:
    regime: Regime
    confidence: float
    reason: str
    timestamp: datetime = field(default_factory=utc_now)

from __future__ import annotations

import math
import uuid

from grande_alpha.domain.models import Bar, Quote, Signal

from .base import Repository

QUOTE_BATCH_SCHEMA_VERSION = 2
EXACT_QUOTE_VALIDATOR_VERSION = 2


class MarketRepository(Repository):
    def record_quote(self, quote: Quote) -> None:
        """Record one legacy unbound quote; never eligible for exact runtime replay."""

        quote.validate()
        with self.transaction():
            self._connection.execute(
                "INSERT INTO quotes(observed_at,symbol,bid,ask,last,venue_timestamp) VALUES(?,?,?,?,?,?)",
                (
                    self._store._now().isoformat(),
                    quote.symbol,
                    quote.bid,
                    quote.ask,
                    quote.last,
                    quote.timestamp.isoformat(),
                ),
            )

    def record_bar(self, bar: Bar) -> None:
        with self.transaction():
            self._connection.execute(
                """INSERT OR REPLACE INTO bars(symbol,start_at,open,high,low,close,samples)
                VALUES(?,?,?,?,?,?,?)""",
                (bar.symbol, bar.start.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.samples),
            )

    def record_signal(self, signal: Signal) -> None:
        with self.transaction():
            self._connection.execute(
                "INSERT INTO signals(created_at,regime,confidence,reason) VALUES(?,?,?,?)",
                (signal.timestamp.isoformat(), signal.regime.value, signal.confidence, signal.reason),
            )

    def record_quote_batch(
        self,
        quotes: dict[str, Quote],
        *,
        stream_id: str,
        validation_profile: str = "passive_unvalidated",
        validation_version: int = 0,
        max_age_seconds: float | None = None,
        max_skew_seconds: float | None = None,
    ) -> str:
        """Atomically persist one accepted provider response with durable batch identity."""

        required = ("QQQ", "TQQQ", "SQQQ")
        if not isinstance(stream_id, str) or not stream_id.strip():
            raise ValueError("Quote batch stream ID must be a nonempty string")
        exact_profile = validation_profile == "exact_execution_quotes"
        if exact_profile:
            if validation_version != EXACT_QUOTE_VALIDATOR_VERSION:
                raise ValueError(f"Exact quote validator version must be {EXACT_QUOTE_VALIDATOR_VERSION}")
            if any(
                value is None or not math.isfinite(float(value)) or float(value) <= 0
                for value in (max_age_seconds, max_skew_seconds)
            ):
                raise ValueError("Exact quote validator envelope must be finite and positive")
            if float(max_skew_seconds) > min(5.0, float(max_age_seconds)):
                raise ValueError("Exact quote skew limit exceeds its validator envelope")
        elif (
            validation_profile != "passive_unvalidated"
            or validation_version != 0
            or max_age_seconds is not None
            or max_skew_seconds is not None
        ):
            raise ValueError("Unknown quote validator profile")
        if tuple(sorted(quotes)) != tuple(sorted(required)):
            raise ValueError("Quote batch must contain exactly QQQ, TQQQ, and SQQQ")
        for symbol in required:
            quote = quotes[symbol]
            quote.validate()
            if quote.symbol != symbol:
                raise ValueError(f"Quote batch key/symbol mismatch for {symbol}")
        observed = self._store._now()
        if exact_profile:
            if any(
                quotes[symbol].bid_timestamp is None or quotes[symbol].ask_timestamp is None
                for symbol in required
            ):
                raise ValueError("Exact quote batch requires bid and ask venue clocks")
            book_times = [
                timestamp
                for symbol in required
                for timestamp in (
                    quotes[symbol].bid_timestamp,
                    quotes[symbol].ask_timestamp,
                )
                if timestamp is not None
            ]
            ages = [(observed - timestamp).total_seconds() for timestamp in book_times]
            if any(age < -2.0 or age > float(max_age_seconds) for age in ages):
                raise ValueError("Exact quote batch violates its recorded age envelope")
            if (max(book_times) - min(book_times)).total_seconds() > float(max_skew_seconds):
                raise ValueError("Exact quote batch violates its recorded skew envelope")
        batch_id = str(uuid.uuid4())
        observed_at = observed.isoformat()
        rows = [
            (
                observed_at,
                symbol,
                quotes[symbol].bid,
                quotes[symbol].ask,
                quotes[symbol].last,
                quotes[symbol].timestamp.isoformat(),
                (
                    quotes[symbol].bid_timestamp.isoformat()
                    if quotes[symbol].bid_timestamp is not None
                    else None
                ),
                (
                    quotes[symbol].ask_timestamp.isoformat()
                    if quotes[symbol].ask_timestamp is not None
                    else None
                ),
                batch_id,
                position,
            )
            for position, symbol in enumerate(required)
        ]
        with self.transaction():
            self._connection.execute(
                """INSERT INTO quote_batches(
                batch_id,stream_id,observed_at,schema_version,symbol_count,
                validation_profile,validation_version,max_age_seconds,max_skew_seconds
                ) VALUES(?,?,?,?,3,?,?,?,?)""",
                (
                    batch_id,
                    stream_id.strip(),
                    observed_at,
                    QUOTE_BATCH_SCHEMA_VERSION,
                    validation_profile,
                    validation_version,
                    max_age_seconds,
                    max_skew_seconds,
                ),
            )
            self._connection.executemany(
                """INSERT INTO quotes(
                observed_at,symbol,bid,ask,last,venue_timestamp,bid_timestamp,ask_timestamp,
                batch_id,batch_position
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        return batch_id

    def prune_market_history(self, retention_days: int) -> dict[str, int]:
        """Remove old derived market observations, never orders, receipts, or research records."""
        if retention_days < 1:
            raise ValueError("Market-history retention must be at least one day")
        modifier = f"-{retention_days} days"
        removed: dict[str, int] = {}
        with self.transaction():
            for table, time_column in (
                ("quotes", "observed_at"),
                ("bars", "start_at"),
                ("signals", "created_at"),
            ):
                cursor = self._connection.execute(
                    f"DELETE FROM {table} WHERE {time_column} < datetime('now', ?)",
                    (modifier,),
                )
                removed[table] = max(0, cursor.rowcount)
            cursor = self._connection.execute(
                """DELETE FROM quote_batches
                WHERE NOT EXISTS (
                    SELECT 1 FROM quotes WHERE quotes.batch_id=quote_batches.batch_id
                )"""
            )
            removed["quote_batches"] = max(0, cursor.rowcount)
        return removed

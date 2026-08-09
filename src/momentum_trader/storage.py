from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from momentum_trader.config import data_dir
from momentum_trader.models import Bar, OrderIntent, Quote, Signal, utc_now


class AuditStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "momentum_trader.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS quotes (
                    id INTEGER PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    bid REAL NOT NULL,
                    ask REAL NOT NULL,
                    last REAL NOT NULL,
                    venue_timestamp TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_quotes_symbol_time ON quotes(symbol, observed_at);
                CREATE TABLE IF NOT EXISTS bars (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    samples INTEGER NOT NULL,
                    UNIQUE(symbol, start_at)
                );
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_intents (
                    ref_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    broker_order_id TEXT,
                    broker_state TEXT
                );
                """
            )

    def record_quote(self, quote: Quote) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO quotes(observed_at,symbol,bid,ask,last,venue_timestamp) VALUES(?,?,?,?,?,?)",
                (utc_now().isoformat(), quote.symbol, quote.bid, quote.ask, quote.last, quote.timestamp.isoformat()),
            )

    def record_bar(self, bar: Bar) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT OR REPLACE INTO bars(symbol,start_at,open,high,low,close,samples)
                VALUES(?,?,?,?,?,?,?)""",
                (bar.symbol, bar.start.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.samples),
            )

    def record_signal(self, signal: Signal) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO signals(created_at,regime,confidence,reason) VALUES(?,?,?,?)",
                (signal.timestamp.isoformat(), signal.regime.value, signal.confidence, signal.reason),
            )

    def receipt(self, category: str, summary: str, payload: Any = None, severity: str = "info") -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO receipts(created_at,category,severity,summary,payload_json) VALUES(?,?,?,?,?)",
                (utc_now().isoformat(), category, severity, summary, json.dumps(payload or {}, default=str)),
            )

    def record_intent(self, intent: OrderIntent) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO order_intents(ref_id,created_at,symbol,side,reason,payload_json)
                VALUES(?,?,?,?,?,?)""",
                (
                    intent.ref_id,
                    intent.created_at.isoformat(),
                    intent.symbol,
                    intent.side,
                    intent.reason,
                    json.dumps(intent.as_dict(), default=str),
                ),
            )

    def update_intent(self, ref_id: str, order_id: str | None, state: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE order_intents SET broker_order_id=?, broker_state=? WHERE ref_id=?",
                (order_id, state, ref_id),
            )

    def recent_receipts(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()


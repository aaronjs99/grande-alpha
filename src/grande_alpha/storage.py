from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from grande_alpha.config import data_dir
from grande_alpha.models import Bar, OrderIntent, Quote, Signal, utc_now


class AuditStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "grande_alpha.db")
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
                CREATE TABLE IF NOT EXISTS research_fund (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    period TEXT NOT NULL,
                    realized_profit REAL NOT NULL,
                    fees REAL NOT NULL,
                    tax_reserve REAL NOT NULL,
                    contribution_rate REAL NOT NULL,
                    eligible_contribution REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('planned','confirmed')),
                    confirmed_at TEXT,
                    confirmation_reference TEXT,
                    notes TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sandbox_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    data_source TEXT NOT NULL,
                    replay_start TEXT NOT NULL,
                    replay_end TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sandbox_fills (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES sandbox_runs(run_id) ON DELETE CASCADE,
                    filled_at TEXT NOT NULL,
                    symbol TEXT NOT NULL CHECK(symbol IN ('TQQQS','SQQQS')),
                    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    commission REAL NOT NULL,
                    realized_pnl REAL,
                    reason TEXT NOT NULL,
                    cash_after REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sandbox_fills_run ON sandbox_fills(run_id,id);
                CREATE TABLE IF NOT EXISTS sandbox_execution_events (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES sandbox_runs(run_id) ON DELETE CASCADE,
                    event_at TEXT NOT NULL,
                    symbol TEXT NOT NULL CHECK(symbol IN ('TQQQS','SQQQS')),
                    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                    status TEXT NOT NULL,
                    requested_quantity REAL NOT NULL,
                    filled_quantity REAL NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sandbox_events_run
                    ON sandbox_execution_events(run_id,id);
                CREATE TABLE IF NOT EXISTS research_promotions (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    strategy_fingerprint TEXT NOT NULL,
                    policy_version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('SHADOW_ONLY','LIVE_REVIEW_ELIGIBLE')),
                    source TEXT NOT NULL,
                    replay_end TEXT NOT NULL,
                    gates_json TEXT NOT NULL,
                    risk_envelope_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_research_promotions_fingerprint_time
                    ON research_promotions(strategy_fingerprint,created_at DESC);
                """
            )
            promotion_columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(research_promotions)").fetchall()
            }
            if "risk_envelope_json" not in promotion_columns:
                self._connection.execute(
                    "ALTER TABLE research_promotions ADD COLUMN risk_envelope_json TEXT NOT NULL DEFAULT '{}'"
                )

    def record_quote(self, quote: Quote) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO quotes(observed_at,symbol,bid,ask,last,venue_timestamp) VALUES(?,?,?,?,?,?)",
                (
                    utc_now().isoformat(),
                    quote.symbol,
                    quote.bid,
                    quote.ask,
                    quote.last,
                    quote.timestamp.isoformat(),
                ),
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

    def prune_market_history(self, retention_days: int) -> dict[str, int]:
        """Remove old derived market observations, never orders, receipts, or research records."""
        if retention_days < 1:
            raise ValueError("Market-history retention must be at least one day")
        modifier = f"-{retention_days} days"
        removed: dict[str, int] = {}
        with self._lock, self._connection:
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
        return removed

    def plan_research_contribution(
        self,
        period: str,
        realized_profit: float,
        fees: float,
        tax_reserve: float,
        contribution_rate: float,
        notes: str = "",
    ) -> int:
        try:
            parsed_period = datetime.strptime(period, "%Y-%m")
        except ValueError as exc:
            raise ValueError("Period must be YYYY-MM") from exc
        if parsed_period.strftime("%Y-%m") != period:
            raise ValueError("Period must be YYYY-MM")
        if fees < 0 or tax_reserve < 0:
            raise ValueError("Fees and tax reserve cannot be negative")
        if not 0 <= contribution_rate <= 1:
            raise ValueError("Contribution rate must be between 0 and 1")
        distributable = max(0.0, realized_profit - fees - tax_reserve)
        eligible = round(distributable * contribution_rate, 2)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO research_fund(
                    created_at,period,realized_profit,fees,tax_reserve,contribution_rate,
                    eligible_contribution,status,notes
                ) VALUES(?,?,?,?,?,?,?,'planned',?)""",
                (
                    utc_now().isoformat(),
                    period,
                    realized_profit,
                    fees,
                    tax_reserve,
                    contribution_rate,
                    eligible,
                    notes,
                ),
            )
            entry_id = int(cursor.lastrowid)
        self.receipt(
            "research_fund",
            f"Planned ${eligible:,.2f} personal contribution for {period}",
            {"entry_id": entry_id, "eligible_contribution": eligible, "status": "planned"},
        )
        return entry_id

    def confirm_research_contribution(self, entry_id: int, reference: str) -> None:
        if not reference.strip():
            raise ValueError("A confirmation reference is required")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT eligible_contribution,status FROM research_fund WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Research-fund entry does not exist")
            if row["status"] == "confirmed":
                raise ValueError("Research-fund entry is already confirmed")
            confirmed_at = utc_now().isoformat()
            self._connection.execute(
                """UPDATE research_fund
                SET status='confirmed',confirmed_at=?,confirmation_reference=? WHERE id=?""",
                (confirmed_at, reference.strip(), entry_id),
            )
        self.receipt(
            "research_fund",
            f"Confirmed ${float(row['eligible_contribution']):,.2f} personal contribution",
            {"entry_id": entry_id, "reference": reference.strip(), "status": "confirmed"},
            "warning",
        )

    def research_fund_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM research_fund ORDER BY period DESC,id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def confirmed_research_total(self) -> float:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(SUM(eligible_contribution),0) AS total FROM research_fund WHERE status='confirmed'"
            ).fetchone()
        return float(row["total"] if row else 0.0)

    def record_sandbox_run(
        self,
        run_id: str,
        data_source: str,
        replay_start: str,
        replay_end: str,
        config: dict[str, Any],
        metrics: dict[str, Any],
        fills: list[dict[str, Any]],
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sandbox_runs(
                    run_id,created_at,data_source,replay_start,replay_end,config_json,metrics_json
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    run_id,
                    utc_now().isoformat(),
                    data_source,
                    replay_start,
                    replay_end,
                    json.dumps(config, default=str),
                    json.dumps(metrics, default=str),
                ),
            )
            self._connection.executemany(
                """INSERT INTO sandbox_fills(
                    run_id,filled_at,symbol,side,quantity,price,commission,
                    realized_pnl,reason,cash_after
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id,
                        fill["timestamp"],
                        fill["symbol"],
                        fill["side"],
                        fill["quantity"],
                        fill["price"],
                        fill["commission"],
                        fill["realized_pnl"],
                        fill["reason"],
                        fill["cash_after"],
                    )
                    for fill in fills
                ],
            )
            self._connection.executemany(
                """INSERT INTO sandbox_execution_events(
                    run_id,event_at,symbol,side,status,requested_quantity,filled_quantity,reason
                ) VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id,
                        event["timestamp"],
                        event["symbol"],
                        event["side"],
                        event["status"],
                        event["requested_quantity"],
                        event["filled_quantity"],
                        event["reason"],
                    )
                    for event in (events or [])
                ],
            )
        self.receipt(
            "sandbox",
            f"Saved sandbox replay {run_id[:8]} with {len(fills)} virtual fills",
            {"run_id": run_id, "metrics": metrics, "data_source": data_source},
        )

    def recent_sandbox_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sandbox_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def record_research_promotion(
        self,
        *,
        dataset_hash: str,
        strategy_fingerprint: str,
        policy_version: int,
        status: str,
        source: str,
        replay_end: str,
        gates: list[dict[str, Any]],
        risk_envelope: dict[str, float | int],
    ) -> int:
        if status not in {"SHADOW_ONLY", "LIVE_REVIEW_ELIGIBLE"}:
            raise ValueError("Unknown research-promotion status")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO research_promotions(
                    created_at,dataset_hash,strategy_fingerprint,policy_version,status,
                    source,replay_end,gates_json,risk_envelope_json
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    utc_now().isoformat(),
                    dataset_hash,
                    strategy_fingerprint,
                    policy_version,
                    status,
                    source,
                    replay_end,
                    json.dumps(gates, default=str),
                    json.dumps(risk_envelope, sort_keys=True),
                ),
            )
            promotion_id = int(cursor.lastrowid)
        self.receipt(
            "research_promotion",
            f"Research evidence result: {status}",
            {
                "promotion_id": promotion_id,
                "dataset_hash": dataset_hash,
                "strategy_fingerprint": strategy_fingerprint,
                "policy_version": policy_version,
                "status": status,
                "risk_envelope": risk_envelope,
            },
            "warning" if status == "SHADOW_ONLY" else "info",
        )
        return promotion_id

    def current_live_evidence(
        self,
        strategy_fingerprint: str,
        max_age_days: int = 30,
        requested_envelope: dict[str, float | int] | None = None,
    ) -> dict[str, Any] | None:
        if max_age_days < 1:
            raise ValueError("Evidence age must be at least one day")
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM research_promotions
                WHERE strategy_fingerprint=? AND status='LIVE_REVIEW_ELIGIBLE'
                AND julianday(created_at) >= julianday('now', ?)
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (strategy_fingerprint, f"-{max_age_days} days"),
            ).fetchone()
        if not row:
            return None
        evidence = dict(row)
        try:
            tested = json.loads(evidence["risk_envelope_json"])
        except (KeyError, TypeError, json.JSONDecodeError):
            return None
        required = {
            "max_order_notional",
            "max_total_exposure",
            "max_daily_loss",
            "max_trades",
            "max_orders_per_minute",
            "max_spread_bps",
        }
        if not required <= tested.keys():
            return None
        if requested_envelope and any(
            float(requested_envelope[name]) > float(tested[name]) for name in required
        ):
            return None
        evidence["risk_envelope"] = tested
        return evidence

    def close(self) -> None:
        with self._lock:
            self._connection.close()

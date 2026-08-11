from __future__ import annotations

import json
import math
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
                    cash_after REAL NOT NULL,
                    unsettled_cash_after REAL NOT NULL DEFAULT 0
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
                CREATE TABLE IF NOT EXISTS research_trials (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    trial_fingerprint TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    UNIQUE(dataset_hash,trial_fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_research_trials_dataset
                    ON research_trials(dataset_hash,id);
                CREATE TABLE IF NOT EXISTS research_holdouts (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    development_hash TEXT NOT NULL,
                    holdout_hash TEXT NOT NULL,
                    holdout_start TEXT NOT NULL,
                    holdout_end TEXT NOT NULL,
                    policy_version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'RESERVED','FROZEN','EVALUATING','CONSUMED','INVALID'
                    )),
                    selected_fingerprint TEXT,
                    evaluation_started_at TEXT,
                    consumed_at TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(dataset_hash,holdout_start,holdout_end)
                );
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
            if "holdout_id" not in promotion_columns:
                self._connection.execute("ALTER TABLE research_promotions ADD COLUMN holdout_id INTEGER")
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_research_holdouts_hash
                ON research_holdouts(holdout_hash)"""
            )
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_research_promotions_holdout
                ON research_promotions(holdout_id) WHERE holdout_id IS NOT NULL"""
            )
            sandbox_fill_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(sandbox_fills)").fetchall()
            }
            if "unsettled_cash_after" not in sandbox_fill_columns:
                self._connection.execute(
                    "ALTER TABLE sandbox_fills ADD COLUMN unsettled_cash_after REAL NOT NULL DEFAULT 0"
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
                    realized_pnl,reason,cash_after,unsettled_cash_after
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
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
                        fill.get("unsettled_cash_after", 0.0),
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

    def sandbox_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM sandbox_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            fills = self._connection.execute(
                "SELECT * FROM sandbox_fills WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
            events = self._connection.execute(
                "SELECT * FROM sandbox_execution_events WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        result["metrics"] = json.loads(result.pop("metrics_json"))
        result["fills"] = [dict(value) for value in fills]
        result["events"] = [dict(value) for value in events]
        return result

    def record_research_trials(self, dataset_hash: str, trials: list[dict[str, Any]]) -> int:
        """Commit unique candidate trials before promotion statistics are evaluated."""

        with self._lock, self._connection:
            before = self._connection.total_changes
            self._connection.executemany(
                """INSERT OR IGNORE INTO research_trials(
                    created_at,dataset_hash,trial_fingerprint,config_json,metrics_json
                ) VALUES(?,?,?,?,?)""",
                [
                    (
                        utc_now().isoformat(),
                        dataset_hash,
                        str(trial["trial_fingerprint"]),
                        json.dumps(trial["config"], sort_keys=True, default=str),
                        json.dumps(trial["metrics"], sort_keys=True, default=str),
                    )
                    for trial in trials
                ],
            )
            inserted = self._connection.total_changes - before
        return inserted

    def research_trial_count(self, dataset_hash: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM research_trials WHERE dataset_hash=?",
                (dataset_hash,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def reserve_research_holdout(
        self,
        *,
        dataset_hash: str,
        development_hash: str,
        holdout_hash: str,
        holdout_start: str,
        holdout_end: str,
        policy_version: int,
    ) -> int:
        """Reserve an unseen chronological block before candidate evaluation begins."""

        with self._lock:
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        """INSERT INTO research_holdouts(
                            created_at,dataset_hash,development_hash,holdout_hash,
                            holdout_start,holdout_end,policy_version,status
                        ) VALUES(?,?,?,?,?,?,?,'RESERVED')""",
                        (
                            utc_now().isoformat(),
                            dataset_hash,
                            development_hash,
                            holdout_hash,
                            holdout_start,
                            holdout_end,
                            policy_version,
                        ),
                    )
                    return int(cursor.lastrowid)
            except sqlite3.IntegrityError as exc:
                existing = self._connection.execute(
                    """SELECT * FROM research_holdouts
                    WHERE holdout_hash=? OR (
                        dataset_hash=? AND holdout_start=? AND holdout_end=?
                    ) ORDER BY id LIMIT 1""",
                    (holdout_hash, dataset_hash, holdout_start, holdout_end),
                ).fetchone()
                if existing is not None and all(
                    (
                        existing["dataset_hash"] == dataset_hash,
                        existing["development_hash"] == development_hash,
                        existing["holdout_hash"] == holdout_hash,
                        existing["holdout_start"] == holdout_start,
                        existing["holdout_end"] == holdout_end,
                        int(existing["policy_version"]) == policy_version,
                        existing["status"] == "RESERVED",
                    )
                ):
                    return int(existing["id"])
                raise ValueError("This final holdout was already reserved or consumed") from exc

    def freeze_research_holdout(self, holdout_id: int, selected_fingerprint: str) -> None:
        if not selected_fingerprint:
            raise ValueError("A selected strategy fingerprint is required")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE research_holdouts
                SET status='FROZEN',selected_fingerprint=?
                WHERE id=? AND status='RESERVED'""",
                (selected_fingerprint, holdout_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Final holdout is not available to freeze")

    def claim_research_holdout(self, holdout_id: int, selected_fingerprint: str) -> None:
        """Atomically make a frozen holdout unavailable before its data is evaluated."""

        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE research_holdouts
                SET status='EVALUATING',evaluation_started_at=?
                WHERE id=? AND status='FROZEN' AND selected_fingerprint=?""",
                (utc_now().isoformat(), holdout_id, selected_fingerprint),
            )
            if cursor.rowcount != 1:
                raise ValueError("Final holdout is not frozen for this exact strategy")

    def consume_research_holdout(
        self,
        holdout_id: int,
        selected_fingerprint: str,
        metrics: dict[str, Any],
    ) -> None:
        if not metrics:
            raise ValueError("Final holdout metrics are required before consumption")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE research_holdouts
                SET status='CONSUMED',consumed_at=?,metrics_json=?
                WHERE id=? AND status='EVALUATING' AND selected_fingerprint=?""",
                (
                    utc_now().isoformat(),
                    json.dumps(metrics, sort_keys=True, default=str),
                    holdout_id,
                    selected_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Final holdout was not claimed or was already consumed")

    def invalidate_research_holdout(self, holdout_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE research_holdouts SET status='INVALID'
                WHERE id=? AND status IN ('RESERVED','FROZEN','EVALUATING')""",
                (holdout_id,),
            )

    def research_holdout(self, holdout_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM research_holdouts WHERE id=?", (holdout_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            metrics = json.loads(result.pop("metrics_json"))
        except (TypeError, json.JSONDecodeError):
            metrics = {}
        result["metrics"] = metrics if isinstance(metrics, dict) else {}
        return result

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
        holdout_id: int | None = None,
    ) -> int:
        if status not in {"SHADOW_ONLY", "LIVE_REVIEW_ELIGIBLE"}:
            raise ValueError("Unknown research-promotion status")
        if not gates:
            raise ValueError("Research promotion requires at least one evidence gate")
        if status == "LIVE_REVIEW_ELIGIBLE":
            from grande_alpha.evidence import (
                REQUIRED_LIVE_GATE_NAMES,
                RUNTIME_SIZING_PARITY_CERTIFIED,
            )

            gate_names = [gate.get("name") for gate in gates if isinstance(gate, dict)]
            if (
                len(gate_names) != len(gates)
                or len(gate_names) != len(set(gate_names))
                or set(gate_names) != REQUIRED_LIVE_GATE_NAMES
                or not all(gate.get("passed") is True for gate in gates)
            ):
                raise ValueError("Live-review eligibility requires every canonical evidence gate to pass")
        if status == "LIVE_REVIEW_ELIGIBLE":
            with self._lock:
                holdout = self._connection.execute(
                    """SELECT status,dataset_hash,holdout_hash,holdout_start,holdout_end,
                    selected_fingerprint,policy_version,metrics_json
                    FROM research_holdouts WHERE id=?""",
                    (holdout_id,),
                ).fetchone()
                existing_promotion = self._connection.execute(
                    "SELECT id FROM research_promotions WHERE holdout_id=?",
                    (holdout_id,),
                ).fetchone()
            try:
                holdout_metrics = json.loads(holdout["metrics_json"]) if holdout else {}
            except (TypeError, json.JSONDecodeError):
                holdout_metrics = {}
            if (
                holdout is None
                or holdout["status"] != "CONSUMED"
                or holdout["dataset_hash"] != dataset_hash
                or holdout["selected_fingerprint"] != strategy_fingerprint
                or int(holdout["policy_version"]) != policy_version
                or holdout["holdout_end"] != replay_end
                or existing_promotion is not None
                or not _passing_holdout_metrics(holdout_metrics, holdout)
            ):
                raise ValueError(
                    "Live-review eligibility requires one unused passing final holdout for the exact dataset and strategy"
                )
            if not _valid_risk_envelope(risk_envelope):
                raise ValueError("Live-review eligibility requires a finite positive risk envelope")
            if not RUNTIME_SIZING_PARITY_CERTIFIED:
                raise ValueError(
                    "Live-review eligibility is blocked until non-cash replay and runtime "
                    "share the certified sizing contract"
                )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO research_promotions(
                    created_at,dataset_hash,strategy_fingerprint,policy_version,status,
                    source,replay_end,gates_json,risk_envelope_json,holdout_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
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
                    holdout_id,
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
                "holdout_id": holdout_id,
            },
            "warning" if status == "SHADOW_ONLY" else "info",
        )
        return promotion_id

    @staticmethod
    def _decode_research_promotion(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try:
            gates = json.loads(result.pop("gates_json"))
            risk_envelope = json.loads(result.pop("risk_envelope_json"))
        except (TypeError, json.JSONDecodeError):
            gates, risk_envelope = [], {}
            result["decode_error"] = "Stored evidence JSON is invalid"
        result["gates"] = gates if isinstance(gates, list) else []
        result["risk_envelope"] = risk_envelope if isinstance(risk_envelope, dict) else {}
        return result

    def recent_research_promotions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM research_promotions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._decode_research_promotion(row) for row in rows]

    def research_promotion(self, promotion_id: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            if promotion_id is None:
                row = self._connection.execute(
                    "SELECT * FROM research_promotions ORDER BY id DESC LIMIT 1"
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT * FROM research_promotions WHERE id=?", (promotion_id,)
                ).fetchone()
        return self._decode_research_promotion(row) if row is not None else None

    def current_live_evidence(
        self,
        strategy_fingerprint: str,
        max_age_days: int = 30,
        requested_envelope: dict[str, float | int] | None = None,
    ) -> dict[str, Any] | None:
        # Import locally so the storage layer stays usable during application
        # initialization while still rejecting certificates from an older policy.
        from grande_alpha.evidence import (
            EVIDENCE_POLICY_VERSION,
            RUNTIME_SIZING_PARITY_CERTIFIED,
        )

        if max_age_days < 1:
            raise ValueError("Evidence age must be at least one day")
        if not RUNTIME_SIZING_PARITY_CERTIFIED:
            return None
        with self._lock:
            row = self._connection.execute(
                """SELECT p.*,h.dataset_hash AS holdout_dataset_hash,
                h.holdout_hash AS sealed_holdout_hash,h.holdout_start AS sealed_holdout_start,
                h.holdout_end AS sealed_holdout_end,h.metrics_json AS holdout_metrics_json
                FROM research_promotions AS p
                JOIN research_holdouts AS h ON h.id=p.holdout_id
                WHERE p.strategy_fingerprint=? AND p.status='LIVE_REVIEW_ELIGIBLE'
                AND h.status='CONSUMED'
                AND h.selected_fingerprint=p.strategy_fingerprint
                AND h.policy_version=p.policy_version
                AND p.policy_version=?
                AND julianday(p.created_at) >= julianday('now', ?)
                ORDER BY p.created_at DESC,p.id DESC LIMIT 1""",
                (strategy_fingerprint, EVIDENCE_POLICY_VERSION, f"-{max_age_days} days"),
            ).fetchone()
        if not row:
            return None
        evidence = dict(row)
        try:
            tested = json.loads(evidence["risk_envelope_json"])
            gates = json.loads(evidence["gates_json"])
            holdout_metrics = json.loads(evidence["holdout_metrics_json"])
        except (KeyError, TypeError, json.JSONDecodeError):
            return None
        from grande_alpha.evidence import REQUIRED_LIVE_GATE_NAMES

        gate_names = (
            [gate.get("name") for gate in gates if isinstance(gate, dict)] if isinstance(gates, list) else []
        )
        if (
            not isinstance(gates, list)
            or not gates
            or len(gate_names) != len(gates)
            or len(gate_names) != len(set(gate_names))
            or set(gate_names) != REQUIRED_LIVE_GATE_NAMES
            or not all(gate.get("passed") is True for gate in gates)
            or evidence["dataset_hash"] != evidence["holdout_dataset_hash"]
            or evidence["replay_end"] != evidence["sealed_holdout_end"]
            or not _passing_holdout_metrics(
                holdout_metrics,
                {
                    "holdout_hash": evidence["sealed_holdout_hash"],
                    "holdout_start": evidence["sealed_holdout_start"],
                    "holdout_end": evidence["sealed_holdout_end"],
                },
            )
        ):
            return None
        if not _valid_risk_envelope(tested):
            return None
        if requested_envelope is not None:
            if not _valid_risk_envelope(requested_envelope):
                return None
            if any(float(requested_envelope[name]) > float(tested[name]) for name in _RISK_ENVELOPE_FIELDS):
                return None
        evidence["risk_envelope"] = tested
        return evidence

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _passing_holdout_metrics(metrics: Any, holdout: Any) -> bool:
    """Recompute the immutable final-holdout pass at the storage trust boundary."""

    if not isinstance(metrics, dict):
        return False
    try:
        net_pnl = float(metrics["net_pnl"])
        round_trips = float(metrics["round_trips"])
        profit_factor = float(metrics["profit_factor"])
        expectancy = float(metrics["expectancy"])
        max_drawdown = float(metrics["max_drawdown_pct"])
        cost_multiplier = float(metrics["cost_multiplier"])
        forced_flatten_count = float(metrics["forced_flatten_count"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    finite_values = (
        net_pnl,
        round_trips,
        expectancy,
        max_drawdown,
        cost_multiplier,
        forced_flatten_count,
    )
    if not all(math.isfinite(value) for value in finite_values):
        return False
    if math.isnan(profit_factor) or profit_factor == -math.inf:
        return False
    return bool(
        metrics.get("holdout_hash") == holdout["holdout_hash"]
        and metrics.get("holdout_start") == holdout["holdout_start"]
        and metrics.get("holdout_end") == holdout["holdout_end"]
        and math.isclose(cost_multiplier, 3.0, rel_tol=0.0, abs_tol=1e-12)
        and net_pnl > 0
        and round_trips >= 5
        and profit_factor >= 1.10
        and expectancy > 0
        and 0 <= max_drawdown <= 5.0
        and metrics.get("ending_position") is None
        and forced_flatten_count == 0
    )


_RISK_ENVELOPE_FIELDS = {
    "max_order_notional",
    "max_total_exposure",
    "max_daily_loss",
    "max_trades",
    "max_orders_per_minute",
    "max_spread_bps",
}


def _valid_risk_envelope(envelope: Any) -> bool:
    if not isinstance(envelope, dict) or not _RISK_ENVELOPE_FIELDS <= envelope.keys():
        return False
    try:
        values = {name: float(envelope[name]) for name in _RISK_ENVELOPE_FIELDS}
    except (TypeError, ValueError, OverflowError):
        return False
    if not all(math.isfinite(value) and value > 0 for value in values.values()):
        return False
    return all(values[name].is_integer() for name in ("max_trades", "max_orders_per_minute"))

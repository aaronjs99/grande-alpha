from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import UTC, date, datetime, timedelta
from numbers import Real
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from grande_alpha.config import data_dir
from grande_alpha.models import Bar, OrderIntent, Quote, Signal, utc_now


def _parse_aware_utc(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp with a UTC offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


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
                    broker_state TEXT,
                    account_number TEXT,
                    authority_id TEXT,
                    strategy_fingerprint TEXT,
                    authorized_notional REAL NOT NULL DEFAULT 0,
                    submission_started_at TEXT
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
                    risk_envelope_json TEXT NOT NULL DEFAULT '{}',
                    provenance_hash TEXT NOT NULL DEFAULT '',
                    provenance_json TEXT NOT NULL DEFAULT '{}'
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
                    provenance_hash TEXT NOT NULL DEFAULT '',
                    development_quality_json TEXT NOT NULL DEFAULT '{}',
                    holdout_quality_json TEXT NOT NULL DEFAULT '{}',
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
            if "provenance_hash" not in promotion_columns:
                self._connection.execute(
                    "ALTER TABLE research_promotions ADD COLUMN provenance_hash TEXT NOT NULL DEFAULT ''"
                )
            if "provenance_json" not in promotion_columns:
                self._connection.execute(
                    "ALTER TABLE research_promotions ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'"
                )
            holdout_columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(research_holdouts)").fetchall()
            }
            if "provenance_hash" not in holdout_columns:
                self._connection.execute(
                    "ALTER TABLE research_holdouts ADD COLUMN provenance_hash TEXT NOT NULL DEFAULT ''"
                )
            if "development_quality_json" not in holdout_columns:
                self._connection.execute(
                    "ALTER TABLE research_holdouts ADD COLUMN development_quality_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "holdout_quality_json" not in holdout_columns:
                self._connection.execute(
                    "ALTER TABLE research_holdouts ADD COLUMN holdout_quality_json TEXT NOT NULL DEFAULT '{}'"
                )
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
            order_intent_columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(order_intents)").fetchall()
            }
            intent_migrations = {
                "account_number": "TEXT",
                "authority_id": "TEXT",
                "strategy_fingerprint": "TEXT",
                "authorized_notional": "REAL NOT NULL DEFAULT 0",
                "submission_started_at": "TEXT",
            }
            for column, declaration in intent_migrations.items():
                if column not in order_intent_columns:
                    self._connection.execute(
                        f"ALTER TABLE order_intents ADD COLUMN {column} {declaration}"
                    )
            self._connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_order_intents_account_submission
                ON order_intents(account_number,submission_started_at)"""
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

    def mark_intent_submitting(
        self,
        ref_id: str,
        *,
        account_number: str,
        authority_id: str,
        strategy_fingerprint: str,
        authorized_notional: float,
    ) -> None:
        """Durably record a placement invocation before any broker network write."""

        identifiers = {
            "intent reference": ref_id,
            "account number": account_number,
            "authority id": authority_id,
            "strategy fingerprint": strategy_fingerprint,
        }
        if any(not isinstance(value, str) or not value.strip() for value in identifiers.values()):
            raise ValueError("Submission provenance identifiers must be nonempty strings")
        if (
            isinstance(authorized_notional, bool)
            or not isinstance(authorized_notional, Real)
            or not math.isfinite(float(authorized_notional))
            or float(authorized_notional) < 0
        ):
            raise ValueError("Authorized notional must be finite and nonnegative")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE order_intents
                SET account_number=?,authority_id=?,strategy_fingerprint=?,
                    authorized_notional=?,submission_started_at=?,broker_state='submitting'
                WHERE ref_id=? AND submission_started_at IS NULL""",
                (
                    account_number.strip(),
                    authority_id.strip(),
                    strategy_fingerprint.strip(),
                    float(authorized_notional),
                    utc_now().isoformat(),
                    ref_id.strip(),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Order intent is missing or placement was already invoked")

    def unresolved_order_intents(self, account_number: str) -> list[dict[str, Any]]:
        """Return uncertain placements, including legacy rows with unknown account ownership."""

        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Account number must be a nonempty string")
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM order_intents
                WHERE lower(trim(COALESCE(broker_state,''))) IN ('submitting','submission_uncertain')
                AND (account_number=? OR account_number IS NULL)
                ORDER BY created_at,ref_id""",
                (account_number.strip(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def live_daily_usage(self, account_number: str, et_date: str) -> dict[str, float | int | str]:
        """Restore placement-attempt usage and receipt-chain state for an ET trading date."""

        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Account number must be a nonempty string")
        try:
            requested_date = date.fromisoformat(et_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("Trading date must use YYYY-MM-DD") from exc
        with self._lock:
            rows = self._connection.execute(
                """SELECT submission_started_at,authorized_notional FROM order_intents
                WHERE account_number=? AND submission_started_at IS NOT NULL""",
                (account_number.strip(),),
            ).fetchall()
            receipt = self._connection.execute(
                """SELECT payload_json FROM receipts WHERE category='authority_action'
                ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        eastern = ZoneInfo("America/New_York")
        daily_notional = 0.0
        submitted_orders = 0
        for row in rows:
            try:
                submitted_at = datetime.fromisoformat(row["submission_started_at"])
                notional = float(row["authorized_notional"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Stored submission provenance is invalid") from exc
            if submitted_at.tzinfo is None or not math.isfinite(notional) or notional < 0:
                raise ValueError("Stored submission provenance is invalid")
            if submitted_at.astimezone(eastern).date() == requested_date:
                daily_notional += notional
                submitted_orders += 1
        last_digest = ""
        if receipt is not None:
            try:
                payload = json.loads(receipt["payload_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Stored authority receipt is invalid") from exc
            if not isinstance(payload, dict):
                raise ValueError("Stored authority receipt is invalid")
            digest = payload.get("receipt_digest", "")
            if digest is not None and not isinstance(digest, str):
                raise ValueError("Stored authority receipt digest is invalid")
            last_digest = digest or ""
        return {
            "daily_notional": daily_notional,
            "submitted_orders": submitted_orders,
            "last_receipt_digest": last_digest,
        }

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
        provenance_hash: str = "",
        development_quality: dict[str, Any] | None = None,
        holdout_quality: dict[str, Any] | None = None,
    ) -> int:
        """Reserve an unseen chronological block before candidate evaluation begins."""

        requested_start = _parse_aware_utc(holdout_start, field="holdout_start")
        requested_end = _parse_aware_utc(holdout_end, field="holdout_end")
        if requested_start >= requested_end:
            raise ValueError("holdout_start must be earlier than holdout_end")

        with self._lock, self._connection:
            prior = self._connection.execute(
                "SELECT * FROM research_holdouts ORDER BY id"
            ).fetchall()
            for existing in prior:
                exact_reserved = all(
                    (
                        existing["dataset_hash"] == dataset_hash,
                        existing["development_hash"] == development_hash,
                        existing["holdout_hash"] == holdout_hash,
                        existing["holdout_start"] == holdout_start,
                        existing["holdout_end"] == holdout_end,
                        int(existing["policy_version"]) == policy_version,
                        existing["provenance_hash"] == provenance_hash,
                        existing["development_quality_json"]
                        == json.dumps(development_quality or {}, sort_keys=True, default=str),
                        existing["holdout_quality_json"]
                        == json.dumps(holdout_quality or {}, sort_keys=True, default=str),
                        existing["status"] == "RESERVED",
                    )
                )
                if exact_reserved:
                    return int(existing["id"])
                try:
                    prior_start = _parse_aware_utc(
                        existing["holdout_start"], field="stored holdout_start"
                    )
                    prior_end = _parse_aware_utc(
                        existing["holdout_end"], field="stored holdout_end"
                    )
                except ValueError as exc:
                    raise ValueError(
                        "A prior final holdout has invalid dates; new holdouts are blocked fail-closed"
                    ) from exc
                if prior_start >= prior_end:
                    raise ValueError(
                        "A prior final holdout has non-monotonic dates; new holdouts are blocked fail-closed"
                    )
                # Holdout endpoints are observations, so sharing either endpoint is reuse.
                overlaps = requested_start <= prior_end and requested_end >= prior_start
                if overlaps:
                    raise ValueError(
                        "Final holdout dates overlap data already reserved or consumed and cannot be reused"
                    )
                if requested_start <= prior_end:
                    raise ValueError(
                        "A new final holdout must be entirely later than every prior sealed holdout"
                    )
            try:
                cursor = self._connection.execute(
                    """INSERT INTO research_holdouts(
                        created_at,dataset_hash,development_hash,holdout_hash,
                        holdout_start,holdout_end,policy_version,provenance_hash,
                        development_quality_json,holdout_quality_json,status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,'RESERVED')""",
                    (
                        utc_now().isoformat(),
                        dataset_hash,
                        development_hash,
                        holdout_hash,
                        holdout_start,
                        holdout_end,
                        policy_version,
                        provenance_hash,
                        json.dumps(development_quality or {}, sort_keys=True, default=str),
                        json.dumps(holdout_quality or {}, sort_keys=True, default=str),
                    ),
                )
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError as exc:
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
        for stored_name, public_name in (
            ("development_quality_json", "development_quality"),
            ("holdout_quality_json", "holdout_quality"),
        ):
            try:
                value = json.loads(result.pop(stored_name))
            except (KeyError, TypeError, json.JSONDecodeError):
                value = {}
            result[public_name] = value if isinstance(value, dict) else {}
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
        provenance_hash: str = "",
        provenance: dict[str, Any] | None = None,
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
                    selected_fingerprint,policy_version,metrics_json,provenance_hash,
                    development_hash,development_quality_json,holdout_quality_json
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
                or holdout["provenance_hash"] != provenance_hash
                or holdout["holdout_end"] != replay_end
                or existing_promotion is not None
                or not _valid_quality_record(
                    holdout["development_quality_json"],
                    holdout["development_hash"],
                    minimum_sessions=120,
                )
                or not _valid_quality_record(
                    holdout["holdout_quality_json"],
                    holdout["holdout_hash"],
                    exact_sessions=20,
                )
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
            if not _valid_provenance_record(provenance_hash, provenance, dataset_hash):
                raise ValueError(
                    "Live-review eligibility requires manifest-bound observed-data provenance"
                )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO research_promotions(
                    created_at,dataset_hash,strategy_fingerprint,policy_version,status,
                    source,replay_end,gates_json,risk_envelope_json,holdout_id,
                    provenance_hash,provenance_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    provenance_hash,
                    json.dumps(provenance or {}, sort_keys=True, default=str),
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
                "provenance_hash": provenance_hash,
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
            provenance = json.loads(result.pop("provenance_json", "{}"))
        except (TypeError, json.JSONDecodeError):
            gates, risk_envelope, provenance = [], {}, {}
            result["decode_error"] = "Stored evidence JSON is invalid"
        result["gates"] = gates if isinstance(gates, list) else []
        result["risk_envelope"] = risk_envelope if isinstance(risk_envelope, dict) else {}
        result["provenance"] = provenance if isinstance(provenance, dict) else {}
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
                h.holdout_end AS sealed_holdout_end,h.metrics_json AS holdout_metrics_json,
                h.provenance_hash AS holdout_provenance_hash,
                h.development_hash AS sealed_development_hash,
                h.development_quality_json AS development_quality_json,
                h.holdout_quality_json AS holdout_quality_json
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
            provenance = json.loads(evidence["provenance_json"])
            development_quality = json.loads(evidence["development_quality_json"])
            holdout_quality = json.loads(evidence["holdout_quality_json"])
        except (KeyError, TypeError, json.JSONDecodeError):
            return None
        from grande_alpha.evidence import REQUIRED_LIVE_GATE_NAMES

        try:
            replay_end = _parse_aware_utc(evidence["replay_end"], field="replay_end")
            promotion_created_at = _parse_aware_utc(
                evidence["created_at"], field="promotion created_at"
            )
        except ValueError:
            return None
        reference = utc_now()
        earliest = reference - timedelta(days=max_age_days)
        if (
            replay_end > reference
            or replay_end < earliest
            or promotion_created_at > reference
            or promotion_created_at < earliest
        ):
            return None

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
            or evidence["provenance_hash"] != evidence["holdout_provenance_hash"]
            or not _valid_provenance_record(
                evidence["provenance_hash"], provenance, evidence["dataset_hash"]
            )
            or evidence["replay_end"] != evidence["sealed_holdout_end"]
            or not _valid_quality_record(
                development_quality,
                evidence["sealed_development_hash"],
                minimum_sessions=120,
            )
            or not _valid_quality_record(
                holdout_quality,
                evidence["sealed_holdout_hash"],
                exact_sessions=20,
            )
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
        evidence["provenance"] = provenance
        evidence["development_quality"] = development_quality
        evidence["holdout_quality"] = holdout_quality
        return evidence

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _valid_provenance_record(
    provenance_hash: str,
    provenance: dict[str, Any] | None,
    dataset_hash: str,
) -> bool:
    if not isinstance(provenance, dict):
        return False
    try:
        from grande_alpha.historical import DataProvenance

        allowed = DataProvenance.__dataclass_fields__.keys()
        record = DataProvenance(
            **{key: value for key, value in provenance.items() if key in allowed}
        )
    except (TypeError, ValueError):
        return False
    return bool(
        record.evidence_eligible
        and record.digest == provenance_hash
        and record.canonical_dataset_hash == dataset_hash
    )


def _valid_quality_record(
    raw_quality: Any,
    expected_hash: str,
    *,
    minimum_sessions: int | None = None,
    exact_sessions: int | None = None,
) -> bool:
    if isinstance(raw_quality, str):
        try:
            raw_quality = json.loads(raw_quality)
        except (TypeError, json.JSONDecodeError):
            return False
    if not isinstance(raw_quality, dict):
        return False
    integer_fields = (
        "aligned_bars",
        "sessions",
        "missing_intervals",
        "zero_volume_bars",
        "duplicate_timestamps",
        "invalid_session_bars",
        "expected_sessions",
        "missing_sessions",
        "complete_sessions",
    )
    if any(type(raw_quality.get(name)) is not int for name in integer_fields):
        return False
    aligned_bars = raw_quality["aligned_bars"]
    sessions = raw_quality["sessions"]
    missing = raw_quality["missing_intervals"]
    duplicates = raw_quality["duplicate_timestamps"]
    complete = raw_quality["complete_sessions"]
    raw_coverage = raw_quality.get("session_coverage_pct")
    if isinstance(raw_coverage, bool) or not isinstance(raw_coverage, (int, float)):
        return False
    coverage = float(raw_coverage)
    expected_coverage = complete / sessions * 100.0 if sessions > 0 else math.nan
    return bool(
        isinstance(raw_quality.get("interval"), str)
        and bool(raw_quality["interval"])
        and isinstance(raw_quality.get("dataset_hash"), str)
        and aligned_bars >= sessions > 0
        and sessions > 0
        and missing == 0
        and duplicates == 0
        and raw_quality["invalid_session_bars"] == 0
        and raw_quality["missing_sessions"] == 0
        and raw_quality["expected_sessions"] == sessions
        and raw_quality["zero_volume_bars"] >= 0
        and 0 <= complete <= sessions
        and math.isfinite(coverage)
        and math.isclose(coverage, expected_coverage, rel_tol=0.0, abs_tol=1e-9)
        and coverage >= 95.0
        and (minimum_sessions is None or sessions >= minimum_sessions)
        and (exact_sessions is None or sessions == exact_sessions)
        and raw_quality.get("dataset_hash") == expected_hash
    )


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
    "max_daily_notional",
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

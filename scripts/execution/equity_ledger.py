"""Isolated stock-capable intent/fill ledger; never migrates legacy ETF evidence."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from grande_alpha.domain.order_models import BrokerOrder
from grande_alpha.domain.policy import EASTERN
from grande_alpha.execution.equity_execution import EquityOrderIntent

EQUITY_SCHEMA = """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS equity_v1_intents (
                ref TEXT PRIMARY KEY, account TEXT NOT NULL, authority TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL, order_id TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS equity_v1_one_pending ON equity_v1_intents(account)
                WHERE state IN ('reserved','submitting','unknown','open');
            CREATE UNIQUE INDEX IF NOT EXISTS equity_v1_order_identity ON equity_v1_intents(account,order_id)
                WHERE order_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS equity_v1_fills (
                account TEXT NOT NULL, execution_id TEXT NOT NULL, ref TEXT NOT NULL REFERENCES equity_v1_intents(ref),
                symbol TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL,
                fee REAL NOT NULL, executed_at TEXT NOT NULL,
                PRIMARY KEY(account,execution_id)
            );
            CREATE TABLE IF NOT EXISTS equity_v1_dispatch (
                ref TEXT PRIMARY KEY REFERENCES equity_v1_intents(ref),
                submitted_at TEXT NOT NULL, notional REAL NOT NULL CHECK(notional > 0)
            );
            CREATE TABLE IF NOT EXISTS equity_v1_engine_lease (
                account TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL
            );
        """


class ExecutionLeaseBusy(RuntimeError):
    """Another owner retains the account lease until its durable expiry."""


class EquityLedger:
    def __init__(self, path: Path, *, legacy_path: Path | None = None):
        from grande_alpha.persistence.transactions import Transactions

        self._owns_connection = not hasattr(path, "_transactions")
        if self._owns_connection:
            self._lock = threading.RLock()
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._transactions = Transactions(self._db, self._lock)
        else:
            self._lock = path._lock
            self._db = path._connection
            self._transactions = path._transactions
            from grande_alpha.persistence.upgrade import require_upgraded_legacy

            old_path = legacy_path or (path.path.parent / "equity_v1.db")
            if old_path.resolve() != path.path.resolve():
                require_upgraded_legacy(self._db, old_path)
        if self._db.in_transaction:
            raise RuntimeError("Initialize the equity repository before starting a transaction")
        self._db.executescript(EQUITY_SCHEMA)

    def close(self):
        if self._owns_connection:
            self._db.close()

    def transaction(self):
        return self._transactions.transaction()

    def acquire_lease(self, account: str, owner: str, *, now: datetime, ttl_seconds: float = 30) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (account, owner)):
            raise ValueError("Lease requires exact account and owner")
        if now.tzinfo is None or now.utcoffset() is None or not 5 <= ttl_seconds <= 300:
            raise ValueError("Lease requires an aware time and a 5-300 second TTL")
        expires = now.astimezone(UTC) + timedelta(seconds=ttl_seconds)
        with self.transaction():
            row = self._db.execute(
                "SELECT owner,expires_at FROM equity_v1_engine_lease WHERE account=?", (account,)
            ).fetchone()
            if (
                row is not None
                and row["owner"] != owner
                and datetime.fromisoformat(row["expires_at"]) > now.astimezone(UTC)
            ):
                raise ExecutionLeaseBusy("Another mixed engine holds the account execution lease")
            self._db.execute(
                "INSERT INTO equity_v1_engine_lease(account,owner,expires_at) VALUES(?,?,?) "
                "ON CONFLICT(account) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at",
                (account, owner, expires.isoformat()),
            )

    def renew_lease(self, account: str, owner: str, *, now: datetime, ttl_seconds: float = 30) -> None:
        if now.tzinfo is None or now.utcoffset() is None or not 5 <= ttl_seconds <= 300:
            raise ValueError("Lease requires an aware time and a 5-300 second TTL")
        expires = now.astimezone(UTC) + timedelta(seconds=ttl_seconds)
        with self.transaction():
            cursor = self._db.execute(
                "UPDATE equity_v1_engine_lease SET expires_at=? WHERE account=? AND owner=? AND expires_at>?",
                (expires.isoformat(), account, owner, now.astimezone(UTC).isoformat()),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Mixed engine execution lease is missing, expired, or replaced")

    def lease_active(self, account: str, owner: str, *, now: datetime) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Lease check time must be aware")
        with self._lock:
            row = self._db.execute(
                "SELECT owner,expires_at FROM equity_v1_engine_lease WHERE account=?", (account,)
            ).fetchone()
        return bool(
            row is not None
            and row["owner"] == owner
            and datetime.fromisoformat(row["expires_at"]) > now.astimezone(UTC)
        )

    def release_lease(self, account: str, owner: str) -> None:
        with self.transaction():
            self._db.execute(
                "DELETE FROM equity_v1_engine_lease WHERE account=? AND owner=?", (account, owner)
            )

    def reserve(self, account: str, authority: str, intent: EquityOrderIntent):
        intent.validate()
        if not all(isinstance(s, str) and s.strip() for s in (account, authority)):
            raise ValueError("Exact account and authority are required")
        with self.transaction():
            self._db.execute(
                "INSERT INTO equity_v1_intents VALUES(?,?,?,?,?,NULL)",
                (intent.ref_id, account, authority, json.dumps(intent.as_dict(), sort_keys=True), "reserved"),
            )

    def mark_submitting(
        self, ref: str, *, submitted_at: datetime | None = None, notional: float | None = None
    ):
        if (submitted_at is None) != (notional is None):
            raise ValueError("Dispatch time and notional must be supplied together")
        if submitted_at is not None:
            if (
                submitted_at.tzinfo is None
                or submitted_at.utcoffset() is None
                or isinstance(notional, bool)
                or not isinstance(notional, (int, float))
                or not math.isfinite(notional)
                or notional <= 0
            ):
                raise ValueError("Invalid dispatch provenance")
        with self.transaction():
            cursor = self._db.execute(
                "UPDATE equity_v1_intents SET state='submitting' WHERE ref=? AND state='reserved'", (ref,)
            )
            if cursor.rowcount != 1:
                raise ValueError("Intent already attempted or missing; do not resubmit")
            if submitted_at is not None:
                self._db.execute(
                    "INSERT INTO equity_v1_dispatch VALUES(?,?,?)",
                    (ref, submitted_at.astimezone(UTC).isoformat(), notional),
                )

    def daily_usage(self, account: str, day: str) -> dict:
        requested = date.fromisoformat(day)
        with self._lock:
            rows = self._db.execute(
                "SELECT i.state,d.submitted_at,d.notional FROM equity_v1_intents i "
                "LEFT JOIN equity_v1_dispatch d ON i.ref=d.ref WHERE i.account=? "
                "AND i.state NOT IN ('reserved','abandoned')",
                (account,),
            ).fetchall()
        count, amount = 0, 0.0
        for row in rows:
            if row["submitted_at"] is None:
                raise ValueError("Attempted stock order lacks durable usage provenance")
            when = datetime.fromisoformat(row["submitted_at"])
            if when.tzinfo is None or not math.isfinite(row["notional"]) or row["notional"] <= 0:
                raise ValueError("Invalid durable stock usage")
            if when.astimezone(EASTERN).date() == requested:
                count += 1
                amount += row["notional"]
        return {"orders": count, "notional": amount}

    def orders_since(self, account: str, since: datetime) -> int:
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("Order-rate boundary must be timezone-aware")
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM equity_v1_dispatch d JOIN equity_v1_intents i ON i.ref=d.ref "
                "WHERE i.account=? AND d.submitted_at>=?",
                (account, since.astimezone(UTC).isoformat()),
            ).fetchone()
        return int(row["count"])

    def holding_open_times(self, account: str) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT symbol,side,quantity,executed_at FROM equity_v1_fills "
                "WHERE account=? ORDER BY executed_at,execution_id",
                (account,),
            ).fetchall()
        quantities, opened = {}, {}
        for row in rows:
            symbol = row["symbol"]
            before = quantities.get(symbol, 0)
            after = before + row["quantity"] * (1 if row["side"] == "buy" else -1)
            if after < -1e-8:
                raise ValueError("Execution history implies short inventory")
            if before <= 1e-8 and after > 1e-8:
                opened[symbol] = row["executed_at"]
            elif after <= 1e-8:
                opened.pop(symbol, None)
            quantities[symbol] = max(0, after)
        return opened

    def mark_unknown(self, ref: str):
        with self.transaction():
            cursor = self._db.execute(
                "UPDATE equity_v1_intents SET state='unknown' WHERE ref=? AND state='submitting'", (ref,)
            )
            if cursor.rowcount != 1:
                raise ValueError("Only an attempted order can become unknown")

    def abandon_reserved(self, ref: str):
        """Release only a ticket proven never to have crossed the dispatch boundary."""
        with self.transaction():
            cursor = self._db.execute(
                "UPDATE equity_v1_intents SET state='abandoned' WHERE ref=? AND state='reserved'", (ref,)
            )
            if cursor.rowcount != 1:
                raise ValueError("Attempted orders cannot be abandoned or reset")

    def unresolved(self, account: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ref,state,order_id FROM equity_v1_intents WHERE account=? "
                "AND state IN ('reserved','submitting','unknown','open')",
                (account,),
            ).fetchall()
        return [dict(row) for row in rows]

    def observe(self, account: str, ref: str, order: BrokerOrder, *, observed_at: datetime):
        from grande_alpha.broker.base import order_is_terminal

        order.validate_execution_provenance(require_snapshot=True, observed_at=observed_at)
        if not isinstance(order.order_id, str) or not order.order_id.strip():
            raise ValueError("Provider order id is missing")
        with self.transaction():
            row = self._db.execute(
                "SELECT * FROM equity_v1_intents WHERE ref=? AND account=?", (ref, account)
            ).fetchone()
            if row is None or row["state"] in {"reserved", "abandoned"}:
                raise ValueError("Observed order has no attempted account-bound intent")
            intent = json.loads(row["payload"])
            if order.created_at is None or order.created_at < datetime.fromisoformat(
                intent["created_at"]
            ) - timedelta(seconds=5):
                raise ValueError("Provider order predates the recorded ticket")
            if row["order_id"] is None and order.raw.get("ref_id") != ref:
                raise ValueError(
                    "Unbound order requires exact provider reference; similarity is not identity"
                )
            if row["order_id"] is not None and row["order_id"] != order.order_id:
                raise ValueError("Provider order identity changed")
            if order.raw.get("ref_id", ref) != ref or (order.symbol, order.side) != (
                intent["symbol"],
                intent["side"],
            ):
                raise ValueError("Provider order does not match the recorded ticket")
            for raw_key, intent_key in (
                ("type", "order_type"),
                ("time_in_force", "time_in_force"),
                ("market_hours", "market_hours"),
            ):
                if order.raw.get(raw_key) != intent[intent_key]:
                    raise ValueError("Provider route does not match the recorded ticket")
            for name in ("quantity", "dollar_amount"):
                expected = intent[name]
                actual = getattr(order, name)
                if expected is not None and (
                    actual is None or not math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-8)
                ):
                    raise ValueError("Provider amount does not match recorded ticket")
            existing = self._db.execute(
                "SELECT execution_id FROM equity_v1_fills WHERE ref=?", (ref,)
            ).fetchall()
            if not {r["execution_id"] for r in existing} <= {e.execution_id for e in order.executions}:
                raise ValueError("Provider snapshot dropped previously observed fills")
            for execution in order.executions:
                values = (
                    account,
                    execution.execution_id,
                    ref,
                    order.symbol,
                    order.side,
                    execution.quantity,
                    execution.price,
                    execution.fees,
                    execution.timestamp.astimezone(UTC).isoformat(),
                )
                previous = self._db.execute(
                    "SELECT * FROM equity_v1_fills WHERE account=? AND execution_id=?",
                    (account, execution.execution_id),
                ).fetchone()
                if previous is not None:
                    if tuple(previous) != values:
                        raise ValueError("Provider changed an immutable execution identity")
                else:
                    self._db.execute("INSERT INTO equity_v1_fills VALUES(?,?,?,?,?,?,?,?,?)", values)
            terminal = order_is_terminal(order)
            if row["state"] == "terminal" and not terminal:
                raise ValueError("Provider terminal state regressed")
            self._db.execute(
                "UPDATE equity_v1_intents SET order_id=?,state=? WHERE ref=?",
                (order.order_id, "terminal" if terminal else "open", ref),
            )

    def inventory(self, account: str) -> dict[str, float]:
        with self._lock:
            rows = self._db.execute(
                "SELECT symbol,side,quantity FROM equity_v1_fills WHERE account=? "
                "ORDER BY executed_at,execution_id",
                (account,),
            ).fetchall()
        totals = {}
        for row in rows:
            symbol = row["symbol"]
            total = totals.get(symbol, 0) + row["quantity"] * (1 if row["side"] == "buy" else -1)
            if total < -1e-8:
                raise ValueError("Execution history implies short inventory")
            totals[symbol] = max(0, total)
        return totals

    def mark_to_market_pnl(self, account: str, quotes: dict) -> dict[str, float]:
        """Value app-owned fills at executable bids, independent of account cash flows."""
        with self._lock:
            fills = self._db.execute(
                "SELECT symbol,side,quantity,price,fee FROM equity_v1_fills "
                "WHERE account=? ORDER BY executed_at,execution_id",
                (account,),
            ).fetchall()
        quantity: dict[str, float] = {}
        cost: dict[str, float] = {}
        realized = 0.0
        for fill in fills:
            symbol = fill["symbol"]
            amount = float(fill["quantity"])
            current_quantity = quantity.get(symbol, 0.0)
            current_cost = cost.get(symbol, 0.0)
            if fill["side"] == "buy":
                quantity[symbol] = current_quantity + amount
                cost[symbol] = current_cost + amount * fill["price"] + fill["fee"]
                continue
            if fill["side"] != "sell" or amount > current_quantity + 1e-8:
                raise ValueError("Execution history is not a long-only buy/sell ledger")
            average_cost = current_cost / current_quantity
            realized += amount * fill["price"] - fill["fee"] - amount * average_cost
            quantity[symbol] = max(0.0, current_quantity - amount)
            cost[symbol] = max(0.0, current_cost - amount * average_cost)
        unrealized = 0.0
        for symbol, amount in quantity.items():
            if amount <= 1e-8:
                continue
            quote = quotes.get(symbol)
            if quote is None:
                raise ValueError(f"Missing exact mark for held {symbol}")
            quote.validate()
            unrealized += amount * quote.bid - cost[symbol]
        if not math.isfinite(realized) or not math.isfinite(unrealized):
            raise ValueError("Execution performance is not finite")
        return {"realized_usd": realized, "unrealized_usd": unrealized, "total_usd": realized + unrealized}

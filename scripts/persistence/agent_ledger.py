"""Durable, account-wide capital reservations for stock and crypto execution.

Budget configuration is not trading authority. Dispatch still requires a separate
runtime authorization decision; reopening this journal never sends an order.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from grande_alpha.domain.crypto_models import decimal_amount
from grande_alpha.domain.models import utc_now

EASTERN = ZoneInfo("America/New_York")
ZERO = Decimal(0)
ACTIVE = ("reserved", "dispatching", "open", "unresolved")


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def stamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Execution timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def amount(value) -> Decimal:
    return decimal_amount(value, "ledger amount")


@dataclass(frozen=True)
class AgentBudget:
    max_order_cash: Decimal = ZERO
    max_committed_cash: Decimal = ZERO
    max_daily_buy_cash: Decimal = ZERO
    max_realized_loss: Decimal = ZERO

    def validate(self) -> None:
        for value in asdict(self).values():
            if not isinstance(value, Decimal) or amount(value) > Decimal("1000000") or value != value.quantize(Decimal("0.01")):
                raise ValueError("Budget amounts must be exact nonnegative dollar amounts with at most two decimals")
        if self.max_order_cash > min(self.max_committed_cash, self.max_daily_buy_cash):
            raise ValueError("Per-order budget cannot exceed total or daily buy budget")


@dataclass(frozen=True)
class ExecutionTicket:
    ref_id: str
    account_number: str
    market: str
    symbol: str
    provider_id: str
    side: str
    quantity: Decimal | None
    reserved_cash: Decimal
    scope_id: str
    strategy_fingerprint: str
    intent_json: str
    broker_account_id: str = ""
    rhs_account_number: str = ""
    rhc_account_number: str = ""

    @property
    def key(self) -> str:
        return f"{self.market}:{self.symbol}"

    def validate(self) -> None:
        if str(uuid.UUID(self.ref_id)) != self.ref_id:
            raise ValueError("Execution reference must be a canonical UUID")
        if self.market not in {"equity", "crypto"} or self.side not in {"buy", "sell"}:
            raise ValueError("Unsupported execution market or side")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", self.symbol):
            raise ValueError("Invalid execution symbol")
        for text in (self.account_number, self.scope_id, self.strategy_fingerprint):
            if not isinstance(text, str) or not text.strip() or text != text.strip():
                raise ValueError("Execution requires exact account, authority scope, and strategy identity")
        if not re.fullmatch(r"[0-9a-f]{64}", self.strategy_fingerprint):
            raise ValueError("Strategy identity must be a SHA-256 fingerprint")
        if not isinstance(self.reserved_cash, Decimal) or amount(self.reserved_cash) <= 0:
            raise ValueError("Execution requires a positive cash reservation or sell-value bound")
        if self.quantity is not None and (not isinstance(self.quantity, Decimal) or amount(self.quantity) <= 0):
            raise ValueError("Execution quantity must be a positive Decimal")
        if self.side == "sell" and self.quantity is None:
            raise ValueError("Managed sells require an exact asset quantity")
        if self.market == "crypto" and (
            not self.symbol.endswith("-USD") or not self.provider_id or not self.broker_account_id
            or not self.rhc_account_number or not self.rhs_account_number.isascii()
            or not self.rhs_account_number.isdigit()
        ):
            raise ValueError("Crypto execution requires exact pair and linked account identities")
        payload = json.loads(self.intent_json)
        if not isinstance(payload, dict) or canonical(payload) != self.intent_json:
            raise ValueError("Execution intent must use canonical object JSON")


@dataclass(frozen=True)
class ExecutionObservation:
    ref_id: str
    order_id: str
    key: str
    side: str
    provider_id: str
    broker_account_id: str
    cumulative_quantity: Decimal
    net_cash: Decimal
    terminal: bool
    state: str
    executions: tuple[dict, ...]


@dataclass(frozen=True)
class BudgetStatus:
    limits: AgentBudget
    committed_cash: Decimal
    daily_buy_cash: Decimal
    realized_pnl: Decimal
    realized_losses: Decimal
    pending_orders: int
    unresolved_orders: int
    has_inventory: bool
    block_reason: str


class AgentLedger:
    """Shares AuditStore's connection/lock; BEGIN IMMEDIATE also serializes processes."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self._db, self._lock = connection, lock
        with lock, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS agent_budgets (
                    account TEXT PRIMARY KEY, limits_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_tickets (
                    ref_id TEXT PRIMARY KEY, account TEXT NOT NULL, asset_key TEXT NOT NULL,
                    market TEXT NOT NULL CHECK(market IN ('equity','crypto')),
                    side TEXT NOT NULL CHECK(side IN ('buy','sell')), ticket_json TEXT NOT NULL,
                    ticket_digest TEXT NOT NULL, created_at TEXT NOT NULL, submission_started_at TEXT,
                    dispatch_day TEXT, state TEXT NOT NULL CHECK(state IN
                      ('reserved','dispatching','open','unresolved','terminal','released')),
                    broker_order_id TEXT, broker_state TEXT NOT NULL DEFAULT '',
                    cumulative_quantity TEXT NOT NULL DEFAULT '0', net_cash TEXT NOT NULL DEFAULT '0',
                    UNIQUE(account,market,broker_order_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_account_state ON agent_tickets(account,state);
                CREATE TABLE IF NOT EXISTS agent_inventory (
                    account TEXT NOT NULL, asset_key TEXT NOT NULL, quantity TEXT NOT NULL,
                    cost TEXT NOT NULL, PRIMARY KEY(account,asset_key)
                );
                CREATE TABLE IF NOT EXISTS agent_account_totals (
                    account TEXT PRIMARY KEY, realized_pnl TEXT NOT NULL DEFAULT '0',
                    realized_losses TEXT NOT NULL DEFAULT '0', breach TEXT NOT NULL DEFAULT '',
                    recovery_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS agent_executions (
                    account TEXT NOT NULL, market TEXT NOT NULL, execution_id TEXT NOT NULL,
                    ref_id TEXT NOT NULL REFERENCES agent_tickets(ref_id), payload_json TEXT NOT NULL,
                    PRIMARY KEY(account,market,execution_id)
                );
                CREATE TABLE IF NOT EXISTS agent_execution_events (
                    id INTEGER PRIMARY KEY, account TEXT NOT NULL, ref_id TEXT,
                    recorded_at TEXT NOT NULL, event TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_cancellations (
                    ref_id TEXT PRIMARY KEY REFERENCES agent_tickets(ref_id),
                    started_at TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
                      ('dispatching','awaiting_terminal','unknown','terminal')),
                    accepted INTEGER, updated_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _transaction(self):
        with self._lock, localcontext() as context:
            context.prec = 60
            if self._db.in_transaction:
                raise RuntimeError("Agent journal cannot nest an uncommitted transaction")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def _event(self, account, ref_id, event, payload, now):
        self._db.execute("INSERT INTO agent_execution_events(account,ref_id,recorded_at,event,payload_json) VALUES(?,?,?,?,?)",
                         (account, ref_id, stamp(now), event, canonical(payload)))

    @staticmethod
    def _account(account):
        if not isinstance(account, str) or not account.strip() or account != account.strip():
            raise ValueError("An exact account identifier is required")

    def save_budget(self, account: str, limits: AgentBudget, *, now: datetime | None = None) -> None:
        self._account(account)
        limits.validate()
        now = now or utc_now()
        with self._transaction():
            self._db.execute("INSERT INTO agent_budgets VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET limits_json=excluded.limits_json,updated_at=excluded.updated_at",
                             (account, canonical(asdict(limits)), stamp(now)))
            self._event(account, None, "budget_saved_no_authority", asdict(limits), now)

    def _budget(self, account) -> AgentBudget:
        row = self._db.execute("SELECT limits_json FROM agent_budgets WHERE account=?", (account,)).fetchone()
        budget = AgentBudget(**{k: Decimal(v) for k, v in json.loads(row[0]).items()}) if row else AgentBudget()
        budget.validate()
        return budget

    @staticmethod
    def _ticket(row) -> ExecutionTicket:
        payload = str(row["ticket_json"])
        if hashlib.sha256(payload.encode()).hexdigest() != row["ticket_digest"]:
            raise ValueError("Durable execution ticket changed; recovery is blocked")
        values = json.loads(payload)
        values["quantity"] = amount(values["quantity"]) if values["quantity"] is not None else None
        values["reserved_cash"] = amount(values["reserved_cash"])
        ticket = ExecutionTicket(**values)
        ticket.validate()
        if (ticket.ref_id, ticket.account_number, ticket.key, ticket.market, ticket.side) != (
            row["ref_id"], row["account"], row["asset_key"], row["market"], row["side"]
        ):
            raise ValueError("Durable execution identity disagrees with its ticket")
        return ticket

    def records(self, account: str) -> list[dict]:
        self._account(account)
        with self._lock:
            rows = self._db.execute("SELECT * FROM agent_tickets WHERE account=? ORDER BY created_at,ref_id", (account,)).fetchall()
            return [{**dict(row), "ticket": self._ticket(row)} for row in rows]

    def inventory(self, account: str) -> dict[str, tuple[Decimal, Decimal]]:
        self._account(account)
        with self._lock:
            return {r["asset_key"]: (amount(r["quantity"]), amount(r["cost"])) for r in
                    self._db.execute("SELECT * FROM agent_inventory WHERE account=?", (account,))}

    def _status(self, account, now) -> BudgetStatus:
        records = self.records(account)
        day = now.astimezone(EASTERN).date().isoformat()
        committed = sum((cost for _, cost in self.inventory(account).values()), ZERO)
        daily = ZERO
        for r in records:
            ticket = r["ticket"]
            if ticket.side != "buy":
                continue
            cash = amount(r["net_cash"])
            if r["state"] in ACTIVE:
                committed += max(ZERO, ticket.reserved_cash - cash)
            if r["submission_started_at"] is not None and r["dispatch_day"] == day:
                daily += max(ticket.reserved_cash, cash)
            elif r["state"] == "reserved":
                # Old unsubmitted reservations also encumber today's budget.
                daily += ticket.reserved_cash
        row = self._db.execute("SELECT * FROM agent_account_totals WHERE account=?", (account,)).fetchone()
        pnl = Decimal(row["realized_pnl"]) if row else ZERO
        if not pnl.is_finite():
            raise ValueError("Invalid persisted realized P&L")
        losses = amount(row["realized_losses"]) if row else ZERO
        unresolved = sum(r["state"] in {"dispatching", "unresolved"} for r in records)
        reason = str(row["breach"] or row["recovery_error"]) if row else ""
        if unresolved:
            reason = reason or "Broker outcomes remain unresolved"
        limits = self._budget(account)
        if losses >= limits.max_realized_loss and losses > 0:
            reason = reason or "Cumulative realized loss budget reached"
        if min(asdict(limits).values()) <= 0:
            reason = reason or "Trading budget is not configured"
        return BudgetStatus(limits, committed, daily, pnl, losses,
                            sum(r["state"] in ACTIVE for r in records), unresolved,
                            any(q > 0 for q, _ in self.inventory(account).values()), reason)

    def status(self, account: str, *, now: datetime | None = None) -> BudgetStatus:
        self._account(account)
        now = now or utc_now()
        stamp(now)
        with self._transaction():
            return self._status(account, now)

    def _check_budget(self, ticket, now, *, reserved=False):
        state = self._status(ticket.account_number, now)
        if ticket.side == "sell":
            # Exits keep working when entry budgets are exhausted. Unresolved
            # positions/orders still block the instrument's next action below.
            return
        if state.block_reason:
            raise ValueError(state.block_reason)
        extra = ZERO if reserved else ticket.reserved_cash
        limits = state.limits
        if ticket.reserved_cash > limits.max_order_cash:
            raise ValueError("Order exceeds its cash budget")
        if state.committed_cash + extra > limits.max_committed_cash:
            raise ValueError("Combined stock/crypto committed cash exceeds the budget")
        if state.daily_buy_cash + extra > limits.max_daily_buy_cash:
            raise ValueError("Daily stock/crypto buy budget exceeded")

    def reserve(self, ticket: ExecutionTicket, *, available_cash: Decimal | None = None, now: datetime | None = None) -> None:
        ticket.validate()
        now = now or utc_now()
        stamp(now)
        payload = canonical(asdict(ticket))
        with self._transaction():
            if self._db.execute("SELECT 1 FROM agent_tickets WHERE ref_id=?", (ticket.ref_id,)).fetchone():
                raise ValueError("Logical execution reference already exists; it cannot be reused")
            active = [r for r in self.records(ticket.account_number) if r["state"] in ACTIVE and r["asset_key"] == ticket.key]
            if active:
                raise ValueError("An order already reserves this instrument; reconcile it first")
            self._check_budget(ticket, now)
            if ticket.side == "buy" and available_cash is not None:
                pending_cash = sum((max(ZERO, r["ticket"].reserved_cash - amount(r["net_cash"]))
                                    for r in self.records(ticket.account_number) if r["state"] in ACTIVE and r["side"] == "buy"), ZERO)
                if pending_cash + ticket.reserved_cash > amount(available_cash):
                    raise ValueError("Pending stock/crypto reservations exceed available broker cash")
            held = self.inventory(ticket.account_number).get(ticket.key, (ZERO, ZERO))[0]
            if ticket.side == "sell" and (ticket.quantity is None or ticket.quantity > held):
                raise ValueError("Sell exceeds the journal's verified managed inventory")
            self._db.execute("""INSERT INTO agent_tickets
                (ref_id,account,asset_key,market,side,ticket_json,ticket_digest,created_at,state)
                VALUES(?,?,?,?,?,?,?,?,'reserved')""",
                (ticket.ref_id, ticket.account_number, ticket.key, ticket.market, ticket.side, payload,
                 hashlib.sha256(payload.encode()).hexdigest(), stamp(now)))
            self._event(ticket.account_number, ticket.ref_id, "reserved", {"cash": ticket.reserved_cash, "key": ticket.key}, now)

    def claim_dispatch(self, ticket: ExecutionTicket, *, now: datetime | None = None) -> None:
        """Commit the irreversible dispatch boundary before invoking any broker write."""
        now = now or utc_now()
        stamp(now)
        with self._transaction():
            row = self._db.execute("SELECT * FROM agent_tickets WHERE ref_id=?", (ticket.ref_id,)).fetchone()
            if row is None or self._ticket(row) != ticket or row["state"] != "reserved" or row["submission_started_at"] is not None:
                raise ValueError("Execution is missing, changed, or already dispatched")
            age = (now - datetime.fromisoformat(row["created_at"])).total_seconds()
            if not 0 <= age <= 60:
                raise ValueError("Execution reservation expired; release it and obtain a fresh review")
            self._check_budget(ticket, now, reserved=True)
            self._db.execute("UPDATE agent_tickets SET state='dispatching',submission_started_at=?,dispatch_day=? WHERE ref_id=?",
                             (stamp(now), now.astimezone(EASTERN).date().isoformat(), ticket.ref_id))
            self._event(ticket.account_number, ticket.ref_id, "dispatch_started", {"retry_allowed": False}, now)

    def release(self, ref_id: str, *, now: datetime | None = None) -> None:
        with self._transaction():
            row = self._db.execute("SELECT * FROM agent_tickets WHERE ref_id=?", (ref_id,)).fetchone()
            if row is None or row["state"] != "reserved" or row["submission_started_at"] is not None:
                raise ValueError("Only a never-dispatched local reservation can be released")
            self._db.execute("UPDATE agent_tickets SET state='released' WHERE ref_id=?", (ref_id,))
            self._event(row["account"], ref_id, "local_reservation_released", {}, now or utc_now())

    def note_submission(self, ref_id: str, order_id: str | None, *, now: datetime | None = None) -> None:
        with self._transaction():
            row = self._db.execute("SELECT * FROM agent_tickets WHERE ref_id=?", (ref_id,)).fetchone()
            if row is None or row["submission_started_at"] is None or row["state"] not in ACTIVE:
                raise ValueError("Submission has no unresolved durable dispatch boundary")
            if order_id is not None and (not isinstance(order_id, str) or not order_id.strip()):
                raise ValueError("Broker order identity must be nonempty")
            if row["broker_order_id"] is not None and order_id not in (None, row["broker_order_id"]):
                raise ValueError("Broker order identity changed")
            self._db.execute("UPDATE agent_tickets SET state='unresolved',broker_order_id=COALESCE(broker_order_id,?) WHERE ref_id=?",
                             (order_id, ref_id))
            self._event(row["account"], ref_id, "awaiting_broker_reconciliation", {"order_id": order_id}, now or utc_now())

    def recovery_failed(self, account: str, reason: str, *, now: datetime | None = None) -> None:
        with self._transaction():
            self._db.execute("INSERT OR IGNORE INTO agent_account_totals(account) VALUES(?)", (account,))
            self._db.execute("UPDATE agent_account_totals SET recovery_error=? WHERE account=?", (reason, account))
            self._db.execute("UPDATE agent_tickets SET state='unresolved' WHERE account=? AND state IN ('dispatching','open','unresolved')", (account,))
            self._event(account, None, "recovery_blocked", {"reason": reason}, now or utc_now())

    def cancellation(self, ref_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM agent_cancellations WHERE ref_id=?", (ref_id,)).fetchone()
            return dict(row) if row else None

    def claim_cancellation(self, ticket: ExecutionTicket, order_id: str, *, now: datetime | None = None) -> None:
        """Record one cancellation attempt before a broker write; never retry it implicitly."""
        now = now or utc_now()
        with self._transaction():
            row = self._db.execute("SELECT * FROM agent_tickets WHERE ref_id=?", (ticket.ref_id,)).fetchone()
            if (row is None or self._ticket(row) != ticket or row["state"] != "open"
                    or row["submission_started_at"] is None or row["broker_order_id"] != order_id):
                raise ValueError("Cancellation requires an exact reconciled open managed order")
            if self.cancellation(ticket.ref_id) is not None:
                raise ValueError("Cancellation was already attempted; reconcile without resending")
            self._db.execute("INSERT INTO agent_cancellations VALUES(?,?,'dispatching',NULL,?)",
                             (ticket.ref_id, stamp(now), stamp(now)))
            self._event(ticket.account_number, ticket.ref_id, "cancellation_started", {"order_id": order_id}, now)

    def note_cancellation(self, ref_id: str, accepted: bool | None, *, now: datetime | None = None) -> None:
        if accepted is not None and type(accepted) is not bool:
            raise ValueError("Cancellation acknowledgement must be a boolean or unknown")
        now = now or utc_now()
        with self._transaction():
            row = self._db.execute("SELECT account FROM agent_tickets WHERE ref_id=?", (ref_id,)).fetchone()
            attempt = self.cancellation(ref_id)
            if row is None or attempt is None:
                raise ValueError("Cancellation has no durable dispatch boundary")
            # A parallel reconciliation may already have established terminality.
            if attempt["state"] != "terminal":
                self._db.execute("UPDATE agent_cancellations SET state=?,accepted=?,updated_at=? WHERE ref_id=?",
                                 ("unknown" if accepted is None else "awaiting_terminal", accepted, stamp(now), ref_id))
            self._event(row["account"], ref_id, "cancellation_response",
                        {"accepted": accepted, "releases_cash": False}, now)

    def reconcile(self, account: str, observations: list[ExecutionObservation], positions: dict[str, Decimal], *, now: datetime | None = None) -> None:
        """Atomically apply exact cumulative fills only when broker inventory agrees."""
        self._account(account)
        now = now or utc_now()
        for value in positions.values():
            amount(value)
        try:
            with self._transaction():
                records = self.records(account)
                by_ref = {o.ref_id: o for o in observations}
                if len(by_ref) != len(observations):
                    raise ValueError("Duplicate broker observations for a logical order")
                if by_ref.keys() - {r["ref_id"] for r in records if r["submission_started_at"] is not None}:
                    raise ValueError("Recovery cannot adopt orders without a durable dispatch")
                for row in records:
                    if row["submission_started_at"] is None:
                        continue
                    observation = by_ref.get(row["ref_id"])
                    if observation is None:
                        if row["state"] != "terminal":
                            self._db.execute("UPDATE agent_tickets SET state='unresolved' WHERE ref_id=?", (row["ref_id"],))
                        continue
                    self._apply(row, observation, now)
                inventory = self.inventory(account)
                managed_keys = {r["asset_key"] for r in records if r["submission_started_at"] is not None}
                for key in managed_keys:
                    if inventory.get(key, (ZERO, ZERO))[0] != positions.get(key, ZERO):
                        raise ValueError(f"Broker inventory disagrees with managed fills for {key}")
                self._db.execute("UPDATE agent_account_totals SET recovery_error='' WHERE account=?", (account,))
                self._event(account, None, "broker_reconciled", {"observations": len(observations)}, now)
        except Exception as exc:
            self.recovery_failed(account, str(exc), now=now)
            raise

    def _apply(self, row, observed: ExecutionObservation, now):
        ticket = self._ticket(row)
        terminal_states = {"filled", "canceled", "cancelled", "rejected", "failed", "voided"}
        if ticket.market == "equity":
            terminal_states |= {"expired", "partially_filled_rest_cancelled"}
        if type(observed.terminal) is not bool or not isinstance(observed.state, str) or not observed.state or observed.terminal != (observed.state in terminal_states):
            raise ValueError("Broker terminal status contradicts its order state")
        if (observed.key, observed.side, observed.provider_id, observed.broker_account_id) != (
            ticket.key, ticket.side, ticket.provider_id, ticket.broker_account_id
        ) or not isinstance(observed.order_id, str) or not observed.order_id.strip():
            raise ValueError("Broker recovery identity disagrees with the durable intent")
        if row["broker_order_id"] not in (None, observed.order_id):
            raise ValueError("Broker recovery changed an order ID")
        quantity, cash = amount(observed.cumulative_quantity), amount(observed.net_cash)
        old_qty, old_cash = amount(row["cumulative_quantity"]), amount(row["net_cash"])
        if quantity < old_qty or cash < old_cash or (quantity == old_qty and cash != old_cash):
            raise ValueError("Broker cumulative execution economics regressed or changed without a fill")
        if ticket.quantity is not None and quantity > ticket.quantity:
            raise ValueError("Broker filled more than the requested quantity")
        if observed.state == "filled" and (quantity <= 0 or (ticket.quantity is not None and quantity != ticket.quantity)):
            raise ValueError("Filled order is incomplete")
        if row["state"] == "terminal" and (not observed.terminal or quantity != old_qty or cash != old_cash or observed.state != row["broker_state"]):
            raise ValueError("Previously terminal execution changed")
        ids = set()
        filled = ZERO
        for execution in observed.executions:
            execution_id = execution.get("id")
            if not isinstance(execution_id, str) or not execution_id or execution_id in ids:
                raise ValueError("Invalid or duplicate immutable execution identity")
            ids.add(execution_id)
            qty = amount(execution.get("quantity"))
            executed_at = datetime.fromisoformat(str(execution.get("timestamp", "")))
            stamp(executed_at)
            submitted_at = datetime.fromisoformat(row["submission_started_at"])
            if executed_at < submitted_at - timedelta(seconds=2) or executed_at > now + timedelta(seconds=5):
                raise ValueError("Broker execution timestamp falls outside the dispatch/observation boundary")
            if qty <= 0:
                raise ValueError("Execution quantity must be positive")
            filled += qty
            payload = canonical(execution)
            existing = self._db.execute("SELECT ref_id,payload_json FROM agent_executions WHERE account=? AND market=? AND execution_id=?",
                                        (ticket.account_number, ticket.market, execution_id)).fetchone()
            if existing and (existing["ref_id"] != ticket.ref_id or existing["payload_json"] != payload):
                raise ValueError("Immutable broker execution changed or belongs to another order")
            self._db.execute("INSERT OR IGNORE INTO agent_executions VALUES(?,?,?,?,?)",
                             (ticket.account_number, ticket.market, execution_id, ticket.ref_id, payload))
        stored_ids = {r[0] for r in self._db.execute("SELECT execution_id FROM agent_executions WHERE ref_id=?", (ticket.ref_id,))}
        if ids != stored_ids or filled != quantity or (quantity == 0 and cash != 0):
            raise ValueError("Broker execution detail is incomplete")
        delta_qty, delta_cash = quantity - old_qty, cash - old_cash
        holding_qty, holding_cost = self.inventory(ticket.account_number).get(ticket.key, (ZERO, ZERO))
        realized = ZERO
        if ticket.side == "buy":
            holding_qty += delta_qty
            holding_cost += delta_cash
        elif delta_qty:
            if delta_qty > holding_qty:
                raise ValueError("Broker sell exceeds known managed inventory")
            basis = holding_cost if delta_qty == holding_qty else (holding_cost * delta_qty / holding_qty).quantize(Decimal("1e-18"))
            holding_qty -= delta_qty
            holding_cost -= basis
            realized = delta_cash - basis
        self._db.execute("INSERT INTO agent_inventory VALUES(?,?,?,?) ON CONFLICT(account,asset_key) DO UPDATE SET quantity=excluded.quantity,cost=excluded.cost",
                         (ticket.account_number, ticket.key, str(holding_qty), str(holding_cost)))
        self._db.execute("INSERT OR IGNORE INTO agent_account_totals(account) VALUES(?)", (ticket.account_number,))
        totals = self._db.execute("SELECT * FROM agent_account_totals WHERE account=?", (ticket.account_number,)).fetchone()
        breach = totals["breach"]
        if ticket.side == "buy" and cash > ticket.reserved_cash:
            breach = "Actual broker debit exceeded the reserved cash; review required"
        self._db.execute("UPDATE agent_account_totals SET realized_pnl=?,realized_losses=?,breach=? WHERE account=?",
                         (str(Decimal(totals["realized_pnl"]) + realized), str(amount(totals["realized_losses"]) + max(ZERO, -realized)), breach, ticket.account_number))
        self._db.execute("UPDATE agent_tickets SET broker_order_id=?,broker_state=?,state=?,cumulative_quantity=?,net_cash=? WHERE ref_id=?",
                         (observed.order_id, observed.state, "terminal" if observed.terminal else "open", str(quantity), str(cash), ticket.ref_id))
        if observed.terminal:
            self._db.execute("UPDATE agent_cancellations SET state='terminal',updated_at=? WHERE ref_id=?",
                             (stamp(now), ticket.ref_id))
        if delta_qty or row["broker_state"] != observed.state or row["state"] not in {"terminal", "open"}:
            self._event(ticket.account_number, ticket.ref_id, "execution_reconciled",
                        {"quantity": quantity, "net_cash": cash, "state": observed.state, "realized_delta": realized}, now)

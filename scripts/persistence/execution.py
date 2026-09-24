from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo

from grande_alpha.domain.loss_recovery import recovery_deadline
from grande_alpha.domain.models import BrokerOrder, OrderIntent
from grande_alpha.execution.candidate_execution import next_consecutive_losses

from .base import Repository
from .validation import (
    _parse_aware_utc,
)


class ExecutionRepository(Repository):
    def register_standing(self, authority_id: str, scope_digest: str) -> None:
        with self.transaction():
            self._connection.execute(
                "INSERT INTO standing_sessions(authority_id,scope_digest) VALUES(?,?)",
                (authority_id, scope_digest),
            )

    def standing_active(self, authority_id: str, scope_digest: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT stopped,scope_digest FROM standing_sessions WHERE authority_id=?",
                (authority_id,),
            ).fetchone()
        return row is not None and row["stopped"] == 0 and row["scope_digest"] == scope_digest

    def active_standing_for_scope(self, scope_digest: str) -> str | None:
        """Return the sole live authority for an exact scope.

        Recovery must never guess between grants. Multiple active rows therefore
        fail closed instead of silently selecting the newest one.
        """
        if not isinstance(scope_digest, str) or not scope_digest.strip():
            raise ValueError("Scope digest must be nonempty")
        with self._lock:
            rows = self._connection.execute(
                "SELECT authority_id FROM standing_sessions "
                "WHERE stopped=0 AND scope_digest=? ORDER BY authority_id",
                (scope_digest.strip(),),
            ).fetchall()
        if len(rows) > 1:
            raise RuntimeError("Multiple active standing authorities exist for the exact scope")
        return None if not rows else str(rows[0]["authority_id"])

    def stop_standing(self, authority_id: str | None = None) -> int:
        """Durable local revocation; deliberately no broker calls and no reset operation."""
        with self.transaction():
            cursor = self._connection.execute(
                "UPDATE standing_sessions SET stopped=1 WHERE stopped=0"
                + (" AND authority_id=?" if authority_id is not None else ""),
                (authority_id,) if authority_id is not None else (),
            )
            return cursor.rowcount

    def record_intent(self, intent: OrderIntent) -> None:
        with self.transaction():
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
        if not isinstance(ref_id, str) or not ref_id.strip():
            raise ValueError("Intent reference must be a nonempty string")
        if order_id is not None and (not isinstance(order_id, str) or not order_id.strip()):
            raise ValueError("Broker order id must be a nonempty string")
        with self.transaction():
            current = self._connection.execute(
                "SELECT account_number FROM order_intents WHERE ref_id=?",
                (ref_id.strip(),),
            ).fetchone()
            if current is None:
                raise ValueError("Order intent is missing")
            normalized_order_id = order_id.strip() if order_id is not None else None
            account_number = current["account_number"]
            if normalized_order_id is not None:
                if account_number is None or not str(account_number).strip():
                    raise ValueError("Broker order id cannot be bound before durable account provenance")
                duplicate = self._connection.execute(
                    """SELECT ref_id FROM order_intents
                    WHERE account_number=? AND broker_order_id=? AND ref_id!=? LIMIT 1""",
                    (str(account_number).strip(), normalized_order_id, ref_id.strip()),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError("Broker order id is already bound to another durable order intent")
            cursor = self._connection.execute(
                "UPDATE order_intents SET broker_order_id=?, broker_state=? WHERE ref_id=?",
                (normalized_order_id, state, ref_id.strip()),
            )
            if cursor.rowcount != 1:
                raise ValueError("Order intent is missing")

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
        with self.transaction():
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
                    self._store._now().isoformat(),
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

    def owned_broker_order_ids(self, account_number: str) -> frozenset[str]:
        """Return broker ids durably bound to GRANDE Alpha placement invocations."""

        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Account number must be a nonempty string")
        with self._lock:
            rows = self._connection.execute(
                """SELECT broker_order_id FROM order_intents
                WHERE account_number=? AND submission_started_at IS NOT NULL
                AND broker_order_id IS NOT NULL""",
                (account_number.strip(),),
            ).fetchall()
        return frozenset(str(row["broker_order_id"]) for row in rows)

    def owned_broker_order_refs(self, account_number: str) -> dict[str, str]:
        """Return exact broker-order to durable-intent ownership bindings."""

        return {
            order_id: str(binding["ref_id"])
            for order_id, binding in self.owned_broker_order_bindings(account_number).items()
        }

    def owned_broker_order_bindings(self, account_number: str) -> dict[str, dict[str, Any]]:
        """Return durable intent tickets keyed by their unique provider order id."""

        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Account number must be a nonempty string")
        with self._lock:
            rows = self._connection.execute(
                """SELECT broker_order_id,ref_id,payload_json,submission_started_at
                FROM order_intents
                WHERE account_number=? AND submission_started_at IS NOT NULL
                AND broker_order_id IS NOT NULL""",
                (account_number.strip(),),
            ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError) as exc:
                raise ValueError("Durable order intent payload is invalid JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("Durable order intent payload must be an object")
            result[str(row["broker_order_id"])] = {
                "ref_id": str(row["ref_id"]),
                "payload": payload,
                "submission_started_at": str(row["submission_started_at"]),
            }
        return result

    def intent_submission_started_at(self, ref_id: str) -> datetime | None:
        """Return the exact durable pre-network submission boundary for one intent."""

        if not isinstance(ref_id, str) or not ref_id.strip():
            raise ValueError("Intent reference must be a nonempty string")
        with self._lock:
            row = self._connection.execute(
                "SELECT submission_started_at FROM order_intents WHERE ref_id=?",
                (ref_id.strip(),),
            ).fetchone()
        if row is None or row["submission_started_at"] is None:
            return None
        return _parse_aware_utc(row["submission_started_at"], field="intent submission_started_at")

    def record_broker_order_executions(
        self,
        account_number: str,
        order: BrokerOrder,
    ) -> None:
        """Idempotently persist provider execution identities, rejecting mutation."""

        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Account number must be a nonempty string")
        order_id = order.order_id.strip()
        symbol = order.symbol.strip().upper()
        side = order.side.strip().lower()
        if not order_id:
            raise ValueError("Broker order id must be a nonempty string")
        if symbol not in {"TQQQ", "SQQQ"} or side not in {"buy", "sell"}:
            raise ValueError("Broker execution order identity is unsupported")
        order.validate_execution_provenance(require_snapshot=bool(order.executions))
        account = account_number.strip()
        with self.transaction():
            bound_intents = list(
                self._connection.execute(
                    """SELECT ref_id,symbol,side,payload_json,authorized_notional,
                submission_started_at,broker_order_id
                FROM order_intents WHERE account_number=? AND broker_order_id=?""",
                    (account, order_id),
                ).fetchall()
            )
            bind_by_reference = False
            provider_ref = order.raw.get("ref_id")
            if provider_ref is not None:
                if not isinstance(provider_ref, str) or not provider_ref.strip():
                    raise ValueError("Broker order reference must be a nonempty string")
                referenced = self._connection.execute(
                    """SELECT ref_id,symbol,side,payload_json,authorized_notional,
                    account_number,broker_order_id,submission_started_at
                    FROM order_intents WHERE ref_id=?""",
                    (provider_ref.strip(),),
                ).fetchone()
                if referenced is not None:
                    if str(referenced["account_number"] or "").strip() != account:
                        raise ValueError("Broker order reference differs from its durable account intent")
                    existing_order_id = referenced["broker_order_id"]
                    if existing_order_id is not None and str(existing_order_id) != order_id:
                        raise ValueError(
                            "Broker order reference differs from its durable broker-order binding"
                        )
                    if all(row["ref_id"] != referenced["ref_id"] for row in bound_intents):
                        bound_intents.append(referenced)
                        bind_by_reference = existing_order_id is None
            if len(bound_intents) > 1:
                raise ValueError("Broker order id is bound to multiple durable order intents")
            if bound_intents:
                bound = bound_intents[0]
                try:
                    payload = json.loads(bound["payload_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("Durable order intent payload is malformed") from exc
                if not isinstance(payload, dict):
                    raise ValueError("Durable order intent payload is malformed")
                if bound["submission_started_at"] is None:
                    raise ValueError("Durable order intent lacks submission chronology")
                submitted_at = _parse_aware_utc(
                    bound["submission_started_at"], field="intent submission_started_at"
                )
                if order.created_at is None:
                    raise ValueError("Broker order lacks creation chronology")
                if order.created_at.astimezone(UTC) < submitted_at - timedelta(seconds=5):
                    raise ValueError("Broker order predates its durable submission intent")
                if (
                    str(bound["symbol"]).strip().upper() != symbol
                    or str(bound["side"]).strip().lower() != side
                ):
                    raise ValueError("Broker order identity differs from its durable order intent")
                for payload_key, actual in (
                    ("symbol", symbol),
                    ("side", side),
                    ("order_type", str(order.raw.get("type", "")).strip().lower()),
                    ("market_hours", str(order.raw.get("market_hours", "")).strip().lower()),
                    ("time_in_force", str(order.raw.get("time_in_force", "")).strip().lower()),
                ):
                    expected = str(payload.get(payload_key, "")).strip().lower()
                    if not expected or str(actual).strip().lower() != expected:
                        raise ValueError(f"Broker order {payload_key} differs from its durable order intent")
                intended_quantity = payload.get("quantity")
                intended_dollars = payload.get("dollar_amount")
                intended_limit = payload.get("limit_price")
                if intended_quantity is not None:
                    if order.quantity is None or not math.isclose(
                        float(order.quantity),
                        float(intended_quantity),
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    ):
                        raise ValueError("Broker requested quantity differs from its durable order intent")
                elif intended_dollars is not None:
                    if order.dollar_amount is None or not math.isclose(
                        float(order.dollar_amount),
                        float(intended_dollars),
                        rel_tol=1e-9,
                        abs_tol=0.005,
                    ):
                        raise ValueError("Broker dollar amount differs from its durable order intent")
                if intended_limit is not None:
                    raw_price = order.raw.get("price")
                    if raw_price is None or not math.isclose(
                        float(raw_price),
                        float(intended_limit),
                        rel_tol=1e-9,
                        abs_tol=0.005,
                    ):
                        raise ValueError("Broker limit price differs from its durable order intent")
                authorized = float(bound["authorized_notional"])
                actual_notional = sum(
                    float(execution.quantity) * float(execution.price) + float(execution.fees)
                    for execution in order.executions
                )
                tolerance = max(0.05, authorized * 0.01)
                if (
                    not math.isfinite(authorized)
                    or authorized < 0
                    or actual_notional > authorized + tolerance
                ):
                    raise ValueError("Broker executions exceed the durable authorized notional")
                if bind_by_reference:
                    try:
                        cursor = self._connection.execute(
                            """UPDATE order_intents SET broker_order_id=?
                            WHERE ref_id=? AND account_number=? AND broker_order_id IS NULL""",
                            (order_id, str(bound["ref_id"]), account),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise ValueError(
                            "Broker order id is already bound to another durable order intent"
                        ) from exc
                    if cursor.rowcount != 1:
                        raise ValueError("Durable order intent changed before broker-order binding")
            existing_identity = self._connection.execute(
                """SELECT DISTINCT symbol,side FROM broker_executions
                WHERE account_number=? AND order_id=?""",
                (account, order_id),
            ).fetchall()
            if any(row["symbol"] != symbol or row["side"] != side for row in existing_identity):
                raise ValueError("Provider order identity changed symbol or side for an existing order id")
            for execution in order.executions:
                values = (
                    account,
                    execution.execution_id.strip(),
                    order_id,
                    symbol,
                    side,
                    float(execution.quantity),
                    float(execution.price),
                    float(execution.fees),
                    execution.timestamp.astimezone(UTC).isoformat(),
                )
                existing = self._connection.execute(
                    """SELECT account_number,execution_id,order_id,symbol,side,quantity,price,fees,executed_at
                    FROM broker_executions WHERE account_number=? AND execution_id=?""",
                    values[:2],
                ).fetchone()
                if existing is not None:
                    stored = tuple(existing[key] for key in existing.keys())
                    if stored != values:
                        raise ValueError(
                            "Provider execution identity was reused with conflicting immutable data"
                        )
                    continue
                self._connection.execute(
                    """INSERT INTO broker_executions(
                    recorded_at,account_number,execution_id,order_id,symbol,side,
                    quantity,price,fees,executed_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (self._store._now().isoformat(), *values),
                )

    def broker_executions(
        self,
        account_number: str,
        *,
        order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Account number must be a nonempty string")
        query = "SELECT * FROM broker_executions WHERE account_number=?"
        arguments: list[Any] = [account_number.strip()]
        if order_id is not None:
            if not isinstance(order_id, str) or not order_id.strip():
                raise ValueError("Order id must be a nonempty string")
            query += " AND order_id=?"
            arguments.append(order_id.strip())
        query += " ORDER BY executed_at,execution_id"
        with self._lock:
            rows = self._connection.execute(query, arguments).fetchall()
        return [dict(row) for row in rows]

    def live_loss_streak(self, account_number: str, et_date: str) -> dict[str, int]:
        """Rebuild fee-inclusive sell-fill losses from immutable provider executions.

        Match replay's per-realized-fill counting and proportional entry-cost
        allocation. The session peak latches the entry pause even if a later exit
        makes a profit. Re-reading the ledger cannot count an execution twice,
        and restarting or reauthorizing cannot reset today's loss history.
        """
        try:
            requested_date = date.fromisoformat(et_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("Trading date must use YYYY-MM-DD") from exc
        eastern = ZoneInfo("America/New_York")
        inventory: dict[str, tuple[float, float]] = {}
        consecutive = peak = 0
        for row in self.broker_executions(account_number):
            executed_at = _parse_aware_utc(row["executed_at"], field="execution timestamp")
            execution_date = executed_at.astimezone(eastern).date()
            if execution_date > requested_date:
                continue
            symbol = str(row["symbol"])
            quantity, price, fees = (float(row[key]) for key in ("quantity", "price", "fees"))
            if (
                symbol not in {"TQQQ", "SQQQ"}
                or not all(math.isfinite(value) for value in (quantity, price, fees))
                or quantity <= 0
                or price <= 0
                or fees < 0
            ):
                raise ValueError("Invalid provider execution in loss history")
            held_quantity, entry_cost = inventory.get(symbol, (0.0, 0.0))
            if row["side"] == "buy":
                next_quantity = held_quantity + quantity
                next_cost = entry_cost + quantity * price + fees
                if not math.isfinite(next_quantity) or not math.isfinite(next_cost):
                    raise ValueError("Nonfinite entry-cost history")
                inventory[symbol] = (next_quantity, next_cost)
                continue
            if row["side"] != "sell" or held_quantity <= 0 or quantity > held_quantity + 1e-7:
                raise ValueError("Sell execution lacks matching entry-cost history")
            cost_share = entry_cost * min(1.0, quantity / held_quantity)
            realized_pnl = quantity * price - fees - cost_share
            remaining = max(0.0, held_quantity - quantity)
            inventory[symbol] = (remaining, entry_cost - cost_share) if remaining > 1e-8 else (0.0, 0.0)
            if execution_date == requested_date:
                consecutive = next_consecutive_losses(consecutive, realized_pnl)
                peak = max(peak, consecutive)
        return {"consecutive_losses": consecutive, "peak_consecutive_losses": peak}

    def live_filled_entry_order_ids(
        self,
        account_number: str,
        et_date: str,
        *,
        strategy_fingerprint: str | None = None,
    ) -> frozenset[str]:
        """Return exact distinct buy-order identities with executions on one ET date."""

        try:
            requested_date = date.fromisoformat(et_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("Trading date must use YYYY-MM-DD") from exc
        rows = self.broker_executions(account_number)
        eligible_order_ids: set[str] | None = None
        if strategy_fingerprint is not None:
            if not isinstance(strategy_fingerprint, str) or not strategy_fingerprint.strip():
                raise ValueError("Strategy fingerprint must be a nonempty string")
            with self._lock:
                intents = self._connection.execute(
                    """SELECT broker_order_id FROM order_intents
                    WHERE account_number=? AND strategy_fingerprint=? AND side='buy'
                    AND broker_order_id IS NOT NULL""",
                    (account_number.strip(), strategy_fingerprint.strip()),
                ).fetchall()
            eligible_order_ids = {str(row["broker_order_id"]) for row in intents}
        eastern = ZoneInfo("America/New_York")
        order_ids: set[str] = set()
        for row in rows:
            executed_at = _parse_aware_utc(row["executed_at"], field="execution timestamp")
            if (
                row["side"] == "buy"
                and executed_at.astimezone(eastern).date() == requested_date
                and (eligible_order_ids is None or row["order_id"] in eligible_order_ids)
            ):
                order_ids.add(str(row["order_id"]))
        return frozenset(order_ids)

    def active_holding_start(
        self,
        account_number: str,
        symbol: str,
        current_quantity: float,
    ) -> datetime | None:
        """Derive the current long holding clock from exact provider executions."""

        normalized_symbol = symbol.strip().upper()
        if normalized_symbol not in {"TQQQ", "SQQQ"}:
            raise ValueError("Holding-time provenance supports only TQQQ and SQQQ")
        if (
            isinstance(current_quantity, bool)
            or not isinstance(current_quantity, Real)
            or not math.isfinite(float(current_quantity))
            or float(current_quantity) < 0
        ):
            raise ValueError("Current quantity must be finite and nonnegative")
        rows = [row for row in self.broker_executions(account_number) if row["symbol"] == normalized_symbol]
        quantity = 0.0
        holding_start: datetime | None = None
        for row in rows:
            delta = float(row["quantity"]) * (1.0 if row["side"] == "buy" else -1.0)
            previous = quantity
            quantity += delta
            if quantity < -1e-8:
                raise ValueError("Execution ledger implies a short position")
            quantity = max(0.0, quantity)
            if previous <= 1e-8 and quantity > 1e-8:
                holding_start = _parse_aware_utc(row["executed_at"], field="execution timestamp")
            if quantity <= 1e-8:
                holding_start = None
        if not math.isclose(quantity, float(current_quantity), rel_tol=1e-8, abs_tol=1e-7):
            raise ValueError("Execution ledger does not reconcile to current broker inventory")
        if quantity > 1e-8 and holding_start is None:
            raise ValueError("Execution ledger cannot prove the active holding start")
        return holding_start

    def validate_execution_inventory(
        self,
        account_number: str,
        positions: list[Any],
    ) -> None:
        """Require the durable execution ledger to equal broker leveraged inventory."""

        actual = {"TQQQ": 0.0, "SQQQ": 0.0}
        for position in positions:
            symbol = str(position.symbol).strip().upper()
            if symbol in actual:
                actual[symbol] += float(position.quantity)
        rows = self.broker_executions(account_number)
        ledger = {"TQQQ": 0.0, "SQQQ": 0.0}
        for row in rows:
            symbol = str(row["symbol"])
            delta = float(row["quantity"]) * (1.0 if row["side"] == "buy" else -1.0)
            ledger[symbol] += delta
            if ledger[symbol] < -1e-8:
                raise ValueError(f"Execution ledger implies a short {symbol} position")
            ledger[symbol] = max(0.0, ledger[symbol])
        for symbol in ("TQQQ", "SQQQ"):
            if not math.isclose(ledger[symbol], actual[symbol], rel_tol=1e-8, abs_tol=1e-7):
                raise ValueError(f"Durable {symbol} execution ledger does not match current broker inventory")

    def incomplete_execution_provenance(self, account_number: str, et_date: str) -> list[str]:
        """Return same-day submitted buy intents whose claimed fills lack executions."""

        try:
            requested_date = date.fromisoformat(et_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("Trading date must use YYYY-MM-DD") from exc
        eastern = ZoneInfo("America/New_York")
        with self._lock:
            rows = self._connection.execute(
                """SELECT ref_id,broker_order_id,broker_state,submission_started_at
                FROM order_intents WHERE account_number=? AND side='buy'
                AND submission_started_at IS NOT NULL""",
                (account_number.strip(),),
            ).fetchall()
        gaps: list[str] = []
        for row in rows:
            submitted_at = _parse_aware_utc(row["submission_started_at"], field="submission timestamp")
            if submitted_at.astimezone(eastern).date() != requested_date:
                continue
            state = str(row["broker_state"] or "").strip().lower()
            if state not in {"filled", "partially_filled"}:
                continue
            order_id = str(row["broker_order_id"] or "").strip()
            if not order_id or not self.broker_executions(account_number, order_id=order_id):
                gaps.append(str(row["ref_id"]))
        return gaps

    def _record_loss_pause(
        self,
        account_number: str,
        et_date: str,
        latched: bool,
        scope_digest: str | None,
        recovery_delay: int | None,
        recovery_unit: str,
        observed: datetime,
    ) -> bool:
        """Update the account pause inside the caller's open risk transaction."""
        if scope_digest is None:
            return False
        pause = self._connection.execute(
            "SELECT * FROM loss_recovery WHERE account_number=?", (account_number,)
        ).fetchone()
        if latched and (pause is None or (pause["cleared_at"] is not None and pause["loss_day"] != et_date)):
            eligible = recovery_deadline(observed, recovery_delay, recovery_unit)
            self._connection.execute(
                "INSERT INTO loss_recovery VALUES(?,?,?,?,?,NULL) ON CONFLICT(account_number) "
                "DO UPDATE SET scope_digest=excluded.scope_digest,loss_day=excluded.loss_day,"
                "paused_at=excluded.paused_at,eligible_at=excluded.eligible_at,cleared_at=NULL",
                (
                    account_number,
                    scope_digest,
                    et_date,
                    observed.astimezone(UTC).isoformat(),
                    eligible.isoformat() if eligible else None,
                ),
            )
            pause = self._connection.execute(
                "SELECT * FROM loss_recovery WHERE account_number=?", (account_number,)
            ).fetchone()
        if pause is None or pause["cleared_at"] is not None:
            return False
        deadline = (
            _parse_aware_utc(pause["eligible_at"], field="loss recovery deadline")
            if pause["eligible_at"]
            else None
        )
        if deadline is None or observed.astimezone(UTC) < deadline:
            return True
        self._connection.execute(
            "UPDATE loss_recovery SET cleared_at=? WHERE account_number=? AND cleared_at IS NULL",
            (observed.astimezone(UTC).isoformat(), account_number),
        )
        return False

    def record_daily_risk(
        self,
        account_number: str,
        et_date: str,
        value: float,
        loss_limit: float,
        *,
        require_existing: bool = False,
        carry_previous_observation: bool = False,
        scope_digest: str | None = None,
        recovery_delay: int | None = None,
        recovery_unit: str = "manual",
        observed_at: datetime | None = None,
    ) -> dict[str, float | bool]:
        """Persist an observed account/day high-water mark and irreversible daily stop.

        A new grant cannot raise the day's strictest limit or erase a prior breach.
        This tracks observed account value, not guaranteed realized P/L or unseen prices.
        """
        if not isinstance(account_number, str) or not account_number.strip():
            raise ValueError("Daily risk requires an account")
        if date.fromisoformat(et_date).isoformat() != et_date:
            raise ValueError("Daily risk requires an exact ISO date")
        for number, positive in ((value, False), (loss_limit, True)):
            if (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
                or number < 0
                or (positive and number == 0)
            ):
                raise ValueError(
                    "Daily risk values must be finite and nonnegative; the limit must be positive"
                )
        if scope_digest is not None and not scope_digest.strip():
            raise ValueError("Loss recovery requires an exact scope identity")
        observed = observed_at or self._store._now()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Risk observation time must be aware")
        recovery_blocked = False
        with self.transaction():
            row = self._connection.execute(
                "SELECT * FROM live_daily_risk WHERE account_number=? AND et_date=?",
                (account_number, et_date),
            ).fetchone()
            if row is None and require_existing:
                raise ValueError("Daily loss history is missing; new authority cannot reset prior daily risk")
            peak, limit, latched = float(value), float(loss_limit), False
            if row is None and carry_previous_observation:
                previous = self._connection.execute(
                    "SELECT last_value FROM live_daily_risk WHERE account_number=? AND et_date<? ORDER BY et_date DESC LIMIT 1",
                    (account_number, et_date),
                ).fetchone()
                if previous is not None:
                    prior = previous["last_value"]
                    if not isinstance(prior, (int, float)) or not math.isfinite(prior) or prior < 0:
                        raise ValueError("Previous risk observation is invalid")
                    peak = max(peak, prior)
            if row is not None:
                numbers = (row["peak_value"], row["last_value"], row["loss_limit"])
                if (
                    any(not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0 for n in numbers)
                    or row["loss_limit"] <= 0
                    or row["peak_value"] < row["last_value"]
                    or row["loss_latched"] not in (0, 1)
                ):
                    raise ValueError("Stored daily loss history is invalid")
                peak = max(peak, row["peak_value"])
                limit = min(limit, row["loss_limit"])
                latched = bool(row["loss_latched"])
            latched = latched or peak - value >= limit
            self._connection.execute(
                """INSERT INTO live_daily_risk VALUES(?,?,?,?,?,?)
                ON CONFLICT(account_number,et_date) DO UPDATE SET
                peak_value=excluded.peak_value,last_value=excluded.last_value,
                loss_limit=excluded.loss_limit,loss_latched=excluded.loss_latched""",
                (account_number, et_date, peak, value, limit, int(latched)),
            )
            recovery_blocked = self._record_loss_pause(
                account_number,
                et_date,
                latched,
                scope_digest,
                recovery_delay,
                recovery_unit,
                observed,
            )
        return {
            "peak_value": peak,
            "last_value": float(value),
            "loss_limit": limit,
            "loss_latched": latched,
            "recovery_blocked": recovery_blocked,
        }

    def record_mixed_daily_pnl(
        self,
        account_number: str,
        et_date: str,
        pnl: float,
        loss_limit: float,
        *,
        scope_digest: str,
        recovery_delay: int | None,
        recovery_unit: str,
        require_existing: bool = False,
        carry_previous_observation: bool = False,
        observed_at: datetime | None = None,
    ) -> dict[str, float | bool]:
        """Latch app-owned realized plus marked-unrealized losses, excluding outside cash flows."""
        if not account_number.strip() or not scope_digest.strip():
            raise ValueError("Mixed daily risk requires exact account and scope")
        if date.fromisoformat(et_date).isoformat() != et_date:
            raise ValueError("Mixed daily risk requires an exact Eastern date")
        if (
            isinstance(pnl, bool)
            or not isinstance(pnl, (int, float))
            or not math.isfinite(pnl)
            or isinstance(loss_limit, bool)
            or not isinstance(loss_limit, (int, float))
            or not math.isfinite(loss_limit)
            or loss_limit <= 0
        ):
            raise ValueError("Mixed P/L and loss limit must be finite; limit must be positive")
        observed = observed_at or self._store._now()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Mixed risk observation time must be aware")
        with self.transaction():
            row = self._connection.execute(
                "SELECT * FROM mixed_daily_pnl WHERE account_number=? AND et_date=?",
                (account_number, et_date),
            ).fetchone()
            if row is None and require_existing:
                raise ValueError("Mixed daily P/L history is missing; prior order risk cannot be reset")
            peak, limit, latched = float(pnl), float(loss_limit), False
            if row is None and carry_previous_observation:
                previous = self._connection.execute(
                    "SELECT last_pnl FROM mixed_daily_pnl WHERE account_number=? AND et_date<? "
                    "ORDER BY et_date DESC LIMIT 1",
                    (account_number, et_date),
                ).fetchone()
                if previous is not None:
                    prior = previous["last_pnl"]
                    if not isinstance(prior, (int, float)) or not math.isfinite(prior):
                        raise ValueError("Previous mixed P/L observation is invalid")
                    peak = max(peak, prior)
            if row is not None:
                if (
                    not all(
                        isinstance(row[key], (int, float)) and math.isfinite(row[key])
                        for key in ("peak_pnl", "last_pnl", "loss_limit")
                    )
                    or row["peak_pnl"] < row["last_pnl"]
                    or row["loss_limit"] <= 0
                    or row["loss_latched"] not in (0, 1)
                ):
                    raise ValueError("Stored mixed daily P/L is invalid")
                peak = max(peak, row["peak_pnl"])
                limit = min(limit, row["loss_limit"])
                latched = bool(row["loss_latched"])
            latched = latched or peak - pnl >= limit
            self._connection.execute(
                "INSERT INTO mixed_daily_pnl VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(account_number,et_date) DO UPDATE SET "
                "peak_pnl=excluded.peak_pnl,last_pnl=excluded.last_pnl,"
                "loss_limit=excluded.loss_limit,loss_latched=excluded.loss_latched",
                (account_number, et_date, peak, pnl, limit, int(latched)),
            )
            recovery_blocked = self._record_loss_pause(
                account_number,
                et_date,
                latched,
                scope_digest,
                recovery_delay,
                recovery_unit,
                observed,
            )
        return {
            "peak_pnl": peak,
            "last_pnl": float(pnl),
            "loss_limit": limit,
            "loss_latched": latched,
            "recovery_blocked": recovery_blocked,
        }

    def acknowledge_loss_recovery(self, account_number: str, *, observed_at: datetime | None = None) -> bool:
        """Explicit manual restart clears only the recovery pause, never daily loss history."""
        observed = observed_at or self._store._now()
        if not account_number.strip() or observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Manual recovery requires an account and aware time")
        with self.transaction():
            row = self._connection.execute(
                "SELECT paused_at FROM loss_recovery WHERE account_number=? AND cleared_at IS NULL",
                (account_number,),
            ).fetchone()
            if row is None:
                return False
            if observed.astimezone(UTC) < _parse_aware_utc(row["paused_at"], field="loss pause time"):
                raise ValueError("Recovery cannot precede the loss pause")
            self._connection.execute(
                "UPDATE loss_recovery SET cleared_at=? WHERE account_number=? AND cleared_at IS NULL",
                (observed.astimezone(UTC).isoformat(), account_number),
            )
            return True

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

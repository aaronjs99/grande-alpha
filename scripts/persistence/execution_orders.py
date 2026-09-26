from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from numbers import Real
from typing import Any

from grande_alpha.domain.order_models import BrokerOrder, OrderIntent

from .validation import (
    _parse_aware_utc,
)


class ExecutionOrderMethods:
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

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from numbers import Real
from typing import Any

from grande_alpha.domain.loss_recovery import recovery_deadline
from grande_alpha.domain.policy import EASTERN
from grande_alpha.execution.candidate_execution import next_consecutive_losses

from .validation import (
    _parse_aware_utc,
)


class ExecutionRiskMethods:
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
        inventory: dict[str, tuple[float, float]] = {}
        consecutive = peak = 0
        for row in self.broker_executions(account_number):
            executed_at = _parse_aware_utc(row["executed_at"], field="execution timestamp")
            execution_date = executed_at.astimezone(EASTERN).date()
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
        order_ids: set[str] = set()
        for row in rows:
            executed_at = _parse_aware_utc(row["executed_at"], field="execution timestamp")
            if (
                row["side"] == "buy"
                and executed_at.astimezone(EASTERN).date() == requested_date
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
            if submitted_at.astimezone(EASTERN).date() != requested_date:
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
            if submitted_at.astimezone(EASTERN).date() == requested_date:
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

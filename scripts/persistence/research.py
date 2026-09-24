from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .base import Repository
from .validation import (
    _RISK_ENVELOPE_FIELDS,
    _parse_aware_utc,
    _passing_holdout_metrics,
    _valid_provenance_record,
    _valid_quality_record,
    _valid_risk_envelope,
)


class ResearchRepository(Repository):
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
        with self.transaction():
            cursor = self._connection.execute(
                """INSERT INTO research_fund(
                    created_at,period,realized_profit,fees,tax_reserve,contribution_rate,
                    eligible_contribution,status,notes
                ) VALUES(?,?,?,?,?,?,?,'planned',?)""",
                (
                    self._store._now().isoformat(),
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
        self._store.receipt(
            "research_fund",
            f"Planned ${eligible:,.2f} capital-ledger contribution for {period}",
            {"entry_id": entry_id, "eligible_contribution": eligible, "status": "planned"},
        )
        return entry_id

    def confirm_research_contribution(self, entry_id: int, reference: str) -> None:
        if not reference.strip():
            raise ValueError("A confirmation reference is required")
        with self.transaction():
            row = self._connection.execute(
                "SELECT eligible_contribution,status FROM research_fund WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Research-fund entry does not exist")
            if row["status"] == "confirmed":
                raise ValueError("Research-fund entry is already confirmed")
            confirmed_at = self._store._now().isoformat()
            self._connection.execute(
                """UPDATE research_fund
                SET status='confirmed',confirmed_at=?,confirmation_reference=? WHERE id=?""",
                (confirmed_at, reference.strip(), entry_id),
            )
        self._store.receipt(
            "research_fund",
            f"Confirmed ${float(row['eligible_contribution']):,.2f} capital-ledger contribution",
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

    def record_research_trials(self, dataset_hash: str, trials: list[dict[str, Any]]) -> int:
        """Commit unique candidate trials before promotion statistics are evaluated."""

        with self.transaction():
            before = self._connection.total_changes
            self._connection.executemany(
                """INSERT OR IGNORE INTO research_trials(
                    created_at,dataset_hash,trial_fingerprint,config_json,metrics_json
                ) VALUES(?,?,?,?,?)""",
                [
                    (
                        self._store._now().isoformat(),
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

        with self.transaction():
            prior = self._connection.execute("SELECT * FROM research_holdouts ORDER BY id").fetchall()
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
                    prior_start = _parse_aware_utc(existing["holdout_start"], field="stored holdout_start")
                    prior_end = _parse_aware_utc(existing["holdout_end"], field="stored holdout_end")
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
                        self._store._now().isoformat(),
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
        with self.transaction():
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

        with self.transaction():
            cursor = self._connection.execute(
                """UPDATE research_holdouts
                SET status='EVALUATING',evaluation_started_at=?
                WHERE id=? AND status='FROZEN' AND selected_fingerprint=?""",
                (self._store._now().isoformat(), holdout_id, selected_fingerprint),
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
        with self.transaction():
            cursor = self._connection.execute(
                """UPDATE research_holdouts
                SET status='CONSUMED',consumed_at=?,metrics_json=?
                WHERE id=? AND status='EVALUATING' AND selected_fingerprint=?""",
                (
                    self._store._now().isoformat(),
                    json.dumps(metrics, sort_keys=True, default=str),
                    holdout_id,
                    selected_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Final holdout was not claimed or was already consumed")

    def invalidate_research_holdout(self, holdout_id: int) -> None:
        with self.transaction():
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
            from grande_alpha.research.evidence import (
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
            if not _valid_provenance_record(
                provenance_hash,
                provenance,
                dataset_hash,
                require_runtime_observation=True,
            ):
                raise ValueError("Live-review eligibility requires exact runtime-observation provenance")
        with self.transaction():
            cursor = self._connection.execute(
                """INSERT INTO research_promotions(
                    created_at,dataset_hash,strategy_fingerprint,policy_version,status,
                    source,replay_end,gates_json,risk_envelope_json,holdout_id,
                    provenance_hash,provenance_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self._store._now().isoformat(),
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
        self._store.receipt(
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
        from grande_alpha.research.evidence import (
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
        from grande_alpha.research.evidence import REQUIRED_LIVE_GATE_NAMES

        try:
            replay_end = _parse_aware_utc(evidence["replay_end"], field="replay_end")
            promotion_created_at = _parse_aware_utc(evidence["created_at"], field="promotion created_at")
        except ValueError:
            return None
        reference = self._store._now()
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
                evidence["provenance_hash"],
                provenance,
                evidence["dataset_hash"],
                require_runtime_observation=True,
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

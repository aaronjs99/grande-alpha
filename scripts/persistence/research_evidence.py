from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from grande_alpha.research.evidence_contract import (
    EVIDENCE_POLICY_VERSION,
    REQUIRED_LIVE_GATE_NAMES,
    RUNTIME_SIZING_PARITY_CERTIFIED,
)

from .validation import (
    _RISK_ENVELOPE_FIELDS,
    _parse_aware_utc,
    _passing_holdout_metrics,
    _valid_provenance_record,
    _valid_quality_record,
    _valid_risk_envelope,
)


class ResearchEvidenceMethods:
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

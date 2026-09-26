from __future__ import annotations

import json
import sqlite3
from typing import Any

from .validation import _parse_aware_utc


class ResearchHoldoutMethods:
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

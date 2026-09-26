from __future__ import annotations

import json
import sqlite3
from typing import Any

from .base import Repository


class ShadowRepository(Repository):
    @staticmethod
    def _decode_shadow_checkpoint(row: sqlite3.Row) -> dict[str, Any]:
        from grande_alpha.strategy.shadow_state import validate_shadow_checkpoint

        try:
            checkpoint = json.loads(str(row["checkpoint_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Shadow checkpoint contains invalid JSON") from exc
        validate_shadow_checkpoint(checkpoint)
        column_bindings = {
            "run_id": row["run_id"],
            "sequence": row["sequence"],
            "recorded_at": row["recorded_at"],
            "schema_version": row["schema_version"],
            "session_key": row["session_key"],
            "account_fingerprint": row["account_fingerprint"],
            "strategy_fingerprint": row["strategy_fingerprint"],
            "contract_fingerprint": row["contract_fingerprint"],
            "event": row["event"],
            "previous_digest": row["previous_digest"],
            "digest": row["digest"],
        }
        if any(checkpoint.get(key) != value for key, value in column_bindings.items()):
            raise ValueError("Shadow checkpoint columns do not match the signed payload")
        return checkpoint

    def append_shadow_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Atomically append one immutable checkpoint to a run's hash chain."""

        from grande_alpha.strategy.shadow_state import validate_shadow_checkpoint

        validate_shadow_checkpoint(checkpoint)
        run_id = str(checkpoint["run_id"])
        sequence = int(checkpoint["sequence"])
        with self.transaction():
            previous = self._connection.execute(
                """SELECT sequence,digest FROM shadow_checkpoints
                WHERE run_id=? ORDER BY sequence DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
            if previous is None:
                if sequence != 1 or checkpoint["previous_digest"] is not None:
                    raise ValueError("New shadow checkpoint run must start at sequence one")
            elif (
                sequence != int(previous["sequence"]) + 1
                or checkpoint["previous_digest"] != previous["digest"]
            ):
                raise ValueError("Shadow checkpoint sequence or previous digest has a gap")
            self._connection.execute(
                """INSERT INTO shadow_checkpoints(
                    run_id,sequence,recorded_at,schema_version,session_key,
                    account_fingerprint,strategy_fingerprint,contract_fingerprint,event,
                    previous_digest,digest,checkpoint_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    sequence,
                    checkpoint["recorded_at"],
                    checkpoint["schema_version"],
                    checkpoint["session_key"],
                    checkpoint["account_fingerprint"],
                    checkpoint["strategy_fingerprint"],
                    checkpoint["contract_fingerprint"],
                    checkpoint["event"],
                    checkpoint["previous_digest"],
                    checkpoint["digest"],
                    json.dumps(
                        checkpoint,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                ),
            )

    def latest_shadow_checkpoint(self) -> dict[str, Any] | None:
        """Load and fully verify the latest run chain before offering recovery."""

        with self._lock:
            latest = self._connection.execute(
                "SELECT * FROM shadow_checkpoints ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                return None
            rows = self._connection.execute(
                """SELECT * FROM shadow_checkpoints
                WHERE run_id=? ORDER BY sequence""",
                (latest["run_id"],),
            ).fetchall()
            previous_digest: str | None = None
            for expected_sequence, row in enumerate(rows, start=1):
                checkpoint = self._decode_shadow_checkpoint(row)
                if (
                    checkpoint["sequence"] != expected_sequence
                    or checkpoint["previous_digest"] != previous_digest
                ):
                    raise ValueError("Shadow checkpoint chain is incomplete or out of order")
                previous_digest = str(checkpoint["digest"])
            if rows[-1]["id"] != latest["id"]:
                raise ValueError("Latest shadow checkpoint is not the end of its run chain")
            return self._decode_shadow_checkpoint(latest)

    def shadow_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        """Return a verified checkpoint chain for inspection and recovery."""

        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("Shadow run id must be nonempty")
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM shadow_checkpoints WHERE run_id=? ORDER BY sequence",
                (run_id.strip(),),
            ).fetchall()
            checkpoints = [self._decode_shadow_checkpoint(row) for row in rows]
        previous_digest: str | None = None
        for expected_sequence, checkpoint in enumerate(checkpoints, start=1):
            if (
                checkpoint["sequence"] != expected_sequence
                or checkpoint["previous_digest"] != previous_digest
            ):
                raise ValueError("Shadow checkpoint chain is incomplete or out of order")
            previous_digest = str(checkpoint["digest"])
        return checkpoints

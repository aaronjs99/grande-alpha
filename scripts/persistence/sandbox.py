from __future__ import annotations

import json
from typing import Any

from .base import Repository


class SandboxRepository(Repository):
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
        with self.transaction():
            self._connection.execute(
                """INSERT INTO sandbox_runs(
                    run_id,created_at,data_source,replay_start,replay_end,config_json,metrics_json
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    run_id,
                    self._store._now().isoformat(),
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
        self._store.receipt(
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

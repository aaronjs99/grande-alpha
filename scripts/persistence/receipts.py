from __future__ import annotations

import json
from typing import Any

from .base import Repository


class ReceiptsRepository(Repository):
    def receipt(self, category: str, summary: str, payload: Any = None, severity: str = "info") -> None:
        with self.transaction():
            cursor = self._connection.execute(
                "INSERT INTO receipts(created_at,category,severity,summary,payload_json) VALUES(?,?,?,?,?)",
                (
                    self._store._now().isoformat(),
                    category,
                    severity,
                    summary,
                    json.dumps(payload or {}, default=str),
                ),
            )
            if severity in {"warning", "error", "critical"}:
                self._connection.execute(
                    "INSERT INTO device_notifications(receipt_id,created_at,severity,summary) VALUES(?,?,?,?)",
                    (cursor.lastrowid, self._store._now().isoformat(), severity, summary),
                )
        if severity in {"warning", "error", "critical"} and self._store.notification_sink is not None:
            # Persist first. A display failure cannot erase an alert or its audit receipt.
            sink = self._store.notification_sink
            self._transactions.after_commit(lambda: sink({"severity": severity, "summary": summary}))

    def device_notifications(
        self, *, after_id: int = 0, limit: int = 100, unread_only: bool = False
    ) -> list[dict]:
        if type(after_id) is not int or after_id < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Notification cursor and limit are invalid")
        with self._lock:
            rows = self._connection.execute(
                "SELECT id,created_at,severity,summary,acknowledged_at FROM device_notifications WHERE id>?"
                + (" AND acknowledged_at IS NULL" if unread_only else "")
                + " ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_notification(self, notification_id: int) -> None:
        if type(notification_id) is not int or notification_id <= 0:
            raise ValueError("Notification id must be a positive integer")
        with self.transaction():
            row = self._connection.execute(
                "UPDATE device_notifications SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                (self._store._now().isoformat(), notification_id),
            )
            if row.rowcount != 1:
                raise ValueError("Notification not found")

    def recent_receipts(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

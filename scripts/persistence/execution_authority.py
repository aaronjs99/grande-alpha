from __future__ import annotations


class ExecutionAuthorityMethods:
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

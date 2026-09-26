from __future__ import annotations

from datetime import datetime
from typing import Any


class ResearchFundMethods:
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

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from grande_alpha.research.evidence_contract import EVIDENCE_POLICY_VERSION
from grande_alpha.research.historical import RUNTIME_REQUIRED_SYMBOLS


def audit_evidence_ledger(database_path: Path) -> dict[str, Any]:
    """Return a query-only ledger inventory without running migrations or reserving a holdout."""

    result: dict[str, Any] = {
        "database": str(database_path),
        "exists": database_path.is_file(),
        "read_only": True,
        "policy_version": EVIDENCE_POLICY_VERSION,
        "trials": 0,
        "trial_datasets": 0,
        "promotions": 0,
        "promotion_statuses": {},
        "promotion_policy_versions": {},
        "holdouts": 0,
        "holdout_statuses": {},
        "latest_promotion": None,
        "runtime_trace": {
            "quotes": 0,
            "quote_symbols": {},
            "quote_start": None,
            "quote_end": None,
            "balanced_required_symbols": False,
            "bars": 0,
            "bar_symbols": {},
            "bar_start": None,
            "bar_end": None,
            "eligible_historical_bundle": False,
            "reason": (
                "Runtime trace is collection progress only: aligned OHLCV bars for QQQ/TQQQ/SQQQ, "
                "complete sessions, exact construction, and manifest-bound provenance are not established"
            ),
        },
    }
    if not database_path.is_file():
        return result
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "research_trials" in tables:
            row = connection.execute(
                "SELECT COUNT(*) AS n,COUNT(DISTINCT dataset_hash) AS datasets FROM research_trials"
            ).fetchone()
            result["trials"] = int(row["n"])
            result["trial_datasets"] = int(row["datasets"])
        if "research_promotions" in tables:
            result["promotions"] = int(
                connection.execute("SELECT COUNT(*) AS n FROM research_promotions").fetchone()["n"]
            )
            result["promotion_statuses"] = {
                row["status"]: int(row["n"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS n FROM research_promotions GROUP BY status"
                )
            }
            result["promotion_policy_versions"] = {
                str(row["policy_version"]): int(row["n"])
                for row in connection.execute(
                    "SELECT policy_version,COUNT(*) AS n FROM research_promotions GROUP BY policy_version"
                )
            }
            latest = connection.execute(
                """SELECT id,created_at,dataset_hash,policy_version,status,source,replay_end,holdout_id
                FROM research_promotions ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            result["latest_promotion"] = dict(latest) if latest is not None else None
        if "research_holdouts" in tables:
            result["holdouts"] = int(
                connection.execute("SELECT COUNT(*) AS n FROM research_holdouts").fetchone()["n"]
            )
            result["holdout_statuses"] = {
                row["status"]: int(row["n"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS n FROM research_holdouts GROUP BY status"
                )
            }
        trace = result["runtime_trace"]
        if "quotes" in tables:
            quote_summary = connection.execute(
                """SELECT COUNT(*) AS n,MIN(venue_timestamp) AS started,
                MAX(venue_timestamp) AS ended FROM quotes"""
            ).fetchone()
            trace["quotes"] = int(quote_summary["n"])
            trace["quote_start"] = quote_summary["started"]
            trace["quote_end"] = quote_summary["ended"]
            trace["quote_symbols"] = {
                row["symbol"]: int(row["n"])
                for row in connection.execute(
                    "SELECT symbol,COUNT(*) AS n FROM quotes GROUP BY symbol ORDER BY symbol"
                )
            }
            required_counts = [
                trace["quote_symbols"].get(symbol, 0) for symbol in RUNTIME_REQUIRED_SYMBOLS
            ]
            trace["balanced_required_symbols"] = bool(
                required_counts and min(required_counts) > 0 and len(set(required_counts)) == 1
            )
        if "bars" in tables:
            bar_summary = connection.execute(
                "SELECT COUNT(*) AS n,MIN(start_at) AS started,MAX(start_at) AS ended FROM bars"
            ).fetchone()
            trace["bars"] = int(bar_summary["n"])
            trace["bar_start"] = bar_summary["started"]
            trace["bar_end"] = bar_summary["ended"]
            trace["bar_symbols"] = {
                row["symbol"]: int(row["n"])
                for row in connection.execute(
                    "SELECT symbol,COUNT(*) AS n FROM bars GROUP BY symbol ORDER BY symbol"
                )
            }
        return result
    finally:
        connection.close()

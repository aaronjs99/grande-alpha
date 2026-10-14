"""Bounded read-only desktop projections; the complete journal stays in the worker."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

TEAM = ("NOVA", "ORIN", "VELA", "KADE", "RUNE", "ZARA")
PAPER_AMOUNTS = ("initial_cash", "trade_cash", "cash", "equity", "realized_pnl", "unrealized_pnl",
                 "total_pnl", "return_pct", "max_drawdown_pct")
PAPER_COUNTS = ("fill_count", "pending_count", "closed_trades", "wins", "losses")


def _amount(value):
    try:
        raw = str(value)
        number = Decimal(raw) if len(raw) <= 64 and not isinstance(value, bool) else None
        return str(number) if number is not None and number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _timestamp(value):
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return instant.isoformat() if instant.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def paper_summary(paper: dict | None) -> dict | None:
    if not paper:
        return None
    result = {name: _amount(paper.get(name)) for name in PAPER_AMOUNTS}
    result.update({name: paper.get(name) if type(paper.get(name)) is int else 0 for name in PAPER_COUNTS})
    result.update(
        source=paper.get("source") if paper.get("source") in {"demo", "broker_quotes"} else None,
        active=paper.get("active") is True, session_id=str(paper.get("session_id", ""))[:128],
        observed_at=_timestamp(paper.get("observed_at")), win_rate=_amount(paper.get("win_rate")),
        position_count=len(paper.get("positions") or []),
        stale_marks=any(item.get("stale") is True for item in paper.get("positions") or []),
        equity_history=[{"at": _timestamp(item.get("at")), "equity": _amount(item.get("equity"))}
                        for item in (paper.get("equity_history") or [])[-160:]],
    )
    return result


def desk_snapshot(snapshot) -> dict | None:
    if snapshot is None:
        return None
    return {
        "session_id": snapshot.session_id[:128], "running": snapshot.running, "cycle": snapshot.cycle,
        "phase": snapshot.phase[:128],
        "observed_at": snapshot.observed_at.isoformat() if snapshot.observed_at else None,
        "team_status": {name: str(snapshot.team_status.get(name, "Idle"))[:80] for name in TEAM},
        "analysis_status": {name: str(snapshot.analysis_status[name])[:80]
                            for name in ("equity", "crypto") if name in snapshot.analysis_status},
        "team_events": [{**{name: str(item.get(name, ""))[:64] for name in ("from", "to", "kind", "at")},
                         "message": str(item.get("message", ""))[:240],
                         "id": item.get("id") if type(item.get("id")) is int else 0,
                         "cycle": item.get("cycle") if type(item.get("cycle")) is int else 0}
                        for item in snapshot.team_events[-12:]],
    }

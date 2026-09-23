"""Replay reports and append-only forward-observation evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grande_alpha.earnings import _number, _time
from grande_alpha.json_inputs import load_json
from grande_alpha.mixed_portfolio import plan
from grande_alpha.portfolio_replay import EASTERN, replay


def _drawdown(curve: list[dict]) -> float:
    peak = 0.0
    worst = 0.0
    for point in curve:
        peak = max(peak, float(point["nav"]))
        worst = max(worst, peak - float(point["nav"]))
    return worst


def replay_report(payload: dict) -> dict:
    result = replay(payload)
    return {
        "input_sha256": result["input_sha256"],
        "frames": len(payload["frames"]),
        "net_change_after_costs": result["net_change"],
        "max_drawdown_usd": _drawdown(result["curve"]),
    }


def _validate_forward_frame(frame: dict) -> datetime:
    if not isinstance(frame, dict) or set(frame) != {"request", "quotes", "theses", "corporate_actions"}:
        raise ValueError("Exact forward frame schema required")
    if frame["corporate_actions"] != []:
        raise ValueError("Unmodeled corporate actions cannot enter forward evidence")
    result = plan(frame["request"])
    observed = _time(result["as_of"], "as_of")
    if not isinstance(frame["theses"], dict) or any(type(value) is not bool for value in frame["theses"].values()):
        raise ValueError("Forward theses must be explicit booleans")
    quotes = frame["quotes"]
    if not isinstance(quotes, dict) or not quotes:
        raise ValueError("Forward evidence requires quotes")
    for symbol, quote in quotes.items():
        if not isinstance(symbol, str) or not isinstance(quote, dict) or set(quote) != {
            "bid", "ask", "observed_at", "available_at"
        }:
            raise ValueError("Exact forward quote schema required")
        bid = _number(quote["bid"], "bid", positive=True)
        ask = _number(quote["ask"], "ask", positive=True)
        quote_at = _time(quote["observed_at"], "quote observed_at")
        available = _time(quote["available_at"], "quote available_at")
        if bid > ask or not quote_at <= available <= observed:
            raise ValueError("Forward quote is crossed or noncausal")
    return observed


class ForwardFrameStore:
    """Records frames near observation time so later replay cannot masquerade as forward evidence."""

    def __init__(self, path: Path, *, now=lambda: datetime.now(UTC), max_recording_lag_seconds: float = 120):
        _number(max_recording_lag_seconds, "max_recording_lag_seconds", positive=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now
        self.max_lag = max_recording_lag_seconds
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS forward_frames(
                    observed_at TEXT PRIMARY KEY, recorded_at TEXT NOT NULL,
                    payload_sha256 TEXT UNIQUE NOT NULL, payload TEXT NOT NULL
                );
            """)

    def close(self) -> None:
        self.db.close()

    def append(self, frame: dict) -> str:
        observed = _validate_forward_frame(frame)
        recorded = self.now()
        if recorded.tzinfo is None or recorded.utcoffset() is None:
            raise ValueError("Forward recorder clock must include an offset")
        lag = (recorded.astimezone(UTC) - observed).total_seconds()
        if not 0 <= lag <= self.max_lag:
            raise ValueError("Frame was not recorded close enough to its claimed decision time")
        canonical = json.dumps(frame, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.db:
            latest = self.db.execute("SELECT MAX(observed_at) AS value FROM forward_frames").fetchone()["value"]
            if latest is not None and observed <= datetime.fromisoformat(latest):
                raise ValueError("Forward frames must be appended in strict chronological order")
            self.db.execute("INSERT INTO forward_frames VALUES(?,?,?,?)",
                            (observed.isoformat(), recorded.astimezone(UTC).isoformat(), digest, canonical))
        return digest

    def report(self, replay_settings: dict) -> dict:
        rows = self.db.execute("SELECT observed_at,payload FROM forward_frames ORDER BY observed_at").fetchall()
        payload = dict(replay_settings)
        payload["frames"] = [json.loads(row["payload"]) for row in rows]
        result = replay(payload)
        sessions = {datetime.fromisoformat(row["observed_at"]).astimezone(EASTERN).date() for row in rows}
        return {
            "input_sha256": result["input_sha256"],
            "sessions": len(sessions),
            "decisions": len(rows),
            "net_change_after_costs": result["net_change"],
            "max_drawdown_usd": _drawdown(result["curve"]),
            "last_observed_at": rows[-1]["observed_at"],
        }


def make_certificate(candidate_digest: str, historical: dict, forward: dict,
                     failure_recovery: dict, *, issued_at: datetime, valid_for: timedelta) -> dict:
    if issued_at.tzinfo is None or issued_at.utcoffset() is None or not timedelta(0) < valid_for <= timedelta(days=30):
        raise ValueError("Certificate time must be aware and valid for at most 30 days")
    from grande_alpha.standing import ROBINHOOD_CONTRACT_SHA256

    return {
        "schema_version": 1,
        "candidate_digest": candidate_digest,
        "broker_contract_sha256": ROBINHOOD_CONTRACT_SHA256,
        "issued_at": issued_at.isoformat(),
        "expires_at": (issued_at + valid_for).isoformat(),
        "historical": historical,
        "forward": forward,
        "failure_recovery": failure_recovery,
    }


def command_replay_report(args) -> int:
    payload = load_json(Path(args.input), max_bytes=32_000_000)
    print(json.dumps(replay_report(payload), indent=2, allow_nan=False))
    return 0


def command_forward_append(args) -> int:
    frame = load_json(Path(args.input), max_bytes=8_000_000)
    store = ForwardFrameStore(Path(args.database))
    try:
        digest = store.append(frame)
    finally:
        store.close()
    print(json.dumps({"recorded": True, "frame_sha256": digest}, indent=2))
    return 0


def command_forward_report(args) -> int:
    settings = load_json(Path(args.settings), max_bytes=64_000)
    if not isinstance(settings, dict) or "frames" in settings:
        raise ValueError("Forward replay settings must be an object without a frames field")
    store = ForwardFrameStore(Path(args.database))
    try:
        report = store.report(settings)
    finally:
        store.close()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0

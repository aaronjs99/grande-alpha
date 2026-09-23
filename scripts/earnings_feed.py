"""Point-in-time earnings observations from Alpha Vantage.

Raw responses are retained with retrieval times and hashes. A post-report response is
never relabeled as a pre-report consensus snapshot.
"""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import json
import math
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx

from grande_alpha.equity_execution import symbol_valid
from grande_alpha.json_inputs import load_json

KEYRING_SERVICE = "GRANDE Alpha"
KEYRING_ACCOUNT = "alpha_vantage_api_key"


def load_api_key() -> tuple[str, str]:
    value = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if value:
        return value, "environment"
    import keyring

    value = (keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or "").strip()
    if value:
        return value, "windows_credential_manager"
    raise RuntimeError("No Alpha Vantage key is configured")


class AlphaVantageEarningsClient:
    URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str, *, timeout_seconds: float = 20):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Alpha Vantage API key is required")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 60):
            raise ValueError("Earnings timeout must be between 1 and 60 seconds")
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds

    async def fetch(self, symbol: str, dataset: str) -> dict:
        if not symbol_valid(symbol) or dataset not in {"EARNINGS", "EARNINGS_ESTIMATES"}:
            raise ValueError("Exact symbol and supported earnings dataset are required")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(self.URL, params={"function": dataset, "symbol": symbol,
                                                          "apikey": self.api_key})
            response.raise_for_status()
            if len(response.content) > 16_000_000:
                raise RuntimeError("Earnings provider response exceeded the bounded size")
            payload = response.json()
        observed_at = datetime.now(UTC)
        if not isinstance(payload, dict) or any(key in payload for key in ("Error Message", "Information", "Note")):
            raise RuntimeError("Earnings provider rejected, throttled, or did not return an object dataset")
        raw = json.dumps({"provider": "alpha_vantage", "dataset": dataset,
                          "symbol": symbol, "payload": payload},
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
        return {"provider": "alpha_vantage", "dataset": dataset, "symbol": symbol,
                "observed_at": observed_at.isoformat(), "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "payload": payload}


class EarningsObservationStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS earnings_observations(
                    sha256 TEXT PRIMARY KEY, provider TEXT NOT NULL, dataset TEXT NOT NULL,
                    symbol TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(provider,dataset,symbol,observed_at)
                );
                CREATE TABLE IF NOT EXISTS earnings_facts(
                    fact_sha256 TEXT PRIMARY KEY, kind TEXT NOT NULL, symbol TEXT NOT NULL,
                    fiscal_period TEXT NOT NULL, basis TEXT NOT NULL, currency TEXT NOT NULL,
                    value REAL NOT NULL, available_at TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL REFERENCES earnings_observations(sha256)
                );
            """)

    def close(self):
        self.db.close()

    def record(self, observation: dict) -> None:
        required = {"provider", "dataset", "symbol", "observed_at", "sha256", "payload"}
        if not isinstance(observation, dict) or set(observation) != required:
            raise ValueError("Exact earnings observation envelope required")
        when = datetime.fromisoformat(observation["observed_at"])
        if when.tzinfo is None or when.utcoffset() is None or not symbol_valid(observation["symbol"]):
            raise ValueError("Invalid earnings observation identity or time")
        if observation["provider"] != "alpha_vantage" or observation["dataset"] not in {
            "EARNINGS", "EARNINGS_ESTIMATES"
        } or not isinstance(observation["payload"], dict):
            raise ValueError("Unsupported earnings observation")
        raw = json.dumps({"provider": observation["provider"], "dataset": observation["dataset"],
                          "symbol": observation["symbol"], "payload": observation["payload"]},
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(raw.encode()).hexdigest() != observation["sha256"]:
            raise ValueError("Earnings payload hash mismatch")
        payload = json.dumps(observation["payload"], sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO earnings_observations VALUES(?,?,?,?,?,?)",
                            (observation["sha256"], observation["provider"], observation["dataset"],
                             observation["symbol"], when.astimezone(UTC).isoformat(), payload))

    def has_preannouncement_consensus(self, symbol: str, announced_at: datetime, sha256: str) -> bool:
        if announced_at.tzinfo is None or announced_at.utcoffset() is None:
            raise ValueError("Announcement time must be aware")
        row = self.db.execute(
            "SELECT observed_at FROM earnings_observations WHERE sha256=? AND symbol=? "
            "AND dataset='EARNINGS_ESTIMATES'", (sha256, symbol)
        ).fetchone()
        return bool(row and datetime.fromisoformat(row["observed_at"]) < announced_at.astimezone(UTC))

    def record_fact(self, fact: dict) -> str:
        required = {"kind", "symbol", "fiscal_period", "basis", "currency", "value",
                    "available_at", "source_sha256"}
        if not isinstance(fact, dict) or set(fact) != required:
            raise ValueError("Exact normalized earnings fact required")
        if (fact["kind"] not in {"actual", "consensus"} or not symbol_valid(fact["symbol"])
                or fact["currency"] != "USD" or not all(
                    isinstance(fact[key], str) and fact[key].strip()
                    for key in ("fiscal_period", "basis", "source_sha256"))):
            raise ValueError("Invalid normalized earnings fact identity")
        if (isinstance(fact["value"], bool) or not isinstance(fact["value"], (int, float))
                or not math.isfinite(fact["value"])):
            raise ValueError("Earnings fact value must be numeric")
        available = datetime.fromisoformat(fact["available_at"])
        if available.tzinfo is None or available.utcoffset() is None:
            raise ValueError("Earnings fact availability must include an offset")
        source = self.db.execute(
            "SELECT dataset,observed_at FROM earnings_observations WHERE sha256=?",
            (fact["source_sha256"],),
        ).fetchone()
        expected = "EARNINGS" if fact["kind"] == "actual" else "EARNINGS_ESTIMATES"
        if source is None or source["dataset"] != expected:
            raise ValueError("Earnings fact is not bound to the required raw provider dataset")
        if available.astimezone(UTC) > datetime.fromisoformat(source["observed_at"]):
            raise ValueError("Fact cannot be available after its recorded provider observation")
        canonical = json.dumps(fact, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO earnings_facts VALUES(?,?,?,?,?,?,?,?,?)",
                            (digest, fact["kind"], fact["symbol"], fact["fiscal_period"],
                             fact["basis"], fact["currency"], float(fact["value"]),
                             available.astimezone(UTC).isoformat(), fact["source_sha256"]))
        return digest

    def normalize_fact(self, source_sha256: str, *, kind: str, fiscal_period: str,
                       basis: str, currency: str) -> str:
        source = self.db.execute(
            "SELECT symbol,dataset,observed_at,payload FROM earnings_observations WHERE sha256=?",
            (source_sha256,),
        ).fetchone()
        if source is None or kind not in {"actual", "consensus"}:
            raise ValueError("Exact raw source and fact kind are required")
        if not all(isinstance(value, str) and value.strip()
                   for value in (fiscal_period, basis, currency)):
            raise ValueError("Fiscal period, basis and currency must be explicit")
        payload = json.loads(source["payload"])
        if kind == "consensus":
            if source["dataset"] != "EARNINGS_ESTIMATES":
                raise ValueError("Consensus requires an EARNINGS_ESTIMATES source")
            matches = [row for row in payload.get("estimates", [])
                       if row.get("horizon") == "fiscal quarter" and row.get("date") == fiscal_period]
            field = "eps_estimate_average"
        else:
            if source["dataset"] != "EARNINGS":
                raise ValueError("Actual EPS requires an EARNINGS source")
            matches = [row for row in payload.get("quarterlyEarnings", [])
                       if row.get("fiscalDateEnding") == fiscal_period]
            field = "reportedEPS"
        if len(matches) != 1:
            raise ValueError("Provider response did not contain exactly one matching quarterly fact")
        try:
            value = float(matches[0][field])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Provider quarterly EPS value is missing or invalid") from exc
        return self.record_fact({"kind": kind, "symbol": source["symbol"],
                                 "fiscal_period": fiscal_period, "basis": basis,
                                 "currency": currency, "value": value,
                                 "available_at": source["observed_at"],
                                 "source_sha256": source_sha256})

    def verify_event(self, event: dict) -> bool:
        if not isinstance(event, dict) or not isinstance(event.get("source_id"), str):
            return False
        parts = event["source_id"].split(":")
        if len(parts) != 3 or parts[0] != "grande-alpha-earnings-v1":
            return False
        consensus = self.db.execute("SELECT * FROM earnings_facts WHERE fact_sha256=?", (parts[1],)).fetchone()
        actual = self.db.execute("SELECT * FROM earnings_facts WHERE fact_sha256=?", (parts[2],)).fetchone()
        if consensus is None or actual is None:
            return False
        announced = datetime.fromisoformat(event["announced_at"]).astimezone(UTC)
        actual_available = datetime.fromisoformat(event["actual_available_at"]).astimezone(UTC)
        identity = (event["symbol"], event["actual_period"], event["actual_basis"], "USD")
        return bool(
            consensus["kind"] == "consensus" and actual["kind"] == "actual"
            and tuple(consensus[key] for key in ("symbol", "fiscal_period", "basis", "currency")) == identity
            and tuple(actual[key] for key in ("symbol", "fiscal_period", "basis", "currency")) == identity
            and float(consensus["value"]) == float(event["consensus_eps"])
            and float(actual["value"]) == float(event["actual_eps"])
            and datetime.fromisoformat(consensus["available_at"]) < announced
            and announced <= datetime.fromisoformat(actual["available_at"]) <= actual_available
        )


def command_fetch(args) -> int:
    api_key, _source = load_api_key()
    observation = asyncio.run(AlphaVantageEarningsClient(api_key).fetch(args.symbol, args.dataset))
    store = EarningsObservationStore(Path(args.database))
    try:
        store.record(observation)
    finally:
        store.close()
    print(json.dumps({key: observation[key] for key in observation if key != "payload"}, indent=2))
    return 0


def command_key_set(args) -> int:
    value = getpass.getpass("Alpha Vantage API key (hidden): ").strip()
    if not value.isalnum() or not 8 <= len(value) <= 128:
        raise ValueError("API key must be 8-128 letters or digits")
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)
    print(json.dumps({"configured": True, "storage": "windows_credential_manager"}, indent=2))
    return 0


def command_key_status(args) -> int:
    try:
        _value, source = load_api_key()
    except RuntimeError:
        print(json.dumps({"configured": False}, indent=2))
        return 2
    print(json.dumps({"configured": True, "source": source}, indent=2))
    return 0


def command_key_delete(args) -> int:
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except PasswordDeleteError:
        pass
    print(json.dumps({"configured_in_credential_manager": False}, indent=2))
    return 0


def command_record_fact(args) -> int:
    fact = load_json(Path(args.input), max_bytes=64_000)
    store = EarningsObservationStore(Path(args.database))
    try:
        digest = store.record_fact(fact)
    finally:
        store.close()
    print(json.dumps({"recorded": True, "fact_sha256": digest}, indent=2))
    return 0


def command_normalize_fact(args) -> int:
    store = EarningsObservationStore(Path(args.database))
    try:
        digest = store.normalize_fact(args.source_sha, kind=args.kind, fiscal_period=args.period,
                                      basis=args.basis, currency=args.currency)
    finally:
        store.close()
    print(json.dumps({"recorded": True, "fact_sha256": digest}, indent=2))
    return 0


def command_verify_event(args) -> int:
    event = load_json(Path(args.input), max_bytes=128_000)
    store = EarningsObservationStore(Path(args.database))
    try:
        verified = store.verify_event(event)
    finally:
        store.close()
    print(json.dumps({"verified": verified, "authority_granted": False}, indent=2))
    return 0 if verified else 2

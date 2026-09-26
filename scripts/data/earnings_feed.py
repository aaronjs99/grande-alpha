"""Point-in-time earnings observations from Alpha Vantage.

Raw responses are retained with retrieval times and hashes. A post-report response is
never relabeled as a pre-report consensus snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from grande_alpha.execution.equity_execution import symbol_valid

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


def store_api_key(value: str) -> None:
    value = value.strip()
    if not value.isalnum() or not 8 <= len(value) <= 128:
        raise ValueError("API key must be 8-128 letters or digits")
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)


def delete_api_key() -> None:
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except PasswordDeleteError:
        pass


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
                CREATE TABLE IF NOT EXISTS earnings_provider_requests(
                    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requested_at TEXT NOT NULL, dataset TEXT NOT NULL, symbol TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS earnings_response_checks(
                    sha256 TEXT NOT NULL REFERENCES earnings_observations(sha256),
                    checked_at TEXT NOT NULL, PRIMARY KEY(sha256,checked_at)
                );
                CREATE TABLE IF NOT EXISTS earnings_events(
                    event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, announced_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
            """)

    def close(self) -> None:
        self.db.close()

    def cached_observation(self, symbol: str, dataset: str, *, now: datetime,
                           max_age: timedelta) -> dict | None:
        """Return a recent raw observation without manufacturing a new availability time."""
        row = self.db.execute(
            "SELECT * FROM earnings_observations WHERE symbol=? AND dataset=? "
            "AND observed_at<=? ORDER BY observed_at DESC LIMIT 1",
            (symbol, dataset, now.astimezone(UTC).isoformat()),
        ).fetchone()
        if row is None:
            return None
        checked = self.db.execute("SELECT MAX(checked_at) FROM earnings_response_checks WHERE sha256=?",
                                  (row["sha256"],)).fetchone()[0]
        freshest = max(datetime.fromisoformat(row["observed_at"]),
                       datetime.fromisoformat(checked) if checked else datetime.min.replace(tzinfo=UTC))
        if now.astimezone(UTC) - freshest > max_age:
            return None
        return {"provider": row["provider"], "dataset": row["dataset"], "symbol": row["symbol"],
                "observed_at": row["observed_at"], "sha256": row["sha256"],
                "payload": json.loads(row["payload"])}

    def reserve_request(self, symbol: str, dataset: str, *, now: datetime,
                        max_requests_per_24h: int = 25) -> int:
        """Count an attempted provider request before dispatch, including failed requests."""
        if not symbol_valid(symbol) or dataset not in {"EARNINGS", "EARNINGS_ESTIMATES"}:
            raise ValueError("Supported dataset and symbol are required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Provider request time must be timezone-aware")
        if type(max_requests_per_24h) is not int or not 1 <= max_requests_per_24h <= 25:
            raise ValueError("Provider request budget must be an integer between 1 and 25")
        boundary = (now.astimezone(UTC) - timedelta(hours=24)).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            count = self.db.execute(
                "SELECT COUNT(*) FROM earnings_provider_requests WHERE requested_at>?", (boundary,)
            ).fetchone()[0]
            if count >= max_requests_per_24h:
                raise RuntimeError("Alpha Vantage request budget is exhausted for this 24-hour window")
            self.db.execute(
                "INSERT INTO earnings_provider_requests(requested_at,dataset,symbol) VALUES(?,?,?)",
                (now.astimezone(UTC).isoformat(), dataset, symbol),
            )
            self.db.commit()
            return max_requests_per_24h - count - 1
        except BaseException:
            self.db.rollback()
            raise

    async def fetch_cached(self, client: AlphaVantageEarningsClient, symbol: str, dataset: str,
                           *, now: datetime | None = None, cache_age: timedelta = timedelta(hours=12),
                           max_requests_per_24h: int = 25) -> tuple[dict, bool, int | None]:
        """Use stored raw data when recent; otherwise reserve quota before a network request."""
        observed = now or datetime.now(UTC)
        if observed.tzinfo is None or observed.utcoffset() is None or cache_age <= timedelta(0):
            raise ValueError("Cache policy requires an aware time and positive age")
        cached = self.cached_observation(symbol, dataset, now=observed, max_age=cache_age)
        if cached is not None:
            return cached, True, None
        remaining = self.reserve_request(symbol, dataset, now=observed,
                                         max_requests_per_24h=max_requests_per_24h)
        result = await client.fetch(symbol, dataset)
        if result.get("symbol") != symbol or result.get("dataset") != dataset:
            raise ValueError("Provider observation does not match the requested symbol and dataset")
        self.record(result)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO earnings_response_checks VALUES(?,?)",
                            (result["sha256"], observed.astimezone(UTC).isoformat()))
        stored = self.db.execute(
            "SELECT provider,dataset,symbol,observed_at,sha256,payload "
            "FROM earnings_observations WHERE sha256=?", (result["sha256"],)
        ).fetchone()
        return {**{key: stored[key] for key in ("provider", "dataset", "symbol", "observed_at", "sha256")},
                "payload": json.loads(stored["payload"])}, False, remaining

    async def refresh_observations(self, symbols: set[str], client: AlphaVantageEarningsClient, *,
                                   now: datetime | None = None,
                                   cache_age: timedelta = timedelta(hours=12),
                                   max_requests_per_24h: int = 25) -> dict[str, dict[str, dict]]:
        """Collect raw quarterly datasets and normalize only unambiguous EPS rows.

        A normalized fact retains its first provider-observation availability. Neither
        a fiscal date nor a later response establishes an announcement timestamp.
        """
        if len(symbols) > 100 or any(not symbol_valid(symbol) for symbol in symbols):
            raise ValueError("A bounded set of exact symbols is required")
        results: dict[str, dict[str, dict]] = {}
        for symbol in sorted(symbols):
            results[symbol] = {}
            for dataset, kind in (("EARNINGS", "actual"), ("EARNINGS_ESTIMATES", "consensus")):
                try:
                    raw, cached, remaining = await self.fetch_cached(
                        client, symbol, dataset, now=now, cache_age=cache_age,
                        max_requests_per_24h=max_requests_per_24h)
                except RuntimeError as exc:
                    status = ("budget_exhausted" if "budget is exhausted" in str(exc)
                              else "provider_error")
                    results[symbol][dataset] = {"status": status, "quarterly_facts": 0,
                                                "reason": str(exc)}
                    continue
                except (ConnectionError, TimeoutError, httpx.HTTPError, ValueError) as exc:
                    results[symbol][dataset] = {"status": "provider_error", "quarterly_facts": 0,
                                                "reason": str(exc)}
                    continue
                payload = raw["payload"]
                rows = payload.get("quarterlyEarnings" if kind == "actual" else "estimates")
                if not isinstance(rows, list):
                    results[symbol][dataset] = {"status": "missing_quarterly_rows",
                                                "quarterly_facts": 0, "sha256": raw["sha256"],
                                                "observed_at": raw["observed_at"]}
                    continue
                periods = set()
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if kind == "consensus" and row.get("horizon") != "fiscal quarter":
                        continue
                    period = row.get("fiscalDateEnding" if kind == "actual" else "date")
                    if isinstance(period, str) and period:
                        periods.add(period)
                count = 0
                for period in sorted(periods):
                    try:
                        self.normalize_fact(raw["sha256"], kind=kind, fiscal_period=period,
                                            basis="alpha_vantage_eps_unspecified", currency="USD")
                    except ValueError:
                        continue
                    count += 1
                results[symbol][dataset] = {
                    "status": "cached" if cached else "observed", "quarterly_facts": count,
                    "sha256": raw["sha256"], "observed_at": raw["observed_at"],
                    "remaining_requests_24h": remaining,
                }
        return results

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
            "SELECT dataset,symbol,observed_at,payload FROM earnings_observations WHERE sha256=?",
            (fact["source_sha256"],),
        ).fetchone()
        expected = "EARNINGS" if fact["kind"] == "actual" else "EARNINGS_ESTIMATES"
        if source is None or source["dataset"] != expected:
            raise ValueError("Earnings fact is not bound to the required raw provider dataset")
        if source["symbol"] != fact["symbol"]:
            raise ValueError("Earnings fact symbol does not match its raw provider observation")
        if available.astimezone(UTC) != datetime.fromisoformat(source["observed_at"]):
            raise ValueError("Fact availability must equal its recorded provider observation")
        payload = json.loads(source["payload"])
        if fact["kind"] == "actual":
            rows = payload.get("quarterlyEarnings", [])
            matches = [row for row in rows if isinstance(row, dict)
                       and row.get("fiscalDateEnding") == fact["fiscal_period"]]
            field = "reportedEPS"
        else:
            rows = payload.get("estimates", [])
            matches = [row for row in rows if isinstance(row, dict)
                       and row.get("horizon") == "fiscal quarter"
                       and row.get("date") == fact["fiscal_period"]]
            field = "eps_estimate_average"
        try:
            provider_value = float(matches[0][field]) if len(matches) == 1 else float("nan")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Fact does not match a raw provider quarterly EPS row") from exc
        if not math.isfinite(provider_value) or provider_value != float(fact["value"]):
            raise ValueError("Fact does not match a raw provider quarterly EPS row")
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
            matches = [row for row in payload.get("estimates", []) if isinstance(row, dict)
                       and row.get("horizon") == "fiscal quarter" and row.get("date") == fiscal_period]
            field = "eps_estimate_average"
        else:
            if source["dataset"] != "EARNINGS":
                raise ValueError("Actual EPS requires an EARNINGS source")
            matches = [row for row in payload.get("quarterlyEarnings", []) if isinstance(row, dict)
                       and row.get("fiscalDateEnding") == fiscal_period]
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

    def record_event(self, event: dict) -> None:
        """Keep only fully screened events bound to stored pre-announcement facts."""
        from grande_alpha.data.earnings import FIELDS, _screen

        if not isinstance(event, dict) or set(event) != FIELDS or not self.verify_event(event):
            raise ValueError("Earnings event is incomplete or lacks point-in-time provider evidence")
        _screen(event, datetime.now(UTC), {"min_surprise_bps": 1, "min_momentum_bps": 1,
                                           "max_spread_bps": 10_000, "max_event_age_days": 10_000,
                                           "max_quote_age_seconds": 10_000})
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.db:
            self.db.execute("INSERT INTO earnings_events(event_id,symbol,announced_at,payload) "
                            "VALUES(?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET payload=excluded.payload",
                            (event["event_id"], event["symbol"], event["announced_at"], payload))

    def events_for_symbols(self, symbols: set[str]) -> list[dict]:
        if not symbols:
            return []
        if len(symbols) > 100 or any(not symbol_valid(symbol) for symbol in symbols):
            raise ValueError("A bounded set of exact symbols is required")
        placeholders = ",".join("?" for _ in symbols)
        rows = self.db.execute(
            f"SELECT payload FROM earnings_events WHERE symbol IN ({placeholders}) "
            "ORDER BY announced_at DESC,event_id", tuple(sorted(symbols)),
        ).fetchall()
        seen = set()
        events = []
        for row in rows:
            event = json.loads(row["payload"])
            if event["symbol"] not in seen and self.verify_event(event):
                events.append(event)
                seen.add(event["symbol"])
        return events

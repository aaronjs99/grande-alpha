import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from grande_alpha.data.earnings_feed import EarningsObservationStore

NOW = datetime(2026, 9, 23, 18, tzinfo=UTC)


def observation(dataset, payload, observed_at=NOW):
    raw = json.dumps({"provider": "alpha_vantage", "dataset": dataset, "symbol": "AAPL",
                      "payload": payload}, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"provider": "alpha_vantage", "dataset": dataset, "symbol": "AAPL",
            "observed_at": observed_at.isoformat(), "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "payload": payload}


def test_fact_availability_cannot_precede_raw_provider_observation(tmp_path):
    store = EarningsObservationStore(tmp_path / "earnings.db")
    try:
        raw = observation("EARNINGS_ESTIMATES", {"estimates": []})
        store.record(raw)
        with pytest.raises(ValueError, match="observation"):
            store.record_fact({"kind": "consensus", "symbol": "AAPL", "fiscal_period": "2026-06-30",
                               "basis": "diluted_eps", "currency": "USD", "value": 1.2,
                               "available_at": (NOW - timedelta(days=1)).isoformat(),
                               "source_sha256": raw["sha256"]})
    finally:
        store.close()


def test_fact_cannot_claim_a_different_symbol_from_its_raw_source(tmp_path):
    store = EarningsObservationStore(tmp_path / "earnings.db")
    try:
        raw = observation("EARNINGS", {"quarterlyEarnings": []})
        store.record(raw)
        with pytest.raises(ValueError, match="symbol"):
            store.record_fact({"kind": "actual", "symbol": "MSFT", "fiscal_period": "2026-06-30",
                               "basis": "alpha_vantage_eps_unspecified", "currency": "USD", "value": 1.5,
                               "available_at": NOW.isoformat(), "source_sha256": raw["sha256"]})
    finally:
        store.close()


def test_request_budget_cannot_be_raised_above_provider_free_limit(tmp_path):
    store = EarningsObservationStore(tmp_path / "earnings.db")
    try:
        with pytest.raises(ValueError, match="25"):
            store.reserve_request("AAPL", "EARNINGS", now=NOW, max_requests_per_24h=26)
        assert store.db.execute("SELECT COUNT(*) FROM earnings_provider_requests").fetchone()[0] == 0
    finally:
        store.close()


def test_fact_value_must_match_its_raw_provider_row(tmp_path):
    store = EarningsObservationStore(tmp_path / "earnings.db")
    try:
        raw = observation("EARNINGS", {"quarterlyEarnings": [
            {"fiscalDateEnding": "2026-06-30", "reportedEPS": "1.50"}]})
        store.record(raw)
        with pytest.raises(ValueError, match="raw provider"):
            store.record_fact({"kind": "actual", "symbol": "AAPL", "fiscal_period": "2026-06-30",
                               "basis": "alpha_vantage_eps_unspecified", "currency": "USD", "value": 2.0,
                               "available_at": NOW.isoformat(), "source_sha256": raw["sha256"]})
        assert store.db.execute("SELECT COUNT(*) FROM earnings_facts").fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_refresh_collects_both_datasets_and_normalizes_quarterly_facts(tmp_path):
    payloads = {
        "EARNINGS": {"quarterlyEarnings": [{"fiscalDateEnding": "2026-06-30", "reportedEPS": "1.50"}]},
        "EARNINGS_ESTIMATES": {"estimates": [{"date": "2026-06-30", "horizon": "fiscal quarter",
                                               "eps_estimate_average": "1.20"}]},
    }

    class Client:
        calls = 0

        async def fetch(self, symbol, dataset):
            self.calls += 1
            return observation(dataset, payloads[dataset])

    client = Client()
    path = tmp_path / "earnings.db"
    store = EarningsObservationStore(path)
    try:
        coverage = await store.refresh_observations({"AAPL"}, client, now=NOW)
        assert coverage["AAPL"]["EARNINGS"]["status"] == "observed"
        assert coverage["AAPL"]["EARNINGS_ESTIMATES"]["status"] == "observed"
        assert coverage["AAPL"]["EARNINGS"]["quarterly_facts"] == 1
        assert coverage["AAPL"]["EARNINGS_ESTIMATES"]["quarterly_facts"] == 1
        assert client.calls == 2
        facts = store.db.execute("SELECT kind,value,available_at FROM earnings_facts ORDER BY kind").fetchall()
        assert [(row["kind"], row["value"], row["available_at"]) for row in facts] == [
            ("actual", 1.5, NOW.isoformat()), ("consensus", 1.2, NOW.isoformat())]
        assert store.has_preannouncement_consensus("AAPL", NOW - timedelta(hours=1),
                                                   coverage["AAPL"]["EARNINGS_ESTIMATES"]["sha256"]) is False
        cached = await store.refresh_observations({"AAPL"}, client, now=NOW + timedelta(hours=1))
        assert cached["AAPL"]["EARNINGS"]["status"] == "cached"
        assert client.calls == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_refresh_reports_budget_exhaustion_without_inventing_facts(tmp_path):
    class Client:
        async def fetch(self, symbol, dataset):
            return observation(dataset, {"quarterlyEarnings": []})

    store = EarningsObservationStore(tmp_path / "earnings.db")
    try:
        coverage = await store.refresh_observations({"AAPL"}, Client(), now=NOW,
                                                    max_requests_per_24h=1)
        assert coverage["AAPL"]["EARNINGS"]["status"] == "observed"
        assert coverage["AAPL"]["EARNINGS_ESTIMATES"]["status"] == "budget_exhausted"
        assert store.db.execute("SELECT COUNT(*) FROM earnings_facts").fetchone()[0] == 0
        assert store.db.execute("SELECT COUNT(*) FROM earnings_provider_requests").fetchone()[0] == 1
    finally:
        store.close()

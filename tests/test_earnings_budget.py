import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from grande_alpha.data.earnings_feed import EarningsObservationStore

NOW = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)


def test_request_budget_is_durable_across_store_restarts(tmp_path):
    path = tmp_path / "earnings.db"
    first = EarningsObservationStore(path)
    assert first.reserve_request("AAPL", "EARNINGS", now=NOW, max_requests_per_24h=2) == 1
    first.close()
    second = EarningsObservationStore(path)
    try:
        assert second.reserve_request("MSFT", "EARNINGS", now=NOW, max_requests_per_24h=2) == 0
        with pytest.raises(RuntimeError, match="budget is exhausted"):
            second.reserve_request("NVDA", "EARNINGS", now=NOW, max_requests_per_24h=2)
        assert second.reserve_request("NVDA", "EARNINGS", now=NOW + timedelta(hours=25),
                                      max_requests_per_24h=2) == 1
    finally:
        second.close()


@pytest.mark.asyncio
async def test_failed_fetch_consumes_budget_and_does_not_create_an_observation(tmp_path):
    class FailingClient:
        async def fetch(self, symbol, dataset):
            raise ConnectionError("provider unavailable")

    store = EarningsObservationStore(tmp_path / "earnings.db")
    try:
        with pytest.raises(ConnectionError):
            await store.fetch_cached(FailingClient(), "AAPL", "EARNINGS", now=NOW,
                                     max_requests_per_24h=1)
        assert store.cached_observation("AAPL", "EARNINGS", now=NOW,
                                        max_age=timedelta(days=1)) is None
        with pytest.raises(RuntimeError, match="budget is exhausted"):
            await store.fetch_cached(FailingClient(), "AAPL", "EARNINGS", now=NOW,
                                     max_requests_per_24h=1)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_identical_refetch_renews_cache_without_rewriting_raw_observation(tmp_path):
    payload = {"quarterlyEarnings": []}
    canonical = json.dumps({"provider": "alpha_vantage", "dataset": "EARNINGS",
                            "symbol": "AAPL", "payload": payload},
                           sort_keys=True, separators=(",", ":"), allow_nan=False)

    class SameResponse:
        calls = 0

        async def fetch(self, symbol, dataset):
            self.calls += 1
            return {"provider": "alpha_vantage", "dataset": dataset, "symbol": symbol,
                    "observed_at": (NOW + timedelta(hours=25 * (self.calls - 1))).isoformat(),
                    "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                    "payload": payload}

    client = SameResponse()
    store = EarningsObservationStore(tmp_path / "earnings.db")
    try:
        await store.fetch_cached(client, "AAPL", "EARNINGS", now=NOW)
        refreshed, fetched_from_cache, _ = await store.fetch_cached(
            client, "AAPL", "EARNINGS", now=NOW + timedelta(hours=25))
        assert fetched_from_cache is False
        assert refreshed["observed_at"] == NOW.isoformat()
        result, cached, _ = await store.fetch_cached(client, "AAPL", "EARNINGS",
                                                      now=NOW + timedelta(hours=26))
        assert client.calls == 2
        assert cached is True
        assert result["observed_at"] == NOW.isoformat()
    finally:
        store.close()

from datetime import UTC, datetime, timedelta

import pytest

import grande_alpha.live_data as live_data
from grande_alpha.earnings_feed import EarningsObservationStore
from grande_alpha.equity_execution import EquityScope
from grande_alpha.mixed_portfolio import AllocationPolicy, plan
from grande_alpha.models import Quote

NOW = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)


class QuoteBroker:
    async def get_quotes(self, symbols):
        assert "QQQ" in symbols
        return {"QQQ": Quote("QQQ", 499.99, 500.01, 500.0, NOW, NOW, NOW)}


@pytest.mark.asyncio
async def test_missing_earnings_and_market_history_keep_allocation_in_cash(tmp_path, monkeypatch):
    monkeypatch.setattr(live_data, "utc_now", lambda: NOW)
    earnings = EarningsObservationStore(tmp_path / "earnings.db")
    scope = EquityScope("account-1", ("AAPL", "TQQQ", "SQQQ"), NOW, None,
                        20, 100, 100, 25, 5, 8, 20)
    policy = AllocationPolicy()
    thresholds = {"min_surprise_bps": 1, "min_momentum_bps": 1, "max_spread_bps": 20,
                  "max_event_age_days": 10, "max_quote_age_seconds": 8}
    source = live_data.LiveDataService(tmp_path / "market.db", QuoteBroker(), earnings,
                                       scope, policy, thresholds)
    try:
        snapshot = await source.snapshot()
        report = plan(snapshot["request"])
        assert report["targets_usd"] == {}
        assert report["cash_target_usd"] == 1.0
        assert snapshot["coverage"]["market_history_ready"] is False
        assert "AAPL" in snapshot["coverage"]["missing"]
    finally:
        source.close()
        earnings.close()


@pytest.mark.asyncio
async def test_missing_stock_event_does_not_increase_etf_cap(tmp_path, monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(live_data, "utc_now", lambda: clock["now"])

    class TrendingBroker:
        async def get_quotes(self, _symbols):
            minute = int((clock["now"] - NOW).total_seconds() // 60)
            mid = 500 * (1 + minute / 6000)
            return {"QQQ": Quote("QQQ", mid - .01, mid + .01, mid,
                                  clock["now"], clock["now"], clock["now"])}

    earnings = EarningsObservationStore(tmp_path / "earnings.db")
    scope = EquityScope("account-1", ("AAPL", "TQQQ", "SQQQ"), NOW, None,
                        20, 100, 100, 25, 5, 8, 20)
    thresholds = {"min_surprise_bps": 1, "min_momentum_bps": 1, "max_spread_bps": 20,
                  "max_event_age_days": 10, "max_quote_age_seconds": 8}
    source = live_data.LiveDataService(tmp_path / "market.db", TrendingBroker(), earnings,
                                       scope, AllocationPolicy(), thresholds)
    try:
        snapshot = None
        for index in range(6):
            clock["now"] = NOW + timedelta(minutes=6 * index)
            snapshot = await source.snapshot()
        targets = plan(snapshot["request"])["targets_usd"]
        assert set(targets) == {"TQQQ"}
        assert 0 < targets["TQQQ"] <= .2
        assert "AAPL" in snapshot["coverage"]["missing"]
    finally:
        source.close()
        earnings.close()

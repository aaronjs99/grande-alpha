from datetime import UTC, datetime, timedelta

import pytest

from grande_alpha.models import Bar, Quote, Regime
from grande_alpha.strategy import (
    BarBuilder,
    CloseMomentumStrategy,
    ConservativeEnsembleStrategy,
    FirstHalfHourMomentumStrategy,
    MomentumStrategy,
    MultiHorizonTrendStrategy,
    OpeningRangeBreakoutStrategy,
    StrategyConfig,
    build_strategy,
    ema,
)


def test_ema_tracks_recent_values() -> None:
    assert ema([1.0, 2.0, 3.0], 2) > 2.0


def test_bar_builder_emits_completed_bar() -> None:
    builder = BarBuilder(seconds=60)
    start = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    assert builder.update(Quote("QQQ", 100.0, 100.2, 100.1, start)) is None
    assert builder.update(Quote("QQQ", 100.1, 100.3, 100.2, start + timedelta(seconds=30))) is None
    bar = builder.update(Quote("QQQ", 100.3, 100.5, 100.4, start + timedelta(seconds=60)))
    assert bar is not None
    assert bar.open == 100.1
    assert bar.close == pytest.approx(100.2)
    assert bar.high == pytest.approx(100.2)
    assert bar.samples == 2


def _bars(prices: list[float]) -> list[Bar]:
    start = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    return [
        Bar("QQQ", start + timedelta(minutes=index), price, price, price, price, 10)
        for index, price in enumerate(prices)
    ]


def test_rising_market_generates_bullish_regime() -> None:
    strategy = MomentumStrategy(
        StrategyConfig(warmup_bars=10, fast_ema=3, slow_ema=8, trend_threshold_bps=1, momentum_bars=2)
    )
    signal = None
    for bar in _bars([100.0 + index * 0.1 for index in range(20)]):
        signal = strategy.on_bar(bar)
    assert signal is not None
    assert signal.regime == Regime.BULLISH


def test_flat_market_stays_flat() -> None:
    strategy = MomentumStrategy(
        StrategyConfig(warmup_bars=10, fast_ema=3, slow_ema=8, trend_threshold_bps=2, momentum_bars=2)
    )
    signal = None
    for bar in _bars([100.0] * 20):
        signal = strategy.on_bar(bar)
    assert signal is not None
    assert signal.regime == Regime.FLAT


def test_multi_horizon_trend_requires_all_horizons_to_agree() -> None:
    strategy = MultiHorizonTrendStrategy(
        StrategyConfig(
            strategy_name="multi_horizon_trend",
            warmup_bars=10,
            fast_ema=3,
            slow_ema=8,
            trend_short_bars=2,
            trend_medium_bars=5,
            trend_long_bars=9,
            trend_threshold_bps=1,
        )
    )
    signal = None
    for bar in _bars([100.0 + index * 0.1 for index in range(15)]):
        signal = strategy.on_bar(bar)
    assert signal and signal.regime == Regime.BULLISH


def test_close_momentum_is_inactive_until_final_half_hour() -> None:
    strategy = CloseMomentumStrategy(StrategyConfig(strategy_name="close_momentum"))
    open_bar = Bar("QQQ", datetime(2026, 8, 10, 13, 30, tzinfo=UTC), 100, 100, 100, 100, 10)
    before_close = Bar("QQQ", datetime(2026, 8, 10, 19, 29, tzinfo=UTC), 102, 102, 102, 102, 10)
    close_window = Bar("QQQ", datetime(2026, 8, 10, 19, 30, tzinfo=UTC), 102, 102, 102, 102, 10)
    strategy.on_bar(open_bar)
    assert strategy.on_bar(before_close).regime == Regime.FLAT
    assert strategy.on_bar(close_window).regime == Regime.BULLISH


def test_first_half_hour_momentum_uses_prior_close_and_fixed_morning_signal() -> None:
    strategy = FirstHalfHourMomentumStrategy(
        StrategyConfig(strategy_name="first_half_hour_momentum", close_momentum_bps=15)
    )
    prior_close = Bar("QQQ", datetime(2026, 8, 7, 19, 55, tzinfo=UTC), 100, 100, 100, 100, 10)
    morning_open = Bar("QQQ", datetime(2026, 8, 10, 13, 30, tzinfo=UTC), 100, 101, 100, 101, 10)
    morning_end = Bar("QQQ", datetime(2026, 8, 10, 13, 55, tzinfo=UTC), 101, 102, 101, 102, 10)
    midday_reversal = Bar("QQQ", datetime(2026, 8, 10, 18, 0, tzinfo=UTC), 95, 95, 94, 94, 10)
    close_window = Bar("QQQ", datetime(2026, 8, 10, 19, 30, tzinfo=UTC), 94, 94, 93, 93, 10)

    strategy.on_bar(prior_close)
    strategy.on_bar(morning_open)
    strategy.on_bar(morning_end)
    assert strategy.on_bar(midday_reversal).regime == Regime.FLAT
    signal = strategy.on_bar(close_window)
    assert signal.regime == Regime.BULLISH
    assert "first-half-hour" in signal.reason.lower()


def test_opening_breakout_uses_only_completed_opening_range() -> None:
    strategy = OpeningRangeBreakoutStrategy(
        StrategyConfig(strategy_name="opening_breakout", opening_range_minutes=30)
    )
    first = Bar("QQQ", datetime(2026, 8, 10, 13, 30, tzinfo=UTC), 100, 101, 99, 100, 10)
    last_range = Bar("QQQ", datetime(2026, 8, 10, 13, 59, tzinfo=UTC), 100, 102, 99.5, 101, 10)
    breakout = Bar("QQQ", datetime(2026, 8, 10, 14, 0, tzinfo=UTC), 102, 103, 102, 103, 10)
    assert strategy.on_bar(first).regime == Regime.FLAT
    assert strategy.on_bar(last_range).regime == Regime.FLAT
    assert strategy.on_bar(breakout).regime == Regime.BULLISH


def test_conservative_ensemble_needs_multiple_votes() -> None:
    config = StrategyConfig(
        strategy_name="conservative_ensemble",
        warmup_bars=10,
        fast_ema=3,
        slow_ema=8,
        trend_short_bars=2,
        trend_medium_bars=5,
        trend_long_bars=9,
        trend_threshold_bps=1,
        ensemble_min_votes=2,
    )
    strategy = ConservativeEnsembleStrategy(config)
    signal = None
    for bar in _bars([100.0 + index * 0.1 for index in range(15)]):
        signal = strategy.on_bar(bar)
    assert signal and signal.regime == Regime.BULLISH
    assert "votes" in signal.reason


def test_strategy_factory_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        build_strategy(StrategyConfig(strategy_name="unregistered"))

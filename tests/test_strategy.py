from datetime import UTC, datetime, timedelta

import pytest

from momentum_trader.models import Bar, Quote, Regime
from momentum_trader.strategy import BarBuilder, MomentumStrategy, StrategyConfig, ema


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

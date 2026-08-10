from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from grande_alpha.models import Bar, Quote, Regime, Signal, utc_now

EASTERN = ZoneInfo("America/New_York")
STRATEGY_NAMES = {
    "ema_momentum": "EMA momentum",
    "multi_horizon_trend": "Multi-horizon trend",
    "close_momentum": "Closing-window momentum",
    "opening_breakout": "Opening-range breakout",
    "conservative_ensemble": "Conservative agreement ensemble",
}


class BarBuilder:
    def __init__(self, symbol: str = "QQQ", seconds: int = 60) -> None:
        self.symbol = symbol
        self.seconds = seconds
        self._bucket: datetime | None = None
        self._prices: list[float] = []

    def update(self, quote: Quote) -> Bar | None:
        if quote.symbol != self.symbol:
            return None
        epoch = int(quote.timestamp.timestamp())
        bucket_epoch = epoch - (epoch % self.seconds)
        bucket = datetime.fromtimestamp(bucket_epoch, tz=quote.timestamp.tzinfo)
        completed: Bar | None = None
        if self._bucket is not None and bucket > self._bucket and self._prices:
            completed = Bar(
                symbol=self.symbol,
                start=self._bucket,
                open=self._prices[0],
                high=max(self._prices),
                low=min(self._prices),
                close=self._prices[-1],
                samples=len(self._prices),
            )
            self._prices = []
        if self._bucket is None or bucket >= self._bucket:
            self._bucket = bucket
            self._prices.append(quote.mid)
        return completed


def ema(values: list[float], period: int) -> float:
    if not values:
        raise ValueError("EMA requires at least one value")
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


@dataclass
class StrategyConfig:
    strategy_name: str = "ema_momentum"
    warmup_bars: int = 24
    fast_ema: int = 8
    slow_ema: int = 21
    trend_threshold_bps: float = 4.0
    momentum_bars: int = 3
    trend_short_bars: int = 3
    trend_medium_bars: int = 12
    trend_long_bars: int = 36
    close_momentum_bps: float = 15.0
    opening_range_minutes: int = 30
    breakout_buffer_bps: float = 3.0
    ensemble_min_votes: int = 2

    def validate(self) -> None:
        if self.strategy_name not in STRATEGY_NAMES:
            raise ValueError(f"Unknown strategy: {self.strategy_name}")
        if self.fast_ema < 1 or self.slow_ema < 2 or self.fast_ema >= self.slow_ema:
            raise ValueError("Fast EMA must be positive and smaller than slow EMA")
        horizons = (self.trend_short_bars, self.trend_medium_bars, self.trend_long_bars)
        if not (0 < horizons[0] < horizons[1] < horizons[2]):
            raise ValueError("Trend horizons must be positive and strictly increasing")
        if self.close_momentum_bps <= 0 or self.breakout_buffer_bps < 0:
            raise ValueError("Momentum threshold must be positive and breakout buffer nonnegative")
        if not 5 <= self.opening_range_minutes <= 120:
            raise ValueError("Opening range must be between 5 and 120 minutes")
        if not 1 <= self.ensemble_min_votes <= 4:
            raise ValueError("Ensemble vote threshold must be between 1 and 4")


class SignalStrategy(Protocol):
    last_signal: Signal

    def on_bar(self, bar: Bar) -> Signal: ...

    def reset(self) -> None: ...


class MomentumStrategy:
    def __init__(self, config: StrategyConfig) -> None:
        config.validate()
        self.config = config
        self.bars: deque[Bar] = deque(maxlen=max(300, config.slow_ema * 5))
        self.last_signal = Signal(Regime.FLAT, 0.0, "Waiting for warm-up data")

    def on_bar(self, bar: Bar) -> Signal:
        self.bars.append(bar)
        closes = [item.close for item in self.bars]
        required = max(self.config.warmup_bars, self.config.slow_ema + 2, self.config.momentum_bars + 1)
        if len(closes) < required:
            self.last_signal = Signal(
                Regime.FLAT,
                0.0,
                f"Warm-up {len(closes)}/{required} completed bars",
                timestamp=bar.start,
            )
            return self.last_signal

        fast = ema(closes[-self.config.slow_ema * 3 :], self.config.fast_ema)
        slow = ema(closes[-self.config.slow_ema * 3 :], self.config.slow_ema)
        separation_bps = (fast - slow) / slow * 10_000
        momentum_bps = (
            (closes[-1] - closes[-1 - self.config.momentum_bars])
            / closes[-1 - self.config.momentum_bars]
            * 10_000
        )
        threshold = self.config.trend_threshold_bps

        if separation_bps >= threshold and momentum_bps > 0:
            regime = Regime.BULLISH
        elif separation_bps <= -threshold and momentum_bps < 0:
            regime = Regime.BEARISH
        else:
            regime = Regime.FLAT
        confidence = min(1.0, abs(separation_bps) / max(threshold * 4.0, 1.0))
        reason = (
            f"QQQ EMA separation {separation_bps:+.1f} bps; "
            f"{self.config.momentum_bars}-bar momentum {momentum_bps:+.1f} bps"
        )
        self.last_signal = Signal(regime, confidence, reason, timestamp=bar.start)
        return self.last_signal

    def reset(self) -> None:
        self.bars.clear()
        self.last_signal = Signal(Regime.FLAT, 0.0, "Strategy reset", timestamp=utc_now())


class MultiHorizonTrendStrategy:
    """Causal trend agreement across three horizons; disagreement means cash."""

    def __init__(self, config: StrategyConfig) -> None:
        config.validate()
        self.config = config
        self.bars: deque[Bar] = deque(maxlen=max(500, config.trend_long_bars * 3))
        self.last_signal = Signal(Regime.FLAT, 0.0, "Waiting for trend horizons")

    def on_bar(self, bar: Bar) -> Signal:
        self.bars.append(bar)
        closes = [item.close for item in self.bars]
        required = max(self.config.warmup_bars, self.config.trend_long_bars + 1)
        if len(closes) < required:
            self.last_signal = Signal(
                Regime.FLAT,
                0.0,
                f"Trend warm-up {len(closes)}/{required} bars",
                timestamp=bar.start,
            )
            return self.last_signal
        horizons = (
            self.config.trend_short_bars,
            self.config.trend_medium_bars,
            self.config.trend_long_bars,
        )
        returns_bps = [(closes[-1] / closes[-1 - horizon] - 1.0) * 10_000 for horizon in horizons]
        threshold = self.config.trend_threshold_bps
        if all(value >= threshold for value in returns_bps):
            regime = Regime.BULLISH
        elif all(value <= -threshold for value in returns_bps):
            regime = Regime.BEARISH
        else:
            regime = Regime.FLAT
        weakest = min(abs(value) for value in returns_bps)
        confidence = min(1.0, weakest / max(threshold * 4.0, 1.0))
        reason = "QQQ multi-horizon returns " + "/".join(f"{value:+.1f}" for value in returns_bps) + " bps"
        self.last_signal = Signal(regime, confidence, reason, timestamp=bar.start)
        return self.last_signal

    def reset(self) -> None:
        self.bars.clear()
        self.last_signal = Signal(Regime.FLAT, 0.0, "Strategy reset", timestamp=utc_now())


class CloseMomentumStrategy:
    """Trades only the closing window using the completed rest-of-day QQQ return."""

    def __init__(self, config: StrategyConfig) -> None:
        config.validate()
        self.config = config
        self.session_date = None
        self.session_open: float | None = None
        self.last_signal = Signal(Regime.FLAT, 0.0, "Waiting for closing window")

    def on_bar(self, bar: Bar) -> Signal:
        local = bar.start.astimezone(EASTERN)
        if self.session_date != local.date():
            self.session_date = local.date()
            self.session_open = bar.open
        assert self.session_open is not None
        minute = local.hour * 60 + local.minute
        close_window_start = 15 * 60 + 30
        if minute < close_window_start or minute >= 16 * 60:
            regime = Regime.FLAT
            return_bps = (bar.close / self.session_open - 1.0) * 10_000
            reason = f"Closing window inactive; rest-of-day return {return_bps:+.1f} bps"
            confidence = 0.0
        else:
            return_bps = (bar.close / self.session_open - 1.0) * 10_000
            threshold = self.config.close_momentum_bps
            if return_bps >= threshold:
                regime = Regime.BULLISH
            elif return_bps <= -threshold:
                regime = Regime.BEARISH
            else:
                regime = Regime.FLAT
            confidence = min(1.0, abs(return_bps) / max(threshold * 4.0, 1.0))
            reason = f"QQQ rest-of-day return {return_bps:+.1f} bps in closing window"
        self.last_signal = Signal(regime, confidence, reason, timestamp=bar.start)
        return self.last_signal

    def reset(self) -> None:
        self.session_date = None
        self.session_open = None
        self.last_signal = Signal(Regime.FLAT, 0.0, "Strategy reset", timestamp=utc_now())


class OpeningRangeBreakoutStrategy:
    """Causal breakout using only the completed opening range."""

    def __init__(self, config: StrategyConfig) -> None:
        config.validate()
        self.config = config
        self.session_date = None
        self.range_high: float | None = None
        self.range_low: float | None = None
        self.last_signal = Signal(Regime.FLAT, 0.0, "Building opening range")

    def on_bar(self, bar: Bar) -> Signal:
        local = bar.start.astimezone(EASTERN)
        if self.session_date != local.date():
            self.session_date = local.date()
            self.range_high = None
            self.range_low = None
        minute = local.hour * 60 + local.minute
        open_minute = 9 * 60 + 30
        cutoff = open_minute + self.config.opening_range_minutes
        if open_minute <= minute < cutoff:
            self.range_high = bar.high if self.range_high is None else max(self.range_high, bar.high)
            self.range_low = bar.low if self.range_low is None else min(self.range_low, bar.low)
            regime, confidence, reason = Regime.FLAT, 0.0, "Building completed-bar opening range"
        elif self.range_high is None or self.range_low is None or minute >= 16 * 60:
            regime, confidence, reason = Regime.FLAT, 0.0, "Opening range unavailable"
        else:
            buffer = self.config.breakout_buffer_bps / 10_000
            upper = self.range_high * (1.0 + buffer)
            lower = self.range_low * (1.0 - buffer)
            if bar.close > upper:
                regime = Regime.BULLISH
                distance_bps = (bar.close / upper - 1.0) * 10_000
            elif bar.close < lower:
                regime = Regime.BEARISH
                distance_bps = (lower / bar.close - 1.0) * 10_000
            else:
                regime = Regime.FLAT
                distance_bps = 0.0
            confidence = min(1.0, distance_bps / max(self.config.breakout_buffer_bps * 4.0, 1.0))
            reason = f"QQQ opening range {self.range_low:.2f}-{self.range_high:.2f}; close {bar.close:.2f}"
        self.last_signal = Signal(regime, confidence, reason, timestamp=bar.start)
        return self.last_signal

    def reset(self) -> None:
        self.session_date = None
        self.range_high = None
        self.range_low = None
        self.last_signal = Signal(Regime.FLAT, 0.0, "Strategy reset", timestamp=utc_now())


class ConservativeEnsembleStrategy:
    """Agreement ensemble; component disagreement produces a flat signal."""

    def __init__(self, config: StrategyConfig) -> None:
        config.validate()
        self.config = config
        self.components: list[SignalStrategy] = [
            MomentumStrategy(config),
            MultiHorizonTrendStrategy(config),
            CloseMomentumStrategy(config),
            OpeningRangeBreakoutStrategy(config),
        ]
        self.last_signal = Signal(Regime.FLAT, 0.0, "Waiting for ensemble components")

    def on_bar(self, bar: Bar) -> Signal:
        signals = [component.on_bar(bar) for component in self.components]
        bullish = sum(signal.regime == Regime.BULLISH for signal in signals)
        bearish = sum(signal.regime == Regime.BEARISH for signal in signals)
        needed = self.config.ensemble_min_votes
        if bullish >= needed and bullish > bearish:
            regime, votes = Regime.BULLISH, bullish
        elif bearish >= needed and bearish > bullish:
            regime, votes = Regime.BEARISH, bearish
        else:
            regime, votes = Regime.FLAT, max(bullish, bearish)
        confidence = votes / len(signals) if regime != Regime.FLAT else 0.0
        reason = f"Ensemble votes bullish {bullish}, bearish {bearish}, required {needed}"
        self.last_signal = Signal(regime, confidence, reason, timestamp=bar.start)
        return self.last_signal

    def reset(self) -> None:
        for component in self.components:
            component.reset()
        self.last_signal = Signal(Regime.FLAT, 0.0, "Strategy reset", timestamp=utc_now())


def build_strategy(config: StrategyConfig) -> SignalStrategy:
    config.validate()
    factories = {
        "ema_momentum": MomentumStrategy,
        "multi_horizon_trend": MultiHorizonTrendStrategy,
        "close_momentum": CloseMomentumStrategy,
        "opening_breakout": OpeningRangeBreakoutStrategy,
        "conservative_ensemble": ConservativeEnsembleStrategy,
    }
    return factories[config.strategy_name](config)

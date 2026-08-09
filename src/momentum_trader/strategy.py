from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from momentum_trader.models import Bar, Quote, Regime, Signal, utc_now


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
    warmup_bars: int = 24
    fast_ema: int = 8
    slow_ema: int = 21
    trend_threshold_bps: float = 4.0
    momentum_bars: int = 3


class MomentumStrategy:
    def __init__(self, config: StrategyConfig) -> None:
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
                f"Warm-up {len(closes)}/{required} one-minute bars",
                timestamp=bar.start,
            )
            return self.last_signal

        fast = ema(closes[-self.config.slow_ema * 3 :], self.config.fast_ema)
        slow = ema(closes[-self.config.slow_ema * 3 :], self.config.slow_ema)
        separation_bps = (fast - slow) / slow * 10_000
        momentum_bps = (closes[-1] - closes[-1 - self.config.momentum_bars]) / closes[-1 - self.config.momentum_bars] * 10_000
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
            f"{self.config.momentum_bars}m momentum {momentum_bps:+.1f} bps"
        )
        self.last_signal = Signal(regime, confidence, reason, timestamp=bar.start)
        return self.last_signal

    def reset(self) -> None:
        self.bars.clear()
        self.last_signal = Signal(Regime.FLAT, 0.0, "Strategy reset", timestamp=utc_now())


from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import IntEnum

from grande_alpha.historical import HistoricalBundle


class TradeCommand(IntEnum):
    SELL = -1
    HOLD = 0
    BUY = 1


@dataclass(frozen=True, order=True)
class PairAction:
    """One of the exact nine order-command combinations for (TQQQS, SQQQS)."""

    t: TradeCommand
    s: TradeCommand

    @property
    def action_id(self) -> int:
        return 3 * (int(self.t) + 1) + (int(self.s) + 1)

    @property
    def label(self) -> str:
        return f"({int(self.t):+d},{int(self.s):+d})".replace("+0", "0")

    @property
    def net_directional_impulse(self) -> int:
        return int(self.t) - int(self.s)

    @classmethod
    def from_id(cls, action_id: int) -> PairAction:
        if not 0 <= action_id < 9:
            raise ValueError("Action id must be between 0 and 8")
        return cls(TradeCommand(action_id // 3 - 1), TradeCommand(action_id % 3 - 1))


ALL_PAIR_ACTIONS = tuple(PairAction.from_id(action_id) for action_id in range(9))


def valid_action_ids(t_units: int, s_units: int, max_units: int = 1) -> tuple[int, ...]:
    """Mask commands that would create unsupported long-only inventory."""

    if max_units < 1 or not 0 <= t_units <= max_units or not 0 <= s_units <= max_units:
        raise ValueError("Inventory must be inside the configured long-only unit bounds")
    valid = []
    for action in ALL_PAIR_ACTIONS:
        next_t = t_units + int(action.t)
        next_s = s_units + int(action.s)
        if 0 <= next_t <= max_units and 0 <= next_s <= max_units:
            valid.append(action.action_id)
    return tuple(valid)


def apply_action(t_units: int, s_units: int, action: PairAction, max_units: int = 1) -> tuple[int, int]:
    if action.action_id not in valid_action_ids(t_units, s_units, max_units):
        raise ValueError(f"Action {action.label} is invalid from inventory ({t_units},{s_units})")
    return t_units + int(action.t), s_units + int(action.s)


@dataclass(frozen=True)
class OfflineTrainingConfig:
    epochs: int = 80
    learning_rate: float = 0.15
    discount: float = 0.95
    exploration: float = 0.15
    train_fraction: float = 0.70
    transaction_cost_bps: float = 8.0
    leg_weight: float = 0.50
    trend_window: int = 20
    volatility_window: int = 20
    neutral_trend_bps: float = 10.0
    random_seed: int = 7007

    def validate(self) -> None:
        if self.epochs < 1 or not 0 < self.learning_rate <= 1 or not 0 <= self.discount <= 1:
            raise ValueError("Training epochs, learning rate, and discount are outside valid bounds")
        if not 0 <= self.exploration <= 1 or not 0.50 <= self.train_fraction <= 0.90:
            raise ValueError("Exploration or chronological training fraction is outside valid bounds")
        if self.transaction_cost_bps < 0 or not 0 < self.leg_weight <= 0.5:
            raise ValueError("Costs must be nonnegative and each leg weight must be in (0, 0.5]")
        if min(self.trend_window, self.volatility_window) < 2 or self.neutral_trend_bps < 0:
            raise ValueError("Feature windows must be at least two and trend threshold cannot be negative")


@dataclass(frozen=True)
class ActionAuditRow:
    timestamp: str
    action_id: int
    action_t: int
    action_s: int
    before_t: int
    before_s: int
    after_t: int
    after_s: int
    reward_pct: float
    equity: float
    split: str = "test"


@dataclass(frozen=True)
class OfflineTrainingResult:
    dataset_hash: str
    training_rows: int
    test_rows: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    test_return_pct: float
    test_max_drawdown_pct: float
    qqq_holdout_pct: float
    tqqq_holdout_pct: float
    sqqq_holdout_pct: float
    action_counts: dict[int, int]
    state_count: int
    audit_rows: list[ActionAuditRow]
    policy: dict[str, int]
    warnings: list[str] = field(default_factory=list)


def _feature_buckets(
    bundle: HistoricalBundle,
    config: OfflineTrainingConfig,
    volatility_cutoff: float,
) -> list[tuple[int, int]]:
    closes = [frame.qqq.close for frame in bundle.frames]
    threshold = config.neutral_trend_bps / 10_000.0
    buckets = []
    for index in range(len(bundle.frames)):
        start = max(0, index - config.trend_window)
        trend = closes[index] / closes[start] - 1.0
        trend_bucket = 1 if trend > threshold else (-1 if trend < -threshold else 0)
        returns = [
            closes[i] / closes[i - 1] - 1.0
            for i in range(max(1, index - config.volatility_window + 1), index + 1)
        ]
        realized = statistics.pstdev(returns) * math.sqrt(252) if len(returns) >= 2 else 0.0
        buckets.append((trend_bucket, int(realized > volatility_cutoff)))
    return buckets


def _daily_reward(bundle: HistoricalBundle, index: int, next_t: int, next_s: int, turnover: int, config: OfflineTrainingConfig) -> float:
    current = bundle.frames[index]
    following = bundle.frames[index + 1]
    # The state is observed at the current close, so a selected inventory earns only
    # the subsequent close-to-close return. This includes overnight exposure without
    # using the following open or any other future information in the decision.
    t_return = following.tqqq.close / current.tqqq.close - 1.0
    s_return = following.sqqq.close / current.sqqq.close - 1.0
    trading_cost = turnover * config.leg_weight * config.transaction_cost_bps / 10_000.0
    return config.leg_weight * (next_t * t_return + next_s * s_return) - trading_cost


def _greedy_action(values: list[float], valid: tuple[int, ...]) -> int:
    best = max(values[action_id] for action_id in valid)
    candidates = [action_id for action_id in valid if abs(values[action_id] - best) <= 1e-12]
    return 4 if 4 in candidates else min(candidates, key=lambda action_id: (abs(ALL_PAIR_ACTIONS[action_id].net_directional_impulse), action_id))


def train_offline_action_policy(
    bundle: HistoricalBundle,
    config: OfflineTrainingConfig | None = None,
) -> OfflineTrainingResult:
    """Train a small auditable Q table on daily bars and evaluate only on the later holdout."""

    settings = config or OfflineTrainingConfig()
    settings.validate()
    if bundle.interval != "1d":
        raise ValueError("The nine-action offline lab requires aligned daily bars")
    warmup = max(settings.trend_window, settings.volatility_window)
    if len(bundle.frames) < max(252, warmup + 30):
        raise ValueError("The offline action lab requires at least 252 aligned daily bars")

    transition_count = len(bundle.frames) - 1
    train_end = max(warmup + 1, int(transition_count * settings.train_fraction))
    if train_end >= transition_count - 20:
        raise ValueError("The chronological holdout is too short")

    closes = [frame.qqq.close for frame in bundle.frames]
    training_volatility = []
    for index in range(warmup, train_end):
        values = [closes[i] / closes[i - 1] - 1.0 for i in range(index - settings.volatility_window + 1, index + 1)]
        training_volatility.append(statistics.pstdev(values) * math.sqrt(252))
    volatility_cutoff = statistics.median(training_volatility)
    feature_buckets = _feature_buckets(bundle, settings, volatility_cutoff)

    q_values: defaultdict[tuple[int, int, int, int], list[float]] = defaultdict(lambda: [0.0] * 9)
    rng = random.Random(settings.random_seed)
    for epoch in range(settings.epochs):
        t_units = s_units = 0
        epsilon = settings.exploration * (1.0 - 0.8 * epoch / max(1, settings.epochs - 1))
        for index in range(warmup, train_end):
            state = (*feature_buckets[index], t_units, s_units)
            valid = valid_action_ids(t_units, s_units)
            action_id = rng.choice(valid) if rng.random() < epsilon else _greedy_action(q_values[state], valid)
            action = ALL_PAIR_ACTIONS[action_id]
            next_t, next_s = apply_action(t_units, s_units, action)
            turnover = abs(next_t - t_units) + abs(next_s - s_units)
            reward = _daily_reward(bundle, index, next_t, next_s, turnover, settings)
            next_state = (*feature_buckets[index + 1], next_t, next_s)
            next_valid = valid_action_ids(next_t, next_s)
            target = reward + settings.discount * max(q_values[next_state][candidate] for candidate in next_valid)
            q_values[state][action_id] += settings.learning_rate * (target - q_values[state][action_id])
            t_units, s_units = next_t, next_s

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    t_units = s_units = 0
    counts: Counter[int] = Counter()
    audit_rows = []
    for index in range(train_end, transition_count):
        state = (*feature_buckets[index], t_units, s_units)
        valid = valid_action_ids(t_units, s_units)
        action_id = _greedy_action(q_values[state], valid)
        action = ALL_PAIR_ACTIONS[action_id]
        next_t, next_s = apply_action(t_units, s_units, action)
        turnover = abs(next_t - t_units) + abs(next_s - s_units)
        reward = _daily_reward(bundle, index, next_t, next_s, turnover, settings)
        equity *= max(0.01, 1.0 + reward)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
        counts[action_id] += 1
        audit_rows.append(
            ActionAuditRow(
                bundle.frames[index + 1].start.isoformat(),
                action_id,
                int(action.t),
                int(action.s),
                t_units,
                s_units,
                next_t,
                next_s,
                reward * 100.0,
                equity,
            )
        )
        t_units, s_units = next_t, next_s

    policy = {
        f"trend={state[0]},vol={state[1]},T={state[2]},S={state[3]}": _greedy_action(
            values, valid_action_ids(state[2], state[3])
        )
        for state, values in sorted(q_values.items())
    }
    return OfflineTrainingResult(
        bundle.dataset_hash,
        train_end - warmup,
        transition_count - train_end,
        bundle.frames[warmup].start.date().isoformat(),
        bundle.frames[train_end].start.date().isoformat(),
        bundle.frames[train_end + 1].start.date().isoformat(),
        bundle.frames[-1].start.date().isoformat(),
        (equity - 1.0) * 100.0,
        max_drawdown * 100.0,
        (bundle.frames[-1].qqq.close / bundle.frames[train_end].qqq.close - 1.0) * 100.0,
        (bundle.frames[-1].tqqq.close / bundle.frames[train_end].tqqq.close - 1.0) * 100.0,
        (bundle.frames[-1].sqqq.close / bundle.frames[train_end].sqqq.close - 1.0) * 100.0,
        dict(sorted(counts.items())),
        len(q_values),
        audit_rows,
        policy,
        [
            "Offline reinforcement learning is an exploratory sandbox model, not evidence of future profit",
            "The holdout is chronological but one market history is still a small sample of regimes",
        ],
    )

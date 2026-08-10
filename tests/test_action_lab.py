from datetime import UTC, datetime, timedelta

from grande_alpha.action_lab import (
    ALL_PAIR_ACTIONS,
    PairAction,
    TradeCommand,
    apply_action,
    evaluate_daily_benchmarks,
    live_feasible_action_ids,
    pair_action_for_target,
    train_offline_action_policy,
    valid_action_ids,
)
from grande_alpha.historical import HistoricalBundle, ReplayFrame, assess_quality
from grande_alpha.models import Bar


def _bar(symbol: str, start: datetime, open_price: float, close_price: float) -> Bar:
    return Bar(symbol, start, open_price, max(open_price, close_price), min(open_price, close_price), close_price, 1, 1_000_000)


def _daily_bundle(rows: int = 320) -> HistoricalBundle:
    start = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    frames = []
    qqq = tqqq = sqqq = 100.0
    for index in range(rows):
        timestamp = start + timedelta(days=index)
        direction = 0.002 if (index // 35) % 2 == 0 else -0.0015
        q_close = qqq * (1 + direction)
        t_close = tqqq * (1 + 3 * direction)
        s_close = sqqq * (1 - 3 * direction)
        frames.append(
            ReplayFrame(
                timestamp,
                _bar("QQQ", timestamp, qqq, q_close),
                _bar("TQQQ", timestamp, tqqq, t_close),
                _bar("SQQQ", timestamp, sqqq, s_close),
            )
        )
        qqq, tqqq, sqqq = q_close, t_close, s_close
    quality = assess_quality(frames, "1d")
    return HistoricalBundle("unit-test daily", start, frames, "1d", quality.dataset_hash, quality)


def test_pair_action_space_contains_exact_nine_combinations() -> None:
    assert len(ALL_PAIR_ACTIONS) == 9
    assert {(int(action.t), int(action.s)) for action in ALL_PAIR_ACTIONS} == {
        (t, s) for t in (-1, 0, 1) for s in (-1, 0, 1)
    }
    assert PairAction(TradeCommand.SELL, TradeCommand.BUY).action_id == 2
    assert PairAction.from_id(2).label == "(-1,+1)"


def test_long_only_action_mask_depends_on_current_inventory() -> None:
    assert valid_action_ids(0, 0) == (4, 5, 7, 8)
    assert valid_action_ids(1, 1) == (0, 1, 3, 4)
    assert apply_action(1, 0, PairAction(TradeCommand.SELL, TradeCommand.BUY)) == (0, 1)


def test_live_targets_are_expressed_in_the_exact_pair_action_vocabulary() -> None:
    assert pair_action_for_target(0, 0, "TQQQ").label == "(+1,0)"
    assert pair_action_for_target(1, 0, "SQQQ").label == "(-1,+1)"
    assert pair_action_for_target(0, 1, "TQQQ").label == "(+1,-1)"
    assert pair_action_for_target(1, 1, None).label == "(-1,-1)"
    assert live_feasible_action_ids(0, 0) == (4, 5, 7)
    assert 8 not in live_feasible_action_ids(0, 0)


def test_offline_action_policy_uses_chronological_holdout_and_audits_actions() -> None:
    result = train_offline_action_policy(_daily_bundle())

    assert result.training_rows > result.test_rows > 20
    assert result.audit_rows
    assert sum(result.action_counts.values()) == result.test_rows
    assert all(row.action_id in valid_action_ids(row.before_t, row.before_s) for row in result.audit_rows)
    assert result.test_start > result.train_start
    assert result.state_count > 0


def test_daily_benchmarks_include_causal_volatility_managed_policies() -> None:
    results = evaluate_daily_benchmarks(_daily_bundle())
    by_name = {result.name: result for result in results}

    assert by_name["Cash"].return_pct == 0
    assert "Vol-managed TQQQ 20%" in by_name
    assert "Vol-managed + QQQ SMA200 20%" in by_name
    assert all(result.max_drawdown_pct >= 0 for result in results)

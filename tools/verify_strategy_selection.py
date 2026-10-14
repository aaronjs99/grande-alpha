"""Offline strategy checks; deterministic prices and disposable data, never orders."""

from __future__ import annotations

import asyncio
import math
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from grande_alpha.domain.market_models import Bar, Regime, Signal
from grande_alpha.domain.policy import market_session_allowed
from grande_alpha.research.bar_replay import SandboxReplayEngine
from grande_alpha.research.evidence_contract import strategy_fingerprint
from grande_alpha.research.evidence_statistics import cost_stressed_config
from grande_alpha.research.historical import assess_quality, dataset_hash
from grande_alpha.research.historical_models import HistoricalBundle, ReplayFrame
from grande_alpha.research.historical_source import deterministic_demo
from grande_alpha.research.sandbox_models import SandboxConfig
from grande_alpha.research.strategy_selection import SHORTLIST, compare_strategies
from grande_alpha.strategy.core import StrategyConfig, build_strategy


def bars(changes):
    start, price = datetime(2026, 1, 5, 15, tzinfo=UTC), 100.0
    result = []
    for i, change in enumerate(changes):
        opening = price
        price *= math.exp(change / 10_000)
        result.append(Bar("QQQ", start + timedelta(minutes=i), opening,
                          max(opening, price), min(opening, price), price, 1, 10000))
    return result


class TrendChecks(unittest.TestCase):
    def signal(self, changes, costs=10):
        strategy = build_strategy(StrategyConfig(strategy_name="cost_aware_trend", round_trip_cost_bps=costs))
        for bar in bars(changes):
            signal = strategy.on_bar(bar)
        return signal

    def test_trend_must_exceed_cost_and_noise_in_each_horizon(self):
        self.assertEqual(self.signal([0.5] * 45).regime, Regime.FLAT)
        self.assertEqual(self.signal([5] * 45).regime, Regime.BULLISH)
        self.assertEqual(self.signal([-5] * 45).regime, Regime.BEARISH)
        self.assertEqual(self.signal([5] * 45, costs=100).regime, Regime.FLAT)
        self.assertEqual(self.signal([60, -59] * 23).regime, Regime.FLAT)

    def test_signals_are_causal_and_reset_on_an_intraday_gap(self):
        config = StrategyConfig(strategy_name="cost_aware_trend")
        path = bars([5] * 60)
        first, second = build_strategy(config), build_strategy(config)
        prefix = [first.on_bar(bar) for bar in path[:40]]
        full = [second.on_bar(bar) for bar in path]
        self.assertEqual(prefix, full[:40])
        signal = second.on_bar(replace(path[-1], start=path[-1].start + timedelta(hours=2)))
        self.assertEqual(signal.regime, Regime.FLAT)
        self.assertIn("warm-up", signal.reason.lower())

    def test_cost_floor_includes_commission_spread_and_two_slippages(self):
        config = SandboxConfig(order_notional=25, commission_per_order=0.01,
                               base_spread_bps=4, slippage_bps=3)
        self.assertAlmostEqual(config.strategy_config().round_trip_cost_bps, 18)
        for invalid in (float("nan"), float("inf"), True, -1):
            with self.assertRaises(ValueError):
                build_strategy(StrategyConfig(strategy_name="cost_aware_trend", round_trip_cost_bps=invalid))

    def test_cost_stress_freezes_the_signal_hurdle_and_identity_binds_it(self):
        config = SandboxConfig(strategy_name="cost_aware_trend")
        stressed = cost_stressed_config(config, 3)
        self.assertEqual(config.strategy_config().round_trip_cost_bps, stressed.strategy_config().round_trip_cost_bps)
        self.assertNotEqual(config.slippage_bps, stressed.slippage_bps)
        self.assertNotEqual(strategy_fingerprint(config), strategy_fingerprint(replace(config, signal_cost_bps=25)))


class ComparisonChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundle = deterministic_demo(days=45)
        frames = [f for f in bundle.frames if market_session_allowed(f.start, 0, 0, "regular_hours")]
        quality = assess_quality(frames, bundle.interval)
        cls.bundle = replace(bundle, frames=frames, quality=quality, dataset_hash=quality.dataset_hash)

    def fake(self, bundle, config):
        # Returns depend only on strategy and cost, never on held-out observations.
        value = {"cash": 0, "ema_momentum": 0.8, "multi_horizon_trend": 0.4,
                 "opening_breakout": -0.2, "cost_aware_trend": 0.6}[config.strategy_name]
        value -= config.slippage_bps / 100 if config.strategy_name != "cash" else 0
        return SimpleNamespace(return_pct=value, max_drawdown_pct=0.2 if value else 0,
                               round_trips=40 if value else 0, total_execution_cost=0.5,
                               ending_position=None, execution_events=[],
                               daily_returns=[value / 100 / 5] * 5)

    def test_fixed_shortlist_and_development_only_choice(self):
        calls = []
        def replay(bundle, config):
            calls.append((bundle.start, config.strategy_name))
            return self.fake(bundle, config)
        result = compare_strategies(self.bundle, replay=replay)
        self.assertEqual(tuple(row["strategy"] for row in result["candidates"]), SHORTLIST)
        self.assertEqual(result["selected_for_holdout"], "ema_momentum")
        boundary = datetime.fromisoformat(result["split"]["holdout_start"])
        held_out_names = {name for start, name in calls if start >= boundary}
        self.assertEqual(held_out_names, {"ema_momentum", "cash"})
        self.assertFalse(result["live_approved"])
        self.assertEqual(result["recommendation"], "cash")  # Synthetic source cannot qualify.

    def test_holdout_failure_cannot_switch_to_another_strategy(self):
        cutoff = self.bundle.frames[int(len(self.bundle.frames) * 0.7)].start
        def replay(bundle, config):
            value = self.fake(bundle, config)
            return replace_namespace(value, return_pct=-1) if bundle.start >= cutoff and config.strategy_name != "cash" else value
        result = compare_strategies(self.bundle, replay=replay)
        self.assertEqual(result["selected_for_holdout"], "ema_momentum")
        self.assertEqual(result["recommendation"], "cash")
        self.assertLess(result["holdout"]["base"]["return_pct"], 0)

    def test_all_losing_candidates_choose_cash_without_holdout_search(self):
        def replay(bundle, config):
            value = self.fake(bundle, config)
            return replace_namespace(value, return_pct=-1) if config.strategy_name != "cash" else value
        result = compare_strategies(self.bundle, replay=replay)
        self.assertEqual(result["selected_for_holdout"], "cash")
        self.assertEqual(result["recommendation"], "cash")

    def test_short_or_daily_data_cannot_produce_a_ranking(self):
        with self.assertRaisesRegex(ValueError, "30"):
            compare_strategies(deterministic_demo(days=5), replay=self.fake)
        with self.assertRaisesRegex(ValueError, "intraday"):
            compare_strategies(replace(self.bundle, interval="1d"), replay=self.fake)

    def test_cancel_and_invalid_inputs_stop_without_evaluating(self):
        with self.assertRaises(InterruptedError):
            compare_strategies(self.bundle, replay=self.fake, cancelled=lambda: True)
        first = self.bundle.frames[0]
        invalid = replace(first, qqq=replace(first.qqq, close=float("nan")))
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            compare_strategies(replace(self.bundle, frames=[invalid, *self.bundle.frames[1:]]), replay=self.fake)
        with self.assertRaisesRegex(ValueError, "hash"):
            compare_strategies(replace(self.bundle, dataset_hash="invalid"), replay=self.fake)

    def test_outside_session_and_off_grid_stamps_are_rejected(self):
        first = self.bundle.frames[0]
        for delta in (-60, 30):
            at = first.start + timedelta(seconds=delta)
            moved = replace(first, start=at, qqq=replace(first.qqq, start=at),
                            tqqq=replace(first.tqqq, start=at), sqqq=replace(first.sqqq, start=at))
            with self.assertRaisesRegex(ValueError, "calendar grid"):
                compare_strategies(replace(self.bundle, frames=[moved, *self.bundle.frames[1:]]), replay=self.fake)

    def test_empty_closing_print_is_excluded_without_altering_source_identity(self):
        last = self.bundle.frames[-1]
        at = last.start + timedelta(minutes=1)
        stamp = replace(last, start=at, qqq=replace(last.qqq, start=at, volume=0),
                        tqqq=replace(last.tqqq, start=at, volume=0), sqqq=replace(last.sqqq, start=at, volume=0))
        frames = [*self.bundle.frames, stamp]
        source = replace(self.bundle, frames=frames, dataset_hash=dataset_hash(frames))
        result = compare_strategies(source, replay=self.fake)
        self.assertEqual(result["dataset"]["excluded_empty_close_stamps"], 1)
        self.assertEqual(result["dataset"]["input_hash"], source.dataset_hash)
        self.assertEqual(result["dataset"]["hash"], self.bundle.dataset_hash)
        self.assertEqual(len(source.frames), len(self.bundle.frames) + 1)


def replace_namespace(value, **changes):
    return SimpleNamespace(**(vars(value) | changes))


class ReplayOrderChecks(unittest.TestCase):
    def run_scenario(self, drop, rotate=False, *, start=None, count=12, oscillating=False, drop_start=2, **overrides):
        start = start or datetime(2026, 10, 13, 14, tzinfo=UTC)
        frames = []
        for i in range(count):
            at = start + timedelta(minutes=i)
            mark = 90.0 if (oscillating and i >= 4 and i % 2 == 0) or (drop and i >= drop_start) else 100.0
            q = Bar("QQQ", at, 100, 100, 100, 100, 1, 100000)
            t = Bar("TQQQ", at, 100 if oscillating or i < drop_start + 1 else mark, 100, mark, mark, 1, 100000)
            s = replace(q, symbol="SQQQ")
            frames.append(ReplayFrame(at, q, t, s))
        class Fixed:
            i = 0
            def reset(self):
                self.i = 0
            def on_bar(self, bar):
                bearish = (oscillating and self.i >= 5 and self.i % 2 == 1) or (rotate and self.i >= 1)
                regime = Regime.BEARISH if bearish else Regime.BULLISH
                self.i += 1
                return Signal(regime, 1, "scenario", bar.start)
        config = SandboxConfig(initial_cash=1000, order_notional=500, warmup_bars=4,
                               fast_ema=1, slow_ema=2, momentum_bars=1,
                               no_trade_open_minutes=0, no_trade_close_minutes=0,
                               hard_stop_pct=.8, take_profit_pct=.8, max_hold_minutes=1000,
                               risk_budget_pct=.5, max_daily_loss_pct=.02, volatility_target_pct=0,
                               slippage_bps=0, base_spread_bps=0, spread_volatility_multiplier=0,
                               force_flat_at_end=False)
        config = replace(config, **overrides)
        with patch("grande_alpha.research.bar_replay.build_strategy", return_value=Fixed()):
            return SandboxReplayEngine(config).run(HistoricalBundle("synthetic", start, frames))

    def test_loss_pause_exit_executes_at_next_open_without_starvation(self):
        result = self.run_scenario(True)
        exits = [fill for fill in result.fills if fill.side == "sell"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].timestamp, datetime(2026, 10, 13, 14, 3, tzinfo=UTC))
        self.assertIsNone(result.ending_position)

    def test_current_close_cannot_cancel_a_due_current_open_rotation(self):
        at = datetime(2026, 10, 13, 14, 2, tzinfo=UTC)
        def fills(result):
            return [(f.side, f.symbol, f.price) for f in result.fills if f.timestamp == at]
        self.assertTrue(fills(self.run_scenario(False, True)))
        self.assertEqual(fills(self.run_scenario(False, True)), fills(self.run_scenario(True, True)))

    def test_truncated_recording_is_not_a_session_close(self):
        result = self.run_scenario(False, force_flat_at_end=True)
        self.assertEqual([f.side for f in result.fills], ["buy"])
        self.assertEqual(result.ending_position, "TQQQS")
        self.assertEqual(result.round_trips, 0)

    def test_delayed_hard_stop_cannot_be_replaced_by_new_direction(self):
        result = self.run_scenario(False, oscillating=True, count=18, latency_bars=3,
                                   hard_stop_pct=.01, max_daily_loss_pct=.9)
        exits = [f for f in result.fills if f.side == "sell"]
        self.assertTrue(exits)
        self.assertEqual(exits[0].timestamp, datetime(2026, 10, 13, 14, 8, tzinfo=UTC))

    def test_a_loss_pause_blocks_the_buy_leg_of_the_same_rotation(self):
        result = self.run_scenario(True, True, drop_start=1, max_consecutive_losses=1, max_daily_loss_pct=.9)
        self.assertEqual([(f.side, f.symbol) for f in result.fills], [("buy", "TQQQS"), ("sell", "TQQQS")])

    def test_daily_loss_at_a_gapped_open_blocks_the_rotation_buy(self):
        result = self.run_scenario(True, True, drop_start=1, max_consecutive_losses=10, max_daily_loss_pct=.02)
        self.assertEqual([(f.side, f.symbol) for f in result.fills], [("buy", "TQQQS"), ("sell", "TQQQS")])

    def test_generic_daily_ema_still_warms_across_sessions(self):
        bundle = deterministic_demo(days=45)
        starts = list(dict.fromkeys(f.start.date() for f in bundle.frames))
        frames = []
        for i, day in enumerate(starts):
            at = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=13, minute=30)
            q = Bar("QQQ", at, 100+i, 100+i, 100+i, 100+i, 1, 100000)
            t = Bar("TQQQ", at, 100+3*i, 100+3*i, 100+3*i, 100+3*i, 1, 100000)
            frames.append(ReplayFrame(at, q, t, replace(t, symbol="SQQQ")))
        config = SandboxConfig(strategy_name="ema_momentum", force_flat_at_end=False,
                               initial_cash=1000, order_notional=500, order_type="limit", time_in_force="gtc")
        result = SandboxReplayEngine(config).run(replace(bundle, frames=frames, interval="1d"))
        self.assertTrue(any(f.side == "buy" for f in result.fills))

    def test_session_reset_preserves_first_half_hour_prior_close(self):
        frames = []
        for day, price in ((12, 100), (13, 101)):
            for minute in range(390):
                at = datetime(2026, 10, day, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
                q = Bar("QQQ", at, price, price, price, price, 1, 100000)
                t = Bar("TQQQ", at, 100, 100, 100, 100, 1, 100000)
                frames.append(ReplayFrame(at, q, t, replace(t, symbol="SQQQ")))
        config = SandboxConfig(strategy_name="first_half_hour_momentum", initial_cash=1000, order_notional=100,
                               slippage_bps=0, base_spread_bps=0, spread_volatility_multiplier=0)
        result = SandboxReplayEngine(config).run(HistoricalBundle("synthetic", frames[0].start, frames))
        buys = [f for f in result.fills if f.side == "buy"]
        self.assertTrue(buys)
        self.assertTrue(all(f.timestamp.day == 13 for f in buys))

    def test_partial_exits_count_as_one_trade_only_after_full_closure(self):
        incomplete = self.run_scenario(True, fill_fraction_pct=50)
        self.assertGreater(sum(f.side == "sell" for f in incomplete.fills), 1)
        self.assertEqual(incomplete.round_trips, 0)
        complete = self.run_scenario(True, count=80, fill_fraction_pct=50)
        self.assertEqual(complete.round_trips, 1)
        self.assertAlmostEqual(complete.expectancy, sum(f.realized_pnl or 0 for f in complete.fills))
        self.assertIsNone(complete.ending_position)

    def test_calendar_early_close_attempt_respects_partial_fills(self):
        start = datetime(2026, 11, 27, 17, 50, tzinfo=UTC)
        result = self.run_scenario(False, start=start, count=10, force_flat_at_end=True,
                                   fill_fraction_pct=50)
        exits = [f for f in result.fills if f.side == "sell"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].timestamp, start + timedelta(minutes=9))
        self.assertAlmostEqual(exits[0].quantity, result.fills[0].quantity * .5)
        self.assertEqual(result.round_trips, 0)
        self.assertIsNotNone(result.ending_position)


class ComparisonDesktopChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from grande_alpha.ui.session_window import SessionWindow
        self.app = QApplication.instance() or QApplication([])
        self.temp = tempfile.TemporaryDirectory(prefix="grande-strategy-test-")
        self.owner = SessionWindow(Path(self.temp.name), auto_probe=False)
        self.owner.status_timer.stop()
        self.owner._show_strategy_comparison()
        self.dialog = self.owner._strategy_dialog
        self.app.processEvents()

    async def asyncTearDown(self):
        self.dialog.reject()
        tasks = list(self.owner._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.owner._closing = True
        self.owner.close()
        self.app.processEvents()
        self.temp.cleanup()

    async def test_error_retry_and_close_during_comparison_remain_local(self):
        self.dialog.path = Path(self.temp.name) / "missing.json"
        self.dialog.start()
        task = self.dialog._task
        self.dialog.start()
        self.assertIs(self.dialog._task, task)
        await task
        self.assertIn("unavailable", self.dialog.status.text())
        self.assertTrue(self.dialog.run_button.isEnabled())
        entered, release = Event(), Event()
        def slow(_path):
            entered.set()
            release.wait(timeout=5)
            raise ValueError("cancelled input")
        with patch("grande_alpha.ui.strategy_comparison.load_strategy_dataset", side_effect=slow):
            self.dialog.start()
            while not entered.is_set():
                await asyncio.sleep(.01)
            self.dialog.reject()
            release.set()
            await self.dialog._task
        self.assertIsNone(self.dialog.report)
        self.owner._show_strategy_comparison()
        self.app.processEvents()
        self.assertTrue(self.dialog.choose.isEnabled())
        self.assertTrue(self.dialog.run_button.isEnabled())
        self.assertFalse(self.owner._worker_seen)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    async def test_report_save_never_replaces_the_loaded_history(self):
        path = Path(self.temp.name) / "history.json"
        path.write_text("original history", encoding="utf-8")
        self.dialog.path, self.dialog.report = path, {"comparison": "example"}
        with patch("grande_alpha.ui.strategy_comparison.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            self.dialog._save()
        self.assertEqual(path.read_text(encoding="utf-8"), "original history")
        self.assertIn("source history is preserved", self.dialog.status.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Research input regressions using synthetic prices and disposable settings only."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from grande_alpha.configuration.config import (
    AppConfig,
    config_document,
    load_config,
    save_config,
    upgrade_config,
)
from grande_alpha.domain.market_models import Bar, Regime
from grande_alpha.execution.candidate_execution import CandidateExecutionContract
from grande_alpha.research.bar_replay import SandboxReplayEngine
from grande_alpha.research.historical_models import HistoricalBundle, ReplayFrame
from grande_alpha.research.sandbox import load_sandbox_config, save_sandbox_config
from grande_alpha.research.sandbox_models import SandboxConfig
from grande_alpha.strategy.core import STRATEGY_NAMES, StrategyConfig, build_strategy


class StrategyInputChecks(unittest.TestCase):
    def test_counts_require_actual_integers(self):
        names = ("warmup_bars", "fast_ema", "slow_ema", "momentum_bars", "trend_short_bars",
                 "trend_medium_bars", "trend_long_bars", "opening_range_minutes", "ensemble_min_votes")
        for name in names:
            for value in (True, 3.0, float("nan"), float("inf")):
                with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                    replace(StrategyConfig(), **{name: value}).validate()

    def test_signal_thresholds_require_finite_numbers_not_booleans(self):
        for name in ("trend_threshold_bps", "close_momentum_bps", "breakout_buffer_bps", "round_trip_cost_bps"):
            for value in (True, float("nan"), float("inf"), float("-inf"), "4", None):
                with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                    replace(StrategyConfig(), **{name: value}).validate()

    def test_nonpositive_warmup_momentum_or_trend_threshold_is_rejected(self):
        for name in ("warmup_bars", "momentum_bars", "trend_threshold_bps"):
            with self.subTest(field=name), self.assertRaises(ValueError):
                replace(StrategyConfig(), **{name: 0}).validate()

    def test_invalid_strategy_names_raise_value_errors(self):
        for value in (None, [], {}, "unavailable"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                StrategyConfig(strategy_name=value).validate()

    def test_every_existing_strategy_accepts_integer_numeric_thresholds(self):
        bar = Bar("QQQ", datetime(2026, 1, 5, 14, 30, tzinfo=UTC), 100, 100, 100, 100, 1, 10000)
        for name in STRATEGY_NAMES:
            with self.subTest(strategy=name):
                config = StrategyConfig(strategy_name=name, trend_threshold_bps=4,
                                        close_momentum_bps=15, breakout_buffer_bps=3, round_trip_cost_bps=10)
                config.validate()
                self.assertEqual(build_strategy(config).on_bar(bar).regime, Regime.FLAT)


class SandboxInputChecks(unittest.TestCase):
    def test_lifecycle_counts_require_actual_integers(self):
        names = ("lookback_days", "csv_bar_seconds", "latency_bars", "random_seed", "decision_stride",
                 "max_hold_minutes", "max_entries_per_day", "no_trade_open_minutes",
                 "no_trade_close_minutes", "max_consecutive_losses")
        for name in names:
            for value in (True, 1.0, float("nan"), float("inf")):
                with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                    replace(SandboxConfig(), **{name: value}).validate()

    def test_execution_and_signal_numbers_reject_nonfinite_values_and_booleans(self):
        names = ("initial_cash", "order_notional", "slippage_bps", "base_spread_bps",
                 "spread_volatility_multiplier", "commission_per_order", "fill_fraction_pct",
                 "rejection_rate_pct", "max_volume_participation_pct", "limit_offset_bps",
                 "hard_stop_pct", "take_profit_pct", "risk_budget_pct", "max_exposure_pct",
                 "max_daily_loss_pct", "volatility_target_pct", "trend_threshold_bps", "signal_cost_bps")
        for name in names:
            for value in (True, float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                    replace(SandboxConfig(), **{name: value}).validate()

    def test_flattening_requires_a_boolean(self):
        for value in ("false", 1, 0, None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SandboxConfig(force_flat_at_end=value).validate()

    def test_large_or_nonnumeric_execution_values_fail_as_invalid_input(self):
        for name in ("initial_cash", "slippage_bps", "limit_offset_bps", "signal_cost_bps"):
            for value in (10 ** 1000, "10", None):
                if name == "signal_cost_bps" and value is None:
                    continue
                with self.subTest(field=name, kind=type(value).__name__), self.assertRaises(ValueError):
                    replace(SandboxConfig(), **{name: value}).validate()

    def test_integer_costs_false_flattening_and_unspecified_signal_cost_remain_valid(self):
        config = SandboxConfig(initial_cash=100, order_notional=25, slippage_bps=0, base_spread_bps=0,
                               spread_volatility_multiplier=0, commission_per_order=0, fill_fraction_pct=100,
                               rejection_rate_pct=0, max_volume_participation_pct=1, limit_offset_bps=10,
                               hard_stop_pct=1, take_profit_pct=1, risk_budget_pct=1, max_exposure_pct=1,
                               max_daily_loss_pct=1, volatility_target_pct=0, force_flat_at_end=False,
                               signal_cost_bps=None)
        config.validate()
        self.assertIs(config.force_flat_at_end, False)
        self.assertEqual(config.strategy_config().round_trip_cost_bps, 0)

    def test_no_trade_windows_use_the_selected_session_length(self):
        # Extended (780m) and all-day (1440m) routes have longer windows than
        # regular (390m); accepting one must still leave that session time to trade.
        routes = (("regular_hours", 300, 90), ("extended_hours", 700, 80), ("all_day_hours", 1000, 440))
        for market_hours, opening, closing in routes:
            with self.subTest(session=market_hours):
                config = SandboxConfig(market_hours=market_hours, order_type="limit",
                                       no_trade_open_minutes=opening, no_trade_close_minutes=closing)
                with self.assertRaises(ValueError):
                    config.validate()
                replace(config, no_trade_close_minutes=closing - 1).validate()

    def test_every_existing_strategy_can_replay_a_constant_synthetic_market(self):
        frames = []
        for minute in range(80):
            at = datetime(2026, 1, 5, 14, 30, tzinfo=UTC) + timedelta(minutes=minute)
            bars = [Bar(symbol, at, 100, 100, 100, 100, 1, 10000) for symbol in ("QQQ", "TQQQ", "SQQQ")]
            frames.append(ReplayFrame(at, *bars))
        bundle = HistoricalBundle("synthetic-input-check", frames[0].start, frames)
        for name in STRATEGY_NAMES:
            with self.subTest(strategy=name):
                result = SandboxReplayEngine(SandboxConfig(strategy_name=name)).run(bundle)
                self.assertEqual(result.final_equity, 50)
                self.assertEqual(result.round_trips, 0)
                self.assertIsNone(result.ending_position)


class ContractInputChecks(unittest.TestCase):
    def test_shared_contract_requires_a_boolean_flatten_control(self):
        for value in ("false", 1, 0, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CandidateExecutionContract(force_flat_at_end=value).validate()

    def test_shared_contract_rejects_unrepresentable_numbers_as_invalid_input(self):
        for name in ("initial_cash", "slippage_bps", "limit_offset_bps"):
            with self.subTest(field=name), self.assertRaises(ValueError):
                replace(CandidateExecutionContract(), **{name: 10 ** 1000}).validate()

    def test_shared_contract_preserves_supported_real_numeric_inputs(self):
        CandidateExecutionContract(initial_cash=100, order_notional=25,
                                   slippage_bps=Fraction(1, 4), force_flat_at_end=False).validate()


class SettingsInputChecks(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name) / "sandbox_config.json"
        seam = patch("grande_alpha.research.sandbox.sandbox_config_path", return_value=self.path)
        seam.start()
        self.addCleanup(seam.stop)

    def assert_rejected_without_rewriting(self, payload: bytes):
        self.path.write_bytes(payload)
        with self.assertRaises(ValueError) as failure:
            load_sandbox_config()
        self.assertIn(str(self.path), str(failure.exception))
        self.assertIn("unchanged", str(failure.exception))
        self.assertEqual(self.path.read_bytes(), payload)
        self.assertEqual([item.name for item in self.path.parent.iterdir()], [self.path.name])
        return str(failure.exception)

    def test_missing_settings_return_defaults_without_creating_a_file(self):
        self.assertEqual(asdict(load_sandbox_config()), asdict(SandboxConfig()))
        self.assertFalse(self.path.exists())

    def test_nonobject_json_requires_explicit_repair(self):
        for payload in (b"null", b"[]", b"[1]", b"true", b"1", b'"text"'):
            with self.subTest(payload=payload):
                self.assert_rejected_without_rewriting(payload)

    def test_duplicate_fields_require_explicit_repair(self):
        for payload in (b'{"lookback_days":2,"lookback_days":3}',
                        b'{"lookback_days":2,"future":{"value":1,"value":2}}'):
            with self.subTest(payload=payload):
                self.assert_rejected_without_rewriting(payload)

    def test_malformed_or_non_utf8_settings_require_explicit_repair(self):
        for payload in (b'{"lookback_days":', b"\xff\xfe\x80"):
            with self.subTest(payload=payload):
                self.assert_rejected_without_rewriting(payload)

    def test_an_existing_directory_is_not_treated_as_missing_settings(self):
        self.path.mkdir()
        with self.assertRaises(ValueError) as failure:
            load_sandbox_config()
        self.assertIn(str(self.path), str(failure.exception))
        self.assertTrue(self.path.is_dir())
        self.assertEqual(list(self.path.iterdir()), [])

    def test_nonfinite_settings_require_explicit_repair(self):
        for token in ("NaN", "Infinity", "-Infinity", "1e309"):
            with self.subTest(token=token):
                self.assert_rejected_without_rewriting((f'{{"lookback_days":2,"trend_threshold_bps":{token}}}').encode())

    def test_invalid_controls_require_explicit_repair(self):
        for field, value in (("lookback_days", True), ("fast_ema", True), ("force_flat_at_end", "false")):
            with self.subTest(field=field):
                self.assert_rejected_without_rewriting(json.dumps({"lookback_days": 2, field: value}).encode())

    def test_unknown_or_misspelled_fields_require_explicit_repair(self):
        for field in ("stratgey_name", "unexpected_setting"):
            with self.subTest(field=field):
                payload = json.dumps({field: "cash"}).encode()
                message = self.assert_rejected_without_rewriting(payload)
                self.assertIn(field, message)

    def test_oversized_or_deeply_nested_settings_require_explicit_repair(self):
        oversized = b'{"lookback_days":2,"future":"' + b"x" * 65536 + b'"}'
        nested = b'{"lookback_days":2,"future":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"
        for payload in (oversized, nested):
            with self.subTest(length=len(payload)):
                self.assert_rejected_without_rewriting(payload)

    def test_valid_bom_partial_settings_preserve_strategy_and_defaults(self):
        payload = b'\xef\xbb\xbf{"strategy_name":"cash","lookback_days":9,"slippage_bps":0,"force_flat_at_end":false,"signal_cost_bps":null}'
        self.path.write_bytes(payload)
        config = load_sandbox_config()
        self.assertEqual(config.strategy_name, "cash")
        self.assertEqual(config.lookback_days, 9)
        self.assertEqual(config.slippage_bps, 0)
        self.assertEqual(config.warmup_bars, 24)
        self.assertEqual(config.order_notional, 25)
        self.assertIs(config.force_flat_at_end, False)
        self.assertIsNone(config.signal_cost_bps)
        self.assertEqual(self.path.read_bytes(), payload)

    def test_all_current_saved_fields_roundtrip(self):
        config = SandboxConfig(strategy_name="cash", initial_cash=200, order_notional=50,
                               lookback_days=9, force_flat_at_end=False, signal_cost_bps=0)
        save_sandbox_config(config)
        previous = self.path.read_bytes()
        self.assertEqual(asdict(load_sandbox_config()), asdict(config))
        self.assertEqual(self.path.read_bytes(), previous)

    def test_saving_invalid_settings_preserves_the_previous_file(self):
        previous = b'{"lookback_days":9}'
        self.path.write_bytes(previous)
        with self.assertRaises(ValueError):
            save_sandbox_config(SandboxConfig(force_flat_at_end="false"))
        self.assertEqual(self.path.read_bytes(), previous)


class MainSettingsInputChecks(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name) / 'config.json'

    def ambiguous(self):
        text = json.dumps(config_document(AppConfig()))
        return text.replace('"broker_connection_enabled": false',
                            '"broker_connection_enabled": false, "broker_connection_enabled": true').encode()

    def test_duplicate_permission_fields_never_choose_the_last_value(self):
        before = self.ambiguous()
        self.path.write_bytes(before)
        for operation in (load_config, upgrade_config):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(ValueError, 'Duplicate JSON field'):
                operation(self.path)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_ambiguous_legacy_settings_are_not_upgraded_or_backed_up(self):
        before = b'{"cadence_version":6,"broker_connection_enabled":false,"broker_connection_enabled":true}'
        self.path.write_bytes(before)
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON field'):
            upgrade_config(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_normal_save_does_not_replace_an_ambiguous_existing_file(self):
        before = self.ambiguous()
        self.path.write_bytes(before)
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON field'):
            save_config(AppConfig(), self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_windows_utf8_bom_settings_remain_readable(self):
        self.path.write_bytes(b'\xef\xbb\xbf' + json.dumps(config_document(AppConfig())).encode())
        self.assertFalse(load_config(self.path).broker.broker_connection_enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)

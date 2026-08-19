import copy
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grande_alpha.broker.base import Broker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.evidence import strategy_fingerprint
from grande_alpha.models import (
    Account,
    BrokerOrder,
    OrderIntent,
    OrderReview,
    Portfolio,
    Quote,
    Regime,
    Signal,
)
from grande_alpha.policy import session_key
from grande_alpha.sandbox import SandboxConfig
from grande_alpha.shadow import (
    LiveShadowEngine,
    shadow_checkpoint_digest,
    shadow_checkpoint_requires_continuity,
)
from grande_alpha.storage import AuditStore

NOW = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)
ACCOUNT_FINGERPRINT = hashlib.sha256(b"agentic-1234").hexdigest()


def _quotes(timestamp: datetime) -> dict[str, Quote]:
    return {
        "TQQQ": Quote("TQQQ", 49.99, 50.01, 50.0, timestamp),
        "SQQQ": Quote("SQQQ", 39.99, 40.01, 40.0, timestamp),
    }


def _config() -> SandboxConfig:
    return SandboxConfig(
        initial_cash=50,
        order_notional=25,
        max_exposure_pct=1.0,
        risk_budget_pct=1.0,
        hard_stop_pct=0.5,
        decision_stride=1,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        settlement_model="cash_t1",
    )


def _checkpoint(
    engine: LiveShadowEngine,
    *,
    sequence: int,
    previous_digest: str | None = None,
    event: str = "advance",
) -> dict[str, object]:
    config = engine.config
    return engine.checkpoint(
        sequence=sequence,
        recorded_at=NOW,
        session=engine.current_session or session_key(NOW, engine.contract.market_hours),
        account_fingerprint=ACCOUNT_FINGERPRINT,
        strategy_fingerprint=strategy_fingerprint(config, "60s"),
        event=event,
        previous_digest=previous_digest,
    )


def test_checkpoint_restores_open_position_and_all_causal_engine_state() -> None:
    config = replace(_config(), fill_fraction_pct=50)
    engine = LiveShadowEngine(config, bar_minutes=1)
    fill = engine.on_causal_quote(NOW, Signal(Regime.BULLISH, 1, "enter"), _quotes(NOW))[0]
    assert fill.side == "buy" and engine.state.position is not None
    exit_fill = engine.on_causal_quote(
        NOW + timedelta(minutes=1),
        Signal(Regime.BEARISH, 1, "reverse"),
        _quotes(NOW + timedelta(minutes=1)),
    )[0]
    assert exit_fill.side == "sell"
    assert engine.state.position is not None
    assert engine.state.unsettled_cash > 0
    checkpoint = _checkpoint(engine, sequence=1, event="fill")

    restored = LiveShadowEngine.restore(
        config,
        checkpoint,
        expected_session=session_key(NOW, engine.contract.market_hours),
        expected_account_fingerprint=ACCOUNT_FINGERPRINT,
        expected_strategy_fingerprint=strategy_fingerprint(config, "60s"),
        bar_minutes=1,
    )

    assert restored.state == engine.state
    assert restored.current_session == engine.current_session
    assert restored.checkpoint(
        sequence=2,
        recorded_at=NOW + timedelta(seconds=1),
        session=restored.current_session or "",
        account_fingerprint=ACCOUNT_FINGERPRINT,
        strategy_fingerprint=strategy_fingerprint(config, "60s"),
        event="recovered",
        previous_digest=str(checkpoint["digest"]),
    )["state"] == engine.checkpoint(
        sequence=2,
        recorded_at=NOW + timedelta(seconds=1),
        session=engine.current_session or "",
        account_fingerprint=ACCOUNT_FINGERPRINT,
        strategy_fingerprint=strategy_fingerprint(config, "60s"),
        event="recovered",
        previous_digest=str(checkpoint["digest"]),
    )["state"]


def test_checkpoint_rejects_tampering_and_incompatible_identity() -> None:
    config = _config()
    engine = LiveShadowEngine(config, bar_minutes=1)
    engine.on_causal_quote(NOW, Signal(Regime.BULLISH, 1, "enter"), _quotes(NOW))
    checkpoint = _checkpoint(engine, sequence=1)
    tampered = copy.deepcopy(checkpoint)
    tampered["state"]["cash"] = 50.0

    with pytest.raises(ValueError, match="digest mismatch"):
        LiveShadowEngine.restore(
            config,
            tampered,
            expected_session=session_key(NOW, engine.contract.market_hours),
            expected_account_fingerprint=ACCOUNT_FINGERPRINT,
            expected_strategy_fingerprint=strategy_fingerprint(config, "60s"),
            bar_minutes=1,
        )
    with pytest.raises(ValueError, match="strategy fingerprint"):
        LiveShadowEngine.restore(
            config,
            checkpoint,
            expected_session=session_key(NOW, engine.contract.market_hours),
            expected_account_fingerprint=ACCOUNT_FINGERPRINT,
            expected_strategy_fingerprint="f" * 64,
            bar_minutes=1,
        )


def test_store_atomically_appends_and_verifies_the_hash_chain(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "shadow.db")
    engine = LiveShadowEngine(_config(), bar_minutes=1)
    first = _checkpoint(engine, sequence=1, event="started")
    store.append_shadow_checkpoint(first)
    engine.on_causal_quote(NOW, Signal(Regime.BULLISH, 1, "enter"), _quotes(NOW))
    second = _checkpoint(engine, sequence=2, previous_digest=str(first["digest"]), event="fill")
    store.append_shadow_checkpoint(second)

    assert store.latest_shadow_checkpoint() == second
    assert store.shadow_checkpoints(engine.state.run_id) == [first, second]
    skipped = _checkpoint(engine, sequence=4, previous_digest=str(second["digest"]))
    with pytest.raises(ValueError, match="sequence or previous digest"):
        store.append_shadow_checkpoint(skipped)
    assert store.latest_shadow_checkpoint() == second

    store._connection.execute(
        "UPDATE shadow_checkpoints SET checkpoint_json=? WHERE run_id=? AND sequence=2",
        ('{"schema_version":1}', engine.state.run_id),
    )
    store._connection.commit()
    with pytest.raises(ValueError):
        store.latest_shadow_checkpoint()
    store.close()


class _ReadOnlyBroker(Broker):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def get_accounts(self) -> list[Account]:
        return []

    async def get_portfolio(self, account_number: str) -> Portfolio:
        raise AssertionError("not used")

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {}

    async def get_positions(self, account_number: str):
        return []

    async def get_orders(self, account_number: str):
        return []

    async def review_order(self, account_number: str, intent: OrderIntent) -> OrderReview:
        raise AssertionError("shadow recovery cannot review orders")

    async def place_order(self, account_number: str, intent: OrderIntent) -> BrokerOrder:
        raise AssertionError("shadow recovery cannot place orders")

    async def cancel_order(self, account_number: str, order_id: str) -> bool:
        raise AssertionError("shadow recovery cannot cancel orders")


def _controller(store: AuditStore, config: AppConfig) -> TradingController:
    controller = TradingController(_ReadOnlyBroker(), config, store, shadow_only_runtime=True)
    controller.snapshot.connected = True
    controller.snapshot.account = Account("agentic-1234", "Agentic", "cash", True, "active")
    return controller


def _app_config(sandbox: SandboxConfig) -> AppConfig:
    return AppConfig(
        broker_connection_enabled=True,
        strategy_name=sandbox.strategy_name,
        warmup_bars=sandbox.warmup_bars,
        fast_ema=sandbox.fast_ema,
        slow_ema=sandbox.slow_ema,
        trend_threshold_bps=sandbox.trend_threshold_bps,
        momentum_bars=sandbox.momentum_bars,
        hard_stop_pct=sandbox.hard_stop_pct,
        trade_every_bars=2,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        settlement_model="cash_t1",
        bar_seconds=60,
    )


def test_controller_recovers_compatible_same_session_without_resetting_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    store = AuditStore(tmp_path / "controller.db")
    sandbox = _config()
    config = _app_config(sandbox)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: NOW)
    monkeypatch.setattr("grande_alpha.storage.utc_now", lambda: NOW)
    monkeypatch.setattr("grande_alpha.controller.load_sandbox_config", lambda: sandbox)
    first = _controller(store, config)
    first.start_shadow()
    assert first._shadow is not None
    first._shadow.on_causal_quote(NOW, Signal(Regime.BULLISH, 1, "enter"), _quotes(NOW))
    first._shadow.on_causal_quote(
        NOW + timedelta(minutes=1),
        Signal(Regime.BULLISH, 1, "enter"),
        _quotes(NOW + timedelta(minutes=1)),
    )
    first._persist_shadow_checkpoint("fill")
    original_state = copy.deepcopy(first._shadow.state)
    original_run_id = original_state.run_id

    restarted = _controller(store, config)
    restarted.start_shadow()

    assert restarted._shadow is not None
    assert restarted._shadow.state == original_state
    assert restarted._shadow.state.run_id == original_run_id
    latest = store.latest_shadow_checkpoint()
    assert latest is not None and latest["event"] == "recovered"
    recovery_receipts = [
        row for row in store.recent_receipts() if row["category"] == "shadow_recovery"
    ]
    assert recovery_receipts
    recovery_payload = json.loads(recovery_receipts[0]["payload_json"])
    assert recovery_payload["virtual_cash"] == pytest.approx(original_state.cash)
    assert recovery_payload["position"]["symbol"] == "TQQQS"
    restarted.stop_shadow("test complete")
    store.close()


def test_controller_fails_closed_instead_of_resetting_an_incompatible_active_run(
    tmp_path: Path, monkeypatch
) -> None:
    store = AuditStore(tmp_path / "blocked.db")
    sandbox = _config()
    base = AppConfig(
        broker_connection_enabled=True,
        strategy_name=sandbox.strategy_name,
        fast_ema=sandbox.fast_ema,
        slow_ema=sandbox.slow_ema,
        hard_stop_pct=sandbox.hard_stop_pct,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        settlement_model="cash_t1",
        bar_seconds=60,
    )
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: NOW)
    monkeypatch.setattr("grande_alpha.storage.utc_now", lambda: NOW)
    monkeypatch.setattr("grande_alpha.controller.load_sandbox_config", lambda: sandbox)
    first = _controller(store, base)
    first.start_shadow()
    checkpoint_count = len(store.shadow_checkpoints(first._shadow.state.run_id))

    changed = _controller(store, replace(base, fast_ema=base.fast_ema + 1))
    with pytest.raises(RuntimeError, match="refusing to reset"):
        changed.start_shadow()
    assert changed._shadow is None
    assert len(store.shadow_checkpoints(first._shadow.state.run_id)) == checkpoint_count
    store.close()


def test_controller_blocks_stopped_same_session_run_with_open_position(
    tmp_path: Path, monkeypatch
) -> None:
    store = AuditStore(tmp_path / "stopped-position.db")
    sandbox = _config()
    config = _app_config(sandbox)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: NOW)
    monkeypatch.setattr("grande_alpha.storage.utc_now", lambda: NOW)
    monkeypatch.setattr("grande_alpha.controller.load_sandbox_config", lambda: sandbox)
    first = _controller(store, config)
    first.start_shadow()
    assert first._shadow is not None
    first._shadow.on_causal_quote(NOW, Signal(Regime.BULLISH, 1, "enter"), _quotes(NOW))
    first._shadow.on_causal_quote(
        NOW + timedelta(minutes=1),
        Signal(Regime.BULLISH, 1, "enter"),
        _quotes(NOW + timedelta(minutes=1)),
    )
    assert first._shadow.state.position is not None
    first._persist_shadow_checkpoint("fill")
    run_id = first._shadow.state.run_id
    first.stop_shadow("transport failure")
    stopped = store.latest_shadow_checkpoint()
    assert stopped is not None
    assert not stopped["state"]["active"]
    assert stopped["state"]["position"] is not None
    checkpoint_count = len(store.shadow_checkpoints(run_id))

    restarted = _controller(store, config)
    with pytest.raises(RuntimeError, match="unresolved virtual state.*refusing to reset"):
        restarted.start_shadow()

    assert restarted._shadow is None
    assert len(store.shadow_checkpoints(run_id)) == checkpoint_count
    assert store.latest_shadow_checkpoint() == stopped
    recovery_receipts = [
        row for row in store.recent_receipts() if row["category"] == "shadow_recovery"
    ]
    assert recovery_receipts
    recovery_payload = json.loads(recovery_receipts[0]["payload_json"])
    assert recovery_payload["prior_run_id"] == run_id
    assert recovery_payload["open_position"] == "TQQQS"
    assert recovery_payload["compatible"] is True
    assert recovery_payload["broker_write_attempted"] is False
    store.close()


def test_controller_allows_fresh_run_after_clean_flat_same_session_stop(
    tmp_path: Path, monkeypatch
) -> None:
    store = AuditStore(tmp_path / "clean-stop.db")
    sandbox = _config()
    config = _app_config(sandbox)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: NOW)
    monkeypatch.setattr("grande_alpha.storage.utc_now", lambda: NOW)
    monkeypatch.setattr("grande_alpha.controller.load_sandbox_config", lambda: sandbox)
    first = _controller(store, config)
    first.start_shadow()
    assert first._shadow is not None
    prior_run_id = first._shadow.state.run_id
    first.stop_shadow("deliberate clean stop")
    stopped = store.latest_shadow_checkpoint()
    assert stopped is not None
    assert not shadow_checkpoint_requires_continuity(stopped)

    restarted = _controller(store, config)
    restarted.start_shadow()

    assert restarted._shadow is not None
    assert restarted._shadow.state.active
    assert restarted._shadow.state.run_id != prior_run_id
    latest = store.latest_shadow_checkpoint()
    assert latest is not None
    assert latest["event"] == "started"
    assert latest["run_id"] == restarted._shadow.state.run_id
    restarted.stop_shadow("test complete")
    store.close()


def test_stopped_checkpoint_continuity_includes_pending_and_unsettled_state() -> None:
    pending_engine = LiveShadowEngine(_config(), bar_minutes=1)
    pending_engine.on_bar(NOW, Signal(Regime.BULLISH, 1, "enter"), _quotes(NOW))
    pending_checkpoint = _checkpoint(pending_engine, sequence=1)
    assert pending_checkpoint["state"]["pending"] is not None
    pending_checkpoint["state"]["active"] = False
    pending_checkpoint["digest"] = shadow_checkpoint_digest(pending_checkpoint)
    assert shadow_checkpoint_requires_continuity(pending_checkpoint)

    unsettled_engine = LiveShadowEngine(_config(), bar_minutes=1)
    unsettled_engine.on_causal_quote(
        NOW,
        Signal(Regime.BULLISH, 1, "enter"),
        _quotes(NOW),
    )
    unsettled_engine.stop(
        _quotes(NOW + timedelta(minutes=1)),
        flatten_at=NOW + timedelta(minutes=1),
    )
    unsettled_checkpoint = _checkpoint(unsettled_engine, sequence=1, event="stopped")
    assert unsettled_checkpoint["state"]["position"] is None
    assert unsettled_checkpoint["state"]["unsettled_cash"] > 0
    assert shadow_checkpoint_requires_continuity(unsettled_checkpoint)

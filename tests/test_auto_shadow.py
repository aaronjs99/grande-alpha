import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

import grande_alpha.broker.robinhood_mcp as robinhood_mcp
from grande_alpha.broker.base import Broker, BrokerError, ShadowOnlyBroker
from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.models import (
    Account,
    Bar,
    BrokerOrder,
    LiveGrant,
    OrderReview,
    Portfolio,
    Position,
    Quote,
    Regime,
    Signal,
)
from grande_alpha.storage import AuditStore
from grande_alpha.strategy import CashStrategy, MomentumStrategy
from grande_alpha.ui.main_window import MainWindow

START = datetime(2026, 8, 11, 13, 31, tzinfo=UTC)  # 09:31 ET Tuesday


class AutoShadowBroker(Broker):
    def __init__(
        self,
        *,
        accounts: int = 1,
        missing_symbol: str | None = None,
        quote_clock=None,
        open_order: bool = False,
        open_order_state: str = "confirmed",
        leveraged_position: bool = False,
    ) -> None:
        self.account_count = accounts
        self.missing_symbol = missing_symbol
        self.quote_clock = quote_clock or (lambda: START)
        self.open_order = open_order
        self.open_order_state = open_order_state
        self.leveraged_position = leveraged_position
        self.fail_quotes = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.review_calls = 0
        self.place_calls = 0
        self.cancel_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def get_accounts(self) -> list[Account]:
        return [
            Account(str(1000 + index), "Agentic", "cash", True, "active")
            for index in range(self.account_count)
        ]

    async def get_portfolio(self, account_number: str) -> Portfolio:
        return Portfolio(100.0, 100.0, 100.0)

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if self.fail_quotes:
            raise RuntimeError("transport unavailable")
        return {
            symbol: Quote(symbol, 100.0, 100.02, 100.01, self.quote_clock())
            for symbol in symbols
            if symbol != self.missing_symbol
        }

    async def get_positions(self, account_number: str):
        return [Position(" tqqq ", 1.0, 1.0, 100.0)] if self.leveraged_position else []

    async def get_orders(self, account_number: str):
        if not self.open_order:
            return []
        return [
            BrokerOrder(
                "external-open-order",
                "TQQQ",
                "buy",
                self.open_order_state,
                1.0,
                None,
                None,
                START,
            )
        ]

    async def review_order(self, account_number, intent):
        self.review_calls += 1
        return OrderReview(intent, "", {}, {})

    async def place_order(self, account_number, intent):
        self.place_calls += 1
        raise AssertionError("Auto-shadow must never place an order")

    async def cancel_order(self, account_number, order_id):
        self.cancel_calls += 1
        raise AssertionError("Auto-shadow must never cancel an order")


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=max(0.001, seconds))


def _controller(
    tmp_path,
    broker: AutoShadowBroker,
    *,
    clock: FakeClock | None = None,
    **config_overrides,
):
    live_enabled = config_overrides.pop("live_trading_enabled", False)
    config = AppConfig(
        broker_connection_enabled=True,
        live_trading_enabled=live_enabled,
        **config_overrides,
    )
    store = AuditStore(tmp_path / "audit.db")
    controller = TradingController(
        broker,
        config,
        store,
        shadow_only_runtime=True,
        auto_shadow_sleep=clock.sleep if clock else None,
    )
    return controller, store


@pytest.mark.asyncio
async def test_auto_shadow_starts_clean_and_never_reaches_broker_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: START)
    broker = AutoShadowBroker()
    controller, store = _controller(tmp_path, broker)
    controller._analysis_sequence = 9
    controller._last_trade_decision_sequence = 6

    assert await controller.auto_start_shadow()
    assert controller.snapshot.connected
    assert controller.snapshot.shadow_running
    assert controller.snapshot.live_status == "LOCKED"
    assert controller.risk.grant is None
    assert not controller.snapshot.strategy_running
    assert isinstance(controller.strategy, CashStrategy)
    assert controller.strategy.last_signal.regime == Regime.FLAT
    assert controller._analysis_sequence == controller._last_trade_decision_sequence == 0
    assert set(controller.snapshot.quotes) == {"QQQ", "TQQQ", "SQQQ"}

    grant = LiveGrant(
        "1000",
        START,
        START + timedelta(hours=1),
        10,
        20,
        2,
        4,
        1,
        20,
        8,
        strategy_fingerprint="a" * 64,
    )
    with pytest.raises(RuntimeError, match="read-only"):
        controller.authorize_live(grant)
    with pytest.raises(RuntimeError, match="read-only"):
        controller.start_strategy()
    with pytest.raises(RuntimeError, match="read-only"):
        await controller.review_flatten("TQQQ")
    with pytest.raises(RuntimeError, match="read-only"):
        await controller.place_reviewed_flatten(None, None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="read-only"):
        await controller._submit(None, None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="read-only"):
        await controller._evaluate_and_trade()
    await controller.stop_and_cancel()

    assert broker.review_calls == broker.place_calls == broker.cancel_calls == 0
    assert any(
        "AUTO SHADOW ACTIVE" in receipt["summary"] for receipt in store.recent_receipts(50)
    )
    assert any(
        "AUTO SHADOW WAITING — regular open 9:30 AM ET; writes blocked" == receipt["summary"]
        for receipt in store.recent_receipts(50)
    )
    await controller.disconnect()
    assert broker.cancel_calls == 0
    store.close()


@pytest.mark.asyncio
async def test_default_auto_shadow_cash_champion_emits_hold_and_zero_fills(
    tmp_path, monkeypatch
) -> None:
    clock = FakeClock(START)
    broker = AutoShadowBroker(quote_clock=lambda: clock.now)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    controller, store = _controller(tmp_path, broker, clock=clock)

    assert await controller.auto_start_shadow()
    for _ in range(8):
        clock.now += timedelta(seconds=5)
        await controller.refresh_quotes(evaluate=False)

    assert isinstance(controller.strategy, CashStrategy)
    assert controller.snapshot.signal.regime == Regime.FLAT
    assert controller.snapshot.pair_action_id == 4
    assert controller.snapshot.pair_action_label == "(0,0)"
    assert controller._shadow is not None
    assert controller._shadow.config.strategy_name == "cash"
    assert controller._shadow.state.position is None
    assert controller._shadow.state.fills == []
    assert broker.review_calls == broker.place_calls == broker.cancel_calls == 0
    store.close()


@pytest.mark.asyncio
async def test_runtime_strategy_change_stops_shadow_and_rebuilds_pipeline(
    tmp_path, monkeypatch
) -> None:
    clock = FakeClock(START)
    broker = AutoShadowBroker(quote_clock=lambda: clock.now)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    controller, store = _controller(tmp_path, broker, clock=clock)

    assert await controller.auto_start_shadow()
    prior_shadow = controller._shadow
    assert prior_shadow is not None and prior_shadow.state.active

    # Seed a partial bar, then prove a signal hot-update discards it while
    # retaining the duplicate guard for the latest provider observation.
    clock.now += timedelta(seconds=1)
    await controller.refresh_quotes(evaluate=False)
    previous_builder = controller.bar_builder
    previous_timestamp = controller._last_qqq_timestamp
    assert previous_builder._prices

    controller.update_config(replace(controller.config, strategy_name="ema_momentum"))

    assert not prior_shadow.state.active
    assert not controller.snapshot.shadow_running
    assert isinstance(controller.strategy, MomentumStrategy)
    assert controller.bar_builder is not previous_builder
    assert controller.bar_builder._bucket is None
    assert controller.bar_builder._prices == []
    assert controller._last_qqq_timestamp == previous_timestamp
    await controller.refresh_quotes(evaluate=False)
    assert controller.bar_builder._bucket is None
    assert controller.bar_builder._prices == []
    assert controller._analysis_sequence == controller._last_trade_decision_sequence == 0
    assert controller.snapshot.signal.regime == Regime.FLAT
    assert broker.review_calls == broker.place_calls == broker.cancel_calls == 0
    assert any(
        "previous live-shadow run was stopped" in item["summary"]
        for item in store.recent_receipts(20)
    )
    store.close()


@pytest.mark.asyncio
async def test_deliberate_ema_runtime_still_creates_only_virtual_shadow_fill(
    tmp_path, monkeypatch
) -> None:
    clock = FakeClock(START)
    broker = AutoShadowBroker(quote_clock=lambda: clock.now)

    async def rising_quotes(symbols: list[str]) -> dict[str, Quote]:
        step = max(0, int((clock.now - START).total_seconds() // 5))
        return {
            symbol: Quote(
                symbol,
                (100.0 + step * 0.2 if symbol == "QQQ" else 100.0),
                (100.02 + step * 0.2 if symbol == "QQQ" else 100.02),
                (100.01 + step * 0.2 if symbol == "QQQ" else 100.01),
                clock.now,
            )
            for symbol in symbols
        }

    broker.get_quotes = rising_quotes  # type: ignore[method-assign]
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    controller, store = _controller(
        tmp_path,
        broker,
        clock=clock,
        strategy_name="ema_momentum",
        warmup_bars=4,
        fast_ema=1,
        slow_ema=2,
        momentum_bars=1,
        trend_threshold_bps=0.1,
        trade_every_bars=2,
        no_trade_open_minutes=0,
    )

    assert await controller.auto_start_shadow()
    for _ in range(10):
        clock.now += timedelta(seconds=5)
        await controller.refresh_quotes(evaluate=False)

    assert isinstance(controller.strategy, MomentumStrategy)
    assert controller.snapshot.signal.regime == Regime.BULLISH
    assert controller._shadow is not None
    assert controller._shadow.config.strategy_name == "ema_momentum"
    assert controller._shadow.state.position is not None
    assert controller._shadow.state.fills
    assert all(fill.side == "buy" for fill in controller._shadow.state.fills)
    assert broker.review_calls == broker.place_calls == broker.cancel_calls == 0
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broker", "blocker"),
    [
        (AutoShadowBroker(accounts=2), "exactly one"),
        (AutoShadowBroker(missing_symbol="SQQQ"), "exact QQQ/TQQQ/SQQQ"),
    ],
)
async def test_auto_shadow_blocks_ambiguous_account_or_incomplete_quotes(
    tmp_path, monkeypatch, broker, blocker
) -> None:
    clock = FakeClock(START)
    broker.quote_clock = lambda: clock.now
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    controller, store = _controller(tmp_path, broker, clock=clock)

    assert not await controller.auto_start_shadow()
    assert not controller.snapshot.connected
    assert not controller.snapshot.shadow_running
    assert controller.snapshot.live_status == "LOCKED"
    assert broker.review_calls == broker.place_calls == broker.cancel_calls == 0
    assert any(blocker in receipt["summary"] for receipt in store.recent_receipts(20))
    store.close()


@pytest.mark.asyncio
async def test_auto_shadow_transport_failure_stops_and_read_only_disconnects(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: START)
    broker = AutoShadowBroker()
    controller, store = _controller(tmp_path, broker)
    assert await controller.auto_start_shadow()
    broker.fail_quotes = True

    await controller.refresh_quotes()

    assert not controller.snapshot.connected
    assert not controller.snapshot.shadow_running
    assert broker.disconnect_calls >= 1
    assert broker.review_calls == broker.place_calls == broker.cancel_calls == 0
    assert any("AUTO SHADOW BLOCKED" in item["summary"] for item in store.recent_receipts(20))
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broker", "blocker"),
    [
        (AutoShadowBroker(open_order=True, open_order_state=" CONFIRMED "), "open Agentic order"),
        (
            AutoShadowBroker(open_order=True, open_order_state="provider_new_state"),
            "open Agentic order",
        ),
        (AutoShadowBroker(leveraged_position=True), "real leveraged position"),
    ],
)
async def test_auto_shadow_preflight_blocks_real_position_or_open_order_without_cancelling(
    tmp_path, monkeypatch, broker, blocker
) -> None:
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: START)
    controller, store = _controller(tmp_path, broker)

    assert not await controller.auto_start_shadow()

    assert not controller.snapshot.shadow_running
    assert controller.snapshot.portfolio is None
    assert controller.snapshot.positions == []
    assert controller.snapshot.orders == []
    assert broker.disconnect_calls >= 1
    assert broker.cancel_calls == broker.review_calls == broker.place_calls == 0
    assert any(blocker in item["summary"] for item in store.recent_receipts(20))
    store.close()


@pytest.mark.asyncio
async def test_auto_shadow_connects_early_waits_without_real_sleep_and_resets_at_open(
    tmp_path, monkeypatch
) -> None:
    clock = FakeClock(datetime(2026, 8, 11, 13, 20, tzinfo=UTC))  # 06:20 PT / 09:20 ET
    broker = AutoShadowBroker(quote_clock=lambda: clock.now)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    controller, store = _controller(
        tmp_path,
        broker,
        clock=clock,
        strategy_name="ema_momentum",
    )
    assert isinstance(controller.strategy, MomentumStrategy)
    controller.strategy.bars.append(Bar("QQQ", clock.now, 1, 1, 1, 1, 1))
    controller._analysis_sequence = 4

    assert await controller.auto_start_shadow()

    assert broker.connect_calls == 1
    assert clock.sleeps
    assert clock.now == datetime(2026, 8, 11, 13, 30, tzinfo=UTC)
    assert controller.snapshot.shadow_running
    assert controller._analysis_sequence == 0
    assert not controller.strategy.bars
    store.close()


@pytest.mark.asyncio
async def test_auto_shadow_rechecks_live_capability_after_premarket_wait(tmp_path, monkeypatch) -> None:
    clock = FakeClock(datetime(2026, 8, 11, 13, 29, 50, tzinfo=UTC))
    broker = AutoShadowBroker(quote_clock=lambda: clock.now)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    controller, store = _controller(tmp_path, broker, clock=clock)

    async def revoke_shadow_only_boundary(seconds: float) -> None:
        await clock.sleep(seconds)
        controller.config.live_trading_enabled = True

    controller._auto_shadow_sleep = revoke_shadow_only_boundary

    assert not await controller.auto_start_shadow()
    assert not controller.snapshot.shadow_running
    assert broker.disconnect_calls >= 1
    assert broker.cancel_calls == broker.review_calls == broker.place_calls == 0
    store.close()


@pytest.mark.asyncio
async def test_auto_shadow_rechecks_real_account_state_at_market_open(tmp_path, monkeypatch) -> None:
    clock = FakeClock(datetime(2026, 8, 11, 13, 29, 50, tzinfo=UTC))
    broker = AutoShadowBroker(quote_clock=lambda: clock.now)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    controller, store = _controller(tmp_path, broker, clock=clock)

    async def external_order_appears(seconds: float) -> None:
        await clock.sleep(seconds)
        broker.open_order = True

    controller._auto_shadow_sleep = external_order_appears

    assert not await controller.auto_start_shadow()
    assert not controller.snapshot.shadow_running
    assert broker.cancel_calls == broker.review_calls == broker.place_calls == 0
    assert any("open Agentic order" in item["summary"] for item in store.recent_receipts(20))
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("new_account_truth", ["unknown_order", "leveraged_position"])
async def test_auto_shadow_reconcile_stops_on_new_real_account_risk(
    tmp_path, monkeypatch, new_account_truth
) -> None:
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: START)
    broker = AutoShadowBroker()
    controller, store = _controller(tmp_path, broker)
    assert await controller.auto_start_shadow()

    if new_account_truth == "unknown_order":
        broker.open_order = True
        broker.open_order_state = " PROVIDER_NEW_STATE "
    else:
        broker.leveraged_position = True
    await controller.reconcile()

    assert not controller.snapshot.connected
    assert not controller.snapshot.shadow_running
    assert broker.disconnect_calls >= 1
    assert broker.cancel_calls == broker.review_calls == broker.place_calls == 0
    assert any(
        "account truth/invariant check failed" in item["summary"]
        for item in store.recent_receipts(20)
    )
    store.close()


@pytest.mark.asyncio
async def test_auto_shadow_blocks_when_real_order_capability_is_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: START)
    broker = AutoShadowBroker()
    controller, store = _controller(tmp_path, broker, live_trading_enabled=True)

    assert not await controller.auto_start_shadow()
    assert broker.connect_calls == 0
    assert any("Real-order capability" in item["summary"] for item in store.recent_receipts(10))
    store.close()


@pytest.mark.asyncio
async def test_shadow_only_broker_facade_rejects_all_write_methods() -> None:
    wrapped = AutoShadowBroker()
    facade = ShadowOnlyBroker(wrapped)

    with pytest.raises(BrokerError, match="review"):
        await facade.review_order("1000", None)  # type: ignore[arg-type]
    with pytest.raises(BrokerError, match="placement"):
        await facade.place_order("1000", None)  # type: ignore[arg-type]
    with pytest.raises(BrokerError, match="cancellation"):
        await facade.cancel_order("1000", "order")
    assert wrapped.review_calls == wrapped.place_calls == wrapped.cancel_calls == 0


@pytest.mark.asyncio
async def test_noninteractive_broker_never_starts_oauth_callback_server(monkeypatch) -> None:
    callback_calls = {"start": 0, "stop": 0}
    auth_handlers = {}

    class FakeCallbackServer:
        def start(self) -> None:
            callback_calls["start"] += 1

        def stop(self) -> None:
            callback_calls["stop"] += 1

    class FakeAuthProvider:
        def __init__(self, **kwargs) -> None:
            auth_handlers["redirect"] = kwargs["redirect_handler"]
            auth_handlers["callback"] = kwargs["callback_handler"]

    def reject_transport(*args, **kwargs):
        raise RuntimeError("transport stopped for test")

    monkeypatch.setattr(robinhood_mcp, "CredentialTokenStorage", object)
    monkeypatch.setattr(robinhood_mcp, "OAuthCallbackServer", FakeCallbackServer)
    monkeypatch.setattr(robinhood_mcp, "OAuthClientProvider", FakeAuthProvider)
    monkeypatch.setattr(robinhood_mcp, "streamablehttp_client", reject_transport)
    broker = RobinhoodMCPBroker(allow_interactive_auth=False)
    ready = asyncio.get_running_loop().create_future()

    with pytest.raises(RuntimeError, match="transport stopped"):
        await broker._session_owner(ready)
    assert isinstance(ready.exception(), RuntimeError)
    assert callback_calls == {"start": 0, "stop": 0}
    with pytest.raises(BrokerError, match="browser consent is blocked"):
        await auth_handlers["redirect"]("https://example.invalid/consent")
    with pytest.raises(BrokerError, match="OAuth callback is blocked"):
        await auth_handlers["callback"]()


def test_auto_shadow_start_deadline_and_session_close_use_eastern_clock(tmp_path) -> None:
    broker = AutoShadowBroker()
    controller, store = _controller(tmp_path, broker)
    # Use explicit UTC instants so the assertion remains independent of the host timezone.
    before_deadline = datetime(2026, 8, 11, 13, 34, 59, tzinfo=UTC)
    at_deadline = datetime(2026, 8, 11, 13, 35, 0, tzinfo=UTC)
    before_close = datetime(2026, 8, 11, 19, 59, 59, tzinfo=UTC)
    at_close = datetime(2026, 8, 11, 20, 0, 0, tzinfo=UTC)

    assert controller.auto_shadow_start_allowed(before_deadline)
    assert not controller.auto_shadow_start_allowed(at_deadline)
    assert not controller.auto_shadow_session_complete(before_close)
    assert controller.auto_shadow_session_complete(at_close)
    store.close()


def test_auto_shadow_rejects_stale_or_nonfinite_quote_snapshot(tmp_path) -> None:
    broker = AutoShadowBroker()
    controller, store = _controller(tmp_path, broker)
    stale = {
        symbol: Quote(symbol, 100.0, 100.02, 100.01, START - timedelta(seconds=9))
        for symbol in ("QQQ", "TQQQ", "SQQQ")
    }
    invalid = {
        symbol: Quote(symbol, 100.0, 100.02, 100.01, START)
        for symbol in ("QQQ", "TQQQ", "SQQQ")
    }
    invalid["TQQQ"] = Quote("TQQQ", float("nan"), 100.02, 100.01, START)

    with pytest.raises(BrokerError, match="not fresh"):
        controller._validated_shadow_quotes(stale, START)
    with pytest.raises(ValueError, match="finite and positive"):
        controller._validated_shadow_quotes(invalid, START)
    store.close()


@pytest.mark.parametrize("maximum_age", [0.0, -1.0, float("nan"), float("inf"), True])
def test_app_config_rejects_nonpositive_or_nonfinite_quote_age(maximum_age) -> None:
    config = AppConfig(default_max_quote_age_seconds=maximum_age)

    with pytest.raises(ValueError, match="finite and positive"):
        config.validate_cadence()


@pytest.mark.asyncio
async def test_main_window_session_close_uses_read_only_shutdown(tmp_path, monkeypatch) -> None:
    clock = FakeClock(START)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    _app = QApplication.instance() or QApplication([])
    broker = AutoShadowBroker(quote_clock=lambda: clock.now)
    controller, store = _controller(tmp_path, broker)
    assert await controller.auto_start_shadow()
    assert controller._shadow is not None
    bullish = Signal(Regime.BULLISH, 1.0, "daily-flat regression")
    entry_start = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)  # 10:00 ET, after entry blackout.
    for offset in range(4):
        timestamp = entry_start + timedelta(seconds=offset * controller.config.bar_seconds)
        controller._shadow.on_bar(timestamp, bullish, await broker.get_quotes(["TQQQ", "SQQQ"]))
    assert controller._shadow.state.position is not None

    clock.now = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)
    controller.snapshot.quotes = await broker.get_quotes(["QQQ", "TQQQ", "SQQQ"])
    window = MainWindow(controller, controller.config, auto_shadow=False)
    window._on_snapshot(controller.snapshot)

    await window._finish_auto_shadow_session()

    assert controller._shadow.state.position is None
    assert controller._shadow.state.fills[-1].side == "sell"
    assert "DAILY FLAT" in controller._shadow.state.fills[-1].reason
    assert broker.disconnect_calls >= 1
    assert broker.cancel_calls == broker.review_calls == broker.place_calls == 0
    assert not controller.snapshot.connected
    assert any("AUTO SHADOW DAILY FLAT" in item["summary"] for item in store.recent_receipts(20))
    store.close()


@pytest.mark.asyncio
async def test_controller_shadow_fills_completed_bar_decision_at_first_causal_next_open(
    tmp_path, monkeypatch
) -> None:
    bar_seconds = 5
    first_open = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)  # 10:00 ET
    clock = FakeClock(first_open)

    class TraceBroker(AutoShadowBroker):
        async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
            tick = max(0, int((clock.now - first_open).total_seconds() // bar_seconds))
            mids = {
                "QQQ": 100.0 + min(tick, 4),
                "TQQQ": 50.0 + 10.0 * min(tick, 4),
                "SQQQ": 40.0 - min(tick, 4),
            }
            timestamps = {
                "QQQ": clock.now,
                "TQQQ": clock.now + timedelta(seconds=2),
                "SQQQ": clock.now - timedelta(seconds=1),
            }
            return {
                symbol: Quote(
                    symbol,
                    mids[symbol] - 0.01,
                    mids[symbol] + 0.01,
                    mids[symbol],
                    timestamps[symbol],
                )
                for symbol in symbols
            }

    class BullishTraceStrategy:
        def on_bar(self, bar: Bar) -> Signal:
            return Signal(Regime.BULLISH, 1.0, "synthetic timing trace", timestamp=bar.start)

    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: clock.now)
    broker = TraceBroker(quote_clock=lambda: clock.now)
    controller, store = _controller(
        tmp_path,
        broker,
        bar_seconds=bar_seconds,
        trade_every_bars=3,
    )
    await controller.connect()  # Seeds the first analysis-bar bucket at first_open.
    controller.strategy = BullishTraceStrategy()
    controller.start_shadow()
    assert controller._shadow is not None

    # Completed bars t=0 and t=1 do not yet satisfy the three-bar decision stride.
    for offset in (1, 2):
        clock.now = first_open + timedelta(seconds=offset * bar_seconds)
        await controller.refresh_quotes(evaluate=False)
        assert controller._shadow.state.fills == []

    # At the quote/open for t+1, BarBuilder emits completed bar t and the decision
    # fills immediately at that causal quote. It must not wait for the t+2 quote.
    clock.now = first_open + timedelta(seconds=3 * bar_seconds)
    await controller.refresh_quotes(evaluate=False)
    fills = controller._shadow.state.fills
    assert [(fill.side, fill.symbol, fill.timestamp) for fill in fills] == [
        ("buy", "TQQQS", clock.now + timedelta(seconds=2))
    ]
    assert controller.snapshot.last_analysis_at == clock.now - timedelta(seconds=bar_seconds)
    assert fills[0].timestamp > controller.snapshot.last_analysis_at
    assert fills[0].timestamp >= controller.snapshot.quotes["TQQQ"].timestamp
    assert fills[0].price == pytest.approx((80.0 + 0.01) * (1.0 + 2.0 / 10_000.0))

    clock.now += timedelta(seconds=bar_seconds)
    await controller.refresh_quotes(evaluate=False)
    assert len(controller._shadow.state.fills) == 1

    # The timing change must retain the explicit virtual-only daily flatten and
    # must never cross the read-only broker facade.
    clock.now = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)
    controller.snapshot.quotes = {
        symbol: replace(quote, timestamp=clock.now)
        for symbol, quote in (await broker.get_quotes(["QQQ", "TQQQ", "SQQQ"])).items()
    }
    controller.stop_shadow("synthetic timing trace complete", flatten_virtual=True, timestamp=clock.now)
    assert controller._shadow.state.position is None
    assert [(fill.side, fill.symbol) for fill in controller._shadow.state.fills] == [
        ("buy", "TQQQS"),
        ("sell", "TQQQS"),
    ]
    assert controller._shadow.state.fills[-1].timestamp == clock.now
    assert broker.review_calls == broker.place_calls == broker.cancel_calls == 0
    await controller.disconnect_shadow_only()
    store.close()

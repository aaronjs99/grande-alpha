import asyncio
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
    controller.strategy.bars.append(Bar("QQQ", START, 1, 1, 1, 1, 1))
    controller._analysis_sequence = 9
    controller._last_trade_decision_sequence = 6

    assert await controller.auto_start_shadow()
    assert controller.snapshot.connected
    assert controller.snapshot.shadow_running
    assert controller.snapshot.live_status == "LOCKED"
    assert controller.risk.grant is None
    assert not controller.snapshot.strategy_running
    assert not controller.strategy.bars
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
    controller, store = _controller(tmp_path, broker, clock=clock)
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

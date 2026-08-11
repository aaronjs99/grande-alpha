from __future__ import annotations

import json
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

import grande_alpha.controller as controller_module
import grande_alpha.risk as risk_module
import grande_alpha.ui.main_window as main_window_module
from grande_alpha.broker.base import Broker, BrokerError
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.models import (
    Account,
    BrokerOrder,
    LiveGrant,
    OrderIntent,
    OrderReview,
    Portfolio,
    Position,
    Quote,
    Regime,
    Signal,
)
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow

NOW = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)  # Tuesday, 11:00 AM ET.
ACCOUNT_NUMBER = "123456789"


def _account(number: str = ACCOUNT_NUMBER) -> Account:
    return Account(number, "Agentic", "cash", True, "active")


def _quotes(timestamp: datetime = NOW) -> dict[str, Quote]:
    return {
        "QQQ": Quote("QQQ", 499.98, 500.02, 500.0, timestamp),
        "TQQQ": Quote("TQQQ", 49.99, 50.01, 50.0, timestamp),
        "SQQQ": Quote("SQQQ", 39.99, 40.01, 40.0, timestamp),
    }


def _order(
    order_id: str = "broker-order-1",
    *,
    state: str = "queued",
    ref_id: str = "",
) -> BrokerOrder:
    raw = {"ref_id": ref_id} if ref_id else {}
    return BrokerOrder(
        order_id=order_id,
        symbol="TQQQ",
        side="buy",
        state=state,
        quantity=None,
        dollar_amount=10.0,
        average_price=None,
        created_at=NOW,
        raw=raw,
    )


class DeterministicBroker(Broker):
    def __init__(self) -> None:
        self.accounts = [_account()]
        self.portfolio = Portfolio(100.0, 100.0, 100.0)
        self.quotes = _quotes()
        self.positions: list[Position] = []
        self.orders: list[BrokerOrder] = []
        self.order_snapshots: deque[list[BrokerOrder]] = deque()
        self.connect_calls = 0
        self.review_calls: list[str] = []
        self.place_calls: list[str] = []
        self.placed_intents: list[OrderIntent] = []
        self.cancel_calls: list[str] = []
        self.place_exception: BaseException | None = None
        self.terminal_on_cancel = True

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        return None

    async def get_accounts(self) -> list[Account]:
        return list(self.accounts)

    async def get_portfolio(self, account_number: str) -> Portfolio:
        assert account_number == ACCOUNT_NUMBER
        return self.portfolio

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    async def get_positions(self, account_number: str) -> list[Position]:
        assert account_number == ACCOUNT_NUMBER
        return list(self.positions)

    async def get_orders(self, account_number: str) -> list[BrokerOrder]:
        assert account_number == ACCOUNT_NUMBER
        if self.order_snapshots:
            if len(self.order_snapshots) > 1:
                self.orders = list(self.order_snapshots.popleft())
            else:
                self.orders = list(self.order_snapshots[0])
        return list(self.orders)

    async def review_order(self, account_number: str, intent: OrderIntent) -> OrderReview:
        assert account_number == ACCOUNT_NUMBER
        self.review_calls.append(intent.ref_id)
        return OrderReview(intent, "", {}, {})

    async def place_order(self, account_number: str, intent: OrderIntent) -> BrokerOrder:
        assert account_number == ACCOUNT_NUMBER
        self.place_calls.append(intent.ref_id)
        self.placed_intents.append(intent)
        if self.place_exception is not None:
            raise self.place_exception
        order = _order(ref_id=intent.ref_id)
        self.orders = [order, *[item for item in self.orders if item.order_id != order.order_id]]
        return order

    async def cancel_order(self, account_number: str, order_id: str) -> bool:
        assert account_number == ACCOUNT_NUMBER
        self.cancel_calls.append(order_id)
        if self.terminal_on_cancel:
            self.orders = [
                replace(order, state="cancelled") if order.order_id == order_id else order
                for order in self.orders
            ]
        return True


@pytest.fixture(autouse=True)
def _fixed_live_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(risk_module, "utc_now", lambda: NOW)


def _controller(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    broker: DeterministicBroker | None = None,
    name: str = "audit",
) -> tuple[TradingController, DeterministicBroker, AuditStore, LiveGrant]:
    active_broker = broker or DeterministicBroker()
    store = AuditStore(tmp_path / f"{name}.db")
    config = AppConfig(
        broker_connection_enabled=True,
        live_trading_enabled=True,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
    )
    controller = TradingController(active_broker, config, store)
    controller.snapshot.connected = True
    controller.snapshot.account = _account()
    controller.snapshot.portfolio = active_broker.portfolio
    controller.snapshot.positions = list(active_broker.positions)
    controller.snapshot.orders = list(active_broker.orders)
    controller.snapshot.quotes = dict(active_broker.quotes)
    controller.snapshot.last_reconcile_at = NOW
    draft = LiveGrant(
        account_number=ACCOUNT_NUMBER,
        starts_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
        max_order_notional=25.0,
        max_total_exposure=40.0,
        max_daily_loss=5.0,
        max_trades=8,
        max_orders_per_minute=4,
        max_spread_bps=20.0,
        max_quote_age_seconds=8.0,
        max_daily_notional=75.0,
        strategy_fingerprint="a" * 64,
    )
    grant = replace(draft, strategy_fingerprint=controller.current_strategy_fingerprint(draft))
    monkeypatch.setattr(controller, "live_evidence_ready", lambda grant=None: True)
    return controller, active_broker, store, grant


def _intent(ref_id: str) -> OrderIntent:
    return OrderIntent(
        ref_id=ref_id,
        symbol="TQQQ",
        side="buy",
        reason="deterministic live-autonomy fault test",
        dollar_amount=10.0,
    )


def _qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.asyncio
async def test_multiple_active_agentic_accounts_fail_connection(tmp_path) -> None:
    broker = DeterministicBroker()
    broker.accounts = [_account(), _account("987654321")]
    store = AuditStore(tmp_path / "multiple-accounts.db")
    controller = TradingController(
        broker,
        AppConfig(broker_connection_enabled=True),
        store,
    )

    with pytest.raises(BrokerError, match="exactly one active Agentic account"):
        await controller.connect()

    assert broker.connect_calls == 1
    assert not controller.snapshot.connected
    assert controller.snapshot.account is None
    store.close()


def test_live_grant_requires_flat_order_free_and_fresh_exact_quotes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = (
        (
            "held-position",
            [Position("TQQQ", 0.2, 0.2, 50.0)],
            [],
            _quotes(),
            "flat TQQQ/SQQQ account",
        ),
        ("open-order", [], [_order()], _quotes(), "zero nonterminal Agentic orders"),
        (
            "missing-quote",
            [],
            [],
            {symbol: quote for symbol, quote in _quotes().items() if symbol != "QQQ"},
            "exact QQQ/TQQQ/SQQQ quotes",
        ),
        (
            "stale-quote",
            [],
            [],
            _quotes(NOW - timedelta(seconds=20)),
            "venue quote is not fresh",
        ),
    )

    for name, positions, orders, quotes, message in cases:
        controller, _broker, store, grant = _controller(
            tmp_path,
            monkeypatch,
            name=name,
        )
        controller.snapshot.positions = positions
        controller.snapshot.orders = orders
        controller.snapshot.quotes = quotes
        with pytest.raises(RuntimeError, match=message):
            controller.authorize_live(grant)
        assert controller.risk.grant is None
        store.close()

    controller, _broker, store, grant = _controller(tmp_path, monkeypatch, name="passing-preflight")
    controller.authorize_live(grant)
    assert controller.risk.grant == grant
    assert controller.snapshot.live_status == "LIVE"
    store.close()


def test_live_authority_rejects_premarket_and_start_discards_prestart_pipeline(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="session-clean-start",
    )
    premarket = datetime(2026, 8, 11, 13, 20, tzinfo=UTC)  # 9:20 AM ET.
    controller.snapshot.quotes = _quotes(premarket)
    controller.snapshot.last_reconcile_at = premarket
    premarket_grant = replace(
        grant,
        starts_at=premarket - timedelta(minutes=1),
        expires_at=premarket + timedelta(hours=1),
    )
    monkeypatch.setattr(controller_module, "utc_now", lambda: premarket)
    monkeypatch.setattr(risk_module, "utc_now", lambda: premarket)

    with pytest.raises(RuntimeError, match="inside the regular-session entry window"):
        controller.authorize_live(premarket_grant)
    assert controller.risk.grant is None

    monkeypatch.setattr(controller_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(risk_module, "utc_now", lambda: NOW)
    controller.snapshot.quotes = _quotes(NOW - timedelta(seconds=1))
    controller.snapshot.last_reconcile_at = NOW
    controller.authorize_live(grant)
    old_builder = controller.bar_builder
    controller._analysis_sequence = 7
    controller._last_trade_decision_sequence = 4
    controller.start_strategy()

    assert controller.bar_builder is not old_builder
    assert controller._analysis_sequence == 0
    assert controller._last_trade_decision_sequence == 0
    assert controller._last_qqq_timestamp == NOW
    assert controller.snapshot.last_analysis_at is None
    store.close()


@pytest.mark.asyncio
async def test_ambiguous_placement_is_durable_revokes_authority_and_never_retries(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = DeterministicBroker()
    broker.place_exception = TimeoutError("provider response timed out after possible acceptance")
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="ambiguous-place",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    intent = _intent("ambiguous-ref")

    assert await controller._submit(intent, controller.snapshot.quotes["TQQQ"]) is None

    unresolved = store.unresolved_order_intents(ACCOUNT_NUMBER)
    assert [row["ref_id"] for row in unresolved] == [intent.ref_id]
    assert unresolved[0]["broker_state"] == "submission_uncertain"
    assert controller.risk.grant is None
    assert controller.risk.session_status() == "LOCKED"
    assert not controller.snapshot.strategy_running
    assert intent.ref_id in controller._uncertain_submission_refs
    assert broker.place_calls == [intent.ref_id]

    # Re-entering the exact controller path cannot cross review or placement again.
    assert await controller._submit(intent, controller.snapshot.quotes["TQQQ"]) is None
    assert broker.review_calls == [intent.ref_id]
    assert broker.place_calls == [intent.ref_id]
    store.close()


@pytest.mark.asyncio
async def test_successful_placement_blocks_decisions_until_broker_reconciliation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="post-submit-reconcile",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    intent = _intent("successful-ref")

    order = await controller._submit(intent, controller.snapshot.quotes["TQQQ"])
    assert order is not None
    assert controller._submission_reconcile_required == {intent.ref_id: order.order_id}

    controller.snapshot.signal = Signal(Regime.BULLISH, 1.0, "stay bullish", NOW)
    controller.snapshot.last_analysis_at = NOW
    controller._analysis_sequence = controller.config.trade_every_bars
    controller._last_trade_decision_sequence = 0
    await controller._evaluate_and_trade()

    assert broker.place_calls == [intent.ref_id]
    pair_receipt = next(
        receipt for receipt in store.recent_receipts() if receipt["category"] == "pair_decision"
    )
    assert "Waiting for post-submission broker reconciliation" in pair_receipt["summary"]

    broker.orders = [replace(order, state="filled")]
    await controller.reconcile()
    assert controller._submission_reconcile_required == {}
    assert controller._uncertain_submission_refs == set()
    store.close()


@pytest.mark.asyncio
async def test_stop_cancel_treats_pending_cancelled_as_nonterminal_and_fails_closed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(controller_module.asyncio, "sleep", no_wait)
    broker = DeterministicBroker()
    broker.terminal_on_cancel = False
    broker.orders = [_order("pending-cancel", state="pending_cancelled")]
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="pending-cancel",
    )

    verified = await controller.stop_and_cancel("fault-test stop")

    assert not verified
    assert broker.cancel_calls == []
    receipt = next(
        receipt for receipt in store.recent_receipts() if receipt["category"] == "kill_switch"
    )
    payload = json.loads(receipt["payload_json"])
    assert payload["target_order_ids"] == ["pending-cancel"]
    assert payload["remaining_order_ids"] == ["pending-cancel"]
    store.close()


@pytest.mark.asyncio
async def test_stop_cancel_succeeds_only_after_pending_cancel_is_observed_terminal(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = DeterministicBroker()
    pending = _order("eventually-cancelled", state="pending_cancelled")
    broker.order_snapshots.extend([[pending], [replace(pending, state="cancelled")]])
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="terminal-cancel",
    )

    assert await controller.stop_and_cancel("fault-test stop")
    assert broker.cancel_calls == []
    assert controller.snapshot.orders[0].state == "cancelled"
    store.close()


@pytest.mark.asyncio
async def test_stale_live_quote_batch_revokes_authority_and_cancels_open_orders(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="stale-live-batch",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    broker.orders = [_order("risk-order", state="queued")]
    broker.quotes = _quotes(NOW - timedelta(seconds=20))

    await controller.refresh_quotes()

    assert broker.cancel_calls == ["risk-order"]
    assert broker.orders[0].state == "cancelled"
    assert controller.risk.grant is None
    assert controller.snapshot.live_status == "LOCKED"
    assert not controller.snapshot.strategy_running
    summaries = [receipt["summary"] for receipt in store.recent_receipts()]
    assert any("Quote refresh failed" in summary and "not fresh" in summary for summary in summaries)
    store.close()


@pytest.mark.asyncio
async def test_session_authority_can_be_paused_resumed_and_irrevocably_revoked(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="pause-revoke",
    )
    controller.authorize_live(grant)
    controller.start_strategy()

    controller.pause_live_authority("operator pause")
    assert controller.snapshot.live_status == "PAUSED"
    assert not controller.snapshot.strategy_running
    assert controller.risk.grant == grant

    controller.resume_live_authority("operator resume")
    assert controller.snapshot.live_status == "LIVE"
    assert controller.risk.grant == grant

    assert await controller.revoke_live_authority("operator revoke")
    assert controller.snapshot.live_status == "LOCKED"
    assert controller.risk.grant is None
    actions = {
        json.loads(receipt["payload_json"])["action"]
        for receipt in store.recent_receipts()
        if receipt["category"] == "authority_action"
    }
    assert {"authority_paused", "authority_resumed", "authority_revoked"} <= actions
    store.close()


@pytest.mark.asyncio
async def test_daily_loss_limit_submits_one_exit_and_never_retries_stale_inventory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="loss-limit-exit",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    position = Position("TQQQ", 0.4, 0.4, 50.0)
    controller.snapshot.positions = [position]
    broker.positions = [position]
    losing = Portfolio(94.0, 94.0, 94.0)
    controller.snapshot.portfolio = losing
    broker.portfolio = losing
    controller.risk.update_portfolio(losing)

    await controller._evaluate_and_trade()

    assert controller.risk.session_status() == "LOCKED"
    assert controller.risk.grant is None
    assert len(broker.placed_intents) == 1
    assert broker.placed_intents[0].symbol == "TQQQ"
    assert broker.placed_intents[0].side == "sell"
    assert broker.placed_intents[0].quantity == 0.4
    assert broker.placed_intents[0].dollar_amount is None

    # Even if the next positions response is stale while the order is terminal,
    # the revoked one-shot authority cannot submit a duplicate sell.
    broker.orders = [replace(broker.orders[0], state="filled")]
    await controller.reconcile()
    await controller._evaluate_and_trade()

    assert len(broker.placed_intents) == 1
    assert controller.snapshot.positions == [position]

    broker.positions = []
    await controller.reconcile()

    assert controller.risk.grant is None
    assert controller.risk.session_status() == "LOCKED"
    assert not controller.snapshot.strategy_running
    summaries = [receipt["summary"] for receipt in store.recent_receipts()]
    assert any("automatic retry is disabled" in summary for summary in summaries)
    store.close()


@pytest.mark.asyncio
async def test_regular_exit_is_one_shot_when_filled_order_races_stale_inventory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="regular-one-shot-exit",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    position = Position("TQQQ", 0.4, 0.4, 50.0)
    controller.snapshot.positions = [position]
    broker.positions = [position]
    controller._analysis_sequence = controller.config.trade_every_bars

    await controller._evaluate_and_trade()

    assert len(broker.placed_intents) == 1
    assert broker.placed_intents[0].side == "sell"
    assert controller.risk.grant is None
    assert controller.risk.session_status() == "LOCKED"

    broker.orders = [replace(broker.orders[0], state="filled")]
    await controller.reconcile()
    controller._analysis_sequence += controller.config.trade_every_bars
    await controller._evaluate_and_trade()

    assert controller.snapshot.positions == [position]
    assert len(broker.placed_intents) == 1
    store.close()


@pytest.mark.asyncio
async def test_daily_loss_cancels_pending_buy_before_any_liquidation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="loss-limit-pending-buy",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    position = Position("TQQQ", 0.4, 0.4, 50.0)
    controller.snapshot.positions = [position]
    broker.positions = [position]
    pending_buy = _order("pending-buy", state="queued", ref_id="pending-buy-ref")
    controller.snapshot.orders = [pending_buy]
    broker.orders = [pending_buy]
    controller._submission_reconcile_required[pending_buy.raw["ref_id"]] = pending_buy.order_id
    losing = Portfolio(94.0, 94.0, 94.0)
    controller.snapshot.portfolio = losing
    broker.portfolio = losing
    controller.risk.update_portfolio(losing)

    await controller._evaluate_and_trade()

    assert broker.cancel_calls == ["pending-buy"]
    assert broker.orders[0].state == "cancelled"
    assert broker.placed_intents == []
    assert controller.risk.grant is None
    assert controller.risk.session_status() == "LOCKED"
    assert not controller.snapshot.strategy_running
    summaries = [receipt["summary"] for receipt in store.recent_receipts()]
    assert any("prior submission" in summary for summary in summaries)
    store.close()


@pytest.mark.asyncio
async def test_reconcile_rejects_inconsistent_leveraged_inventory_without_selling(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="inconsistent-sellable-inventory",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    broker.positions = [Position("TQQQ", 0.4, 0.6, 50.0)]

    await controller.reconcile()

    assert controller.risk.grant is None
    assert controller.risk.session_status() == "LOCKED"
    assert not controller.snapshot.strategy_running
    assert broker.placed_intents == []
    summaries = [receipt["summary"] for receipt in store.recent_receipts()]
    assert any("sellable quantity exceeds held TQQQ inventory" in summary for summary in summaries)
    store.close()


def test_live_readiness_tab_renders_operator_gates_and_next_actions(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _qt_app()
    controller, _broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        name="live-readiness-ui",
    )
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)

    tab_index = window.tabs.indexOf(window.live_readiness_table)
    assert tab_index >= 0
    assert window.tabs.tabText(tab_index) == "Live Readiness"
    assert [
        window.live_readiness_table.horizontalHeaderItem(column).text()
        for column in range(window.live_readiness_table.columnCount())
    ] == ["Gate", "Status", "Observed", "Next action"]

    rows = {
        window.live_readiness_table.item(row, 0).text(): (
            window.live_readiness_table.item(row, 1).text(),
            window.live_readiness_table.item(row, 2).text(),
            window.live_readiness_table.item(row, 3).text(),
        )
        for row in range(window.live_readiness_table.rowCount())
    }
    assert {
        "Broker capability",
        "Real-order capability",
        "Exact Agentic account",
        "Fresh account truth",
        "Flat leveraged inventory",
        "No working Agentic orders",
        "No ambiguous placements",
        "Fresh exact venue quotes",
        "Autonomous pilot route",
        "Immutable runtime contract",
        "Positive exact evidence",
        "F-1 / tax suitability",
    } <= rows.keys()
    assert rows["Fresh exact venue quotes"][0] == "PASS"
    assert rows["Fresh exact venue quotes"][1] == "QQQ/TQQQ/SQQQ exact and fresh"
    assert rows["F-1 / tax suitability"][0] == "USER ACTION"
    assert "UCLA DSO" in rows["F-1 / tax suitability"][2]

    window._closing_after_cleanup = True
    window.close()
    store.close()


def test_authorize_and_start_is_one_click_with_exact_operator_label(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _qt_app()
    controller, _broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        name="authorize-start-ui",
    )
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)
    expected_grant = object()
    calls: list[tuple[str, object | None]] = []

    class AcceptingGrantDialog:
        class DialogCode:
            Accepted = 1

        def __init__(self, *_args, strategy_fingerprint: str, **_kwargs) -> None:
            assert strategy_fingerprint == controller.current_strategy_fingerprint()

        def exec(self) -> int:
            return self.DialogCode.Accepted

        def grant(self) -> object:
            return expected_grant

    monkeypatch.setattr(main_window_module, "LiveGrantDialog", AcceptingGrantDialog)
    monkeypatch.setattr(
        controller,
        "authorize_live",
        lambda grant: calls.append(("authorize", grant)),
    )
    monkeypatch.setattr(
        controller,
        "start_strategy",
        lambda: calls.append(("start", None)),
    )

    assert window.authorize_button.text().replace("&&", "&") == "Authorize & Start Live Session"
    assert (
        window.authorize_action.text().replace("&&", "&").removesuffix("…")
        == "Authorize & Start Live Session"
    )
    assert window.authorize_button.isEnabled()
    window.authorize_button.click()
    assert calls == [("authorize", expected_grant), ("start", None)]

    window._closing_after_cleanup = True
    window.close()
    store.close()


@pytest.mark.asyncio
async def test_live_window_keeps_pause_resume_revoke_and_stop_controls_visible(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _qt_app()
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="authority-controls-ui",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)

    panel = window.authority_controls
    assert window.kill_button.text() == "STOP + CANCEL"
    assert not window.kill_button.isHidden()
    assert window.kill_button.isEnabled()
    assert panel.pause_button.text() == "Pause authority"
    assert not panel.pause_button.isHidden()
    assert panel.pause_button.isEnabled()
    assert panel.revoke_button.text() == "Revoke authority"
    assert not panel.revoke_button.isHidden()
    assert panel.revoke_button.isEnabled()

    panel.pause_button.click()
    assert controller.snapshot.live_status == "PAUSED"
    assert panel.pause_button.isHidden()
    assert panel.resume_button.text() == "Resume authority"
    assert not panel.resume_button.isHidden()
    assert panel.resume_button.isEnabled()
    assert not panel.revoke_button.isHidden()
    assert not window.kill_button.isHidden()

    panel.resume_button.click()
    assert controller.snapshot.live_status == "LIVE"
    assert not panel.pause_button.isHidden()
    assert panel.pause_button.isEnabled()

    assert await controller.revoke_live_authority("UI fault-test revoke")
    assert controller.snapshot.live_status == "LOCKED"
    window._closing_after_cleanup = True
    window.close()
    store.close()


@pytest.mark.asyncio
async def test_window_close_stays_blocked_when_broker_cleanup_is_unverified(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qt_app()
    controller, _broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        name="blocked-close-ui",
    )
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)
    window.show()
    app.processEvents()
    captured_tasks = []
    critical_messages: list[tuple[str, str]] = []

    async def unverified_disconnect() -> None:
        raise BrokerError("terminal broker cleanup remains unverified")

    def capture_task(coroutine):
        captured_tasks.append(coroutine)
        return SimpleNamespace()

    monkeypatch.setattr(controller, "disconnect", unverified_disconnect)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda _parent, title, text: critical_messages.append((title, text)),
    )
    monkeypatch.setattr(
        main_window_module,
        "asyncio",
        SimpleNamespace(create_task=capture_task),
    )

    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert len(captured_tasks) == 1
    await captured_tasks[0]

    assert not window._closing_after_cleanup
    assert not window.isHidden()
    assert critical_messages
    assert critical_messages[0][0] == "Exit blocked — broker cleanup is not verified"
    assert "retry STOP + CANCEL" in critical_messages[0][1]

    window._closing_after_cleanup = True
    window.close()
    store.close()

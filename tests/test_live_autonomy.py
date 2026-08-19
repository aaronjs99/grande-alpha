from __future__ import annotations

import asyncio
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
import grande_alpha.storage as storage_module
import grande_alpha.ui.main_window as main_window_module
from grande_alpha.broker.base import Broker, BrokerError
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.models import (
    Account,
    BrokerExecution,
    BrokerOrder,
    LiveGrant,
    OrderConfirmationDecision,
    OrderConfirmationRequest,
    OrderIntent,
    OrderReview,
    Portfolio,
    Position,
    Quote,
    Regime,
    Signal,
)
from grande_alpha.storage import AuditStore
from grande_alpha.ui.dialogs import OrderConfirmationDialog
from grande_alpha.ui.main_window import MainWindow

NOW = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)  # Tuesday, 11:00 AM ET.
ACCOUNT_NUMBER = "123456789"


def _account(number: str = ACCOUNT_NUMBER) -> Account:
    return Account(number, "Agentic", "cash", True, "active")


async def _accept_reviewed_order(
    request: OrderConfirmationRequest,
) -> OrderConfirmationDecision:
    return OrderConfirmationDecision(
        preview_id=request.preview_id,
        accepted=True,
        typed_phrase=request.confirmation_phrase,
        confirmed_at=request.requested_at,
    )


def _quotes(timestamp: datetime = NOW) -> dict[str, Quote]:
    return {
        "QQQ": Quote("QQQ", 499.98, 500.02, 500.0, timestamp, timestamp, timestamp),
        "TQQQ": Quote("TQQQ", 49.99, 50.01, 50.0, timestamp, timestamp, timestamp),
        "SQQQ": Quote("SQQQ", 39.99, 40.01, 40.0, timestamp, timestamp, timestamp),
    }


def _order(
    order_id: str = "broker-order-1",
    *,
    state: str = "queued",
    ref_id: str = "",
    symbol: str = "TQQQ",
    side: str = "buy",
    quantity: float | None = None,
    dollar_amount: float | None = 10.0,
    average_price: float | None = None,
    executions: tuple[BrokerExecution, ...] = (),
    cumulative_quantity: float | None = None,
    last_transaction_at: datetime | None = None,
    created_at: datetime = NOW,
    placed_agent: str = "agentic",
) -> BrokerOrder:
    raw = {
        "type": "market",
        "market_hours": "regular_hours",
        "time_in_force": "gfd",
    }
    if ref_id:
        raw["ref_id"] = ref_id
    return BrokerOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        state=state,
        quantity=quantity,
        dollar_amount=dollar_amount,
        average_price=average_price,
        created_at=created_at,
        raw=raw,
        executions=executions,
        cumulative_quantity=cumulative_quantity,
        last_transaction_at=last_transaction_at,
        placed_agent=placed_agent,
    )


def _observed_fill(
    order: BrokerOrder,
    *,
    quantity: float,
    price: float,
    execution_id: str,
    state: str = "filled",
    timestamp: datetime = NOW,
) -> BrokerOrder:
    execution = BrokerExecution(execution_id, quantity, price, 0.0, timestamp)
    return replace(
        order,
        state=state,
        average_price=price,
        executions=(execution,),
        cumulative_quantity=quantity,
        last_transaction_at=timestamp,
    )


def _seed_holding(
    store: AuditStore,
    *,
    symbol: str,
    quantity: float,
    price: float,
    order_id: str,
    timestamp: datetime = NOW - timedelta(minutes=5),
) -> None:
    store.record_broker_order_executions(
        ACCOUNT_NUMBER,
        _order(
            order_id=order_id,
            state="filled",
            symbol=symbol,
            side="buy",
            quantity=quantity,
            dollar_amount=None,
            average_price=price,
            executions=(
                BrokerExecution(f"{order_id}-execution", quantity, price, 0.0, timestamp),
            ),
            cumulative_quantity=quantity,
            last_transaction_at=timestamp,
            created_at=timestamp,
        ),
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
        return OrderReview(intent, "", {}, self.quotes[intent.symbol], {})

    async def place_order(self, account_number: str, intent: OrderIntent) -> BrokerOrder:
        assert account_number == ACCOUNT_NUMBER
        self.place_calls.append(intent.ref_id)
        self.placed_intents.append(intent)
        if self.place_exception is not None:
            raise self.place_exception
        order = _order(
            order_id=f"broker-order-{len(self.place_calls)}",
            ref_id=intent.ref_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            dollar_amount=intent.dollar_amount,
        )
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


class BlockingSafeReadBroker(DeterministicBroker):
    def __init__(self) -> None:
        super().__init__()
        self.portfolio_read_started = asyncio.Event()
        self.release_portfolio_read = asyncio.Event()

    async def get_portfolio(self, account_number: str) -> Portfolio:
        assert account_number == ACCOUNT_NUMBER
        self.portfolio_read_started.set()
        await self.release_portfolio_read.wait()
        return self.portfolio


class BlockingQuoteBroker(DeterministicBroker):
    def __init__(self) -> None:
        super().__init__()
        self.quote_read_started = asyncio.Event()
        self.release_quote_read = asyncio.Event()
        self.block_next_quote_read = True

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if self.block_next_quote_read:
            self.block_next_quote_read = False
            self.quote_read_started.set()
            await self.release_quote_read.wait()
        return await super().get_quotes(symbols)


class MutatingCancelBroker(DeterministicBroker):
    def __init__(self) -> None:
        super().__init__()
        self.mutate_order_id = ""

    async def cancel_order(self, account_number: str, order_id: str) -> bool:
        accepted = await super().cancel_order(account_number, order_id)
        if self.mutate_order_id:
            self.orders = [
                replace(order, quantity=2.0, dollar_amount=None)
                if order.order_id == self.mutate_order_id
                else order
                for order in self.orders
            ]
        return accepted


@pytest.fixture(autouse=True)
def _fixed_live_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(risk_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(storage_module, "utc_now", lambda: NOW)


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
    controller = TradingController(
        active_broker,
        config,
        store,
        order_confirmer=_accept_reviewed_order,
    )
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


def _bind_owned_order(
    store: AuditStore,
    order: BrokerOrder,
    *,
    ref_id: str | None = None,
) -> BrokerOrder:
    durable_ref = ref_id or f"{order.order_id}-ref"
    bound = order
    intent = OrderIntent(
        ref_id=durable_ref,
        symbol=bound.symbol,
        side=bound.side,
        reason="owned cancellation fixture",
        order_type=str(bound.raw["type"]),
        dollar_amount=bound.dollar_amount,
        quantity=bound.quantity,
        limit_price=(
            float(bound.raw["price"]) if bound.raw.get("price") is not None else None
        ),
        market_hours=str(bound.raw["market_hours"]),
        time_in_force=str(bound.raw["time_in_force"]),
        created_at=bound.created_at or NOW,
    )
    store.record_intent(intent)
    store.mark_intent_submitting(
        durable_ref,
        account_number=ACCOUNT_NUMBER,
        authority_id="cancel-fixture-authority",
        strategy_fingerprint="c" * 64,
        authorized_notional=float(
            bound.dollar_amount
            if bound.dollar_amount is not None
            else (bound.quantity or 0.0) * 50.0
        ),
    )
    store.update_intent(durable_ref, bound.order_id, bound.state)
    return bound


def _manual_flatten_ticket(
    store: AuditStore,
    *,
    ref_id: str,
    symbol: str = "TQQQ",
    quantity: float = 0.4,
) -> tuple[OrderIntent, OrderReview]:
    intent = OrderIntent(
        ref_id=ref_id,
        symbol=symbol,
        side="sell",
        reason="Manual flatten confirmed in desktop app",
        quantity=quantity,
        created_at=NOW,
    )
    store.record_intent(intent)
    return intent, OrderReview(
        intent,
        "manual flatten reviewed",
        {},
        _quotes()[symbol],
        {},
    )


def _external_flatten_fill(*, symbol: str = "TQQQ", quantity: float = 0.4) -> BrokerOrder:
    return _observed_fill(
        _order(
            "external-flatten-order",
            state="queued",
            symbol=symbol,
            side="sell",
            quantity=quantity,
            dollar_amount=None,
        ),
        quantity=quantity,
        price=49.9,
        execution_id="external-flatten-execution",
    )


def _qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.asyncio
async def test_manual_flatten_ui_shows_exact_reviewed_estimate_and_verbatim_disclosure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _qt_app()
    held = Position("TQQQ", 0.4, 0.4, 50.0)
    controller, broker, store, _grant = _controller(
        tmp_path, monkeypatch, name="flatten-preview-ui"
    )
    broker.positions = [held]
    controller.snapshot.positions = [held]
    intent, review = _manual_flatten_ticket(store, ref_id="flatten-preview-ref")
    review = replace(
        review,
        market_data_disclosure="PROVIDER VERBATIM: bids move; execution is not guaranteed.",
        quote=replace(review.quote, bid=49.75, ask=49.80, last=49.77),
    )

    async def reviewed(_symbol: str) -> tuple[OrderIntent, OrderReview]:
        return intent, review

    captured: list[str] = []

    def decline(_parent, _title: str, prompt: str):
        captured.append(prompt)
        return "", False

    monkeypatch.setattr(controller, "review_flatten", reviewed)
    monkeypatch.setattr(main_window_module.QInputDialog, "getText", decline)
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)

    await window._flatten()

    assert len(captured) == 1
    prompt = captured[0]
    assert "Symbol: TQQQ" in prompt
    assert "Side: SELL" in prompt
    assert "Order type: market" in prompt
    assert "Quantity: 0.4 shares" in prompt
    assert "Estimated sell price at reviewed bid: $49.75 per share" in prompt
    assert "Estimated proceeds: $19.90" in prompt
    assert "estimate from the reviewed bid, not a guaranteed fill" in prompt
    assert "PROVIDER VERBATIM: bids move; execution is not guaranteed." in prompt
    assert broker.place_calls == []
    window._closing_after_cleanup = True
    window.close()
    store.close()


def test_strategy_order_dialog_is_exact_one_use_and_safe_default() -> None:
    _qt_app()
    intent = _intent("preview-dialog-ref")
    review = OrderReview(
        intent,
        "PROVIDER VERBATIM: market prices can move.",
        {},
        _quotes()["TQQQ"],
        {},
    )
    request = OrderConfirmationRequest(
        account_number=ACCOUNT_NUMBER,
        account_masked=_account().masked,
        intent=intent,
        review=review,
        authority_id="authority-preview",
        strategy_fingerprint="a" * 64,
        requested_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )

    dialog = OrderConfirmationDialog(request)

    preview = dialog.details.toPlainText()
    assert f"Account: {_account().masked} (ending 6789)" in preview
    assert "Symbol: TQQQ" in preview
    assert "Side: BUY" in preview
    assert "Dollar amount: $10.00" in preview
    assert "Order type: market" in preview
    assert "Market hours: regular_hours" in preview
    assert "Time in force: gfd" in preview
    assert "Limit price: none (market order)" in preview
    assert "Estimated order value: $10.00" in preview
    assert "Session authority: authority-preview" in preview
    assert f"Strategy fingerprint: {'a' * 64}" in preview
    assert f"Preview id: {request.preview_id}" in preview
    assert dialog.disclosure.toPlainText() == "PROVIDER VERBATIM: market prices can move."
    assert dialog.cancel_button.isDefault()
    assert not dialog.confirm_button.isEnabled()

    dialog.confirmation.setText(request.confirmation_phrase)
    assert dialog.confirm_button.isEnabled()
    dialog.close()


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


@pytest.mark.asyncio
async def test_strategy_submission_fails_closed_without_per_order_confirmation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="confirmation-unavailable",
    )
    controller.set_order_confirmer(None)
    controller.authorize_live(grant)
    controller.start_strategy()
    intent = _intent("confirmation-unavailable-ref")

    assert await controller._submit(intent, controller.snapshot.quotes["TQQQ"]) is None

    assert broker.review_calls == [intent.ref_id]
    assert broker.place_calls == []
    assert controller.risk.grant is None
    assert not controller.snapshot.strategy_running
    receipts = store.recent_receipts()
    assert any(row["category"] == "order_review" for row in receipts)
    assert not any(row["category"] == "order_confirmation_consumed" for row in receipts)
    store.close()


@pytest.mark.asyncio
async def test_declined_strategy_ticket_places_nothing_and_session_can_continue(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[OrderConfirmationRequest] = []

    async def decline(request: OrderConfirmationRequest) -> OrderConfirmationDecision:
        seen.append(request)
        return OrderConfirmationDecision(
            preview_id=request.preview_id,
            accepted=False,
            typed_phrase="",
            confirmed_at=request.requested_at,
        )

    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="confirmation-declined",
    )
    controller.set_order_confirmer(decline)
    controller.authorize_live(grant)
    controller.start_strategy()
    intent = _intent("confirmation-declined-ref")

    assert await controller._submit(intent, controller.snapshot.quotes["TQQQ"]) is None

    assert len(seen) == 1
    assert seen[0].intent == intent
    assert seen[0].review.intent == intent
    assert seen[0].account_number == ACCOUNT_NUMBER
    assert seen[0].confirmation_phrase == "PLACE BUY $10.00 TQQQ ON 6789"
    assert broker.place_calls == []
    assert controller.risk.grant is grant
    assert controller.snapshot.strategy_running
    decision = next(
        row for row in store.recent_receipts() if row["category"] == "order_confirmation_decision"
    )
    assert json.loads(decision["payload_json"])["accepted"] is False
    store.close()


@pytest.mark.asyncio
async def test_confirmed_strategy_ticket_is_bound_consumed_and_then_placed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[OrderConfirmationRequest] = []

    async def confirm(request: OrderConfirmationRequest) -> OrderConfirmationDecision:
        seen.append(request)
        return OrderConfirmationDecision(
            preview_id=request.preview_id,
            accepted=True,
            typed_phrase=request.confirmation_phrase,
            confirmed_at=request.requested_at,
        )

    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="confirmation-accepted",
    )
    controller.set_order_confirmer(confirm)
    controller.authorize_live(grant)
    controller.start_strategy()
    intent = _intent("confirmation-accepted-ref")

    order = await controller._submit(intent, controller.snapshot.quotes["TQQQ"])

    assert order is not None
    assert len(seen) == 1
    assert len(seen[0].preview_id) == 64
    assert broker.review_calls == [intent.ref_id]
    assert broker.place_calls == [intent.ref_id]
    receipts = store.recent_receipts()
    consumed = next(row for row in receipts if row["category"] == "order_confirmation_consumed")
    assert json.loads(consumed["payload_json"])["preview_id"] == seen[0].preview_id
    store.close()


@pytest.mark.asyncio
async def test_supervised_experimental_authority_is_distinct_and_hard_capped(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="supervised-experimental-authority",
    )
    monkeypatch.setattr(controller, "live_evidence_ready", lambda grant=None: False)
    supervised_grant = replace(
        grant,
        max_order_notional=10.0,
        max_daily_notional=50.0,
        max_total_exposure=40.0,
    )

    controller.authorize_supervised_experimental(supervised_grant)
    controller.start_strategy()

    assert controller.authority_mode == "supervised_experimental"
    assert controller.snapshot.strategy_running
    controller._revoke_live_automation("test complete")

    oversized = replace(supervised_grant, max_order_notional=10.01)
    with pytest.raises(RuntimeError, match=r"capped at \$10\.00"):
        controller.authorize_supervised_experimental(oversized)
    store.close()


@pytest.mark.asyncio
async def test_review_with_stale_one_side_book_clock_never_reaches_confirmation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="confirmation-stale-one-side",
    )
    confirmations: list[OrderConfirmationRequest] = []

    async def should_not_confirm(request: OrderConfirmationRequest) -> OrderConfirmationDecision:
        confirmations.append(request)
        return await _accept_reviewed_order(request)

    async def stale_review(_account_number: str, intent: OrderIntent) -> OrderReview:
        quote = replace(
            broker.quotes[intent.symbol],
            bid_timestamp=NOW - timedelta(seconds=9),
            ask_timestamp=NOW,
        )
        return OrderReview(intent, "verbatim", {}, quote, {})

    controller.set_order_confirmer(should_not_confirm)
    monkeypatch.setattr(broker, "review_order", stale_review)
    controller.authorize_live(grant)
    controller.start_strategy()

    assert await controller._submit(
        _intent("stale-one-side-ref"), controller.snapshot.quotes["TQQQ"]
    ) is None
    assert confirmations == []
    assert broker.place_calls == []
    assert controller.risk.grant is None
    store.close()


@pytest.mark.asyncio
async def test_expired_order_confirmation_places_nothing_and_session_continues(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="confirmation-expired",
    )

    async def expire(request: OrderConfirmationRequest) -> OrderConfirmationDecision:
        monkeypatch.setattr(controller_module, "utc_now", lambda: NOW + timedelta(seconds=31))
        return await _accept_reviewed_order(request)

    controller.set_order_confirmer(expire)
    controller.authorize_live(grant)
    controller.start_strategy()

    assert await controller._submit(
        _intent("confirmation-expired-ref"), controller.snapshot.quotes["TQQQ"]
    ) is None
    assert broker.place_calls == []
    assert controller.risk.grant is grant
    assert controller.snapshot.strategy_running
    store.close()


@pytest.mark.asyncio
async def test_post_confirmation_price_drift_requires_a_new_preview(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="confirmation-price-drift",
    )

    async def confirm_then_move(request: OrderConfirmationRequest) -> OrderConfirmationDecision:
        broker.quotes["TQQQ"] = Quote("TQQQ", 50.19, 50.21, 50.20, NOW, NOW, NOW)
        return await _accept_reviewed_order(request)

    controller.set_order_confirmer(confirm_then_move)
    controller.authorize_live(grant)
    controller.start_strategy()

    assert await controller._submit(
        _intent("confirmation-drift-ref"), controller.snapshot.quotes["TQQQ"]
    ) is None
    assert broker.place_calls == []
    assert controller.risk.grant is grant
    receipt = next(
        row
        for row in store.recent_receipts()
        if row["category"] == "order_confirmation"
        and "changed materially" in row["summary"]
    )
    assert json.loads(receipt["payload_json"])["broker_write_attempted"] is False
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
            "venue bid is not fresh",
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


def test_fresh_last_trade_cannot_hide_stale_executable_book(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        name="fresh-trade-stale-book",
    )
    stale = NOW - timedelta(seconds=20)
    quotes = _quotes()
    quotes["TQQQ"] = Quote("TQQQ", 49.99, 50.01, 50.0, NOW, stale, stale)

    with pytest.raises(BrokerError, match="TQQQ venue bid is not fresh"):
        controller._validated_execution_quotes(quotes, NOW, max_age_seconds=8.0)

    store.close()


def test_live_authority_and_readiness_reject_limit_route_even_with_regular_gfd(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        name="nonpilot-limit-route",
    )
    controller.update_config(replace(controller.config, order_type="limit"))
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
        order_type="limit",
        strategy_fingerprint="a" * 64,
    )
    grant = replace(draft, strategy_fingerprint=controller.current_strategy_fingerprint(draft))

    readiness = {row["gate"]: row for row in controller.live_readiness()}
    assert readiness["Supported real-order route"]["status"] == "BLOCKED"
    with pytest.raises(RuntimeError, match="market orders"):
        controller.authorize_live(grant)

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


def test_unrelated_completed_order_does_not_enter_scoped_execution_ledger(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        name="unrelated-completed-order",
    )
    execution = BrokerExecution("soxl-execution", 1.0, 198.37, 0.0, NOW)
    unrelated = _order(
        order_id="soxl-order",
        state="filled",
        symbol="SOXL",
        side="sell",
        quantity=1.0,
        dollar_amount=None,
        average_price=198.37,
        executions=(execution,),
        cumulative_quantity=1.0,
        last_transaction_at=NOW,
    )

    controller._persist_execution_truth(ACCOUNT_NUMBER, [unrelated])

    assert store.broker_executions(ACCOUNT_NUMBER) == []
    assert controller._execution_provenance_gaps == set()
    store.close()


def test_missing_scoped_execution_provenance_blocks_live_authority(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="missing-scoped-provenance",
    )
    incomplete = _order(
        order_id="historical-tqqq-fill",
        state="filled",
        symbol="TQQQ",
        side="buy",
        quantity=0.2,
        dollar_amount=None,
        average_price=50.0,
    )
    controller._persist_execution_truth(ACCOUNT_NUMBER, [incomplete])

    with pytest.raises(RuntimeError, match="lacks exact provider execution provenance"):
        controller.authorize_live(grant)

    assert controller.risk.grant is None
    store.close()


def test_live_authority_restores_exact_candidate_entry_count_from_durable_fills(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="exact-entry-restart",
    )
    buy_at = NOW - timedelta(minutes=3)
    sell_at = NOW - timedelta(minutes=2)
    buy_intent = replace(_intent("prior-entry-ref"), created_at=NOW - timedelta(minutes=4))
    sell_intent = OrderIntent(
        "prior-exit-ref",
        "TQQQ",
        "sell",
        "prior exact exit",
        quantity=0.2,
        created_at=NOW - timedelta(minutes=4),
    )
    for intent, order_id, submitted_at in (
        (buy_intent, "prior-buy", buy_at - timedelta(seconds=1)),
        (sell_intent, "prior-sell", sell_at - timedelta(seconds=1)),
    ):
        store.record_intent(intent)
        monkeypatch.setattr(storage_module, "utc_now", lambda value=submitted_at: value)
        store.mark_intent_submitting(
            intent.ref_id,
            account_number=ACCOUNT_NUMBER,
            authority_id="prior-authority",
            strategy_fingerprint=grant.strategy_fingerprint,
            authorized_notional=10.0,
        )
        store.update_intent(intent.ref_id, order_id, "filled")
    monkeypatch.setattr(storage_module, "utc_now", lambda: NOW)
    store.record_broker_order_executions(
        ACCOUNT_NUMBER,
        _order(
            order_id="prior-buy",
            state="filled",
            side="buy",
            quantity=None,
            dollar_amount=10.0,
            average_price=50.0,
            executions=(BrokerExecution("prior-buy-exec", 0.2, 50.0, 0.0, buy_at),),
            cumulative_quantity=0.2,
            last_transaction_at=buy_at,
            created_at=buy_at,
        ),
    )
    store.record_broker_order_executions(
        ACCOUNT_NUMBER,
        _order(
            order_id="prior-sell",
            state="filled",
            side="sell",
            quantity=0.2,
            dollar_amount=None,
            average_price=50.1,
            executions=(BrokerExecution("prior-sell-exec", 0.2, 50.1, 0.0, sell_at),),
            cumulative_quantity=0.2,
            last_transaction_at=sell_at,
            created_at=sell_at,
        ),
    )

    controller.authorize_live(grant)

    assert controller._prior_entry_upper_bound == 1
    assert controller.risk.grant == grant
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

    broker.positions = [Position("TQQQ", 0.2, 0.2, 50.0)]
    broker.orders = [
        _observed_fill(
            order,
            quantity=0.2,
            price=50.0,
            execution_id="successful-execution",
        )
    ]
    await controller.reconcile()
    assert controller._submission_reconcile_required == {}
    assert controller._uncertain_submission_refs == set()
    assert controller._confirmed_entry_order_ids == {order.order_id}
    fill_receipts = [
        receipt
        for receipt in store.recent_receipts()
        if receipt["category"] == "live_fill_reconciliation"
    ]
    assert fill_receipts
    fill_payload = json.loads(fill_receipts[0]["payload_json"])
    assert fill_payload["cumulative_quantity"] == pytest.approx(0.2)
    assert fill_payload["cumulative_notional"] == pytest.approx(10.0)
    assert fill_payload["actual_fill_timestamp_available"] is True
    store.close()


@pytest.mark.asyncio
async def test_post_review_opposite_inventory_blocks_placement_toctou(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="post-review-opposite-inventory",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    original_review = broker.review_order

    async def review_then_inject_position(account_number, intent):
        review = await original_review(account_number, intent)
        broker.positions = [Position("SQQQ", 0.1, 0.1, 40.0)]
        return review

    monkeypatch.setattr(broker, "review_order", review_then_inject_position)

    assert await controller._submit(
        _intent("post-review-race"), controller.snapshot.quotes["TQQQ"]
    ) is None

    assert broker.place_calls == []
    assert controller.risk.grant is None
    assert controller.snapshot.positions == [Position("SQQQ", 0.1, 0.1, 40.0)]
    store.close()


@pytest.mark.asyncio
async def test_post_review_refresh_waits_for_inflight_reconcile_before_placement(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = BlockingSafeReadBroker()
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="post-review-inflight-reconcile",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    background = asyncio.create_task(controller.reconcile())
    await broker.portfolio_read_started.wait()

    submit = asyncio.create_task(
        controller._submit(
            _intent("post-review-locked-race"), controller.snapshot.quotes["TQQQ"]
        )
    )
    await asyncio.sleep(0)
    broker.positions = [Position("SQQQ", 0.1, 0.1, 40.0)]
    broker.release_portfolio_read.set()

    await background
    assert await submit is None
    assert broker.place_calls == []
    assert controller.snapshot.positions == [Position("SQQQ", 0.1, 0.1, 40.0)]
    store.close()


@pytest.mark.asyncio
async def test_post_review_refresh_waits_for_quote_lock_and_reauthorizes_latest_quote(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = BlockingQuoteBroker()
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="post-review-inflight-quote",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    background = asyncio.create_task(controller.refresh_quotes(evaluate=False))
    await broker.quote_read_started.wait()

    submit = asyncio.create_task(
        controller._submit(
            _intent("post-review-quote-race"), controller.snapshot.quotes["TQQQ"]
        )
    )
    await asyncio.sleep(0)
    broker.quotes["TQQQ"] = Quote("TQQQ", 49.0, 51.0, 50.0, NOW, NOW, NOW)
    broker.release_quote_read.set()

    await background
    assert await submit is None
    assert broker.place_calls == []
    assert controller.snapshot.quotes["TQQQ"].spread_bps > grant.max_spread_bps
    assert controller.risk.grant is None
    store.close()


@pytest.mark.asyncio
async def test_manual_flatten_places_only_after_exact_current_truth_matches_review(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = Position("TQQQ", 0.4, 0.4, 50.0)
    broker = DeterministicBroker()
    broker.positions = [held]
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="manual-flatten-exact-success",
    )
    _seed_holding(
        store,
        symbol="TQQQ",
        quantity=held.quantity,
        price=50.0,
        order_id="manual-success-entry",
    )
    intent, review = _manual_flatten_ticket(store, ref_id="manual-success-ref")

    order = await controller.place_reviewed_flatten(intent, review)

    assert order.order_id == "broker-order-1"
    assert broker.place_calls == [intent.ref_id]
    assert broker.placed_intents == [intent]
    assert controller._submission_reconcile_required == {intent.ref_id: order.order_id}
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("review_quote", "message"),
    [
        (
            Quote(
                "TQQQ",
                49.99,
                50.01,
                50.0,
                NOW - timedelta(seconds=20),
                NOW - timedelta(seconds=20),
                NOW - timedelta(seconds=20),
            ),
            "not fresh",
        ),
        (
            Quote(
                "TQQQ",
                49.99,
                50.01,
                50.0,
                NOW + timedelta(seconds=5),
                NOW + timedelta(seconds=5),
                NOW + timedelta(seconds=5),
            ),
            "not fresh",
        ),
        (
            Quote(
                "TQQQ",
                49.99,
                50.01,
                50.0,
                NOW,
                NOW - timedelta(seconds=6),
                NOW,
            ),
            "timestamps are misaligned",
        ),
    ],
)
async def test_manual_flatten_blocks_stale_future_or_skewed_review_quote(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    review_quote: Quote,
    message: str,
) -> None:
    held = Position("TQQQ", 0.4, 0.4, 50.0)
    broker = DeterministicBroker()
    broker.positions = [held]
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name=f"manual-review-clock-{review_quote.timestamp.timestamp()}",
    )
    _seed_holding(
        store,
        symbol="TQQQ",
        quantity=held.quantity,
        price=50.0,
        order_id="manual-clock-entry",
    )
    intent, _review = _manual_flatten_ticket(store, ref_id="manual-clock-ref")
    review = OrderReview(intent, "verbatim disclosure", {}, review_quote, {})

    with pytest.raises(RuntimeError, match=message):
        await controller.place_reviewed_flatten(intent, review)

    assert broker.place_calls == []
    store.close()


@pytest.mark.asyncio
async def test_manual_flatten_requires_rereview_after_material_bid_drift(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = Position("TQQQ", 0.4, 0.4, 50.0)
    broker = DeterministicBroker()
    broker.positions = [held]
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="manual-review-bid-drift",
    )
    _seed_holding(
        store,
        symbol="TQQQ",
        quantity=held.quantity,
        price=50.0,
        order_id="manual-drift-entry",
    )
    intent, review = _manual_flatten_ticket(store, ref_id="manual-drift-ref")
    broker.quotes["TQQQ"] = Quote(
        "TQQQ", 49.79, 49.81, 49.8, NOW, NOW, NOW
    )

    with pytest.raises(RuntimeError, match="changed materially"):
        await controller.place_reviewed_flatten(intent, review)

    assert broker.place_calls == []
    store.close()


@pytest.mark.asyncio
async def test_manual_flatten_waits_for_inflight_reconcile_and_rejects_new_open_order(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = Position("TQQQ", 0.4, 0.4, 50.0)
    broker = BlockingSafeReadBroker()
    broker.positions = [held]
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="manual-flatten-reconcile-overlap",
    )
    _seed_holding(
        store,
        symbol="TQQQ",
        quantity=held.quantity,
        price=50.0,
        order_id="manual-overlap-entry",
    )
    intent, review = _manual_flatten_ticket(store, ref_id="manual-reconcile-race")

    background = asyncio.create_task(controller.reconcile())
    await broker.portfolio_read_started.wait()
    placement = asyncio.create_task(controller.place_reviewed_flatten(intent, review))
    await asyncio.sleep(0)

    assert not placement.done()
    assert broker.place_calls == []

    # A working sell appears while the timer reconciliation owns the account
    # lock. The placement path must wait and then perform its own non-coalescing
    # truth read rather than creating a duplicate flatten order.
    broker.orders = [
        _order(
            "external-working-sell",
            state="queued",
            symbol="TQQQ",
            side="sell",
            quantity=held.quantity,
            dollar_amount=None,
        )
    ]
    broker.release_portfolio_read.set()
    await background

    with pytest.raises(RuntimeError, match="nonterminal order appeared after review"):
        await asyncio.wait_for(placement, timeout=1.0)

    assert controller.snapshot.positions == [held]
    assert broker.place_calls == []
    assert store.unresolved_order_intents(ACCOUNT_NUMBER) == []
    store.close()


@pytest.mark.asyncio
async def test_manual_flatten_rereads_account_after_waiting_for_quote_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = Position("TQQQ", 0.4, 0.4, 50.0)
    broker = BlockingQuoteBroker()
    broker.positions = [held]
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="manual-flatten-quote-overlap",
    )
    _seed_holding(
        store,
        symbol="TQQQ",
        quantity=held.quantity,
        price=50.0,
        order_id="manual-quote-entry",
    )
    intent, review = _manual_flatten_ticket(store, ref_id="manual-quote-race")

    background = asyncio.create_task(controller.refresh_quotes(evaluate=False))
    await broker.quote_read_started.wait()
    placement = asyncio.create_task(controller.place_reviewed_flatten(intent, review))
    await asyncio.sleep(0)

    assert not placement.done()
    assert broker.place_calls == []

    # Truth changes during the quote-lock wait. The exact placement refresh
    # acquires both locks before reading account state, so this new flat state
    # must win over the stale reviewed inventory.
    broker.positions = []
    broker.orders = [_external_flatten_fill()]
    broker.release_quote_read.set()
    await background

    with pytest.raises(RuntimeError, match="Sellable position changed after review"):
        await asyncio.wait_for(placement, timeout=1.0)

    assert controller.snapshot.positions == []
    assert broker.place_calls == []
    assert store.unresolved_order_intents(ACCOUNT_NUMBER) == []
    store.close()


@pytest.mark.asyncio
async def test_live_quote_evaluation_releases_quote_lock_before_preplacement_refresh(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="live-refresh-placement-lock-order",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    controller.snapshot.signal = Signal(Regime.BULLISH, 1.0, "bullish lock-order test", NOW)
    controller.snapshot.last_analysis_at = NOW
    controller._analysis_sequence = controller.config.trade_every_bars
    controller._last_trade_decision_sequence = 0

    await asyncio.wait_for(controller.refresh_quotes(evaluate=True), timeout=1.0)

    assert len(broker.place_calls) == 1
    assert controller._submission_reconcile_required
    store.close()


@pytest.mark.asyncio
async def test_failed_reconcile_invalidates_freshness_before_new_authority(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="reconcile-freshness-invalidated",
    )

    async def fail_portfolio(_account_number):
        raise BrokerError("read failed")

    monkeypatch.setattr(broker, "get_portfolio", fail_portfolio)
    await controller.reconcile()

    assert controller.snapshot.last_reconcile_at is None
    with pytest.raises(RuntimeError, match="freshly reconciled"):
        controller.authorize_live(grant)
    assert controller.risk.grant is None
    store.close()


@pytest.mark.asyncio
async def test_partial_then_cancelled_buy_is_idempotent_and_releases_reconciliation_gate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="partial-cancelled-buy",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    intent = _intent("partial-cancelled-ref")
    order = await controller._submit(intent, controller.snapshot.quotes["TQQQ"])
    assert order is not None

    execution = BrokerExecution("partial-cancelled-execution", 0.1, 50.0, 0.0, NOW)
    partial = replace(
        order,
        state="partially_filled",
        average_price=50.0,
        executions=(execution,),
        cumulative_quantity=0.1,
        last_transaction_at=NOW,
    )
    broker.orders = [partial]
    broker.positions = [Position("TQQQ", 0.1, 0.1, 50.0)]

    await controller.reconcile()
    assert controller._submission_reconcile_required == {intent.ref_id: order.order_id}
    assert controller.risk.grant is grant

    # Providers may repeat the same cumulative snapshot. Its immutable execution id
    # must remain a single durable row and must not create a second entry count.
    await controller.reconcile()
    rows = store.broker_executions(ACCOUNT_NUMBER, order_id=order.order_id)
    assert [row["execution_id"] for row in rows] == [execution.execution_id]
    assert controller._confirmed_entry_order_ids == {order.order_id}

    broker.orders = [replace(partial, state="partially_filled_rest_cancelled")]
    await controller.reconcile()

    assert controller._submission_reconcile_required == {}
    assert controller.risk.grant is grant
    assert len(store.broker_executions(ACCOUNT_NUMBER, order_id=order.order_id)) == 1

    # The provider's exact terminal state releases the HOLD gate. The remaining
    # partial inventory is then eligible for the normal managed exit path.
    controller.snapshot.signal = Signal(Regime.FLAT, 0.0, "managed exit", NOW)
    controller.snapshot.last_analysis_at = NOW
    controller._analysis_sequence += controller.config.trade_every_bars
    controller._last_submission_at = None
    await controller._evaluate_and_trade()

    assert broker.placed_intents[-1].side == "sell"
    assert broker.placed_intents[-1].quantity == pytest.approx(0.1)
    store.close()


@pytest.mark.asyncio
async def test_rejected_order_resolves_no_fill_and_a_new_reference_can_continue(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="rejected-order-continuation",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    rejected_intent = _intent("rejected-ref")
    rejected_order = await controller._submit(
        rejected_intent,
        controller.snapshot.quotes["TQQQ"],
    )
    assert rejected_order is not None

    broker.orders = [
        replace(
            rejected_order,
            state="rejected",
            average_price=None,
            executions=(),
            cumulative_quantity=0.0,
            last_transaction_at=NOW,
        )
    ]
    broker.positions = []
    await controller.reconcile()

    assert controller._submission_reconcile_required == {}
    assert controller.risk.grant is grant
    assert store.broker_executions(ACCOUNT_NUMBER, order_id=rejected_order.order_id) == []

    replacement = _intent("replacement-after-rejection")
    replacement_order = await controller._submit(
        replacement,
        controller.snapshot.quotes["TQQQ"],
    )
    assert replacement_order is not None
    assert replacement_order.order_id != rejected_order.order_id
    assert broker.place_calls == [rejected_intent.ref_id, replacement.ref_id]
    store.close()


@pytest.mark.asyncio
async def test_confirmed_flat_sell_can_eventually_reverse_into_opposite_fund(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="confirmed-flat-reversal",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    _seed_holding(
        store,
        symbol="TQQQ",
        quantity=0.4,
        price=50.0,
        order_id="reversal-entry",
    )
    held = Position("TQQQ", 0.4, 0.4, 50.0)
    controller.snapshot.positions = [held]
    broker.positions = [held]

    sell_intent = OrderIntent(
        ref_id="reversal-sell-ref",
        symbol="TQQQ",
        side="sell",
        reason="confirmed-flat reversal lifecycle",
        quantity=0.4,
        created_at=NOW,
    )
    sell_order = await controller._submit(
        sell_intent,
        controller.snapshot.quotes["TQQQ"],
    )
    assert sell_order is not None
    broker.orders = [
        _observed_fill(
            sell_order,
            quantity=0.4,
            price=49.9,
            execution_id="reversal-sell-execution",
        )
    ]
    broker.positions = []
    await controller.reconcile()

    assert controller._submission_reconcile_required == {}
    assert controller.snapshot.positions == []
    assert store.active_holding_start(ACCOUNT_NUMBER, "TQQQ", 0.0) is None

    buy_intent = OrderIntent(
        ref_id="reversal-buy-ref",
        symbol="SQQQ",
        side="buy",
        reason="enter opposite fund only after confirmed flat",
        dollar_amount=10.0,
        created_at=NOW,
    )
    buy_order = await controller._submit(
        buy_intent,
        controller.snapshot.quotes["SQQQ"],
    )
    assert buy_order is not None
    filled_buy = _observed_fill(
        buy_order,
        quantity=0.25,
        price=40.0,
        execution_id="reversal-buy-execution",
    )
    broker.orders = [filled_buy, *broker.orders[1:]]
    broker.positions = [Position("SQQQ", 0.25, 0.25, 40.0)]
    await controller.reconcile()

    assert controller._submission_reconcile_required == {}
    assert controller.snapshot.positions == broker.positions
    assert store.active_holding_start(ACCOUNT_NUMBER, "SQQQ", 0.25) == NOW
    assert [(item.side, item.symbol) for item in broker.placed_intents] == [
        ("sell", "TQQQ"),
        ("buy", "SQQQ"),
    ]
    store.close()


@pytest.mark.asyncio
async def test_restart_with_durable_fill_but_flat_broker_inventory_blocks_new_authority(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "restart-ledger-mismatch.db"
    execution_time = NOW - timedelta(minutes=5)
    filled_entry = _order(
        order_id="restart-entry-order",
        state="filled",
        ref_id="restart-entry-ref",
        symbol="TQQQ",
        side="buy",
        quantity=0.2,
        dollar_amount=None,
        average_price=50.0,
        executions=(
            BrokerExecution(
                "restart-entry-execution",
                0.2,
                50.0,
                0.0,
                execution_time,
            ),
        ),
        cumulative_quantity=0.2,
        last_transaction_at=execution_time,
        created_at=execution_time,
    )
    first_store = AuditStore(path)
    first_store.record_broker_order_executions(ACCOUNT_NUMBER, filled_entry)
    first_store.close()

    broker = DeterministicBroker()
    broker.orders = [filled_entry]
    broker.positions = []
    restored_store = AuditStore(path)
    config = AppConfig(
        broker_connection_enabled=True,
        live_trading_enabled=True,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
    )
    controller = TradingController(broker, config, restored_store)
    monkeypatch.setattr(controller, "live_evidence_ready", lambda grant=None: True)

    await controller.connect()
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

    with pytest.raises(
        RuntimeError,
        match="Durable execution history does not reconcile to broker inventory",
    ):
        controller.authorize_live(grant)

    assert controller.risk.grant is None
    assert not controller.snapshot.strategy_running
    assert broker.place_calls == []
    restored_store.close()


@pytest.mark.asyncio
async def test_stop_cancel_treats_pending_cancelled_as_nonterminal_and_fails_closed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(controller_module.asyncio, "sleep", no_wait)
    broker = DeterministicBroker()
    broker.terminal_on_cancel = False
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="pending-cancel",
    )
    pending = _bind_owned_order(
        store, _order("pending-cancel", state="pending_cancelled")
    )
    broker.orders = [pending]
    plan = await controller.prepare_cancel_plan()

    verified = await controller.execute_confirmed_cancel(plan, reason="fault-test stop")

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
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="terminal-cancel",
    )
    pending = _bind_owned_order(
        store, _order("eventually-cancelled", state="pending_cancelled")
    )
    broker.orders = [pending]
    plan = await controller.prepare_cancel_plan()
    broker.order_snapshots.extend([[pending], [replace(pending, state="cancelled")]])

    assert await controller.execute_confirmed_cancel(plan, reason="fault-test stop")
    assert broker.cancel_calls == []
    assert controller.snapshot.orders[0].state == "cancelled"
    store.close()


@pytest.mark.asyncio
async def test_stop_without_confirmed_plan_never_calls_provider_cancel(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, _grant = _controller(
        tmp_path, monkeypatch, name="unconfirmed-cancel"
    )
    broker.orders = [
        _bind_owned_order(store, _order("owned-but-unconfirmed", state="queued"))
    ]

    assert not await controller.stop_and_cancel("unconfirmed stop")
    assert broker.cancel_calls == []
    store.close()


@pytest.mark.asyncio
async def test_confirmed_cancel_only_targets_durably_owned_agentic_orders(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, _grant = _controller(
        tmp_path, monkeypatch, name="mixed-cancel-scope"
    )
    owned = _bind_owned_order(store, _order("grande-order", state="queued"))
    unrelated = _order(
        "manual-order",
        state="queued",
        symbol="SQQQ",
        placed_agent="user",
    )
    broker.orders = [owned, unrelated]

    plan = await controller.prepare_cancel_plan()

    assert plan.order_ids == ("grande-order",)
    assert plan.unrelated_order_ids == ("manual-order",)
    assert await controller.execute_confirmed_cancel(plan)
    assert broker.cancel_calls == ["grande-order"]
    assert next(order for order in broker.orders if order.order_id == "manual-order").state == "queued"
    store.close()


@pytest.mark.asyncio
async def test_cancel_aborts_before_any_write_if_confirmed_order_identity_mutates(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, _grant = _controller(
        tmp_path, monkeypatch, name="cancel-identity-mutation"
    )
    owned = _bind_owned_order(store, _order("mutated-order", state="queued"))
    broker.orders = [owned]
    plan = await controller.prepare_cancel_plan()
    broker.orders = [replace(owned, symbol="SQQQ")]

    assert not await controller.execute_confirmed_cancel(plan)
    assert broker.cancel_calls == []
    store.close()


@pytest.mark.asyncio
async def test_cancel_aborts_before_any_write_if_reconciliation_fails_after_preview(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, _grant = _controller(
        tmp_path, monkeypatch, name="cancel-reconcile-failure"
    )
    owned = _bind_owned_order(store, _order("reconcile-order", state="queued"))
    broker.orders = [owned]
    plan = await controller.prepare_cancel_plan()
    controller._submission_reconcile_required["missing-placement"] = "missing-order"

    assert not await controller.execute_confirmed_cancel(plan)
    assert broker.cancel_calls == []
    store.close()


@pytest.mark.asyncio
async def test_cancel_revalidates_remaining_identity_before_every_provider_write(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = MutatingCancelBroker()
    controller, broker, store, _grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="cancel-mid-batch-mutation",
    )
    first = _bind_owned_order(store, _order("a-first-order", state="queued"))
    second = _bind_owned_order(store, _order("b-second-order", state="queued"))
    broker.orders = [first, second]
    broker.mutate_order_id = second.order_id
    plan = await controller.prepare_cancel_plan()

    assert not await controller.execute_confirmed_cancel(plan)
    assert broker.cancel_calls == ["a-first-order"]
    assert next(order for order in broker.orders if order.order_id == second.order_id).state == "queued"
    store.close()


@pytest.mark.asyncio
async def test_disconnect_and_revoke_never_cancel_owned_open_orders_without_consent(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path, monkeypatch, name="generic-actions-no-cancel"
    )
    controller.authorize_live(grant)
    owned = _bind_owned_order(store, _order("owned-open-order", state="queued"))
    broker.orders = [owned]
    controller.snapshot.orders = [owned]

    assert not await controller.revoke_live_authority("operator revoke")
    with pytest.raises(BrokerError, match=r"Use STOP \+ CANCEL"):
        await controller.disconnect()

    assert broker.cancel_calls == []
    assert controller.risk.grant is None
    store.close()


@pytest.mark.asyncio
async def test_stale_live_quote_batch_revokes_authority_without_implicit_cancellation(
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

    assert broker.cancel_calls == []
    assert broker.orders[0].state == "queued"
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
async def test_daily_loss_exit_waits_for_reconciliation_and_revokes_when_flat(
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
    _seed_holding(store, symbol="TQQQ", quantity=0.4, price=50.0, order_id="loss-entry")
    controller.snapshot.positions = [position]
    broker.positions = [position]
    losing = Portfolio(94.0, 94.0, 94.0)
    controller.snapshot.portfolio = losing
    broker.portfolio = losing
    controller.risk.update_portfolio(losing)

    await controller._evaluate_and_trade()

    assert controller.risk.session_status() == "LOSS LIMIT"
    assert controller.risk.grant is grant
    assert len(broker.placed_intents) == 1
    assert broker.placed_intents[0].symbol == "TQQQ"
    assert broker.placed_intents[0].side == "sell"
    assert broker.placed_intents[0].quantity == 0.4
    assert broker.placed_intents[0].dollar_amount is None

    # A second strategy tick cannot duplicate the sell while reconciliation is pending.
    await controller._evaluate_and_trade()
    assert len(broker.placed_intents) == 1

    broker.orders = [
        _observed_fill(
            broker.orders[0],
            quantity=0.4,
            price=49.9,
            execution_id="loss-exit-execution",
        )
    ]
    broker.positions = []
    await controller.reconcile()

    assert controller.risk.grant is None
    assert controller.risk.session_status() == "LOCKED"
    assert not controller.snapshot.strategy_running
    summaries = [receipt["summary"] for receipt in store.recent_receipts()]
    assert any("awaiting broker order and inventory truth" in summary for summary in summaries)
    assert any("confirmed flat" in summary for summary in summaries)
    store.close()


@pytest.mark.asyncio
async def test_regular_exit_never_duplicates_and_locks_after_repeated_stale_inventory(
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
    _seed_holding(store, symbol="TQQQ", quantity=0.4, price=50.0, order_id="regular-entry")
    controller.snapshot.positions = [position]
    broker.positions = [position]
    controller._analysis_sequence = controller.config.trade_every_bars

    await controller._evaluate_and_trade()

    assert len(broker.placed_intents) == 1
    assert broker.placed_intents[0].side == "sell"
    assert controller.risk.grant is grant
    assert controller._submission_reconcile_required

    broker.orders = [
        _observed_fill(
            broker.orders[0],
            quantity=0.4,
            price=49.9,
            execution_id="regular-exit-execution",
        )
    ]
    await controller.reconcile()
    assert controller.risk.grant is grant
    assert controller._submission_reconcile_required

    controller._analysis_sequence += controller.config.trade_every_bars
    await controller._evaluate_and_trade()
    assert controller.snapshot.positions == [position]
    assert len(broker.placed_intents) == 1

    # A second complete broker batch that still says both "filled" and unchanged inventory
    # is an execution deviation. Authority locks rather than risking an oversell.
    await controller.reconcile()
    assert controller.risk.grant is None
    assert controller.risk.session_status() == "LOCKED"
    assert len(broker.placed_intents) == 1
    summaries = [receipt["summary"] for receipt in store.recent_receipts()]
    assert any("never produced matching inventory" in summary for summary in summaries)
    store.close()


@pytest.mark.asyncio
async def test_take_profit_can_exit_exact_inventory_above_entry_order_cap(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="profitable-exit-allowance",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    position = Position("TQQQ", 0.5, 0.5, 50.0)
    _seed_holding(store, symbol="TQQQ", quantity=0.5, price=50.0, order_id="profit-entry")
    winning_quote = Quote("TQQQ", 51.98, 52.02, 52.0, NOW, NOW, NOW)
    controller.snapshot.positions = [position]
    broker.positions = [position]
    controller.snapshot.quotes["TQQQ"] = winning_quote
    broker.quotes["TQQQ"] = winning_quote
    controller.snapshot.signal = Signal(Regime.BULLISH, 1.0, "remain bullish", NOW)
    controller._analysis_sequence = controller.config.trade_every_bars

    await controller._evaluate_and_trade()

    assert grant.max_order_notional == 25.0
    assert position.sellable_quantity * winning_quote.mid == pytest.approx(26.0)
    assert len(broker.placed_intents) == 1
    assert broker.placed_intents[0].side == "sell"
    assert broker.placed_intents[0].quantity == pytest.approx(0.5)
    assert controller.risk.grant is grant
    authority_receipts = [
        json.loads(receipt["payload_json"])
        for receipt in store.recent_receipts()
        if receipt["category"] == "authority_action"
    ]
    assert any(
        payload.get("action") == "order_authorized"
        and "inventory-reducing exit" in payload.get("reason", "")
        for payload in authority_receipts
    )
    store.close()


@pytest.mark.asyncio
async def test_daily_loss_locks_without_implicitly_cancelling_pending_buy(
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
    _seed_holding(store, symbol="TQQQ", quantity=0.4, price=50.0, order_id="pending-loss-entry")
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

    assert broker.cancel_calls == []
    assert broker.orders[0].state == "queued"
    assert broker.placed_intents == []
    assert controller.risk.grant is None
    assert controller.risk.session_status() == "LOCKED"
    assert not controller.snapshot.strategy_running
    summaries = [receipt["summary"] for receipt in store.recent_receipts()]
    assert any("prior non-exit submission" in summary for summary in summaries)
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
    _seed_holding(
        store,
        symbol="TQQQ",
        quantity=0.4,
        price=50.0,
        order_id="inconsistent-inventory-entry",
    )
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

    tab_index = window.tabs.indexOf(window.activation_widget)
    assert tab_index >= 0
    assert window.tabs.tabText(tab_index) == "Live Readiness"
    assert [
        window.live_readiness_table.horizontalHeaderItem(column).text()
        for column in range(window.live_readiness_table.columnCount())
    ] == ["Condition", "Owner", "Status", "Current result", "Exact next action"]

    rows = {
        window.live_readiness_table.item(row, 0).text(): (
            window.live_readiness_table.item(row, 1).text(),
            window.live_readiness_table.item(row, 2).text(),
            window.live_readiness_table.item(row, 3).text(),
            window.live_readiness_table.item(row, 4).text(),
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
        "Supported real-order route",
        "Immutable runtime contract",
        "Runtime execution parity",
        "Positive exact evidence",
    } <= rows.keys()
    assert rows["Fresh exact venue quotes"][0] == "APP CHECK"
    assert rows["Fresh exact venue quotes"][1] == "PASS"
    assert rows["Fresh exact venue quotes"][2] == "QQQ/TQQQ/SQQQ exact and fresh"
    assert rows["Runtime execution parity"][0] == "APP GATE"
    assert rows["Runtime execution parity"][1] == "BLOCKED"
    assert "execution_timing_and_fill_economics" in rows["Runtime execution parity"][2]
    assert "Jurisdiction & account suitability" not in rows
    assert "does not collect or certify jurisdiction" in (
        window.activation_widget.external_resources.text()
    )
    assert "structurally read-only" in window.activation_widget.mode_notice.text().lower()
    readiness_summary = window.activation_widget.summary.text().lower()
    assert "hard-capped supervised" in readiness_summary
    assert "evidence-gated autonomous" in readiness_summary

    window._closing_after_cleanup = True
    window.close()
    store.close()


@pytest.mark.asyncio
async def test_safe_activation_checks_refuse_active_authority_without_any_broker_write(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _qt_app()
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="safe-check-active-authority",
    )
    controller.authorize_live(grant)
    controller.start_strategy()
    broker.orders = [_order("working-order")]
    controller.snapshot.orders = list(broker.orders)
    window = MainWindow(controller, controller.config)
    window._on_snapshot(controller.snapshot)

    assert not window.activation_widget.safe_checks_button.isEnabled()
    await window._run_safe_activation_checks()

    assert "SAFE CHECKS REFUSED" in window.status.text()
    assert broker.review_calls == []
    assert broker.place_calls == []
    assert broker.placed_intents == []
    assert broker.cancel_calls == []
    assert controller.risk.grant is grant
    assert controller.snapshot.strategy_running

    window._closing_after_cleanup = True
    window.close()
    store.close()


@pytest.mark.asyncio
async def test_safe_read_only_refresh_blocks_authority_and_survives_midread_grant_race(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = BlockingSafeReadBroker()
    controller, _broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        broker=broker,
        name="safe-check-race",
    )

    refresh = asyncio.create_task(controller.safe_read_only_refresh())
    await broker.portfolio_read_started.wait()

    with pytest.raises(RuntimeError, match="Safe read-only refresh is in progress"):
        controller.authorize_live(grant)
    assert controller.risk.grant is None

    # Simulate an out-of-band state change that bypasses the public controller API. The
    # post-await guard must stop the inspection without invoking live cleanup or any write.
    controller.risk.arm(grant, broker.portfolio)
    broker.release_portfolio_read.set()
    with pytest.raises(BrokerError, match="became active"):
        await refresh

    assert broker.review_calls == []
    assert broker.place_calls == []
    assert broker.placed_intents == []
    assert broker.cancel_calls == []
    assert controller.risk.grant is grant
    assert not controller.snapshot.strategy_running
    receipts = store.recent_receipts()
    assert any(
        receipt["category"] == "read_only_check"
        and "write" not in receipt["summary"].lower()
        and "stopped" in receipt["summary"].lower()
        for receipt in receipts
    )
    store.close()


@pytest.mark.asyncio
async def test_safe_read_only_refresh_recovers_restart_fill_truth_before_marking_fresh(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="safe-check-restart-fill",
    )
    intent = _intent("crashed-placement-ref")
    store.record_intent(intent)
    store.mark_intent_submitting(
        intent.ref_id,
        account_number=ACCOUNT_NUMBER,
        authority_id=grant.authority_id,
        strategy_fingerprint=grant.strategy_fingerprint,
        authorized_notional=10.0,
    )
    controller._submission_reconcile_required[intent.ref_id] = "restart-order"
    filled = _observed_fill(
        _order(
            "restart-order",
            ref_id=intent.ref_id,
            symbol="TQQQ",
            side="buy",
            dollar_amount=10.0,
        ),
        quantity=0.2,
        price=50.0,
        execution_id="restart-execution",
    )
    broker.orders = [filled]
    broker.positions = [Position("TQQQ", 0.2, 0.2, 50.0)]
    controller.snapshot.last_reconcile_at = None
    controller.snapshot.last_refresh = None

    await controller.safe_read_only_refresh()

    assert controller.snapshot.last_reconcile_at == NOW
    assert controller.snapshot.last_refresh == NOW
    assert controller._submission_reconcile_required == {}
    assert store.live_filled_entry_order_ids(ACCOUNT_NUMBER, "2026-08-11") == {
        "restart-order"
    }
    assert store.unresolved_order_intents(ACCOUNT_NUMBER) == []
    assert broker.review_calls == []
    assert broker.place_calls == []
    assert broker.cancel_calls == []
    store.close()


@pytest.mark.asyncio
async def test_restart_reconciliation_rejects_order_predating_durable_submission(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, broker, store, grant = _controller(
        tmp_path,
        monkeypatch,
        name="restart-order-predates-submit",
    )
    intent = _intent("predating-placement-ref")
    store.record_intent(intent)
    store.mark_intent_submitting(
        intent.ref_id,
        account_number=ACCOUNT_NUMBER,
        authority_id=grant.authority_id,
        strategy_fingerprint=grant.strategy_fingerprint,
        authorized_notional=10.0,
    )
    controller._submission_reconcile_required[intent.ref_id] = "predating-order"
    too_old = NOW - timedelta(minutes=1)
    broker.orders = [
        _observed_fill(
            _order(
                "predating-order",
                ref_id=intent.ref_id,
                created_at=too_old,
            ),
            quantity=0.2,
            price=50.0,
            execution_id="predating-execution",
            timestamp=too_old,
        )
    ]
    broker.positions = [Position("TQQQ", 0.2, 0.2, 50.0)]

    with pytest.raises(BrokerError, match="predates (the durable placement invocation|its durable submission intent)"):
        await controller.safe_read_only_refresh()

    assert controller.snapshot.last_reconcile_at is None
    assert broker.place_calls == []
    store.close()


def test_authorize_and_start_prefers_evidence_route_with_exact_operator_label(
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
    monkeypatch.setattr(controller, "authorize_live", lambda grant: calls.append(("authorize", grant)))
    monkeypatch.setattr(
        controller,
        "start_strategy",
        lambda: calls.append(("start", None)),
    )

    assert (
        window.authorize_button.text().replace("&&", "&")
        == "Authorize & Start Evidence-Gated Session"
    )
    assert (
        window.authorize_action.text().replace("&&", "&").removesuffix("…")
        == "Authorize & Start Evidence-Gated Session"
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

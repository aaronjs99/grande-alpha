from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import grande_alpha.storage as storage_module
from grande_alpha.models import BrokerExecution, BrokerOrder, OrderIntent
from grande_alpha.storage import AuditStore

NOW = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)


def _execution(
    execution_id: str,
    *,
    quantity: float,
    price: float,
    seconds: int,
    fees: float = 0.0,
) -> BrokerExecution:
    return BrokerExecution(
        execution_id=execution_id,
        quantity=quantity,
        price=price,
        fees=fees,
        timestamp=NOW + timedelta(seconds=seconds),
    )


def _order(
    *,
    state: str,
    executions: tuple[BrokerExecution, ...] = (),
    cumulative_quantity: float = 0.0,
    average_price: float | None = None,
    order_id: str = "order-1",
    symbol: str = "TQQQ",
    side: str = "buy",
    requested_quantity: float | None = None,
    dollar_amount: float | None = None,
) -> BrokerOrder:
    return BrokerOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        state=state,
        quantity=(None if dollar_amount is not None else (
            requested_quantity
            if requested_quantity is not None
            else (cumulative_quantity if state == "filled" else 0.2)
        )),
        dollar_amount=dollar_amount,
        average_price=average_price,
        created_at=NOW,
        raw={
            "ref_id": "ref-1",
            "type": "market",
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
        },
        executions=executions,
        cumulative_quantity=cumulative_quantity,
        last_transaction_at=NOW + timedelta(seconds=10),
    )


@pytest.mark.parametrize(
    ("state", "executions", "cumulative_quantity", "average_price"),
    [
        (
            "filled",
            (
                _execution("exec-a", quantity=0.1, price=49.9, seconds=3),
                _execution("exec-b", quantity=0.1, price=50.1, seconds=5),
            ),
            0.2,
            50.0,
        ),
        (
            "partially_filled",
            (_execution("exec-partial", quantity=0.075, price=50.0, seconds=4),),
            0.075,
            50.0,
        ),
        (
            "cancelled",
            (_execution("exec-before-cancel", quantity=0.05, price=50.0, seconds=2),),
            0.05,
            50.0,
        ),
        ("rejected", (), 0.0, None),
    ],
)
def test_provider_observed_terminal_and_partial_snapshots_validate_exact_execution_provenance(
    state: str,
    executions: tuple[BrokerExecution, ...],
    cumulative_quantity: float,
    average_price: float | None,
) -> None:
    order = _order(
        state=state,
        executions=executions,
        cumulative_quantity=cumulative_quantity,
        average_price=average_price,
    )

    order.validate_execution_provenance(require_snapshot=True)

    assert sum(execution.quantity for execution in order.executions) == pytest.approx(
        cumulative_quantity
    )


def test_duplicate_provider_execution_id_is_rejected_even_when_totals_match() -> None:
    duplicate = (
        _execution("same-id", quantity=0.1, price=50.0, seconds=1),
        _execution("same-id", quantity=0.1, price=50.0, seconds=2),
    )
    order = _order(
        state="filled",
        executions=duplicate,
        cumulative_quantity=0.2,
        average_price=50.0,
    )

    with pytest.raises(ValueError, match="duplicate execution id"):
        order.validate_execution_provenance(require_snapshot=True)


def test_out_of_order_provider_execution_list_uses_earliest_actual_fill_timestamp() -> None:
    first = _execution("earliest", quantity=0.05, price=49.8, seconds=2)
    second = _execution("latest", quantity=0.15, price=50.0666666667, seconds=9)
    order = _order(
        state="filled",
        executions=(second, first),
        cumulative_quantity=0.2,
        average_price=50.0,
    )

    order.validate_execution_provenance(require_snapshot=True)

    assert order.first_execution_at == first.timestamp


def test_execution_snapshot_rejects_missing_or_inconsistent_provider_totals() -> None:
    pending_without_provider_total = replace(
        _order(state="queued"),
        cumulative_quantity=None,
    )
    with pytest.raises(ValueError, match="omitted cumulative execution quantity"):
        pending_without_provider_total.validate_execution_provenance(require_snapshot=True)

    mismatched_quantity = _order(
        state="filled",
        executions=(_execution("exec-1", quantity=0.1, price=50.0, seconds=2),),
        cumulative_quantity=0.2,
        average_price=50.0,
    )
    with pytest.raises(ValueError, match="cumulative execution quantity"):
        mismatched_quantity.validate_execution_provenance(require_snapshot=True)

    mismatched_average = _order(
        state="filled",
        executions=(
            _execution("exec-1", quantity=0.1, price=49.0, seconds=2),
            _execution("exec-2", quantity=0.1, price=51.0, seconds=3),
        ),
        cumulative_quantity=0.2,
        average_price=49.5,
    )
    with pytest.raises(ValueError, match="cumulative average price"):
        mismatched_average.validate_execution_provenance(require_snapshot=True)


def test_durable_execution_store_is_idempotent_and_sorts_out_of_order_provider_data(
    tmp_path,
) -> None:
    store = AuditStore(tmp_path / "execution-idempotency.db")
    earliest = _execution("execution-early", quantity=0.08, price=49.5, seconds=2, fees=0.01)
    latest = _execution("execution-late", quantity=0.12, price=50.3333333333, seconds=9)
    order = _order(
        state="filled",
        executions=(latest, earliest),
        cumulative_quantity=0.2,
        average_price=50.0,
    )

    store.record_broker_order_executions("account-1", order)
    store.record_broker_order_executions("account-1", order)

    rows = store.broker_executions("account-1", order_id=order.order_id)
    assert [row["execution_id"] for row in rows] == ["execution-early", "execution-late"]
    assert len(rows) == 2
    assert sum(row["quantity"] for row in rows) == pytest.approx(0.2)
    store.close()


def test_provider_execution_identity_cannot_be_reused_by_another_order(tmp_path) -> None:
    store = AuditStore(tmp_path / "execution-conflict.db")
    execution = _execution("immutable-id", quantity=0.1, price=50.0, seconds=3)
    first = _order(
        state="filled",
        executions=(execution,),
        cumulative_quantity=0.1,
        average_price=50.0,
        order_id="order-a",
    )
    conflicting = replace(first, order_id="order-b")
    store.record_broker_order_executions("account-1", first)

    with pytest.raises(ValueError, match="reused with conflicting immutable data"):
        store.record_broker_order_executions("account-1", conflicting)

    assert [row["order_id"] for row in store.broker_executions("account-1")] == ["order-a"]
    store.close()


def test_provider_order_identity_cannot_change_with_a_distinct_execution_id(tmp_path) -> None:
    store = AuditStore(tmp_path / "order-identity-conflict.db")
    first = _order(
        state="filled",
        executions=(_execution("first-id", quantity=0.1, price=50.0, seconds=2),),
        cumulative_quantity=0.1,
        average_price=50.0,
        order_id="same-order",
    )
    conflicting = replace(
        first,
        symbol="SQQQ",
        executions=(_execution("distinct-id", quantity=0.1, price=50.0, seconds=3),),
    )
    store.record_broker_order_executions("account-1", first)

    with pytest.raises(ValueError, match="order identity changed symbol or side"):
        store.record_broker_order_executions("account-1", conflicting)

    assert [row["execution_id"] for row in store.broker_executions("account-1")] == [
        "first-id"
    ]
    store.close()


def test_execution_provenance_rejects_implausibly_future_provider_times() -> None:
    future = NOW + timedelta(seconds=6)
    order = replace(
        _order(
            state="filled",
            executions=(BrokerExecution("future-id", 0.1, 50.0, 0.0, future),),
            cumulative_quantity=0.1,
            average_price=50.0,
        ),
        last_transaction_at=future,
    )

    with pytest.raises(ValueError, match="implausibly in the future"):
        order.validate_execution_provenance(require_snapshot=True, observed_at=NOW)


def test_partial_then_cancelled_snapshot_persists_each_execution_once(tmp_path) -> None:
    store = AuditStore(tmp_path / "partial-cancel.db")
    first = _execution("partial-a", quantity=0.04, price=50.0, seconds=2)
    second = _execution("partial-b", quantity=0.01, price=50.5, seconds=5)
    partial = _order(
        state="partially_filled",
        executions=(first,),
        cumulative_quantity=0.04,
        average_price=50.0,
    )
    cancelled = _order(
        state="cancelled",
        executions=(second, first),
        cumulative_quantity=0.05,
        average_price=50.1,
    )

    store.record_broker_order_executions("account-1", partial)
    store.record_broker_order_executions("account-1", cancelled)

    rows = store.broker_executions("account-1", order_id="order-1")
    assert [row["execution_id"] for row in rows] == ["partial-a", "partial-b"]
    assert sum(row["quantity"] for row in rows) == pytest.approx(0.05)
    store.close()


def test_rejected_order_persists_no_execution_and_is_not_an_incomplete_fill(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage_module, "utc_now", lambda: NOW)
    store = AuditStore(tmp_path / "rejected.db")
    intent = OrderIntent(
        ref_id="rejected-ref",
        symbol="TQQQ",
        side="buy",
        reason="provider rejection test",
        dollar_amount=10.0,
        created_at=NOW,
    )
    store.record_intent(intent)
    store.mark_intent_submitting(
        intent.ref_id,
        account_number="account-1",
        authority_id="authority-1",
        strategy_fingerprint="a" * 64,
        authorized_notional=10.0,
    )
    store.update_intent(intent.ref_id, "rejected-order", "rejected")
    store.record_broker_order_executions(
        "account-1",
        _order(
            state="rejected",
            executions=(),
            cumulative_quantity=0.0,
            average_price=None,
            order_id="rejected-order",
            dollar_amount=10.0,
        ),
    )

    assert store.broker_executions("account-1") == []
    assert store.incomplete_execution_provenance("account-1", "2026-08-11") == []
    store.close()


def test_filled_intent_without_provider_execution_stays_incomplete_until_exact_fill_arrives(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_module, "utc_now", lambda: NOW)
    store = AuditStore(tmp_path / "incomplete-fill.db")
    intent = OrderIntent(
        ref_id="filled-ref",
        symbol="TQQQ",
        side="buy",
        reason="restart provenance test",
        dollar_amount=10.0,
        created_at=NOW,
    )
    store.record_intent(intent)
    store.mark_intent_submitting(
        intent.ref_id,
        account_number="account-1",
        authority_id="authority-1",
        strategy_fingerprint="a" * 64,
        authorized_notional=10.0,
    )
    store.update_intent(intent.ref_id, "filled-order", "filled")
    assert store.incomplete_execution_provenance("account-1", "2026-08-11") == [intent.ref_id]

    execution = _execution("filled-exec", quantity=0.2, price=50.0, seconds=3)
    store.record_broker_order_executions(
        "account-1",
        _order(
            state="filled",
            executions=(execution,),
            cumulative_quantity=0.2,
            average_price=50.0,
            order_id="filled-order",
            dollar_amount=10.0,
        ),
    )
    assert store.incomplete_execution_provenance("account-1", "2026-08-11") == []
    store.close()


def test_first_execution_snapshot_must_match_exact_durable_ticket_and_notional(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage_module, "utc_now", lambda: NOW)
    store = AuditStore(tmp_path / "durable-ticket-binding.db")
    intent = OrderIntent(
        ref_id="bound-ref",
        symbol="TQQQ",
        side="buy",
        reason="exact durable binding",
        dollar_amount=10.0,
        created_at=NOW,
    )
    store.record_intent(intent)
    store.mark_intent_submitting(
        intent.ref_id,
        account_number="account-1",
        authority_id="authority-1",
        strategy_fingerprint="a" * 64,
        authorized_notional=10.0,
    )
    store.update_intent(intent.ref_id, "bound-order", "filled")
    inflated = _order(
        state="filled",
        executions=(_execution("inflated", quantity=0.3, price=50.0, seconds=2),),
        cumulative_quantity=0.3,
        average_price=50.0,
        order_id="bound-order",
        dollar_amount=10.0,
    )

    with pytest.raises(ValueError, match="authorized notional"):
        store.record_broker_order_executions("account-1", inflated)
    with pytest.raises(ValueError, match="identity differs"):
        store.record_broker_order_executions(
            "account-1", replace(inflated, symbol="SQQQ")
        )

    assert store.broker_executions("account-1") == []
    store.close()


def test_first_snapshot_is_bound_by_provider_reference_before_order_id_update(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage_module, "utc_now", lambda: NOW)
    store = AuditStore(tmp_path / "first-snapshot-reference.db")
    intent = OrderIntent(
        ref_id="ref-1",
        symbol="TQQQ",
        side="buy",
        reason="pre-update binding",
        dollar_amount=10.0,
        created_at=NOW,
    )
    store.record_intent(intent)
    store.mark_intent_submitting(
        intent.ref_id,
        account_number="account-1",
        authority_id="authority-1",
        strategy_fingerprint="a" * 64,
        authorized_notional=10.0,
    )
    snapshot = _order(
        state="filled",
        executions=(_execution("first-exec", quantity=0.2, price=50.0, seconds=2),),
        cumulative_quantity=0.2,
        average_price=50.0,
        order_id="new-order-id",
        dollar_amount=10.0,
    )

    with pytest.raises(ValueError, match="identity differs"):
        store.record_broker_order_executions(
            "account-1", replace(snapshot, symbol="SQQQ")
        )
    quarantined = store._connection.execute(
        "SELECT broker_order_id,broker_state FROM order_intents WHERE ref_id=?",
        (intent.ref_id,),
    ).fetchone()
    assert quarantined["broker_order_id"] is None
    assert quarantined["broker_state"] == "submitting"
    assert store.broker_executions("account-1") == []

    with pytest.raises(ValueError, match="predates its durable submission"):
        store.record_broker_order_executions(
            "account-1", replace(snapshot, created_at=NOW - timedelta(seconds=6))
        )

    store.record_broker_order_executions("account-1", snapshot)
    atomically_bound = store._connection.execute(
        "SELECT broker_order_id FROM order_intents WHERE ref_id=?", (intent.ref_id,)
    ).fetchone()
    assert atomically_bound["broker_order_id"] == snapshot.order_id
    store.update_intent(intent.ref_id, snapshot.order_id, "filled")

    assert [row["execution_id"] for row in store.broker_executions("account-1")] == [
        "first-exec"
    ]
    store.close()


def test_restart_restores_entry_count_and_active_holding_from_exact_provider_executions(
    tmp_path,
) -> None:
    path = tmp_path / "restart.db"
    store = AuditStore(path)
    first_buy = _order(
        state="filled",
        executions=(_execution("buy-1", quantity=0.2, price=50.0, seconds=1),),
        cumulative_quantity=0.2,
        average_price=50.0,
        order_id="buy-order-1",
    )
    flat_sell = _order(
        state="filled",
        executions=(_execution("sell-1", quantity=0.2, price=50.5, seconds=4),),
        cumulative_quantity=0.2,
        average_price=50.5,
        order_id="sell-order-1",
        side="sell",
    )
    active_fill_time = NOW + timedelta(seconds=8)
    second_buy = _order(
        state="filled",
        executions=(
            BrokerExecution("buy-2", 0.1, 40.0, 0.0, active_fill_time),
        ),
        cumulative_quantity=0.1,
        average_price=40.0,
        order_id="buy-order-2",
        symbol="SQQQ",
    )
    for order in (first_buy, flat_sell, second_buy):
        store.record_broker_order_executions("account-1", order)
    store.close()

    restored = AuditStore(path)
    assert restored.live_filled_entry_order_ids("account-1", "2026-08-11") == frozenset(
        {"buy-order-1", "buy-order-2"}
    )
    assert restored.active_holding_start("account-1", "TQQQ", 0.0) is None
    assert restored.active_holding_start("account-1", "SQQQ", 0.1) == active_fill_time
    restored.close()

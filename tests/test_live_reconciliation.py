from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from grande_alpha.live_reconciliation import (
    LiveSubmissionReconciliation,
    reconcile_execution,
)
from grande_alpha.models import BrokerExecution, BrokerOrder, Position

NOW = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)


def _tracking(*, side: str = "buy") -> LiveSubmissionReconciliation:
    return LiveSubmissionReconciliation(
        ref_id="ref-1",
        order_id="order-1",
        symbol="TQQQ",
        side=side,
        starting_quantity=0.0 if side == "buy" else 0.4,
        expected_quantity=None if side == "buy" else 0.4,
        authorized_notional=10.0 if side == "buy" else 20.0,
        submitted_at=NOW,
        reference_price=50.0,
    )


def _order(
    *,
    side: str = "buy",
    state: str = "queued",
    average_price: float | None = None,
    fill_quantities: tuple[float, ...] = (),
    quantity: float | None = None,
) -> BrokerOrder:
    executions = tuple(
        BrokerExecution(f"execution-{index}", quantity, float(average_price), 0.0, NOW)
        for index, quantity in enumerate(fill_quantities, start=1)
    )
    return BrokerOrder(
        order_id="order-1",
        symbol="TQQQ",
        side=side,
        state=state,
        quantity=quantity if quantity is not None else (None if side == "buy" else 0.4),
        dollar_amount=10.0 if side == "buy" else None,
        average_price=average_price,
        created_at=NOW,
        raw={"ref_id": "ref-1"},
        executions=executions,
        cumulative_quantity=sum(fill_quantities) if fill_quantities else 0.0,
        last_transaction_at=NOW,
    )


def test_partial_then_terminal_fill_records_only_incremental_actual_economics() -> None:
    tracking = _tracking()
    partial = reconcile_execution(
        tracking,
        _order(state="partially_filled", average_price=50.0, fill_quantities=(0.1,)),
        [Position("TQQQ", 0.1, 0.1, 50.0)],
    )
    assert partial.status == "partial_fill"
    assert not partial.resolved
    assert partial.incremental_quantity == pytest.approx(0.1)
    assert partial.incremental_notional == pytest.approx(5.0)

    filled_quantity = 10.0 / 50.0
    filled = reconcile_execution(
        tracking,
        _order(state="filled", average_price=50.0, fill_quantities=(0.1, 0.1)),
        [Position("TQQQ", filled_quantity, filled_quantity, 50.0)],
    )
    assert filled.status == "filled"
    assert filled.resolved
    assert filled.cumulative_notional == pytest.approx(10.0)
    assert filled.incremental_quantity == pytest.approx(0.1)
    assert filled.incremental_notional == pytest.approx(5.0)
    assert filled.conservative_clock == NOW


def test_terminal_fill_waits_one_batch_then_rejects_missing_inventory() -> None:
    tracking = _tracking()
    event = reconcile_execution(
        tracking,
        _order(state="filled", average_price=50.0, fill_quantities=(0.2,)),
        [],
    )
    assert event.status == "awaiting_inventory"
    assert not event.resolved

    with pytest.raises(ValueError, match="never produced matching inventory"):
        reconcile_execution(
            tracking,
            _order(state="filled", average_price=50.0, fill_quantities=(0.2,)),
            [],
        )


def test_cancelled_partial_sell_resolves_at_current_inventory_without_overselling() -> None:
    tracking = _tracking(side="sell")
    event = reconcile_execution(
        tracking,
        _order(
            side="sell",
            state="cancelled",
            average_price=49.9,
            fill_quantities=(0.15,),
        ),
        [Position("TQQQ", 0.25, 0.25, 50.0)],
    )
    assert event.status == "partial_fill"
    assert event.resolved
    assert event.cumulative_quantity == pytest.approx(0.15)
    assert event.cumulative_notional == pytest.approx(0.15 * 49.9)


def test_reconciliation_rejects_identity_quantity_and_notional_deviations() -> None:
    with pytest.raises(ValueError, match="identity differs"):
        reconcile_execution(
            _tracking(),
            replace(_order(), symbol="SQQQ"),
            [],
        )

    with pytest.raises(ValueError, match="exceed.*(?:requested share|submitted) quantity"):
        reconcile_execution(
            replace(_tracking(side="sell"), starting_quantity=0.6),
            _order(
                side="sell",
                state="filled",
                average_price=50.0,
                fill_quantities=(0.6,),
                quantity=0.6,
            ),
            [],
        )

    with pytest.raises(ValueError, match="authorized notional"):
        reconcile_execution(
            _tracking(),
            _order(
                state="partially_filled",
                average_price=60.0,
                fill_quantities=(0.2,),
            ),
            [Position("TQQQ", 0.2, 0.2, 60.0)],
        )

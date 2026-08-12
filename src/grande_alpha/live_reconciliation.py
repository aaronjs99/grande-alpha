from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from grande_alpha.broker.base import normalized_order_state, order_is_terminal
from grande_alpha.models import BrokerOrder, Position


@dataclass
class LiveSubmissionReconciliation:
    """In-process baseline for reconciling one broker placement.

    Provider execution identity/time is persisted separately. This object retains the
    independent account-inventory reconciliation needed to prove that those executions
    actually reached the current position snapshot.
    """

    ref_id: str
    order_id: str
    symbol: str
    side: str
    starting_quantity: float
    expected_quantity: float | None
    authorized_notional: float
    submitted_at: datetime
    reference_price: float
    observed_filled_quantity: float = 0.0
    observed_fill_notional: float = 0.0
    terminal_without_inventory_observations: int = 0


@dataclass(frozen=True)
class ReconciledExecutionEvent:
    status: str
    resolved: bool
    cumulative_quantity: float
    incremental_quantity: float
    cumulative_notional: float
    incremental_notional: float
    average_price: float | None
    conservative_clock: datetime | None
    reason: str


def _position_quantity(positions: list[Position], symbol: str) -> float:
    matches = [
        float(position.quantity)
        for position in positions
        if position.symbol.strip().upper() == symbol
    ]
    if any(not math.isfinite(quantity) or quantity < -1e-9 for quantity in matches):
        raise ValueError(f"Broker returned invalid {symbol} inventory")
    return max(0.0, sum(matches))


def reconcile_execution(
    tracking: LiveSubmissionReconciliation,
    order: BrokerOrder,
    positions: list[Position],
) -> ReconciledExecutionEvent:
    """Reconcile cumulative position change to one known broker order, fail-closed.

    A successful result requires the position delta from the pre-placement baseline to agree
    with the provider execution list and cumulative average price. Holding time uses the actual
    first execution timestamp, never order creation time.
    """

    symbol = order.symbol.strip().upper()
    side = order.side.strip().lower()
    if order.order_id != tracking.order_id:
        raise ValueError("Reconciled broker order id differs from the submitted order")
    if symbol != tracking.symbol or side != tracking.side:
        raise ValueError("Reconciled broker order identity differs from the submitted intent")
    if side not in {"buy", "sell"}:
        raise ValueError("Reconciled broker order side is unsupported")
    order.validate_execution_provenance(require_snapshot=bool(order.executions))

    current_quantity = _position_quantity(positions, symbol)
    cumulative_quantity = (
        current_quantity - tracking.starting_quantity
        if side == "buy"
        else tracking.starting_quantity - current_quantity
    )
    tolerance = 1e-7
    if cumulative_quantity < -tolerance:
        raise ValueError("Inventory moved opposite to the submitted order")
    cumulative_quantity = max(0.0, cumulative_quantity)

    maximum_quantity = tracking.expected_quantity
    if maximum_quantity is None and order.quantity is not None:
        maximum_quantity = float(order.quantity)
    if maximum_quantity is not None:
        if not math.isfinite(maximum_quantity) or maximum_quantity <= 0:
            raise ValueError("Broker returned an invalid requested quantity")
        if cumulative_quantity > maximum_quantity + tolerance:
            raise ValueError("Observed fill exceeds the submitted quantity")

    average_price: float | None = None
    cumulative_notional = 0.0
    if cumulative_quantity > tolerance:
        if order.average_price is None:
            raise ValueError("Inventory changed but the broker omitted cumulative average price")
        average_price = float(order.average_price)
        if not math.isfinite(average_price) or average_price <= 0:
            raise ValueError("Broker returned an invalid cumulative average price")
        cumulative_notional = cumulative_quantity * average_price
        if side == "buy":
            allowed_overage = max(0.05, tracking.authorized_notional * 0.01)
            if cumulative_notional > tracking.authorized_notional + allowed_overage:
                raise ValueError("Observed buy fill exceeds the authorized notional")

    incremental_quantity = cumulative_quantity - tracking.observed_filled_quantity
    incremental_notional = cumulative_notional - tracking.observed_fill_notional
    if incremental_quantity < -tolerance:
        raise ValueError("Broker inventory regressed after an observed fill")
    if incremental_notional < -0.01:
        raise ValueError("Broker cumulative fill notional regressed")
    incremental_quantity = max(0.0, incremental_quantity)
    incremental_notional = max(0.0, incremental_notional)

    state = normalized_order_state(order.state)
    if order_is_terminal(order):
        if state == "filled":
            if cumulative_quantity <= tolerance:
                tracking.terminal_without_inventory_observations += 1
                if tracking.terminal_without_inventory_observations < 2:
                    return ReconciledExecutionEvent(
                        "awaiting_inventory",
                        False,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        None,
                        order.first_execution_at,
                        "Filled order was observed before matching inventory; one more full "
                        "reconciliation is required",
                    )
                raise ValueError("Terminal filled order never produced matching inventory")
            if maximum_quantity is not None and cumulative_quantity < maximum_quantity - tolerance:
                raise ValueError("Terminal filled order did not produce its full submitted quantity")
            status = "filled"
        elif state in {"rejected", "failed", "voided"}:
            if cumulative_quantity > tolerance:
                raise ValueError(f"Broker state {state} conflicts with changed inventory")
            status = "no_fill"
        else:
            status = "partial_fill" if cumulative_quantity > tolerance else "no_fill"
        resolved = True
    else:
        status = "partial_fill" if cumulative_quantity > tolerance else "pending"
        resolved = False

    tracking.observed_filled_quantity = cumulative_quantity
    tracking.observed_fill_notional = cumulative_notional
    if cumulative_quantity > tolerance:
        tracking.terminal_without_inventory_observations = 0
    return ReconciledExecutionEvent(
        status,
        resolved,
        cumulative_quantity,
        incremental_quantity,
        cumulative_notional,
        incremental_notional,
        average_price,
        order.first_execution_at,
        "Broker order state and account inventory reconciled",
    )

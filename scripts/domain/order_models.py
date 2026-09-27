from __future__ import annotations

import math
from dataclasses import asdict, field
from datetime import UTC, datetime
from numbers import Real
from typing import Any

from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.execution_profile import execution_profile
from grande_alpha.domain.market_models import Quote


class BrokerExecution:
    """One immutable provider-identified equity execution."""

    execution_id: str
    quantity: float
    price: float
    fees: float
    timestamp: datetime

    def validate(self) -> None:
        if not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise ValueError("Broker execution id must be a nonempty string")
        values = {
            "quantity": self.quantity,
            "price": self.price,
            "fees": self.fees,
        }
        for label, value in values.items():
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"Broker execution {label} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"Broker execution {label} must be finite")
            if label in {"quantity", "price"} and numeric <= 0:
                raise ValueError(f"Broker execution {label} must be positive")
            if label == "fees" and numeric < 0:
                raise ValueError("Broker execution fees must be nonnegative")
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError("Broker execution timestamp must be timezone-aware")


class BrokerOrder:
    order_id: str
    symbol: str
    side: str
    state: str
    quantity: float | None
    dollar_amount: float | None
    average_price: float | None
    created_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)
    executions: tuple[BrokerExecution, ...] = ()
    cumulative_quantity: float | None = None
    last_transaction_at: datetime | None = None
    placed_agent: str = ""

    def validate_execution_provenance(
        self,
        *,
        require_snapshot: bool = False,
        observed_at: datetime | None = None,
    ) -> None:
        """Validate exact fill identity and top-level provider totals.

        ``require_snapshot`` is used at the provider/durable boundary. Tests and
        non-provider adapters may still construct pending orders without the newer
        cumulative fields, but such an order can never establish fill provenance.
        """

        if require_snapshot and self.cumulative_quantity is None:
            raise ValueError("Broker order omitted cumulative execution quantity")
        if self.cumulative_quantity is not None:
            if (
                isinstance(self.cumulative_quantity, bool)
                or not isinstance(self.cumulative_quantity, Real)
                or not math.isfinite(float(self.cumulative_quantity))
                or float(self.cumulative_quantity) < 0
            ):
                raise ValueError("Broker cumulative execution quantity must be finite and nonnegative")
        seen: set[str] = set()
        for execution in self.executions:
            execution.validate()
            execution_id = execution.execution_id.strip()
            if execution_id in seen:
                raise ValueError("Broker order returned a duplicate execution id")
            seen.add(execution_id)
        executed_quantity = sum(float(execution.quantity) for execution in self.executions)
        if self.cumulative_quantity is not None and not math.isclose(
            executed_quantity,
            float(self.cumulative_quantity),
            rel_tol=1e-9,
            abs_tol=1e-8,
        ):
            raise ValueError("Broker executions do not match cumulative execution quantity")
        state = str(self.state or "").strip().lower()
        if state == "filled" and require_snapshot and executed_quantity <= 0:
            raise ValueError("Filled broker order must include a positive execution")
        dollar_based = self.dollar_amount is not None
        if dollar_based:
            requested_dollars = float(self.dollar_amount)
            if not math.isfinite(requested_dollars) or requested_dollars <= 0:
                raise ValueError("Broker requested dollar amount must be finite and positive")
        if self.quantity is not None:
            requested_quantity = float(self.quantity)
            minimum_quantity = 0 if dollar_based else 1e-300
            if not math.isfinite(requested_quantity) or requested_quantity < minimum_quantity:
                qualifier = "nonnegative" if dollar_based else "positive"
                raise ValueError(f"Broker requested quantity must be finite and {qualifier}")
        elif require_snapshot and not dollar_based:
            raise ValueError("Share-based broker order omitted requested quantity")
        # A dollar-notional order may expose quantity=0 as a provider sentinel or
        # a positive executed quantity. Neither is a requested share quantity, so
        # only true share-based orders are constrained by this field.
        if self.quantity is not None and not dollar_based:
            requested_quantity = float(self.quantity)
            if executed_quantity > requested_quantity + 1e-8:
                raise ValueError("Broker executions exceed requested share quantity")
            if state == "filled" and require_snapshot and not math.isclose(
                executed_quantity,
                requested_quantity,
                rel_tol=1e-8,
                abs_tol=1e-8,
            ):
                raise ValueError("Filled broker executions do not match requested share quantity")
        if executed_quantity > 0:
            if self.average_price is None:
                raise ValueError("Broker executions require a cumulative average price")
            average_price = float(self.average_price)
            if not math.isfinite(average_price) or average_price <= 0:
                raise ValueError("Broker cumulative average price must be finite and positive")
            weighted_average = sum(
                float(execution.quantity) * float(execution.price)
                for execution in self.executions
            ) / executed_quantity
            # The provider may round its top-level average price to cents while
            # retaining finer per-execution prices. Quantity/identity remain exact.
            if not math.isclose(weighted_average, average_price, rel_tol=1e-6, abs_tol=0.0051):
                raise ValueError("Broker executions do not match cumulative average price")
        elif require_snapshot and self.average_price is not None:
            raise ValueError("Broker average price is present without an execution")
        if self.last_transaction_at is not None and (
            self.last_transaction_at.tzinfo is None
            or self.last_transaction_at.utcoffset() is None
        ):
            raise ValueError("Broker last-transaction timestamp must be timezone-aware")
        if self.created_at is not None:
            if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
                raise ValueError("Broker order creation timestamp must be timezone-aware")
            if any(execution.timestamp < self.created_at for execution in self.executions):
                raise ValueError("Broker execution predates order creation")
        if self.last_transaction_at is not None and self.executions:
            if self.last_transaction_at < max(item.timestamp for item in self.executions):
                raise ValueError("Broker last-transaction timestamp predates an execution")
        # Future-skew requires an exact observation boundary. The provider adapter
        # supplies it when parsing a response; durable/offline validation cannot
        # manufacture when the snapshot was observed.
        if observed_at is not None:
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise ValueError("Broker observation timestamp must be timezone-aware")
            latest_allowed = observed_at.astimezone(UTC).timestamp() + 5.0
            if any(
                item.timestamp.astimezone(UTC).timestamp() > latest_allowed
                for item in self.executions
            ):
                raise ValueError("Broker execution timestamp is implausibly in the future")
            if (
                self.last_transaction_at is not None
                and self.last_transaction_at.astimezone(UTC).timestamp() > latest_allowed
            ):
                raise ValueError("Broker last-transaction timestamp is implausibly in the future")

    @property
    def first_execution_at(self) -> datetime | None:
        return min((item.timestamp for item in self.executions), default=None)


class OrderIntent:
    ref_id: str
    symbol: str
    side: str
    reason: str
    order_type: str = "market"
    dollar_amount: float | None = None
    quantity: float | None = None
    limit_price: float | None = None
    market_hours: str = "regular_hours"
    time_in_force: str = "gfd"
    created_at: datetime = field(default_factory=utc_now)

    @property
    def estimated_notional(self) -> float:
        if self.dollar_amount is not None:
            return float(self.dollar_amount)
        if self.quantity is not None and self.limit_price is not None:
            return float(self.quantity * self.limit_price)
        return 0.0

    def _validate_symbol(self) -> None:
        if self.symbol not in {"TQQQ", "SQQQ"}:
            raise ValueError("Automatic equity orders are restricted to TQQQ and SQQQ")

    def validate(self) -> None:
        profile = execution_profile(self)
        if self.side not in {"buy", "sell"}:
            raise ValueError("Order side must be buy or sell")
        self._validate_symbol()
        has_dollars = self.dollar_amount is not None
        has_quantity = self.quantity is not None
        if has_dollars == has_quantity:
            raise ValueError("Specify exactly one of dollar amount or quantity")
        for label, value in (
            ("Dollar amount", self.dollar_amount),
            ("Quantity", self.quantity),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{label} must be numeric")
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{label} must be numeric") from exc
            if not math.isfinite(numeric) or numeric <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.limit_price is not None:
            if isinstance(self.limit_price, bool) or not isinstance(self.limit_price, Real):
                raise ValueError("Limit price must be numeric")
            try:
                limit_price = float(self.limit_price)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Limit price must be numeric") from exc
            if not math.isfinite(limit_price):
                raise ValueError("Limit price must be finite")
        if profile.order_type == "market":
            if self.limit_price is not None:
                raise ValueError("Market orders cannot include a limit price")
        else:
            if self.dollar_amount is not None:
                raise ValueError("The Trading MCP accepts limit orders by share quantity only")
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError("Limit orders require a positive limit price")
            if self.quantity is None or abs(self.quantity - round(self.quantity)) > 1e-9:
                raise ValueError("The Trading MCP requires whole-share automatic limit orders")

    def broker_arguments(self, account_number: str) -> dict[str, str]:
        self.validate()
        args = {
            "account_number": account_number,
            "symbol": self.symbol,
            "side": self.side,
            "type": self.order_type,
            "market_hours": self.market_hours,
            "time_in_force": self.time_in_force,
        }
        if self.dollar_amount is not None:
            args["dollar_amount"] = f"{self.dollar_amount:.2f}"
        if self.quantity is not None:
            args["quantity"] = f"{self.quantity:.6f}".rstrip("0").rstrip(".")
        if self.limit_price is not None:
            args["limit_price"] = f"{self.limit_price:.2f}"
        return args

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result


class OrderReview:
    intent: OrderIntent
    market_data_disclosure: str | None
    checks: dict[str, Any]
    quote: Quote
    raw: dict[str, Any]

    @property
    def estimated_execution_price(self) -> float:
        """Conservative preview price for the reviewed side; never a fill guarantee."""

        return self.quote.ask if self.intent.side == "buy" else self.quote.bid

    @property
    def estimated_notional(self) -> float:
        if self.intent.quantity is not None:
            return float(self.intent.quantity) * self.estimated_execution_price
        return float(self.intent.dollar_amount or 0.0)

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from grande_alpha.broker.base import BrokerError
from grande_alpha.domain.order_models import OrderIntent


def _exception_details(exc: BaseException) -> str:
    """Expose actionable leaf failures hidden by AnyIO TaskGroup wrappers."""

    if isinstance(exc, BaseExceptionGroup):
        messages: list[str] = []
        for child in exc.exceptions:
            detail = _exception_details(child)
            if detail and detail not in messages:
                messages.append(detail)
        return "; ".join(messages)
    return str(exc).strip()


def _tool_contract(item: object) -> tuple[str, dict[str, Any]]:
    """Normalize MCP tool metadata from the provider model or a protocol-compatible object.

    The MCP client returns Pydantic models today, but the broker boundary only requires a tool
    name and input schema. Accepting mapping-like metadata also keeps contract inspection usable
    with compatible client implementations and deterministic integration fakes.
    """
    if isinstance(item, Mapping):
        name = item.get("name")
        schema = item.get("inputSchema")
        metadata = dict(item)
    else:
        name = getattr(item, "name", None)
        schema = getattr(item, "inputSchema", None)
        dump = getattr(item, "model_dump", None)
        if callable(dump):
            metadata = dump(mode="json", exclude_none=True)
        else:
            try:
                attributes = vars(item)
            except TypeError:
                attributes = {}
            metadata = {
                key: value
                for key, value in attributes.items()
                if not key.startswith("_") and value is not None
            }
    if not isinstance(name, str) or not name:
        raise BrokerError("Robinhood MCP returned a tool without a valid name")
    normalized_schema = schema if isinstance(schema, dict) else {}
    metadata["name"] = name
    metadata["inputSchema"] = normalized_schema
    return name, metadata


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _required_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise BrokerError(f"Robinhood {field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BrokerError(f"Robinhood {field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise BrokerError(f"Robinhood {field} must be finite")
    return parsed


def _required_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise BrokerError(f"Robinhood {field} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrokerError(f"Robinhood {field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BrokerError(f"Robinhood {field} must be a timezone-aware timestamp")
    return parsed.astimezone(UTC)


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrokerError(f"Robinhood {field} must be a nonempty string")
    return value.strip()


def _required_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise BrokerError(f"Robinhood {field} must be a boolean")
    return value


def _next_cursor(data: dict[str, Any], *, resource: str) -> str | None:
    if any(data.get(key) for key in ("next_page_token", "next_cursor", "cursor")):
        raise BrokerError(
            f"Robinhood returned a paginated {resource} set without its exact continuation URL"
        )
    continuation = data.get("next")
    if continuation is None or continuation == "":
        if data.get("has_more") is True:
            raise BrokerError(
                f"Robinhood {resource} pagination claimed more data without a continuation URL"
            )
        return None
    if not isinstance(continuation, str):
        raise BrokerError(f"Robinhood {resource} pagination continuation must be a URL")
    values = parse_qs(urlparse(continuation).query, keep_blank_values=True).get("cursor", [])
    if len(values) != 1 or not values[0].strip():
        raise BrokerError(
            f"Robinhood {resource} pagination continuation omitted one exact cursor"
        )
    return values[0].strip()


def _require_order_identity(echo: dict[str, Any], intent: OrderIntent, *, context: str) -> None:
    for key, expected in (
        ("symbol", intent.symbol),
        ("side", intent.side),
        ("type", intent.order_type),
    ):
        if str(echo.get(key, "")).strip().lower() != str(expected).strip().lower():
            raise BrokerError(f"Robinhood {context} echoed a different {key}")


def _require_review_echo(data: dict[str, Any], intent: OrderIntent) -> None:
    """Bind a review using fields declared by the review tool response schema."""

    _require_order_identity(data, intent, context="review")
    if intent.quantity is not None:
        actual = _required_number(data.get("quantity"), field="review quantity")
        if not math.isclose(actual, float(intent.quantity), rel_tol=1e-9, abs_tol=1e-9):
            raise BrokerError("Robinhood review echoed a different quantity")
    if intent.dollar_amount is not None:
        actual = _required_number(data.get("dollar_amount"), field="review dollar amount")
        if not math.isclose(actual, float(intent.dollar_amount), rel_tol=1e-9, abs_tol=0.005):
            raise BrokerError("Robinhood review echoed a different dollar amount")
    if intent.limit_price is not None:
        actual = _required_number(data.get("limit_price"), field="review limit price")
        if not math.isclose(actual, float(intent.limit_price), rel_tol=1e-9, abs_tol=0.005):
            raise BrokerError("Robinhood review echoed a different limit price")


def _require_placement_echo(row: dict[str, Any], intent: OrderIntent) -> None:
    """Bind a placed order using fields declared by the order response schema."""

    _require_order_identity(row, intent, context="placement")
    for key, expected in (
        ("market_hours", intent.market_hours),
        ("time_in_force", intent.time_in_force),
    ):
        if str(row.get(key, "")).strip().lower() != str(expected).strip().lower():
            raise BrokerError(f"Robinhood placement echoed a different {key}")
    if intent.quantity is not None:
        actual = _required_number(row.get("quantity"), field="placement quantity")
        if not math.isclose(actual, float(intent.quantity), rel_tol=1e-9, abs_tol=1e-9):
            raise BrokerError("Robinhood placement echoed a different quantity")
    if intent.dollar_amount is not None:
        raw_dollars = row.get("dollar_based_amount")
        if isinstance(raw_dollars, dict):
            raw_dollars = raw_dollars.get("amount")
        actual = _required_number(raw_dollars, field="placement dollar amount")
        if not math.isclose(actual, float(intent.dollar_amount), rel_tol=1e-9, abs_tol=0.005):
            raise BrokerError("Robinhood placement echoed a different dollar amount")
    if intent.limit_price is not None:
        actual = _required_number(row.get("price"), field="placement price")
        if not math.isclose(actual, float(intent.limit_price), rel_tol=1e-9, abs_tol=0.005):
            raise BrokerError("Robinhood placement echoed a different price")

from __future__ import annotations

from abc import ABC, abstractmethod

from grande_alpha.models import (
    Account,
    BrokerOrder,
    EquityTradability,
    OrderIntent,
    OrderReview,
    Portfolio,
    Position,
    Quote,
)

TERMINAL_ORDER_STATES = frozenset(
    {
        "filled",
        "cancelled",
        "canceled",
        "rejected",
        "failed",
        "expired",
        "voided",
    }
)


def normalized_order_state(state: str) -> str:
    return str(state or "").strip().lower()


def order_is_terminal(order: BrokerOrder) -> bool:
    """Treat unknown states as open so execution fails closed."""

    return normalized_order_state(order.state) in TERMINAL_ORDER_STATES


class BrokerError(RuntimeError):
    pass


class Broker(ABC):
    def clear_credentials(self) -> None:
        """Forget locally stored credentials when the adapter supports persistence."""
        return None

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def get_accounts(self) -> list[Account]: ...

    @abstractmethod
    async def get_portfolio(self, account_number: str) -> Portfolio: ...

    @abstractmethod
    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    async def get_tradability(self, account_number: str, symbols: list[str]) -> dict[str, EquityTradability]:
        """Return current session eligibility when the provider exposes it."""
        return {}

    @abstractmethod
    async def get_positions(self, account_number: str) -> list[Position]: ...

    @abstractmethod
    async def get_orders(self, account_number: str) -> list[BrokerOrder]: ...

    @abstractmethod
    async def review_order(self, account_number: str, intent: OrderIntent) -> OrderReview: ...

    @abstractmethod
    async def place_order(self, account_number: str, intent: OrderIntent) -> BrokerOrder: ...

    @abstractmethod
    async def cancel_order(self, account_number: str, order_id: str) -> bool: ...


class ShadowOnlyBroker(Broker):
    """Read-only broker facade used by unattended auto-shadow runtime."""

    def __init__(self, wrapped: Broker) -> None:
        self.wrapped = wrapped

    def clear_credentials(self) -> None:
        self.wrapped.clear_credentials()

    async def connect(self) -> None:
        await self.wrapped.connect()

    async def disconnect(self) -> None:
        await self.wrapped.disconnect()

    async def get_accounts(self) -> list[Account]:
        return await self.wrapped.get_accounts()

    async def get_portfolio(self, account_number: str) -> Portfolio:
        return await self.wrapped.get_portfolio(account_number)

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return await self.wrapped.get_quotes(symbols)

    async def get_tradability(
        self, account_number: str, symbols: list[str]
    ) -> dict[str, EquityTradability]:
        return await self.wrapped.get_tradability(account_number, symbols)

    async def get_positions(self, account_number: str) -> list[Position]:
        return await self.wrapped.get_positions(account_number)

    async def get_orders(self, account_number: str) -> list[BrokerOrder]:
        return await self.wrapped.get_orders(account_number)

    async def review_order(self, account_number: str, intent: OrderIntent) -> OrderReview:
        raise BrokerError("Auto-shadow broker facade blocks order review")

    async def place_order(self, account_number: str, intent: OrderIntent) -> BrokerOrder:
        raise BrokerError("Auto-shadow broker facade blocks order placement")

    async def cancel_order(self, account_number: str, order_id: str) -> bool:
        raise BrokerError("Auto-shadow broker facade blocks order cancellation")

from __future__ import annotations

from abc import ABC, abstractmethod

from grande_alpha.models import (
    Account,
    BrokerOrder,
    OrderIntent,
    OrderReview,
    Portfolio,
    Position,
    Quote,
)


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

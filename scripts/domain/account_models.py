from __future__ import annotations

import math
from numbers import Real


class Account:
    account_number: str
    nickname: str
    account_type: str
    agentic_allowed: bool
    state: str
    rhs_account_number: str = ""
    rhc_account_number: str = ""
    brokerage_account_type: str = ""

    @property
    def masked(self) -> str:
        return f"••••{self.account_number[-4:]}"


class Portfolio:
    total_value: float
    buying_power: float
    cash: float
    currency: str = "USD"
    crypto_buying_power: float | None = None
    crypto_value: float | None = None

    def validate(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, Real)
            for value in (self.total_value, self.buying_power, self.cash)
        ):
            raise ValueError("Portfolio values must be numeric")
        try:
            values = (float(self.total_value), float(self.buying_power), float(self.cash))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Portfolio values must be numeric") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Portfolio values must be finite")
        if values[0] < 0 or values[1] < 0:
            raise ValueError("Portfolio value and buying power cannot be negative")
        for value in (self.crypto_buying_power, self.crypto_value):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, Real)
                or not math.isfinite(float(value)) or value < 0
            ):
                raise ValueError("Crypto portfolio values must be finite and nonnegative when available")


class Position:
    symbol: str
    quantity: float
    sellable_quantity: float
    average_price: float | None = None


class EquityTradability:
    symbol: str
    tradeable: bool
    all_day_tradeable: bool
    extended_hours_fractional_tradeable: bool

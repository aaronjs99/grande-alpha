from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime

from grande_alpha.broker import RobinhoodMCPBroker
from grande_alpha.broker.base import Broker, BrokerError, ShadowOnlyBroker, order_is_terminal
from grande_alpha.models import Quote, utc_now

REQUIRED_QUOTE_SYMBOLS = ("QQQ", "TQQQ", "SQQQ")
DEFAULT_MAX_QUOTE_AGE_SECONDS = 8.0
MAX_QUOTE_FUTURE_SKEW_SECONDS = 2.0
MAX_QUOTE_BATCH_SKEW_SECONDS = 5.0


@dataclass(frozen=True)
class BrokerCheckResult:
    active_agentic_accounts: int
    selected_account: str
    account_type: str
    portfolio_received: bool
    total_value: float
    buying_power: float
    quote_symbols: tuple[str, ...]
    position_count: int
    order_count: int
    open_order_count: int
    maximum_quote_age_seconds: float
    quote_timestamp_skew_seconds: float
    read_only_boundary_enforced: bool
    write_tools_called: int


def _validate_quotes(
    quotes: dict[str, Quote],
    observed_at: datetime,
    max_quote_age_seconds: float,
) -> tuple[float, float]:
    expected = set(REQUIRED_QUOTE_SYMBOLS)
    returned = set(quotes)
    if returned != expected:
        missing = ", ".join(sorted(expected - returned)) or "none"
        unexpected = ", ".join(sorted(returned - expected)) or "none"
        raise BrokerError(
            "Readiness requires exactly QQQ/TQQQ/SQQQ venue quotes "
            f"(missing: {missing}; unexpected: {unexpected})"
        )
    timestamps = []
    ages = []
    for symbol in REQUIRED_QUOTE_SYMBOLS:
        quote = quotes[symbol]
        quote.validate()
        if quote.symbol != symbol:
            raise BrokerError(f"Quote key/symbol mismatch for {symbol}")
        if quote.bid_timestamp is None or quote.ask_timestamp is None:
            raise BrokerError(f"{symbol} lacks exact bid/ask venue clocks")
        for side, timestamp in (
            ("bid", quote.bid_timestamp),
            ("ask", quote.ask_timestamp),
        ):
            age = (observed_at - timestamp).total_seconds()
            if age < -MAX_QUOTE_FUTURE_SKEW_SECONDS:
                raise BrokerError(
                    f"{symbol} venue {side} timestamp is {abs(age):.1f}s in the future"
                )
            if age > max_quote_age_seconds:
                raise BrokerError(
                    f"{symbol} venue {side} is stale "
                    f"({age:.1f}s; limit {max_quote_age_seconds:.1f}s)"
                )
            timestamps.append(timestamp)
            ages.append(max(0.0, age))
    timestamp_skew = (max(timestamps) - min(timestamps)).total_seconds()
    if timestamp_skew > min(MAX_QUOTE_BATCH_SKEW_SECONDS, max_quote_age_seconds):
        raise BrokerError(f"Venue quote timestamps are misaligned by {timestamp_skew:.1f}s")
    return max(ages), timestamp_skew


async def check_broker(
    broker: Broker,
    *,
    reference_time: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> BrokerCheckResult:
    """Exercise only provider reads through a facade that blocks every broker order write."""
    try:
        max_age = float(max_quote_age_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Maximum quote age must be finite and positive") from exc
    if isinstance(max_quote_age_seconds, bool) or not math.isfinite(max_age):
        raise ValueError("Maximum quote age must be finite and positive")
    if max_age <= 0:
        raise ValueError("Maximum quote age must be finite and positive")
    if reference_time is not None and (
        not isinstance(reference_time, datetime)
        or reference_time.tzinfo is None
        or reference_time.utcoffset() is None
    ):
        raise ValueError("Readiness reference time must be timezone-aware")
    read_broker = ShadowOnlyBroker(broker)
    await read_broker.connect()
    try:
        accounts = await read_broker.get_accounts()
        candidates = [
            item
            for item in accounts
            if item.agentic_allowed and item.state.strip().lower() == "active"
        ]
        if len(candidates) != 1:
            raise BrokerError(
                f"Readiness requires exactly one active Agentic account; received {len(candidates)}"
            )
        account = candidates[0]
        if not account.account_number.strip():
            raise BrokerError("The active Agentic account has no account number")
        portfolio, quotes, positions, orders = await asyncio.gather(
            read_broker.get_portfolio(account.account_number),
            read_broker.get_quotes(list(REQUIRED_QUOTE_SYMBOLS)),
            read_broker.get_positions(account.account_number),
            read_broker.get_orders(account.account_number),
        )
        portfolio.validate()
        if not portfolio.currency.strip():
            raise BrokerError("Portfolio response has no currency")
        observed_at = reference_time or utc_now()
        maximum_age, timestamp_skew = _validate_quotes(quotes, observed_at, max_age)
        return BrokerCheckResult(
            active_agentic_accounts=1,
            selected_account=f"{account.nickname} {account.masked.replace('•', '*')}",
            account_type=account.account_type,
            portfolio_received=True,
            total_value=portfolio.total_value,
            buying_power=portfolio.buying_power,
            quote_symbols=tuple(sorted(quotes)),
            position_count=len(positions),
            order_count=len(orders),
            open_order_count=sum(not order_is_terminal(order) for order in orders),
            maximum_quote_age_seconds=maximum_age,
            quote_timestamp_skew_seconds=timestamp_skew,
            read_only_boundary_enforced=True,
            write_tools_called=0,
        )
    finally:
        await read_broker.disconnect()


async def _run() -> None:
    result = await check_broker(RobinhoodMCPBroker())
    print("Robinhood read-only check: PASS")
    print(f"Active Agentic accounts: {result.active_agentic_accounts}")
    print(f"Selected account: {result.selected_account} ({result.account_type})")
    print(f"Portfolio response: {'yes' if result.portfolio_received else 'no'}")
    print(f"Account value: ${result.total_value:,.2f}")
    print(f"Buying power: ${result.buying_power:,.2f}")
    print(f"Quotes returned: {', '.join(result.quote_symbols) or 'none'}")
    print(f"Oldest bid/ask venue book: {result.maximum_quote_age_seconds:.2f}s")
    print(f"Bid/ask venue timestamp skew: {result.quote_timestamp_skew_seconds:.2f}s")
    print(f"Positions returned: {result.position_count}")
    print(f"Orders returned (all states): {result.order_count}")
    print(f"Open orders: {result.open_order_count}")
    print("Read-only boundary: ENFORCED (review/place/cancel blocked)")
    print(f"Write tools called: {result.write_tools_called}")


def main() -> int:
    try:
        asyncio.run(_run())
        return 0
    except asyncio.CancelledError:
        print("Robinhood read-only check: FAILED - authorization was cancelled or timed out")
        return 1
    except Exception as exc:
        print(f"Robinhood read-only check: FAILED - {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

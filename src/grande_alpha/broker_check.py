from __future__ import annotations

import asyncio
from dataclasses import dataclass

from grande_alpha.broker import RobinhoodMCPBroker
from grande_alpha.broker.base import Broker, BrokerError

OPEN_ORDER_STATES = {
    "new",
    "queued",
    "confirmed",
    "unconfirmed",
    "partially_filled",
    "pending_cancelled",
}


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


async def check_broker(broker: Broker) -> BrokerCheckResult:
    """Exercise only provider read methods; never review, place, or cancel an order."""
    await broker.connect()
    try:
        accounts = await broker.get_accounts()
        candidates = [item for item in accounts if item.agentic_allowed and item.state == "active"]
        if not candidates:
            raise BrokerError("No active Agentic account was returned")
        candidates.sort(key=lambda item: (item.nickname.lower() != "agentic", item.account_number))
        account = candidates[0]
        portfolio, quotes, positions, orders = await asyncio.gather(
            broker.get_portfolio(account.account_number),
            broker.get_quotes(["QQQ", "TQQQ", "SQQQ"]),
            broker.get_positions(account.account_number),
            broker.get_orders(account.account_number),
        )
        return BrokerCheckResult(
            active_agentic_accounts=len(candidates),
            selected_account=f"{account.nickname} {account.masked.replace('•', '*')}",
            account_type=account.account_type,
            portfolio_received=portfolio.currency != "",
            total_value=portfolio.total_value,
            buying_power=portfolio.buying_power,
            quote_symbols=tuple(sorted(quotes)),
            position_count=len(positions),
            order_count=len(orders),
            open_order_count=sum(order.state.lower() in OPEN_ORDER_STATES for order in orders),
        )
    finally:
        await broker.disconnect()


async def _run() -> None:
    result = await check_broker(RobinhoodMCPBroker())
    print("Robinhood read-only check: PASS")
    print(f"Active Agentic accounts: {result.active_agentic_accounts}")
    print(f"Selected account: {result.selected_account} ({result.account_type})")
    print(f"Portfolio response: {'yes' if result.portfolio_received else 'no'}")
    print(f"Account value: ${result.total_value:,.2f}")
    print(f"Buying power: ${result.buying_power:,.2f}")
    print(f"Quotes returned: {', '.join(result.quote_symbols) or 'none'}")
    print(f"Positions returned: {result.position_count}")
    print(f"Orders returned (all states): {result.order_count}")
    print(f"Open orders: {result.open_order_count}")
    print("Write tools called: 0")


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

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from grande_alpha.broker import RobinhoodMCPBroker
from grande_alpha.broker.base import Broker, BrokerError


@dataclass(frozen=True)
class BrokerCheckResult:
    active_agentic_accounts: int
    portfolio_received: bool
    quote_symbols: tuple[str, ...]
    position_count: int
    order_count: int


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
            portfolio_received=portfolio.currency != "",
            quote_symbols=tuple(sorted(quotes)),
            position_count=len(positions),
            order_count=len(orders),
        )
    finally:
        await broker.disconnect()


async def _run() -> None:
    result = await check_broker(RobinhoodMCPBroker())
    print("Robinhood read-only check: PASS")
    print(f"Active Agentic accounts: {result.active_agentic_accounts}")
    print(f"Portfolio response: {'yes' if result.portfolio_received else 'no'}")
    print(f"Quotes returned: {', '.join(result.quote_symbols) or 'none'}")
    print(f"Positions returned: {result.position_count}")
    print(f"Orders returned: {result.order_count}")
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

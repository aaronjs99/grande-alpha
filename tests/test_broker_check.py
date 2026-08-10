from grande_alpha.broker.base import Broker
from grande_alpha.broker_check import check_broker
from grande_alpha.models import Account, Portfolio, Quote, utc_now


class ReadOnlyBroker(Broker):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.calls.append("disconnect")

    async def get_accounts(self):
        self.calls.append("get_accounts")
        return [Account("123456", "Agentic", "cash", True, "active")]

    async def get_portfolio(self, account_number):
        self.calls.append("get_portfolio")
        return Portfolio(100.0, 100.0, 100.0)

    async def get_quotes(self, symbols):
        self.calls.append("get_quotes")
        now = utc_now()
        return {symbol: Quote(symbol, 100.0, 100.02, 100.01, now) for symbol in symbols}

    async def get_positions(self, account_number):
        self.calls.append("get_positions")
        return []

    async def get_orders(self, account_number):
        self.calls.append("get_orders")
        return []

    async def review_order(self, account_number, intent):
        raise AssertionError("read-only check called review_order")

    async def place_order(self, account_number, intent):
        raise AssertionError("read-only check called place_order")

    async def cancel_order(self, account_number, order_id):
        raise AssertionError("read-only check called cancel_order")


async def test_broker_check_exercises_reads_and_never_order_methods() -> None:
    broker = ReadOnlyBroker()
    result = await check_broker(broker)

    assert result.active_agentic_accounts == 1
    assert result.quote_symbols == ("QQQ", "SQQQ", "TQQQ")
    assert broker.calls == [
        "connect",
        "get_accounts",
        "get_portfolio",
        "get_quotes",
        "get_positions",
        "get_orders",
        "disconnect",
    ]

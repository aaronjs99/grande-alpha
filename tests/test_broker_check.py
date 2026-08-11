from datetime import UTC, datetime, timedelta

import pytest

from grande_alpha import broker_check
from grande_alpha.broker.base import Broker, BrokerError
from grande_alpha.broker_check import check_broker
from grande_alpha.models import Account, BrokerOrder, Portfolio, Quote


class ReadOnlyBroker(Broker):
    def __init__(self, now: datetime | None = None) -> None:
        self.calls: list[str] = []
        self.now = now or datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
        self.accounts = [Account("123456", "Agentic", "cash", True, "active")]
        self.quotes = {
            symbol: Quote(symbol, 100.0, 100.02, 100.01, self.now)
            for symbol in ("QQQ", "TQQQ", "SQQQ")
        }
        self.orders: list[BrokerOrder] = []

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.calls.append("disconnect")

    async def get_accounts(self):
        self.calls.append("get_accounts")
        return self.accounts

    async def get_portfolio(self, account_number):
        self.calls.append("get_portfolio")
        return Portfolio(100.0, 100.0, 100.0)

    async def get_quotes(self, symbols):
        self.calls.append("get_quotes")
        return self.quotes

    async def get_positions(self, account_number):
        self.calls.append("get_positions")
        return []

    async def get_orders(self, account_number):
        self.calls.append("get_orders")
        return self.orders

    async def review_order(self, account_number, intent):
        raise AssertionError("read-only check called review_order")

    async def place_order(self, account_number, intent):
        raise AssertionError("read-only check called place_order")

    async def cancel_order(self, account_number, order_id):
        raise AssertionError("read-only check called cancel_order")


async def test_broker_check_exercises_reads_and_never_order_methods() -> None:
    broker = ReadOnlyBroker()
    result = await check_broker(broker, reference_time=broker.now)

    assert result.active_agentic_accounts == 1
    assert result.selected_account == "Agentic ****3456"
    assert result.account_type == "cash"
    assert result.total_value == 100.0
    assert result.buying_power == 100.0
    assert result.quote_symbols == ("QQQ", "SQQQ", "TQQQ")
    assert result.position_count == 0
    assert result.order_count == 0
    assert result.open_order_count == 0
    assert result.maximum_quote_age_seconds == 0
    assert result.quote_timestamp_skew_seconds == 0
    assert result.read_only_boundary_enforced
    assert result.write_tools_called == 0
    assert broker.calls == [
        "connect",
        "get_accounts",
        "get_portfolio",
        "get_quotes",
        "get_positions",
        "get_orders",
        "disconnect",
    ]


async def test_broker_check_requires_exactly_one_active_agentic_account() -> None:
    broker = ReadOnlyBroker()
    broker.accounts.append(Account("654321", "Agentic 2", "cash", True, " ACTIVE "))

    with pytest.raises(BrokerError, match="exactly one active Agentic account; received 2"):
        await check_broker(broker, reference_time=broker.now)

    assert broker.calls[-1] == "disconnect"


async def test_broker_check_normalizes_terminal_states_and_fails_unknown_open() -> None:
    broker = ReadOnlyBroker()
    broker.orders = [
        _order("filled", " Filled "),
        _order("canceled", "CANCELED"),
        _order("pending", " pending_cancelled "),
        _order("unknown", "provider_new_state"),
    ]

    result = await check_broker(broker, reference_time=broker.now)

    assert result.order_count == 4
    assert result.open_order_count == 2


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda broker: broker.quotes.pop("QQQ"), "exactly QQQ/TQQQ/SQQQ"),
        (
            lambda broker: broker.quotes.update(
                {"SPY": Quote("SPY", 100, 100.02, 100.01, broker.now)}
            ),
            "unexpected: SPY",
        ),
        (
            lambda broker: broker.quotes.__setitem__(
                "QQQ", Quote("SPY", 100, 100.02, 100.01, broker.now)
            ),
            "key/symbol mismatch",
        ),
        (
            lambda broker: broker.quotes.__setitem__(
                "QQQ", Quote("QQQ", 100, 100.02, 100.01, broker.now - timedelta(seconds=9))
            ),
            "venue quote is stale",
        ),
        (
            lambda broker: broker.quotes.__setitem__(
                "QQQ", Quote("QQQ", 100, 100.02, 100.01, broker.now + timedelta(seconds=3))
            ),
            "in the future",
        ),
    ],
)
async def test_broker_check_requires_exact_fresh_venue_quotes(mutate, message) -> None:
    broker = ReadOnlyBroker()
    mutate(broker)

    with pytest.raises(BrokerError, match=message):
        await check_broker(broker, reference_time=broker.now)

    assert broker.calls[-1] == "disconnect"


def _order(order_id: str, state: str) -> BrokerOrder:
    return BrokerOrder(
        order_id=order_id,
        symbol="TQQQ",
        side="buy",
        state=state,
        quantity=1,
        dollar_amount=None,
        average_price=None,
        created_at=None,
    )


async def test_cli_report_states_the_structural_no_write_boundary(monkeypatch, capsys) -> None:
    broker = ReadOnlyBroker(datetime.now(UTC))
    monkeypatch.setattr(broker_check, "RobinhoodMCPBroker", lambda: broker)

    await broker_check._run()

    output = capsys.readouterr().out
    assert "Robinhood read-only check: PASS" in output
    assert "Read-only boundary: ENFORCED (review/place/cancel blocked)" in output
    assert "Write tools called: 0" in output
    assert not {"review_order", "place_order", "cancel_order"} & set(broker.calls)

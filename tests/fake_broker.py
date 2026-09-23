"""Broker stub for UI tests; any unexpected broker read or write fails."""

from grande_alpha.broker.base import Broker


class DisabledBroker(Broker):
    async def connect(self):
        raise AssertionError("UI tests must not connect to a broker")

    async def disconnect(self):
        return None

    async def get_accounts(self):
        return []

    async def get_portfolio(self, account_number):
        raise AssertionError("Unexpected broker portfolio read")

    async def get_quotes(self, symbols):
        raise AssertionError("Unexpected broker quote read")

    async def get_positions(self, account_number):
        raise AssertionError("Unexpected broker position read")

    async def get_orders(self, account_number):
        raise AssertionError("Unexpected broker order read")

    async def review_order(self, account_number, intent):
        raise AssertionError("Unexpected broker review")

    async def place_order(self, account_number, intent):
        raise AssertionError("Unexpected broker placement")

    async def cancel_order(self, account_number, order_id):
        raise AssertionError("Unexpected broker cancellation")

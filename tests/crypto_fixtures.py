"""Synthetic fixtures for the September 2026 advertised crypto/scanner contract."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

from grande_alpha.broker.crypto import RobinhoodCrypto
from grande_alpha.crypto_models import CryptoOrderIntent
from grande_alpha.models import Account

NOW = datetime(2026, 9, 23, 15, tzinfo=UTC)
PAIR_ID = "11111111-1111-4111-8111-111111111111"
ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
ORDER_ID = "33333333-3333-4333-8333-333333333333"
PAIR = {
    "id": PAIR_ID, "symbol": "BTC-USD", "name": "Bitcoin",
    "asset_currency": {"id": "btc", "code": "BTC", "name": "Bitcoin", "type": "cryptocurrency"},
    "quote_currency": {"id": "usd", "code": "USD", "name": "US Dollar", "type": "fiat"},
    "tradability": "tradable", "display_only": False, "halted": False, "market_orders_only": False,
    "min_order_size": "0.00000001", "max_order_size": "100", "min_order_quantity_increment": "0.00000001",
    "min_order_price_increment": "0.01", "min_order_quote_amount": "1.00",
}
QUOTE = {
    "id": PAIR_ID, "symbol": "BTCUSD", "bid_price": "99900", "ask_price": "100000",
    "mark_price": "99950", "open_price": "99900", "updated_at": NOW.isoformat(),
}
SCHEMAS = {
    name: {"type": "object", "properties": dict.fromkeys(keys, {}), "required": required}
    for name, keys, required in (
        ("get_currency_pairs", ["limit", "cursor"], []),
        ("get_crypto_quotes", ["symbols", "rhs_account_number"], ["symbols"]),
        ("get_scans", [], []), ("run_scan", ["scan_id"], ["scan_id"]),
        ("get_crypto_positions", ["rhs_account_number", "cursor"], ["rhs_account_number"]),
        ("get_crypto_orders", ["rhs_account_number", "cursor", "order_id"], ["rhs_account_number"]),
        ("get_portfolio", ["account_number"], ["account_number"]),
        ("preview_crypto_order", ["rhs_account_number", "symbol", "side", "type", "time_in_force", "quantity", "dollar_amount", "limit_price", "stop_price"], ["rhs_account_number", "symbol", "side", "type"]),
        ("place_crypto_order", ["rhs_account_number", "symbol", "side", "type", "time_in_force", "quantity", "dollar_amount", "limit_price", "stop_price", "ref_id"], ["rhs_account_number", "symbol", "side", "type"]),
        ("cancel_crypto_order", ["rhs_account_number", "order_id"], ["rhs_account_number", "order_id"]),
    )
}
ACCOUNT = Account("SYNTHETIC", "Fixture", "cash", True, "active", "12345678", "RHC-FIXTURE", "individual")
INTENT = CryptoOrderIntent("BTC-USD", PAIR_ID, "buy", dollar_amount=Decimal("5.00"))
ORDER = {
    "id": ORDER_ID, "ref_id": INTENT.ref_id, "account_id": ACCOUNT_ID, "currency_pair_id": PAIR_ID,
    "side": "buy", "type": "market", "state": "confirmed", "time_in_force": "gtc",
    "quantity": "0.00005", "cumulative_quantity": "0", "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
    "speculative": False, "limit_price": None, "stop_price": None,
    "net_rounded_estimated_notional": "5.00", "net_rounded_executed_notional": None,
}
POSITION = {
    "account_id": ACCOUNT_ID, "currency_pair_id": PAIR_ID,
    "currency": {"code": "BTC", "name": "Bitcoin", "type": "cryptocurrency"},
    "quantity": "0.00010", "quantity_transferable": "0.00008", "quantity_held_for_sell": "0.00002",
    "cost_bases": [{"direct_quantity": "0.00004", "direct_cost_basis": "4.00", "intraday_quantity": "0", "intraday_cost_basis": "0"}],
}
EXECUTION = {
    "id": "44444444-4444-4444-8444-444444444444", "quantity": "0.00002", "price": "100000",
    "effective_price": "100500", "notional": "2.01", "timestamp": NOW.isoformat(),
}


class FakeCryptoServer:
    def __init__(self):
        self.now = NOW
        self.accounts = [ACCOUNT]
        self.calls = []
        self.responses = {
            "get_currency_pairs": {"results": [PAIR]},
            "get_crypto_quotes": {"results": [QUOTE]},
            "get_crypto_positions": {"results": [POSITION]},
            "get_crypto_orders": {"rhs_account_number": ACCOUNT.rhs_account_number, "crypto_account_number": ACCOUNT.rhc_account_number, "results": [ORDER]},
            "get_portfolio": {"crypto_buying_power": {"buying_power": "10.18"}},
            "preview_crypto_order": {"order": {**ORDER, "speculative": True}, "estimated_fee": "0.03", "crypto_account_number": ACCOUNT.rhc_account_number},
            "place_crypto_order": {"order": ORDER, "crypto_account_number": ACCOUNT.rhc_account_number},
            "cancel_crypto_order": {"accepted": True},
        }
        self.responses = deepcopy(self.responses)
        self.schemas = deepcopy(SCHEMAS)
        self.adapter = RobinhoodCrypto(self.call, lambda: self.schemas, self.get_accounts, clock=lambda: self.now)

    async def get_accounts(self):
        return self.accounts

    async def call(self, name, args):
        self.calls.append((name, deepcopy(args)))
        value = self.responses[name]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(args)
        return deepcopy(value)

"""Production broker-permission adapter for the mixed execution engine."""

from grande_alpha.standing import validate_contract


class VerifiedBrokerEligibility:
    """Resolve one exact instrument against the connected, pinned provider contract."""

    def __init__(self, broker):
        self.broker = broker

    async def __call__(self, account_number: str, intent) -> tuple[bool, bool]:
        validate_contract(self.broker)
        symbol = intent.symbol
        rows = await self.broker.get_tradability(account_number, [symbol])
        if set(rows) != {symbol} or rows[symbol].symbol != symbol:
            raise RuntimeError("Broker did not return exactly the requested instrument permission")
        item = rows[symbol]
        if item.tradeable is not True:
            return False, False
        review = await self.broker.review_order(account_number, intent)
        if review.intent != intent:
            raise RuntimeError("Broker review did not bind the exact proposed ticket")
        if review.checks:
            raise RuntimeError(f"Broker review returned order warnings; placement is locked: {review.checks}")
        # A successful dollar-notional review establishes fractional eligibility for
        # this exact ticket; a quantity ticket does not need fractional eligibility.
        return True, intent.dollar_amount is None or review is not None

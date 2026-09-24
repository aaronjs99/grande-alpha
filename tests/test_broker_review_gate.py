from datetime import UTC, datetime
from uuid import uuid4

import pytest

from grande_alpha.broker.permissions import VerifiedBrokerEligibility
from grande_alpha.domain.models import EquityTradability, OrderReview, Quote
from grande_alpha.execution.equity_execution import EquityOrderIntent
from grande_alpha.execution.standing import REQUIRED_TOOL_ARGUMENTS, ROBINHOOD_URL


@pytest.mark.asyncio
async def test_review_warnings_block_mixed_order_eligibility():
    now = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)
    intent = EquityOrderIntent(str(uuid4()), "TQQQ", "buy", "allocation",
                               dollar_amount=10, created_at=now)

    class Broker:
        def tool_contract_snapshot(self):
            return {"server_url": ROBINHOOD_URL, "tools": [
                {"name": name, "inputSchema": {"properties": {key: {} for key in arguments}}}
                for name, arguments in REQUIRED_TOOL_ARGUMENTS.items()
            ]}

        async def get_tradability(self, _account, _symbols):
            return {"TQQQ": EquityTradability("TQQQ", True, False, False)}

        async def review_order(self, _account, proposed):
            quote = Quote("TQQQ", 49.99, 50.01, 50.0, now, now, now)
            return OrderReview(proposed, None, {"buying_power": "insufficient"}, quote, {})

    with pytest.raises(RuntimeError, match="order warnings"):
        await VerifiedBrokerEligibility(Broker())("account-1", intent)

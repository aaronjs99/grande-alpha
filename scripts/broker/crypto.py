"""Account-scoped crypto transport primitives, separate from equity execution.

These methods do not grant trading authority. A future execution coordinator must
provide durable intent/reservation journaling and restart reconciliation before
connecting them to the research agent. No caller in the agent invokes mutations.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, localcontext

from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.discovery import ReadCall, RobinhoodDiscovery, object_rows
from grande_alpha.domain.account_models import Account
from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.crypto_models import (
    CryptoExecution,
    CryptoOrder,
    CryptoOrderIntent,
    CryptoPosition,
    CryptoReview,
    decimal_amount,
)

CRYPTO_TOOLS = frozenset({
    "get_crypto_positions", "get_crypto_orders", "get_portfolio",
    "preview_crypto_order", "place_crypto_order", "cancel_crypto_order",
})


class CryptoOutcomeUnknown(BrokerError):
    """A dispatched request may have reached the broker; reconcile, never resubmit."""

    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__(f"Crypto outcome is unresolved for {reference}; reconcile broker orders before any retry")


def parse_order(row: dict) -> CryptoOrder:
    from grande_alpha.broker.robinhood_contract import _required_bool, _required_datetime, _required_text

    if not isinstance(row, dict):
        raise BrokerError("Crypto response has no identifiable order")

    def text(key):
        return _required_text(row.get(key), field=f"crypto order {key}")

    def optional_amount(key):
        return decimal_amount(row[key], key) if row.get(key) is not None else None

    executions = tuple(CryptoExecution(
        _required_text(e.get("id"), field="crypto execution id"),
        decimal_amount(e.get("quantity"), "execution quantity", positive=True),
        decimal_amount(e.get("price"), "execution price", positive=True),
        decimal_amount(e.get("effective_price"), "effective price", positive=True),
        decimal_amount(e.get("notional"), "execution notional"),
        _required_datetime(e.get("timestamp"), field="crypto execution timestamp"),
    ) for e in object_rows({"executions": row.get("executions")}, "executions"))
    order = CryptoOrder(
        text("id"), text("ref_id") if row.get("ref_id") is not None else "",
        text("account_id"), text("currency_pair_id"), text("side"), text("type"),
        text("time_in_force"), text("state"), optional_amount("quantity"),
        decimal_amount(row.get("cumulative_quantity"), "cumulative quantity"),
        optional_amount("limit_price"), optional_amount("stop_price"),
        _required_datetime(row.get("created_at"), field="crypto created_at"),
        _required_datetime(row.get("updated_at"), field="crypto updated_at"),
        _required_bool(row["speculative"], field="crypto speculative") if row.get("speculative") is not None else None,
        str(row.get("routing") or ""), executions,
        optional_amount("net_rounded_estimated_notional"), optional_amount("net_rounded_executed_notional"),
    )
    if order.side not in {"buy", "sell"} or order.created_at > order.updated_at:
        raise BrokerError("Invalid crypto order side or chronology")
    if len({e.execution_id for e in executions}) != len(executions):
        raise BrokerError("Duplicate crypto execution identity")
    with localcontext() as context:
        context.prec = 60
        if sum((e.quantity for e in executions), Decimal(0)) > order.cumulative_quantity:
            raise BrokerError("Crypto executions exceed cumulative quantity")
    if order.quantity is not None and order.cumulative_quantity > order.quantity:
        raise BrokerError("Crypto cumulative quantity exceeds order quantity")
    # Missing fill rows or net totals remain visibly incomplete, never synthesized.
    return order


class RobinhoodCrypto:
    def __init__(
        self, call: ReadCall, schemas: Callable[[], dict],
        accounts: Callable[[], Awaitable[list[Account]]], *, clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._transport, self._schemas, self._accounts, self._clock = call, schemas, accounts, clock
        self._lock = asyncio.Lock()
        self._account_ids: dict[str, str] = {}
        self._reviews: dict[str, CryptoReview] = {}
        self._sent_refs: set[str] = set()

    def invalidate_reviews(self) -> None:
        self._reviews.clear()
        # Do not forget dispatched references or account pins on reconnect.

    async def _call(self, name: str, args: dict) -> dict:
        schema = self._schemas().get(name)
        if name not in CRYPTO_TOOLS or not isinstance(schema, dict):
            raise BrokerError(f"Crypto tool unavailable: {name}")
        if set(schema.get("required", [])) - args.keys() or args.keys() - schema.get("properties", {}).keys():
            raise BrokerError(f"Unsupported {name} input contract")
        return await self._transport(name, args)

    async def _account(self, account_number: str) -> Account:
        eligible = [a for a in await self._accounts() if a.agentic_allowed is True and a.state == "active"]
        if len(eligible) != 1 or eligible[0].account_number != account_number:
            raise BrokerError("Crypto requires the uniquely selected active Agentic account")
        account = eligible[0]
        rhs = account.rhs_account_number
        if not rhs or not rhs.isascii() or not rhs.isdigit() or not account.rhc_account_number:
            raise BrokerError("Selected account has no verified numeric RHS and linked crypto account")
        return account

    def _scope(self, account: Account, data: dict, *, echo: bool = False) -> None:
        if echo and data.get("rhs_account_number") != account.rhs_account_number:
            raise BrokerError("Crypto response changed or omitted the requested RHS account")
        if data.get("crypto_account_number") is not None and data["crypto_account_number"] != account.rhc_account_number:
            raise BrokerError("Crypto response belongs to a different linked account")

    def _pin(self, account: Account, identities: set[str]) -> None:
        if not identities:
            return
        old = self._account_ids.get(account.rhs_account_number)
        if len(identities) != 1 or (old is not None and identities != {old}):
            raise BrokerError("Crypto account UUID changed across scoped responses")
        self._account_ids[account.rhs_account_number] = next(iter(identities))

    async def _rows(self, name: str, account: Account, **filters) -> list[dict]:
        from grande_alpha.broker.robinhood_contract import _next_cursor

        args = {"rhs_account_number": account.rhs_account_number, **filters}
        rows, cursors = [], set()
        for _ in range(100):
            data = await self._call(name, args)
            self._scope(account, data, echo=name == "get_crypto_orders")
            rows.extend(object_rows(data, "results"))
            if len(rows) > 10000:
                raise BrokerError("Crypto reconciliation exceeded its row limit")
            cursor = _next_cursor(data, resource="crypto")
            if cursor is None:
                return rows
            if cursor in cursors:
                raise BrokerError("Crypto pagination repeated a cursor")
            cursors.add(cursor)
            args = {**args, "cursor": cursor}
        raise BrokerError("Crypto reconciliation exceeded its page limit")

    async def positions(self, account_number: str) -> list[CryptoPosition]:
        return await self._positions(await self._account(account_number))

    async def _positions(self, account: Account) -> list[CryptoPosition]:
        from grande_alpha.broker.robinhood_contract import _required_text

        positions = []
        for row in await self._rows("get_crypto_positions", account):
            currency = row.get("currency")
            if not isinstance(currency, dict):
                raise BrokerError("Crypto position omitted its currency identity")
            symbol = _required_text(currency.get("code"), field="crypto position currency")
            bases = object_rows({"bases": row.get("cost_bases")}, "bases")
            with localcontext() as context:
                context.prec = 60
                direct_quantity = sum((decimal_amount(b.get("direct_quantity"), "direct quantity") for b in bases), Decimal(0))
                direct_basis = sum((decimal_amount(b.get("direct_cost_basis"), "direct basis") for b in bases), Decimal(0))
            position = CryptoPosition(
                _required_text(row.get("account_id"), field="crypto position account"), symbol,
                str(row.get("currency_pair_id") or ""), decimal_amount(row.get("quantity"), "position quantity"),
                decimal_amount(row.get("quantity_transferable"), "sellable quantity"),
                decimal_amount(row.get("quantity_held_for_sell"), "held quantity"), direct_quantity, direct_basis,
            )
            if position.sellable_quantity + position.held_for_sell > position.quantity or direct_quantity > position.quantity:
                raise BrokerError("Crypto position components exceed its total quantity")
            positions.append(position)
        if len({p.symbol for p in positions}) != len(positions):
            raise BrokerError("Duplicate crypto position currency")
        self._pin(account, {p.account_id for p in positions})
        return positions

    async def orders(self, account_number: str, *, order_id: str = "") -> list[CryptoOrder]:
        return await self._orders(await self._account(account_number), order_id=order_id)

    async def _orders(self, account: Account, *, order_id: str = "") -> list[CryptoOrder]:
        filters = {"order_id": order_id} if order_id else {}
        orders = [parse_order(row) for row in await self._rows("get_crypto_orders", account, **filters)]
        if len({o.order_id for o in orders}) != len(orders):
            raise BrokerError("Duplicate crypto order identity")
        if order_id and any(o.order_id != order_id for o in orders):
            raise BrokerError("Crypto response changed the requested order")
        if any(o.speculative is True for o in orders):
            raise BrokerError("Crypto history returned a speculative order")
        self._pin(account, {o.account_id for o in orders})
        return orders

    async def _rules(self, account: Account, intent: CryptoOrderIntent):
        if not account.brokerage_account_type:
            raise BrokerError("Crypto tradability requires the brokerage account type")
        pairs = await RobinhoodDiscovery(self._transport, self._schemas()).currency_pairs()
        matching = [p for p in pairs if p.provider_id == intent.pair_id and p.symbol == intent.symbol]
        if len(matching) != 1 or matching[0].crypto_rules is None:
            raise BrokerError("Crypto pair is no longer available")
        rules = matching[0].crypto_rules
        rules.validate_intent(intent, account.brokerage_account_type)
        return rules

    @staticmethod
    def _match(order: CryptoOrder, intent: CryptoOrderIntent, *, placement: bool) -> None:
        for field, expected in (
            ("pair_id", intent.pair_id), ("side", intent.side), ("order_type", intent.order_type),
            ("time_in_force", intent.time_in_force), ("limit_price", intent.limit_price), ("stop_price", intent.stop_price),
        ):
            if getattr(order, field) != expected:
                raise BrokerError(f"Crypto order echoed a different {field}")
        if intent.quantity is not None and order.quantity != intent.quantity:
            raise BrokerError("Crypto order echoed a different quantity")
        if order.quantity is None or order.quantity <= 0:
            raise BrokerError("Crypto order omitted its resolved quantity")
        if placement and order.ref_id != intent.ref_id:
            raise BrokerError("Crypto placement omitted or changed its reference")
        if order.speculative is not (False if placement else True):
            raise BrokerError("Crypto response did not establish preview/placement status")

    async def _funds(self, account: Account, intent: CryptoOrderIntent, order: CryptoOrder) -> None:
        if intent.side == "sell":
            positions = await self._positions(account)
            available = [p for p in positions if p.pair_id == intent.pair_id and p.symbol == intent.symbol.removesuffix("-USD")]
            if len(available) != 1 or order.quantity is None or available[0].sellable_quantity < order.quantity:
                raise BrokerError("Insufficient verified sellable crypto quantity")
            if available[0].account_id != order.account_id:
                raise BrokerError("Crypto preview and position account identities differ")
            return
        data = await self._call("get_portfolio", {"account_number": account.account_number})
        bp = data.get("crypto_buying_power")
        if not isinstance(bp, dict):
            raise BrokerError("Crypto cash buying power is unavailable; equity buying power cannot substitute")
        buying_power = decimal_amount(bp.get("buying_power"), "crypto buying power")
        cost = order.net_estimated_notional
        if cost is None or cost <= 0:
            raise BrokerError("Crypto preview omitted its net estimated cost")
        # Conservative affordability check, not a capital reservation or a risk cap.
        # Dollar market/stop buys may debit ~1% more; limits can add a rounding cent.
        with localcontext() as context:
            context.prec = 60
            cost = max(cost, intent.dollar_amount or Decimal(0))
            cost = cost * Decimal("1.01") if intent.order_type in {"market", "stop_loss"} else cost
            cost += Decimal("0.01")
        if cost > buying_power:
            raise BrokerError("Crypto preview plus execution allowance exceeds crypto cash buying power")

    async def preview(self, account_number: str, intent: CryptoOrderIntent) -> CryptoReview:
        async with self._lock:
            intent.validate()
            if intent.ref_id in self._sent_refs:
                raise BrokerError("Crypto reference was already dispatched; reconcile it first")
            account = await self._account(account_number)
            rules = await self._rules(account, intent)
            data = await self._call("preview_crypto_order", intent.arguments(account.rhs_account_number))
            received_at = self._clock()
            self._scope(account, data)
            order = parse_order(data.get("order"))
            self._match(order, intent, placement=False)
            if not -2 <= (received_at - order.updated_at).total_seconds() <= 15:
                raise BrokerError("Crypto provider preview is stale or future-dated")
            if order.terminal or order.cumulative_quantity != 0 or order.executions:
                raise BrokerError("Crypto preview returned a terminal or executed order")
            if order.net_estimated_notional is None or order.net_estimated_notional <= 0:
                raise BrokerError("Crypto preview omitted its net estimated cost or credit")
            rules.validate_intent(replace(intent, quantity=order.quantity, dollar_amount=None), account.brokerage_account_type)
            self._pin(account, {order.account_id})
            await self._funds(account, intent, order)
            review = CryptoReview(intent, account_number, account.rhs_account_number, account.rhc_account_number,
                                  order, decimal_amount(data.get("estimated_fee"), "estimated fee"), received_at)
            self._reviews = {k: v for k, v in self._reviews.items() if 0 <= (self._clock() - v.reviewed_at).total_seconds() <= 15}
            self._reviews[intent.ref_id] = review
            return review

    async def place(self, review: CryptoReview) -> CryptoOrder:
        async with self._lock:
            intent = review.intent
            if self._reviews.get(intent.ref_id) is not review or intent.ref_id in self._sent_refs:
                raise BrokerError("Crypto placement requires this adapter's unused preview")
            account = await self._account(review.account_number)
            if (account.rhs_account_number, account.rhc_account_number) != (review.rhs_account_number, review.rhc_account_number):
                raise BrokerError("Crypto account binding changed after preview")
            await self._rules(account, intent)
            await self._funds(account, intent, review.order)
            if not 0 <= (self._clock() - review.reviewed_at).total_seconds() <= 15:
                raise BrokerError("Crypto preview expired; obtain a fresh review")
            if self._reviews.get(intent.ref_id) is not review:
                raise BrokerError("Crypto preview was invalidated during preflight")
            if len(self._sent_refs) >= 10000:
                raise BrokerError("Crypto dispatch limit reached; reconcile before restarting")
            args = intent.arguments(account.rhs_account_number, placement=True)
            self._sent_refs.add(intent.ref_id)
            self._reviews.pop(intent.ref_id, None)
            try:
                data = await self._call("place_crypto_order", args)
                self._scope(account, data)
                order = parse_order(data.get("order"))
                self._match(order, intent, placement=True)
                self._pin(account, {order.account_id})
                return order
            except asyncio.CancelledError:
                # The reference remains consumed even if the caller stops waiting.
                raise
            except Exception as exc:
                # Includes approval-only/no-order responses and invalid echoes.
                raise CryptoOutcomeUnknown(intent.ref_id) from exc

    async def cancel(self, account_number: str, order_id: str) -> bool:
        """Return request acceptance only; caller must reconcile the final state."""
        from grande_alpha.broker.robinhood_contract import _required_bool, _required_text

        async with self._lock:
            _required_text(order_id, field="crypto cancellation order id")
            account = await self._account(account_number)
            orders = await self._orders(account, order_id=order_id)
            if len(orders) != 1 or orders[0].terminal:
                raise BrokerError("Crypto cancellation requires one verified open order in this account")
            try:
                data = await self._call("cancel_crypto_order", {"rhs_account_number": account.rhs_account_number, "order_id": order_id})
                return _required_bool(data.get("accepted"), field="crypto cancellation accepted")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise CryptoOutcomeUnknown(order_id) from exc

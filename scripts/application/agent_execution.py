"""Broker-backed recovery and explicitly gated dispatch for the multi-market journal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext

from grande_alpha.broker.base import Broker, BrokerError, order_is_terminal
from grande_alpha.domain.crypto_models import CryptoOrder, CryptoReview
from grande_alpha.domain.models import Account, BrokerOrder, OrderReview, utc_now
from grande_alpha.persistence.agent_ledger import (
    AgentLedger,
    ExecutionObservation,
    ExecutionTicket,
    canonical,
    stamp,
)


def exact_float(value: float) -> Decimal:
    # Existing equity models use floats; don't introduce their binary expansion
    # into crypto arithmetic. Provider parsing has already validated finiteness.
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("Broker amount must be finite and nonnegative")
    return number


def crypto_ticket(review: CryptoReview, *, scope_id: str, strategy_fingerprint: str) -> ExecutionTicket:
    intent = review.intent
    intent.validate()
    if intent.order_type != "limit" or intent.quantity is None:
        raise ValueError("The new managed crypto route requires a quantity-based limit order")
    estimate = review.order.net_estimated_notional
    if estimate is None or estimate <= 0:
        raise ValueError("Crypto preview omitted net cost or credit")
    with localcontext() as context:
        context.prec = 60
        cash = max(estimate, intent.quantity * intent.limit_price + (review.estimated_fee if intent.side == "buy" else Decimal(0)))
        cash = (cash + Decimal("0.01")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    return ExecutionTicket(
        intent.ref_id, review.account_number, "crypto", intent.symbol, intent.pair_id,
        intent.side, intent.quantity, cash, scope_id, strategy_fingerprint,
        canonical(intent.arguments(review.rhs_account_number, placement=True)),
        review.order.account_id, review.rhs_account_number, review.rhc_account_number,
    )


def equity_ticket(account_number: str, review: OrderReview, *, scope_id: str, strategy_fingerprint: str, reserved_cash: Decimal) -> ExecutionTicket:
    # Does not broaden the existing equity adapter's validated ticker/route scope.
    intent = review.intent
    intent.validate()
    if intent.order_type != "limit" or intent.quantity is None:
        raise ValueError("The new managed equity route requires a quantity-based limit order")
    if review.checks:
        raise ValueError("Broker equity preview contains blocking order checks")
    if reserved_cash < exact_float(intent.quantity) * exact_float(intent.limit_price):
        raise ValueError("Equity cash reservation is below the reviewed limit notional")
    return ExecutionTicket(intent.ref_id, account_number, "equity", intent.symbol, "", intent.side,
                           exact_float(intent.quantity) if intent.quantity is not None else None,
                           reserved_cash, scope_id, strategy_fingerprint, canonical(intent.as_dict()))


def observe_crypto(ticket: ExecutionTicket, order: CryptoOrder) -> ExecutionObservation:
    if order.ref_id != ticket.ref_id or order.speculative is True or not order.execution_complete:
        raise ValueError("Crypto recovery lacks exact reference or complete fill economics")
    import json

    payload = json.loads(ticket.intent_json)
    for name, actual in (("type", order.order_type), ("time_in_force", order.time_in_force)):
        if payload.get(name) != actual:
            raise ValueError("Crypto recovered order route changed")
    for name in ("limit_price", "stop_price"):
        expected = Decimal(payload[name]) if name in payload else None
        if getattr(order, name) != expected:
            raise ValueError("Crypto recovered price bound changed")
    if ticket.quantity is not None and order.quantity != ticket.quantity:
        raise ValueError("Crypto recovered order quantity changed")
    executions = tuple({"id": e.execution_id, "quantity": str(e.quantity), "price": str(e.price),
                        "effective_price": str(e.effective_price), "notional": str(e.notional),
                        "timestamp": stamp(e.timestamp)} for e in order.executions)
    return ExecutionObservation(ticket.ref_id, order.order_id, ticket.key, order.side, order.pair_id,
                                order.account_id, order.cumulative_quantity, order.net_executed_notional or Decimal(0),
                                order.terminal, order.state, executions)


def observe_equity(ticket: ExecutionTicket, order: BrokerOrder) -> ExecutionObservation:
    import json

    order.validate_execution_provenance(require_snapshot=True)
    payload = json.loads(ticket.intent_json)
    echo = order.raw.get("ref_id")
    if echo is not None and echo != ticket.ref_id:
        raise ValueError("Equity recovery reference changed")
    if order.symbol != ticket.symbol:
        raise ValueError("Equity recovery symbol changed")
    for key in ("type", "time_in_force", "market_hours"):
        expected = payload["order_type" if key == "type" else key]
        if order.raw.get(key) != expected:
            raise ValueError("Equity recovered order route changed")
    if ticket.quantity is not None and exact_float(order.quantity) != ticket.quantity:
        raise ValueError("Equity recovered order quantity changed")
    if payload["dollar_amount"] is not None and exact_float(order.dollar_amount) != exact_float(payload["dollar_amount"]):
        raise ValueError("Equity recovered dollar sizing changed")
    if payload["limit_price"] is not None and exact_float(order.raw.get("price")) != exact_float(payload["limit_price"]):
        raise ValueError("Equity recovered limit price changed")
    with localcontext() as context:
        context.prec = 60
        cash = sum((exact_float(e.quantity) * exact_float(e.price) + (exact_float(e.fees) if order.side == "buy" else -exact_float(e.fees)) for e in order.executions), Decimal(0))
        quantity = sum((exact_float(e.quantity) for e in order.executions), Decimal(0))
    executions = tuple({"id": e.execution_id, "quantity": str(exact_float(e.quantity)),
                        "price": str(exact_float(e.price)), "fees": str(exact_float(e.fees)),
                        "timestamp": stamp(e.timestamp)} for e in order.executions)
    return ExecutionObservation(ticket.ref_id, order.order_id, ticket.key, order.side, "", "",
                                quantity, cash, order_is_terminal(order), order.state, executions)


class AgentExecutor:
    def __init__(self, broker: Broker, ledger: AgentLedger, *,
                 authorize: Callable[[ExecutionTicket], bool] = lambda _ticket: False,
                 authorize_cancel: Callable[[ExecutionTicket], bool] = lambda _ticket: False,
                 clock: Callable[[], datetime] = utc_now) -> None:
        self.broker, self.ledger, self._authorize, self._clock = broker, ledger, authorize, clock
        self._authorize_cancel = authorize_cancel
        self._lock = asyncio.Lock()

    async def _account(self, account_number: str) -> Account:
        accounts = [a for a in await self.broker.get_accounts() if a.agentic_allowed is True and a.state == "active"]
        if len(accounts) != 1 or accounts[0].account_number != account_number:
            raise BrokerError("Execution recovery requires the selected active Agentic account")
        return accounts[0]

    async def recover(self, account_number: str) -> None:
        """Read only: never resume, retry, preview, place, or cancel while recovering."""
        async with self._lock:
            await self._recover(account_number)

    async def cancel_managed(self, ticket: ExecutionTicket) -> bool:
        """Attempt one explicitly authorized cancellation; acceptance is not terminality."""
        async with self._lock:
            ticket.validate()
            if self._authorize_cancel(ticket) is not True:
                raise PermissionError("No managed cancellation authority")
            rows = [r for r in self.ledger.records(ticket.account_number) if r["ref_id"] == ticket.ref_id]
            if len(rows) != 1 or rows[0]["ticket"] != ticket:
                raise ValueError("Cancellation cannot adopt an unowned or changed order")
            if self.ledger.cancellation(ticket.ref_id) is not None:
                raise ValueError("Cancellation was already attempted; reconcile without resending")
            await self._recover(ticket.account_number)
            row = next(r for r in self.ledger.records(ticket.account_number) if r["ref_id"] == ticket.ref_id)
            if self._authorize_cancel(ticket) is not True:
                raise PermissionError("Managed cancellation authority was revoked")
            self.ledger.claim_cancellation(ticket, row["broker_order_id"], now=self._clock())
            try:
                cancel = self.broker.cancel_crypto_order if ticket.market == "crypto" else self.broker.cancel_order
                accepted = await cancel(ticket.account_number, row["broker_order_id"])
                self.ledger.note_cancellation(ticket.ref_id, accepted, now=self._clock())
                return accepted
            except BaseException:
                self.ledger.note_cancellation(ticket.ref_id, None, now=self._clock())
                raise

    async def _recover(self, account_number: str) -> None:
        records = [r for r in self.ledger.records(account_number) if r["submission_started_at"] is not None]
        if not records:
            return
        try:
            account = await self._account(account_number)
            observations, positions = [], {}
            for market in sorted({r["market"] for r in records}):
                owned_rows = [r for r in records if r["market"] == market]
                for row in owned_rows:
                    ticket = row["ticket"]
                    if market == "crypto" and (account.rhs_account_number, account.rhc_account_number) != (ticket.rhs_account_number, ticket.rhc_account_number):
                        raise ValueError("Crypto account binding changed since durable dispatch")
                orders = await (self.broker.get_crypto_orders(account_number) if market == "crypto" else self.broker.get_orders(account_number))
                claimed_ids = set()
                for row in owned_rows:
                    ticket = row["ticket"]
                    matches = [o for o in orders if (
                        o.order_id == row["broker_order_id"] if row["broker_order_id"] else
                        (o.ref_id if market == "crypto" else o.raw.get("ref_id")) == ticket.ref_id
                    )]
                    if len(matches) > 1 or (matches and matches[0].order_id in claimed_ids):
                        raise ValueError("Broker order reference is ambiguous or shared")
                    if matches:
                        claimed_ids.add(matches[0].order_id)
                        observations.append(observe_crypto(ticket, matches[0]) if market == "crypto" else observe_equity(ticket, matches[0]))
                earliest = min(datetime.fromisoformat(r["submission_started_at"]) for r in owned_rows) - timedelta(seconds=2)
                for order in orders:
                    relevant = any(
                        order.pair_id == r["ticket"].provider_id if market == "crypto" else order.symbol == r["ticket"].symbol
                        for r in owned_rows
                    )
                    if order.order_id not in claimed_ids and relevant:
                        if any(e.timestamp >= earliest for e in order.executions):
                            raise ValueError("An unowned execution changed a managed instrument; inventory provenance requires review")
                        if (order.cumulative_quantity or 0) > 0 and not order.executions:
                            raise ValueError("An unowned filled order lacks the provenance needed to reconcile managed inventory")
                holdings = await (self.broker.get_crypto_positions(account_number) if market == "crypto" else self.broker.get_positions(account_number))
                for p in holdings:
                    symbol = f"{p.symbol}-USD" if market == "crypto" else p.symbol
                    key = f"{market}:{symbol}"
                    if key in positions:
                        raise ValueError("Broker inventory contains duplicate asset identities")
                    if market == "crypto":
                        for row in owned_rows:
                            ticket = row["ticket"]
                            if ticket.key == key and (p.account_id, p.pair_id) != (ticket.broker_account_id, ticket.provider_id):
                                raise ValueError("Crypto recovered position identity changed")
                    positions[key] = p.quantity if market == "crypto" else exact_float(p.quantity)
            self.ledger.reconcile(account_number, observations, positions, now=self._clock())
        except Exception as exc:
            self.ledger.recovery_failed(account_number, str(exc), now=self._clock())
            raise

    async def _preflight(self, ticket: ExecutionTicket) -> Decimal | None:
        account = await self._account(ticket.account_number)
        if ticket.market == "crypto" and (account.rhs_account_number, account.rhc_account_number) != (ticket.rhs_account_number, ticket.rhc_account_number):
            raise ValueError("Crypto account changed before dispatch")
        actual, sellable = {}, {}
        equity = await self.broker.get_positions(ticket.account_number)
        crypto = await self.broker.get_crypto_positions(ticket.account_number)
        for market, positions in (("equity", equity), ("crypto", crypto)):
            for position in positions:
                if market == "crypto" and position.symbol == "USD":
                    continue
                key = f"{market}:{position.symbol}" + ("-USD" if market == "crypto" else "")
                if key in actual:
                    raise ValueError("Duplicate broker inventory identity")
                actual[key] = exact_float(position.quantity) if market == "equity" else position.quantity
                sellable[key] = exact_float(position.sellable_quantity) if market == "equity" else position.sellable_quantity
        expected = {key: qty for key, (qty, _cost) in self.ledger.inventory(ticket.account_number).items() if qty > 0}
        nonzero = {key: qty for key, qty in actual.items() if qty > 0}
        if ticket.side == "buy" and nonzero != expected:
            raise ValueError("Unmanaged stock/crypto holdings require review before allocating agent cash")
        if ticket.side == "sell" and (actual.get(ticket.key) != expected.get(ticket.key) or sellable.get(ticket.key, Decimal(0)) < ticket.quantity):
            raise ValueError("Managed exit lacks freshly verified sellable inventory")
        known = {(r["market"], r["broker_order_id"]) for r in self.ledger.records(ticket.account_number) if r["broker_order_id"]}
        orders = await self.broker.get_orders(ticket.account_number)
        crypto_orders = await self.broker.get_crypto_orders(ticket.account_number)
        if ticket.side == "sell" and (
            (ticket.market == "equity" and any(o.symbol == ticket.symbol and not order_is_terminal(o) for o in orders))
            or (ticket.market == "crypto" and any(o.pair_id == ticket.provider_id and not o.terminal for o in crypto_orders))
        ):
            raise ValueError("Managed exit conflicts with an open order on this instrument")
        if ticket.side == "buy" and (
            any(not order_is_terminal(o) and ("equity", o.order_id) not in known for o in orders)
            or any(not o.terminal and ("crypto", o.order_id) not in known for o in crypto_orders)
        ):
            raise ValueError("Unmanaged open orders reserve account funds")
        portfolio = await self.broker.get_portfolio(ticket.account_number)
        portfolio.validate()
        cash = portfolio.crypto_buying_power if ticket.market == "crypto" else min(portfolio.cash, portfolio.buying_power)
        if ticket.side == "buy" and cash is None:
            raise ValueError("Broker cash buying power is unavailable")
        return exact_float(cash) if ticket.side == "buy" else None

    async def submit_crypto(self, review: CryptoReview, ticket: ExecutionTicket) -> CryptoOrder:
        expected = crypto_ticket(review, scope_id=ticket.scope_id, strategy_fingerprint=ticket.strategy_fingerprint)
        if ticket != expected:
            raise ValueError("Crypto dispatch differs from its immutable reviewed ticket")
        def fresh():
            if not 0 <= (self._clock() - review.reviewed_at).total_seconds() <= 15:
                raise ValueError("Crypto preview expired before durable dispatch")

        return await self._submit(ticket, lambda: self.broker.place_crypto_order(review), fresh)

    async def submit_equity(self, review: OrderReview, ticket: ExecutionTicket) -> BrokerOrder:
        expected = equity_ticket(ticket.account_number, review, scope_id=ticket.scope_id,
                                 strategy_fingerprint=ticket.strategy_fingerprint, reserved_cash=ticket.reserved_cash)
        if ticket != expected:
            raise ValueError("Equity dispatch differs from its immutable reviewed ticket")
        def fresh():
            review.quote.validate()
            if review.quote.symbol != ticket.symbol or review.quote.age_seconds(self._clock()) > 15:
                raise ValueError("Equity preview quote is stale or belongs to another instrument")
            if ((review.quote.latest_book_timestamp or review.quote.timestamp) - self._clock()).total_seconds() > 2:
                raise ValueError("Equity preview quote is future-dated")
        # Existing controller remains responsible for equity confirmation, quote,
        # evidence, market-session, and account checks through the authorizer.
        return await self._submit(ticket, lambda: self.broker.place_order(ticket.account_number, review.intent), fresh)

    async def _submit(self, ticket, submit, validate_review):
        async with self._lock:
            ticket.validate()
            if any(row["ref_id"] == ticket.ref_id for row in self.ledger.records(ticket.account_number)):
                raise ValueError("Logical execution reference already exists; it cannot be reused")
            if self._authorize(ticket) is not True:
                raise PermissionError("No live multi-market authority; saved budgets grant no trading permission")
            await self._recover(ticket.account_number)
            available_cash = await self._preflight(ticket)
            validate_review()
            self.ledger.reserve(ticket, available_cash=available_cash, now=self._clock())
            try:
                if self._authorize(ticket) is not True:
                    raise PermissionError("Live execution authority was revoked before dispatch")
                validate_review()
            except BaseException:
                self.ledger.release(ticket.ref_id, now=self._clock())
                raise
            self.ledger.claim_dispatch(ticket, now=self._clock())
            try:
                order = await submit()
                self.ledger.note_submission(ticket.ref_id, order.order_id, now=self._clock())
                return order
            except BaseException:
                # Cancellation/timeouts and process death after this commit cannot
                # release the budget or make this UUID eligible for another send.
                self.ledger.note_submission(ticket.ref_id, None, now=self._clock())
                raise

    def status_payload(self, account_number: str) -> dict:
        status = self.ledger.status(account_number, now=self._clock())
        return {**asdict(status), "limits": asdict(status.limits), "account_number": account_number}

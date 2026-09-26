"""Mixed-strategy dispatch, closed by default without an exact user permit.

The foreground CLI activates this engine through separate authorization, broker,
data, and risk checks. Source availability does not imply provider acceptance.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import math
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from grande_alpha.broker.base import order_is_terminal
from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.market_calendar import regular_session_times
from grande_alpha.execution.equity_execution import EquityOrderIntent, EquityScope, assess_ticket
from grande_alpha.execution.read_retry import read_with_backoff
from grande_alpha.research.portfolio_replay import EASTERN
from grande_alpha.strategy.mixed_portfolio import AllocationPolicy, plan


def mixed_candidate_digest(scope: EquityScope, policy: AllocationPolicy, earnings_thresholds: dict) -> str:
    """Canonical identity shared by offline checks and the live engine."""
    scope.validate()
    policy.validate()
    raw = json.dumps(
        {"scope": asdict(scope), "policy": asdict(policy),
         "earnings_thresholds": earnings_thresholds},
        sort_keys=True, default=str, allow_nan=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _closed_gate(_):
    raise RuntimeError("Mixed-strategy authorization is not configured")


async def run_cycles(
    engine, source, *, poll_seconds: float = 5, max_cycles: int | None = None,
    cycle_reporter: Callable[[dict], None] | None = None,
):
    """Drive an already-armed engine from current broker and stored earnings observations.

    Never renews authority, reconnects/retries broker writes, or installs a scheduler.
    The optional local reporter receives completed cycle results for status display only.
    """
    from grande_alpha.data.earnings import _number

    _number(poll_seconds, "poll_seconds", positive=True)
    if max_cycles is not None and (type(max_cycles) is not int or max_cycles <= 0):
        raise ValueError("max_cycles must be a positive integer")
    count = 0

    async def idle():
        loop = asyncio.get_running_loop()
        until = loop.time() + poll_seconds
        renewal = loop.time()
        while loop.time() < until:
            engine._check()
            if loop.time() >= renewal:
                engine.heartbeat()
                renewal = loop.time() + 5
            await asyncio.sleep(min(.1, max(0, until-loop.time())))

    try:
        while True:
            engine.heartbeat()
            now = utc_now().astimezone(EASTERN)
            session = regular_session_times(now.date())
            if session is None or not session[0] <= now.time().replace(tzinfo=None) < session[1]:
                # Standing authorization is valid independently of exchange hours.
                # Keep the process and lease alive, but do not request a strategy
                # snapshot or construct an order while this v1 route is closed.
                await idle()
                continue
            source_task = asyncio.create_task(source())
            try:
                async with asyncio.timeout(30):
                    while not source_task.done():
                        await asyncio.sleep(.1)
                        engine.heartbeat()
                    snapshot = await source_task
            except (ConnectionError, TimeoutError, OSError):
                source_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionError, TimeoutError, OSError):
                    await source_task
                engine.audit.receipt("mixed_data_wait", "Read-only market data unavailable; no order submitted",
                                     {}, "warning")
                await idle()
                continue
            except BaseException:
                source_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await source_task
                raise
            engine._check()
            result = await engine.cycle(snapshot["request"], snapshot["theses"])
            if cycle_reporter is not None:
                cycle_reporter(result)
            count += 1
            if max_cycles is not None and count >= max_cycles:
                return count
            # Observe revocation while idle too, without launching another process.
            await idle()
    finally:
        # Revocation is automatic; cancellation remains a separate authorized action.
        await engine.shutdown(cancel_open=False)


class MixedExecutionEngine:
    def __init__(self, broker, ledger, audit, scope: EquityScope, policy: AllocationPolicy,
                 earnings_thresholds: dict, *, eligibility, authorization_gate=_closed_gate,
                 research_verifier=None):
        scope.validate()
        policy.validate()
        self.broker, self.ledger, self.audit = broker, ledger, audit
        self.scope, self.policy = scope, policy
        self.thresholds = copy.deepcopy(earnings_thresholds)
        self.eligibility = eligibility
        self.authorization_gate = authorization_gate
        self.research_verifier = research_verifier
        self.authority_id = None
        self._lease_owner = None
        self._lock = asyncio.Lock()
        self._digest = self.digest()

    def digest(self):
        return mixed_candidate_digest(self.scope, self.policy, self.thresholds)

    def arm(self):
        """Require an exact user permit before acquiring the durable process lease."""
        if self.authority_id is not None:
            raise RuntimeError("Revoke the existing session before rearming")
        if self.authorization_gate(self._digest) is not True:
            raise RuntimeError("Authorization did not approve the exact scope")
        if not self._scope_current():
            raise RuntimeError("Mixed scope has expired")
        authority_id = str(uuid.uuid4())
        lease_owner = str(uuid.uuid4())
        self.ledger.acquire_lease(self.scope.account_number, lease_owner, now=utc_now())
        try:
            self.audit.register_standing(authority_id, self._digest)
            self.audit.receipt("mixed_authority", "Mixed-engine session armed",
                               {"scope_digest": self._digest, "authority_id": authority_id}, "warning")
        except BaseException:
            self.ledger.release_lease(self.scope.account_number, lease_owner)
            self.audit.stop_standing(authority_id)
            raise
        self.authority_id, self._lease_owner = authority_id, lease_owner

    def recover(self, authority_id: str) -> None:
        """Recover an unexpired, unchanged durable grant after process restart."""
        if self.authority_id is not None or not isinstance(authority_id, str) or not authority_id.strip():
            raise RuntimeError("Recovery requires one inactive engine and an exact authority ID")
        if self.authorization_gate(self._digest) is not True:
            raise RuntimeError("Authorization did not approve the exact recovered scope")
        if not self._scope_current():
            raise RuntimeError("Recovered mixed scope has expired")
        if not self.audit.standing_active(authority_id, self._digest):
            raise RuntimeError("Standing authority is missing, stopped, or changed")
        lease_owner = str(uuid.uuid4())
        self.ledger.acquire_lease(self.scope.account_number, lease_owner, now=utc_now())
        self.authority_id, self._lease_owner = authority_id, lease_owner
        self.audit.receipt("mixed_recovery", "Existing mixed authority recovered after restart",
                           {"scope_digest": self._digest, "authority_id": authority_id}, "warning")

    def stop(self):
        authority_id, self.authority_id = self.authority_id, None
        lease_owner, self._lease_owner = self._lease_owner, None
        if authority_id is not None:
            self.audit.stop_standing(authority_id)
        if lease_owner is not None:
            self.ledger.release_lease(self.scope.account_number, lease_owner)

    async def shutdown(self, *, cancel_open: bool = True) -> dict:
        """Revoke writes immediately, then cancel and verify only ledger-owned orders."""
        authority_id = self.authority_id
        owned = self.ledger.unresolved(self.scope.account_number)
        self.stop()
        if not cancel_open:
            remaining = sorted(row["ref"] for row in owned)
            self.audit.receipt("mixed_shutdown", "Local authority stopped; broker order status unverified",
                               {"authority_id": authority_id, "unresolved_refs": remaining},
                               "critical" if remaining else "warning")
            return {"authority_id": authority_id, "cancelled": [], "remaining": remaining,
                    "broker_verified": False}
        if not owned:
            return {"authority_id": authority_id, "cancelled": [], "remaining": [],
                    "broker_verified": False}
        orders = await read_with_backoff(lambda: self.broker.get_orders(self.scope.account_number))
        by_id = {order.order_id: order for order in orders}
        cancelled = []
        for row in owned:
            order = by_id.get(row["order_id"])
            if order is not None and not order_is_terminal(order):
                accepted = await self.broker.cancel_order(self.scope.account_number, order.order_id)
                if accepted is not True:
                    raise RuntimeError("Broker did not explicitly accept cancellation of a Grande Alpha order")
                cancelled.append(order.order_id)
        refreshed = await read_with_backoff(lambda: self.broker.get_orders(self.scope.account_number))
        remaining = sorted(order.order_id for order in refreshed
                           if not order_is_terminal(order)
                           and any(row["order_id"] == order.order_id for row in owned))
        self.audit.receipt("mixed_shutdown", "Mixed authority revoked and owned orders checked",
                           {"authority_id": authority_id, "cancelled": cancelled,
                            "remaining": remaining}, "critical" if remaining else "warning")
        if remaining:
            raise RuntimeError("One or more Grande Alpha orders remain open after cancellation")
        return {"authority_id": authority_id, "cancelled": cancelled, "remaining": remaining,
                "broker_verified": True}

    def heartbeat(self) -> None:
        self._check()
        self.ledger.renew_lease(self.scope.account_number, self._lease_owner, now=utc_now())

    def _scope_current(self) -> bool:
        now = utc_now()
        return self.scope.expires_at is None or now < self.scope.expires_at

    def _check(self):
        if (self.authority_id is None or self._lease_owner is None or self.digest() != self._digest
                or not self._scope_current()
                or not self.audit.standing_active(self.authority_id, self._digest)
                or not self.ledger.lease_active(self.scope.account_number, self._lease_owner, now=utc_now())):
            raise RuntimeError("Mixed authority stopped, expired, changed, or missing")
        if self.authorization_gate(self._digest) is not True:
            raise RuntimeError("Authorization did not approve the exact scope")

    def _projected_caps(self, intent, snapshot, request):
        if intent.side == "sell":
            return
        from grande_alpha.data.earnings import _time

        _, portfolio, positions, quotes, _ = snapshot
        values = {p.symbol: p.quantity*quotes[p.symbol].ask for p in positions}
        values[intent.symbol] = values.get(intent.symbol, 0)+intent.dollar_amount
        etfs = {"TQQQ", "SQQQ"}
        stocks = {s: v for s, v in values.items() if s not in etfs and v > 0}
        risk = {row["symbol"]: row for row in request["stock_risk"]}
        sectors = {}
        for symbol, value in stocks.items():
            if symbol not in risk:
                raise RuntimeError("Held stock sector/risk metadata is missing")
            age = (utc_now()-_time(risk[symbol]["available_at"], "risk available_at")).total_seconds()/86400
            if not 0 <= age <= self.policy.max_risk_age_days:
                raise RuntimeError("Held stock risk metadata is stale")
            sector = risk[symbol]["sector"]
            sectors[sector] = sectors.get(sector, 0)+value
        total = portfolio.total_value
        bounds = [(sum(values.values()), self.policy.max_invested_fraction),
                  (sum(stocks.values()), self.policy.max_stock_fraction),
                  (sum(v for s, v in values.items() if s in etfs), self.policy.max_etf_fraction),
                  (sum(v*(3 if s in etfs else 1) for s, v in values.items()), self.policy.max_gross_leverage_proxy)]
        bounds += [(v, self.policy.max_single_stock_fraction) for v in stocks.values()]
        bounds += [(v, self.policy.max_sector_fraction) for v in sectors.values()]
        if any(value > total*cap+1e-8 for value, cap in bounds):
            raise RuntimeError("Projected portfolio violates the allocation envelope")

    async def _snapshot(self):
        started = utc_now()
        accounts = [a for a in await read_with_backoff(self.broker.get_accounts)
                    if a.account_number == self.scope.account_number]
        if len(accounts) != 1:
            raise RuntimeError("Exact scoped account was not found uniquely")
        if accounts[0].agentic_allowed is not True:
            raise RuntimeError("Broker Agentic order permission is unavailable")
        portfolio = await read_with_backoff(lambda: self.broker.get_portfolio(self.scope.account_number))
        portfolio.validate()
        positions = await read_with_backoff(lambda: self.broker.get_positions(self.scope.account_number))
        orders = await read_with_backoff(lambda: self.broker.get_orders(self.scope.account_number))
        if len({o.order_id for o in orders}) != len(orders):
            raise RuntimeError("Duplicate provider order identities")
        for pending in self.ledger.unresolved(self.scope.account_number):
            matches = [o for o in orders if o.order_id == pending["order_id"] or o.raw.get("ref_id") == pending["ref"]]
            if len(matches) > 1:
                raise RuntimeError("Ambiguous broker reference mapping")
            if matches:
                self.ledger.observe(self.scope.account_number, pending["ref"], matches[0], observed_at=utc_now())
        if self.ledger.unresolved(self.scope.account_number) or any(not order_is_terminal(o) for o in orders):
            raise RuntimeError("Outstanding order must reconcile before another allocation")
        inventory = self.ledger.inventory(self.scope.account_number)
        if len({p.symbol for p in positions}) != len(positions):
            raise RuntimeError("Duplicate position symbols")
        actual = {p.symbol: p.quantity for p in positions}
        if any(not math.isclose(inventory.get(s, 0), actual.get(s, 0), abs_tol=1e-8, rel_tol=1e-8)
               for s in set(inventory) | set(actual)):
            raise RuntimeError("Account inventory does not match durable execution history")
        quotes = await read_with_backoff(
            lambda: self.broker.get_quotes(sorted(set(self.scope.allowed_symbols) | set(actual)))
        )
        self._check()
        day = utc_now().astimezone(EASTERN).date().isoformat()
        usage = self.ledger.daily_usage(self.scope.account_number, day)
        legacy = self.audit.live_daily_usage(self.scope.account_number, day)
        pnl = self.ledger.mark_to_market_pnl(self.scope.account_number, quotes)
        self.audit.record_mixed_daily_pnl(
            self.scope.account_number, day, pnl["total_usd"], self.scope.max_daily_loss_usd,
            require_existing=usage["orders"]+legacy["submitted_orders"] > 0,
            carry_previous_observation=True, scope_digest=self._digest,
            recovery_delay=self.scope.loss_recovery_delay,
            recovery_unit=self.scope.loss_recovery_unit,
        )
        return accounts[0], portfolio, positions, quotes, started

    def _plan(self, request, theses, snapshot):
        _, portfolio, positions, quotes, _ = snapshot
        data = copy.deepcopy(request)
        if data["policy"] != asdict(self.policy) or data["earnings"]["thresholds"] != self.thresholds:
            raise RuntimeError("Research parameters differ from the bound candidate")
        if self.research_verifier is not None:
            for event in data["earnings"]["events"]:
                if self.research_verifier(event) is not True:
                    raise RuntimeError("Earnings event lacks exact point-in-time provider provenance")
        if not isinstance(theses, dict) or any(type(value) is not bool for value in theses.values()):
            raise ValueError("An explicit thesis status map is required")
        opened = self.ledger.holding_open_times(self.scope.account_number)
        data["capital_usd"] = portfolio.total_value
        data["holdings"] = []
        for position in positions:
            if position.symbol not in theses or position.symbol not in opened:
                raise RuntimeError("Holding thesis or opening provenance is missing")
            data["holdings"].append({"symbol": position.symbol, "market_value": position.quantity*quotes[position.symbol].mid,
                                     "opened_at": opened[position.symbol], "thesis_valid": theses[position.symbol]})
        result = plan(data)
        from grande_alpha.data.earnings import _time

        now = utc_now()
        if not 0 <= (now-_time(result["as_of"], "as_of")).total_seconds() <= self.scope.max_quote_age_seconds:
            raise RuntimeError("Allocation research snapshot is stale or future-dated")
        if not set(result["targets_usd"]) <= set(self.scope.allowed_symbols):
            raise RuntimeError("Allocation proposes an asset outside the exact scope")
        return result

    async def cycle(self, research_request: dict, theses: dict):
        """At most one ticket per cycle; always reconcile before considering another."""
        if self._lock.locked():
            return {"status": "BUSY", "submitted": False}
        async with self._lock:
            ref = None
            attempted = False
            driver = asyncio.current_task()

            async def watch():
                last_renewal = asyncio.get_running_loop().time()
                while True:
                    await asyncio.sleep(.1)
                    try:
                        if asyncio.get_running_loop().time() - last_renewal >= 5:
                            self.heartbeat()
                            last_renewal = asyncio.get_running_loop().time()
                        self._check()
                    except Exception:
                        driver.cancel()
                        return

            watcher = asyncio.create_task(watch())
            try:
                self._check()
                async with asyncio.timeout(30):
                    initial = await self._snapshot()
                    self._plan(research_request, theses, initial)
                    # Freshness checks and allocation must survive a second broker snapshot.
                    snapshot = await self._snapshot()
                    allocation = self._plan(research_request, theses, snapshot)
                    account, portfolio, positions, quotes, reconciled_at = snapshot
                    reviews = [r for r in allocation["rebalance_reviews"] if r["review_rebalance"] and abs(r["delta_usd"]) > .01]
                    reviews.sort(key=lambda r: (r["delta_usd"] >= 0, r["symbol"]))
                    if not reviews:
                        return {"status": "NO_TICKET", "submitted": False}
                    selected = reviews[0]
                    symbol = selected["symbol"]
                    quote = quotes[symbol]
                    amount = min(abs(selected["delta_usd"]), self.scope.max_order_usd)
                    ref = str(uuid.uuid4())
                    if selected["delta_usd"] > 0:
                        if theses.get(symbol) is not True:
                            return {"status": "THESIS_NOT_VALID", "submitted": False}
                        amount = float(Decimal(str(amount)).quantize(Decimal(".01"), rounding=ROUND_DOWN))
                        if amount <= 0:
                            return {"status": "BELOW_MINIMUM", "submitted": False}
                        intent = EquityOrderIntent(ref, symbol, "buy", "mixed allocation", dollar_amount=amount, created_at=utc_now())
                    else:
                        held = next(p for p in positions if p.symbol == symbol)
                        quantity = min(held.sellable_quantity, amount/quote.ask)
                        quantity = float(Decimal(str(quantity)).quantize(Decimal(".000001"), rounding=ROUND_DOWN))
                        if quantity <= 0:
                            return {"status": "BELOW_MINIMUM", "submitted": False}
                        intent = EquityOrderIntent(ref, symbol, "sell", "mixed allocation exit", quantity=quantity, created_at=utc_now())
                    tradable, fractional = await self.eligibility(account.account_number, intent)
                    now = utc_now()
                    day = now.astimezone(EASTERN).date().isoformat()
                    usage = self.ledger.daily_usage(account.account_number, day)
                    recent_orders = self.ledger.orders_since(account.account_number, now - timedelta(minutes=1))
                    legacy = self.audit.live_daily_usage(account.account_number, day)
                    pnl = self.ledger.mark_to_market_pnl(account.account_number, quotes)
                    loss = self.audit.record_mixed_daily_pnl(
                        account.account_number, day, pnl["total_usd"], self.scope.max_daily_loss_usd,
                        require_existing=usage["orders"]+legacy["submitted_orders"] > 0,
                        scope_digest=self._digest,
                        recovery_delay=self.scope.loss_recovery_delay,
                        recovery_unit=self.scope.loss_recovery_unit,
                    )
                    decision = assess_ticket(intent, self.scope, account=account, portfolio=portfolio,
                                             positions=positions, quotes=quotes, now=now, reconciled_at=reconciled_at,
                                             tradable=tradable, fractional=fractional,
                                             daily_notional=usage["notional"]+legacy["daily_notional"],
                                             daily_orders=usage["orders"]+legacy["submitted_orders"],
                                             daily_loss_latched=loss["loss_latched"] or loss["recovery_blocked"],
                                             has_unresolved_orders=False,
                                             orders_last_minute=recent_orders)
                    if not decision["allowed"]:
                        return {"status": "RISK_BLOCKED", "submitted": False, "reasons": decision["reasons"]}
                    self._projected_caps(intent, snapshot, research_request)
                    self._check()
                    self.ledger.reserve(account.account_number, self.authority_id, intent)
                    self._check()
                    self.ledger.mark_submitting(ref, submitted_at=now, notional=decision["estimated_notional"])
                    attempted = True
                    self._check()
                    order = await self.broker.place_order(account.account_number, intent)
                    self.ledger.observe(account.account_number, ref, order, observed_at=utc_now())
                    self.audit.receipt("mixed_order", "Stock-capable order response recorded", {"ref": ref}, "warning")
                    return {"status": "RESPONSE_RECORDED", "submitted": True, "ref": ref}
            except BaseException as exc:
                if ref is None and isinstance(exc, (ConnectionError, TimeoutError, OSError)):
                    self.audit.receipt("mixed_read_wait", "Broker read unavailable before order selection",
                                       {}, "warning")
                    return {"status": "READ_UNAVAILABLE", "submitted": False}
                self.stop()
                if ref is not None:
                    if attempted:
                        # Keep any terminal/open binding already written; otherwise mark unknown.
                        pending = self.ledger.unresolved(self.scope.account_number)
                        if any(r["ref"] == ref and r["state"] == "submitting" for r in pending):
                            self.ledger.mark_unknown(ref)
                    else:
                        pending = self.ledger.unresolved(self.scope.account_number)
                        if any(r["ref"] == ref and r["state"] == "reserved" for r in pending):
                            self.ledger.abandon_reserved(ref)
                self.audit.receipt("mixed_failure", "Mixed execution stopped; inspect unresolved orders", {"ref": ref}, "critical")
                raise
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

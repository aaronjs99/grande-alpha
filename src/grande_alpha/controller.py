from __future__ import annotations

import asyncio
import hashlib
import math
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from PySide6.QtCore import QObject, Signal

from grande_alpha.action_lab import (
    ALL_PAIR_ACTIONS,
    PairAction,
    TradeCommand,
    live_feasible_action_ids,
    pair_action_for_target,
)
from grande_alpha.broker.base import (
    Broker,
    BrokerError,
    ShadowOnlyBroker,
    normalized_order_state,
    order_is_terminal,
)
from grande_alpha.candidate_execution import (
    CandidateExecutionContract,
    annualized_volatility,
    contract_from_app_and_sandbox,
    decision_due,
    held_minutes,
    runtime_parity_assessment,
    size_entry,
)
from grande_alpha.config import AppConfig
from grande_alpha.evidence import strategy_fingerprint
from grande_alpha.execution import ExecutionProfile, execution_profile
from grande_alpha.live_reconciliation import (
    LiveSubmissionReconciliation,
    reconcile_execution,
)
from grande_alpha.models import (
    Account,
    BrokerOrder,
    LiveGrant,
    OrderIntent,
    OrderReview,
    Portfolio,
    Position,
    Quote,
    Regime,
    utc_now,
)
from grande_alpha.models import (
    Signal as TradeSignal,
)
from grande_alpha.policy import (
    EASTERN,
    DecisionPolicy,
    PolicyConfig,
    PolicyPosition,
    market_session_allowed,
    session_bounds,
)
from grande_alpha.risk import RiskEngine
from grande_alpha.sandbox import load_sandbox_config
from grande_alpha.shadow import LiveShadowEngine
from grande_alpha.storage import AuditStore
from grande_alpha.strategy import BarBuilder, StrategyConfig, build_strategy


def _runtime_strategy_config(config: AppConfig) -> StrategyConfig:
    return StrategyConfig(
        strategy_name=config.strategy_name,
        warmup_bars=config.warmup_bars,
        fast_ema=config.fast_ema,
        slow_ema=config.slow_ema,
        trend_threshold_bps=config.trend_threshold_bps,
        momentum_bars=config.momentum_bars,
    )


@dataclass
class TradingSnapshot:
    connected: bool = False
    account: Account | None = None
    portfolio: Portfolio | None = None
    quotes: dict[str, Quote] = field(default_factory=dict)
    positions: list[Position] = field(default_factory=list)
    orders: list[BrokerOrder] = field(default_factory=list)
    signal: TradeSignal = field(default_factory=lambda: TradeSignal(Regime.FLAT, 0.0, "Not connected"))
    strategy_running: bool = False
    live_status: str = "LOCKED"
    session_expires_at: datetime | None = None
    drawdown: float = 0.0
    trades_today: int = 0
    last_refresh: datetime | None = None
    last_reconcile_at: datetime | None = None
    shadow_running: bool = False
    shadow_equity: float = 0.0
    shadow_pnl: float = 0.0
    shadow_position: str | None = None
    shadow_fills: int = 0
    pair_action_id: int = 4
    pair_action_label: str = "(0,0)"
    last_analysis_at: datetime | None = None
    last_trade_decision_at: datetime | None = None


class TradingController(QObject):
    snapshot_changed = Signal(object)
    event = Signal(str, str)
    connection_busy = Signal(bool)

    def __init__(
        self,
        broker: Broker,
        config: AppConfig,
        store: AuditStore,
        *,
        shadow_only_runtime: bool = False,
        auto_shadow_sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        config.validate_cadence()
        if shadow_only_runtime and config.market_hours != "regular_hours":
            raise ValueError("Auto-shadow v1 supports regular market hours only")
        self.broker = broker
        self.config = config
        self.store = store
        self.shadow_only_runtime = shadow_only_runtime
        self._auto_shadow_sleep = auto_shadow_sleep or asyncio.sleep
        self.risk = RiskEngine(config.no_trade_open_minutes, config.no_trade_close_minutes)
        self.strategy = build_strategy(_runtime_strategy_config(config))
        self.bar_builder = BarBuilder("QQQ", config.bar_seconds)
        self.policy = DecisionPolicy(
            PolicyConfig(
                hard_stop_pct=config.hard_stop_pct,
                take_profit_pct=config.take_profit_pct,
                max_hold_minutes=config.max_hold_minutes,
                no_trade_open_minutes=config.no_trade_open_minutes,
                no_trade_close_minutes=config.no_trade_close_minutes,
                market_hours=config.market_hours,
            )
        )
        self.snapshot = TradingSnapshot()
        self._quote_lock = asyncio.Lock()
        self._reconcile_lock = asyncio.Lock()
        self._safe_read_only_refresh_lock = asyncio.Lock()
        self._last_qqq_timestamp: datetime | None = None
        self._last_submission_at: datetime | None = None
        self._submission_reconcile_required: dict[str, str | None] = {}
        self._uncertain_submission_refs: set[str] = set()
        self._live_submissions: dict[str, LiveSubmissionReconciliation] = {}
        self._confirmed_entry_order_ids: set[str] = set()
        self._prior_entry_upper_bound = 0
        self._cleanup_unresolved = False
        self._analysis_sequence = 0
        self._last_trade_decision_sequence = 0
        self._recent_returns = {"TQQQ": deque(maxlen=30), "SQQQ": deque(maxlen=30)}
        self._previous_prices: dict[str, float] = {}
        self._shadow: LiveShadowEngine | None = None

    def _require_order_runtime(self, action: str) -> None:
        if self._safe_read_only_refresh_lock.locked():
            raise RuntimeError(
                f"Safe read-only refresh is in progress; wait before attempting to {action}"
            )
        if self.shadow_only_runtime:
            self.log(
                f"BLOCKED: auto-shadow runtime cannot {action}; no broker write was attempted",
                "critical",
                "shadow_only_boundary",
                {"action": action, "broker_write_attempted": False},
            )
            raise RuntimeError(f"Auto-shadow runtime is read-only and cannot {action}")

    def _reset_signal_pipeline(self, reason: str) -> None:
        self.strategy = build_strategy(_runtime_strategy_config(self.config))
        self.bar_builder = BarBuilder("QQQ", self.config.bar_seconds)
        self._last_qqq_timestamp = None
        self._analysis_sequence = 0
        self._last_trade_decision_sequence = 0
        self.snapshot.signal = TradeSignal(Regime.FLAT, 0.0, reason)
        self.snapshot.last_analysis_at = None
        self._recent_returns = {"TQQQ": deque(maxlen=30), "SQQQ": deque(maxlen=30)}
        self._previous_prices.clear()

    def _runtime_candidate_config(self, config: AppConfig | None = None):
        """Bind the saved research candidate to every runtime-owned behavior field."""

        active = config or self.config
        return replace(
            load_sandbox_config(),
            strategy_name=active.strategy_name,
            warmup_bars=active.warmup_bars,
            fast_ema=active.fast_ema,
            slow_ema=active.slow_ema,
            trend_threshold_bps=active.trend_threshold_bps,
            momentum_bars=active.momentum_bars,
            hard_stop_pct=active.hard_stop_pct,
            take_profit_pct=active.take_profit_pct,
            max_hold_minutes=active.max_hold_minutes,
            no_trade_open_minutes=active.no_trade_open_minutes,
            no_trade_close_minutes=active.no_trade_close_minutes,
            decision_stride=active.trade_every_bars,
            market_hours=active.market_hours,
            order_type=active.order_type,
            time_in_force=active.time_in_force,
            limit_offset_bps=active.limit_offset_bps,
            settlement_model=active.settlement_model,
        )

    def runtime_execution_contract(self, config: AppConfig | None = None) -> CandidateExecutionContract:
        active = config or self.config
        candidate = self._runtime_candidate_config(active)
        return contract_from_app_and_sandbox(active, candidate)

    def current_strategy_fingerprint(
        self,
        grant: LiveGrant | None = None,
        config: AppConfig | None = None,
    ) -> str:
        active = config or self.config
        candidate = self._runtime_candidate_config(active)
        return strategy_fingerprint(candidate, f"{active.bar_seconds}s", execution=grant)

    def _persist_risk_receipts(self) -> None:
        for receipt in self.risk.drain_receipts():
            payload = receipt.as_dict()
            self.store.receipt(
                "authority_action",
                f"{receipt.action}: {receipt.reason}",
                payload,
                "warning" if receipt.action in {"authority_granted", "order_submitted"} else "info",
            )

    @staticmethod
    def _nonterminal_orders(orders: list[BrokerOrder]) -> list[BrokerOrder]:
        return [order for order in orders if not order_is_terminal(order)]

    def _update_runtime_returns(self, quotes: dict[str, Quote]) -> None:
        for symbol in ("TQQQ", "SQQQ"):
            quote = quotes.get(symbol)
            if quote is None:
                continue
            previous = self._previous_prices.get(symbol)
            if previous is not None and previous > 0:
                self._recent_returns[symbol].append(quote.mid / previous - 1.0)
            self._previous_prices[symbol] = quote.mid

    @staticmethod
    def _order_ref_id(order: BrokerOrder) -> str:
        for key in ("ref_id", "client_order_id", "client_id"):
            value = order.raw.get(key)
            if value:
                return str(value)
        return ""

    def _reconcile_submission_tracking(
        self,
        orders: list[BrokerOrder],
        positions: list[Position] | None = None,
    ) -> None:
        by_order_id = {order.order_id: order for order in orders if order.order_id}
        by_ref_id = {
            reference: order
            for order in orders
            if (reference := self._order_ref_id(order))
        }
        unresolved = []
        reconciled_positions = self.snapshot.positions if positions is None else positions
        for ref_id, order_id in list(self._submission_reconcile_required.items()):
            order = by_order_id.get(order_id or "") or by_ref_id.get(ref_id)
            if order is None:
                unresolved.append(ref_id)
                continue
            self.store.update_intent(ref_id, order.order_id, normalized_order_state(order.state))
            tracking = self._live_submissions.get(ref_id)
            if tracking is not None:
                try:
                    execution = reconcile_execution(tracking, order, reconciled_positions)
                except ValueError as exc:
                    self.log(
                        f"LIVE EXECUTION DEVIATION for {order.side.upper()} {order.symbol}: {exc}",
                        "critical",
                        "live_fill_reconciliation",
                        {
                            "ref_id": ref_id,
                            "order_id": order.order_id,
                            "broker_state": normalized_order_state(order.state),
                            "authority_locked": True,
                        },
                    )
                    raise BrokerError(str(exc)) from exc
                if execution.incremental_quantity > 1e-9 or execution.status in {
                    "awaiting_inventory",
                    "filled",
                    "no_fill",
                }:
                    self.log(
                        f"Reconciled {execution.status.replace('_', ' ')} for "
                        f"{order.side.upper()} {order.symbol}",
                        "warning" if execution.status == "awaiting_inventory" else "info",
                        "live_fill_reconciliation",
                        {
                            "ref_id": ref_id,
                            "order_id": order.order_id,
                            "broker_state": normalized_order_state(order.state),
                            "cumulative_quantity": execution.cumulative_quantity,
                            "incremental_quantity": execution.incremental_quantity,
                            "cumulative_notional": execution.cumulative_notional,
                            "incremental_notional": execution.incremental_notional,
                            "average_price": execution.average_price,
                            "conservative_clock": (
                                execution.conservative_clock.isoformat()
                                if execution.conservative_clock is not None
                                else None
                            ),
                            "actual_fill_timestamp_available": False,
                            "resolved": execution.resolved,
                            "reason": execution.reason,
                        },
                    )
                if (
                    order.side.strip().lower() == "buy"
                    and execution.cumulative_quantity > 1e-9
                    and order.order_id not in self._confirmed_entry_order_ids
                ):
                    self._confirmed_entry_order_ids.add(order.order_id)
                    self.log(
                        f"Confirmed live entry #{len(self._confirmed_entry_order_ids)} "
                        f"from broker inventory for {order.symbol}",
                        category="live_entry_ledger",
                        payload={
                            "order_id": order.order_id,
                            "ref_id": ref_id,
                            "confirmed_entries_in_process": len(
                                self._confirmed_entry_order_ids
                            ),
                            "conservative_session_entry_count": (
                                self._prior_entry_upper_bound
                                + len(self._confirmed_entry_order_ids)
                            ),
                            "durable_fill_timestamp_available": False,
                        },
                    )
                if not execution.resolved:
                    self._submission_reconcile_required[ref_id] = order.order_id
                    continue
            elif order_is_terminal(order) and (
                self.risk.grant is not None or self.snapshot.strategy_running
            ):
                raise BrokerError(
                    "A terminal tracked order lacks its in-process fill baseline; "
                    "automatic continuation is locked"
                )
            if order_is_terminal(order):
                self._submission_reconcile_required.pop(ref_id, None)
                self._uncertain_submission_refs.discard(ref_id)
                self._live_submissions.pop(ref_id, None)
            else:
                self._submission_reconcile_required[ref_id] = order.order_id
        if unresolved:
            self._cleanup_unresolved = True
            joined = ", ".join(reference[:8] for reference in unresolved)
            raise BrokerError(
                "Broker reconciliation could not prove the outcome of placement reference(s) "
                f"{joined}; automation remains locked and no retry is allowed"
            )
        self._cleanup_unresolved = False

    def _validate_reconciled_live_state(self) -> None:
        leveraged = self._leveraged_positions()
        if len(leveraged) > 1:
            raise BrokerError("Both TQQQ and SQQQ are held; autonomous execution is locked")
        for position in leveraged:
            if position.quantity < 0:
                raise BrokerError(
                    f"Negative {position.symbol.strip().upper()} inventory cannot be managed "
                    "by long-only autonomous execution"
                )
            if position.sellable_quantity > position.quantity + 1e-9:
                raise BrokerError(
                    f"Broker sellable quantity exceeds held {position.symbol.strip().upper()} inventory"
                )
        tracked_ids = {
            order_id for order_id in self._submission_reconcile_required.values() if order_id
        }
        tracked_refs = set(self._submission_reconcile_required)
        external_open = [
            order
            for order in self._nonterminal_orders(self.snapshot.orders)
            if order.order_id not in tracked_ids and self._order_ref_id(order) not in tracked_refs
        ]
        if external_open and (self.risk.grant is not None or self.snapshot.strategy_running):
            raise BrokerError(
                f"Found {len(external_open)} nonterminal Agentic order(s) outside this live session"
            )

    @staticmethod
    def _validate_account_truth(
        portfolio: Portfolio,
        positions: list[Position],
        orders: list[BrokerOrder],
    ) -> None:
        portfolio.validate()
        for position in positions:
            values = (position.quantity, position.sellable_quantity)
            if not all(math.isfinite(float(value)) for value in values):
                raise BrokerError(f"Broker returned non-finite position data for {position.symbol}")
            if position.sellable_quantity < 0:
                raise BrokerError(f"Broker returned negative sellable quantity for {position.symbol}")
            if position.average_price is not None and not math.isfinite(float(position.average_price)):
                raise BrokerError(f"Broker returned non-finite average price for {position.symbol}")
        for order in orders:
            if not order.order_id or not order.symbol or not order.side or not order.state:
                raise BrokerError("Broker returned an order with incomplete identity or state")
            for value in (order.quantity, order.dollar_amount, order.average_price):
                if value is not None and not math.isfinite(float(value)):
                    raise BrokerError(f"Broker returned non-finite order data for {order.order_id}")

    def _validated_execution_quotes(
        self,
        quotes: dict[str, Quote],
        reference: datetime | None = None,
        *,
        max_age_seconds: float | None = None,
        context: str = "Live execution",
    ) -> dict[str, Quote]:
        required = {"QQQ", "TQQQ", "SQQQ"}
        if set(quotes) != required:
            missing = ", ".join(sorted(required - set(quotes))) or "none"
            raise BrokerError(f"{context} requires exact QQQ/TQQQ/SQQQ quotes; missing {missing}")
        observed = reference or utc_now()
        age_limit = (
            float(max_age_seconds)
            if max_age_seconds is not None
            else float(self.config.default_max_quote_age_seconds)
        )
        if not math.isfinite(age_limit) or age_limit <= 0:
            raise BrokerError(f"{context} quote-age limit is invalid")
        timestamps = []
        for symbol in sorted(required):
            quote = quotes[symbol]
            quote.validate()
            if quote.symbol != symbol:
                raise BrokerError(f"{context} quote key/symbol mismatch for {symbol}")
            age = (observed - quote.timestamp).total_seconds()
            if age < -2.0 or age > age_limit:
                raise BrokerError(
                    f"{context} {symbol} venue quote is not fresh ({age:.1f}s; "
                    f"limit {age_limit:.1f}s)"
                )
            timestamps.append(quote.timestamp)
        skew = (max(timestamps) - min(timestamps)).total_seconds()
        skew_limit = min(5.0, age_limit)
        if skew > skew_limit:
            raise BrokerError(
                f"{context} quote timestamps are misaligned by {skew:.1f}s; "
                f"limit {skew_limit:.1f}s"
            )
        return quotes

    def _validated_shadow_quotes(
        self,
        quotes: dict[str, Quote],
        reference: datetime | None = None,
    ) -> dict[str, Quote]:
        return self._validated_execution_quotes(quotes, reference, context="Auto-shadow")

    def auto_shadow_start_allowed(self, reference: datetime | None = None) -> bool:
        if not self.shadow_only_runtime or self.config.market_hours != "regular_hours":
            return False
        observed = reference or utc_now()
        local = observed.astimezone(EASTERN)
        opened, _closed = session_bounds(observed, "regular_hours")
        deadline = opened + timedelta(minutes=5)
        return local.weekday() < 5 and observed < deadline

    def auto_shadow_market_open(self, reference: datetime | None = None) -> datetime:
        observed = reference or utc_now()
        opened, _closed = session_bounds(observed, "regular_hours")
        return opened

    def auto_shadow_session_complete(self, reference: datetime | None = None) -> bool:
        if not self.shadow_only_runtime:
            return False
        observed = reference or utc_now()
        _opened, closed = session_bounds(observed, "regular_hours")
        return observed.astimezone(EASTERN).weekday() < 5 and observed >= closed

    def _validate_auto_shadow_config(self) -> None:
        if not self.config.broker_connection_enabled:
            raise RuntimeError("Broker read capability is disabled")
        if self.config.live_trading_enabled is not False:
            raise RuntimeError("Real-order capability must be disabled for auto-shadow")
        if self.config.market_hours != "regular_hours":
            raise RuntimeError("Auto-shadow v1 supports regular market hours only")

    async def _refresh_auto_shadow_account_state(self) -> None:
        if not self.snapshot.connected or self.snapshot.account is None:
            raise BrokerError("Auto-shadow could not resolve the exact Agentic account")
        account_number = self.snapshot.account.account_number
        portfolio = await self.broker.get_portfolio(account_number)
        portfolio.validate()
        positions = await self.broker.get_positions(account_number)
        orders = await self.broker.get_orders(account_number)
        self._validate_account_truth(portfolio, positions, orders)
        for position in positions:
            if position.symbol.strip().upper() in {"TQQQ", "SQQQ"} and not all(
                math.isfinite(float(value)) for value in (position.quantity, position.sellable_quantity)
            ):
                raise BrokerError(f"Auto-shadow received invalid real position data for {position.symbol}")
        real_positions = [
            position
            for position in positions
            if position.symbol.strip().upper() in {"TQQQ", "SQQQ"}
            and abs(position.quantity) > 1e-12
        ]
        open_orders = [order for order in orders if not order_is_terminal(order)]
        if real_positions:
            symbols = ", ".join(position.symbol for position in real_positions)
            raise BrokerError(f"Auto-shadow preflight found real leveraged position(s): {symbols}")
        if open_orders:
            raise BrokerError(f"Auto-shadow preflight found {len(open_orders)} open Agentic order(s)")
        self.snapshot.portfolio = portfolio
        self.snapshot.positions = positions
        self.snapshot.orders = orders
        self.risk.update_portfolio(portfolio)

    def _emit(self) -> None:
        self.snapshot.live_status = self.risk.session_status()
        self.snapshot.drawdown = self.risk.drawdown
        self.snapshot.trades_today = self.risk.trades_today
        liquidation_in_progress = bool(
            self.snapshot.live_status == "LOSS LIMIT"
            and self.risk.grant is not None
            and self._leveraged_positions()
        )
        self.snapshot.strategy_running = (
            self.snapshot.strategy_running
            and (self.snapshot.live_status == "LIVE" or liquidation_in_progress)
        )
        if self._shadow is not None:
            state = self._shadow.state
            self.snapshot.shadow_running = state.active
            self.snapshot.shadow_equity = state.equity
            self.snapshot.shadow_pnl = state.pnl
            self.snapshot.shadow_position = state.position.symbol if state.position else None
            self.snapshot.shadow_fills = len(state.fills)
        self.snapshot_changed.emit(self.snapshot)

    def log(
        self, summary: str, severity: str = "info", category: str = "runtime", payload: Any = None
    ) -> None:
        self.store.receipt(category, summary, payload, severity)
        self.event.emit(severity, summary)

    async def connect(self) -> None:
        if not self.config.broker_connection_enabled:
            raise RuntimeError("Broker connections are disabled. Enable the capability in Settings first")
        self.connection_busy.emit(True)
        try:
            await self.broker.connect()
            accounts = await self.broker.get_accounts()
            candidates = [
                account
                for account in accounts
                if account.agentic_allowed and account.state.strip().lower() == "active"
            ]
            if not candidates:
                raise BrokerError("No active Robinhood account is enabled for this agent")
            if len(candidates) != 1:
                raise BrokerError(
                    "GRANDE Alpha requires exactly one active Agentic account; "
                    f"provider returned {len(candidates)}"
                )
            account = candidates[0]
            self.snapshot.account = account
            self.snapshot.connected = True
            durable_unresolved = self.store.unresolved_order_intents(account.account_number)
            for row in durable_unresolved:
                reference = str(row["ref_id"])
                order_id = row.get("broker_order_id")
                self._submission_reconcile_required[reference] = str(order_id) if order_id else None
                self._uncertain_submission_refs.add(reference)
            if durable_unresolved:
                self.log(
                    f"Recovered {len(durable_unresolved)} unresolved placement intent(s); "
                    "new authority stays locked until broker reconciliation proves each outcome",
                    "critical",
                    "order_recovery",
                    {"references": [str(row["ref_id"]) for row in durable_unresolved]},
                )
            self.log(
                f"Connected to {account.nickname} {account.masked} ({account.account_type})",
                category="connection",
                payload={"last4": account.account_number[-4:], "type": account.account_type},
            )
            await self.refresh(evaluate=False)
        except Exception as exc:
            self.snapshot.connected = False
            self.log(f"Connection failed: {exc}", "error", "connection")
            raise
        finally:
            self.connection_busy.emit(False)
            self._emit()

    async def disconnect(self) -> None:
        if self.shadow_only_runtime:
            await self.disconnect_shadow_only("Auto-shadow read-only disconnect")
            return
        cleanup_verified = await self.stop_and_cancel("Disconnected by user")
        if not cleanup_verified:
            raise BrokerError(
                "Disconnect blocked: GRANDE Alpha could not verify every Agentic order terminal. "
                "Check Robinhood, retry STOP + CANCEL, and keep this window open."
            )
        await self.broker.disconnect()
        self.snapshot = TradingSnapshot()
        self._emit()

    async def disconnect_shadow_only(
        self,
        reason: str = "Auto-shadow read-only disconnect",
        *,
        flatten_virtual: bool = False,
    ) -> None:
        """Disconnect without reviewing, placing, or cancelling any broker order."""

        self.stop_shadow(reason, flatten_virtual=flatten_virtual)
        self.snapshot.strategy_running = False
        self.snapshot.session_expires_at = None
        self.risk.disarm()
        try:
            await self.broker.disconnect()
        finally:
            self.snapshot = TradingSnapshot()
            self.log(
                f"{reason}; broker writes remained BLOCKED",
                "warning",
                "shadow_only_boundary",
                {"broker_write_attempted": False},
            )
            self._emit()

    async def auto_start_shadow(self) -> bool:
        """Connect and start a fresh, read-only live-shadow run."""

        if not self.shadow_only_runtime:
            raise RuntimeError("Auto-shadow startup requires the shadow-only runtime boundary")
        self.risk.disarm()
        self.snapshot.strategy_running = False
        self.snapshot.session_expires_at = None
        try:
            self._validate_auto_shadow_config()
            if not self.auto_shadow_start_allowed():
                raise RuntimeError(
                    "Auto-shadow start window is closed; launch before 9:35 AM ET on a weekday"
                )
            await self.connect()
            await self._refresh_auto_shadow_account_state()
            opened = self.auto_shadow_market_open()
            deadline = opened + timedelta(minutes=5)
            self.log(
                "AUTO SHADOW WAITING — regular open 9:30 AM ET; writes blocked",
                "warning",
                "shadow_only_boundary",
                {"broker_write_attempted": False, "market_open": opened.isoformat()},
            )
            while utc_now() < opened:
                remaining = max(0.0, (opened - utc_now()).total_seconds())
                await self._auto_shadow_sleep(min(30.0, remaining))

            self._validate_auto_shadow_config()
            await self._refresh_auto_shadow_account_state()
            quotes: dict[str, Quote] | None = None
            last_quote_error: Exception | None = None
            while utc_now() < deadline:
                try:
                    candidate = await self.broker.get_quotes(["QQQ", "TQQQ", "SQQQ"])
                    quotes = self._validated_shadow_quotes(candidate)
                    break
                except Exception as exc:
                    last_quote_error = exc
                    self.log(
                        f"AUTO SHADOW WAITING for fresh exact venue quotes: {exc}",
                        "warning",
                        "shadow_only_boundary",
                        {"broker_write_attempted": False},
                    )
                    remaining = max(0.0, (deadline - utc_now()).total_seconds())
                    await self._auto_shadow_sleep(min(self.config.poll_seconds, remaining))
            if quotes is None:
                raise BrokerError(
                    "Fresh exact venue quotes were unavailable by 9:35 AM ET"
                    + (f": {last_quote_error}" if last_quote_error else "")
                )
            self.snapshot.quotes = quotes
            self.snapshot.last_refresh = utc_now()
            for quote in quotes.values():
                self.store.record_quote(quote)
            self._validate_auto_shadow_config()
            self._reset_signal_pipeline("Auto-shadow clean start; warming up")
            self.start_shadow()
            self.log(
                "AUTO SHADOW ACTIVE — read-only broker data and virtual fills; live writes BLOCKED",
                "warning",
                "shadow_only_boundary",
                {
                    "account_last4": self.snapshot.account.account_number[-4:],
                    "market_hours": self.config.market_hours,
                    "broker_write_attempted": False,
                },
            )
            self._emit()
            return True
        except Exception as exc:
            self.stop_shadow("AUTO SHADOW BLOCKED")
            self.snapshot.strategy_running = False
            self.snapshot.session_expires_at = None
            self.risk.disarm()
            self.log(
                f"AUTO SHADOW BLOCKED: {exc}",
                "critical",
                "shadow_only_boundary",
                {"error": str(exc), "broker_write_attempted": False},
            )
            try:
                await self.broker.disconnect()
            except Exception as disconnect_exc:
                self.log(
                    f"AUTO SHADOW BLOCKED: read-only disconnect failed: {disconnect_exc}",
                    "error",
                    "shadow_only_boundary",
                )
            self.snapshot = TradingSnapshot()
            self._emit()
            return False

    def update_config(self, config: AppConfig) -> None:
        """Apply safe runtime settings; a bar-size change starts a fresh warm-up."""
        config.validate_cadence()
        if self.shadow_only_runtime and config.market_hours != "regular_hours":
            raise ValueError("Auto-shadow v1 cannot switch away from regular market hours")
        config_changed = config != self.config
        bar_changed = config.bar_seconds != self.config.bar_seconds
        trade_cadence_changed = config.trade_every_bars != self.config.trade_every_bars
        signal_changed = any(
            getattr(config, name) != getattr(self.config, name)
            for name in (
                "strategy_name",
                "warmup_bars",
                "fast_ema",
                "slow_ema",
                "trend_threshold_bps",
                "momentum_bars",
            )
        )
        if config_changed and (self.risk.grant is not None or self.snapshot.strategy_running):
            self._revoke_live_automation(
                "Settings changed; the previous live certificate and session grant were revoked"
            )
        if config_changed and self._shadow is not None and self._shadow.state.active:
            self.stop_shadow("Settings changed; the previous live-shadow run was stopped")
        last_qqq_timestamp = self._last_qqq_timestamp
        self.config = config
        self.risk.no_trade_open_minutes = config.no_trade_open_minutes
        self.risk.no_trade_close_minutes = config.no_trade_close_minutes
        active_market_hours = (
            self.risk.grant.market_hours if self.risk.grant is not None else config.market_hours
        )
        self.policy = self._policy_for_session(active_market_hours)
        if bar_changed or signal_changed:
            reset_reason = (
                "Settings changed; CASH champion holds no position"
                if config.strategy_name == "cash"
                else "Settings changed; warming up"
            )
            self._reset_signal_pipeline(reset_reason)
            # The new builder must not ingest the same provider observation again.
            # Preserve only the duplicate guard; all partial-bar and strategy state
            # was discarded by the atomic pipeline reset above.
            self._last_qqq_timestamp = last_qqq_timestamp
            self.log(
                f"Runtime strategy changed/reset to {config.strategy_name}; "
                f"{config.bar_seconds}s completed-bar pipeline reset",
                "warning",
                "cadence",
            )
        elif trade_cadence_changed:
            self._last_trade_decision_sequence = self._analysis_sequence
            self.log(
                f"Trade decision cadence changed to every {config.trade_every_bars} analysis bars "
                f"({config.trade_seconds}s nominal)",
                "warning",
                "cadence",
            )

    async def refresh(self, evaluate: bool = True) -> None:
        """Compatibility full refresh used at connect and after manual actions."""
        if not self.snapshot.connected or self.snapshot.account is None:
            return
        await self.reconcile()
        await self.refresh_quotes(evaluate=evaluate)

    async def safe_read_only_refresh(self) -> None:
        """Atomically refresh readiness inputs through a broker-write-blocking facade.

        Unlike the normal live refresh path, this inspection method never invokes live-state
        cleanup. Any read, validation, or concurrent-authority failure is reported without review,
        placement, or cancellation. Normal refresh retains its fail-closed cleanup behavior.
        """

        if not self.snapshot.connected or self.snapshot.account is None:
            raise BrokerError("Safe checks require a connected Agentic account")
        if self.risk.grant is not None or self.snapshot.strategy_running:
            raise BrokerError("Safe checks require no live grant and a stopped strategy")
        if self._safe_read_only_refresh_lock.locked():
            raise BrokerError("A safe read-only refresh is already in progress")

        async with self._safe_read_only_refresh_lock:
            account_number = self.snapshot.account.account_number
            read_only = ShadowOnlyBroker(self.broker)

            def require_inactive() -> None:
                if self.risk.grant is not None or self.snapshot.strategy_running:
                    raise BrokerError(
                        "Safe checks stopped because live authority or the strategy became active"
                    )

            try:
                require_inactive()
                accounts = await read_only.get_accounts()
                require_inactive()
                active = [
                    account
                    for account in accounts
                    if account.agentic_allowed and account.state.strip().lower() == "active"
                ]
                if len(active) != 1 or active[0].account_number != account_number:
                    raise BrokerError(
                        "Safe checks require the same exact single active Agentic account"
                    )

                # Sequential reads match the MCP adapter's single-flight contract. Values remain
                # local until every read, validation, and concurrent-authority check succeeds.
                portfolio = await read_only.get_portfolio(account_number)
                require_inactive()
                positions = await read_only.get_positions(account_number)
                require_inactive()
                orders = await read_only.get_orders(account_number)
                require_inactive()
                quotes = await read_only.get_quotes(["QQQ", "TQQQ", "SQQQ"])
                require_inactive()

                self._validate_account_truth(portfolio, positions, orders)
                quotes = self._validated_execution_quotes(
                    quotes,
                    max_age_seconds=self.config.default_max_quote_age_seconds,
                    context="Safe readiness inspection",
                )
                require_inactive()

                now = utc_now()
                self.snapshot.portfolio = portfolio
                self.snapshot.positions = positions
                self.snapshot.orders = orders
                self.snapshot.quotes = quotes
                self.snapshot.last_reconcile_at = now
                self.snapshot.last_refresh = now
                self.log(
                    "Safe read-only activation checks completed; broker write methods remained blocked",
                    category="read_only_check",
                    payload={"broker_write_attempted": False, "account_last4": account_number[-4:]},
                )
            except Exception as exc:
                self.log(
                    f"Safe read-only activation checks stopped: {exc}",
                    "error",
                    "read_only_check",
                    {"broker_write_attempted": False},
                )
                raise
            finally:
                self._emit()

    async def reconcile(self) -> None:
        """Refresh slower account truth without coupling it to the quote clock."""
        if not self.snapshot.connected or self.snapshot.account is None:
            return
        if self._reconcile_lock.locked():
            return
        async with self._reconcile_lock:
            try:
                # Keep these sequential. The MCP adapter intentionally serializes tool calls;
                # enqueueing all three at once would starve a pending fast quote read behind
                # the entire reconciliation batch.
                if self.shadow_only_runtime:
                    await self._refresh_auto_shadow_account_state()
                else:
                    account_number = self.snapshot.account.account_number
                    portfolio = await self.broker.get_portfolio(account_number)
                    positions = await self.broker.get_positions(account_number)
                    orders = await self.broker.get_orders(account_number)
                    self._validate_account_truth(portfolio, positions, orders)
                    self.snapshot.portfolio = portfolio
                    self.snapshot.positions = positions
                    self.snapshot.orders = orders
                    self.snapshot.last_reconcile_at = utc_now()
                    self.risk.update_portfolio(portfolio)
                    self._reconcile_submission_tracking(orders, positions)
                    self._validate_reconciled_live_state()
                    if (
                        self.risk.session_status() == "LOSS LIMIT"
                        and not self._leveraged_positions()
                        and not self._submission_reconcile_required
                    ):
                        self._revoke_live_automation(
                            "Daily-loss liquidation is confirmed flat; session authority revoked"
                        )
            except Exception as exc:
                self.log(f"Account reconciliation failed: {exc}", "error", "broker")
                if self.shadow_only_runtime and self.snapshot.shadow_running:
                    self.stop_shadow(f"AUTO SHADOW BLOCKED: account truth/invariant failure: {exc}")
                    self.log(
                        "AUTO SHADOW BLOCKED: account truth/invariant check failed; "
                        "virtual execution stopped",
                        "critical",
                        "shadow_only_boundary",
                        {"error": str(exc), "broker_write_attempted": False},
                    )
                    try:
                        await self.broker.disconnect()
                    except Exception as disconnect_exc:
                        self.log(
                            f"AUTO SHADOW BLOCKED: read-only disconnect failed: {disconnect_exc}",
                            "error",
                            "shadow_only_boundary",
                        )
                    self.snapshot = TradingSnapshot()
                if self.snapshot.strategy_running or self.risk.grant is not None:
                    await self.stop_and_cancel(
                        "Account reconciliation failed; authority revoked and open orders require cancellation"
                    )
                    self.log(
                        "Strategy locked after account reconciliation failure; filled positions may remain open",
                        "critical",
                        "risk",
                    )
            finally:
                self._emit()

    async def refresh_quotes(self, evaluate: bool = True) -> None:
        """Read one batched quote snapshot; overlapping timer ticks are coalesced."""
        if not self.snapshot.connected or self.snapshot.account is None:
            return
        if self._quote_lock.locked():
            return
        async with self._quote_lock:
            try:
                quotes = await self.broker.get_quotes(["QQQ", "TQQQ", "SQQQ"])
                if self.shadow_only_runtime:
                    quotes = self._validated_shadow_quotes(quotes)
                elif self.risk.grant is not None or self.snapshot.strategy_running:
                    max_age = (
                        self.risk.grant.max_quote_age_seconds
                        if self.risk.grant is not None
                        else self.config.default_max_quote_age_seconds
                    )
                    quotes = self._validated_execution_quotes(
                        quotes,
                        max_age_seconds=max_age,
                        context="Autonomous live execution",
                    )
                self.snapshot.quotes = quotes
                self.snapshot.last_refresh = utc_now()
                for quote in quotes.values():
                    self.store.record_quote(quote)
                qqq = quotes.get("QQQ")
                if qqq and (self._last_qqq_timestamp is None or qqq.timestamp > self._last_qqq_timestamp):
                    self._last_qqq_timestamp = qqq.timestamp
                    bar = self.bar_builder.update(qqq)
                    if bar is not None:
                        self.store.record_bar(bar)
                        signal = self.strategy.on_bar(bar)
                        self._analysis_sequence += 1
                        self.snapshot.signal = signal
                        self.snapshot.last_analysis_at = signal.timestamp
                        self.store.record_signal(signal)
                        self._update_runtime_returns(quotes)
                        self.log(f"Signal: {signal.regime.value} — {signal.reason}", category="signal")
                        if self._shadow is not None and self._shadow.state.active:
                            # This quote caused BarBuilder to emit the completed analysis bar,
                            # so this accepted batch is the first causal quote/open of the
                            # following bar. Timestamp the virtual decision at the latest exact
                            # venue observation in the batch so a fill can never be recorded
                            # before the target quote used to price it.
                            causal_timestamp = max(quote.timestamp for quote in quotes.values())
                            if causal_timestamp <= signal.timestamp:
                                raise BrokerError(
                                    "Shadow execution batch is not later than the completed analysis bar"
                                )
                            fills = self._shadow.on_causal_quote(causal_timestamp, signal, quotes)
                            for fill in fills:
                                self.log(
                                    f"SHADOW {fill.side.upper()} {fill.quantity:.6f} {fill.symbol} "
                                    f"at ${fill.price:.2f}",
                                    "market",
                                    "shadow_fill",
                                    fill.as_dict(),
                                )
                if evaluate and self.snapshot.strategy_running:
                    await self._evaluate_and_trade()
            except Exception as exc:
                self.log(f"Quote refresh failed: {exc}", "error", "broker")
                if self.shadow_only_runtime and self.snapshot.shadow_running:
                    self.stop_shadow(f"AUTO SHADOW BLOCKED: quote/data failure: {exc}")
                    self.log(
                        "AUTO SHADOW BLOCKED: fresh exact quotes unavailable; virtual execution stopped",
                        "critical",
                        "shadow_only_boundary",
                        {"error": str(exc), "broker_write_attempted": False},
                    )
                    try:
                        await self.broker.disconnect()
                    except Exception as disconnect_exc:
                        self.log(
                            f"AUTO SHADOW BLOCKED: read-only disconnect failed: {disconnect_exc}",
                            "error",
                            "shadow_only_boundary",
                        )
                    self.snapshot = TradingSnapshot()
                if self.snapshot.strategy_running or self.risk.grant is not None:
                    await self.stop_and_cancel(
                        "Fresh exact live quotes failed; authority revoked and open orders require cancellation"
                    )
                    self.log(
                        "Strategy locked after quote refresh failure; filled positions may remain open",
                        "critical",
                        "risk",
                    )
            finally:
                self._emit()

    def authorize_live(self, grant: LiveGrant) -> None:
        self._require_order_runtime("authorize live trading")
        if not self.config.live_trading_enabled:
            raise RuntimeError("Real-order controls are disabled. Unlock them deliberately in Settings first")
        try:
            grant.validate()
        except ValueError as exc:
            raise RuntimeError(f"Invalid live-session limits: {exc}") from exc
        expected_fingerprint = self.current_strategy_fingerprint(grant)
        if grant.strategy_fingerprint != expected_fingerprint:
            raise RuntimeError("Live grant does not match the exact installed candidate and order route")
        if grant.allowed_symbols != ("TQQQ", "SQQQ"):
            raise RuntimeError("Autonomous pilot authority must bind exactly TQQQ and SQQQ")
        if grant.execution != execution_profile(self.config):
            raise RuntimeError(
                "Live grant route differs from Settings; change the route in Settings and rerun evidence"
            )
        pilot_contract = self.runtime_execution_contract()
        pilot_route = next(
            check
            for check in runtime_parity_assessment(pilot_contract).checks
            if check.key == "pilot_route"
        )
        if not pilot_route.aligned:
            raise RuntimeError(
                "Autonomous live v1 is restricted to regular hours, market orders, GFD, cash T+1 settlement, "
                "and zero-bar modeled latency; other routes remain research/shadow only"
            )
        if not market_session_allowed(
            utc_now(),
            self.config.no_trade_open_minutes,
            self.config.no_trade_close_minutes,
            grant.market_hours,
        ):
            raise RuntimeError(
                "Authorize and start the autonomous pilot only inside the regular-session "
                "entry window; premarket, close-blackout, holidays, and closed sessions stay locked"
            )
        if not self.live_evidence_ready(grant):
            raise RuntimeError(
                "Real-order authority requires a current passing evidence certificate for this exact strategy. "
                "Run the full Evidence Lab on eligible market history; failed or mismatched research stays shadow-only"
            )
        if self._shadow is not None and self._shadow.state.active:
            raise RuntimeError("Stop live shadow mode before granting real-order authority")
        if self.snapshot.account is None or self.snapshot.portfolio is None:
            raise RuntimeError("Connect and refresh the Agentic account first")
        if not self.snapshot.account.agentic_allowed or self.snapshot.account.state.strip().lower() != "active":
            raise RuntimeError("The selected broker account is not an active Agentic account")
        if grant.account_number != self.snapshot.account.account_number:
            raise RuntimeError("Grant account does not match the connected Agentic account")
        if self.snapshot.account.account_type.lower() == "cash" and self.config.settlement_model != "cash_t1":
            raise RuntimeError("Cash-account authority requires the T+1 settlement evidence model")
        if self.snapshot.portfolio.total_value <= 0 or self.snapshot.portfolio.buying_power <= 0:
            raise RuntimeError(
                "Robinhood reports zero account value or buying power; live trading stays locked"
            )
        if self.snapshot.last_reconcile_at is None or (
            utc_now() - self.snapshot.last_reconcile_at
        ).total_seconds() > max(10.0, self.config.reconcile_seconds * 2.0):
            raise RuntimeError("Agentic account positions and orders are not freshly reconciled")
        if self._leveraged_positions():
            raise RuntimeError("Start an autonomous live session only from a flat TQQQ/SQQQ account")
        if self._nonterminal_orders(self.snapshot.orders):
            raise RuntimeError("Start an autonomous live session only with zero nonterminal Agentic orders")
        if self._submission_reconcile_required or self._uncertain_submission_refs:
            raise RuntimeError("A prior placement outcome is unresolved; reconcile it before new authority")
        unresolved = self.store.unresolved_order_intents(self.snapshot.account.account_number)
        if unresolved:
            raise RuntimeError(
                f"{len(unresolved)} durable order intent(s) have unresolved broker outcomes; "
                "new authority remains locked"
            )
        self._validated_execution_quotes(
            self.snapshot.quotes,
            max_age_seconds=grant.max_quote_age_seconds,
            context="Live-session preflight",
        )
        pilot_contract.validate()
        usage = self.store.live_daily_usage(
            grant.account_number,
            grant.starts_at.astimezone(EASTERN).date().isoformat(),
        )
        # The durable ledger currently proves placement invocations, not provider fills.  On
        # each new authority, count every earlier invocation as a possible entry; this can only
        # stop early and cannot understate same-day entry usage after a restart.
        self._prior_entry_upper_bound = int(usage["submitted_orders"])
        self._confirmed_entry_order_ids.clear()
        self.policy = self._policy_for_session(grant.market_hours)
        self.risk.arm(
            grant,
            self.snapshot.portfolio,
            initial_daily_notional=float(usage["daily_notional"]),
            initial_trades=int(usage["submitted_orders"]),
            previous_receipt_digest=str(usage["last_receipt_digest"]),
        )
        self._persist_risk_receipts()
        self.snapshot.session_expires_at = grant.expires_at
        self.log(
            f"Live authority granted until {grant.expires_at.astimezone().strftime('%I:%M %p')}",
            "warning",
            "authority",
            {
                "account_last4": grant.account_number[-4:],
                "expires_at": grant.expires_at.isoformat(),
                "max_order_notional": grant.max_order_notional,
                "max_daily_notional": grant.max_daily_notional,
                "max_total_exposure": grant.max_total_exposure,
                "max_daily_loss": grant.max_daily_loss,
                "max_trades": grant.max_trades,
                "max_orders_per_minute": grant.max_orders_per_minute,
                "max_spread_bps": grant.max_spread_bps,
                "market_hours": grant.market_hours,
                "order_type": grant.order_type,
                "time_in_force": grant.time_in_force,
                "limit_offset_bps": grant.limit_offset_bps,
            },
        )
        self._emit()

    def live_evidence_ready(self, grant: LiveGrant | None = None) -> bool:
        try:
            fingerprint = self.current_strategy_fingerprint(grant)
        except (TypeError, ValueError):
            return False
        requested = None
        if grant is not None:
            requested = {
                "max_order_notional": grant.max_order_notional,
                "max_daily_notional": grant.max_daily_notional,
                "max_total_exposure": grant.max_total_exposure,
                "max_daily_loss": grant.max_daily_loss,
                "max_trades": grant.max_trades,
                "max_orders_per_minute": grant.max_orders_per_minute,
                "max_spread_bps": grant.max_spread_bps,
            }
        return self.store.current_live_evidence(fingerprint, requested_envelope=requested) is not None

    def _revoke_live_automation(self, reason: str) -> None:
        had_authority = self.risk.grant is not None or self.snapshot.strategy_running
        self.snapshot.strategy_running = False
        self.snapshot.session_expires_at = None
        self.risk.disarm(reason)
        self._persist_risk_receipts()
        if had_authority:
            self.log(reason, "critical", "authority")
        self._emit()

    def _live_automation_current(self, *, allow_loss_liquidation: bool = False) -> bool:
        status = self.risk.session_status()
        if status == "LOSS LIMIT" and not allow_loss_liquidation:
            return False
        if status not in ({"LIVE", "LOSS LIMIT"} if allow_loss_liquidation else {"LIVE"}):
            self._revoke_live_automation("Automatic trading stopped because live authority is not active")
            return False
        if not self.config.live_trading_enabled:
            self._revoke_live_automation("Automatic trading stopped because real-order controls are disabled")
            return False
        grant = self.risk.grant
        if grant is None or self.snapshot.account is None:
            self._revoke_live_automation("Automatic trading stopped because account authority is missing")
            return False
        if grant.account_number != self.snapshot.account.account_number:
            self._revoke_live_automation("Automatic trading stopped because the Agentic account changed")
            return False
        if grant.strategy_fingerprint != self.current_strategy_fingerprint(grant):
            self._revoke_live_automation("Automatic trading stopped because candidate identity changed")
            return False
        try:
            pilot_route = next(
                check
                for check in runtime_parity_assessment(self.runtime_execution_contract()).checks
                if check.key == "pilot_route"
            )
        except (StopIteration, TypeError, ValueError):
            pilot_route = None
        if pilot_route is None or not pilot_route.aligned:
            self._revoke_live_automation(
                "Automatic live v1 route moved outside regular-hours market/GFD/cash-T+1/zero-latency"
            )
            return False
        if self._uncertain_submission_refs:
            self._revoke_live_automation(
                "Automatic trading stopped because a broker placement outcome is uncertain"
            )
            return False
        if not self.live_evidence_ready(self.risk.grant):
            self._revoke_live_automation(
                "Automatic trading stopped because the exact evidence certificate is missing or expired"
            )
            return False
        return True

    def config_evidence_ready(self, config: AppConfig) -> bool:
        try:
            fingerprint = self.current_strategy_fingerprint(config=config)
        except (TypeError, ValueError):
            return False
        return self.store.current_live_evidence(fingerprint) is not None

    def live_readiness(self) -> list[dict[str, str]]:
        """Return a non-mutating, user-facing activation checklist."""

        rows: list[dict[str, str]] = []

        def add(gate: str, passed: bool, observed: str, action: str) -> None:
            rows.append(
                {
                    "gate": gate,
                    "status": "PASS" if passed else "BLOCKED",
                    "observed": observed,
                    "action": "None" if passed else action,
                }
            )

        add(
            "Broker capability",
            self.config.broker_connection_enabled,
            "Enabled" if self.config.broker_connection_enabled else "Disabled",
            "Enable broker data in Settings & Permissions",
        )
        add(
            "Real-order capability",
            self.config.live_trading_enabled,
            "Enabled" if self.config.live_trading_enabled else "Disabled",
            "This remains disabled until exact evidence is eligible",
        )
        account_ready = bool(
            self.snapshot.connected
            and self.snapshot.account
            and self.snapshot.account.agentic_allowed
            and self.snapshot.account.state.strip().lower() == "active"
        )
        add(
            "Exact Agentic account",
            account_ready,
            self.snapshot.account.masked if self.snapshot.account else "Disconnected",
            "Reconnect Robinhood; if authorization is revoked, forget stored OAuth credentials first",
        )
        reconcile_fresh = bool(
            self.snapshot.last_reconcile_at
            and (utc_now() - self.snapshot.last_reconcile_at).total_seconds()
            <= max(10.0, self.config.reconcile_seconds * 2.0)
        )
        add(
            "Fresh account truth",
            reconcile_fresh,
            self.snapshot.last_reconcile_at.isoformat()
            if self.snapshot.last_reconcile_at
            else "Never reconciled",
            "Connect and refresh positions/orders",
        )
        flat = not self._leveraged_positions()
        add(
            "Flat leveraged inventory",
            flat,
            "Flat" if flat else ", ".join(position.symbol for position in self._leveraged_positions()),
            "Manually review and flatten existing TQQQ/SQQQ exposure",
        )
        open_orders = self._nonterminal_orders(self.snapshot.orders)
        add(
            "No working Agentic orders",
            not open_orders,
            f"{len(open_orders)} nonterminal",
            "Use STOP + CANCEL and verify every order terminal in Robinhood",
        )
        try:
            unresolved = (
                self.store.unresolved_order_intents(self.snapshot.account.account_number)
                if self.snapshot.account
                else []
            )
        except Exception as exc:
            unresolved = [{"error": str(exc)}]
        add(
            "No ambiguous placements",
            not unresolved and not self._submission_reconcile_required,
            f"{len(unresolved) + len(self._submission_reconcile_required)} unresolved",
            "Reconcile the recorded reference against Robinhood; never retry it blindly",
        )
        try:
            self._validated_execution_quotes(
                self.snapshot.quotes,
                context="Live-readiness check",
            )
            quotes_ready, quote_observed = True, "QQQ/TQQQ/SQQQ exact and fresh"
        except Exception as exc:
            quotes_ready, quote_observed = False, str(exc)
        add(
            "Fresh exact venue quotes",
            quotes_ready,
            quote_observed,
            "Wait for a complete current QQQ/TQQQ/SQQQ provider batch",
        )
        try:
            contract = self.runtime_execution_contract()
            contract_ready, contract_observed = True, contract.fingerprint[:12] + "…"
            route_check = next(
                check
                for check in runtime_parity_assessment(contract).checks
                if check.key == "pilot_route"
            )
            route_ready = route_check.aligned
            route_observed = (
                f"{route_check.replay}; settlement {contract.settlement_model}; "
                f"modeled latency {contract.latency_bars} bars"
            )
        except (StopIteration, TypeError, ValueError) as exc:
            contract = None
            contract_ready, contract_observed = False, str(exc)
            route_ready = False
            route_observed = str(exc)
        add(
            "Autonomous pilot route",
            route_ready,
            route_observed,
            "Select Regular market, Market order, GFD, cash T+1, and zero modeled latency; "
            "then rerun evidence",
        )
        add(
            "Immutable runtime contract",
            contract_ready,
            contract_observed,
            "Align the saved sandbox candidate with runtime Settings",
        )
        if contract_ready and contract is not None:
            parity = runtime_parity_assessment(contract)
            parity_observed = (
                "Certified"
                if parity.certified
                else "Blocked: " + ", ".join(check.key for check in parity.blockers)
            )
        else:
            parity = None
            parity_observed = "Runtime contract is unavailable"
        add(
            "Runtime execution parity",
            bool(parity and parity.certified),
            parity_observed,
            "Resolve every machine-readable replay/shadow/live parity blocker, then rerun evidence",
        )
        try:
            evidence_ready = self.live_evidence_ready()
        except Exception:
            evidence_ready = False
        add(
            "Positive exact evidence",
            evidence_ready,
            "Current certificate" if evidence_ready else "No live-review-eligible certificate",
            "A noncash candidate must pass walk-forward, stressed costs, sealed holdout, and parity gates",
        )
        rows.append(
            {
                "gate": "F-1 / tax suitability",
                "status": "USER ACTION",
                "observed": "Not decidable by the app",
                "action": "Obtain written guidance from the UCLA DSO and qualified immigration counsel",
            }
        )
        return rows

    @property
    def active_execution_profile(self) -> ExecutionProfile:
        return self.risk.grant.execution if self.risk.grant is not None else execution_profile(self.config)

    def start_strategy(self) -> None:
        self._require_order_runtime("start live trading")
        if not self.config.live_trading_enabled:
            raise RuntimeError("Real-order controls are disabled in Settings")
        if not self.live_evidence_ready(self.risk.grant):
            self.risk.disarm()
            self.snapshot.strategy_running = False
            self._emit()
            raise RuntimeError(
                "The evidence certificate for this exact strategy is missing or expired; "
                "real-order authority has been revoked"
            )
        if self._shadow is not None and self._shadow.state.active:
            raise RuntimeError("Stop live shadow mode before starting real-order automation")
        if self.risk.session_status() != "LIVE":
            raise RuntimeError("Authorize a bounded live session first")
        if self.snapshot.last_reconcile_at is None or (
            utc_now() - self.snapshot.last_reconcile_at
        ).total_seconds() > max(10.0, self.config.reconcile_seconds * 2.0):
            raise RuntimeError("Refresh account truth before starting autonomous execution")
        if self._leveraged_positions() or self._nonterminal_orders(self.snapshot.orders):
            self._revoke_live_automation(
                "Autonomous start blocked because the Agentic account is no longer flat and order-free"
            )
            raise RuntimeError("Autonomous start requires flat TQQQ/SQQQ inventory and zero open orders")
        grant = self.risk.grant
        if grant is None:
            raise RuntimeError("Authorize a bounded live session first")
        if not market_session_allowed(
            utc_now(),
            self.config.no_trade_open_minutes,
            self.config.no_trade_close_minutes,
            grant.market_hours,
        ):
            raise RuntimeError("Autonomous start is outside the certified regular-session entry window")
        self._validated_execution_quotes(
            self.snapshot.quotes,
            max_age_seconds=grant.max_quote_age_seconds,
            context="Autonomous-start preflight",
        )
        preflight_timestamp = self.snapshot.quotes["QQQ"].timestamp
        self._reset_signal_pipeline("Live session clean start; warming up on regular-session data")
        # Do not ingest the preflight quote, or an overlapping refresh whose
        # venue observation predates the clean-start action, as post-start data.
        self._last_qqq_timestamp = max(preflight_timestamp, utc_now())
        self.snapshot.strategy_running = True
        self._last_trade_decision_sequence = self._analysis_sequence
        self.log("Automatic strategy started", "warning", "strategy")
        self._emit()

    def pause_live_authority(self, reason: str = "Paused by user") -> None:
        self.snapshot.strategy_running = False
        if not self.risk.pause(reason):
            raise RuntimeError("No active live authority can be paused")
        self._persist_risk_receipts()
        self.log(reason, "warning", "authority")
        self._emit()

    def resume_live_authority(self, reason: str = "Resumed by user") -> None:
        if self._submission_reconcile_required or self._uncertain_submission_refs:
            raise RuntimeError("Cannot resume while an order outcome is unresolved")
        grant = self.risk.grant
        if grant is None or not self.live_evidence_ready(grant):
            raise RuntimeError("Cannot resume without current exact evidence and authority")
        if not self.risk.resume(reason):
            raise RuntimeError("No paused live authority can be resumed")
        self._persist_risk_receipts()
        self.log(reason, "warning", "authority")
        self._emit()

    async def revoke_live_authority(self, reason: str = "Authority revoked by user") -> bool:
        return await self.stop_and_cancel(reason)

    def start_shadow(self) -> None:
        if not self.config.broker_connection_enabled:
            raise RuntimeError("Broker connections are disabled in Settings")
        if not self.snapshot.connected or self.snapshot.account is None:
            raise RuntimeError("Connect Robinhood read access before starting live shadow mode")
        if self.risk.session_status() == "LIVE" or self.snapshot.strategy_running:
            raise RuntimeError("Live shadow and real-order authority are mutually exclusive")
        if self._shadow is not None and self._shadow.state.active:
            return
        shadow_config = self._runtime_candidate_config()
        if self.snapshot.account.account_type.lower() == "cash":
            shadow_config = replace(shadow_config, settlement_model="cash_t1")
        self._shadow = LiveShadowEngine(
            shadow_config,
            bar_minutes=self.config.bar_seconds / 60.0,
        )
        self.log(
            "Live shadow started — virtual TQQQS/SQQQS fills only; no order authority granted",
            "warning",
            "shadow_authority",
            {
                "run_id": self._shadow.state.run_id,
                "broker_calls_allowed": False,
                "strategy_fingerprint": strategy_fingerprint(shadow_config, f"{self.config.bar_seconds}s"),
            },
        )
        self._emit()

    def stop_shadow(
        self,
        reason: str = "Live shadow stopped by user",
        *,
        flatten_virtual: bool = False,
        timestamp: datetime | None = None,
    ) -> None:
        if self._shadow is None:
            return
        if not self._shadow.state.active and not flatten_virtual:
            return
        starting_position = self._shadow.state.position
        state = self._shadow.stop(
            self.snapshot.quotes,
            flatten_at=(timestamp or utc_now()) if flatten_virtual else None,
            flatten_reason="AUTO SHADOW DAILY FLAT at regular-session close",
        )
        if flatten_virtual:
            if starting_position is None:
                self.log(
                    "AUTO SHADOW DAILY FLAT — virtual ledger was already flat at session close",
                    "warning",
                    "shadow_authority",
                    {"run_id": state.run_id, "ending_position": None},
                )
            elif state.position is None:
                self.log(
                    f"AUTO SHADOW DAILY FLAT — virtually sold {starting_position.symbol} at session close",
                    "warning",
                    "shadow_authority",
                    {
                        "run_id": state.run_id,
                        "ending_position": None,
                        "flatten_fill": state.fills[-1].as_dict(),
                    },
                )
            else:
                self.log(
                    f"AUTO SHADOW DAILY FLAT UNRESOLVED — no usable virtual exit quote for "
                    f"{state.position.symbol}",
                    "critical",
                    "shadow_authority",
                    {"run_id": state.run_id, "ending_position": state.position.symbol},
                )
        self.log(
            f"{reason}; virtual equity ${state.equity:,.2f}; P/L ${state.pnl:+,.2f}",
            "warning",
            "shadow_authority",
            {
                "run_id": state.run_id,
                "virtual_equity": state.equity,
                "virtual_pnl": state.pnl,
                "ending_position": state.position.symbol if state.position else None,
                "fills": [fill.as_dict() for fill in state.fills],
                "real_orders_submitted": 0,
            },
        )
        self._emit()

    async def stop_and_cancel(self, reason: str = "STOP + CANCEL pressed") -> bool:
        if self.shadow_only_runtime:
            self.stop_shadow(f"{reason}; cancellation BLOCKED by auto-shadow runtime")
            self.snapshot.strategy_running = False
            self.snapshot.session_expires_at = None
            self.risk.disarm()
            self.log(
                f"BLOCKED: {reason} cannot cancel orders in auto-shadow runtime",
                "critical",
                "shadow_only_boundary",
                {"broker_write_attempted": False, "cancelled": []},
            )
            self._emit()
            return True
        self.stop_shadow(reason)
        self.snapshot.strategy_running = False
        self.snapshot.session_expires_at = None
        self.risk.revoke(reason)
        self._persist_risk_receipts()
        cancel_accepted: list[str] = []
        failures: list[str] = []
        target_ids: set[str] = set()
        if self.snapshot.connected and self.snapshot.account is not None:
            try:
                orders = await self.broker.get_orders(self.snapshot.account.account_number)
                self.snapshot.orders = orders
                try:
                    self._reconcile_submission_tracking(orders, self.snapshot.positions)
                except Exception as exc:
                    failures.append(str(exc))
                for order in orders:
                    if not order_is_terminal(order):
                        target_ids.add(order.order_id)
                        if normalized_order_state(order.state) == "pending_cancelled":
                            continue
                        try:
                            accepted = await self.broker.cancel_order(
                                self.snapshot.account.account_number, order.order_id
                            )
                            (cancel_accepted if accepted else failures).append(order.order_id)
                        except Exception as exc:
                            failures.append(f"{order.order_id}: {exc}")
            except Exception as exc:
                failures.append(str(exc))
        elif self._nonterminal_orders(self.snapshot.orders):
            target_ids.update(order.order_id for order in self._nonterminal_orders(self.snapshot.orders))
            failures.append("Broker is disconnected; open-order state could not be refreshed")

        remaining = set(target_ids)
        if target_ids and self.snapshot.connected and self.snapshot.account is not None:
            for attempt in range(4):
                if attempt:
                    await asyncio.sleep(0.5)
                try:
                    observed = await self.broker.get_orders(self.snapshot.account.account_number)
                except Exception as exc:
                    failures.append(f"terminal verification failed: {exc}")
                    break
                self.snapshot.orders = observed
                observed_by_id = {order.order_id: order for order in observed}
                remaining = {
                    order_id
                    for order_id in target_ids
                    if order_id not in observed_by_id or not order_is_terminal(observed_by_id[order_id])
                }
                for order in observed:
                    reference = self._order_ref_id(order)
                    if reference:
                        self.store.update_intent(
                            reference,
                            order.order_id,
                            normalized_order_state(order.state),
                        )
                    if order_is_terminal(order):
                        for tracked_ref, tracked_order_id in list(
                            self._submission_reconcile_required.items()
                        ):
                            if tracked_order_id == order.order_id or (
                                reference and tracked_ref == reference
                            ):
                                self._submission_reconcile_required.pop(tracked_ref, None)
                                self._uncertain_submission_refs.discard(tracked_ref)
                                self._live_submissions.pop(tracked_ref, None)
                if not remaining:
                    break
        if remaining:
            failures.append(
                "nonterminal/unverified order ids: " + ", ".join(sorted(remaining))
            )
        self._cleanup_unresolved = bool(failures)
        severity = "critical" if self._cleanup_unresolved else "warning"
        self.log(
            f"{reason}; new orders locked; cancel accepted for {len(cancel_accepted)} order(s); "
            f"terminal verification {'FAILED' if self._cleanup_unresolved else 'passed'}",
            severity,
            "kill_switch",
            {
                "cancel_accepted": cancel_accepted,
                "target_order_ids": sorted(target_ids),
                "remaining_order_ids": sorted(remaining),
                "failures": failures,
                "filled_positions_may_remain": bool(self._leveraged_positions()),
            },
        )
        self._emit()
        return not self._cleanup_unresolved

    def _leveraged_positions(self) -> list[Position]:
        return [
            item
            for item in self.snapshot.positions
            if item.symbol.strip().upper() in {"TQQQ", "SQQQ"} and abs(item.quantity) > 1e-12
        ]

    def _exposure(self) -> float:
        total = 0.0
        for position in self._leveraged_positions():
            quote = self.snapshot.quotes.get(position.symbol.strip().upper())
            if quote:
                total += abs(position.quantity * quote.mid)
        return total

    def _has_open_order(self) -> bool:
        return bool(self._nonterminal_orders(self.snapshot.orders))

    def _policy_for_session(self, market_hours: str) -> DecisionPolicy:
        return DecisionPolicy(
            PolicyConfig(
                hard_stop_pct=self.config.hard_stop_pct,
                take_profit_pct=self.config.take_profit_pct,
                max_hold_minutes=self.config.max_hold_minutes,
                no_trade_open_minutes=self.config.no_trade_open_minutes,
                no_trade_close_minutes=self.config.no_trade_close_minutes,
                market_hours=market_hours,
            )
        )

    @staticmethod
    def _limit_price(quote: Quote, side: str, offset_bps: float) -> float:
        if side == "buy":
            raw = Decimal(str(quote.ask)) * (Decimal("1") + Decimal(str(offset_bps)) / 10_000)
            return float(raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING))
        raw = Decimal(str(quote.bid)) * (Decimal("1") - Decimal(str(offset_bps)) / 10_000)
        return float(raw.quantize(Decimal("0.01"), rounding=ROUND_FLOOR))

    def _execution_intent(
        self,
        symbol: str,
        side: str,
        quote: Quote,
        reason: str,
        *,
        notional: float | None = None,
        quantity: float | None = None,
    ) -> OrderIntent:
        grant = self.risk.grant
        if grant is None:
            raise RuntimeError("No active execution profile")
        profile = grant.execution
        common = {
            "ref_id": str(uuid.uuid4()),
            "symbol": symbol,
            "side": side,
            "reason": reason,
            "order_type": profile.order_type,
            "market_hours": profile.market_hours,
            "time_in_force": profile.time_in_force,
        }
        if profile.order_type == "market":
            if side == "buy":
                return OrderIntent(**common, dollar_amount=round(float(notional or 0), 2))
            return OrderIntent(**common, quantity=float(quantity or 0))
        limit_price = self._limit_price(quote, side, profile.limit_offset_bps)
        if side == "buy":
            shares = math.floor(float(notional or 0) / limit_price)
            if shares < 1:
                raise RuntimeError(
                    f"The {profile.market_hours} limit route requires a whole share, but the "
                    f"authorized notional is below one {symbol} share at ${limit_price:.2f}"
                )
        else:
            available = float(quantity or 0)
            shares = math.floor(available + 1e-9)
            if shares < 1 or not math.isclose(available, shares, abs_tol=1e-9):
                raise RuntimeError(
                    "Automatic limit exits require a whole-share position; use the separately reviewed "
                    "regular-hours manual flatten for fractional inventory"
                )
        return OrderIntent(**common, quantity=float(shares), limit_price=limit_price)

    def _inventory_units(self) -> tuple[int, int]:
        symbols = {
            position.symbol.strip().upper()
            for position in self._leveraged_positions()
            if position.quantity > 0
        }
        return int("TQQQ" in symbols), int("SQQQ" in symbols)

    def _trade_decision_due(self) -> bool:
        return decision_due(
            analysis_count=self._analysis_sequence,
            last_decision_count=self._last_trade_decision_sequence,
            decision_stride=self.config.trade_every_bars,
        )

    def _record_pair_decision(
        self,
        action: PairAction,
        trade_at: datetime,
        target_symbol: str | None,
        reason: str,
        state_feasible: bool,
    ) -> None:
        t_units, s_units = self._inventory_units()
        route = self.active_execution_profile
        self.snapshot.pair_action_id = action.action_id
        self.snapshot.pair_action_label = action.label
        self.snapshot.last_trade_decision_at = trade_at
        self.store.receipt(
            "pair_decision",
            f"Pair action {action.label}: {reason}",
            {
                "action_id": action.action_id,
                "action_t": int(action.t),
                "action_s": int(action.s),
                "before_t": t_units,
                "before_s": s_units,
                "action_space_size": len(ALL_PAIR_ACTIONS),
                "state_feasible_action_ids": list(live_feasible_action_ids(t_units, s_units)),
                "target_symbol": target_symbol or "cash",
                "signal_regime": self.snapshot.signal.regime.value,
                "analysis_at": (
                    self.snapshot.last_analysis_at.isoformat()
                    if self.snapshot.last_analysis_at is not None
                    else None
                ),
                "trade_at": trade_at.isoformat(),
                "analysis_sequence": self._analysis_sequence,
                "decision_stride": self.config.trade_every_bars,
                "nominal_analysis_seconds": self.config.bar_seconds,
                "nominal_trade_seconds": self.config.trade_seconds,
                "market_hours": route.market_hours,
                "order_type": route.order_type,
                "time_in_force": route.time_in_force,
                "limit_offset_bps": route.limit_offset_bps,
                "state_feasible": state_feasible,
                "reason": reason,
            },
        )

    async def _evaluate_and_trade(self) -> None:
        self._require_order_runtime("evaluate live orders")
        if self.risk.session_status() == "LOSS LIMIT":
            await self._liquidate_for_loss_limit()
            return
        if not self._live_automation_current():
            return
        if not self._trade_decision_due():
            return
        self._last_trade_decision_sequence = self._analysis_sequence
        trade_at = utc_now()
        if self._submission_reconcile_required:
            self._record_pair_decision(
                PairAction(TradeCommand.HOLD, TradeCommand.HOLD),
                trade_at,
                None,
                "Waiting for post-submission broker reconciliation",
                False,
            )
            return
        if self._has_open_order():
            self._record_pair_decision(
                PairAction(TradeCommand.HOLD, TradeCommand.HOLD),
                trade_at,
                None,
                "Open broker order is still pending",
                False,
            )
            return
        if self._last_submission_at and trade_at - self._last_submission_at < timedelta(seconds=12):
            self._record_pair_decision(
                PairAction(TradeCommand.HOLD, TradeCommand.HOLD),
                trade_at,
                None,
                "Independent 12-second submission cooldown is active",
                False,
            )
            return
        positions = self._leveraged_positions()
        held = positions[0] if positions else None
        if len(positions) > 1:
            self._record_pair_decision(
                pair_action_for_target(1, 1, None),
                trade_at,
                None,
                "Both leveraged funds are held; automatic execution is locked",
                False,
            )
            self.snapshot.strategy_running = False
            self.risk.disarm()
            self.log("Both TQQQ and SQQQ are held; automatic trading locked", "critical", "risk")
            return

        held_symbol = held.symbol.strip().upper() if held else ""
        held_quote = self.snapshot.quotes.get(held_symbol) if held else None
        held_duration_minutes = None
        if held:
            entries = [
                order.created_at
                for order in self.snapshot.orders
                if order.symbol.strip().upper() == held_symbol
                and order.side.strip().lower() == "buy"
                and order.created_at is not None
                and normalized_order_state(order.state) in {"filled", "partially_filled"}
            ]
            if entries:
                held_duration_minutes = held_minutes(max(entries), utc_now())
        policy_position = (
            PolicyPosition(
                held_symbol,
                held.average_price,
                held_quote.mid if held_quote else None,
                held_duration_minutes,
            )
            if held
            else None
        )
        decision = self.policy.decide(self.snapshot.signal, trade_at, policy_position)
        target = decision.target_symbol
        t_units, s_units = self._inventory_units()
        pair_action = pair_action_for_target(t_units, s_units, target)
        self._record_pair_decision(
            pair_action,
            trade_at,
            target,
            decision.reason or "Policy target unchanged",
            True,
        )

        if held is not None:
            quote = held_quote
            if target == held_symbol:
                return
            reason = decision.reason or f"Regime changed from {held_symbol} to {target or 'cash'}"
            if held.sellable_quantity <= 0:
                self.log(f"Cannot exit {held_symbol}: broker reports zero sellable shares", "error", "risk")
                return
            if quote is None:
                self.log(f"Cannot exit {held_symbol}: no current quote is available", "error", "risk")
                return
            try:
                intent = self._execution_intent(
                    held_symbol,
                    "sell",
                    quote,
                    reason,
                    quantity=held.sellable_quantity,
                )
            except RuntimeError as exc:
                self.snapshot.strategy_running = False
                self.risk.disarm()
                self.log(f"Automatic exit route blocked: {exc}", "critical", "risk")
                return
            order = await self._submit(intent, quote)
            if order is not None:
                self.log(
                    f"Automatic exit for {held_symbol} is awaiting broker order and inventory truth",
                    "warning",
                    "live_exit_lifecycle",
                    {
                        "order_id": order.order_id,
                        "ref_id": intent.ref_id,
                        "retry_allowed_only_after_terminal_reconciliation": True,
                    },
                )
            return

        if target is None or self.snapshot.portfolio is None:
            return
        quote = self.snapshot.quotes.get(target)
        if quote is None or self.risk.grant is None:
            return
        contract = self.runtime_execution_contract()
        conservative_entries = self._prior_entry_upper_bound + len(
            self._confirmed_entry_order_ids
        )
        if conservative_entries >= contract.max_entries_per_day:
            self.log(
                "No buy submitted: conservative daily entry cap reached",
                "warning",
                "risk",
                {
                    "conservative_entries": conservative_entries,
                    "max_entries_per_day": contract.max_entries_per_day,
                    "prior_placement_upper_bound": self._prior_entry_upper_bound,
                    "confirmed_in_process": len(self._confirmed_entry_order_ids),
                },
            )
            return
        realized = annualized_volatility(
            tuple(self._recent_returns[target]),
            bar_minutes=self.config.bar_seconds / 60.0,
            market_hours=contract.market_hours,
        )
        sizing = size_entry(
            contract,
            equity=self.snapshot.portfolio.total_value,
            settled_cash=self.snapshot.portfolio.buying_power,
            price=quote.ask,
            realized_volatility=realized,
        )
        remaining_exposure = max(0.0, self.risk.grant.max_total_exposure - self._exposure())
        notional = min(
            sizing.budget,
            self.risk.grant.max_order_notional,
            remaining_exposure,
            self.snapshot.portfolio.buying_power,
        )
        if notional < 1.0:
            reason = sizing.blocked_reason or "less than $1 of certified buying power remains"
            self.log(f"No buy submitted: {reason}", "warning", "risk")
            return
        try:
            intent = self._execution_intent(
                target,
                "buy",
                quote,
                decision.reason,
                notional=notional,
            )
        except RuntimeError as exc:
            self.log(f"No buy submitted: {exc}", "warning", "risk")
            return
        await self._submit(intent, quote)

    async def _liquidate_for_loss_limit(self) -> None:
        """Use the still-bounded grant only to reduce a held leveraged position."""

        trade_at = utc_now()
        if not self._live_automation_current(allow_loss_liquidation=True):
            return
        if self._submission_reconcile_required:
            tracked_ids = {
                order_id for order_id in self._submission_reconcile_required.values() if order_id
            }
            tracked_refs = set(self._submission_reconcile_required)
            tracked_orders = [
                order
                for order in self.snapshot.orders
                if order.order_id in tracked_ids or self._order_ref_id(order) in tracked_refs
            ]
            if tracked_orders and all(
                order.side.strip().lower() == "sell" for order in tracked_orders
            ):
                self._record_pair_decision(
                    PairAction(TradeCommand.HOLD, TradeCommand.HOLD),
                    trade_at,
                    None,
                    "Daily-loss exit is waiting for broker order and inventory reconciliation",
                    False,
                )
                return
            await self.stop_and_cancel(
                "Daily loss limit reached while a prior non-exit submission remained unresolved"
            )
            self.log(
                "Daily-loss exit needs manual verification because a prior non-exit submission "
                "had to be cancelled; filled positions may remain",
                "critical",
                "risk",
            )
            return
        if self._has_open_order():
            await self.stop_and_cancel(
                "Daily loss limit reached while an untracked Agentic order remained open"
            )
            self.log(
                "Daily-loss exit needs manual verification because an external or untracked order "
                "had to be cancelled; filled positions may remain",
                "critical",
                "risk",
            )
            return
        positions = self._leveraged_positions()
        if not positions:
            self._revoke_live_automation(
                "Daily loss limit reached while flat; session authority revoked"
            )
            return
        if len(positions) != 1:
            await self.stop_and_cancel(
                "Daily loss limit reached with conflicting leveraged inventory"
            )
            self.log(
                "Daily-loss liquidation is blocked because both leveraged funds are held; "
                "manual flatten is required",
                "critical",
                "risk",
            )
            return
        held = positions[0]
        symbol = held.symbol.strip().upper()
        quote = self.snapshot.quotes.get(symbol)
        if held.sellable_quantity <= 0 or quote is None:
            self._revoke_live_automation(
                "Daily-loss liquidation could not prove sellable inventory and a fresh exit quote"
            )
            self.log(
                f"Daily-loss exit for {symbol} was not submitted; check Robinhood and flatten manually",
                "critical",
                "risk",
            )
            return
        intent = self._execution_intent(
            symbol,
            "sell",
            quote,
            "Daily loss limit reached; liquidation-only exit",
            quantity=held.sellable_quantity,
        )
        self._record_pair_decision(
            pair_action_for_target(*self._inventory_units(), None),
            trade_at,
            None,
            intent.reason,
            True,
        )
        order = await self._submit(intent, quote, liquidation_only=True)
        if order is not None:
            self.log(
                f"Daily-loss exit for {symbol} is awaiting broker order and inventory truth",
                "critical",
                "live_exit_lifecycle",
                {
                    "order_id": order.order_id,
                    "ref_id": intent.ref_id,
                    "retry_allowed_only_after_terminal_reconciliation": True,
                },
            )
            return
        if (
            order is None
            and self.risk.grant is not None
            and not self._submission_reconcile_required
        ):
            self._revoke_live_automation(
                "Daily-loss exit was not authorized or submitted; manual flatten is required"
            )
            self.log(
                f"Daily-loss exit for {symbol} was not submitted; check Robinhood and flatten manually",
                "critical",
                "risk",
            )

    async def _submit(
        self,
        intent: OrderIntent,
        quote: Quote | None,
        *,
        liquidation_only: bool = False,
    ) -> BrokerOrder | None:
        self._require_order_runtime("submit an order")
        if self.snapshot.account is None or self.snapshot.portfolio is None or quote is None:
            return None
        if liquidation_only and intent.side != "sell":
            raise RuntimeError("Liquidation-only authority cannot create exposure")
        if not self._live_automation_current(allow_loss_liquidation=liquidation_only):
            return None
        exit_position = None
        if intent.side == "sell":
            exit_position = next(
                (
                    position
                    for position in self._leveraged_positions()
                    if position.symbol.strip().upper() == intent.symbol
                ),
                None,
            )
            if exit_position is None:
                self.log(
                    f"Order blocked: no freshly reconciled {intent.symbol} inventory backs the sell",
                    "critical",
                    "risk",
                    intent.as_dict(),
                )
                return None
        decision = self.risk.authorize(
            intent,
            quote,
            self.snapshot.portfolio,
            self._exposure(),
            account_number=self.snapshot.account.account_number,
            strategy_fingerprint=self.current_strategy_fingerprint(self.risk.grant),
            reconciled_position_quantity=(
                exit_position.quantity if exit_position is not None else None
            ),
            reconciled_sellable_quantity=(
                exit_position.sellable_quantity if exit_position is not None else None
            ),
        )
        self._persist_risk_receipts()
        if not decision.allowed:
            self.log(f"Order blocked: {decision.reason}", "warning", "risk", intent.as_dict())
            return None
        if intent.market_hours == "all_day_hours":
            try:
                tradability = await self.broker.get_tradability(
                    self.snapshot.account.account_number,
                    [intent.symbol],
                )
                eligibility = tradability.get(intent.symbol)
            except Exception as exc:
                self.risk.release_authorization(
                    intent.ref_id, "Tradability check failed before placement"
                )
                self._persist_risk_receipts()
                self.snapshot.strategy_running = False
                self.risk.disarm("24 Hour Market eligibility check failed")
                self._persist_risk_receipts()
                self.log(f"24 Hour Market eligibility check failed: {exc}", "error", "risk")
                return None
            if eligibility is None or not eligibility.tradeable or not eligibility.all_day_tradeable:
                self.risk.release_authorization(
                    intent.ref_id, "Symbol was not eligible before placement"
                )
                self._persist_risk_receipts()
                self.snapshot.strategy_running = False
                self.risk.disarm("24 Hour Market symbol was ineligible")
                self._persist_risk_receipts()
                self.log(
                    f"Order blocked: {intent.symbol} is not currently eligible for the 24 Hour Market",
                    "warning",
                    "risk",
                    intent.as_dict(),
                )
                return None
            if not self._live_automation_current(allow_loss_liquidation=liquidation_only):
                return None
        self.store.record_intent(intent)
        try:
            review = await self.broker.review_order(self.snapshot.account.account_number, intent)
        except Exception as exc:
            self.risk.release_authorization(intent.ref_id, "Broker review failed before placement")
            self._persist_risk_receipts()
            self.store.update_intent(intent.ref_id, None, "review_failed")
            self._revoke_live_automation("Broker review failed; automatic trading was locked")
            self.log(f"Robinhood review failed before placement: {exc}", "critical", "order_review")
            return None
        if review.market_data_disclosure:
            self.event.emit("market", review.market_data_disclosure)
        self.store.receipt(
            "order_review",
            f"Reviewed {intent.side} {intent.symbol}",
            {
                "intent": intent.as_dict(),
                "market_data_disclosure": review.market_data_disclosure,
                "checks": review.checks,
            },
        )
        if review.checks:
            self.risk.release_authorization(intent.ref_id, "Broker review blocked placement")
            self._persist_risk_receipts()
            self.store.update_intent(intent.ref_id, None, "blocked_by_review")
            self.snapshot.strategy_running = False
            self.risk.disarm("Robinhood review blocked placement")
            self._persist_risk_receipts()
            self.log(
                f"Robinhood review alert blocked {intent.side} {intent.symbol}: {review.checks}",
                "critical",
                "order_review",
            )
            return None
        if not self._live_automation_current(allow_loss_liquidation=liquidation_only):
            self.store.update_intent(intent.ref_id, None, "blocked_evidence_revoked")
            return None
        authorized_notional = float(self.risk.authorized_notionals.get(intent.ref_id, 0.0))
        grant = self.risk.grant
        if grant is None:
            self.store.update_intent(intent.ref_id, None, "blocked_authority_revoked")
            return None
        self.store.mark_intent_submitting(
            intent.ref_id,
            account_number=self.snapshot.account.account_number,
            authority_id=grant.authority_id,
            strategy_fingerprint=grant.strategy_fingerprint,
            authorized_notional=authorized_notional,
        )
        # Count the irreversible placement invocation before crossing the broker boundary.
        # Any timeout/transport loss is conservatively treated as possibly accepted and is
        # never retried with a new reference.
        self.risk.record_submission(intent)
        self._persist_risk_receipts()
        self._submission_reconcile_required[intent.ref_id] = None
        self._uncertain_submission_refs.add(intent.ref_id)
        submitted_at = utc_now()
        self._last_submission_at = submitted_at
        starting_quantity = sum(
            max(0.0, float(position.quantity))
            for position in self.snapshot.positions
            if position.symbol.strip().upper() == intent.symbol
        )
        try:
            order = await self.broker.place_order(self.snapshot.account.account_number, intent)
        except BaseException as exc:
            self.store.update_intent(intent.ref_id, None, "submission_uncertain")
            self.snapshot.strategy_running = False
            self.risk.revoke("Broker placement response was ambiguous")
            self._persist_risk_receipts()
            self._cleanup_unresolved = True
            self.log(
                f"PLACEMENT OUTCOME UNKNOWN for {intent.side.upper()} {intent.symbol}; "
                "automation locked, no retry allowed: " + str(exc),
                "critical",
                "order",
                {"ref_id": intent.ref_id, "intent": intent.as_dict(), "retry_allowed": False},
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return None
        if not order.order_id:
            self.store.update_intent(intent.ref_id, None, "submission_uncertain")
            self.snapshot.strategy_running = False
            self.risk.revoke("Broker placement returned no order id")
            self._persist_risk_receipts()
            self._cleanup_unresolved = True
            self.log(
                "PLACEMENT OUTCOME UNKNOWN: Robinhood returned no order id; no retry allowed",
                "critical",
                "order",
                {"ref_id": intent.ref_id, "intent": intent.as_dict(), "retry_allowed": False},
            )
            return None
        self._submission_reconcile_required[intent.ref_id] = order.order_id
        self._uncertain_submission_refs.discard(intent.ref_id)
        self._live_submissions[intent.ref_id] = LiveSubmissionReconciliation(
            ref_id=intent.ref_id,
            order_id=order.order_id,
            symbol=intent.symbol,
            side=intent.side,
            starting_quantity=starting_quantity,
            expected_quantity=(float(intent.quantity) if intent.quantity is not None else None),
            authorized_notional=authorized_notional,
            submitted_at=submitted_at,
            reference_price=quote.ask if intent.side == "buy" else quote.bid,
        )
        self.snapshot.orders = [
            order,
            *[item for item in self.snapshot.orders if item.order_id != order.order_id],
        ]
        self.store.update_intent(intent.ref_id, order.order_id, order.state)
        self.log(
            f"Submitted {intent.side.upper()} {intent.symbol}; broker state {order.state}",
            "warning",
            "order",
            {"order_id": order.order_id, "ref_id": intent.ref_id, "intent": intent.as_dict()},
        )
        return order

    async def review_flatten(self, symbol: str) -> tuple[OrderIntent, OrderReview]:
        self._require_order_runtime("review a flatten order")
        if not self.config.live_trading_enabled:
            raise RuntimeError("Real-order controls are disabled in Settings")
        if self.snapshot.account is None:
            raise RuntimeError("Robinhood is not connected")
        if self.risk.grant is not None or self.snapshot.strategy_running:
            self._revoke_live_automation("Manual flatten initiated; autonomous authority revoked")
        await self.reconcile()
        await self.refresh_quotes(evaluate=False)
        if self.snapshot.last_reconcile_at is None or (
            utc_now() - self.snapshot.last_reconcile_at
        ).total_seconds() > max(10.0, self.config.reconcile_seconds * 2.0):
            raise RuntimeError("Manual flatten requires fresh broker position and order truth")
        normalized_symbol = symbol.strip().upper()
        position = next(
            (
                item
                for item in self.snapshot.positions
                if item.symbol.strip().upper() == normalized_symbol
            ),
            None,
        )
        if position is None or position.sellable_quantity <= 0:
            raise RuntimeError(f"No sellable {normalized_symbol} position")
        if any(
            order.symbol.strip().upper() == normalized_symbol
            for order in self._nonterminal_orders(self.snapshot.orders)
        ):
            raise RuntimeError(f"A nonterminal {normalized_symbol} order already exists")
        quote = self.snapshot.quotes.get(normalized_symbol)
        if quote is None:
            raise RuntimeError(f"No current {normalized_symbol} quote is available")
        quote.validate()
        if quote.age_seconds() > self.config.default_max_quote_age_seconds:
            raise RuntimeError(f"The {normalized_symbol} quote is stale")
        intent = OrderIntent(
            ref_id=str(uuid.uuid4()),
            symbol=normalized_symbol,
            side="sell",
            quantity=position.sellable_quantity,
            reason="Manual flatten confirmed in desktop app",
        )
        self.store.record_intent(intent)
        try:
            review = await self.broker.review_order(self.snapshot.account.account_number, intent)
        except Exception:
            self.store.update_intent(intent.ref_id, None, "review_failed")
            raise
        if review.market_data_disclosure:
            self.event.emit("market", review.market_data_disclosure)
        if review.checks:
            self.store.update_intent(intent.ref_id, None, "blocked_by_review")
            raise RuntimeError(f"Robinhood review alert: {review.checks}")
        return intent, review

    async def place_reviewed_flatten(self, intent: OrderIntent, review: OrderReview) -> BrokerOrder:
        self._require_order_runtime("place a flatten order")
        if not self.config.live_trading_enabled:
            raise RuntimeError("Real-order controls are disabled in Settings")
        if self.snapshot.account is None:
            raise RuntimeError("Robinhood is not connected")
        if intent.ref_id != review.intent.ref_id or intent.side != "sell":
            raise RuntimeError("Manual flatten preview no longer matches the order")
        if (utc_now() - intent.created_at).total_seconds() > 30.0:
            self.store.update_intent(intent.ref_id, None, "review_expired")
            raise RuntimeError("Manual flatten review expired; request a fresh review")
        await self.reconcile()
        position = next(
            (
                item
                for item in self.snapshot.positions
                if item.symbol.strip().upper() == intent.symbol
            ),
            None,
        )
        if position is None or position.sellable_quantity + 1e-9 < float(intent.quantity or 0.0):
            self.store.update_intent(intent.ref_id, None, "position_changed")
            raise RuntimeError("Sellable position changed after review; request a fresh flatten")
        if any(
            order.symbol.strip().upper() == intent.symbol
            for order in self._nonterminal_orders(self.snapshot.orders)
        ):
            self.store.update_intent(intent.ref_id, None, "open_order_detected")
            raise RuntimeError("A nonterminal order appeared after review; flatten was not placed")
        quote = self.snapshot.quotes.get(intent.symbol)
        if quote is None or quote.age_seconds() > self.config.default_max_quote_age_seconds:
            self.store.update_intent(intent.ref_id, None, "quote_stale")
            raise RuntimeError("A fresh quote is required immediately before manual flatten placement")
        manual_authority_id = f"manual-flatten-{uuid.uuid4()}"
        self.store.mark_intent_submitting(
            intent.ref_id,
            account_number=self.snapshot.account.account_number,
            authority_id=manual_authority_id,
            strategy_fingerprint=hashlib.sha256(b"manual-flatten-v1").hexdigest(),
            authorized_notional=float(intent.quantity or 0.0) * quote.mid,
        )
        self._submission_reconcile_required[intent.ref_id] = None
        self._uncertain_submission_refs.add(intent.ref_id)
        submitted_at = utc_now()
        starting_quantity = float(position.quantity)
        try:
            order = await self.broker.place_order(self.snapshot.account.account_number, intent)
        except BaseException as exc:
            self.store.update_intent(intent.ref_id, None, "submission_uncertain")
            self._cleanup_unresolved = True
            self.log(
                f"MANUAL FLATTEN OUTCOME UNKNOWN for {intent.symbol}; check Robinhood and do not retry: {exc}",
                "critical",
                "manual_flatten",
                {"ref_id": intent.ref_id, "retry_allowed": False},
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError("Manual flatten outcome is unknown; check Robinhood before any retry") from exc
        if not order.order_id:
            self.store.update_intent(intent.ref_id, None, "submission_uncertain")
            self._cleanup_unresolved = True
            raise RuntimeError("Manual flatten outcome is unknown because no broker order id was returned")
        self._submission_reconcile_required[intent.ref_id] = order.order_id
        self._uncertain_submission_refs.discard(intent.ref_id)
        self._live_submissions[intent.ref_id] = LiveSubmissionReconciliation(
            ref_id=intent.ref_id,
            order_id=order.order_id,
            symbol=intent.symbol,
            side=intent.side,
            starting_quantity=starting_quantity,
            expected_quantity=float(intent.quantity or 0.0),
            authorized_notional=float(intent.quantity or 0.0) * quote.mid,
            submitted_at=submitted_at,
            reference_price=quote.bid,
        )
        self.snapshot.orders = [
            order,
            *[item for item in self.snapshot.orders if item.order_id != order.order_id],
        ]
        self.store.update_intent(intent.ref_id, order.order_id, order.state)
        self.log(
            f"Manual flatten submitted for {intent.quantity or 0:g} {intent.symbol}; {order.state}",
            "critical",
            "manual_flatten",
            {"order_id": order.order_id, "ref_id": intent.ref_id},
        )
        return order

    async def forget_broker_credentials(self) -> None:
        if self.snapshot.connected:
            await self.disconnect()
        self.broker.clear_credentials()
        self.log(
            "Stored broker OAuth credentials were removed; reconnect to restore access",
            "warning",
            "credentials",
        )

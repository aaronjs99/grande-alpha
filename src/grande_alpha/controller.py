from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QObject, Signal

from grande_alpha.broker.base import Broker, BrokerError
from grande_alpha.config import AppConfig
from grande_alpha.evidence import strategy_fingerprint
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
from grande_alpha.policy import DecisionPolicy, PolicyConfig, PolicyPosition
from grande_alpha.risk import RiskEngine
from grande_alpha.sandbox import load_sandbox_config
from grande_alpha.shadow import LiveShadowEngine
from grande_alpha.storage import AuditStore
from grande_alpha.strategy import BarBuilder, MomentumStrategy, StrategyConfig


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
    shadow_running: bool = False
    shadow_equity: float = 0.0
    shadow_pnl: float = 0.0
    shadow_position: str | None = None
    shadow_fills: int = 0


class TradingController(QObject):
    snapshot_changed = Signal(object)
    event = Signal(str, str)
    connection_busy = Signal(bool)

    def __init__(self, broker: Broker, config: AppConfig, store: AuditStore) -> None:
        super().__init__()
        self.broker = broker
        self.config = config
        self.store = store
        self.risk = RiskEngine(config.no_trade_open_minutes, config.no_trade_close_minutes)
        self.strategy = MomentumStrategy(
            StrategyConfig(
                warmup_bars=config.warmup_bars,
                fast_ema=config.fast_ema,
                slow_ema=config.slow_ema,
                trend_threshold_bps=config.trend_threshold_bps,
                momentum_bars=config.momentum_bars,
            )
        )
        self.bar_builder = BarBuilder("QQQ", config.bar_seconds)
        self.policy = DecisionPolicy(
            PolicyConfig(
                hard_stop_pct=config.hard_stop_pct,
                take_profit_pct=config.take_profit_pct,
                max_hold_minutes=config.max_hold_minutes,
                no_trade_open_minutes=config.no_trade_open_minutes,
                no_trade_close_minutes=config.no_trade_close_minutes,
            )
        )
        self.snapshot = TradingSnapshot()
        self._tick_lock = asyncio.Lock()
        self._last_qqq_timestamp: datetime | None = None
        self._last_submission_at: datetime | None = None
        self._shadow: LiveShadowEngine | None = None

    def _emit(self) -> None:
        self.snapshot.live_status = self.risk.session_status()
        self.snapshot.drawdown = self.risk.drawdown
        self.snapshot.trades_today = self.risk.trades_today
        self.snapshot.strategy_running = (
            self.snapshot.strategy_running and self.snapshot.live_status == "LIVE"
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
                account for account in accounts if account.agentic_allowed and account.state == "active"
            ]
            if not candidates:
                raise BrokerError("No active Robinhood account is enabled for this agent")
            candidates.sort(key=lambda item: (item.nickname.lower() != "agentic", item.account_number))
            account = candidates[0]
            self.snapshot.account = account
            self.snapshot.connected = True
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
        await self.stop_and_cancel("Disconnected by user")
        await self.broker.disconnect()
        self.snapshot = TradingSnapshot()
        self._emit()

    async def refresh(self, evaluate: bool = True) -> None:
        if not self.snapshot.connected or self.snapshot.account is None:
            return
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            account_number = self.snapshot.account.account_number
            try:
                portfolio, quotes, positions, orders = await asyncio.gather(
                    self.broker.get_portfolio(account_number),
                    self.broker.get_quotes(["QQQ", "TQQQ", "SQQQ"]),
                    self.broker.get_positions(account_number),
                    self.broker.get_orders(account_number),
                )
                self.snapshot.portfolio = portfolio
                self.snapshot.quotes = quotes
                self.snapshot.positions = positions
                self.snapshot.orders = orders
                self.snapshot.last_refresh = utc_now()
                self.risk.update_portfolio(portfolio)
                for quote in quotes.values():
                    self.store.record_quote(quote)
                qqq = quotes.get("QQQ")
                if qqq and (self._last_qqq_timestamp is None or qqq.timestamp > self._last_qqq_timestamp):
                    self._last_qqq_timestamp = qqq.timestamp
                    bar = self.bar_builder.update(qqq)
                    if bar is not None:
                        self.store.record_bar(bar)
                        signal = self.strategy.on_bar(bar)
                        self.snapshot.signal = signal
                        self.store.record_signal(signal)
                        self.log(f"Signal: {signal.regime.value} — {signal.reason}", category="signal")
                        if self._shadow is not None and self._shadow.state.active:
                            fills = self._shadow.on_bar(bar.start, signal, quotes)
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
                self.log(f"Refresh failed: {exc}", "error", "broker")
                if self.snapshot.strategy_running:
                    self.snapshot.strategy_running = False
                    self.risk.disarm()
                    self.log("Strategy locked after broker refresh failure", "critical", "risk")
            finally:
                self._emit()

    def authorize_live(self, grant: LiveGrant) -> None:
        if not self.config.live_trading_enabled:
            raise RuntimeError("Real-order controls are disabled. Unlock them deliberately in Settings first")
        if not self.live_evidence_ready():
            raise RuntimeError(
                "Real-order authority requires a current passing evidence certificate for this exact strategy. "
                "Run the full Evidence Lab on eligible market history; failed or mismatched research stays shadow-only"
            )
        if self._shadow is not None and self._shadow.state.active:
            raise RuntimeError("Stop live shadow mode before granting real-order authority")
        if self.snapshot.account is None or self.snapshot.portfolio is None:
            raise RuntimeError("Connect and refresh the Agentic account first")
        if grant.account_number != self.snapshot.account.account_number:
            raise RuntimeError("Grant account does not match the connected Agentic account")
        if self.snapshot.portfolio.total_value <= 0 or self.snapshot.portfolio.buying_power <= 0:
            raise RuntimeError(
                "Robinhood reports zero account value or buying power; live trading stays locked"
            )
        self.risk.arm(grant, self.snapshot.portfolio)
        self.snapshot.session_expires_at = grant.expires_at
        self.log(
            f"Live authority granted until {grant.expires_at.astimezone().strftime('%I:%M %p')}",
            "warning",
            "authority",
            {
                "account_last4": grant.account_number[-4:],
                "expires_at": grant.expires_at.isoformat(),
                "max_order_notional": grant.max_order_notional,
                "max_total_exposure": grant.max_total_exposure,
                "max_daily_loss": grant.max_daily_loss,
                "max_trades": grant.max_trades,
                "max_orders_per_minute": grant.max_orders_per_minute,
                "max_spread_bps": grant.max_spread_bps,
            },
        )
        self._emit()

    def live_evidence_ready(self) -> bool:
        fingerprint = strategy_fingerprint(self.config)
        return self.store.current_live_evidence(fingerprint) is not None

    def start_strategy(self) -> None:
        if not self.config.live_trading_enabled:
            raise RuntimeError("Real-order controls are disabled in Settings")
        if not self.live_evidence_ready():
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
        self.snapshot.strategy_running = True
        self.log("Automatic strategy started", "warning", "strategy")
        self._emit()

    def start_shadow(self) -> None:
        if not self.config.broker_connection_enabled:
            raise RuntimeError("Broker connections are disabled in Settings")
        if not self.snapshot.connected or self.snapshot.account is None:
            raise RuntimeError("Connect Robinhood read access before starting live shadow mode")
        if self.risk.session_status() == "LIVE" or self.snapshot.strategy_running:
            raise RuntimeError("Live shadow and real-order authority are mutually exclusive")
        if self._shadow is not None and self._shadow.state.active:
            return
        self._shadow = LiveShadowEngine(load_sandbox_config())
        self.log(
            "Live shadow started — virtual TQQQS/SQQQS fills only; no order authority granted",
            "warning",
            "shadow_authority",
            {"run_id": self._shadow.state.run_id, "broker_calls_allowed": False},
        )
        self._emit()

    def stop_shadow(self, reason: str = "Live shadow stopped by user") -> None:
        if self._shadow is None or not self._shadow.state.active:
            return
        state = self._shadow.stop(self.snapshot.quotes)
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

    async def stop_and_cancel(self, reason: str = "STOP + CANCEL pressed") -> None:
        self.stop_shadow(reason)
        self.snapshot.strategy_running = False
        self.risk.disarm()
        cancelled: list[str] = []
        failures: list[str] = []
        if self.snapshot.connected and self.snapshot.account is not None:
            try:
                orders = await self.broker.get_orders(self.snapshot.account.account_number)
                for order in orders:
                    if order.state in {"new", "queued", "confirmed", "unconfirmed", "partially_filled"}:
                        try:
                            accepted = await self.broker.cancel_order(
                                self.snapshot.account.account_number, order.order_id
                            )
                            (cancelled if accepted else failures).append(order.order_id)
                        except Exception as exc:
                            failures.append(f"{order.order_id}: {exc}")
            except Exception as exc:
                failures.append(str(exc))
        severity = "critical" if failures else "warning"
        self.log(
            f"{reason}; new orders locked; cancel accepted for {len(cancelled)} order(s)",
            severity,
            "kill_switch",
            {"cancelled": cancelled, "failures": failures},
        )
        self._emit()

    def _leveraged_positions(self) -> list[Position]:
        return [item for item in self.snapshot.positions if item.symbol in {"TQQQ", "SQQQ"}]

    def _exposure(self) -> float:
        total = 0.0
        for position in self._leveraged_positions():
            quote = self.snapshot.quotes.get(position.symbol)
            if quote:
                total += abs(position.quantity * quote.mid)
        return total

    def _has_open_order(self) -> bool:
        return any(
            order.state in {"new", "queued", "confirmed", "unconfirmed", "partially_filled"}
            for order in self.snapshot.orders
        )

    async def _evaluate_and_trade(self) -> None:
        if self.risk.session_status() != "LIVE":
            self.snapshot.strategy_running = False
            return
        if self._has_open_order():
            return
        if self._last_submission_at and utc_now() - self._last_submission_at < timedelta(seconds=12):
            return
        positions = self._leveraged_positions()
        held = positions[0] if positions else None
        if len(positions) > 1:
            self.snapshot.strategy_running = False
            self.risk.disarm()
            self.log("Both TQQQ and SQQQ are held; automatic trading locked", "critical", "risk")
            return

        held_quote = self.snapshot.quotes.get(held.symbol) if held else None
        held_minutes = None
        if held:
            entries = [
                order.created_at
                for order in self.snapshot.orders
                if order.symbol == held.symbol
                and order.side == "buy"
                and order.created_at is not None
                and order.state in {"filled", "partially_filled"}
            ]
            if entries:
                held_minutes = max(0, int((utc_now() - max(entries)).total_seconds() / 60))
        policy_position = (
            PolicyPosition(
                held.symbol,
                held.average_price,
                held_quote.mid if held_quote else None,
                held_minutes,
            )
            if held
            else None
        )
        decision = self.policy.decide(self.snapshot.signal, utc_now(), policy_position)
        target = decision.target_symbol

        if held is not None:
            quote = held_quote
            if target == held.symbol:
                return
            reason = decision.reason or f"Regime changed from {held.symbol} to {target or 'cash'}"
            if held.sellable_quantity <= 0:
                self.log(f"Cannot exit {held.symbol}: broker reports zero sellable shares", "error", "risk")
                return
            intent = OrderIntent(
                ref_id=str(uuid.uuid4()),
                symbol=held.symbol,
                side="sell",
                quantity=held.sellable_quantity,
                reason=reason,
            )
            await self._submit(intent, quote)
            return

        if target is None or self.snapshot.portfolio is None:
            return
        quote = self.snapshot.quotes.get(target)
        if quote is None or self.risk.grant is None:
            return
        remaining_exposure = max(0.0, self.risk.grant.max_total_exposure - self._exposure())
        notional = min(
            self.risk.grant.max_order_notional,
            remaining_exposure,
            self.snapshot.portfolio.buying_power * 0.95,
        )
        if notional < 1.0:
            self.log("No buy submitted: less than $1 of authorized buying power remains", "warning", "risk")
            return
        intent = OrderIntent(
            ref_id=str(uuid.uuid4()),
            symbol=target,
            side="buy",
            dollar_amount=round(notional, 2),
            reason=decision.reason,
        )
        await self._submit(intent, quote)

    async def _submit(self, intent: OrderIntent, quote: Quote | None) -> BrokerOrder | None:
        if self.snapshot.account is None or self.snapshot.portfolio is None or quote is None:
            return None
        decision = self.risk.authorize(
            intent,
            quote,
            self.snapshot.portfolio,
            self._exposure(),
        )
        if not decision.allowed:
            self.log(f"Order blocked: {decision.reason}", "warning", "risk", intent.as_dict())
            return None
        self.store.record_intent(intent)
        review = await self.broker.review_order(self.snapshot.account.account_number, intent)
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
            self.store.update_intent(intent.ref_id, None, "blocked_by_review")
            self.snapshot.strategy_running = False
            self.risk.disarm()
            self.log(
                f"Robinhood review alert blocked {intent.side} {intent.symbol}: {review.checks}",
                "critical",
                "order_review",
            )
            return None
        order = await self.broker.place_order(self.snapshot.account.account_number, intent)
        self.risk.record_submission(intent)
        self._last_submission_at = utc_now()
        self.store.update_intent(intent.ref_id, order.order_id, order.state)
        self.log(
            f"Submitted {intent.side.upper()} {intent.symbol}; broker state {order.state}",
            "warning",
            "order",
            {"order_id": order.order_id, "ref_id": intent.ref_id, "intent": intent.as_dict()},
        )
        return order

    async def review_flatten(self, symbol: str) -> tuple[OrderIntent, OrderReview]:
        if not self.config.live_trading_enabled:
            raise RuntimeError("Real-order controls are disabled in Settings")
        if self.snapshot.account is None:
            raise RuntimeError("Robinhood is not connected")
        position = next((item for item in self.snapshot.positions if item.symbol == symbol), None)
        if position is None or position.sellable_quantity <= 0:
            raise RuntimeError(f"No sellable {symbol} position")
        intent = OrderIntent(
            ref_id=str(uuid.uuid4()),
            symbol=symbol,
            side="sell",
            quantity=position.sellable_quantity,
            reason="Manual flatten confirmed in desktop app",
        )
        self.store.record_intent(intent)
        review = await self.broker.review_order(self.snapshot.account.account_number, intent)
        if review.market_data_disclosure:
            self.event.emit("market", review.market_data_disclosure)
        if review.checks:
            self.store.update_intent(intent.ref_id, None, "blocked_by_review")
            raise RuntimeError(f"Robinhood review alert: {review.checks}")
        return intent, review

    async def place_reviewed_flatten(self, intent: OrderIntent, review: OrderReview) -> BrokerOrder:
        if not self.config.live_trading_enabled:
            raise RuntimeError("Real-order controls are disabled in Settings")
        if self.snapshot.account is None:
            raise RuntimeError("Robinhood is not connected")
        if intent.ref_id != review.intent.ref_id or intent.side != "sell":
            raise RuntimeError("Manual flatten preview no longer matches the order")
        order = await self.broker.place_order(self.snapshot.account.account_number, intent)
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

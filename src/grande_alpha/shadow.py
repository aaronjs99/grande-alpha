from __future__ import annotations

import hashlib
import json
import math
import random
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from grande_alpha.candidate_execution import (
    annualized_volatility,
    contract_from_config,
    daily_loss_reached,
    decision_due,
    effective_spread_bps,
    entry_block_reason,
    execution_price,
    fillable_quantity,
    held_minutes,
    next_consecutive_losses,
    size_entry,
)
from grande_alpha.models import Quote, Signal
from grande_alpha.policy import DecisionPolicy, PolicyConfig, PolicyPosition, session_key
from grande_alpha.sandbox import SandboxConfig

ALIASES = {"TQQQ": "TQQQS", "SQQQ": "SQQQS"}
UNDERLYING = {value: key for key, value in ALIASES.items()}
SHADOW_CHECKPOINT_SCHEMA_VERSION = 1


def _aware_datetime(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a nonnegative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a nonnegative integer") from exc
    if result < 0 or result != value:
        raise ValueError(f"{field} must be a nonnegative integer")
    return result


def _tuple_tree(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tuple_tree(item) for item in value)
    return value


def _list_tree(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return [_list_tree(item) for item in value]
    return value


def shadow_checkpoint_digest(checkpoint: dict[str, Any]) -> str:
    """Return the canonical digest for a checkpoint, excluding its digest field."""

    unsigned = {key: value for key, value in checkpoint.items() if key != "digest"}
    try:
        encoded = json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Shadow checkpoint must be canonical JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


def validate_shadow_checkpoint(checkpoint: object) -> dict[str, Any]:
    """Validate structural integrity and return the original checkpoint mapping."""

    if not isinstance(checkpoint, dict):
        raise ValueError("Shadow checkpoint must be an object")
    if checkpoint.get("schema_version") != SHADOW_CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("Unsupported shadow checkpoint schema version")
    for field_name in (
        "run_id",
        "session_key",
        "account_fingerprint",
        "strategy_fingerprint",
        "contract_fingerprint",
        "event",
        "digest",
    ):
        value = checkpoint.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Shadow checkpoint {field_name} must be nonempty")
    for field_name in ("account_fingerprint", "strategy_fingerprint", "contract_fingerprint", "digest"):
        value = str(checkpoint[field_name])
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"Shadow checkpoint {field_name} must be a lowercase SHA-256 digest")
    sequence = _nonnegative_int(checkpoint.get("sequence"), field="checkpoint sequence")
    if sequence < 1:
        raise ValueError("Shadow checkpoint sequence must start at one")
    previous_digest = checkpoint.get("previous_digest")
    if previous_digest is not None and (
        not isinstance(previous_digest, str)
        or len(previous_digest) != 64
        or any(character not in "0123456789abcdef" for character in previous_digest)
    ):
        raise ValueError("Shadow checkpoint previous_digest must be null or a SHA-256 digest")
    if sequence == 1 and previous_digest is not None:
        raise ValueError("First shadow checkpoint cannot name a previous digest")
    if sequence > 1 and previous_digest is None:
        raise ValueError("Later shadow checkpoint must name the previous digest")
    _aware_datetime(checkpoint.get("recorded_at"), field="checkpoint recorded_at")
    state = checkpoint.get("state")
    if not isinstance(state, dict):
        raise ValueError("Shadow checkpoint state must be an object")
    if state.get("run_id") != checkpoint["run_id"]:
        raise ValueError("Shadow checkpoint run identity does not match its state")
    if not isinstance(state.get("active"), bool):
        raise ValueError("Shadow checkpoint active state must be boolean")
    expected_digest = shadow_checkpoint_digest(checkpoint)
    if checkpoint["digest"] != expected_digest:
        raise ValueError("Shadow checkpoint digest mismatch")
    return checkpoint


@dataclass(frozen=True)
class ShadowFill:
    timestamp: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    realized_pnl: float | None
    reason: str
    cash_after: float
    unsettled_cash_after: float = 0.0
    commission: float = 0.0
    requested_quantity: float = 0.0
    fill_fraction: float = 1.0
    execution_cost: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["timestamp"] = self.timestamp.isoformat()
        return values


@dataclass
class ShadowPosition:
    symbol: str
    quantity: float
    entry_price: float
    entry_time: datetime
    entry_cost: float


@dataclass
class ShadowState:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    active: bool = True
    starting_cash: float = 0.0
    cash: float = 0.0
    unsettled_cash: float = 0.0
    equity: float = 0.0
    pnl: float = 0.0
    position: ShadowPosition | None = None
    fills: list[ShadowFill] = field(default_factory=list)


@dataclass(frozen=True)
class _PendingTransition:
    target: str | None
    reason: str
    due_analysis_count: int
    session: str


class LiveShadowEngine:
    """Live-quote virtual executor. This module has no broker dependency by design."""

    def __init__(self, config: SandboxConfig, *, bar_minutes: float | None = None) -> None:
        config.validate()
        self.config = config
        self.contract = contract_from_config(config)
        self.policy = DecisionPolicy(
            PolicyConfig(
                bullish_symbol="TQQQS",
                bearish_symbol="SQQQS",
                hard_stop_pct=self.contract.hard_stop_pct,
                take_profit_pct=self.contract.take_profit_pct,
                max_hold_minutes=self.contract.max_hold_minutes,
                no_trade_open_minutes=self.contract.no_trade_open_minutes,
                no_trade_close_minutes=self.contract.no_trade_close_minutes,
                market_hours=self.contract.market_hours,
            )
        )
        self.state = ShadowState(
            starting_cash=self.contract.initial_cash,
            cash=self.contract.initial_cash,
            equity=self.contract.initial_cash,
        )
        self._pending: _PendingTransition | None = None
        self._current_session: str | None = None
        self._analysis_count = 0
        self._session_analysis_count = 0
        self._last_decision_count = 0
        self._bar_minutes = (
            float(bar_minutes)
            if bar_minutes is not None
            else max(float(config.csv_bar_seconds) / 60.0, 1.0 / 60.0)
        )
        if self._bar_minutes <= 0:
            raise ValueError("Analysis bar duration must be positive")
        self._rng = random.Random(self.contract.random_seed)
        self._entries_by_session: dict[str, int] = {}
        self._session_start_equity: dict[str, float] = {}
        self._session_peak_equity: dict[str, float] = {}
        self._paused_sessions: set[str] = set()
        self._consecutive_losses = 0
        self._recent_returns = {"TQQQS": deque(maxlen=30), "SQQQS": deque(maxlen=30)}
        self._previous_prices: dict[str, float] = {}

    @property
    def current_session(self) -> str | None:
        return self._current_session

    def checkpoint(
        self,
        *,
        sequence: int,
        recorded_at: datetime,
        session: str,
        account_fingerprint: str,
        strategy_fingerprint: str,
        event: str,
        previous_digest: str | None,
    ) -> dict[str, Any]:
        """Create a versioned, hash-chained snapshot of all causal shadow state."""

        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("Shadow checkpoint time must be timezone-aware")
        position = self.state.position
        pending = self._pending
        checkpoint: dict[str, Any] = {
            "schema_version": SHADOW_CHECKPOINT_SCHEMA_VERSION,
            "run_id": self.state.run_id,
            "sequence": sequence,
            "recorded_at": recorded_at.astimezone(UTC).isoformat(),
            "session_key": session,
            "account_fingerprint": account_fingerprint,
            "strategy_fingerprint": strategy_fingerprint,
            "contract_fingerprint": self.contract.fingerprint,
            "event": event,
            "previous_digest": previous_digest,
            "state": {
                "run_id": self.state.run_id,
                "active": self.state.active,
                "starting_cash": self.state.starting_cash,
                "cash": self.state.cash,
                "unsettled_cash": self.state.unsettled_cash,
                "equity": self.state.equity,
                "pnl": self.state.pnl,
                "position": (
                    {
                        "symbol": position.symbol,
                        "quantity": position.quantity,
                        "entry_price": position.entry_price,
                        "entry_time": position.entry_time.isoformat(),
                        "entry_cost": position.entry_cost,
                    }
                    if position is not None
                    else None
                ),
                "fills": [fill.as_dict() for fill in self.state.fills],
                "pending": (
                    {
                        "target": pending.target,
                        "reason": pending.reason,
                        "due_analysis_count": pending.due_analysis_count,
                        "session": pending.session,
                    }
                    if pending is not None
                    else None
                ),
                "current_session": self._current_session,
                "analysis_count": self._analysis_count,
                "session_analysis_count": self._session_analysis_count,
                "last_decision_count": self._last_decision_count,
                "bar_minutes": self._bar_minutes,
                "entries_by_session": dict(self._entries_by_session),
                "session_start_equity": dict(self._session_start_equity),
                "session_peak_equity": dict(self._session_peak_equity),
                "paused_sessions": sorted(self._paused_sessions),
                "consecutive_losses": self._consecutive_losses,
                "recent_returns": {
                    symbol: list(returns) for symbol, returns in self._recent_returns.items()
                },
                "previous_prices": dict(self._previous_prices),
                "rng_state": _list_tree(self._rng.getstate()),
            },
        }
        checkpoint["digest"] = shadow_checkpoint_digest(checkpoint)
        validate_shadow_checkpoint(checkpoint)
        return checkpoint

    @classmethod
    def restore(
        cls,
        config: SandboxConfig,
        checkpoint: object,
        *,
        expected_session: str,
        expected_account_fingerprint: str,
        expected_strategy_fingerprint: str,
        bar_minutes: float | None = None,
    ) -> LiveShadowEngine:
        """Restore one exact compatible session, rejecting partial or ambiguous continuity."""

        validated = validate_shadow_checkpoint(checkpoint)
        if validated["session_key"] != expected_session:
            raise ValueError("Shadow checkpoint belongs to a different market session")
        if validated["account_fingerprint"] != expected_account_fingerprint:
            raise ValueError("Shadow checkpoint belongs to a different broker account")
        if validated["strategy_fingerprint"] != expected_strategy_fingerprint:
            raise ValueError("Shadow checkpoint strategy fingerprint does not match")
        engine = cls(config, bar_minutes=bar_minutes)
        if validated["contract_fingerprint"] != engine.contract.fingerprint:
            raise ValueError("Shadow checkpoint execution contract does not match")
        state = validated["state"]
        if not state.get("active"):
            raise ValueError("Stopped shadow checkpoint cannot be resumed")

        starting_cash = _finite(state.get("starting_cash"), field="starting_cash")
        cash = _finite(state.get("cash"), field="cash")
        unsettled_cash = _finite(state.get("unsettled_cash"), field="unsettled_cash")
        equity = _finite(state.get("equity"), field="equity")
        pnl = _finite(state.get("pnl"), field="pnl")
        if starting_cash <= 0 or cash < -1e-6 or unsettled_cash < -1e-6 or equity < -1e-6:
            raise ValueError("Shadow checkpoint ledger contains impossible balances")
        if not math.isclose(starting_cash, engine.contract.initial_cash, abs_tol=1e-9):
            raise ValueError("Shadow checkpoint starting cash does not match its execution contract")
        if engine.contract.settlement_model != "cash_t1" and unsettled_cash > 1e-6:
            raise ValueError("Shadow checkpoint has unsettled cash under an instant-settlement contract")
        if not math.isclose(pnl, equity - starting_cash, abs_tol=1e-6):
            raise ValueError("Shadow checkpoint P/L does not reconcile to equity")

        raw_position = state.get("position")
        position: ShadowPosition | None = None
        if raw_position is not None:
            if not isinstance(raw_position, dict) or raw_position.get("symbol") not in UNDERLYING:
                raise ValueError("Shadow checkpoint position is invalid")
            quantity = _finite(raw_position.get("quantity"), field="position quantity")
            entry_price = _finite(raw_position.get("entry_price"), field="position entry_price")
            entry_cost = _finite(raw_position.get("entry_cost"), field="position entry_cost")
            if quantity <= 0 or entry_price <= 0 or entry_cost <= 0:
                raise ValueError("Shadow checkpoint position values must be positive")
            position = ShadowPosition(
                str(raw_position["symbol"]),
                quantity,
                entry_price,
                _aware_datetime(raw_position.get("entry_time"), field="position entry_time"),
                entry_cost,
            )

        raw_fills = state.get("fills")
        if not isinstance(raw_fills, list):
            raise ValueError("Shadow checkpoint fills must be a list")
        fills: list[ShadowFill] = []
        for index, raw_fill in enumerate(raw_fills):
            if not isinstance(raw_fill, dict):
                raise ValueError(f"Shadow checkpoint fill {index} must be an object")
            symbol = raw_fill.get("symbol")
            side = raw_fill.get("side")
            if symbol not in UNDERLYING or side not in {"buy", "sell"}:
                raise ValueError(f"Shadow checkpoint fill {index} has an invalid instrument or side")
            quantity = _finite(raw_fill.get("quantity"), field=f"fill {index} quantity")
            price = _finite(raw_fill.get("price"), field=f"fill {index} price")
            requested = _finite(
                raw_fill.get("requested_quantity", 0.0),
                field=f"fill {index} requested_quantity",
            )
            fill_fraction = _finite(
                raw_fill.get("fill_fraction", 1.0), field=f"fill {index} fill_fraction"
            )
            if quantity <= 0 or price <= 0 or requested < 0 or not 0 < fill_fraction <= 1:
                raise ValueError(f"Shadow checkpoint fill {index} has impossible values")
            realized_raw = raw_fill.get("realized_pnl")
            fills.append(
                ShadowFill(
                    timestamp=_aware_datetime(
                        raw_fill.get("timestamp"), field=f"fill {index} timestamp"
                    ),
                    symbol=str(symbol),
                    side=str(side),
                    quantity=quantity,
                    price=price,
                    realized_pnl=(
                        None
                        if realized_raw is None
                        else _finite(realized_raw, field=f"fill {index} realized_pnl")
                    ),
                    reason=str(raw_fill.get("reason", "")),
                    cash_after=_finite(
                        raw_fill.get("cash_after"), field=f"fill {index} cash_after"
                    ),
                    unsettled_cash_after=_finite(
                        raw_fill.get("unsettled_cash_after", 0.0),
                        field=f"fill {index} unsettled_cash_after",
                    ),
                    commission=_finite(
                        raw_fill.get("commission", 0.0), field=f"fill {index} commission"
                    ),
                    requested_quantity=requested,
                    fill_fraction=fill_fraction,
                    execution_cost=_finite(
                        raw_fill.get("execution_cost", 0.0),
                        field=f"fill {index} execution_cost",
                    ),
                )
            )

        raw_pending = state.get("pending")
        pending: _PendingTransition | None = None
        if raw_pending is not None:
            if not isinstance(raw_pending, dict):
                raise ValueError("Shadow checkpoint pending transition must be an object")
            target = raw_pending.get("target")
            if target is not None and target not in UNDERLYING:
                raise ValueError("Shadow checkpoint pending target is invalid")
            pending_session = raw_pending.get("session")
            if not isinstance(pending_session, str) or not pending_session:
                raise ValueError("Shadow checkpoint pending session is invalid")
            pending = _PendingTransition(
                target,
                str(raw_pending.get("reason", "")),
                _nonnegative_int(
                    raw_pending.get("due_analysis_count"), field="pending due_analysis_count"
                ),
                pending_session,
            )

        current_session = state.get("current_session")
        if current_session is not None and current_session != expected_session:
            raise ValueError("Shadow checkpoint internal session does not match its binding")
        analysis_count = _nonnegative_int(state.get("analysis_count"), field="analysis_count")
        session_analysis_count = _nonnegative_int(
            state.get("session_analysis_count"), field="session_analysis_count"
        )
        last_decision_count = _nonnegative_int(
            state.get("last_decision_count"), field="last_decision_count"
        )
        if session_analysis_count > analysis_count or last_decision_count > session_analysis_count:
            raise ValueError("Shadow checkpoint decision counters are inconsistent")
        if analysis_count and current_session is None:
            raise ValueError("Shadow checkpoint with analysis history must identify its session")
        restored_bar_minutes = _finite(state.get("bar_minutes"), field="bar_minutes")
        if not math.isclose(restored_bar_minutes, engine._bar_minutes, abs_tol=1e-12):
            raise ValueError("Shadow checkpoint bar duration does not match")

        def session_int_map(field_name: str) -> dict[str, int]:
            raw = state.get(field_name)
            if not isinstance(raw, dict):
                raise ValueError(f"Shadow checkpoint {field_name} must be an object")
            result: dict[str, int] = {}
            for key, value in raw.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(f"Shadow checkpoint {field_name} has an invalid session key")
                result[key] = _nonnegative_int(value, field=f"{field_name}.{key}")
            return result

        def session_float_map(field_name: str) -> dict[str, float]:
            raw = state.get(field_name)
            if not isinstance(raw, dict):
                raise ValueError(f"Shadow checkpoint {field_name} must be an object")
            result: dict[str, float] = {}
            for key, value in raw.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(f"Shadow checkpoint {field_name} has an invalid session key")
                result[key] = _finite(value, field=f"{field_name}.{key}")
            return result

        entries_by_session = session_int_map("entries_by_session")
        session_start_equity = session_float_map("session_start_equity")
        session_peak_equity = session_float_map("session_peak_equity")
        raw_paused = state.get("paused_sessions")
        if not isinstance(raw_paused, list) or any(
            not isinstance(value, str) or not value for value in raw_paused
        ):
            raise ValueError("Shadow checkpoint paused_sessions must contain session keys")
        recent_returns = state.get("recent_returns")
        if not isinstance(recent_returns, dict) or set(recent_returns) != set(UNDERLYING):
            raise ValueError("Shadow checkpoint recent return instruments do not match")
        restored_returns: dict[str, deque[float]] = {}
        for symbol in UNDERLYING:
            values = recent_returns[symbol]
            if not isinstance(values, list) or len(values) > 30:
                raise ValueError(f"Shadow checkpoint {symbol} returns are invalid")
            restored_returns[symbol] = deque(
                (_finite(value, field=f"{symbol} return") for value in values), maxlen=30
            )
        raw_prices = state.get("previous_prices")
        if not isinstance(raw_prices, dict) or any(symbol not in UNDERLYING for symbol in raw_prices):
            raise ValueError("Shadow checkpoint previous prices contain an invalid instrument")
        previous_prices = {
            str(symbol): _finite(value, field=f"previous price {symbol}")
            for symbol, value in raw_prices.items()
        }
        if any(value <= 0 for value in previous_prices.values()):
            raise ValueError("Shadow checkpoint previous prices must be positive")

        engine.state = ShadowState(
            run_id=str(validated["run_id"]),
            active=True,
            starting_cash=starting_cash,
            cash=cash,
            unsettled_cash=unsettled_cash,
            equity=equity,
            pnl=pnl,
            position=position,
            fills=fills,
        )
        engine._pending = pending
        engine._current_session = current_session
        engine._analysis_count = analysis_count
        engine._session_analysis_count = session_analysis_count
        engine._last_decision_count = last_decision_count
        engine._entries_by_session = entries_by_session
        engine._session_start_equity = session_start_equity
        engine._session_peak_equity = session_peak_equity
        engine._paused_sessions = set(raw_paused)
        engine._consecutive_losses = _nonnegative_int(
            state.get("consecutive_losses"), field="consecutive_losses"
        )
        engine._recent_returns = restored_returns
        engine._previous_prices = previous_prices
        try:
            engine._rng.setstate(_tuple_tree(state.get("rng_state")))
        except (TypeError, ValueError) as exc:
            raise ValueError("Shadow checkpoint RNG state is invalid") from exc
        return engine

    def on_bar(self, timestamp: datetime, signal: Signal, quotes: dict[str, Quote]) -> list[ShadowFill]:
        """Advance a synthetic bar trace whose decision fills on the next call."""

        return self._advance(timestamp, signal, quotes, execute_current_decision=False)

    def on_causal_quote(
        self,
        timestamp: datetime,
        signal: Signal,
        quotes: dict[str, Quote],
    ) -> list[ShadowFill]:
        """Execute a completed-bar decision at its first causally available quote."""

        return self._advance(timestamp, signal, quotes, execute_current_decision=True)

    def _advance(
        self,
        timestamp: datetime,
        signal: Signal,
        quotes: dict[str, Quote],
        *,
        execute_current_decision: bool,
    ) -> list[ShadowFill]:
        if not self.state.active:
            return []
        fills: list[ShadowFill] = []
        current_session = session_key(timestamp, self.contract.market_hours)
        if self._current_session is None:
            self._current_session = current_session
            self._session_analysis_count = 0
            self._last_decision_count = 0
        elif current_session != self._current_session:
            if self.contract.settlement_model == "cash_t1":
                self.state.cash += self.state.unsettled_cash
                self.state.unsettled_cash = 0.0
            self._current_session = current_session
            self._consecutive_losses = 0
            self._session_analysis_count = 0
            self._last_decision_count = 0
        self._analysis_count += 1
        self._session_analysis_count += 1
        if (
            self._pending is not None
            and self.contract.time_in_force == "gfd"
            and self._pending.session != current_session
        ):
            self._pending = None

        self._update_returns(quotes)
        self._mark(quotes)
        self._session_start_equity.setdefault(current_session, self.state.equity)
        self._session_peak_equity[current_session] = max(
            self._session_peak_equity.get(current_session, self.state.equity),
            self.state.equity,
        )
        if daily_loss_reached(
            self.contract,
            session_start_equity=self._session_start_equity[current_session],
            session_peak_equity=self._session_peak_equity[current_session],
            current_equity=self.state.equity,
        ):
            self._paused_sessions.add(current_session)
            if self.state.position is not None and self._pending is None:
                self._pending = _PendingTransition(
                    None,
                    "Daily loss pause",
                    self._analysis_count + 1 + self.contract.latency_bars,
                    current_session,
                )

        window_allowed = self.policy.trading_window_allowed(timestamp)
        pending_is_exit = bool(
            self.state.position is not None
            and self._pending is not None
            and self._pending.target != self.state.position.symbol
        )
        pending_window = self.policy.exit_window_allowed(timestamp) if pending_is_exit else window_allowed
        if (
            self._pending is not None
            and self._analysis_count >= self._pending.due_analysis_count
            and pending_window
        ):
            complete, fill = self._transition(
                timestamp,
                self._pending.target,
                self._pending.reason,
                quotes,
            )
            if fill:
                fills.append(fill)
                self.state.fills.append(fill)
                self._record_realized(fill, current_session)
            if complete:
                self._pending = None
            elif self._pending is not None:
                self._pending = _PendingTransition(
                    self._pending.target,
                    self._pending.reason,
                    self._analysis_count + 1,
                    current_session,
                )

        position = self.state.position
        marked = None
        if position:
            quote = quotes.get(UNDERLYING[position.symbol])
            marked = quote.mid if quote else None
        policy_position = (
            PolicyPosition(
                position.symbol,
                position.entry_price,
                marked,
                held_minutes(position.entry_time, timestamp),
            )
            if position
            else None
        )
        if decision_due(
            analysis_count=self._session_analysis_count,
            last_decision_count=self._last_decision_count,
            decision_stride=self.contract.decision_stride,
        ):
            self._last_decision_count = self._session_analysis_count
            decision = self.policy.decide(signal, timestamp, policy_position)
            current = position.symbol if position else None
            decision_window = (
                self.policy.exit_window_allowed(timestamp) if current is not None else window_allowed
            )
            entry_blocked = bool(
                current is None
                and decision.target_symbol is not None
                and entry_block_reason(
                    self.contract,
                    entries_this_session=self._entries_by_session.get(current_session, 0),
                    consecutive_losses=self._consecutive_losses,
                    daily_loss_paused=current_session in self._paused_sessions,
                )
            )
            if (
                decision_window
                and not entry_blocked
                and decision.target_symbol != current
                and self._pending is None
            ):
                due = self._analysis_count + self.contract.latency_bars
                if not execute_current_decision:
                    due += 1
                pending = _PendingTransition(
                    decision.target_symbol,
                    decision.reason,
                    due,
                    current_session,
                )
                if due <= self._analysis_count:
                    complete, fill = self._transition(
                        timestamp,
                        pending.target,
                        pending.reason,
                        quotes,
                    )
                    if fill:
                        fills.append(fill)
                        self.state.fills.append(fill)
                        self._record_realized(fill, current_session)
                    if not complete:
                        self._pending = _PendingTransition(
                            pending.target,
                            pending.reason,
                            self._analysis_count + 1,
                            current_session,
                        )
                else:
                    self._pending = pending
        self._mark(quotes)
        return fills

    def stop(
        self,
        quotes: dict[str, Quote] | None = None,
        *,
        flatten_at: datetime | None = None,
        flatten_reason: str = "Virtual end-of-day flatten",
    ) -> ShadowState:
        if flatten_at is not None and self.state.position is not None:
            _complete, fill = self._transition(
                flatten_at,
                None,
                flatten_reason,
                quotes or {},
                force_fill=True,
            )
            if fill is not None:
                self.state.fills.append(fill)
        self.state.active = False
        self._pending = None
        if quotes:
            self._mark(quotes)
        return self.state

    def _transition(
        self,
        timestamp: datetime,
        target: str | None,
        reason: str,
        quotes: dict[str, Quote],
        *,
        force_fill: bool = False,
    ) -> tuple[bool, ShadowFill | None]:
        position = self.state.position
        if position:
            if target == position.symbol:
                return True, None
            quote = quotes.get(UNDERLYING[position.symbol])
            if not quote:
                return False, None
            if not force_fill and self._rng.random() < self.contract.rejection_rate_pct / 100.0:
                return False, None
            spread = effective_spread_bps(self.contract, quoted_spread_bps=quote.spread_bps)
            price = execution_price(
                self.contract,
                reference_price=quote.mid,
                side="sell",
                spread_bps=spread,
            )
            if self.contract.order_type == "limit" and not force_fill:
                modeled_bid = quote.mid * (1 - spread / 20_000)
                limit_price = modeled_bid * (1 - self.contract.limit_offset_bps / 10_000)
                if price < limit_price:
                    return False, None
            requested = position.quantity
            quantity = (
                requested
                if force_fill
                else fillable_quantity(self.contract, requested_quantity=requested)
            )
            if quantity <= 0:
                return False, None
            cost_share = position.entry_cost * (quantity / position.quantity)
            proceeds = quantity * price - self.contract.commission_per_order
            realized = proceeds - cost_share
            if self.contract.settlement_model == "cash_t1":
                self.state.unsettled_cash += proceeds
            else:
                self.state.cash += proceeds
            remaining = position.quantity - quantity
            self.state.position = None
            if remaining > 1e-9:
                self.state.position = ShadowPosition(
                    position.symbol,
                    remaining,
                    position.entry_price,
                    position.entry_time,
                    position.entry_cost - cost_share,
                )
            execution_cost = max(0.0, (quote.mid - price) * quantity) + self.contract.commission_per_order
            fill = ShadowFill(
                timestamp,
                position.symbol,
                "sell",
                quantity,
                price,
                realized,
                reason or "Shadow exit",
                self.state.cash,
                self.state.unsettled_cash,
                self.contract.commission_per_order,
                requested,
                quantity / requested,
                execution_cost,
            )
            return target is None and self.state.position is None, fill
        if target is None:
            return True, None
        current_session = session_key(timestamp, self.contract.market_hours)
        if entry_block_reason(
            self.contract,
            entries_this_session=self._entries_by_session.get(current_session, 0),
            consecutive_losses=self._consecutive_losses,
            daily_loss_paused=current_session in self._paused_sessions,
        ):
            return True, None
        quote = quotes.get(UNDERLYING[target])
        if not quote:
            return False, None
        spread = effective_spread_bps(self.contract, quoted_spread_bps=quote.spread_bps)
        price = execution_price(
            self.contract,
            reference_price=quote.mid,
            side="buy",
            spread_bps=spread,
        )
        if self.contract.order_type == "limit":
            modeled_ask = quote.mid * (1 + spread / 20_000)
            limit_price = modeled_ask * (1 + self.contract.limit_offset_bps / 10_000)
            if price > limit_price:
                return False, None
        realized = annualized_volatility(
            tuple(self._recent_returns[target]),
            bar_minutes=self._bar_minutes,
            market_hours=self.contract.market_hours,
        )
        sizing = size_entry(
            self.contract,
            equity=self.state.equity,
            settled_cash=self.state.cash,
            price=price,
            realized_volatility=realized,
        )
        requested = sizing.requested_quantity
        if requested <= 0:
            return True, None
        if self._rng.random() < self.contract.rejection_rate_pct / 100.0:
            return False, None
        quantity = sizing.fillable_quantity
        if quantity <= 0:
            return False, None
        cost = quantity * price + self.contract.commission_per_order
        self.state.cash -= cost
        self.state.position = ShadowPosition(target, quantity, price, timestamp, cost)
        self._entries_by_session[current_session] = self._entries_by_session.get(current_session, 0) + 1
        execution_cost = max(0.0, (price - quote.mid) * quantity) + self.contract.commission_per_order
        return True, ShadowFill(
            timestamp,
            target,
            "buy",
            quantity,
            price,
            None,
            reason or "Shadow entry",
            self.state.cash,
            self.state.unsettled_cash,
            self.contract.commission_per_order,
            requested,
            quantity / requested,
            execution_cost,
        )

    def _record_realized(self, fill: ShadowFill, current_session: str) -> None:
        if fill.realized_pnl is None:
            return
        self._consecutive_losses = next_consecutive_losses(
            self._consecutive_losses,
            fill.realized_pnl,
        )
        if self._consecutive_losses >= self.contract.max_consecutive_losses:
            self._paused_sessions.add(current_session)

    def _update_returns(self, quotes: dict[str, Quote]) -> None:
        for alias, underlying in UNDERLYING.items():
            quote = quotes.get(underlying)
            if quote is None:
                continue
            mark = quote.mid
            previous = self._previous_prices.get(alias)
            if previous is not None and previous > 0:
                self._recent_returns[alias].append(mark / previous - 1.0)
            self._previous_prices[alias] = mark

    def _mark(self, quotes: dict[str, Quote]) -> None:
        value = self.state.cash + self.state.unsettled_cash
        if self.state.position:
            quote = quotes.get(UNDERLYING[self.state.position.symbol])
            if quote:
                value += self.state.position.quantity * quote.mid
        self.state.equity = value
        self.state.pnl = value - self.state.starting_cash

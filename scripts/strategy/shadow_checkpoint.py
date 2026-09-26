from __future__ import annotations

import math
from collections import deque
from datetime import UTC, datetime
from typing import Any, Self

from grande_alpha.research.sandbox_models import SandboxConfig
from grande_alpha.strategy.shadow_state import (
    SHADOW_CHECKPOINT_SCHEMA_VERSION,
    UNDERLYING,
    ShadowFill,
    ShadowPosition,
    ShadowState,
    _aware_datetime,
    _finite,
    _list_tree,
    _nonnegative_int,
    _PendingTransition,
    _tuple_tree,
    shadow_checkpoint_digest,
    validate_shadow_checkpoint,
)


class ShadowCheckpoint:
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
    ) -> Self:
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

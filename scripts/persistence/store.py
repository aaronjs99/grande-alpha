from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from grande_alpha.configuration.config import data_dir
from grande_alpha.domain.clock import utc_now
from grande_alpha.domain.market_models import Bar, Quote, Signal
from grande_alpha.domain.order_models import BrokerOrder, OrderIntent

from .execution import ExecutionRepository
from .market import MarketRepository
from .receipts import ReceiptsRepository
from .research import ResearchRepository
from .sandbox import SandboxRepository
from .schema import SchemaRepository
from .shadow import ShadowRepository
from .transactions import Transactions
from .upgrade import upgrade_execution_store as upgrade_execution_store
from .validation import (
    _RISK_ENVELOPE_FIELDS as _RISK_ENVELOPE_FIELDS,
)
from .validation import (
    _parse_aware_utc as _parse_aware_utc,
)
from .validation import (
    _passing_holdout_metrics as _passing_holdout_metrics,
)
from .validation import (
    _valid_provenance_record as _valid_provenance_record,
)
from .validation import (
    _valid_quality_record as _valid_quality_record,
)
from .validation import (
    _valid_risk_envelope as _valid_risk_envelope,
)

QUOTE_BATCH_SCHEMA_VERSION = 2
EXACT_QUOTE_VALIDATOR_VERSION = 2


class AuditStore:
    """Compatibility facade; each operation belongs to one focused repository."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "grande_alpha.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.notification_sink = None
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._transactions = Transactions(self._connection, self._lock)
        SchemaRepository(self)._initialize()
        self.execution = ExecutionRepository(self)
        self.market = MarketRepository(self)
        self.receipts = ReceiptsRepository(self)
        self.research = ResearchRepository(self)
        self.sandbox = SandboxRepository(self)
        self.shadow = ShadowRepository(self)

    def transaction(self):
        return self._transactions.transaction()

    def _now(self):
        return utc_now()

    def record_quote(self, quote: Quote):
        return self.market.record_quote(quote)

    def record_bar(self, bar: Bar):
        return self.market.record_bar(bar)

    def record_signal(self, signal: Signal):
        return self.market.record_signal(signal)

    def receipt(self, category: str, summary: str, payload: Any = None, severity: str = "info"):
        return self.receipts.receipt(category, summary, payload, severity)

    def device_notifications(self, *, after_id: int = 0, limit: int = 100, unread_only: bool = False):
        return self.receipts.device_notifications(after_id=after_id, limit=limit, unread_only=unread_only)

    def acknowledge_notification(self, notification_id: int):
        return self.receipts.acknowledge_notification(notification_id)

    _decode_shadow_checkpoint = staticmethod(ShadowRepository._decode_shadow_checkpoint)

    def append_shadow_checkpoint(self, checkpoint: dict[str, Any]):
        return self.shadow.append_shadow_checkpoint(checkpoint)

    def latest_shadow_checkpoint(self):
        return self.shadow.latest_shadow_checkpoint()

    def shadow_checkpoints(self, run_id: str):
        return self.shadow.shadow_checkpoints(run_id)

    def register_standing(self, authority_id: str, scope_digest: str):
        return self.execution.register_standing(authority_id, scope_digest)

    def standing_active(self, authority_id: str, scope_digest: str):
        return self.execution.standing_active(authority_id, scope_digest)

    def active_standing_for_scope(self, scope_digest: str):
        return self.execution.active_standing_for_scope(scope_digest)

    def stop_standing(self, authority_id: str | None = None):
        return self.execution.stop_standing(authority_id)

    def record_intent(self, intent: OrderIntent):
        return self.execution.record_intent(intent)

    def update_intent(self, ref_id: str, order_id: str | None, state: str):
        return self.execution.update_intent(ref_id, order_id, state)

    def mark_intent_submitting(
        self,
        ref_id: str,
        *,
        account_number: str,
        authority_id: str,
        strategy_fingerprint: str,
        authorized_notional: float,
    ):
        return self.execution.mark_intent_submitting(
            ref_id,
            account_number=account_number,
            authority_id=authority_id,
            strategy_fingerprint=strategy_fingerprint,
            authorized_notional=authorized_notional,
        )

    def record_quote_batch(
        self,
        quotes: dict[str, Quote],
        *,
        stream_id: str,
        validation_profile: str = "passive_unvalidated",
        validation_version: int = 0,
        max_age_seconds: float | None = None,
        max_skew_seconds: float | None = None,
    ):
        return self.market.record_quote_batch(
            quotes,
            stream_id=stream_id,
            validation_profile=validation_profile,
            validation_version=validation_version,
            max_age_seconds=max_age_seconds,
            max_skew_seconds=max_skew_seconds,
        )

    def unresolved_order_intents(self, account_number: str):
        return self.execution.unresolved_order_intents(account_number)

    def owned_broker_order_ids(self, account_number: str):
        return self.execution.owned_broker_order_ids(account_number)

    def owned_broker_order_refs(self, account_number: str):
        return self.execution.owned_broker_order_refs(account_number)

    def owned_broker_order_bindings(self, account_number: str):
        return self.execution.owned_broker_order_bindings(account_number)

    def intent_submission_started_at(self, ref_id: str):
        return self.execution.intent_submission_started_at(ref_id)

    def record_broker_order_executions(self, account_number: str, order: BrokerOrder):
        return self.execution.record_broker_order_executions(account_number, order)

    def broker_executions(self, account_number: str, *, order_id: str | None = None):
        return self.execution.broker_executions(account_number, order_id=order_id)

    def live_loss_streak(self, account_number: str, et_date: str):
        return self.execution.live_loss_streak(account_number, et_date)

    def live_filled_entry_order_ids(
        self, account_number: str, et_date: str, *, strategy_fingerprint: str | None = None
    ):
        return self.execution.live_filled_entry_order_ids(
            account_number, et_date, strategy_fingerprint=strategy_fingerprint
        )

    def active_holding_start(self, account_number: str, symbol: str, current_quantity: float):
        return self.execution.active_holding_start(account_number, symbol, current_quantity)

    def validate_execution_inventory(self, account_number: str, positions: list[Any]):
        return self.execution.validate_execution_inventory(account_number, positions)

    def incomplete_execution_provenance(self, account_number: str, et_date: str):
        return self.execution.incomplete_execution_provenance(account_number, et_date)

    def _record_loss_pause(
        self,
        account_number: str,
        et_date: str,
        latched: bool,
        scope_digest: str | None,
        recovery_delay: int | None,
        recovery_unit: str,
        observed: datetime,
    ):
        return self.execution._record_loss_pause(
            account_number, et_date, latched, scope_digest, recovery_delay, recovery_unit, observed
        )

    def record_daily_risk(
        self,
        account_number: str,
        et_date: str,
        value: float,
        loss_limit: float,
        *,
        require_existing: bool = False,
        carry_previous_observation: bool = False,
        scope_digest: str | None = None,
        recovery_delay: int | None = None,
        recovery_unit: str = "manual",
        observed_at: datetime | None = None,
    ):
        return self.execution.record_daily_risk(
            account_number,
            et_date,
            value,
            loss_limit,
            require_existing=require_existing,
            carry_previous_observation=carry_previous_observation,
            scope_digest=scope_digest,
            recovery_delay=recovery_delay,
            recovery_unit=recovery_unit,
            observed_at=observed_at,
        )

    def record_mixed_daily_pnl(
        self,
        account_number: str,
        et_date: str,
        pnl: float,
        loss_limit: float,
        *,
        scope_digest: str,
        recovery_delay: int | None,
        recovery_unit: str,
        require_existing: bool = False,
        carry_previous_observation: bool = False,
        observed_at: datetime | None = None,
    ):
        return self.execution.record_mixed_daily_pnl(
            account_number,
            et_date,
            pnl,
            loss_limit,
            scope_digest=scope_digest,
            recovery_delay=recovery_delay,
            recovery_unit=recovery_unit,
            require_existing=require_existing,
            carry_previous_observation=carry_previous_observation,
            observed_at=observed_at,
        )

    def acknowledge_loss_recovery(self, account_number: str, *, observed_at: datetime | None = None):
        return self.execution.acknowledge_loss_recovery(account_number, observed_at=observed_at)

    def live_daily_usage(self, account_number: str, et_date: str):
        return self.execution.live_daily_usage(account_number, et_date)

    def recent_receipts(self, limit: int = 200):
        return self.receipts.recent_receipts(limit)

    def prune_market_history(self, retention_days: int):
        return self.market.prune_market_history(retention_days)

    def plan_research_contribution(
        self,
        period: str,
        realized_profit: float,
        fees: float,
        tax_reserve: float,
        contribution_rate: float,
        notes: str = "",
    ):
        return self.research.plan_research_contribution(
            period, realized_profit, fees, tax_reserve, contribution_rate, notes
        )

    def confirm_research_contribution(self, entry_id: int, reference: str):
        return self.research.confirm_research_contribution(entry_id, reference)

    def research_fund_entries(self, limit: int = 200):
        return self.research.research_fund_entries(limit)

    def confirmed_research_total(self):
        return self.research.confirmed_research_total()

    def record_sandbox_run(
        self,
        run_id: str,
        data_source: str,
        replay_start: str,
        replay_end: str,
        config: dict[str, Any],
        metrics: dict[str, Any],
        fills: list[dict[str, Any]],
        events: list[dict[str, Any]] | None = None,
    ):
        return self.sandbox.record_sandbox_run(
            run_id, data_source, replay_start, replay_end, config, metrics, fills, events
        )

    def recent_sandbox_runs(self, limit: int = 50):
        return self.sandbox.recent_sandbox_runs(limit)

    def sandbox_run(self, run_id: str):
        return self.sandbox.sandbox_run(run_id)

    def record_research_trials(self, dataset_hash: str, trials: list[dict[str, Any]]):
        return self.research.record_research_trials(dataset_hash, trials)

    def research_trial_count(self, dataset_hash: str):
        return self.research.research_trial_count(dataset_hash)

    def reserve_research_holdout(
        self,
        *,
        dataset_hash: str,
        development_hash: str,
        holdout_hash: str,
        holdout_start: str,
        holdout_end: str,
        policy_version: int,
        provenance_hash: str = "",
        development_quality: dict[str, Any] | None = None,
        holdout_quality: dict[str, Any] | None = None,
    ):
        return self.research.reserve_research_holdout(
            dataset_hash=dataset_hash,
            development_hash=development_hash,
            holdout_hash=holdout_hash,
            holdout_start=holdout_start,
            holdout_end=holdout_end,
            policy_version=policy_version,
            provenance_hash=provenance_hash,
            development_quality=development_quality,
            holdout_quality=holdout_quality,
        )

    def freeze_research_holdout(self, holdout_id: int, selected_fingerprint: str):
        return self.research.freeze_research_holdout(holdout_id, selected_fingerprint)

    def claim_research_holdout(self, holdout_id: int, selected_fingerprint: str):
        return self.research.claim_research_holdout(holdout_id, selected_fingerprint)

    def consume_research_holdout(self, holdout_id: int, selected_fingerprint: str, metrics: dict[str, Any]):
        return self.research.consume_research_holdout(holdout_id, selected_fingerprint, metrics)

    def invalidate_research_holdout(self, holdout_id: int):
        return self.research.invalidate_research_holdout(holdout_id)

    def research_holdout(self, holdout_id: int):
        return self.research.research_holdout(holdout_id)

    def record_research_promotion(
        self,
        *,
        dataset_hash: str,
        strategy_fingerprint: str,
        policy_version: int,
        status: str,
        source: str,
        replay_end: str,
        gates: list[dict[str, Any]],
        risk_envelope: dict[str, float | int],
        holdout_id: int | None = None,
        provenance_hash: str = "",
        provenance: dict[str, Any] | None = None,
    ):
        return self.research.record_research_promotion(
            dataset_hash=dataset_hash,
            strategy_fingerprint=strategy_fingerprint,
            policy_version=policy_version,
            status=status,
            source=source,
            replay_end=replay_end,
            gates=gates,
            risk_envelope=risk_envelope,
            holdout_id=holdout_id,
            provenance_hash=provenance_hash,
            provenance=provenance,
        )

    _decode_research_promotion = staticmethod(ResearchRepository._decode_research_promotion)

    def recent_research_promotions(self, limit: int = 50):
        return self.research.recent_research_promotions(limit)

    def research_promotion(self, promotion_id: int | None = None):
        return self.research.research_promotion(promotion_id)

    def current_live_evidence(
        self,
        strategy_fingerprint: str,
        max_age_days: int = 30,
        requested_envelope: dict[str, float | int] | None = None,
    ):
        return self.research.current_live_evidence(strategy_fingerprint, max_age_days, requested_envelope)

    def close(self):
        with self._lock:
            self._connection.close()

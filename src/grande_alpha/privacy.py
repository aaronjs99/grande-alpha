from __future__ import annotations

import json
import platform
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from grande_alpha import __version__
from grande_alpha.config import AppConfig
from grande_alpha.storage import AuditStore

SENSITIVE_FRAGMENTS = (
    "account",
    "authorization",
    "client_secret",
    "confirmation_reference",
    "oauth",
    "order_id",
    "ref_id",
    "token",
)


def redact(value: Any, key: str = "") -> Any:
    if any(fragment in key.lower() for fragment in SENSITIVE_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def public_config(config: AppConfig) -> dict[str, Any]:
    allowed = {
        "onboarding_version",
        "cadence_version",
        "disclosure_version",
        "broker_connection_enabled",
        "live_trading_enabled",
        "remote_market_data_enabled",
        "personal_ledger_enabled",
        "market_history_retention_days",
        "poll_seconds",
        "reconcile_seconds",
        "bar_seconds",
        "warmup_bars",
        "fast_ema",
        "slow_ema",
        "trend_threshold_bps",
        "momentum_bars",
    }
    return {key: value for key, value in asdict(config).items() if key in allowed}


def export_diagnostics(config: AppConfig, store: AuditStore, path: Path) -> None:
    receipts = []
    for row in store.recent_receipts(200):
        payload = json.loads(row.get("payload_json") or "{}")
        receipts.append(
            {
                "created_at": row.get("created_at"),
                "category": row.get("category"),
                "severity": row.get("severity"),
                "summary": redact_text(str(row.get("summary") or "")),
                "payload": redact(payload),
            }
        )
    document = {
        "notice": "User-reviewed support export. Credentials and known account/order identifiers are redacted.",
        "application": {"name": "GRANDE Alpha", "version": __version__},
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "configuration": public_config(config),
        "recent_receipts": receipts,
    }
    path.write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")


def redact_text(value: str) -> str:
    """Mask common UUID, account-tail, and long identifier forms in free text."""
    value = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "[REDACTED-ID]",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(?:account|acct)\s*(?:ending|#)?\s*\d{3,}\b", "[REDACTED-ACCOUNT]", value, flags=re.IGNORECASE
    )
    return re.sub(r"\b[0-9A-Za-z_-]{24,}\b", "[REDACTED-ID]", value)

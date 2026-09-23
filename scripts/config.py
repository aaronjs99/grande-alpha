from __future__ import annotations

import json
import math
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from platformdirs import user_data_path

from grande_alpha.execution import execution_profile
from grande_alpha.strategy import STRATEGY_NAMES

APP_NAME = "GRANDEAlpha"
DISPLAY_NAME = "GRANDE Alpha"
LEGACY_APP_NAME = "MomentumTrader"
MCP_URL = "https://agent.robinhood.com/mcp/trading"
ONBOARDING_VERSION = 1
DISCLOSURE_VERSION = "2026-08"
CADENCE_VERSION = 6


class ConfigUpgradeRequired(RuntimeError):
    """Raised when a saved configuration needs the explicit upgrade operation."""


@dataclass
class AppConfig:
    cadence_version: int = CADENCE_VERSION
    onboarding_version: int = 0
    disclosure_version: str = ""
    broker_connection_enabled: bool = False
    live_trading_enabled: bool = False
    remote_market_data_enabled: bool = False
    personal_ledger_enabled: bool = False
    # Preserve enough calendar history to make the 141-session evidence floor achievable.
    market_history_retention_days: int = 240
    # Retail low-latency profile: quote reads are completion-gated, decisions use
    # completed bars, and account/order state is reconciled on a separate clock.
    poll_seconds: float = 1.0
    reconcile_seconds: float = 5.0
    bar_seconds: int = 5
    # One policy action is selected only after this many completed analysis bars.
    # The default therefore gives t_analysis=5s and t_trade=15s.
    trade_every_bars: int = 3
    # Runtime execution champion. Research sandbox presets remain independent.
    # Legacy files that predate this field migrate to the fail-safe cash policy.
    strategy_name: str = "cash"
    # Defaults copied into each separately confirmed live grant. The user can
    # change them again in the grant dialog before any authority is created.
    market_hours: str = "regular_hours"
    order_type: str = "market"
    time_in_force: str = "gfd"
    limit_offset_bps: float = 10.0
    # Conservative research/live-shadow assumption. The broker's reported buying
    # power remains authoritative for actual orders.
    settlement_model: str = "cash_t1"
    warmup_bars: int = 24
    fast_ema: int = 8
    slow_ema: int = 21
    trend_threshold_bps: float = 4.0
    momentum_bars: int = 3
    hard_stop_pct: float = 0.008
    take_profit_pct: float = 0.015
    max_hold_minutes: int = 45
    no_trade_open_minutes: int = 5
    no_trade_close_minutes: int = 10
    default_session_minutes: int = 60
    default_max_order_notional: float = 25.0
    default_max_daily_notional: float = 50.0
    default_max_total_exposure: float = 40.0
    default_max_daily_loss: float = 2.0
    default_max_trades: int = 6
    default_max_orders_per_minute: int = 2
    default_max_spread_bps: float = 20.0
    default_max_quote_age_seconds: float = 8.0

    @property
    def trade_seconds(self) -> int:
        return self.bar_seconds * self.trade_every_bars

    def validate_cadence(self) -> None:
        try:
            max_daily_notional = float(self.default_max_daily_notional)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Maximum daily notional must be finite and positive") from exc
        if isinstance(self.default_max_daily_notional, bool) or not (
            math.isfinite(max_daily_notional) and max_daily_notional > 0
        ):
            raise ValueError("Maximum daily notional must be finite and positive")
        try:
            max_quote_age = float(self.default_max_quote_age_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Maximum quote age must be finite and positive") from exc
        if isinstance(self.default_max_quote_age_seconds, bool) or not (
            math.isfinite(max_quote_age) and max_quote_age > 0
        ):
            raise ValueError("Maximum quote age must be finite and positive")
        if not 0.25 <= self.poll_seconds <= 5.0:
            raise ValueError("Quote request target must be between 0.25 and 5 seconds")
        if not 2.0 <= self.reconcile_seconds <= 60.0:
            raise ValueError("Account reconciliation must be between 2 and 60 seconds")
        if not 1 <= self.bar_seconds <= 300:
            raise ValueError("Analysis bar must be between 1 and 300 seconds")
        if not 2 <= self.trade_every_bars <= 120:
            raise ValueError("Trade decisions must be separated by 2 to 120 analysis bars")
        if self.strategy_name not in STRATEGY_NAMES:
            raise ValueError(f"Unknown runtime strategy: {self.strategy_name}")
        execution_profile(self)
        if self.settlement_model not in {"cash_t1", "instant"}:
            raise ValueError("Settlement model must be cash_t1 or instant")


def migrate_legacy_data(legacy: Path, destination: Path) -> list[Path]:
    """Copy legacy Momentum Trader state once without deleting the recoverable originals."""
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    mappings = {
        "config.json": "config.json",
        "momentum_trader.db": "grande_alpha.db",
        "momentum_trader.log": "legacy_momentum_trader.log",
    }
    if not legacy.exists() or legacy.resolve() == destination.resolve():
        return copied
    for old_name, new_name in mappings.items():
        source = legacy / old_name
        target = destination / new_name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)
            copied.append(target)
    return copied


def data_dir() -> Path:
    """Return the application-data location without creating or migrating anything."""
    return user_data_path(APP_NAME, appauthor=False)


def ensure_data_dir() -> Path:
    """Create the application-data location for an operation that will write to it."""
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    """Read the saved configuration without writing defaults or silently upgrading it."""
    path = path or config_path()
    if not path.exists():
        return AppConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a JSON object")
    if int(raw.get("cadence_version", 0)) < CADENCE_VERSION:
        raise ConfigUpgradeRequired(
            "Configuration needs an explicit upgrade; run 'grande-alpha-cli config upgrade' first"
        )
    allowed = set(AppConfig.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration settings: {', '.join(sorted(unknown))}")
    config = AppConfig(**raw)
    config.validate_cadence()
    return config


def migrate_config_payload(raw: dict) -> dict:
    """Upgrade pre-cadence settings; those releases had no timing controls in the UI."""
    upgraded = dict(raw)
    version = int(upgraded.get("cadence_version", 0))
    if version < 1:
        upgraded.update(
            poll_seconds=1.0,
            reconcile_seconds=5.0,
            bar_seconds=5,
        )
    if version < 2:
        upgraded["trade_every_bars"] = 3
    if version < 3:
        upgraded.update(
            market_hours="regular_hours",
            order_type="market",
            time_in_force="gfd",
            limit_offset_bps=10.0,
        )
    if version < 4:
        upgraded["settlement_model"] = "cash_t1"
    if version < 5:
        upgraded.setdefault("strategy_name", "cash")
    if version < 6 and upgraded.get("market_history_retention_days", 90) == 90:
        upgraded["market_history_retention_days"] = 240
    upgraded["cadence_version"] = CADENCE_VERSION
    return upgraded


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Atomically persist a validated configuration to an explicitly writable location."""
    config.validate_cadence()
    path = path or (ensure_data_dir() / "config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".json.pending")
    pending.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    pending.replace(path)


def upgrade_config(path: Path | None = None) -> Path | None:
    """Back up and upgrade one saved configuration; returns its backup or ``None`` for defaults.

    This is deliberately separate from normal reads and startup. It never discovers or copies a
    legacy application directory; call :func:`migrate_legacy_data` explicitly when that recovery
    is actually intended.
    """
    path = path or config_path()
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a JSON object")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}.{timestamp}.backup{path.suffix}")
    shutil.copy2(path, backup)
    migrated = migrate_config_payload(raw)
    allowed = set(AppConfig.__dataclass_fields__)
    unknown = set(migrated) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration settings: {', '.join(sorted(unknown))}")
    save_config(AppConfig(**migrated), path)
    return backup

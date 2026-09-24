from __future__ import annotations

import json
import math
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from platformdirs import user_data_path

from grande_alpha.domain.execution_profile import execution_profile
from grande_alpha.strategy.core import STRATEGY_NAMES

APP_NAME = "GRANDEAlpha"
DISPLAY_NAME = "GRANDE Alpha"
LEGACY_APP_NAME = "MomentumTrader"
MCP_URL = "https://agent.robinhood.com/mcp/trading"
ONBOARDING_VERSION = 1
DISCLOSURE_VERSION = "2026-08"
CADENCE_VERSION = 6
CONFIG_SCHEMA_VERSION = 1

CONFIG_SECTIONS = {
    "broker": ("broker_connection_enabled", "live_trading_enabled"),
    "data": ("remote_market_data_enabled", "market_history_retention_days",
             "poll_seconds", "reconcile_seconds", "bar_seconds"),
    "strategy": ("strategy_name", "trade_every_bars", "warmup_bars", "fast_ema",
                 "slow_ema", "trend_threshold_bps", "momentum_bars", "hard_stop_pct",
                 "take_profit_pct", "max_hold_minutes", "no_trade_open_minutes",
                 "no_trade_close_minutes"),
    "execution": ("market_hours", "order_type", "time_in_force", "limit_offset_bps",
                  "settlement_model", "default_session_minutes"),
    "risk": ("default_max_order_notional", "default_max_daily_notional",
             "default_max_total_exposure", "default_max_daily_loss", "default_max_trades",
             "default_max_orders_per_minute", "default_max_spread_bps",
             "default_max_quote_age_seconds"),
    "storage": ("personal_ledger_enabled",),
    "desktop": ("onboarding_version", "disclosure_version"),
}


class ConfigUpgradeRequired(RuntimeError):
    """Raised when a saved configuration needs the explicit upgrade operation."""


@dataclass
class BrokerConfig:
    broker_connection_enabled: bool = False
    live_trading_enabled: bool = False


@dataclass
class DataConfig:
    remote_market_data_enabled: bool = False
    market_history_retention_days: int = 240
    poll_seconds: float = 1.0
    reconcile_seconds: float = 5.0
    bar_seconds: int = 5


@dataclass
class StrategyConfig:
    strategy_name: str = "cash"
    trade_every_bars: int = 3
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


@dataclass
class ExecutionConfig:
    market_hours: str = "regular_hours"
    order_type: str = "market"
    time_in_force: str = "gfd"
    limit_offset_bps: float = 10.0
    settlement_model: str = "cash_t1"
    default_session_minutes: int = 60


@dataclass
class RiskConfig:
    default_max_order_notional: float = 25.0
    default_max_daily_notional: float = 50.0
    default_max_total_exposure: float = 40.0
    default_max_daily_loss: float = 2.0
    default_max_trades: int = 6
    default_max_orders_per_minute: int = 2
    default_max_spread_bps: float = 20.0
    default_max_quote_age_seconds: float = 8.0


@dataclass
class StorageConfig:
    personal_ledger_enabled: bool = False


@dataclass
class DesktopConfig:
    onboarding_version: int = 0
    disclosure_version: str = ""


SECTION_TYPES = {
    "broker": BrokerConfig,
    "data": DataConfig,
    "strategy": StrategyConfig,
    "execution": ExecutionConfig,
    "risk": RiskConfig,
    "storage": StorageConfig,
    "desktop": DesktopConfig,
}
SETTING_SECTION = {name: section for section, names in CONFIG_SECTIONS.items() for name in names}


def flat_config_values(config: AppConfig) -> dict[str, object]:
    """Compatibility snapshot for consumers still expecting the former flat shape."""
    return {"cadence_version": config.cadence_version,
            **{name: getattr(getattr(config, section), name)
               for section, names in CONFIG_SECTIONS.items() for name in names}}


@dataclass(init=False)
class AppConfig:
    """Typed settings sections; flat access remains a temporary caller adapter."""

    cadence_version: int = CADENCE_VERSION
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    data: DataConfig = field(default_factory=DataConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    desktop: DesktopConfig = field(default_factory=DesktopConfig)

    def __init__(self, *, cadence_version: int = CADENCE_VERSION, **values: object) -> None:
        object.__setattr__(self, "cadence_version", cadence_version)
        for section, section_type in SECTION_TYPES.items():
            supplied = values.pop(section, None)
            if supplied is not None and not isinstance(supplied, section_type):
                raise ValueError(f"{section} must be a {section_type.__name__}")
            object.__setattr__(self, section, supplied if supplied is not None else section_type())
        for name, value in values.items():
            if name not in SETTING_SECTION:
                raise ValueError(f"Unknown configuration setting: {name}")
            setattr(self, name, value)

    def __getattr__(self, name: str) -> object:
        section = SETTING_SECTION.get(name)
        if section is None:
            raise AttributeError(name)
        return getattr(getattr(self, section), name)

    def __setattr__(self, name: str, value: object) -> None:
        section = SETTING_SECTION.get(name)
        if section is not None:
            setattr(getattr(self, section), name, value)
        else:
            object.__setattr__(self, name, value)

    def with_flat_updates(self, **updates: object) -> AppConfig:
        """Return an independent copy with validated flat-name updates."""
        copied = AppConfig(**flat_config_values(self))
        for name, value in updates.items():
            if name not in SETTING_SECTION:
                raise ValueError(f"Unknown configuration setting: {name}")
            setattr(copied, name, value)
        copied.validate_cadence()
        return copied

    @property
    def trade_seconds(self) -> int:
        return self.bar_seconds * self.trade_every_bars

    def validate_cadence(self) -> None:
        for section, section_type in SECTION_TYPES.items():
            if not isinstance(getattr(self, section), section_type):
                raise ValueError(f"{section} must be a {section_type.__name__}")
        for name in ("broker_connection_enabled", "live_trading_enabled",
                     "remote_market_data_enabled", "personal_ledger_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        if not isinstance(self.disclosure_version, str):
            raise ValueError("disclosure_version must be text")
        for name in ("onboarding_version", "market_history_retention_days", "bar_seconds",
                     "trade_every_bars",
                     "warmup_bars", "fast_ema", "slow_ema", "momentum_bars",
                     "max_hold_minutes", "default_session_minutes", "default_max_trades",
                     "default_max_orders_per_minute"):
            value = getattr(self, name)
            minimum = 0 if name == "onboarding_version" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer of at least {minimum}")
        for name in ("no_trade_open_minutes", "no_trade_close_minutes"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        for name in ("hard_stop_pct", "take_profit_pct", "default_max_order_notional",
                     "default_max_daily_notional", "default_max_total_exposure",
                     "default_max_daily_loss", "default_max_spread_bps",
                     "default_max_quote_age_seconds"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")
        for name in ("trend_threshold_bps", "limit_offset_bps"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("poll_seconds", "reconcile_seconds"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise ValueError(f"{name} must be a finite number of seconds")
        if self.hard_stop_pct >= 1 or self.take_profit_pct >= 1:
            raise ValueError("Hard stop and take profit must be fractions below 1")
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
    if "schema_version" not in raw:
        raise ConfigUpgradeRequired(
            "Configuration needs an explicit upgrade; run 'grande-alpha-cli config upgrade' first"
        )
    if type(raw["schema_version"]) is not int or raw["schema_version"] != CONFIG_SCHEMA_VERSION:
        raise ValueError(f"Unsupported configuration schema version (expected {CONFIG_SCHEMA_VERSION})")
    unknown = set(raw) - {"schema_version", *CONFIG_SECTIONS}
    if unknown:
        raise ValueError(f"Unknown configuration sections: {', '.join(sorted(unknown))}")
    missing = set(CONFIG_SECTIONS) - set(raw)
    if missing:
        raise ValueError(f"Missing configuration sections: {', '.join(sorted(missing))}")
    values: dict = {}
    for section, names in CONFIG_SECTIONS.items():
        saved = raw[section]
        if not isinstance(saved, dict):
            raise ValueError(f"Configuration section '{section}' must be an object")
        unknown_settings = set(saved) - set(names)
        if unknown_settings:
            raise ValueError(
                f"Unknown {section} settings: {', '.join(sorted(unknown_settings))}"
            )
        values[section] = SECTION_TYPES[section](**saved)
    config = AppConfig(**values)
    config.validate_cadence()
    return config


def config_document(config: AppConfig) -> dict:
    """Serialize one validated settings object without duplicating its defaults."""
    config.validate_cadence()
    values = asdict(config)
    grouped = {section: {name: values[section][name] for name in names}
               for section, names in CONFIG_SECTIONS.items()}
    grouped["schema_version"] = CONFIG_SCHEMA_VERSION
    return grouped


def migrate_config_payload(raw: dict) -> dict:
    """Upgrade pre-cadence settings; those releases had no timing controls in the UI."""
    upgraded = dict(raw)
    version = upgraded.get("cadence_version", 0)
    if type(version) is not int or version < 0:
        raise ValueError("cadence_version must be a nonnegative integer")
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


def save_config(config: AppConfig, path: Path | None = None, *, _upgrading: bool = False) -> None:
    """Atomically persist a validated configuration to an explicitly writable location."""
    document = config_document(config)
    path = path or (ensure_data_dir() / "config.json")
    if path.exists() and not _upgrading:
        # A normal settings save must never replace an old or malformed file
        # before the operator has a validated, backed-up upgrade.
        load_config(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f"{path.name}.{uuid.uuid4().hex}.pending")
    try:
        with pending.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


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
    if type(raw.get("schema_version")) is int and raw["schema_version"] == CONFIG_SCHEMA_VERSION:
        load_config(path)
        return None
    if "schema_version" in raw:
        raise ValueError(f"Unsupported configuration schema version (expected {CONFIG_SCHEMA_VERSION})")
    migrated = migrate_config_payload(raw)
    allowed = {"cadence_version", *SETTING_SECTION}
    unknown = set(migrated) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration settings: {', '.join(sorted(unknown))}")
    config = AppConfig(**migrated)
    config.validate_cadence()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.stem}.{timestamp}.backup{path.suffix}")
    shutil.copy2(path, backup)
    save_config(config, path, _upgrading=True)
    return backup

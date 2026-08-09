from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_data_path

APP_NAME = "MomentumTrader"
MCP_URL = "https://agent.robinhood.com/mcp/trading"


@dataclass
class AppConfig:
    poll_seconds: float = 2.0
    bar_seconds: int = 60
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
    default_max_total_exposure: float = 40.0
    default_max_daily_loss: float = 2.0
    default_max_trades: int = 6
    default_max_orders_per_minute: int = 2
    default_max_spread_bps: float = 20.0
    default_max_quote_age_seconds: float = 8.0


def data_dir() -> Path:
    path = user_data_path(APP_NAME, appauthor=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        config = AppConfig()
        save_config(config)
        return config
    raw = json.loads(path.read_text(encoding="utf-8"))
    allowed = AppConfig.__dataclass_fields__.keys()
    return AppConfig(**{key: value for key, value in raw.items() if key in allowed})


def save_config(config: AppConfig) -> None:
    config_path().write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


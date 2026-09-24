"""Small, presentation-only helpers shared by CLI command groups."""

from __future__ import annotations

import json
from typing import Any


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str, sort_keys=True))


def sandbox_metric_rows(metrics: dict[str, Any]) -> list[list[str]]:
    return [
        ["Final equity", f"${float(metrics.get('final_equity', 0)):,.2f}"],
        ["Net P/L", f"${float(metrics.get('net_pnl', 0)):+,.2f}"],
        ["Return", f"{float(metrics.get('return_pct', 0)):+.2f}%"],
        ["Max drawdown", f"{float(metrics.get('max_drawdown_pct', 0)):.2f}%"],
        ["Round trips", str(metrics.get("round_trips", 0))],
        ["Win rate", f"{float(metrics.get('win_rate', 0)):.1f}%"],
        ["Profit factor", f"{float(metrics.get('profit_factor', 0)):.2f}"],
        ["Expectancy", f"${float(metrics.get('expectancy', 0)):+.4f}"],
        ["Sharpe", f"{float(metrics.get('sharpe', 0)):+.2f}"],
        ["Sortino", f"{float(metrics.get('sortino', 0)):+.2f}"],
        ["Exposure", f"{float(metrics.get('exposure_pct', 0)):.1f}%"],
        ["Execution cost", f"${float(metrics.get('total_execution_cost', 0)):,.4f}"],
        ["Ending position", str(metrics.get("ending_position") or "cash")],
    ]

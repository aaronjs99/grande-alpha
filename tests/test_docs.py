from pathlib import Path

from momentum_trader.config import AppConfig

ROOT = Path(__file__).resolve().parents[1]


def test_required_operating_docs_exist() -> None:
    required = {
        "README.md",
        "MONDAY_RUNBOOK.md",
        "STRATEGY_AND_PROFIT.md",
        "SAFETY_AND_COMPLIANCE.md",
        "TROUBLESHOOTING.md",
        "DAILY_JOURNAL_TEMPLATE.md",
    }
    assert required <= {path.name for path in (ROOT / "docs").glob("*.md")}


def test_strategy_doc_matches_critical_defaults() -> None:
    text = (ROOT / "docs" / "STRATEGY_AND_PROFIT.md").read_text(encoding="utf-8")
    config = AppConfig()
    assert f"| Warm-up | {config.warmup_bars} completed bars |" in text
    assert f"| Fast EMA | {config.fast_ema} bars |" in text
    assert f"| Slow EMA | {config.slow_ema} bars |" in text
    assert f"| Hard position stop | −{config.hard_stop_pct:.1%}" in text
    assert f"| Take-profit | +{config.take_profit_pct:.1%}" in text


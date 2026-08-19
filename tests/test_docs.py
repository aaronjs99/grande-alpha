from pathlib import Path

from grande_alpha.config import AppConfig

ROOT = Path(__file__).resolve().parents[1]


def test_required_operating_docs_exist() -> None:
    required = {
        "README.md",
        "MONDAY_RUNBOOK.md",
        "STRATEGY_AND_PROFIT.md",
        "SAFETY_AND_COMPLIANCE.md",
        "TROUBLESHOOTING.md",
        "DAILY_JOURNAL_TEMPLATE.md",
        "SYSTEM_ARCHITECTURE.md",
        "GRANDE_RESEARCH_FUND.md",
        "SANDBOX.md",
        "EVIDENCE_LAB.md",
        "DATASET_READINESS.md",
        "SHADOW_MODE.md",
        "LOW_LATENCY_EXECUTION.md",
        "ACTIVATION_CHECKLIST.md",
        "WINDOWS_INSTALLATION.md",
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


def test_activation_doc_separates_automation_consent_and_external_review() -> None:
    text = (ROOT / "docs" / "ACTIVATION_CHECKLIST.md").read_text(encoding="utf-8")

    assert "structurally read-only" in text
    assert "APP CHECK" in text and "APP GATE" in text
    assert "Jurisdiction & account suitability" in text
    assert "GRANDE_ALPHA_EXTERNAL_GUIDANCE_LINKS" in text
    assert "Robinhood Support" in text
    assert "Apply bounded pilot settings" in text
    assert "cannot grant, schedule, review, place, or cancel" in text

"""Capture the agent workspace with explicitly synthetic data and no network calls."""

from __future__ import annotations

import argparse
import math
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from PySide6.QtWidgets import QApplication
from test_responsive_ui import DisabledBroker

from grande_alpha.agent_ledger import AgentBudget
from grande_alpha.agent_models import AgentDecision, AgentSnapshot, AssetClass, Instrument
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController, TradingSnapshot
from grande_alpha.models import Account, Portfolio, Quote, utc_now
from grande_alpha.storage import AuditStore
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.themes import apply_application_theme


def capture(path: Path, width: int = 1366, height: int = 900, *, budget: bool = False, prompts: bool = False, chatgpt_help: bool = False, chatgpt_step: int = 1, theme: str = "light", paper: bool = False, compact_chat: bool = False) -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        store = AuditStore(Path(directory) / "synthetic.db")
        controller = TradingController(DisabledBroker(), AppConfig(broker_connection_enabled=True), store)
        window = MainWindow(controller, controller.config)
        apply_application_theme(theme)
        window._apply_theme_widgets()
        window.resize(width, height)
        window.setWindowTitle("GRANDE Alpha · Synthetic UI verification · No real account data")
        widget = window.agent_widget
        window.tabs.setCurrentWidget(widget)
        now = datetime(2026, 9, 23, 15, tzinfo=UTC)
        store.agent_ledger.save_budget("synthetic", AgentBudget(Decimal(5), Decimal(10), Decimal(12), Decimal(1)), now=now)
        values = [1000 + 0.12 * math.sin(i / 4) + 0.06 * math.cos(i / 1.7) for i in range(40)]
        values[-1] = 1000.1
        for index, balance in enumerate(values):
            widget.update_account(
                TradingSnapshot(
                    connected=True,
                    account=Account("synthetic", "Synthetic", "cash", True, "active"),
                    portfolio=Portfolio(balance, 900, 900, crypto_buying_power=850, crypto_value=25),
                    last_reconcile_at=now + timedelta(seconds=index * 30),
                    agent_budget=controller.agent_executor.status_payload("synthetic"),
                )
            )
        decisions = []
        for market, symbol, price, action, reason in (
            (AssetClass.EQUITY, "AAPL", 200.0, "hold", "Observed movement is below the research threshold"),
            (
                AssetClass.EQUITY,
                "QQQ",
                500.0,
                "buy",
                "Observed midpoint change +24 bps; research threshold 20 bps",
            ),
            (AssetClass.CRYPTO, "BTC-USD", 90000.0, "hold", "Collecting distinct broker observations"),
            (AssetClass.CRYPTO, "ETH-USD", 3000.0, "hold", "Spread exceeds this market's research limit"),
        ):
            decisions.append(
                AgentDecision(
                    Instrument(market, symbol),
                    Quote(symbol, price, price + 0.01, price, now),
                    action,
                    reason,
                    "Blocked" if symbol == "ETH-USD" else "Data checks passed",
                    4,
                )
            )
        widget.update_agent(
            AgentSnapshot(
                cycle=4,
                phase="Waiting",
                observed_at=now,
                decisions=tuple(decisions),
                market_status={
                    "equity": "Synthetic fixture · 2 candidates",
                    "crypto": "Synthetic fixture · 2 candidates",
                },
            )
        )
        widget.mode.setText("SYNTHETIC UI VERIFICATION · PROPOSALS ONLY · NO REAL TRADES")
        if paper:
            from test_agent_paper import decision

            now = utc_now()
            controller.save_agent_preferences(replace(controller.agent.settings, local_ai_model="qwen2.5:3b"))
            book = controller.agent.paper
            book.start("broker_quotes", 1000, 100)
            controller.agent.paper_source = "broker_quotes"
            for index in range(90):
                at = now - timedelta(seconds=(89 - index) * 10)
                price = 100 + math.sin(index / 14) * 2 - index * .015
                action = "buy" if index in (2, 3, 38, 39) else "exit" if index in (30, 31, 80, 81) else "hold"
                book.consume([decision(action, at, price, price + .02)], at, 15)
            report = book.summary(active=True, now=now)
            events = tuple({"id": i + 1, "at": (now - timedelta(seconds=(5 - i) * 2)).isoformat(), "cycle": 90,
                            "from": sender, "to": to, "kind": kind, "message": message}
                           for i, (sender, to, kind, message) in enumerate((
                               ("ZARA", "NOVA", "BOOK", "Paper portfolio updated"),
                               ("ORIN", "VELA", "SCAN", "Checking selected crypto pairs"),
                               ("RUNE", "ZARA", "FILL", "Latest simulated exit recorded"),
                               ("KADE", "RUNE", "RISK", "Trading costs checked"),
                               ("VELA", "KADE", "RISK", "Reviewing the price signal"),
                               ("NOVA", "VELA", "SCAN", "Scanning stock watchlist"),
                           )))
            controller.agent.snapshot = AgentSnapshot(
                running=True, cycle=90, phase="Waiting", observed_at=now, paper=report, session_id=report["session_id"],
                analyst="Adaptive trend", decisions=(decision("hold", now, 99, 99.02),), team_events=events,
                team_status={"NOVA": "Scanning stocks", "ORIN": "Loading crypto pairs", "VELA": "Reading observations",
                             "KADE": "Checking quotes and limits", "RUNE": "Waiting for eligible signals", "ZARA": "Tracking paper P&L"})
            widget.update_agent(controller.agent.snapshot)
            widget.mode.setText("SYNTHETIC UI PREVIEW")
            widget.chat.transcript.appendPlainText("You: Why are we losing money? Reduce our exposure.")
            widget.chat.transcript.appendPlainText("Local AI: The latest paper exits recorded losses. We can reduce exposure while reviewing the trades and their costs.")
            widget.chat.preview.setText("Max positions: 4 → 2\nVirtual exposure: 40% → 20%\nExisting exit rules stay active.")
            widget.chat.review_box.show()
            widget.chat.apply.setEnabled(True)
        widget.budget_toggle.setChecked(budget)
        widget.prompts_toggle.setChecked(prompts)
        if prompts:
            widget.briefs["team"].setText("Compare observed moves and spreads; explain uncertainty")
            widget.briefs["equity"].setText("Focus on stock quote quality and momentum")
            widget.briefs["crypto"].setText("Focus on crypto volatility and spread changes")
        window.show()
        app.processEvents()
        if compact_chat:
            widget._show_compact_chat(True)
            app.processEvents()
        if paper:
            for name in ("NOVA", "ORIN", "VELA", "KADE"):
                widget.team_cards[name][0]._set_level(.8)
            app.processEvents()
        if prompts:
            widget.ensureWidgetVisible(widget.prompt_box)
            app.processEvents()
        if budget:
            widget.ensureWidgetVisible(widget.save_budget)
            app.processEvents()
        path.parent.mkdir(parents=True, exist_ok=True)
        target = window
        if chatgpt_help:
            widget.chatgpt_setup.click()
            app.processEvents()
            target = widget._chatgpt_help
            # Captures show a synthetic path instead of this machine's environment.
            target.command.setPlainText("/example/grande-alpha/.venv/bin/python -m grande_alpha.agent_mcp --bridge /example/data/agent-mcp.db")
            # Preview a page without writing connection settings or granting access.
            target._step(chatgpt_step - 1)
            app.processEvents()
            assert not controller.agent_bridge.session
            assert not controller.agent.snapshot.running
            assert controller.risk.grant is None
        if not target.grab().save(str(path)):
            raise RuntimeError("Could not save agent screenshot")
        window.close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/images/agent-workspace.png"))
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument("--budget", action="store_true")
    parser.add_argument("--prompts", action="store_true")
    parser.add_argument("--chatgpt-help", action="store_true")
    parser.add_argument("--chatgpt-step", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--paper", action="store_true", help="Preview actual simulated ledger results and a sample chat; no network")
    parser.add_argument("--compact-chat", action="store_true")
    args = parser.parse_args()
    capture(args.output, args.width, args.height, budget=args.budget, prompts=args.prompts, chatgpt_help=args.chatgpt_help, chatgpt_step=args.chatgpt_step, theme=args.theme, paper=args.paper, compact_chat=args.compact_chat)

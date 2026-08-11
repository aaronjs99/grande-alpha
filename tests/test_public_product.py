import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
)

from grande_alpha import __version__
from grande_alpha.broker.base import Broker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.evidence import (
    EVIDENCE_POLICY_VERSION,
    REQUIRED_LIVE_GATE_NAMES,
    strategy_fingerprint,
)
from grande_alpha.historical import deterministic_demo
from grande_alpha.models import Account, LiveGrant, Portfolio, Position, Quote, Regime, Signal, utc_now
from grande_alpha.privacy import export_diagnostics
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine
from grande_alpha.storage import AuditStore
from grande_alpha.ui.dialogs import LiveGrantDialog
from grande_alpha.ui.glossary import TERM_HELP, ExplainedLabel, GlossaryDialog
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.sandbox_widget import SandboxWidget
from grande_alpha.ui.settings_dialog import LIVE_PHRASE, SettingsDialog


class DisabledBroker(Broker):
    async def connect(self):
        raise AssertionError("Disabled broker must not be called")

    async def disconnect(self):
        return None

    async def get_accounts(self):
        return []

    async def get_portfolio(self, account_number):
        raise AssertionError

    async def get_quotes(self, symbols):
        raise AssertionError

    async def get_positions(self, account_number):
        raise AssertionError

    async def get_orders(self, account_number):
        raise AssertionError

    async def review_order(self, account_number, intent):
        raise AssertionError

    async def place_order(self, account_number, intent):
        raise AssertionError

    async def cancel_order(self, account_number, order_id):
        raise AssertionError


def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_public_defaults_are_research_only() -> None:
    config = AppConfig()
    assert not config.broker_connection_enabled
    assert not config.live_trading_enabled
    assert not config.remote_market_data_enabled
    assert not config.personal_ledger_enabled
    assert config.poll_seconds == 1.0
    assert config.reconcile_seconds == 5.0
    assert config.bar_seconds == 5
    assert config.trade_every_bars == 3
    assert config.trade_seconds == 15
    assert config.market_hours == "regular_hours"
    assert config.order_type == "market"
    assert config.settlement_model == "cash_t1"
    assert __version__ == "0.13.0"


def test_controller_constructs_whole_share_limit_intents_from_authorized_route(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    now = utc_now()
    portfolio = Portfolio(500, 500, 500)
    controller.risk.arm(
        LiveGrant(
            account_number="123",
            starts_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=30),
            max_order_notional=250,
            max_total_exposure=400,
            max_daily_loss=20,
            max_trades=5,
            max_orders_per_minute=2,
            max_spread_bps=20,
            max_quote_age_seconds=8,
            market_hours="extended_hours",
            order_type="limit",
            time_in_force="gfd",
            limit_offset_bps=10,
        ),
        portfolio,
    )
    quote = Quote("TQQQ", 100, 100.05, 100.02, now)

    intent = controller._execution_intent("TQQQ", "buy", quote, "test", notional=250)

    assert intent.market_hours == "extended_hours"
    assert intent.order_type == "limit"
    assert intent.quantity == 2
    assert intent.limit_price == 100.16
    assert intent.dollar_amount is None
    store.close()


def test_main_window_starts_with_independent_low_latency_clocks(tmp_path) -> None:
    qt_app()
    store = AuditStore(tmp_path / "audit.db")
    config = AppConfig(poll_seconds=0.25, reconcile_seconds=2.0, bar_seconds=1)
    controller = TradingController(DisabledBroker(), config, store)
    window = MainWindow(controller, config)

    assert window.timer.interval() == 250
    assert window.reconcile_timer.interval() == 2_000
    assert controller.bar_builder.seconds == 1
    assert controller.config.trade_seconds == 3
    assert not controller.snapshot.connected

    window.close()
    store.close()


def test_main_window_exposes_complete_desktop_navigation(tmp_path) -> None:
    qt_app()
    store = AuditStore(tmp_path / "audit.db")
    config = AppConfig(broker_connection_enabled=True)
    controller = TradingController(DisabledBroker(), config, store)
    window = MainWindow(controller, config)

    menu_titles = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert menu_titles == ["File", "View", "Broker", "Research", "Safety", "Help"]
    assert window.settings_button.text() == "Settings && Permissions"
    assert window.minimumWidth() >= 1180
    assert window.minimumHeight() >= 720
    assert window.market_splitter.minimumHeight() >= 220
    assert window.refresh_action.shortcut().toString() == "F5"
    assert window.full_screen_action.shortcut().toString() == "F11"
    assert window.stop_cancel_action.shortcut().toString() == "Ctrl+Shift+X"
    assert window.glossary_action.shortcut().toString() == "F1"
    assert isinstance(window.account_card.title, ExplainedLabel)
    assert "dedicated Robinhood account" in window.account_card.title.toolTip()
    assert window.quotes_table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert all(
        table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
        for table in window.findChildren(QTableWidget)
    )
    assert not window.refresh_action.isEnabled()
    assert not window.flatten_action.isEnabled()
    controller.snapshot.account = Account("123456789", "Agentic", "cash", True, "active")
    window._on_snapshot(controller.snapshot)
    assert "CASH" in window.account_card.title.text()
    assert "cash account" in window.account_card.toolTip().lower()

    window.close()
    store.close()


class CadenceBroker(DisabledBroker):
    def __init__(self) -> None:
        self.quote_calls = 0
        self.quote_started = asyncio.Event()
        self.release_quote = asyncio.Event()

    async def get_quotes(self, symbols):
        self.quote_calls += 1
        self.quote_started.set()
        await self.release_quote.wait()
        now = utc_now()
        return {symbol: Quote(symbol, 100.0, 100.02, 100.01, now) for symbol in symbols}


@pytest.mark.asyncio
async def test_quote_timer_ticks_are_coalesced_while_provider_is_busy(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    broker = CadenceBroker()
    controller = TradingController(broker, AppConfig(), store)
    controller.snapshot.connected = True
    controller.snapshot.account = Account("123456789", "Agentic", "cash", True, "active")

    first = asyncio.create_task(controller.refresh_quotes(evaluate=False))
    await broker.quote_started.wait()
    await controller.refresh_quotes(evaluate=False)
    assert broker.quote_calls == 1
    broker.release_quote.set()
    await first
    assert set(controller.snapshot.quotes) == {"QQQ", "TQQQ", "SQQQ"}
    store.close()


@pytest.mark.asyncio
async def test_disabled_broker_is_blocked_before_adapter_call(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    controller = TradingController(DisabledBroker(), AppConfig(), store)
    with pytest.raises(RuntimeError, match="disabled"):
        await controller.connect()
    store.close()


def test_live_setting_requires_broker_and_exact_phrase() -> None:
    qt_app()
    dialog = SettingsDialog(AppConfig(), live_evidence_ready=True)
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    dialog.live.setChecked(True)
    assert not save.isEnabled()
    dialog.broker.setChecked(True)
    assert not save.isEnabled()
    dialog.live_phrase.setText(LIVE_PHRASE)
    assert save.isEnabled()
    updated = dialog.updated_config()
    assert updated.broker_connection_enabled and updated.live_trading_enabled


def test_live_setting_stays_blocked_without_passing_evidence() -> None:
    qt_app()
    dialog = SettingsDialog(AppConfig(), live_evidence_ready=False)
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    dialog.broker.setChecked(True)
    dialog.live.setChecked(True)
    dialog.live_phrase.setText(LIVE_PHRASE)
    assert not save.isEnabled()
    assert "shadow-only" in dialog.validation.text()
    assert "Uncheck real-order automation" in dialog.validation.text()
    assert "Uncheck real-order automation" in save.toolTip()


def test_glossary_terms_are_dashed_discoverable_and_searchable() -> None:
    qt_app()
    label = ExplainedLabel("Trading session")

    assert "dashed" in label.styleSheet()
    assert "Trading session" in label.toolTip()
    assert label.accessibleName() == "Trading session"
    assert label.accessibleDescription() == TERM_HELP["Trading session"]
    assert label.whatsThis() == TERM_HELP["Trading session"]
    assert label.cursor().shape() == Qt.CursorShape.WhatsThisCursor

    glossary = GlossaryDialog()
    glossary.search.setText("bid-ask")
    visible = [
        glossary.terms.item(row).text()
        for row in range(glossary.terms.count())
        if not glossary.terms.item(row).isHidden()
    ]
    assert "Base spread" in visible
    assert glossary.definition.text()
    glossary.close()


def test_settings_sandbox_and_live_grant_expose_contextual_help(tmp_path) -> None:
    qt_app()
    settings = SettingsDialog(AppConfig(), live_evidence_ready=False)
    settings_terms = {label.text() for label in settings.findChildren(ExplainedLabel)}
    assert {"Trading session", "Completed analysis bar", "Local credential"} <= settings_terms
    assert settings.evidence_status.toolTip()

    store = AuditStore(tmp_path / "glossary.db")
    sandbox = SandboxWidget(store, allow_remote_data=False)
    sandbox_terms = {label.text() for label in sandbox.findChildren(ExplainedLabel)}
    assert {"Source", "Slippage / side", "Hard stop", "Research strategy"} <= sandbox_terms
    assert "Unsettled cash" in TERM_HELP
    assert sandbox.evidence_button.toolTip()
    assert sandbox.gates_table.horizontalHeaderItem(0).toolTip()
    assert sandbox.promotion_label.toolTip()
    sandbox._show_evidence(
        [],
        None,
        SimpleNamespace(
            passed=False,
            status="SHADOW_ONLY",
            dataset_hash="test-dataset-hash",
            gates=[
                SimpleNamespace(
                    name="Data breadth",
                    passed=False,
                    observed="5 sessions",
                    requirement="At least 120 sessions",
                )
            ],
        ),
        SimpleNamespace(
            trials=100,
            median_return_pct=0.0,
            percentile_10=-1.0,
            percentile_90=1.0,
            strategy_percentile=50.0,
        ),
    )
    assert "5 sessions" in sandbox.gates_table.item(0, 0).toolTip()
    assert "0/1 gates passed" in sandbox.promotion_label.text()
    assert "not a progress score" in sandbox.evidence_overview.text()
    assert "Use at least 120 complete market sessions" in sandbox.gate_inspector.toPlainText()
    assert sandbox.gates_table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert sandbox.gates_table.columnWidth(1) < sandbox.gates_table.columnWidth(2)
    sandbox.gates_table.horizontalHeader().resizeSection(1, 92)
    assert sandbox.gates_table.columnWidth(1) == 92
    sandbox.reset_table_columns()
    assert sandbox.gates_table.columnWidth(1) == 64
    assert "Right-click" in sandbox.gates_table.horizontalHeader().toolTip()

    account = Account("123456789", "Agentic", "cash", True, "active")
    grant = LiveGrantDialog(account, Portfolio(100, 100, 100), AppConfig())
    grant_terms = {label.text() for label in grant.findChildren(ExplainedLabel)}
    assert {"Session duration", "Max session loss", "Authorized session"} <= grant_terms
    authorize = grant.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not authorize.isEnabled()
    assert "Check the attestation" in authorize.toolTip()
    assert any("cannot continuously recycle" in label.text() for label in grant.findChildren(QLabel))

    grant.close()
    sandbox.close()
    store.close()
    settings.close()


def test_sandbox_restores_latest_evidence_receipt_with_next_step(tmp_path) -> None:
    qt_app()
    store = AuditStore(tmp_path / "saved-evidence.db")
    promotion_id = store.record_research_promotion(
        dataset_hash="saved-dataset",
        strategy_fingerprint="saved-strategy",
        policy_version=EVIDENCE_POLICY_VERSION,
        status="SHADOW_ONLY",
        source="Deterministic offline scenario",
        replay_end="2026-08-10T20:00:00+00:00",
        gates=[
            {
                "name": "Data breadth",
                "passed": False,
                "observed": "5 sessions",
                "requirement": "At least 120 sessions",
            }
        ],
        risk_envelope={},
    )

    sandbox = SandboxWidget(store, allow_remote_data=False)

    assert f"receipt #{promotion_id}" in sandbox.evidence_overview.text()
    assert "5 sessions" in sandbox.gates_table.item(0, 2).text()
    assert "Use at least 120 complete market sessions" in sandbox.gate_inspector.toPlainText()
    assert "Loaded evidence receipt" in sandbox.status.text()
    sandbox.close()
    store.close()


def test_settings_dialog_is_scrollable_and_explains_agentic_account_scope() -> None:
    qt_app()
    dialog = SettingsDialog(AppConfig(broker_connection_enabled=True), live_evidence_ready=False)

    assert isinstance(dialog.scroll, QScrollArea)
    assert dialog.scroll.widgetResizable()
    assert dialog.scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert dialog.minimumWidth() >= 780
    assert dialog.minimumHeight() >= 650
    assert dialog.broker.text() == "Connect Robinhood broker data"
    assert dialog.broker_note.wordWrap()
    assert "active Agentic account" in dialog.broker_note.text()
    assert "regular investing account is not selected" in dialog.broker_note.text()
    assert "LOCKED" in dialog.evidence_status.text()
    assert dialog.remote_data_note.wordWrap()
    assert dialog.credential_note.wordWrap()
    assert "t_analysis = 5s" in dialog.cadence_note.text()
    assert "t_trade = 15s" in dialog.cadence_note.text()
    dialog.market_hours.setCurrentIndex(dialog.market_hours.findData("extended_hours"))
    assert dialog.order_type.currentData() == "limit"
    assert "whole-share" in dialog.routing_note.text()
    updated = dialog.updated_config()
    assert updated.market_hours == "extended_hours"
    assert updated.order_type == "limit"
    dialog.close()


def test_trade_decision_requires_multiple_completed_analysis_bars(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    controller = TradingController(
        DisabledBroker(),
        AppConfig(bar_seconds=5, trade_every_bars=3),
        store,
    )

    controller._analysis_sequence = 2
    assert not controller._trade_decision_due()
    controller._analysis_sequence = 3
    assert controller._trade_decision_due()
    store.close()


@pytest.mark.asyncio
async def test_due_trade_tick_records_exact_pair_action_without_forcing_turnover(
    tmp_path, monkeypatch
) -> None:
    store = AuditStore(tmp_path / "audit.db")
    controller = TradingController(
        DisabledBroker(),
        AppConfig(bar_seconds=5, trade_every_bars=3),
        store,
    )
    now = utc_now().replace(hour=16, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("grande_alpha.controller.utc_now", lambda: now)
    controller._live_automation_current = lambda: True
    controller.snapshot.signal = Signal(Regime.BULLISH, 1.0, "Test bullish signal", now)
    controller.snapshot.last_analysis_at = now
    controller.snapshot.positions = [Position("TQQQ", 0.1, 0.1, 100.0)]
    controller.snapshot.quotes = {"TQQQ": Quote("TQQQ", 100.0, 100.02, 100.01, now)}
    controller._analysis_sequence = 3

    await controller._evaluate_and_trade()

    assert controller.snapshot.pair_action_id == 4
    assert controller.snapshot.pair_action_label == "(0,0)"
    receipt = store.recent_receipts(1)[0]
    assert receipt["category"] == "pair_decision"
    payload = json.loads(receipt["payload_json"])
    assert payload["action_t"] == payload["action_s"] == 0
    assert payload["nominal_analysis_seconds"] == 5
    assert payload["nominal_trade_seconds"] == 15
    assert payload["market_hours"] == "regular_hours"
    assert payload["order_type"] == "market"
    store.close()


def test_controller_requires_matching_current_evidence_before_live_authority(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    config = AppConfig(broker_connection_enabled=True, live_trading_enabled=True)
    controller = TradingController(DisabledBroker(), config, store)
    now = utc_now()
    controller.snapshot.account = Account("123456789", "Agentic", "cash", True, "active")
    controller.snapshot.portfolio = Portfolio(100.0, 100.0, 100.0)
    grant = LiveGrant(
        account_number="123456789",
        starts_at=now,
        expires_at=now + timedelta(hours=1),
        max_order_notional=10.0,
        max_total_exposure=20.0,
        max_daily_loss=2.0,
        max_trades=4,
        max_orders_per_minute=1,
        max_spread_bps=20.0,
        max_quote_age_seconds=8.0,
    )
    with pytest.raises(RuntimeError, match="evidence certificate"):
        controller.authorize_live(grant)

    fingerprint = strategy_fingerprint(config)
    holdout_id = store.reserve_research_holdout(
        dataset_hash="market-history-hash",
        development_hash="development-history-hash",
        holdout_hash="sealed-holdout-hash",
        holdout_start="2026-07-13T13:30:00+00:00",
        holdout_end=now.isoformat(),
        policy_version=EVIDENCE_POLICY_VERSION,
    )
    store.freeze_research_holdout(holdout_id, fingerprint)
    store.claim_research_holdout(holdout_id, fingerprint)
    store.consume_research_holdout(
        holdout_id,
        fingerprint,
        {
            "net_pnl": 1.0,
            "round_trips": 5,
            "profit_factor": 1.2,
            "expectancy": 0.2,
            "max_drawdown_pct": 1.0,
            "ending_position": None,
            "cost_multiplier": 3.0,
            "holdout_hash": "sealed-holdout-hash",
            "holdout_start": "2026-07-13T13:30:00+00:00",
            "holdout_end": now.isoformat(),
        },
    )
    store.record_research_promotion(
        dataset_hash="market-history-hash",
        strategy_fingerprint=fingerprint,
        policy_version=EVIDENCE_POLICY_VERSION,
        status="LIVE_REVIEW_ELIGIBLE",
        source="licensed CSV",
        replay_end=now.isoformat(),
        gates=[{"name": name, "passed": True} for name in sorted(REQUIRED_LIVE_GATE_NAMES)],
        risk_envelope={
            "max_order_notional": 10.0,
            "max_total_exposure": 20.0,
            "max_daily_loss": 2.0,
            "max_trades": 4,
            "max_orders_per_minute": 1,
            "max_spread_bps": 20.0,
        },
        holdout_id=holdout_id,
    )
    controller.authorize_live(grant)
    assert controller.snapshot.live_status == "LIVE"

    controller.snapshot.strategy_running = True
    controller.update_config(replace(config, fast_ema=config.fast_ema + 1))
    assert controller.snapshot.live_status == "LOCKED"
    assert not controller.snapshot.strategy_running
    assert controller.risk.grant is None
    assert controller.strategy.config.fast_ema == config.fast_ema + 1
    assert controller.strategy.last_signal.regime == Regime.FLAT

    controller.update_config(config)
    controller.authorize_live(grant)
    controller.snapshot.strategy_running = True
    controller.live_evidence_ready = lambda grant=None: False
    asyncio.run(controller._evaluate_and_trade())
    assert controller.snapshot.live_status == "LOCKED"
    assert not controller.snapshot.strategy_running
    store.close()


def test_cash_account_rejects_instant_settlement_live_authority(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    config = AppConfig(
        broker_connection_enabled=True,
        live_trading_enabled=True,
        settlement_model="instant",
    )
    controller = TradingController(DisabledBroker(), config, store)
    now = utc_now()
    controller.snapshot.account = Account("123456789", "Agentic", "cash", True, "active")
    controller.snapshot.portfolio = Portfolio(100.0, 100.0, 100.0)
    controller.live_evidence_ready = lambda grant=None: True
    grant = LiveGrant(
        account_number="123456789",
        starts_at=now,
        expires_at=now + timedelta(hours=1),
        max_order_notional=10.0,
        max_total_exposure=20.0,
        max_daily_loss=2.0,
        max_trades=4,
        max_orders_per_minute=1,
        max_spread_bps=20.0,
        max_quote_age_seconds=8.0,
    )

    with pytest.raises(RuntimeError, match=r"T\+1 settlement"):
        controller.authorize_live(grant)
    store.close()


def test_live_shadow_overrides_saved_research_signal_with_live_settings(tmp_path, monkeypatch) -> None:
    store = AuditStore(tmp_path / "audit.db")
    config = AppConfig(
        broker_connection_enabled=True,
        fast_ema=5,
        slow_ema=18,
        trend_threshold_bps=7.0,
        max_hold_minutes=33,
    )
    monkeypatch.setattr(
        "grande_alpha.controller.load_sandbox_config",
        lambda: SandboxConfig(
            strategy_name="close_momentum",
            fast_ema=3,
            slow_ema=10,
            max_hold_minutes=90,
        ),
    )
    controller = TradingController(DisabledBroker(), config, store)
    controller.snapshot.connected = True
    controller.snapshot.account = Account("123456789", "Agentic", "cash", True, "active")

    controller.start_shadow()

    assert controller._shadow is not None
    shadow = controller._shadow.config
    assert shadow.strategy_name == "ema_momentum"
    assert (shadow.fast_ema, shadow.slow_ema) == (5, 18)
    assert shadow.trend_threshold_bps == 7.0
    assert shadow.max_hold_minutes == 33
    assert shadow.decision_stride == config.trade_every_bars
    assert shadow.settlement_model == "cash_t1"
    store.close()


def test_sandbox_trade_timeline_marks_every_virtual_fill(tmp_path) -> None:
    qt_app()
    store = AuditStore(tmp_path / "audit.db")
    widget = SandboxWidget(store, allow_remote_data=False)
    bundle = deterministic_demo(2, seed=9)
    config = SandboxConfig(
        warmup_bars=5,
        fast_ema=1,
        slow_ema=3,
        trend_threshold_bps=0.1,
        momentum_bars=1,
        hard_stop_pct=0.5,
        take_profit_pct=0.5,
        max_hold_minutes=100,
        max_entries_per_day=10,
        no_trade_open_minutes=0,
        no_trade_close_minutes=0,
        force_flat_at_end=True,
    )
    result = SandboxReplayEngine(config).run(bundle)
    widget.bundle = bundle
    widget.result = result

    widget._show_result(result)

    assert len(widget.tqqqs_price_curve.xData) >= len(bundle.frames)
    assert len(widget.buy_markers.data) == sum(fill.side == "buy" for fill in result.fills)
    plotted_sales = (
        len(widget.profitable_sale_markers.data)
        + len(widget.losing_sale_markers.data)
        + len(widget.flat_sale_markers.data)
    )
    assert plotted_sales == sum(fill.side == "sell" for fill in result.fills)
    assert "total realized P/L" in widget.sales_summary.text()
    widget.close()
    store.close()


def test_windows_scripts_share_short_managed_runtime_fallback() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "build.ps1").read_text(encoding="utf-8")
    runtime = (root / "runtime-path.ps1").read_text(encoding="utf-8")

    assert "Get-GrandeAlphaPython" in script
    assert "else { 'python' }" in script
    assert "LOCALAPPDATA" in runtime
    assert "GRANDE_ALPHA_RUNTIME_DIR" in runtime


def test_release_labels_unsigned_binary_and_produces_source_bundle() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "release.ps1").read_text(encoding="utf-8")
    doctor = (root / "doctor.ps1").read_text(encoding="utf-8")

    assert "unsigned-windows-x64" in script
    assert "UNSIGNED_BUILD.txt" in script
    assert "windows-source" in script
    assert "Get-AuthenticodeSignature" in doctor
    assert "SOURCE APP READY" in doctor
    assert "install-local.ps1" in script
    assert "cli.ps1" in script
    assert "GRANDE Alpha CLI.cmd" in script


def test_local_installer_uses_trusted_launcher_and_both_shortcut_locations() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "install-local.ps1").read_text(encoding="utf-8")

    assert "powershell.exe" in script
    assert "GetFolderPath('Desktop')" in script
    assert "GetFolderPath('Programs')" in script
    assert "run.ps1" in script
    assert "GRANDE Alpha Morning Check.lnk" in script
    assert "Morning Check.cmd" in script
    assert "grande_alpha.windows_shortcut" in script


def test_morning_check_is_explicitly_read_only_and_runs_broker_diagnostics() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "morning-check.ps1").read_text(encoding="utf-8")
    launcher = (root / "Morning Check.cmd").read_text(encoding="utf-8")

    assert "doctor.ps1') -Broker" in script
    assert "grande_alpha.cli status" in script
    assert "cannot submit, review, or cancel an order" in script
    assert "ExecutionPolicy Bypass" in launcher


def test_windows_app_identity_uses_the_grande_alpha_logo_group() -> None:
    root = Path(__file__).resolve().parents[1]
    app_source = (root / "src" / "grande_alpha" / "app.py").read_text(encoding="utf-8")
    shortcut_source = (root / "src" / "grande_alpha" / "windows_shortcut.py").read_text(encoding="utf-8")

    assert 'WINDOWS_APP_USER_MODEL_ID = "AaronJS.GRANDEAlpha"' in shortcut_source
    assert "SetCurrentProcessExplicitAppUserModelID" in app_source
    assert "window.setWindowIcon(app.windowIcon())" in app_source
    assert "System.AppUserModel.ID" in shortcut_source


def test_sandbox_exposes_exact_nine_action_matrix_and_full_history_source(tmp_path) -> None:
    qt_app()
    store = AuditStore(tmp_path / "audit.db")
    widget = SandboxWidget(store, allow_remote_data=False)

    cells = {
        widget.action_matrix.item(row, column).text().splitlines()[-1]
        for row in range(3)
        for column in range(3)
    }
    assert cells == {f"({t:+d},{s:+d})".replace("+0", "0") for t in (-1, 0, 1) for s in (-1, 0, 1)}
    assert any(
        "full shared history" in widget.source.itemText(index).lower()
        for index in range(widget.source.count())
    )
    assert "session end" in widget.force_flat.text().lower()
    assert widget.daily_benchmark_table.columnCount() == 7
    widget.close()
    store.close()


def test_diagnostic_export_redacts_identifiers(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    secret = "0123456789abcdef0123456789abcdef"
    store.receipt(
        "test",
        f"Account ending 8900 order {secret}",
        {"account_number": "123456789", "token": "secret-token", "safe": "visible"},
    )
    destination = tmp_path / "diagnostics.json"
    export_diagnostics(AppConfig(), store, destination)
    document = json.loads(destination.read_text(encoding="utf-8"))
    encoded = json.dumps(document)
    assert "123456789" not in encoded
    assert "secret-token" not in encoded
    assert secret not in encoded
    assert "visible" in encoded
    store.close()


def test_market_history_pruning_never_deletes_receipts(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    store.receipt("test", "retain this audit record")
    store.prune_market_history(90)
    assert store.recent_receipts(1)[0]["summary"] == "retain this audit record"
    store.close()

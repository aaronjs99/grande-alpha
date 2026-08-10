import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from grande_alpha import __version__
from grande_alpha.broker.base import Broker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.evidence import EVIDENCE_POLICY_VERSION, strategy_fingerprint
from grande_alpha.historical import deterministic_demo
from grande_alpha.models import Account, LiveGrant, Portfolio, Quote, utc_now
from grande_alpha.privacy import export_diagnostics
from grande_alpha.sandbox import SandboxConfig, SandboxReplayEngine
from grande_alpha.storage import AuditStore
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
    assert __version__ == "0.9.0"


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

    store.record_research_promotion(
        dataset_hash="market-history-hash",
        strategy_fingerprint=strategy_fingerprint(config),
        policy_version=EVIDENCE_POLICY_VERSION,
        status="LIVE_REVIEW_ELIGIBLE",
        source="licensed CSV",
        replay_end=now.isoformat(),
        gates=[{"name": "test fixture", "passed": True}],
        risk_envelope={
            "max_order_notional": 10.0,
            "max_total_exposure": 20.0,
            "max_daily_loss": 2.0,
            "max_trades": 4,
            "max_orders_per_minute": 1,
            "max_spread_bps": 20.0,
        },
    )
    controller.authorize_live(grant)
    assert controller.snapshot.live_status == "LIVE"
    controller.live_evidence_ready = lambda: False
    with pytest.raises(RuntimeError, match="missing or expired"):
        controller.start_strategy()
    assert controller.snapshot.live_status == "LOCKED"
    assert not controller.snapshot.strategy_running
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


def test_windows_build_uses_active_python_when_local_venv_is_absent() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "build.ps1").read_text(encoding="utf-8")

    assert "Test-Path -LiteralPath $VenvPython" in script
    assert "else { 'python' }" in script


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
    assert any("full shared history" in widget.source.itemText(index).lower() for index in range(widget.source.count()))
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

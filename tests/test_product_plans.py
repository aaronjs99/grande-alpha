from __future__ import annotations

import json

from PySide6.QtWidgets import QApplication, QLabel

from grande_alpha import cli
from grande_alpha.broker.base import Broker
from grande_alpha.config import AppConfig
from grande_alpha.controller import TradingController
from grande_alpha.models import Account, Portfolio
from grande_alpha.product import (
    COMMUNITY_FEATURE_IDS,
    COMMUNITY_PLAN,
    PRO_PLAN,
    SAFETY_FEATURE_IDS,
    FeatureStatus,
    configured_upgrade_url,
    current_entitlement,
)
from grande_alpha.storage import AuditStore
from grande_alpha.ui import main_window as main_window_module
from grande_alpha.ui.main_window import MainWindow
from grande_alpha.ui.product_dialog import ProductPlansDialog


def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


class DisabledBroker(Broker):
    async def connect(self):
        raise AssertionError("Product UI tests must not connect a broker")

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


class AcceptingGrantDialog:
    class DialogCode:
        Accepted = 1

    evidence_gated_values: list[bool] = []
    expected_grant = object()

    def __init__(self, *_args, evidence_gated: bool, **_kwargs) -> None:
        self.evidence_gated_values.append(evidence_gated)

    def exec(self) -> int:
        return self.DialogCode.Accepted

    def grant(self) -> object:
        return self.expected_grant


def _live_window(tmp_path, monkeypatch, *, evidence_ready: bool):
    store = AuditStore(tmp_path / f"product-live-{evidence_ready}.db")
    config = AppConfig(broker_connection_enabled=True, live_trading_enabled=True)
    controller = TradingController(DisabledBroker(), config, store)
    controller.snapshot.connected = True
    controller.snapshot.account = Account("123456789", "Trading", "cash", True, "active")
    controller.snapshot.portfolio = Portfolio(50, 50, 50)
    monkeypatch.setattr(controller, "live_evidence_ready", lambda grant=None: evidence_ready)
    window = MainWindow(controller, config)
    window._on_snapshot(controller.snapshot)
    window.timer.stop()
    window.reconcile_timer.stop()
    return window, controller, store


def test_built_in_community_plan_is_functional_without_a_paid_backend() -> None:
    entitlement = current_entitlement()

    assert entitlement.plan_id == "community"
    assert entitlement.plan_name == "Community"
    assert entitlement.source == "built-in local Community access"
    assert not entitlement.checkout_available
    assert not entitlement.paid_entitlement_available
    assert COMMUNITY_PLAN.price_label == "$0"
    assert all(feature.status == FeatureStatus.AVAILABLE for feature in COMMUNITY_PLAN.features)
    assert COMMUNITY_FEATURE_IDS


def test_safety_and_evidence_controls_are_never_plan_gated() -> None:
    entitlement = current_entitlement()

    assert SAFETY_FEATURE_IDS <= COMMUNITY_FEATURE_IDS
    assert all(entitlement.allows(feature_id) for feature_id in SAFETY_FEATURE_IDS)
    assert not entitlement.allows("experiment_organization")


def test_pro_catalog_is_truthfully_planned_not_active() -> None:
    assert PRO_PLAN.availability_label == "Coming soon"
    assert PRO_PLAN.price_label == "Price not announced"
    assert all(feature.status == FeatureStatus.PLANNED for feature in PRO_PLAN.features)


def test_upgrade_information_url_accepts_only_credential_free_https(monkeypatch) -> None:
    monkeypatch.delenv("GRANDE_ALPHA_UPGRADE_URL", raising=False)
    assert configured_upgrade_url() is None
    assert configured_upgrade_url("https://example.com/grande-alpha/pro") == (
        "https://example.com/grande-alpha/pro"
    )
    assert configured_upgrade_url("http://example.com") is None
    assert configured_upgrade_url("https://user:secret@example.com/pro") is None
    assert configured_upgrade_url("not a url") is None


def test_plan_dialog_discloses_no_checkout_and_reflows() -> None:
    app = qt_app()
    dialog = ProductPlansDialog(upgrade_url="")
    dialog.resize(700, 650)
    dialog.show()
    app.processEvents()

    text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "fully functional Community plan" in text
    assert "no paid checkout" in text
    assert "never paywalled" in text.lower()
    assert dialog._columns == 1
    assert not dialog.upgrade_button.isEnabled()
    assert dialog.upgrade_button.text() == "Pro updates coming soon"

    dialog.resize(900, 680)
    app.processEvents()
    assert dialog._columns == 2
    dialog.close()


def test_configured_upgrade_link_is_information_only() -> None:
    qt_app()
    dialog = ProductPlansDialog(upgrade_url="https://example.com/grande-alpha/pro")

    assert dialog.upgrade_button.isEnabled()
    assert dialog.upgrade_button.text() == "View Pro updates"
    assert "not in-app checkout" in dialog.upgrade_button.toolTip().lower()
    dialog.close()


def test_cli_reports_the_same_free_entitlement_without_checkout(capsys, monkeypatch) -> None:
    monkeypatch.delenv("GRANDE_ALPHA_UPGRADE_URL", raising=False)

    assert cli.main(["plans", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["current_entitlement"]["plan_id"] == "community"
    assert payload["current_entitlement"]["checkout_available"] is False
    assert payload["current_entitlement"]["paid_entitlement_available"] is False
    assert payload["upgrade_information"] == {
        "configured": False,
        "url": None,
        "checkout": False,
    }
    assert {plan["plan_id"] for plan in payload["plans"]} == {"community", "pro"}


def test_authorize_ui_prefers_the_exact_evidence_gated_route(tmp_path, monkeypatch) -> None:
    qt_app()
    AcceptingGrantDialog.evidence_gated_values.clear()
    window, controller, store = _live_window(tmp_path, monkeypatch, evidence_ready=True)
    calls: list[tuple[str, object | None]] = []
    monkeypatch.setattr(main_window_module, "LiveGrantDialog", AcceptingGrantDialog)
    monkeypatch.setattr(controller, "authorize_live", lambda grant: calls.append(("evidence", grant)))
    monkeypatch.setattr(
        controller,
        "authorize_supervised_experimental",
        lambda grant: calls.append(("supervised", grant)),
    )
    monkeypatch.setattr(controller, "start_strategy", lambda: calls.append(("start", None)))

    assert window.plan_button.text() == "COMMUNITY · FREE"
    assert window.plans_action.text().replace("&&", "&") == "Plans & Upgrade…"
    assert window.mode_badge.text() == "LIVE EVIDENCE READY"
    assert "Evidence-Gated" in window.authorize_button.text()
    window._authorize()

    assert AcceptingGrantDialog.evidence_gated_values == [True]
    assert calls == [("evidence", AcceptingGrantDialog.expected_grant), ("start", None)]
    window._closing_after_cleanup = True
    window.close()
    store.close()


def test_authorize_ui_uses_explicit_supervised_route_without_evidence(
    tmp_path, monkeypatch
) -> None:
    qt_app()
    AcceptingGrantDialog.evidence_gated_values.clear()
    window, controller, store = _live_window(tmp_path, monkeypatch, evidence_ready=False)
    calls: list[tuple[str, object | None]] = []
    monkeypatch.setattr(main_window_module, "LiveGrantDialog", AcceptingGrantDialog)
    monkeypatch.setattr(controller, "authorize_live", lambda grant: calls.append(("evidence", grant)))
    monkeypatch.setattr(
        controller,
        "authorize_supervised_experimental",
        lambda grant: calls.append(("supervised", grant)),
    )
    monkeypatch.setattr(controller, "start_strategy", lambda: calls.append(("start", None)))

    assert window.mode_badge.text() == "SUPERVISED EXPERIMENTAL — CONFIRM EACH ORDER"
    assert "Supervised" in window.authorize_button.text()
    window._authorize()

    assert AcceptingGrantDialog.evidence_gated_values == [False]
    assert calls == [("supervised", AcceptingGrantDialog.expected_grant), ("start", None)]
    window._closing_after_cleanup = True
    window.close()
    store.close()


def test_supervised_review_cannot_silently_upgrade_if_evidence_appears(
    tmp_path, monkeypatch
) -> None:
    qt_app()
    AcceptingGrantDialog.evidence_gated_values.clear()
    window, controller, store = _live_window(tmp_path, monkeypatch, evidence_ready=False)
    readiness = iter((False, True))
    calls: list[tuple[str, object | None]] = []
    monkeypatch.setattr(
        controller,
        "live_evidence_ready",
        lambda grant=None: next(readiness),
    )
    monkeypatch.setattr(main_window_module, "LiveGrantDialog", AcceptingGrantDialog)
    monkeypatch.setattr(controller, "authorize_live", lambda grant: calls.append(("evidence", grant)))
    monkeypatch.setattr(
        controller,
        "authorize_supervised_experimental",
        lambda grant: calls.append(("supervised", grant)),
    )
    monkeypatch.setattr(controller, "start_strategy", lambda: calls.append(("start", None)))

    window._authorize()

    assert AcceptingGrantDialog.evidence_gated_values == [False]
    assert calls == [("supervised", AcceptingGrantDialog.expected_grant), ("start", None)]
    window._closing_after_cleanup = True
    window.close()
    store.close()


def test_evidence_review_fails_closed_if_certificate_disappears(tmp_path, monkeypatch) -> None:
    qt_app()
    AcceptingGrantDialog.evidence_gated_values.clear()
    window, controller, store = _live_window(tmp_path, monkeypatch, evidence_ready=True)
    readiness = iter((True, False))
    calls: list[tuple[str, object | None]] = []
    errors: list[str] = []
    monkeypatch.setattr(
        controller,
        "live_evidence_ready",
        lambda grant=None: next(readiness),
    )
    monkeypatch.setattr(main_window_module, "LiveGrantDialog", AcceptingGrantDialog)
    monkeypatch.setattr(controller, "authorize_live", lambda grant: calls.append(("evidence", grant)))
    monkeypatch.setattr(
        controller,
        "authorize_supervised_experimental",
        lambda grant: calls.append(("supervised", grant)),
    )
    monkeypatch.setattr(controller, "start_strategy", lambda: calls.append(("start", None)))
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: errors.append(str(message)),
    )

    window._authorize()

    assert AcceptingGrantDialog.evidence_gated_values == [True]
    assert calls == []
    assert errors and "certificate changed" in errors[0]
    window._closing_after_cleanup = True
    window.close()
    store.close()

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.product import (
    COMMUNITY_PLAN,
    PRO_PLAN,
    FeatureStatus,
    ProductPlan,
    configured_upgrade_url,
    current_entitlement,
)


class _PlanCard(QFrame):
    def __init__(self, plan: ProductPlan, *, current: bool) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setAccessibleName(f"{plan.name} plan")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        name = QLabel(plan.name)
        name.setStyleSheet("font-size:17pt;font-weight:700")
        layout.addWidget(name)
        price = QLabel(plan.price_label)
        price.setStyleSheet("font-size:14pt;font-weight:650;color:#8fd3ff")
        layout.addWidget(price)
        status = QLabel("CURRENT PLAN" if current else plan.availability_label.upper())
        status.setStyleSheet(
            "background:#123827;color:#6de98a;border:1px solid #2b7650;"
            "border-radius:6px;padding:5px 8px;font-weight:700"
            if current
            else "background:#2b2315;color:#ffd27a;border:1px solid #6f5727;"
            "border-radius:6px;padding:5px 8px;font-weight:700"
        )
        layout.addWidget(status)

        for feature in plan.features:
            prefix = "Included" if feature.status == FeatureStatus.AVAILABLE else "Planned"
            item = QLabel(f"<b>{prefix} · {feature.label}</b><br>{feature.description}")
            item.setWordWrap(True)
            item.setAccessibleName(f"{prefix}: {feature.label}")
            layout.addWidget(item)
        layout.addStretch()


class ProductPlansDialog(QDialog):
    """Plan status and upgrade information without pretending checkout exists."""

    def __init__(self, parent=None, *, upgrade_url: str | None = None) -> None:
        super().__init__(parent)
        self.entitlement = current_entitlement()
        self.upgrade_url = configured_upgrade_url(upgrade_url)
        self.setWindowTitle("Plans and upgrade — GRANDE Alpha")
        self.setMinimumSize(620, 520)
        self.resize(900, 680)
        self.setSizeGripEnabled(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title = QLabel("Plans that keep the safety model intact")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)
        intro = QLabel(
            "You are using the fully functional Community plan. It requires no GRANDE Alpha account, "
            "license key, payment, or entitlement server. Broker and market-data providers may still "
            "have their own eligibility, terms, or charges."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        safety = QLabel(
            "NEVER PAYWALLED · Evidence and provenance checks, risk limits, stop controls, privacy "
            "boundaries, and per-order consent remain available on every plan."
        )
        safety.setObjectName("validationWarning")
        safety.setWordWrap(True)
        safety.setAccessibleName("Safety controls are never paywalled")
        outer.addWidget(safety)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body = QWidget()
        self.cards_layout = QGridLayout(self.body)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(12)
        self.cards_layout.setVerticalSpacing(12)
        self.community_card = _PlanCard(COMMUNITY_PLAN, current=True)
        self.pro_card = _PlanCard(PRO_PLAN, current=False)
        self.cards = (self.community_card, self.pro_card)
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)

        backend = QLabel(
            "This release has no paid checkout and no server-side Pro entitlement. Pro items above "
            "are a product direction, not active features or a purchase offer."
        )
        backend.setObjectName("settingsDescription")
        backend.setWordWrap(True)
        backend.setAccessibleName("Paid plan availability disclosure")
        outer.addWidget(backend)

        self.upgrade_button = QPushButton(
            "View Pro updates" if self.upgrade_url else "Pro updates coming soon"
        )
        self.upgrade_button.setEnabled(bool(self.upgrade_url))
        self.upgrade_button.setToolTip(
            "Opens the configured product-information page. It is not in-app checkout."
            if self.upgrade_url
            else "No product-information URL is configured in this build."
        )
        self.upgrade_button.setAccessibleName("View Pro product updates")
        self.upgrade_button.clicked.connect(self._open_upgrade_url)
        outer.addWidget(self.upgrade_button)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

        self._columns = 0
        self._apply_responsive_layout(self.width())

    def _apply_responsive_layout(self, width: int) -> None:
        columns = 2 if width >= 780 else 1
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.cards:
            self.cards_layout.removeWidget(card)
        for index, card in enumerate(self.cards):
            self.cards_layout.addWidget(card, index // columns, index % columns)
        for column in range(2):
            self.cards_layout.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

    def _open_upgrade_url(self) -> None:
        if self.upgrade_url:
            QDesktopServices.openUrl(QUrl(self.upgrade_url))

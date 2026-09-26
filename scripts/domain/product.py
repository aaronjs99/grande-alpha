from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class FeatureStatus(StrEnum):
    AVAILABLE = "available"
    PLANNED = "planned"


@dataclass(frozen=True)
class ProductFeature:
    feature_id: str
    label: str
    description: str
    status: FeatureStatus


@dataclass(frozen=True)
class ProductPlan:
    plan_id: str
    name: str
    price_label: str
    availability_label: str
    features: tuple[ProductFeature, ...]


@dataclass(frozen=True)
class EntitlementSnapshot:
    """Truthful local plan state for a build with no entitlement service."""

    plan_id: str
    plan_name: str
    source: str
    checkout_available: bool
    paid_entitlement_available: bool

COMMUNITY_FEATURES = (
    ProductFeature(
        "research_sandbox",
        "Research and historical replay",
        "Run local market research, deterministic replay, and configuration comparisons.",
        FeatureStatus.AVAILABLE,
    ),
    ProductFeature(
        "evidence_and_provenance",
        "Evidence and provenance tools",
        "Use data checks, cost stress, walk-forward evaluation, and local evidence receipts.",
        FeatureStatus.AVAILABLE,
    ),
    ProductFeature(
        "simulation_and_records",
        "Simulation and local records",
        "Use synthetic or read-only provider data for local simulations and keep research records.",
        FeatureStatus.AVAILABLE,
    ),
    ProductFeature(
        "safety_and_consent",
        "All safety and consent controls",
        "Account permissions, bounded authorization, risk limits, and Stop controls remain available to every user.",
        FeatureStatus.AVAILABLE,
    ),
)

PRO_PLANNED_FEATURES = (
    ProductFeature(
        "experiment_organization",
        "Expanded experiment organization",
        "Planned convenience for larger saved-run libraries and comparison workflows.",
        FeatureStatus.PLANNED,
    ),
    ProductFeature(
        "extended_analytics",
        "Extended analytics",
        "Planned reporting and analysis conveniences beyond the Community workspace.",
        FeatureStatus.PLANNED,
    ),
    ProductFeature(
        "report_exports",
        "Additional report exports",
        "Planned presentation-ready exports; no format is promised in the current release.",
        FeatureStatus.PLANNED,
    ),
)

COMMUNITY_PLAN = ProductPlan(
    plan_id="community",
    name="Community",
    price_label="$0",
    availability_label="Included in this release",
    features=COMMUNITY_FEATURES,
)

PRO_PLAN = ProductPlan(
    plan_id="pro",
    name="Pro",
    price_label="Price not announced",
    availability_label="Coming soon",
    features=PRO_PLANNED_FEATURES,
)

PRODUCT_PLANS = (COMMUNITY_PLAN, PRO_PLAN)
UPGRADE_URL_ENV = "GRANDE_ALPHA_UPGRADE_URL"


def current_entitlement() -> EntitlementSnapshot:
    return EntitlementSnapshot(
        plan_id=COMMUNITY_PLAN.plan_id,
        plan_name=COMMUNITY_PLAN.name,
        source="built-in local Community access",
        checkout_available=False,
        paid_entitlement_available=False,
    )


def configured_upgrade_url(value: str | None = None) -> str | None:
    """Return an optional HTTPS information URL; it is never treated as checkout."""

    candidate = (os.getenv(UPGRADE_URL_ENV, "") if value is None else value).strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    return candidate

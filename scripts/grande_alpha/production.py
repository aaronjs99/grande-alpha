"""Explicit production dependency wiring; creating an engine grants no authority."""

from pathlib import Path

from grande_alpha.authorization import UserAuthorizationGate
from grande_alpha.broker_permissions import VerifiedBrokerEligibility
from grande_alpha.mixed_engine import MixedExecutionEngine
from grande_alpha.qualification import ProductionGate


def build_production_engine(broker, ledger, audit, scope, policy, earnings_thresholds,
                            *, qualification_certificate: Path, authorization_permit: Path,
                            earnings_store) -> MixedExecutionEngine:
    qualification = ProductionGate(qualification_certificate)
    authorization = UserAuthorizationGate(authorization_permit, scope.account_number)

    def exact_gate(candidate_digest: str) -> bool:
        return qualification(candidate_digest) is True and authorization(candidate_digest) is True

    return MixedExecutionEngine(
        broker,
        ledger,
        audit,
        scope,
        policy,
        earnings_thresholds,
        eligibility=VerifiedBrokerEligibility(broker),
        qualification_gate=exact_gate,
        research_verifier=earnings_store.verify_event,
    )

"""Wire the mixed execution engine without creating trading authority."""

from pathlib import Path

from grande_alpha.broker.permissions import VerifiedBrokerEligibility
from grande_alpha.execution.authorization import UserAuthorizationGate
from grande_alpha.execution.mixed_engine import MixedExecutionEngine


def build_production_engine(broker, ledger, audit, scope, policy, earnings_thresholds,
                            *, authorization_permit: Path,
                            earnings_store) -> MixedExecutionEngine:
    authorization = UserAuthorizationGate(authorization_permit, scope.account_number)

    return MixedExecutionEngine(
        broker,
        ledger,
        audit,
        scope,
        policy,
        earnings_thresholds,
        eligibility=VerifiedBrokerEligibility(broker),
        authorization_gate=authorization,
        research_verifier=earnings_store.verify_event,
    )

from __future__ import annotations

from .base import Repository
from .execution_authority import ExecutionAuthorityMethods
from .execution_orders import ExecutionOrderMethods
from .execution_risk import ExecutionRiskMethods


class ExecutionRepository(
    ExecutionAuthorityMethods,
    ExecutionOrderMethods,
    ExecutionRiskMethods,
    Repository,
):
    """One SQLite repository composed from focused authority, order, and risk operations."""

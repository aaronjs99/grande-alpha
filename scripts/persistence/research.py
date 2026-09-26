from __future__ import annotations

from .base import Repository
from .research_evidence import ResearchEvidenceMethods
from .research_fund import ResearchFundMethods
from .research_holdouts import ResearchHoldoutMethods


class ResearchRepository(
    ResearchFundMethods,
    ResearchHoldoutMethods,
    ResearchEvidenceMethods,
    Repository,
):
    """Research ledgers share the store connection and transaction manager."""

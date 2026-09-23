"""Broker adapter entry points.

The broker contract stays importable without optional network-adapter
dependencies. Importing a named adapter remains explicit at the boundary where
it is actually needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker

__all__ = ["RobinhoodMCPBroker"]


def __getattr__(name: str) -> object:
    if name == "RobinhoodMCPBroker":
        from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker

        return RobinhoodMCPBroker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

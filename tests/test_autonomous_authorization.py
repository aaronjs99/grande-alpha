"""Regression checks for explicit, durable mixed-engine authorization."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from grande_alpha.execution.authorization import (
    UserAuthorizationGate,
    create_authorization,
    revoke_authorization,
)
from grande_alpha.execution.equity_execution import EquityScope
from grande_alpha.execution.standing import REQUIRED_TOOL_ARGUMENTS, ROBINHOOD_URL, validate_contract

NOW = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)


def test_authorization_persists_until_revoked_and_never_widens(tmp_path):
    permit = tmp_path / "approval.json"
    create_authorization(permit, "account-1", "scope-one", now=NOW)
    gate = UserAuthorizationGate(permit, "account-1", now=lambda: NOW + timedelta(days=365))
    assert gate("scope-one") is True
    with pytest.raises(RuntimeError, match="scope and account"):
        gate("scope-two")
    with pytest.raises(RuntimeError, match="scope and account"):
        UserAuthorizationGate(permit, "account-2", now=lambda: NOW)("scope-one")
    with pytest.raises(RuntimeError, match="Revoke"):
        create_authorization(permit, "account-1", "scope-two", now=NOW + timedelta(days=1))
    revoke_authorization(permit, now=NOW + timedelta(days=2))
    with pytest.raises(RuntimeError, match="revoked"):
        gate("scope-one")


def test_scope_can_be_approved_without_expiry_but_checks_limits():
    scope = EquityScope("account-1", ("TQQQ", "SQQQ"), NOW, None,
                        20, 100, 100, 25, 5, 8, 20)
    scope.validate()
    with pytest.raises(ValueError, match="max_daily_loss_usd"):
        EquityScope("account-1", ("TQQQ",), NOW, None, 20, 100, 100, 0, 5, 8, 20).validate()


def test_irrelevant_provider_tool_does_not_change_supported_contract():
    tools = [{"name": name, "inputSchema": {"type": "object", "properties": {
        argument: {"type": "array" if argument == "symbols" else "string"} for argument in arguments}}}
             for name, arguments in REQUIRED_TOOL_ARGUMENTS.items()]
    broker = SimpleNamespace(tool_contract_snapshot=lambda: {
        "server_url": ROBINHOOD_URL, "tools": [*tools, {"name": "new_unrelated_tool", "inputSchema": {}}]
    })
    validate_contract(broker)
    tools[0]["inputSchema"] = {}
    with pytest.raises(RuntimeError, match="incompatible input schema"):
        validate_contract(broker)


def test_provider_argument_type_change_blocks_placement():
    tools = [{"name": name, "inputSchema": {"type": "object", "properties": {
        argument: {"type": "array" if argument == "symbols" else "string"} for argument in arguments}}}
             for name, arguments in REQUIRED_TOOL_ARGUMENTS.items()]
    place = next(tool for tool in tools if tool["name"] == "place_equity_order")
    place["inputSchema"]["properties"]["account_number"]["type"] = "integer"
    broker = SimpleNamespace(tool_contract_snapshot=lambda: {"server_url": ROBINHOOD_URL, "tools": tools})
    with pytest.raises(RuntimeError, match="account_number argument type"):
        validate_contract(broker)

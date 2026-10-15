"""Offline regressions for broker catalog compatibility and readable local errors."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QStyleOptionViewItem

from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker
from grande_alpha.domain.account_models import Account, EquityTradability, Portfolio, Position
from grande_alpha.domain.market_models import Quote
from grande_alpha.domain.order_models import BrokerExecution, BrokerOrder, OrderIntent, OrderReview
from grande_alpha.execution.equity_execution import EquityOrderIntent
from grande_alpha.execution.session_runtime import BrokerSessionRuntime
from grande_alpha.execution.standing import REQUIRED_TOOL_ARGUMENTS, ROBINHOOD_URL, validate_contract
from grande_alpha.execution.worker_ipc import LocalControlServer
from grande_alpha.ui.agent_desk import ActivityDelegate
from grande_alpha.ui.research_desk import ResearchDesk


def catalog():
    tools = []
    for name, arguments in REQUIRED_TOOL_ARGUMENTS.items():
        schema = {"type": "object", "additionalProperties": False}
        if arguments:
            schema["properties"] = {a: {"type": "array" if a == "symbols" else "string"} for a in arguments}
        tools.append({"name": name, "inputSchema": schema})
    return {"server_url": ROBINHOOD_URL, "tools": tools}


class BrokerFixture:
    connected = False
    def __init__(self, snapshot=None):
        self.snapshot = snapshot or catalog()
        self.calls = []
    async def connect(self):
        self.calls.append("connect")
        self.connected = True
    async def disconnect(self):
        self.calls.append("disconnect")
        self.connected = False
    def tool_contract_snapshot(self):
        return self.snapshot
    async def get_accounts(self):
        self.calls.append("get_accounts")
        return [SimpleNamespace(masked="****TEST", agentic_allowed=True)]


class BrokerCatalogChecks(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapter_decodes_account_portfolio_position_and_tradability_records(self):
        fixtures = {
            "get_accounts": {"accounts": [{"account_number": "TEST-1234", "agentic_allowed": True, "state": "active"}]},
            "get_portfolio": {"total_value": "1000", "buying_power": {"buying_power": "900"}, "cash": "900"},
            "get_equity_positions": {"positions": [{"symbol": "QQQ", "quantity": "1", "shares_available_for_sells": "1"}]},
            "get_equity_tradability": {"results": [{"symbol": "QQQ", "tradeable": True,
                                                   "all_day_tradability": "tradable", "extended_hours_fractional_tradability": True}]},
        }
        calls = []
        async def call(name, _arguments):
            calls.append(name)
            return fixtures[name]
        broker = RobinhoodMCPBroker(allow_interactive_auth=False)
        broker._call = call
        accounts = await broker.get_accounts()
        self.assertIsInstance(accounts[0], Account)
        self.assertTrue(accounts[0].masked.endswith("1234"))
        portfolio = await broker.get_portfolio("TEST-1234")
        self.assertIsInstance(portfolio, Portfolio)
        portfolio.validate()
        self.assertIsInstance((await broker.get_positions("TEST-1234"))[0], Position)
        self.assertIsInstance((await broker.get_tradability("TEST-1234", ["QQQ"]))["QQQ"], EquityTradability)
        self.assertEqual(set(calls), set(fixtures))

    def test_equity_value_records_construct_validate_and_keep_independent_defaults(self):
        now = datetime(2026, 10, 14, 15, tzinfo=UTC)
        intent = EquityOrderIntent(ref_id="00000000-0000-0000-0000-000000000001", symbol="QQQ", side="buy",
                                   reason="offline fixture", dollar_amount=10, created_at=now)
        intent.validate()
        self.assertIsInstance(intent, OrderIntent)
        self.assertEqual(intent.broker_arguments("TEST-1234")["dollar_amount"], "10.00")
        self.assertEqual(intent.as_dict()["created_at"], now.isoformat())
        execution = BrokerExecution("fixture-fill", 1, 100, 0, now)
        execution.validate()
        order = BrokerOrder("fixture-order", "QQQ", "buy", "queued", 1, None, None, now,
                            cumulative_quantity=0)
        order.validate_execution_provenance(require_snapshot=True, observed_at=now)
        second = BrokerOrder("fixture-order-2", "QQQ", "buy", "queued", 1, None, None, now)
        order.raw["fixture"] = True
        self.assertNotIn("fixture", second.raw)
        review = OrderReview(intent, None, {}, Quote("QQQ", 99, 100, 100, now), {})
        self.assertEqual(review.estimated_notional, 10)
        with self.assertRaises(FrozenInstanceError):
            intent.symbol = "SQQQ"

    def test_no_argument_schema_omits_properties_per_mcp_specification(self):
        validate_contract(BrokerFixture())
        alternative = catalog()
        alternative["tools"][0]["inputSchema"] = {"type": "object"}
        validate_contract(BrokerFixture(alternative))

    def test_explicitly_malformed_or_missing_required_schemas_still_fail(self):
        for schema in (None, {"type": "object", "properties": None}, {"type": "array"}):
            value = catalog()
            value["tools"][0]["inputSchema"] = schema
            with self.assertRaises(RuntimeError):
                validate_contract(BrokerFixture(value))
        value = catalog()
        value["tools"][-1]["inputSchema"]["properties"].pop("order_id")
        with self.assertRaises(RuntimeError):
            validate_contract(BrokerFixture(value))

    def test_nullable_supported_types_are_compatible_but_other_types_are_rejected(self):
        for declared, valid in ((["null", "array"], True), (["array", "object"], False),
                                (["null"], False), (["array", {}], False)):
            value = catalog()
            quote_tool = next(t for t in value["tools"] if t["name"] == "get_equity_quotes")
            quote_tool["inputSchema"]["properties"]["symbols"]["type"] = declared
            if valid:
                validate_contract(BrokerFixture(value))
            else:
                with self.assertRaises(RuntimeError):
                    validate_contract(BrokerFixture(value))

    async def test_valid_catalog_connects_and_reads_masked_accounts_without_orders(self):
        broker = BrokerFixture()
        with tempfile.TemporaryDirectory() as scratch:
            runtime = BrokerSessionRuntime(Path(scratch), broker_factory=lambda _interactive: broker)
            result = await runtime.connect(False)
            self.assertTrue(result["connected"])
            self.assertEqual(result["accounts"], [{"masked": "****TEST", "agentic_allowed": True}])
            self.assertEqual(broker.calls, ["connect", "get_accounts"])
            await broker.disconnect()

    async def dispatch(self, exception):
        async def handler(_operation, _payload):
            raise exception
        server = LocalControlServer(Path("unused-offline"), handler)
        server._token = "fixture-local-token"
        return await server._dispatch({"version": 1, "id": "fixture-request", "token": "fixture-local-token",
                                       "deadline": time.time()+5, "operation": "connect", "payload": {}})

    async def test_known_contract_failure_reaches_the_local_operator(self):
        from grande_alpha.execution.standing import BrokerContractError
        response = await self.dispatch(BrokerContractError("Broker tool get_accounts requires an object input schema"))
        self.assertEqual(response["error"]["code"], "broker_contract")
        self.assertIn("get_accounts", response["error"]["message"])

    async def test_unexpected_errors_do_not_expose_tokens_accounts_or_raw_provider_text(self):
        response = await self.dispatch(RuntimeError("Bearer SECRET; account=PRIVATE; https://example.test/?code=HIDDEN"))
        text = str(response)
        for value in ("SECRET", "PRIVATE", "HIDDEN"):
            self.assertNotIn(value, text)


class ActivityChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.desk = ResearchDesk()
        self.desk.resize(390, 700)

    def tearDown(self):
        if self.desk._activity_detail is not None:
            self.desk._activity_detail.close()
        self.desk.close()
        self.app.processEvents()

    def test_time_or_message_activation_shows_the_same_full_snapshot(self):
        text = "Connection did not complete: " + "long safe explanation " * 30
        self.desk.record_event("WARN", text, "2026-10-14T16:51:00-07:00")
        for column in (0, 1):
            self.desk.activity.itemActivated.emit(self.desk.activity.item(0, column))
            dialog = self.desk._activity_detail
            self.assertIsNotNone(dialog)
            body = dialog.findChild(QPlainTextEdit)
            self.assertIn(text, body.toPlainText())
            self.desk.record_event("INFO", "A newer row")
            self.desk.activity.removeRow(1)
            self.assertIn(text, body.toPlainText())
            dialog.close()
            self.app.processEvents()
            self.desk.activity.setRowCount(0)
            self.desk.record_event("WARN", text, "2026-10-14T16:51:00-07:00")

    def test_delegate_paints_generic_message_once_and_keeps_structured_details(self):
        class Recorder(QPainter):
            def __init__(self, device):
                super().__init__(device)
                self.drawn = []
            def drawText(self, *arguments):
                self.drawn.append(str(arguments[-1]))
                return super().drawText(*arguments)
        for text, expected in (("A short error", 1), ("NOVA → VELA · distinct detail", 2)):
            self.desk.activity.setRowCount(0)
            self.desk.record_event("WARN", text)
            pixmap = QPixmap(500, 47)
            painter = Recorder(pixmap)
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, 500, 47)
            ActivityDelegate(self.desk.activity).paint(painter, option, self.desk.activity.model().index(0, 1))
            painter.end()
            self.assertEqual(len(painter.drawn), expected+1)  # One badge plus message line(s).


if __name__ == "__main__":
    unittest.main(verbosity=2)

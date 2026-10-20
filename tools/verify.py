"""Local acceptance: python tools/verify.py [--executable dist/GRANDEAlpha/GRANDEAlpha.exe].

Only disposable data and synthetic/fake services are used. No pytest or CI is required.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from grande_alpha.configuration.json_inputs import load_json
from grande_alpha.execution.candidate_profile import (
    candidate_template,
    save_candidate_document,
    validate_candidate,
)
from grande_alpha.execution.session_runtime import BrokerSessionRuntime
from grande_alpha.execution.session_worker import SessionWorker
from grande_alpha.execution.worker_control import WorkerControlStore, WorkerPermission
from grande_alpha.execution.worker_ipc import LocalControlClient, LocalControlError, LocalControlServer
from grande_alpha.persistence.store import AuditStore
from grande_alpha.research.paper.journal import read_journal, save_journal_csv

PAPER = {"source": "demo", "initial_cash": "1000", "trade_cash": "100", "loop_demo": False,
         "news_enabled": False, "social_enabled": False, "local_ai_enabled": False, "local_ai_model": ""}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def rejected(action, errors=(ValueError, RuntimeError)) -> None:
    try:
        action()
    except errors:
        return
    raise AssertionError("Invalid or unsafe operation was accepted")


def check_inputs(directory: Path) -> None:
    source = directory / "candidate.json"
    draft = candidate_template()
    save_candidate_document(source, draft)
    original = source.read_bytes()
    rejected(lambda: validate_candidate(draft))
    rejected(lambda: save_candidate_document(source, draft, source_path=source, overwrite=True))
    edited = json.loads(original)
    edited["scope"]["max_order_usd"] = 10
    save_candidate_document(directory / "edited.json", edited, source_path=source)
    require(source.read_bytes() == original, "Editing a copy changed the source")
    for text in ('{"scope":1,"scope":2}', '{"amount":NaN}'):
        invalid = directory / "invalid.json"
        invalid.write_text(text, encoding="utf-8")
        rejected(lambda path=invalid: load_json(path, max_bytes=65_536))


def check_records(directory: Path) -> None:
    import sqlite3

    missing = directory / "missing" / "records.db"
    require(AuditStore.read_existing(missing) is None and not missing.parent.exists(), "Reading missing records created files")
    path = directory / "records.db"
    writer = AuditStore(path)
    writer.receipt("acceptance", "Disposable record")
    writer.close()
    original = path.read_bytes()
    reader = AuditStore.read_existing(path)
    try:
        rejected(lambda: reader.receipt("acceptance", "Must not persist"), (sqlite3.OperationalError,))
    finally:
        reader.close()
    require(path.read_bytes() == original, "Read-only inspection changed records")


def check_stop(directory: Path) -> None:
    path = directory / "control.db"
    control = WorkerControlStore(path)
    try:
        rejected(lambda: control.start("unreviewed"))
        reviewed = control.review(account="fixture", scope_digest="fixture-scope", candidate_path="fixture.json",
                                  authorization_path="fixture-permit.json", earnings_database="fixture.db", poll_seconds=5)
        running = control.start(reviewed.scope_digest, expected_generation=reviewed.generation)
        permit = WorkerPermission(control, running.generation, lambda _scope: True)
        require(permit(reviewed.scope_digest), "Reviewed fixture did not start")
        control.stop()
        rejected(lambda: permit(reviewed.scope_digest))
        rejected(lambda: control.start(reviewed.scope_digest, expected_generation=reviewed.generation))
    finally:
        control.close()
    reopened = WorkerControlStore(path)
    try:
        require(not reopened.current().running, "Restart restored stopped trading intent")
    finally:
        reopened.close()


async def check_broker(directory: Path) -> None:
    from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker
    from grande_alpha.execution.standing import REQUIRED_TOOL_ARGUMENTS, ROBINHOOD_URL, validate_contract

    tools = [{"name": name, "inputSchema": {"type": "object", **({"properties": {
        argument: {"type": ["null", "array"] if argument == "symbols" else "string"}
        for argument in arguments}} if arguments else {})}} for name, arguments in REQUIRED_TOOL_ARGUMENTS.items()]
    snapshot = {"server_url": ROBINHOOD_URL, "tools": tools}
    validate_contract(SimpleNamespace(tool_contract_snapshot=lambda: snapshot))
    tools[-1]["inputSchema"]["properties"].pop("order_id")
    rejected(lambda: validate_contract(SimpleNamespace(tool_contract_snapshot=lambda: snapshot)))
    fixtures = {
        "get_accounts": {"accounts": [{"account_number": "TEST-1234", "agentic_allowed": True, "state": "active"}]},
        "get_portfolio": {"total_value": "1000", "buying_power": {"buying_power": "900"}, "cash": "900"},
        "get_equity_positions": {"positions": [{"symbol": "QQQ", "quantity": "1", "shares_available_for_sells": "1"}]},
        "get_equity_tradability": {"results": [{"symbol": "QQQ", "tradeable": True,
                                               "all_day_tradability": "tradable", "extended_hours_fractional_tradability": True}]},
    }

    async def call(name, _arguments):
        return fixtures[name]  # Unexpected operations, including orders, fail.

    adapter = RobinhoodMCPBroker(allow_interactive_auth=False)
    adapter._call = call
    require((await adapter.get_accounts())[0].masked.endswith("1234"), "Account decoding failed")
    (await adapter.get_portfolio("TEST-1234")).validate()
    require((await adapter.get_positions("TEST-1234"))[0].symbol == "QQQ", "Position decoding failed")
    require("QQQ" in await adapter.get_tradability("TEST-1234", ["QQQ"]), "Tradability decoding failed")

    # A provider's exception can contain credentials or account data. It must not
    # enter snapshots, diagnostics, or the research context sent to AI clients.
    from dataclasses import asdict

    from grande_alpha.research.agent_models import AgentSettings
    from grande_alpha.research.agent_runtime import AgentRuntime

    async def unavailable(*_args):
        raise RuntimeError("PRIVATE_TOKEN account=PRIVATE_ACCOUNT")

    async def empty(*_args):
        return []

    agent = AgentRuntime(equity_quotes=unavailable, crypto_pairs=empty, crypto_quotes=unavailable,
                         equity_scan=empty, connected=lambda: True, changed=lambda _snapshot: None,
                         log=lambda *_args, **_kwargs: None)
    try:
        await agent.cycle()
        status = json.dumps(asdict(agent.snapshot), default=str)
        require("PRIVATE_TOKEN" not in status and "PRIVATE_ACCOUNT" not in status,
                "Provider exception exposed private material in research status")
        agent.start_paper(AgentSettings(), source="broker_quotes")
        agent.paper._db.execute("PRAGMA query_only=ON")
        await asyncio.wait_for(agent._task, timeout=3)
        require(agent.snapshot.phase == "Error" and "Could not save session results" in agent.snapshot.error,
                "Concurrent paper storage failure lost its actionable error")
        from grande_alpha.research.worker_research import WorkerResearch

        service = WorkerResearch(None, directory / "error-mcp.db", log=lambda *_args, **_kwargs: None)
        service.agent = agent
        require(service.status().get("error") == agent.snapshot.error,
                "Paper failure guidance did not reach the desktop status")
    finally:
        await agent.close()

    async def accounts():
        return [SimpleNamespace(account_number="FIXTURE", agentic_allowed=True,
                                rhs_account_number="", brokerage_account_type="")]

    async def quotes(*_args, **_kwargs):
        return {}

    broker = SimpleNamespace(connected=True, get_accounts=accounts, get_quotes=quotes,
                             discover_crypto=empty, get_crypto_quotes=quotes,
                             discover_equities=empty, disconnect=empty)
    control = WorkerControlStore(directory / "quote-only.db")
    runtime = BrokerSessionRuntime(directory, control=control)
    runtime.broker = broker
    initial = control.current()
    try:
        result = await runtime.start_paper_research(initial, {**PAPER, "source": "broker_quotes"})
        require(result["running"] is True and result["orders_available"] is False,
                "Connected quote-only paper could not start")
        require(control.current() == initial and not initial.account and not initial.scope_digest,
                "Quote-only paper changed live review or authority")
        settings = runtime.research.agent.settings
        rejected(lambda: runtime.research.start_paper({**PAPER, "source": "broker_quotes",
                                                      "local_ai_enabled": True, "local_ai_model": "fixture:model"}))
        require(runtime.research.agent.settings == settings,
                "Rejected paper Start changed the active AI settings")
        account = runtime.research.account
        for source in ("demo", "broker_quotes"):
            try:
                await runtime.start_paper_research(initial, {**PAPER, "source": source})
            except (ValueError, RuntimeError):
                pass
            else:
                raise AssertionError("Running paper accepted another Start")
            require(runtime.research.account is account, "Rejected paper Start changed its active account")
        await runtime.stop_paper_research()

        entered, releases = [], [asyncio.Event(), asyncio.Event()]

        async def delayed_accounts():
            index = len(entered)
            entered.append(index)
            await releases[index].wait()
            return [SimpleNamespace(account_number=f"FIXTURE-{index}", agentic_allowed=True,
                                    rhs_account_number="", brokerage_account_type="")]

        broker.get_accounts = delayed_accounts
        starts = [asyncio.create_task(runtime.start_paper_research(initial, {**PAPER, "source": "broker_quotes"}))
                  for _ in releases]
        await asyncio.sleep(0)
        require(len(entered) == 2, "Concurrent paper Start fixture did not reach both account reads")
        releases[0].set()
        await starts[0]
        account = runtime.research.account
        releases[1].set()
        try:
            await starts[1]
        except (ValueError, RuntimeError):
            pass
        else:
            raise AssertionError("Concurrent paper Start was accepted")
        require(runtime.research.account is account, "Concurrent rejected Start changed the running account")
        await runtime.stop_paper_research()

        async def ambiguous():
            return [*(await accounts()), SimpleNamespace(account_number="OTHER", agentic_allowed=True)]

        broker.get_accounts = ambiguous
        try:
            await runtime.start_paper_research(initial, {**PAPER, "source": "broker_quotes"})
        except RuntimeError:
            pass
        else:
            raise AssertionError("Paper silently selected among multiple eligible accounts")
    finally:
        await runtime.close()
        control.close()


async def check_exit(directory: Path) -> None:
    from grande_alpha.ui.session_window import SessionWindow

    def unavailable(_interactive):
        raise RuntimeError("Fixture broker unavailable")

    control = WorkerControlStore(directory / "control.db")
    runtime = BrokerSessionRuntime(directory, control=control, broker_factory=unavailable)
    try:
        report = {**await runtime.cleanup_report(control.current()), "stopped": True, "process_exiting": True}
        require(report["broker_check_required"] is False, "Fresh paper worker requested broker verification")
        require(SessionWindow._cleanup_warning(report) == "", "Paper-only exit incorrectly warns")
        try:
            await runtime.connect(False)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Unavailable broker unexpectedly connected")
        require(runtime.broker_context_seen is True, "Failed connection lost broker context")
        report = {**await runtime.cleanup_report(control.current()), "stopped": True, "process_exiting": True}
        require(bool(SessionWindow._cleanup_warning(report)), "Unverified broker context was silently dismissed")
        require(bool(SessionWindow._cleanup_warning({"stopped": True})), "Unknown shutdown was silently dismissed")
    finally:
        await runtime.close()
        control.close()


def check_journal(directory: Path, paper: dict) -> dict:
    path = directory / "agent-paper.db"
    journal = read_journal(path, session_id=paper["session_id"], limit=2)
    require(journal["fill_count"] == 4 and len(journal["fills"]) == 2, "Saved fill pagination failed")
    older = read_journal(path, session_id=paper["session_id"], before=journal["next_before"], limit=2)
    require(len(older["fills"]) == 2 and older["next_before"] is None, "Older fills are missing")
    require(journal["session"]["equity"] == paper["equity"], "Saved equity differs from the desk")
    target = directory / "fills.csv"
    save_journal_csv(journal, target)
    with target.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == 2, "Export included fills outside the loaded page")
    for row, fill in zip(rows, journal["fills"], strict=True):
        require(all(row[key] == fill[key] for key in ("quantity", "price", "realized_pnl")), "CSV rounded saved decimals")
    original = target.read_bytes()
    rejected(lambda: save_journal_csv(journal, target))
    rejected(lambda: save_journal_csv(journal, path, overwrite=True))
    with patch("grande_alpha.research.paper.journal.os.replace", side_effect=OSError("Interrupted export")):
        rejected(lambda: save_journal_csv(journal, target, overwrite=True), (OSError,))
    require(target.read_bytes() == original, "Interrupted export destroyed the prior file")
    return read_journal(path, session_id=paper["session_id"])


async def check_paper(directory: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from grande_alpha.ui.paper_journal import PaperJournalDialog
    from grande_alpha.ui.session_window import SessionWindow

    app = QApplication.instance() or QApplication([])
    control = WorkerControlStore(directory / "grande_alpha.db")

    def no_external(*_args, **_kwargs):
        raise AssertionError("Paper acceptance attempted another worker or broker connection")

    runtime = BrokerSessionRuntime(directory, control=control, broker_factory=no_external)
    worker = SessionWorker(control, runtime)
    server = LocalControlServer(directory, worker.handle)
    # The fixture server is owned here; a failed probe must not spawn another worker.
    window = SessionWindow(directory, auto_probe=False, launcher=no_external)
    window.status_timer.stop()
    with patch("grande_alpha.execution.worker_ipc._token", return_value="d" * 64), \
         patch("grande_alpha.research.agent_runtime.DEMO_INTERVAL_SECONDS", .005):
        try:
            await server.start()
            window._client = LocalControlClient(directory)
            await window._refresh_status(show_errors=False)
            await window._start_paper()
            await asyncio.wait_for(runtime.research.agent._task, timeout=8)
            await window._refresh_status(show_errors=False)
            paper = window._status["research"]["paper"]
            require(paper["fill_count"] == 4 and paper["closed_trades"] == 2, "Demo did not complete its four-fill cycle")
            require(len(window.desk.curve.getData()[0]) > 1, "Paper equity chart did not update")
            require(not runtime.connected and not control.current().running, "Paper granted broker authority")
            failed = {**window._status["research"], "phase": "Error", "error": "Could not save session results. Check disk space."}
            window._render_research_status(failed)
            require("error" in window.status_notice.text().lower() and "disk space" in window.research_status.text(),
                    "Paper save failure looked like normal completion")
            window._render_research_status({**failed, "phase": "Disconnected", "error": ""})
            require("disconnected" in window.status_notice.text().lower(), "Disconnected paper looked successfully saved")
            from grande_alpha.ui.strategy_comparison import StrategyComparisonDialog

            with patch("grande_alpha.ui.strategy_comparison.Path.glob", side_effect=PermissionError("Fixture cache unavailable")):
                comparison = StrategyComparisonDialog(window)
            require(comparison.path is None and comparison.choose.isEnabled(), "Unavailable cache blocked manual history choice")
            comparison.reject()
            journal = check_journal(directory, paper)
            dialog = PaperJournalDialog(window)
            dialog._render(journal)
            dialog._set_busy(False)
            require(dialog.table.rowCount() == 4 and dialog.export_button.isEnabled(), "Saved journal did not render")
            require(dialog.table.horizontalHeaderItem(1).text() == "Demo time", "Demo time label regressed")
            dialog.reject()
            window.paper_loop_demo.setChecked(True)
            await window._start_paper()
            await asyncio.sleep(.025)
            await window._stop()
            require(not runtime.research.agent.snapshot.running and not control.current().running, "STOP left paper running")
        finally:
            await server.close()
            await worker.close()
            control.close()
            window._closing = True
            window.close()
            app.processEvents()


def check_packaged(executable: Path) -> None:
    """Exercise only synthetic operations in an isolated child worker."""
    import keyring

    from grande_alpha.execution.worker_ipc import _TOKEN_SERVICE, _channel_id
    from grande_alpha.ui.session_window import SessionWindow

    if os.name == "nt":
        from keyring.backends.Windows import WinVaultKeyring
        keyring.set_keyring(WinVaultKeyring())
    require(executable.is_file(), "Packaged executable is missing")
    with tempfile.TemporaryDirectory(prefix="ga-packaged-paper-") as scratch:
        directory = Path(scratch)
        channel = _channel_id(directory)
        had_credential = keyring.get_password(_TOKEN_SERVICE, channel) is not None if os.name == "nt" else False
        client = LocalControlClient(directory)
        process = None

        def request(operation, payload=None):
            require(operation in {"status", "research_paper_start", "research_paper_stop", "stop", "shutdown"},
                    "Acceptance attempted an unauthorized operation")
            return client.request(operation, payload or {}, timeout_seconds=6)

        def status():
            result = request("status")
            require(not result["connected"] and not result["running"] and result["broker_context_seen"] is False,
                    "Synthetic worker acquired broker context or trading authority")
            require(result["research"]["orders_available"] is False, "Paper exposed broker orders")
            return result

        def launch():
            nonlocal process
            process = subprocess.Popen([str(executable), "--worker", "--data-dir", str(directory), "--no-recovery"],
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 20
            while True:
                require(process.poll() is None, "Packaged worker exited during startup")
                try:
                    return status()
                except LocalControlError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.2)

        def shutdown():
            result = request("shutdown")
            require(result["stopped"] is True and result["process_exiting"] is True, "Worker shutdown failed")
            require(result["broker_check_required"] is False and result["broker_verified"] is False,
                    "Paper shutdown reported incorrect broker state")
            require(SessionWindow._cleanup_warning(result) == "", "Paper-only exit incorrectly warned")
            process.wait(timeout=15)
            require(process.returncode == 0, "Packaged worker exited with an error")

        try:
            require(not launch()["research"]["running"], "Fresh worker automatically resumed research")
            require(request("research_paper_start", PAPER)["running"] is True, "Packaged paper did not start")
            deadline = time.monotonic() + 45
            while True:
                current = status()
                if current["research"]["phase"] == "Demo complete":
                    break
                require(current["research"]["phase"] != "Error", "Packaged demo reported an error")
                require(time.monotonic() < deadline, "Packaged demo timed out")
                time.sleep(.5)
            paper = current["research"]["paper"]
            require(paper["fill_count"] == 4 and paper["closed_trades"] == 2, "Packaged demo fills are incomplete")
            require(not paper["active"] and paper["pending_count"] == 0 and paper["position_count"] == 0,
                    "Completed demo retained virtual orders or positions")
            journal = check_journal(directory, paper)
            shutdown()
            require(not launch()["research"]["running"], "Restart automatically resumed research")
            require(read_journal(directory / "agent-paper.db", session_id=paper["session_id"])["fills"] == journal["fills"],
                    "Restart lost saved fills")
            request("research_paper_start", {**PAPER, "loop_demo": True})
            deadline = time.monotonic() + 20
            while status()["research"]["paper"]["fill_count"] < 2:
                require(time.monotonic() < deadline, "Repeating paper did not produce entries")
                time.sleep(.5)
            stopped = request("research_paper_stop")
            require(not stopped["running"] and not stopped["paper"]["active"], "Paper Stop failed")
            count = stopped["paper"]["fill_count"]
            time.sleep(1.2)
            stopped = request("stop")
            require(not stopped["research"]["running"] and stopped["research"]["paper"]["fill_count"] == count,
                    "Global Stop failed or paper continued filling")
            shutdown()
            launch()
            request("research_paper_start", {**PAPER, "loop_demo": True})
            deadline = time.monotonic() + 20
            while True:
                crash_paper = status()["research"]["paper"]
                if crash_paper["fill_count"] >= 2:
                    break
                require(time.monotonic() < deadline, "Crash fixture did not save its virtual entries")
                time.sleep(.5)
            # Terminate only this owned synthetic child, then inspect durable recovery.
            process.terminate()
            process.wait(timeout=10)
            require(not launch()["research"]["running"], "Crash recovery automatically resumed paper")
            recovered = read_journal(directory / "agent-paper.db", session_id=crash_paper["session_id"])
            require(recovered["fill_count"] >= crash_paper["fill_count"], "Crash lost committed paper fills")
            shutdown()
            print(f"PASS packaged paper: 4 fills, equity {paper['equity']}; journal/CSV, Stop, restart, crash, exit", flush=True)
        finally:
            if process is not None and process.poll() is None:
                try:
                    request("shutdown")
                    process.wait(timeout=10)
                except Exception:
                    process.terminate()
                    process.wait(timeout=5)
            if os.name == "nt" and not had_credential and keyring.get_password(_TOKEN_SERVICE, channel) is not None:
                keyring.delete_password(_TOKEN_SERVICE, channel)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, help="Check the shipped worker instead of source scenarios")
    arguments = parser.parse_args()
    if arguments.executable:
        check_packaged(arguments.executable.resolve())
        return 0
    from grande_alpha.desktop.startup_check import check_desktop
    require(check_desktop()["ok"], "Desktop startup failed")
    print("PASS desktop startup, narrow setup and empty journal; no worker or AI", flush=True)
    with tempfile.TemporaryDirectory(prefix="ga-acceptance-") as scratch:
        for check in (check_inputs, check_records, check_stop, check_broker, check_exit, check_paper):
            directory = Path(scratch) / check.__name__
            directory.mkdir()
            result = check(directory)
            if asyncio.iscoroutine(result):
                asyncio.run(result)
            print(f"PASS {check.__name__.removeprefix('check_')}", flush=True)
    print("7 local acceptance scenarios passed; no broker connection, order, or model request.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

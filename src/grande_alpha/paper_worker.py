"""Local background paper worker, process supervisor and loopback dashboard.

Only the allowlisted broker reads below are reachable. No Controller or live
executor is instantiated. Operating expenses are recorded, never purchased.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import plistlib
import secrets
import signal
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from grande_alpha.agent_models import AgentSettings
from grande_alpha.agent_runtime import AgentRuntime
from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.robinhood_mcp import RobinhoodMCPBroker
from grande_alpha.config import config_path, data_dir
from grande_alpha.paper_experiment import POLICY, ExperimentLedger

PORT = 8767
URL = f"http://127.0.0.1:{PORT}"
READ_TOOLS = frozenset({"get_accounts", "get_equity_quotes", "get_currency_pairs", "get_crypto_quotes", "run_scan"})


class PaperQuoteBroker(RobinhoodMCPBroker):
    def __init__(self):
        super().__init__(allow_interactive_auth=False)

    async def _call(self, name, arguments):
        if name not in READ_TOOLS:
            raise BrokerError("The background paper worker permits market-data reads only")
        return await super()._call(name, arguments)


def experiment_dir():
    path = data_dir() / "paper-experiment"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def atomic_json(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def read_status(directory=None):
    path = (directory or experiment_dir()) / "status.json"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("policy") != POLICY:
            return {}
        result["online"] = 0 <= time.time() - result["heartbeat"] < 10
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {}


@contextmanager
def instance_lock(path):
    """OS lock survives stale lock files but is released on process death."""
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("The background paper service is already running") from None
    try:
        yield
    finally:
        handle.close()


def child_command(action, directory):
    return [sys.executable, "-m", "grande_alpha.paper_worker", "--directory", str(directory), action]


def child_env():
    return {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1]),
                                                                    os.environ.get("PYTHONPATH", "")]))}


def start_background(directory=None):
    if getattr(sys, "frozen", False):
        raise ValueError("Background paper needs a Python source installation; this packaged executable cannot launch it")
    directory = directory or experiment_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    (directory / "enabled").touch(mode=0o600)
    if read_status(directory).get("online"):
        return URL
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(child_command("supervise", directory), env=child_env(), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    return URL


def stop_background(directory=None):
    (directory or experiment_dir()).joinpath("enabled").unlink(missing_ok=True)


def supervise(directory):
    with instance_lock(directory / "supervisor.lock"):
        stopping = False

        def stop_signal(*_):
            nonlocal stopping
            stopping = True
            stop_background(directory)

        signal.signal(signal.SIGTERM, stop_signal)
        signal.signal(signal.SIGINT, stop_signal)
        while not stopping and (directory / "enabled").exists():
            child = subprocess.Popen(child_command("serve", directory), env=child_env(), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            launched = time.time()
            while child.poll() is None and not stopping and (directory / "enabled").exists():
                heartbeat = read_status(directory).get("heartbeat", launched)
                if time.time() - max(heartbeat, launched) > 120:
                    child.terminate()
                    break
                time.sleep(0.5)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
            if child.returncode == 2:  # Corrupt state or port collision: preserve evidence, require review.
                stop_background(directory)
            for _ in range(10):
                if stopping or not (directory / "enabled").exists():
                    break
                time.sleep(0.5)


def broker_reading_enabled():
    try:
        return json.loads(config_path().read_text(encoding="utf-8")).get("broker_connection_enabled") is True
    except (OSError, ValueError, TypeError):
        return False


class PaperWorker:
    def __init__(self, directory, *, broker=None, permission=broker_reading_enabled):
        self.directory, self.permission = directory, permission
        self.book = ExperimentLedger(directory / "experiment.db")
        self.book.initialize()
        state = dict(self.book.state)
        state["unavailable_marks"] = list(state["positions"])
        self.book._save(state)
        self.broker = broker or PaperQuoteBroker()
        self.account = None
        self.token = secrets.token_urlsafe(32)
        self.instance = secrets.token_hex(12)
        self.message = "Starting background paper experiment"
        self.phase = "Starting"
        self.runtime = AgentRuntime(equity_quotes=self.broker.get_quotes, crypto_pairs=self.broker.discover_crypto,
                                    crypto_quotes=self.crypto_quotes, equity_scan=self.broker.discover_equities,
                                    connected=lambda: bool(self.broker.connected and self.account and self.permission()),
                                    changed=lambda snapshot: None, log=lambda *args, **kwargs: None,
                                    crypto_account_type=lambda: self.account.brokerage_account_type if self.account else "",
                                    paper=self.book)

    async def crypto_quotes(self, instruments):
        if not self.account or not self.account.rhs_account_number or not self.account.rhc_account_number:
            raise BrokerError("The connected account has no verified crypto account")
        return await self.broker.get_crypto_quotes(instruments, rhs_account_number=self.account.rhs_account_number)

    def snapshot(self):
        now = datetime.now(UTC)
        paper = self.book.summary(active=self.runtime.snapshot.running, now=now)
        decisions = [{"symbol": d.instrument.key, "action": d.action, "reason": d.reason,
                      "status": d.risk_status, "spread_bps": d.quote.spread_bps if d.quote else None}
                     for d in self.runtime.snapshot.decisions]
        if paper["loss_locked"]:
            self.message = "$10 loss budget reached. New entries are locked; eligible quotes can close remaining paper positions."
        elif paper["paused"]:
            self.message = "New entries paused. Existing paper positions keep their exit rules."
        phase = self.runtime.snapshot.phase if self.runtime.snapshot.running else self.phase
        return {"policy": POLICY, "heartbeat": time.time(), "phase": phase, "message": self.message,
                "cycle": self.runtime.snapshot.cycle, "paper": paper, "decisions": decisions,
                "markets": {k: "Unavailable: quote read failed" if v.startswith("Unavailable") else v
                            for k, v in self.runtime.snapshot.market_status.items()},
                "url": URL, "instance": self.instance, "worker_pid": os.getpid(), "mode": "PAPER ONLY"}

    def save_status(self):
        atomic_json(self.directory / "status.json", self.snapshot())

    async def halt_runtime(self):
        pending = [t for t in (self.runtime._task, *self.runtime._background_tasks) if t is not None]
        self.runtime.stop()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def connect(self):
        try:
            async with asyncio.timeout(45):
                await self.broker.connect()
                accounts = [a for a in await self.broker.get_accounts() if a.agentic_allowed and a.state.lower() == "active"]
                if len(accounts) != 1:
                    raise BrokerError("Exactly one active Agentic account is required")
                account = accounts[0]
                fingerprint = hashlib.sha256((account.account_number + ":" + account.rhs_account_number).encode()).hexdigest()
                expected = self.book.state.get("account_fingerprint")
                if expected and expected != fingerprint:
                    self.phase, self.message = "Account changed", "Reconnect the original account to continue this saved experiment."
                    await self.broker.disconnect()
                    return False
                if not expected:
                    state = dict(self.book.state)
                    state["account_fingerprint"] = fingerprint
                    self.book._save(state)
                self.account = account
            self.phase, self.message = "Connected", "Reading Robinhood quotes. All fills and money remain virtual."
            return True
        except Exception:
            self.account = None
            self.phase = "Connection needed"
            self.message = "Open GRANDE, enable broker access and connect Robinhood. This worker retries saved sign-in every 30 seconds."
            await self.broker.disconnect()
            return False

    async def run(self):
        server = None
        try:
            server = await asyncio.start_server(self.http, "127.0.0.1", PORT, limit=16384)
            reconnect_at = 0.0
            connect_task = None
            while (self.directory / "enabled").exists():
                if not self.permission():
                    if connect_task:
                        connect_task.cancel()
                        await asyncio.gather(connect_task, return_exceptions=True)
                        connect_task = None
                    await self.halt_runtime()
                    if self.broker.connected:
                        await self.broker.disconnect()
                    self.phase, self.message = "Connection needed", "Enable broker access in GRANDE Settings and connect Robinhood."
                elif not self.broker.connected or not self.account:
                    await self.halt_runtime()
                    if connect_task is None and time.monotonic() >= reconnect_at:
                        connect_task = asyncio.create_task(self.connect())
                elif not self.runtime.snapshot.running:
                    if self.runtime.snapshot.phase == "Error":
                        self.phase, self.message = "Needs review", "Paper worker stopped after an error. Results were preserved; stop and restart after checking storage."
                    else:
                        values = dict(self.book.state["settings"])
                        for key in ("equity_symbols", "crypto_symbols"):
                            values[key] = tuple(values[key])
                        self.runtime.resume_paper(AgentSettings(**values))
                if connect_task and connect_task.done():
                    connect_task.result()
                    connect_task = None
                    reconnect_at = time.monotonic() + 30
                # A broken transport can still say connected while every read fails.
                markets = self.runtime.snapshot.market_status
                if markets and all(v.startswith("Unavailable") for v in markets.values()) and time.monotonic() >= reconnect_at:
                    await self.halt_runtime()
                    await self.broker.disconnect()
                    self.account = None
                    reconnect_at = time.monotonic() + 30
                self.save_status()
                await asyncio.sleep(1)
        finally:
            if 'connect_task' in locals() and connect_task:
                connect_task.cancel()
                await asyncio.gather(connect_task, return_exceptions=True)
            await self.halt_runtime()
            await self.broker.disconnect()
            self.phase, self.message = "Stopped", "Paper experiment saved. Restart continues the same balance, costs and loss budget."
            self.save_status()
            # Mark stopped services offline immediately.
            status = self.snapshot()
            status["heartbeat"] = 0
            atomic_json(self.directory / "status.json", status)
            if server:
                server.close()
                await server.wait_closed()
            self.book.close()

    async def http(self, reader, writer):
        status, result, mime = 200, {}, "application/json"
        try:
            async with asyncio.timeout(5):
                head = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
                lines = head.split("\r\n")
                method, path, _ = lines[0].split(" ")
                headers = {k.lower(): v.strip() for k, v in (line.split(":", 1) for line in lines[1:] if line)}
                if headers.get("host") != f"127.0.0.1:{PORT}":
                    raise ValueError("Use the local dashboard address")
                if method == "GET" and path == "/":
                    mime = "text/html; charset=utf-8"
                    result = Path(__file__).with_name("assets").joinpath("paper-dashboard.html").read_text(encoding="utf-8").replace("__TOKEN__", self.token)
                elif method == "GET" and path == "/api/status":
                    result = self.snapshot()
                elif method == "POST":
                    if (headers.get("origin") not in (None, URL) or
                            not hmac.compare_digest(headers.get("x-experiment-token", ""), self.token)):
                        status, result = 403, {"error": "Reopen the local dashboard"}
                    else:
                        size = int(headers.get("content-length", "0"))
                        if not 0 <= size <= 4096:
                            raise ValueError("Request too large")
                        body = json.loads(await reader.readexactly(size) or b"{}")
                        if path == "/api/expense":
                            self.book.add_expense(body.get("amount"), body.get("note"), body.get("id"))
                        elif path in ("/api/pause", "/api/resume"):
                            self.book.set_paused(path.endswith("pause"))
                            self.message = "Reading Robinhood quotes. All fills and money remain virtual."
                        elif path == "/api/stop":
                            stop_background(self.directory)
                        else:
                            raise ValueError("Unknown control")
                        self.save_status()
                        result = {"ok": True}
                else:
                    status, result = 404, {"error": "Not found"}
        except (ValueError, TypeError, KeyError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            status, result = 400, {"error": "Invalid request. Expenses need a positive dollar amount and a description."}
        except Exception:
            status, result = 503, {"error": "Could not save this change. Check available disk space; no success was recorded."}
        try:
            payload = (result if mime.startswith("text/html") else json.dumps(result, allow_nan=False)).encode()
            nonce_policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            writer.write((f"HTTP/1.1 {status} Response\r\nContent-Type: {mime}\r\nContent-Length: {len(payload)}\r\n"
                          f"Cache-Control: no-store\r\nContent-Security-Policy: {nonce_policy}\r\n"
                          "X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n").encode() + payload)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


def install_login_service(directory):
    if sys.platform != "darwin":
        raise ValueError("Start-at-login installation is available on macOS; use start on this platform")
    path = Path.home() / "Library/LaunchAgents/com.grande-alpha.paper.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"Label": "com.grande-alpha.paper", "ProgramArguments": child_command("supervise", directory),
               "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 10,
               "EnvironmentVariables": {"PYTHONPATH": child_env()["PYTHONPATH"]}}
    if path.exists() and plistlib.loads(path.read_bytes()) != payload:
        raise ValueError("A different GRANDE login service already exists; remove it before replacing it")
    path.write_bytes(plistlib.dumps(payload))
    path.chmod(0o600)
    # Launch at the next login. start handles the current login without touching other services.
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Persistent $100 paper experiment; no real orders")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("action", choices=("start", "stop", "status", "dashboard", "supervise", "serve", "install-login", "remove-login"))
    args = parser.parse_args(argv)
    directory = args.directory or experiment_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if args.action == "start":
            print(start_background(directory))
        elif args.action == "stop":
            stop_background(directory)
            print("Stop requested. Saved paper holdings and expenses are retained.")
        elif args.action == "status":
            print(json.dumps(read_status(directory), indent=2))
        elif args.action == "dashboard":
            if not read_status(directory).get("online"):
                print("Start the background paper worker first.")
                return 1
            webbrowser.open(URL)
        elif args.action == "supervise":
            supervise(directory)
        elif args.action == "install-login":
            print(f"Start-at-login saved: {install_login_service(directory)}")
        elif args.action == "remove-login":
            if sys.platform != "darwin":
                raise ValueError("This command is for macOS")
            Path.home().joinpath("Library/LaunchAgents/com.grande-alpha.paper.plist").unlink(missing_ok=True)
            print("Start-at-login removed. Use stop to end a currently running worker.")
        else:
            with instance_lock(directory / "worker.lock"):
                asyncio.run(PaperWorker(directory).run())
    except RuntimeError as exc:
        print(str(exc))
        return 0  # Another supervisor owns the experiment.
    except Exception:
        atomic_json(directory / "status.json", {"policy": POLICY, "heartbeat": 0, "phase": "Needs review",
                    "message": "Background worker could not start. Check the saved database and whether port 8767 is already in use."})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

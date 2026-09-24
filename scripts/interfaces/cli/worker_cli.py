"""Command-line client for the user-started local session worker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from grande_alpha.configuration.config import data_dir
from grande_alpha.execution.worker_control import WorkerControlStore
from grande_alpha.execution.worker_ipc import LocalControlClient, LocalControlError
from grande_alpha.execution.worker_process import launch_worker


def _client(*, launch: bool) -> LocalControlClient:
    target = data_dir()
    return launch_worker(target) if launch else LocalControlClient(target)


def _show(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))


def _approval(prompt: str, phrase: str) -> str:
    if not sys.stdin.isatty():
        raise RuntimeError("Approval requires an interactive terminal")
    print(prompt)
    entered = input(f"Type {phrase!r}: ").strip()
    if entered != phrase:
        raise RuntimeError("Approval was declined")
    return entered


def _review_payload(args) -> dict:
    return {
        "candidate_path": str(Path(args.candidate).resolve()),
        "authorization_path": str(Path(args.authorization).resolve()),
        "earnings_database": str(Path(args.earnings_database).resolve()),
        "poll_seconds": args.poll_seconds or 5.0,
    }


def command_worker_run(args) -> int:
    """Review and approve an exact scope, then leave its worker running."""
    client = _client(launch=True)
    review = client.request("review", _review_payload(args), timeout_seconds=300)
    _show(review)
    phrase = f"AUTHORIZE {review['scope_digest']}"
    _approval(
        "This approval permits real-money autonomous orders within the displayed account, symbols and limits. "
        "Stop or revoke remains available; broker orders already sent may still fill.",
        phrase,
    )
    client.request("authorize", {"phrase": phrase})
    _show(client.request("start", {"scope_digest": review["scope_digest"],
                                   "expected_generation": review["generation"]}))
    return 0


def command_worker_control(args) -> int:
    operation = args.session_command
    if operation == "status":
        client = _client(launch=False)
        _show(client.request("status", {}))
        return 0
    if operation == "connect":
        _show(_client(launch=True).request("connect", {"authenticate": bool(args.authenticate)},
                                          timeout_seconds=300))
        return 0
    if operation == "review":
        _show(_client(launch=True).request("review", _review_payload(args), timeout_seconds=300))
        return 0
    if operation == "authorize":
        client = _client(launch=False)
        scope = client.request("status", {})["scope_digest"]
        if not scope:
            raise RuntimeError("Review an exact candidate first")
        phrase = f"AUTHORIZE {scope}"
        _approval("Approve real-money autonomous orders for the reviewed scope.", phrase)
        _show(client.request("authorize", {"phrase": phrase}))
        return 0
    if operation == "start":
        client = _client(launch=False)
        status = client.request("status", {})
        scope = status["scope_digest"]
        if not scope:
            raise RuntimeError("Review and authorize an exact candidate first")
        _show(client.request("start", {"scope_digest": scope,
                                        "expected_generation": status["generation"]}))
        return 0
    if operation == "revoke":
        client = _client(launch=False)
        account = client.request("status", {}).get("account")
        if not account:
            raise RuntimeError("No reviewed account is available to revoke")
        phrase = f"REVOKE {account}"
        _approval("Revoke the exact account authorization and stop the worker.", phrase)
        _show(client.request("revoke", {"phrase": phrase}))
        return 0
    if operation in {"recovery-ack", "shutdown"}:
        mapped = "recovery_ack" if operation == "recovery-ack" else "shutdown"
        _show(_client(launch=False).request(mapped, {}))
        return 0
    raise ValueError("Unsupported worker command")


def command_worker_stop(_args) -> int:
    """Fence submissions even if the broker or worker control channel is unresponsive."""
    target = data_dir()
    try:
        result = LocalControlClient(target).request("stop", {}, timeout_seconds=8)
        _show(result)
        return 0
    except (LocalControlError, OSError, TimeoutError) as error:
        control_path = target / "grande_alpha.db"
        if not control_path.is_file():
            raise RuntimeError("No worker control record exists; no local session was stopped") from error
        control = WorkerControlStore(control_path)
        try:
            state = control.stop()
        finally:
            control.close()
        _show({"running": False, "generation": state.generation,
               "broker_cleanup_verified": False, "worker_response": str(error)})
        return 0


def command_research_mcp(args) -> int:
    """Opt into the worker's separate research-only MCP mailbox."""
    client = _client(launch=False)
    if args.research_mcp_command == "status":
        _show(client.request("status", {}).get("research", {}))
    elif args.research_mcp_command in {"enable", "disable"}:
        _show(client.request(f"research_{args.research_mcp_command}", {}))
    else:
        raise ValueError("Unsupported research MCP command")
    return 0

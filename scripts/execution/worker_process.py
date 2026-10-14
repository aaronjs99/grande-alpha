"""Start and supervise the current user's local trading worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from grande_alpha.configuration.config import data_dir
from grande_alpha.execution.process_lock import ProcessLock
from grande_alpha.execution.session_runtime import BrokerSessionRuntime
from grande_alpha.execution.session_worker import SessionWorker
from grande_alpha.execution.worker_control import WorkerControlStore
from grande_alpha.execution.worker_ipc import LocalControlClient, LocalControlError, LocalControlServer


async def serve(runtime_data_dir: Path, *, recover_existing: bool = True) -> int:
    """Own the broker and execution lease until the user requests shutdown."""
    lock = ProcessLock(runtime_data_dir / "app.lock")
    if not lock.acquire(timeout_seconds=0.1):
        return 2
    control = None
    server = None
    worker = None
    try:
        control = WorkerControlStore(runtime_data_dir / "grande_alpha.db")
        if not recover_existing and control.current().running:
            # A demo/Connect click must never inherit a later live Start from
            # another client. Check under the owning process lock, before
            # broker creation, IPC credentials, or any recovery operation.
            logging.error("Non-recovering startup refused saved live intent; record Stop before retrying")
            return 3
        worker = SessionWorker(control, BrokerSessionRuntime(runtime_data_dir, control=control))
        server = LocalControlServer(runtime_data_dir, worker.handle)
        await server.start()
        if recover_existing:
            await worker.recover()
        await worker.shutdown_requested.wait()
        # Let the final command response leave its pipe before closing it.
        await asyncio.sleep(0.1)
        return 0
    finally:
        if server is not None:
            await server.close()
        if worker is not None:
            await worker.close()
        if control is not None:
            control.close()
        lock.release()


def launch_worker(runtime_data_dir: Path | None = None, *, timeout_seconds: float = 10,
                  recover_existing: bool = True) -> LocalControlClient:
    """Return the existing worker or start one hidden for this user."""
    target = Path(runtime_data_dir or data_dir()).resolve()
    client = LocalControlClient(target)
    try:
        client.request("status", {}, timeout_seconds=0.5)
        return client
    except (LocalControlError, OSError):
        pass
    target.mkdir(parents=True, exist_ok=True)
    executable = Path(sys.executable)
    packaged = bool(getattr(sys, "frozen", False))
    if os.name == "nt" and not packaged and executable.name.lower() != "pythonw.exe":
        windowed = executable.with_name("pythonw.exe")
        if not windowed.is_file():
            raise RuntimeError("This Python installation has no pythonw.exe for a hidden worker")
        executable = windowed
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    command = ([str(executable), "--worker"] if packaged else
               [str(executable), "-m", "grande_alpha.execution.worker_process"])
    if not recover_existing:
        command.append("--no-recovery")
    with (target / "worker-startup.log").open("wb") as startup_log:
        process = subprocess.Popen(
            command + ["--data-dir", str(target)],
            stdin=subprocess.DEVNULL, stdout=startup_log, stderr=subprocess.STDOUT,
            cwd=str(target), close_fds=True, creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            client.request("status", {}, timeout_seconds=0.5)
            return client
        except (LocalControlError, OSError):
            if process.poll() not in {None, 2}:
                raise RuntimeError(
                    "The local worker refused startup; record Stop if a saved session is active, "
                    f"then inspect {target / 'worker-startup.log'}"
                ) from None
            time.sleep(0.05)
    raise RuntimeError(
        f"The local worker did not become available; inspect {target / 'worker-startup.log'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grande-alpha-worker")
    parser.add_argument("--data-dir", type=Path, default=data_dir())
    parser.add_argument("--no-recovery", action="store_true",
                        help="Refuse saved live intent and start without automatic live recovery")
    args = parser.parse_args(argv)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=args.data_dir / "worker.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(serve(args.data_dir, recover_existing=not args.no_recovery))
    except Exception:
        logging.exception("The local worker stopped unexpectedly")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

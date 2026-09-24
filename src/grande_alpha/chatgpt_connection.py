"""Explicit, reversible setup of GRANDE's local ChatGPT/Codex connection.

Only the named MCP entry is added. Model, approvals, sandbox and other servers stay
untouched. No authentication, process launch or research permission is granted here.
"""
from __future__ import annotations

import copy
import json
import os
import stat
import sys
import tempfile
import tomllib
from pathlib import Path

SERVER_NAME = "grande-alpha"
BEGIN = "# BEGIN GRANDE research connection\n"
END = "# END GRANDE research connection\n"


class ConnectionSetupError(ValueError):
    pass


def config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser() / "config.toml"


def connection_settings(bridge_path: Path) -> dict:
    if getattr(sys, "frozen", False):
        raise ConnectionSetupError("Open GRANDE from its Python installation to add this connection.")
    return {
        "command": sys.executable,
        "args": ["-m", "grande_alpha.agent_mcp", "--bridge", str(bridge_path.resolve())],
        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    }


def connection_block(settings: dict) -> str:
    # JSON basic strings/arrays are also valid TOML; no shell interpolation is used.
    return (
        BEGIN + f"[mcp_servers.{SERVER_NAME}]\n"
        + f"command = {json.dumps(settings['command'], ensure_ascii=False)}\n"
        + f"args = {json.dumps(settings['args'], ensure_ascii=False)}\n"
        + f"[mcp_servers.{SERVER_NAME}.env]\n"
        + f"PYTHONPATH = {json.dumps(settings['env']['PYTHONPATH'], ensure_ascii=False)}\n" + END
    )


def _read(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1_048_576:
        raise ConnectionSetupError("Settings need manual review. No changes were made.")
    return path.read_bytes()


def _parse(raw: bytes) -> dict:
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as from_error:
        raise ConnectionSetupError("ChatGPT settings could not be read. Fix them in ChatGPT before retrying.") from from_error


def _write(path: Path, original: bytes | None, updated: bytes) -> None:
    """Back up before atomic replacement; refuse detected concurrent edits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".grande-settings-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        if _read(path) != original:
            raise ConnectionSetupError("Settings changed during setup. Close ChatGPT settings and try again.")
        if original is None:
            # Exclusive publication: never replace a settings file created concurrently.
            os.link(temporary, path)
        else:
            backup_fd, _ = tempfile.mkstemp(prefix="config.toml.grande-backup-", dir=path.parent)
            with os.fdopen(backup_fd, "wb") as backup:
                backup.write(original)
                backup.flush()
                os.fsync(backup.fileno())
            if _read(path) != original:
                raise ConnectionSetupError("Settings changed during setup. Close ChatGPT settings and try again.")
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def add_connection(path: Path, settings: dict) -> None:
    original = _read(path)
    raw = original or b""
    parsed = _parse(raw)
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ConnectionSetupError("ChatGPT connection settings need manual review.")
    if SERVER_NAME in servers:
        if servers[SERVER_NAME] == settings:
            return
        raise ConnectionSetupError("A different GRANDE connection already exists. Review it in ChatGPT Settings → MCP servers.")
    if BEGIN.encode() in raw or END.encode() in raw:
        raise ConnectionSetupError("An earlier GRANDE setup needs manual review in ChatGPT settings.")
    updated = raw + b"\n" + connection_block(settings).encode("utf-8")
    expected = copy.deepcopy(parsed)
    expected.setdefault("mcp_servers", {})[SERVER_NAME] = settings
    if _parse(updated) != expected:
        raise ConnectionSetupError("Settings could not be extended safely. Use ChatGPT's Add server option.")
    _write(path, original, updated)


def remove_connection(path: Path, settings: dict) -> None:
    original = _read(path)
    raw = original or b""
    parsed = _parse(raw)
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ConnectionSetupError("ChatGPT connection settings need manual review.")
    if SERVER_NAME not in servers:
        return
    block = connection_block(settings).encode("utf-8")
    if servers[SERVER_NAME] != settings or raw.count(block) != 1:
        raise ConnectionSetupError("This connection was edited outside GRANDE. Remove it in ChatGPT Settings → MCP servers.")
    # Preserve every other byte, including changes made after installation.
    updated = raw.replace(b"\n" + block, b"", 1) if b"\n" + block in raw else raw.replace(block, b"", 1)
    expected = copy.deepcopy(parsed)
    del expected["mcp_servers"][SERVER_NAME]
    actual = _parse(updated)
    if not expected["mcp_servers"] and "mcp_servers" not in actual:
        del expected["mcp_servers"]
    if actual != expected:
        raise ConnectionSetupError("Settings need manual review before removing this connection.")
    _write(path, original, updated)

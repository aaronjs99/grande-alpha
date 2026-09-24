"""Authenticated, machine-local control channel for the shared worker.

This module never opens a TCP listener. Each message is a length-prefixed JSON
object capped at 64 KiB, and each connection carries exactly one request.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import secrets
import socket
import stat
import struct
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
_TOKEN_SERVICE = "GRANDE Alpha local control"


class LocalControlError(RuntimeError):
    """A remote operation or local transport operation failed."""


def _channel_id(data_dir: Path) -> str:
    return hashlib.sha256(str(data_dir.resolve()).encode("utf-8")).hexdigest()[:24]


def _address(data_dir: Path) -> str:
    channel = _channel_id(data_dir)
    if os.name == "nt":
        return rf"\\.\pipe\grande-alpha-{channel}"
    return str(data_dir / "worker-control.sock")


def _token(data_dir: Path, *, create: bool) -> str:
    if os.name == "nt":
        import keyring

        account = _channel_id(data_dir)
        value = keyring.get_password(_TOKEN_SERVICE, account)
        if value is None and create:
            value = secrets.token_hex(32)
            keyring.set_password(_TOKEN_SERVICE, account, value)
        if not value:
            raise LocalControlError("Local control credential is unavailable")
        return value

    path = data_dir / "worker-control.token"
    if create:
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(secrets.token_hex(32))
    try:
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise LocalControlError("Local control credential has unsafe ownership or permissions")
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError as error:
        raise LocalControlError("Local control credential is unavailable") from error
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise LocalControlError("Local control credential is invalid")
    return value


def _encode(message: dict) -> bytes:
    try:
        content = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise LocalControlError("Control message is not valid JSON data") from error
    if len(content) > MAX_MESSAGE_BYTES:
        raise LocalControlError("Control message exceeds 64 KiB")
    return struct.pack("!I", len(content)) + content


def _decode(content: bytes) -> dict:
    try:
        message = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise LocalControlError("Control message is not valid JSON") from error
    if not isinstance(message, dict):
        raise LocalControlError("Control message must be an object")
    return message


async def _read_async(reader: asyncio.StreamReader) -> dict:
    length = struct.unpack("!I", await reader.readexactly(4))[0]
    if length == 0 or length > MAX_MESSAGE_BYTES:
        raise LocalControlError("Control message length is invalid")
    return _decode(await reader.readexactly(length))


def _read_sync(read_exact: Callable[[int], bytes]) -> dict:
    length = struct.unpack("!I", read_exact(4))[0]
    if length == 0 or length > MAX_MESSAGE_BYTES:
        raise LocalControlError("Control message length is invalid")
    return _decode(read_exact(length))


def _error(request_id: str | None, code: str, message: str) -> dict:
    return {"version": PROTOCOL_VERSION, "id": request_id, "ok": False,
            "error": {"code": code, "message": message}}


class LocalControlServer:
    def __init__(self, data_dir: Path, async_handler: Callable[[str, dict], Awaitable[dict]]) -> None:
        self.data_dir = Path(data_dir)
        self.async_handler = async_handler
        self._token: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unix_server: asyncio.AbstractServer | None = None
        self._pipe_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._pipe_error: Exception | None = None

    async def start(self) -> None:
        if self._loop is not None:
            raise LocalControlError("Local control server is already started")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._token = _token(self.data_dir, create=True)
        self._loop = asyncio.get_running_loop()
        if os.name == "nt":
            self._pipe_thread = threading.Thread(target=self._accept_pipes, daemon=True)
            self._pipe_thread.start()
            if not await asyncio.to_thread(self._ready.wait, 5):
                raise LocalControlError("Local control pipe did not become ready")
            if self._pipe_error is not None:
                raise LocalControlError("Local control pipe could not start") from self._pipe_error
        else:
            address = Path(_address(self.data_dir))
            if address.exists():
                raise LocalControlError("Local control socket already exists")
            self._unix_server = await asyncio.start_unix_server(self._serve_unix, path=str(address))
            address.chmod(0o600)

    async def close(self) -> None:
        if self._loop is None:
            return
        if self._unix_server is not None:
            self._unix_server.close()
            await self._unix_server.wait_closed()
            Path(_address(self.data_dir)).unlink(missing_ok=True)
            self._unix_server = None
        if self._pipe_thread is not None:
            self._stop.set()
            try:
                await asyncio.to_thread(self._wake_pipe)
            except Exception:
                pass
            await asyncio.to_thread(self._pipe_thread.join, 2)
            self._pipe_thread = None
        self._loop = None

    async def _dispatch(self, request: dict) -> dict:
        request_id = request.get("id") if isinstance(request.get("id"), str) else None
        if type(request.get("version")) is not int or request["version"] != PROTOCOL_VERSION:
            return _error(request_id, "version", "Unsupported protocol version")
        if request_id is None or len(request_id) > 128:
            return _error(None, "request", "Invalid request id")
        supplied_token = request.get("token")
        if not isinstance(supplied_token, str) or not hmac.compare_digest(
            supplied_token, self._token or ""
        ):
            return _error(request_id, "authentication", "Authentication failed")
        if (not isinstance(request.get("deadline"), (int, float))
                or isinstance(request["deadline"], bool)
                or not math.isfinite(request["deadline"])):
            return _error(request_id, "deadline", "Invalid deadline")
        if request["deadline"] <= time.time():
            return _error(request_id, "deadline", "Request deadline expired")
        operation, payload = request.get("operation"), request.get("payload")
        if not isinstance(operation, str) or not operation or not isinstance(payload, dict):
            return _error(request_id, "request", "Invalid operation or payload")
        try:
            result = await asyncio.wait_for(
                self.async_handler(operation, payload),
                timeout=max(0, request["deadline"] - time.time()),
            )
            if not isinstance(result, dict):
                raise TypeError("Handler result must be an object")
            response = {"version": PROTOCOL_VERSION, "id": request_id, "ok": True, "result": result}
            _encode(response)
            return response
        except TimeoutError:
            return _error(request_id, "deadline", "Request deadline expired")
        except Exception:
            return _error(request_id, "handler", "Control operation failed")

    async def _serve_unix(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(_read_async(reader), timeout=10)
            response = await self._dispatch(request)
            writer.write(_encode(response))
            await writer.drain()
        except (LocalControlError, asyncio.IncompleteReadError, TimeoutError):
            try:
                writer.write(_encode(_error(None, "protocol", "Invalid control message")))
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    def _accept_pipes(self) -> None:
        import pywintypes
        import win32api
        import win32con
        import win32pipe
        import win32security

        process_token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        user_sid = win32security.GetTokenInformation(process_token, win32security.TokenUser)[0]
        acl = win32security.ACL()
        acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, user_sid)
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorDacl(1, acl, 0)
        attributes = win32security.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        try:
            self._accept_pipes_loop(win32pipe, win32security, win32con, win32api, pywintypes, attributes)
        except Exception as error:
            self._pipe_error = error
            self._ready.set()

    def _accept_pipes_loop(self, win32pipe, win32security, win32con, win32api, pywintypes,
                           attributes) -> None:
        while not self._stop.is_set():
            pipe = win32pipe.CreateNamedPipe(
                _address(self.data_dir),
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                MAX_MESSAGE_BYTES + 4,
                MAX_MESSAGE_BYTES + 4,
                1000,
                attributes,
            )
            self._ready.set()
            try:
                try:
                    win32pipe.ConnectNamedPipe(pipe, None)
                except pywintypes.error as error:
                    if error.winerror != 535:  # ERROR_PIPE_CONNECTED
                        raise
                if not self._stop.is_set():
                    threading.Thread(target=self._serve_pipe, args=(pipe,), daemon=True).start()
                    pipe = None
            finally:
                if pipe is not None:
                    win32api.CloseHandle(pipe)

    def _wake_pipe(self) -> None:
        import win32con
        import win32file

        pipe = win32file.CreateFile(_address(self.data_dir),
                                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                    0, None, win32con.OPEN_EXISTING, 0, None)
        win32file.CloseHandle(pipe)

    def _serve_pipe(self, pipe: object) -> None:
        import win32api
        import win32file

        def read_exact(count: int) -> bytes:
            chunks = bytearray()
            while len(chunks) < count:
                _, chunk = win32file.ReadFile(pipe, count - len(chunks))
                if not chunk:
                    raise LocalControlError("Connection closed")
                chunks.extend(chunk)
            return bytes(chunks)

        try:
            request = _read_sync(read_exact)
            assert self._loop is not None
            deadline = request.get("deadline")
            wait = max(1, deadline - time.time() + 1) if isinstance(deadline, (int, float)) and math.isfinite(deadline) else 1
            response = asyncio.run_coroutine_threadsafe(self._dispatch(request), self._loop).result(timeout=wait)
            win32file.WriteFile(pipe, _encode(response))
        except Exception:
            try:
                win32file.WriteFile(pipe, _encode(_error(None, "protocol", "Invalid control message")))
            except Exception:
                pass
        finally:
            win32api.CloseHandle(pipe)


class LocalControlClient:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def request(self, operation: str, payload: dict, timeout_seconds: float = 10) -> dict:
        if not isinstance(operation, str) or not operation or not isinstance(payload, dict):
            raise ValueError("operation must be text and payload must be an object")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        request_id = uuid.uuid4().hex
        request = {"version": PROTOCOL_VERSION, "id": request_id,
                   "deadline": time.time() + timeout_seconds,
                   "token": _token(self.data_dir, create=False),
                   "operation": operation, "payload": payload}
        encoded = _encode(request)
        if os.name == "nt":
            response = self._request_pipe_bounded(encoded, timeout_seconds)
        else:
            response = self._request_unix(encoded, timeout_seconds)
        if response.get("version") != PROTOCOL_VERSION or response.get("id") != request_id:
            raise LocalControlError("Invalid control response")
        if response.get("ok") is True and isinstance(response.get("result"), dict):
            return response["result"]
        error = response.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            raise LocalControlError(error["message"])
        raise LocalControlError("Invalid control response")

    def _request_pipe_bounded(self, encoded: bytes, timeout_seconds: float) -> dict:
        """Bound a blocking Win32 pipe call even if its peer stops responding.

        A timed-out command may still have reached the worker. Callers must
        inspect status before retrying Start; Stop callers write the durable
        local fence when this raises.
        """
        completed = threading.Event()
        outcome: dict = {}

        def perform() -> None:
            try:
                outcome["response"] = self._request_pipe(encoded, timeout_seconds)
            except BaseException as error:
                outcome["error"] = error
            finally:
                completed.set()

        threading.Thread(target=perform, name="grande-control-pipe", daemon=True).start()
        if not completed.wait(timeout_seconds):
            raise LocalControlError("Local control request timed out; its outcome is uncertain")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["response"]

    def _request_unix(self, encoded: bytes, timeout_seconds: float) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout_seconds)
            connection.connect(_address(self.data_dir))
            connection.sendall(encoded)

            def read_exact(count: int) -> bytes:
                chunks = bytearray()
                while len(chunks) < count:
                    chunk = connection.recv(count - len(chunks))
                    if not chunk:
                        raise LocalControlError("Connection closed")
                    chunks.extend(chunk)
                return bytes(chunks)

            return _read_sync(read_exact)

    def _request_pipe(self, encoded: bytes, timeout_seconds: float) -> dict:
        import pywintypes
        import win32con
        import win32file
        import win32pipe

        address = _address(self.data_dir)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                win32pipe.WaitNamedPipe(address, max(1, int((deadline - time.monotonic()) * 1000)))
                pipe = win32file.CreateFile(address, win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                            0, None, win32con.OPEN_EXISTING, 0, None)
                break
            except pywintypes.error as error:
                if error.winerror not in (2, 231) or time.monotonic() >= deadline:
                    raise LocalControlError("Local control pipe is unavailable") from error
                time.sleep(0.01)
        try:
            win32file.WriteFile(pipe, encoded)

            def read_exact(count: int) -> bytes:
                chunks = bytearray()
                while len(chunks) < count:
                    _, chunk = win32file.ReadFile(pipe, count - len(chunks))
                    if not chunk:
                        raise LocalControlError("Connection closed")
                    chunks.extend(chunk)
                return bytes(chunks)

            return _read_sync(read_exact)
        finally:
            win32file.CloseHandle(pipe)

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import keyring
from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

KEYRING_SERVICE = "GRANDEAlpha.RobinhoodMCP"
LEGACY_KEYRING_SERVICE = "MomentumTrader.RobinhoodMCP"


class CredentialTokenStorage(TokenStorage):
    """Store OAuth material in Windows Credential Manager through keyring."""

    def __init__(self, profile: str = "default") -> None:
        self.profile = profile

    def _get(self, suffix: str) -> str | None:
        username = f"{self.profile}:{suffix}"
        value = keyring.get_password(KEYRING_SERVICE, username)
        if value is None:
            value = keyring.get_password(LEGACY_KEYRING_SERVICE, username)
            if value is not None:
                keyring.set_password(KEYRING_SERVICE, username, value)
        return value

    def _set(self, suffix: str, value: str) -> None:
        keyring.set_password(KEYRING_SERVICE, f"{self.profile}:{suffix}", value)

    async def get_tokens(self) -> OAuthToken | None:
        raw = await asyncio.to_thread(self._get, "tokens")
        return OAuthToken.model_validate_json(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await asyncio.to_thread(self._set, "tokens", tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = await asyncio.to_thread(self._get, "client")
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await asyncio.to_thread(self._set, "client", client_info.model_dump_json())

    def clear(self) -> None:
        for service in (KEYRING_SERVICE, LEGACY_KEYRING_SERVICE):
            for suffix in ("tokens", "client"):
                try:
                    keyring.delete_password(service, f"{self.profile}:{suffix}")
                except keyring.errors.PasswordDeleteError:
                    pass


class OAuthCallbackServer:
    def __init__(self, port: int = 37654) -> None:
        self.port = port
        self.data: dict[str, Any] = {"code": None, "state": None, "error": None}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._event = threading.Event()

    def start(self) -> None:
        callback = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                params = parse_qs(urlparse(self.path).query)
                if "code" in params:
                    callback.data["code"] = params["code"][0]
                    callback.data["state"] = params.get("state", [None])[0]
                    status, title = 200, "Robinhood connected"
                else:
                    callback.data["error"] = params.get("error", ["OAuth callback failed"])[0]
                    status, title = 400, "Robinhood connection failed"
                callback._event.set()
                body = (
                    "<!doctype html><html><body style='font-family:Segoe UI;background:#0c1118;color:#f3f7fb;"
                    "display:grid;place-items:center;height:100vh'><main><h1>"
                    + title
                    + "</h1><p>You can close this tab and return to GRANDE Alpha.</p></main></body></html>"
                ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float = 300.0) -> tuple[str, str | None]:
        if not self._event.wait(timeout):
            raise TimeoutError("Timed out waiting for Robinhood OAuth")
        if self.data["error"]:
            raise RuntimeError(f"Robinhood OAuth failed: {self.data['error']}")
        return str(self.data["code"]), self.data["state"]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._server = None
        self._thread = None


def pretty_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str)

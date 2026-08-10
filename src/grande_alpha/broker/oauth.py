from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import keyring
from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

KEYRING_SERVICE = "GRANDEAlpha.RobinhoodMCP"
LEGACY_KEYRING_SERVICE = "MomentumTrader.RobinhoodMCP"
CHUNKED_CREDENTIAL_PREFIX = "GRANDE_ALPHA_CREDENTIAL_V1:"
# Windows Credential Manager limits generic credential blobs to 2,560 bytes. The Windows keyring
# backend stores text as UTF-16, so keep each base64 (ASCII) chunk comfortably below that boundary.
CHUNK_CHARACTERS = 900


class CredentialTokenStorage(TokenStorage):
    """Store OAuth material in Windows Credential Manager through keyring."""

    def __init__(self, profile: str = "default") -> None:
        self.profile = profile

    def _username(self, suffix: str) -> str:
        return f"{self.profile}:{suffix}"

    @staticmethod
    def _parse_manifest(raw: str | None) -> dict[str, Any] | None:
        if raw is None or not raw.startswith(CHUNKED_CREDENTIAL_PREFIX):
            return None
        try:
            manifest = json.loads(raw.removeprefix(CHUNKED_CREDENTIAL_PREFIX))
            version = str(manifest["version"])
            chunks = int(manifest["chunks"])
            digest = str(manifest["sha256"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Credential vault manifest is invalid") from exc
        if not version or chunks < 1 or len(digest) != 64:
            raise RuntimeError("Credential vault manifest is invalid")
        return {"version": version, "chunks": chunks, "sha256": digest}

    @staticmethod
    def _delete_password(service: str, username: str) -> None:
        try:
            keyring.delete_password(service, username)
        except keyring.errors.PasswordDeleteError:
            pass

    def _read_service(self, service: str, suffix: str) -> str | None:
        username = self._username(suffix)
        raw = keyring.get_password(service, username)
        manifest = self._parse_manifest(raw)
        if manifest is None:
            return raw

        parts = []
        for index in range(manifest["chunks"]):
            chunk_username = f"{username}:{manifest['version']}:{index:04d}"
            chunk = keyring.get_password(service, chunk_username)
            if chunk is None:
                raise RuntimeError("Credential vault record is incomplete")
            parts.append(chunk)
        encoded = "".join(parts)
        try:
            value = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("Credential vault record cannot be decoded") from exc
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, manifest["sha256"]):
            raise RuntimeError("Credential vault record failed its integrity check")
        return value

    def _get(self, suffix: str) -> str | None:
        value = self._read_service(KEYRING_SERVICE, suffix)
        if value is None:
            value = self._read_service(LEGACY_KEYRING_SERVICE, suffix)
            if value is not None:
                self._set(suffix, value)
        return value

    def _set(self, suffix: str, value: str) -> None:
        username = self._username(suffix)
        old_raw = keyring.get_password(KEYRING_SERVICE, username)
        old_manifest = self._parse_manifest(old_raw)
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        chunks = [
            encoded[start : start + CHUNK_CHARACTERS]
            for start in range(0, len(encoded), CHUNK_CHARACTERS)
        ] or [""]
        version = secrets.token_hex(8)
        written_usernames = []
        try:
            for index, chunk in enumerate(chunks):
                chunk_username = f"{username}:{version}:{index:04d}"
                keyring.set_password(KEYRING_SERVICE, chunk_username, chunk)
                written_usernames.append(chunk_username)
            manifest = CHUNKED_CREDENTIAL_PREFIX + json.dumps(
                {
                    "version": version,
                    "chunks": len(chunks),
                    "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                },
                separators=(",", ":"),
            )
            keyring.set_password(KEYRING_SERVICE, username, manifest)
        except Exception:
            for chunk_username in written_usernames:
                self._delete_password(KEYRING_SERVICE, chunk_username)
            raise

        if old_manifest is not None:
            for index in range(old_manifest["chunks"]):
                old_username = f"{username}:{old_manifest['version']}:{index:04d}"
                self._delete_password(KEYRING_SERVICE, old_username)

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
                username = self._username(suffix)
                raw = keyring.get_password(service, username)
                manifest = self._parse_manifest(raw)
                self._delete_password(service, username)
                if manifest is not None:
                    for index in range(manifest["chunks"]):
                        chunk_username = f"{username}:{manifest['version']}:{index:04d}"
                        self._delete_password(service, chunk_username)


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

"""Authenticated MCP session ownership and bounded broker request transport."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import webbrowser
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientMetadata

from grande_alpha.broker.base import BrokerError
from grande_alpha.broker.oauth import OAuthCallbackServer
from grande_alpha.broker.robinhood_contract import _exception_details, _tool_contract

TOOL_PRIORITIES = {
    "cancel_crypto_order": 0,
    "place_crypto_order": 1,
    "preview_crypto_order": 2,
    "cancel_equity_order": 0,
    "place_equity_order": 1,
    "review_equity_order": 2,
}
TOOL_TIMEOUT_SECONDS = {
    "cancel_crypto_order": 10.0,
    "place_crypto_order": 20.0,
    "preview_crypto_order": 15.0,
    "cancel_equity_order": 10.0,
    "place_equity_order": 20.0,
    "review_equity_order": 15.0,
}
DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0
DISCONNECT_GRACE_SECONDS = 3.0
DISCONNECT_CANCEL_SECONDS = 2.0
@dataclass
class _ToolRequest:
    name: str
    arguments: dict[str, Any]
    future: asyncio.Future[Any]
    timeout_seconds: float

class RobinhoodTransport:
    @property
    def connected(self) -> bool:
        return self._connected and self._worker is not None and not self._worker.done()

    @property
    def tools(self) -> set[str]:
        return set(self._tools)

    def tool_contract_snapshot(self) -> dict[str, Any]:
        """Provider-supplied metadata, never an authorization or account-data snapshot."""
        if not self.connected:
            raise BrokerError("Connect before inspecting the current MCP tool contract")
        contracts = [copy.deepcopy(self._tool_contracts[name]) for name in sorted(self._tool_contracts)]
        canonical = json.dumps(contracts, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return {
            "server_url": self.server_url,
            "observed_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "tools": contracts,
            "authority_granted": False,
        }

    def clear_credentials(self) -> None:
        if self.connected:
            raise BrokerError("Disconnect before forgetting stored broker credentials")
        self.storage.clear()

    def agent_tool_contracts(self) -> dict:
        if not self.connected:
            raise BrokerError("Connect Robinhood before exporting tool contracts")
        return {
            "schema_version": 1,
            "provider": "Robinhood Trading MCP",
            "notice": "Tool definitions only; no account data, quotes, credentials, or order calls.",
            "tools": copy.deepcopy(self._agent_tool_contracts),
        }

    async def connect(self) -> None:
        async with self._lifecycle_lock:
            if self.connected:
                return
            if self._worker is not None:
                await self._stop_worker()

            loop = asyncio.get_running_loop()
            ready: asyncio.Future[None] = loop.create_future()
            self._requests = asyncio.PriorityQueue()
            self._worker = asyncio.create_task(
                self._session_owner(ready),
                name="grande-alpha-robinhood-session",
            )
            try:
                await ready
            except BaseException as exc:
                if self._worker is not None and not self._worker.done():
                    self._worker.cancel()
                try:
                    await self._stop_worker()
                except BaseException:
                    # Preserve the readiness error. AnyIO may wrap the same leaf
                    # failure in a TaskGroup exception while the transport unwinds.
                    pass
                if isinstance(exc, asyncio.CancelledError):
                    raise
                details = _exception_details(exc)
                if details and details != str(exc):
                    raise BrokerError(details) from exc
                raise

    async def _session_owner(self, ready: asyncio.Future[None]) -> None:
        """Own the MCP contexts and every session call in one asyncio task.

        AnyIO transport cancel scopes must be exited by the same task that entered them.
        Public controller methods run in independent GUI timer tasks, so they communicate with
        this owner through a queue instead of touching ClientSession directly.
        """
        callback = OAuthCallbackServer()
        if self.allow_interactive_auth:
            callback.start()

        async def redirect_handler(url: str) -> None:
            if not self.allow_interactive_auth:
                raise BrokerError(
                    "This non-interactive connection requires cached OAuth credentials"
                )
            await asyncio.to_thread(webbrowser.open, url, 2)

        async def callback_handler() -> tuple[str, str | None]:
            if not self.allow_interactive_auth:
                raise BrokerError(
                    "This non-interactive connection cannot receive a new OAuth callback"
                )
            return await asyncio.to_thread(callback.wait, 300.0)

        metadata = OAuthClientMetadata.model_validate(
            {
                "client_name": "GRANDE Alpha",
                "redirect_uris": ["http://localhost:37654/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }
        )
        auth = OAuthClientProvider(
            server_url=self.server_url,
            client_metadata=metadata,
            storage=self.storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=300.0,
        )
        try:
            async with AsyncExitStack() as stack:
                streams = await stack.enter_async_context(
                    streamablehttp_client(
                        self.server_url,
                        timeout=30.0,
                        sse_read_timeout=300.0,
                        # Robinhood currently rejects the optional MCP DELETE-session request with 400.
                        # Closing the authenticated HTTP transport is sufficient and avoids a false warning.
                        terminate_on_close=False,
                        auth=auth,
                    )
                )
                read_stream, write_stream, _ = streams
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                listing = await session.list_tools()
                self._tools = {}
                self._tool_contracts = {}
                for item in listing.tools:
                    name, metadata = _tool_contract(item)
                    self._tools[name] = metadata["inputSchema"]
                    self._tool_contracts[name] = metadata
                agent_tool_names = {
                    "get_accounts", "get_portfolio", "get_equity_quotes", "get_equity_tradability",
                    "get_equity_positions", "get_equity_orders", "review_equity_order",
                    "place_equity_order", "cancel_equity_order", "get_currency_pairs",
                    "get_crypto_quotes", "get_crypto_positions", "get_crypto_orders",
                    "preview_crypto_order", "place_crypto_order", "cancel_crypto_order",
                    "get_scans", "run_scan", "get_scanner_filter_specs",
                }
                self._agent_tool_contracts = {
                    item.name: {
                        "input_schema": self._tools[item.name],
                        "output_schema": getattr(item, "outputSchema", None),
                        "description": getattr(item, "description", None),
                    }
                    for item in listing.tools if item.name in agent_tool_names
                }
                required = {
                    "get_accounts",
                    "get_portfolio",
                    "get_equity_quotes",
                    "get_equity_tradability",
                    "get_equity_positions",
                    "get_equity_orders",
                    "review_equity_order",
                    "place_equity_order",
                    "cancel_equity_order",
                }
                missing = sorted(required - self.tools)
                if missing:
                    raise BrokerError(f"Robinhood MCP is missing required tools: {', '.join(missing)}")

                self._connected = True
                self._accepting_calls = True
                ready.set_result(None)
                await self._serve_requests(session)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            self._connected = False
            self._accepting_calls = False
            self._tools.clear()
            self._tool_contracts.clear()
            self._agent_tool_contracts.clear()
            self._fail_pending_requests(BrokerError("Robinhood disconnected"))
            self._crypto.invalidate_reviews()
            if self.allow_interactive_auth:
                callback.stop()

    async def _serve_requests(self, session: ClientSession) -> None:
        if self._requests is None:
            raise RuntimeError("Robinhood request queue was not initialized")
        while True:
            _priority, _sequence, request = await self._requests.get()
            if request is None:
                return
            if request.future.cancelled():
                continue
            try:
                result = await session.call_tool(
                    request.name,
                    request.arguments,
                    read_timeout_seconds=timedelta(seconds=request.timeout_seconds),
                )
            except TimeoutError:
                if not request.future.done():
                    request.future.set_exception(
                        BrokerError(
                            f"Robinhood {request.name} timed out after "
                            f"{request.timeout_seconds:.0f}s; the remote outcome is unknown"
                        )
                    )
            except Exception as exc:
                if not request.future.done():
                    request.future.set_exception(exc)
            else:
                if not request.future.done():
                    request.future.set_result(result)
            finally:
                # A cancelled transport owner must also release the active caller.
                if not request.future.done():
                    request.future.set_exception(BrokerError(
                        f"Robinhood {request.name} was interrupted; the remote outcome is unknown"
                    ))

    def _fail_pending_requests(self, exc: Exception) -> None:
        if self._requests is None:
            return
        while True:
            try:
                _priority, _sequence, request = self._requests.get_nowait()
            except asyncio.QueueEmpty:
                return
            if request is not None and not request.future.done():
                request.future.set_exception(exc)

    async def _finish_worker(self) -> None:
        worker = self._worker
        if worker is not None and not worker.done():
            raise BrokerError("Previous Robinhood transport is still closing; retry disconnect shortly")
        self._worker = None
        try:
            if worker is not None and not worker.cancelled():
                worker.result()
        finally:
            self._requests = None
            self._connected = False
            self._accepting_calls = False
            self._tools.clear()
            self._tool_contracts.clear()
            self._agent_tool_contracts.clear()

    async def _stop_worker(self) -> None:
        """Bound teardown while the original task still owns every MCP context."""

        self._accepting_calls = False
        self._fail_pending_requests(BrokerError("Robinhood disconnected; queued request was not sent"))
        worker = self._worker
        if worker is not None and not worker.done():
            if self._requests is not None:
                self._requests.put_nowait((-100, next(self._request_sequence), None))
            done, _ = await asyncio.wait({worker}, timeout=DISCONNECT_GRACE_SECONDS)
            if not done:
                worker.cancel()
                done, _ = await asyncio.wait({worker}, timeout=DISCONNECT_CANCEL_SECONDS)
            if not done:
                # Retain the worker identity: reconnect cannot start a second owner.
                raise BrokerError("Robinhood transport is still closing; local broker requests are disabled")
        await self._finish_worker()

    async def disconnect(self) -> None:
        async with self._lifecycle_lock:
            self._crypto.invalidate_reviews()
            if self._worker is None:
                self._connected = False
                self._accepting_calls = False
                self._tools.clear()
                self._tool_contracts.clear()
                self._agent_tool_contracts.clear()
                return
            await self._stop_worker()

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.connected or not self._accepting_calls or self._requests is None:
            raise BrokerError("Robinhood is not connected")
        if name not in self._tools:
            raise BrokerError(f"Robinhood tool is unavailable: {name}")
        future = asyncio.get_running_loop().create_future()
        timeout_seconds = TOOL_TIMEOUT_SECONDS.get(name, DEFAULT_TOOL_TIMEOUT_SECONDS)
        priority = TOOL_PRIORITIES.get(name, 10)
        await self._requests.put(
            (
                priority,
                next(self._request_sequence),
                _ToolRequest(name, arguments, future, timeout_seconds),
            )
        )
        result = await future
        if getattr(result, "isError", False):
            message = "Robinhood tool error"
            for item in getattr(result, "content", []):
                if getattr(item, "type", "") == "text":
                    message = item.text
                    break
            raise BrokerError(message)
        payload = getattr(result, "structuredContent", None)
        if not payload:
            for item in getattr(result, "content", []):
                if getattr(item, "type", "") == "text":
                    try:
                        payload = json.loads(item.text)
                        break
                    except json.JSONDecodeError:
                        continue
        if not isinstance(payload, dict):
            raise BrokerError(f"Unexpected response from {name}")
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise BrokerError(f"Unexpected data from {name}")
        return data

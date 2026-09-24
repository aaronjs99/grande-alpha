from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from grande_alpha.execution.worker_ipc import (
    MAX_MESSAGE_BYTES,
    LocalControlClient,
    LocalControlError,
    LocalControlServer,
    _encode,
    _token,
)


def test_local_round_trip_and_multiple_clients(tmp_path):
    async def scenario():
        calls = []

        async def handler(operation, payload):
            calls.append((operation, payload))
            return {"echo": payload["value"]}

        server = LocalControlServer(tmp_path, handler)
        await server.start()
        try:
            clients = [LocalControlClient(tmp_path) for _ in range(4)]
            results = await asyncio.gather(*(
                asyncio.to_thread(client.request, "echo", {"value": index})
                for index, client in enumerate(clients)
            ))
            assert results == [{"echo": index} for index in range(4)]
            assert len(calls) == 4
        finally:
            await server.close()

    asyncio.run(scenario())


def test_handler_failure_is_explicit_without_secret_detail(tmp_path):
    async def scenario():
        async def handler(_operation, _payload):
            raise RuntimeError("sensitive-detail")

        server = LocalControlServer(tmp_path, handler)
        await server.start()
        try:
            with pytest.raises(LocalControlError, match="Control operation failed") as error:
                await asyncio.to_thread(LocalControlClient(tmp_path).request, "fail", {})
            assert "sensitive-detail" not in str(error.value)
        finally:
            await server.close()

    asyncio.run(scenario())


def test_oversized_request_is_rejected_before_transport(tmp_path):
    with pytest.raises(LocalControlError, match="64 KiB"):
        _encode({"payload": "x" * MAX_MESSAGE_BYTES})


@pytest.mark.skipif(os.name == "nt", reason="Unix token-file permissions")
def test_unix_token_file_is_private_and_stable(tmp_path):
    first = _token(tmp_path, create=True)
    assert _token(tmp_path, create=True) == first
    path = tmp_path / "worker-control.token"
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(LocalControlError, match="unsafe"):
        _token(tmp_path, create=False)


def test_invalid_response_shape_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("grande_alpha.execution.worker_ipc._token", lambda *_args, **_kwargs: "a" * 64)
    monkeypatch.setattr(LocalControlClient, "_request_pipe" if os.name == "nt" else "_request_unix",
                        lambda *_args: {"version": 1, "id": "wrong", "ok": True, "result": {}})
    with pytest.raises(LocalControlError, match="Invalid control response"):
        LocalControlClient(tmp_path).request("ping", {})


def test_stalled_pipe_request_returns_before_deadline(tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(LocalControlClient, "_request_pipe",
                        lambda *_args: release.wait(timeout=1))
    started = time.monotonic()
    try:
        with pytest.raises(LocalControlError, match="outcome is uncertain"):
            LocalControlClient(tmp_path)._request_pipe_bounded(b"request", 0.05)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()


def test_authentication_and_deadline_reject_before_handler(tmp_path):
    async def scenario():
        calls = []

        async def handler(_operation, _payload):
            calls.append(1)
            return {}

        server = LocalControlServer(tmp_path, handler)
        await server.start()
        try:
            base = {"version": 1, "id": "request-1", "operation": "ping", "payload": {},
                    "deadline": time.time() + 10, "token": "incorrect"}
            assert (await server._dispatch(base))["error"]["code"] == "authentication"
            base["token"] = _token(tmp_path, create=False)
            base["deadline"] = time.time() - 1
            assert (await server._dispatch(base))["error"]["code"] == "deadline"
            assert calls == []
        finally:
            await server.close()

    asyncio.run(scenario())

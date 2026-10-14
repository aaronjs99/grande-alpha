"""Optional-AI memory and locality checks; fake HTTP and disposable locks only."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from grande_alpha.execution.process_lock import ProcessLock
from grande_alpha.research.local_ollama import LocalOllamaClient

GIB = 1024**3


class FakeHTTP:
    def __init__(self):
        self.calls = []
        self.loaded = []
        self.capabilities = ["completion"]
        self.remote = {}
        self.ps_response = None
        self.entered = asyncio.Event()
        self.release = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def response(self, data, url):
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/ps") and self.ps_response is not None:
            return self.response(self.ps_response, url)
        return self.response(
            {"models": self.loaded if url.endswith("/ps") else [{"name": "fixture:latest", "size": GIB}]}, url
        )

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/show"):
            return self.response(
                {
                    "capabilities": self.capabilities,
                    "model_info": {"fixture.context_length": 8192},
                    **self.remote,
                },
                url,
            )
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return self.response({"done": True, "message": {"content": '{"reply":"Only a paper snapshot"}'}}, url)


class LocalAIAcceptance(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="grande-ai-check-")
        self.path = Path(self.scratch.name)

    def tearDown(self):
        self.scratch.cleanup()

    def client(self, http, *, memory=16 * GIB):
        return LocalOllamaClient(
            lock_path=self.path / "ai.lock",
            memory_available=lambda: memory,
            client_factory=lambda **kwargs: http,
        )

    def chat(self, client):
        return client.chat(
            {
                "model": "fixture",
                "messages": [{"role": "user", "content": "Explain"}],
                "format": {"type": "object"},
            },
            context_tokens=4096,
            output_tokens=768,
        )

    def test_low_or_unknown_ram_blocks_inference_and_never_downloads_models(self):
        for memory in (GIB, None):
            http = FakeHTTP()
            with self.assertRaises(ValueError):
                asyncio.run(self.chat(self.client(http, memory=memory)))
            self.assertFalse(any(url.endswith("/chat") or url.endswith("/pull") for url, _ in http.calls))

    def test_cloud_embedding_and_missing_models_are_rejected_before_inference(self):
        for kind in ("cloud", "embedding", "missing"):
            http = FakeHTTP()
            if kind == "cloud":
                http.remote = {"remote_host": "https://example.invalid", "remote_model": "fixture"}
            if kind == "embedding":
                http.capabilities = ["embedding"]
            client = self.client(http)
            payload = {"model": "missing:latest" if kind == "missing" else "fixture:latest", "messages": []}
            with self.assertRaises(ValueError):
                asyncio.run(client.chat(payload, context_tokens=4096, output_tokens=768))
            self.assertFalse(any(url.endswith("/chat") for url, _ in http.calls))

    def test_cold_request_has_explicit_context_output_and_release_options(self):
        http = FakeHTTP()
        reply = asyncio.run(self.chat(self.client(http)))
        self.assertTrue(reply["done"])
        request = [kwargs["json"] for url, kwargs in http.calls if url.endswith("/chat")][0]
        self.assertEqual(request["model"], "fixture:latest")
        self.assertEqual(request["options"]["num_ctx"], 4096)
        self.assertEqual(request["options"]["num_predict"], 768)
        self.assertEqual(request["keep_alive"], 0)
        self.assertIs(request["stream"], False)
        self.assertTrue(all(url.startswith("http://127.0.0.1:11434/api/") for url, _ in http.calls))

    def test_already_loaded_model_is_not_explicitly_unloaded(self):
        http = FakeHTTP()
        http.loaded = [{"name": "fixture:latest", "context_length": 4096}]
        asyncio.run(self.chat(self.client(http, memory=3 * GIB)))
        request = [kwargs["json"] for url, kwargs in http.calls if url.endswith("/chat")][0]
        self.assertNotIn("keep_alive", request)

    def test_other_grande_process_request_is_not_queued_or_overlapped(self):
        lock = ProcessLock(self.path / "ai.lock")
        self.assertTrue(lock.acquire())
        http = FakeHTTP()
        try:
            with self.assertRaises(ValueError):
                asyncio.run(self.chat(self.client(http)))
            self.assertEqual(http.calls, [])
        finally:
            lock.release()

    def test_cancellation_releases_local_lock_for_a_retry(self):
        async def scenario():
            http = FakeHTTP()
            http.release = asyncio.Event()
            task = asyncio.create_task(self.chat(self.client(http)))
            await http.entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            retry = FakeHTTP()
            self.assertTrue((await self.chat(self.client(retry)))["done"])

        asyncio.run(scenario())

    def test_unknown_loaded_state_cannot_be_treated_as_a_cold_model(self):
        for payload in (
            {},
            {"models": None},
            {"models": {}},
            {"models": [None]},
            {"models": [{"name": "fixture:latest", "context_length": None}]},
        ):
            http = FakeHTTP()
            http.ps_response = payload
            with self.assertRaisesRegex(ValueError, "Loaded model state"):
                asyncio.run(self.chat(self.client(http)))
            self.assertFalse(any(url.endswith("/chat") for url, _ in http.calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)

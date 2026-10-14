"""Explicit, bounded optional inference; never starts Ollama or downloads a model."""

from __future__ import annotations

import asyncio
import copy
import ctypes
import json
import os
import re
from pathlib import Path

import httpx

from grande_alpha.configuration.config import data_dir
from grande_alpha.execution.process_lock import ProcessLock

GIB = 1024**3
MIB = 1024**2
API = "http://127.0.0.1:11434/api"
RAM_RESERVE = 2 * GIB


def available_memory_bytes() -> int | None:
    """Available system RAM, not a promise about model or GPU allocation."""
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                *[
                    (name, ctypes.c_ulonglong)
                    for name in (
                        "total_physical",
                        "available_physical",
                        "total_page",
                        "available_page",
                        "total_virtual",
                        "available_virtual",
                        "available_extended",
                    )
                ],
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.available_physical)
        return None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _body(response, *, max_bytes=262_144):
    response.raise_for_status()
    if len(response.content) > max_bytes:
        raise ValueError("Optional AI response exceeds the size limit")
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Unexpected local Ollama response")
    return result


class LocalOllamaClient:
    def __init__(self, *, lock_path=None, memory_available=available_memory_bytes, client_factory=None):
        self.lock_path = Path(lock_path) if lock_path is not None else None
        self.memory_available = memory_available
        self.client_factory = client_factory or httpx.AsyncClient

    async def chat(
        self, payload: dict, *, context_tokens: int, output_tokens: int, timeout_seconds=45.0
    ) -> dict:
        model = payload.get("model")
        if not isinstance(model, str) or not re.fullmatch(r"[\w./:\-]{1,120}", model):
            raise ValueError("Enter an installed local Ollama model name")
        if (
            type(context_tokens) is not int
            or not 1024 <= context_tokens <= 8192
            or type(output_tokens) is not int
            or not 1 <= output_tokens <= 2048
        ):
            raise ValueError("Optional AI context or output exceeds its limit")
        lock = ProcessLock(self.lock_path or data_dir() / "local-ai.lock")
        if not lock.acquire():
            raise ValueError("Another GRANDE AI request is in progress; retry after it finishes")
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.client_factory(
                    trust_env=False, follow_redirects=False, timeout=httpx.Timeout(timeout_seconds, connect=5)
                ) as client:
                    tags = _body(await client.get(f"{API}/tags", timeout=5))
                    installed = tags.get("models")
                    names = {model, model + ":latest"} if ":" not in model else {model}
                    selected = (
                        next(
                            (
                                item
                                for item in installed
                                if isinstance(item, dict) and item.get("name") in names
                            ),
                            None,
                        )
                        if isinstance(installed, list)
                        else None
                    )
                    if selected is None:
                        raise ValueError("That model is not installed. Core sessions work with AI off")
                    name = selected["name"]
                    show = _body(await client.post(f"{API}/show", json={"model": name}, timeout=5))
                    if (
                        name.endswith("-cloud")
                        or ":cloud" in name
                        or show.get("remote_host")
                        or show.get("remote_model")
                        or selected.get("remote_host")
                        or selected.get("remote_model")
                    ):
                        raise ValueError("Choose a local model; cloud-routed Ollama models are not supported")
                    capabilities = show.get("capabilities")
                    if not isinstance(capabilities, list) or "completion" not in capabilities:
                        raise ValueError("Choose a chat model; embedding-only models cannot answer questions")
                    size = selected.get("size")
                    if type(size) is not int or size < MIB:
                        raise ValueError("Local model size could not be verified; optional AI stays off")
                    model_info = show.get("model_info")
                    lengths = (
                        [
                            value
                            for key, value in model_info.items()
                            if key.endswith(".context_length") and type(value) is int and value > 0
                        ]
                        if isinstance(model_info, dict)
                        else []
                    )
                    if lengths:
                        context_tokens = min(context_tokens, min(lengths))
                    if context_tokens < 1024 or output_tokens >= context_tokens:
                        raise ValueError("This model's context is too small for the bounded request")
                    running = _body(await client.get(f"{API}/ps", timeout=5)).get("models")
                    if not isinstance(running, list) or any(
                        not isinstance(item, dict)
                        or not isinstance(item.get("name"), str)
                        or not item["name"]
                        or (
                            "context_length" in item
                            and (type(item["context_length"]) is not int or item["context_length"] <= 0)
                        )
                        for item in running
                    ):
                        raise ValueError("Loaded model state could not be verified; optional AI stays off")
                    loaded = next((item for item in running if item["name"] == name), None)
                    free = self.memory_available()
                    if type(free) is not int or free <= 0:
                        raise ValueError("Available RAM could not be measured; optional AI stays off")
                    # Conservative admission estimate. Actual allocation varies by architecture,
                    # quantization and GPU offload; other applications can consume RAM afterward.
                    loaded_context = loaded.get("context_length", 0) if loaded else 0
                    loaded_context = (
                        loaded_context if type(loaded_context) is int and loaded_context > 0 else 0
                    )
                    extra_context = max(0, context_tokens - loaded_context)
                    needed = RAM_RESERVE + 512 * MIB + extra_context * 256 * 1024
                    if loaded is None:
                        needed += int(size * 1.15)
                    if free < needed:
                        raise ValueError(
                            f"Optional AI needs about {needed / GIB:.1f} GiB free RAM; "
                            f"{free / GIB:.1f} GiB is available. Keep AI off or use a smaller model"
                        )
                    request = copy.deepcopy(payload)
                    request.update(model=name, stream=False)
                    request.setdefault("options", {}).update(
                        temperature=0, num_ctx=context_tokens, num_predict=output_tokens
                    )
                    if "thinking" in capabilities:
                        request["think"] = False
                    # Only a model observed cold receives the immediate-release request.
                    # Do not explicitly unload models already in use by another application.
                    if loaded is None:
                        request["keep_alive"] = 0
                    if len(json.dumps(request, allow_nan=False).encode("utf-8")) > 65_536:
                        raise ValueError("Optional AI input is too large; reduce the prompts or watchlist")
                    return _body(await client.post(f"{API}/chat", json=request), max_bytes=131_072)
        finally:
            lock.release()

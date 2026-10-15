from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path

from grande_alpha.configuration.config import data_dir
from grande_alpha.configuration.json_inputs import load_json
from grande_alpha.domain.market_models import Signal
from grande_alpha.research.bar_replay import SandboxReplayEngine
from grande_alpha.research.historical_models import HistoricalBundle
from grande_alpha.research.runtime_replay import RuntimeObservationReplayEngine
from grande_alpha.research.sandbox_models import SandboxConfig, SandboxResult


class SandboxReplayRunner:
    """Lock one evidence run to either exact-runtime or generic replay semantics."""

    def __init__(self, *, exact_runtime_observation: bool = False) -> None:
        self.exact_runtime_observation = exact_runtime_observation

    @classmethod
    def for_evidence_bundle(cls, bundle: HistoricalBundle) -> SandboxReplayRunner:
        return cls(exact_runtime_observation=bundle.runtime_observation_parity_eligible)

    def run(
        self,
        bundle: HistoricalBundle,
        config: SandboxConfig,
        *,
        signals: tuple[Signal, ...] | list[Signal] | None = None,
    ) -> SandboxResult:
        if self.exact_runtime_observation:
            # RuntimeObservationReplayEngine rejects even one non-exact child frame. There is
            # intentionally no catch-and-fallback path to generic OHLCV execution.
            return RuntimeObservationReplayEngine(config).run_result(bundle, signals=signals)
        if signals is not None:
            raise ValueError("Custom causal signals are supported only by exact runtime replay")
        return SandboxReplayEngine(config).run(bundle)

def sandbox_config_path() -> Path:
    return data_dir() / "sandbox_config.json"


def load_sandbox_config() -> SandboxConfig:
    path = sandbox_config_path()
    problem = "are invalid or unreadable"
    try:
        raw = load_json(path, max_bytes=65_536)
        if not isinstance(raw, dict):
            raise ValueError("Sandbox settings must be a JSON object")
        allowed = SandboxConfig.__dataclass_fields__.keys()
        unknown = sorted(raw.keys() - allowed)
        if unknown:
            problem = "contain unknown fields: " + ", ".join(repr(key) for key in unknown)
            raise ValueError(problem)
        config = SandboxConfig(**raw)
        config.validate()
        return config
    except FileNotFoundError:
        return SandboxConfig()
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValueError(
            f"Saved sandbox settings at {path} {problem}. "
            "Correct this file or move it aside before retrying; it was left unchanged."
        ) from None


def save_sandbox_config(config: SandboxConfig) -> None:
    config.validate()
    payload = json.dumps(asdict(config), indent=2, allow_nan=False)
    path = sandbox_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.pending")
    try:
        with pending.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from grande_alpha.configuration.config import data_dir
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
    if not path.exists():
        return SandboxConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = SandboxConfig.__dataclass_fields__.keys()
        config = SandboxConfig(**{key: value for key, value in raw.items() if key in allowed})
        config.validate()
        return config
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return SandboxConfig()


def save_sandbox_config(config: SandboxConfig) -> None:
    config.validate()
    path = sandbox_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")

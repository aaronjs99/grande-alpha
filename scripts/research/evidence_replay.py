from __future__ import annotations

from grande_alpha.domain.policy import session_key
from grande_alpha.research.historical import HistoricalBundle, assess_quality
from grande_alpha.research.sandbox import SandboxReplayRunner
from grande_alpha.research.sandbox_models import SandboxConfig, SandboxResult


def _run_replay(
    bundle: HistoricalBundle,
    config: SandboxConfig,
    replay_runner: SandboxReplayRunner | None,
) -> SandboxResult:
    return (replay_runner or SandboxReplayRunner()).run(bundle, config)


def _sessions(bundle: HistoricalBundle) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for frame in bundle.frames:
        day = session_key(frame.start, bundle.market_hours)
        grouped.setdefault(day, []).append(frame)
    return grouped


def _subset(bundle: HistoricalBundle, session_names: list[str]) -> HistoricalBundle:
    allowed = set(session_names)
    frames = [frame for frame in bundle.frames if session_key(frame.start, bundle.market_hours) in allowed]
    quality = assess_quality(frames, bundle.interval, bundle.market_hours)
    return HistoricalBundle(
        source=bundle.source,
        downloaded_at=bundle.downloaded_at,
        frames=frames,
        interval=bundle.interval,
        dataset_hash=quality.dataset_hash,
        quality=quality,
        market_hours=bundle.market_hours,
        provenance=bundle.provenance,
    )

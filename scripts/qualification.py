"""Exact-candidate production qualification certificate validation."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grande_alpha.json_inputs import load_json

# Historical certificate format only. This inventory digest is not a live gate.
HISTORICAL_BROKER_CONTRACT_SHA256 = "15f945f6287aecf6d89140c57e0fe0f6a0efff89ef53268a6f6afbcdbae8bc1f"

REQUIRED_RECOVERY = {"stop", "daily_loss", "duplicate_process", "unknown_order", "restart"}


class ProductionGate:
    def __init__(self, certificate_path: Path, *, now=lambda: datetime.now(UTC)):
        self.path, self.now = certificate_path, now

    def __call__(self, candidate_digest: str) -> bool:
        value = load_json(self.path, max_bytes=1_000_000)
        required = {"schema_version", "candidate_digest", "broker_contract_sha256", "issued_at",
                    "expires_at", "historical", "forward", "failure_recovery"}
        if not isinstance(value, dict) or set(value) != required or value["schema_version"] != 1:
            raise RuntimeError("Qualification certificate schema is invalid")
        try:
            issued = datetime.fromisoformat(value["issued_at"])
            expires = datetime.fromisoformat(value["expires_at"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Qualification certificate times are invalid") from exc
        current = self.now()
        if any(t.tzinfo is None or t.utcoffset() is None for t in (issued, expires, current)):
            raise RuntimeError("Qualification certificate times must include offsets")
        if value["candidate_digest"] != candidate_digest or value["broker_contract_sha256"] != HISTORICAL_BROKER_CONTRACT_SHA256:
            raise RuntimeError("Qualification certificate does not match the candidate and broker contract")
        if not issued <= current < expires or expires - issued > timedelta(days=30):
            raise RuntimeError("Qualification certificate is expired, future-dated, or too long-lived")
        historical, forward = value["historical"], value["forward"]
        for report, fields in ((historical, {"input_sha256", "frames", "net_change_after_costs", "max_drawdown_usd"}),
                               (forward, {"input_sha256", "sessions", "decisions", "net_change_after_costs",
                                          "max_drawdown_usd", "last_observed_at"})):
            if not isinstance(report, dict) or set(report) != fields:
                raise RuntimeError("Qualification evidence report is incomplete")
        for report, count_fields in ((historical, ("frames",)),
                                     (forward, ("sessions", "decisions"))):
            if any(type(report[key]) is not int or report[key] < 0 for key in count_fields):
                raise RuntimeError("Qualification evidence counts are invalid")
            if any(type(report[key]) not in {int, float} or not math.isfinite(report[key])
                   for key in ("net_change_after_costs", "max_drawdown_usd")):
                raise RuntimeError("Qualification evidence metrics are invalid")
            if report["max_drawdown_usd"] < 0:
                raise RuntimeError("Qualification drawdown must be a nonnegative loss magnitude")
            if not isinstance(report["input_sha256"], str) or len(report["input_sha256"]) != 64:
                raise RuntimeError("Qualification evidence digest is invalid")
        if historical["frames"] < 100 or historical["net_change_after_costs"] <= 0:
            raise RuntimeError("Historical evidence is insufficient or non-positive after costs")
        try:
            last = datetime.fromisoformat(forward["last_observed_at"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Forward evidence time is invalid") from exc
        if last.tzinfo is None or last.utcoffset() is None:
            raise RuntimeError("Forward evidence time must include an offset")
        if (forward["sessions"] < 20 or forward["decisions"] < 100
                or forward["net_change_after_costs"] <= 0 or not timedelta(0) <= current-last <= timedelta(days=7)):
            raise RuntimeError("Forward evidence is insufficient, stale, or non-positive after costs")
        recovery = value["failure_recovery"]
        if not isinstance(recovery, dict) or set(recovery) != REQUIRED_RECOVERY or any(v is not True for v in recovery.values()):
            raise RuntimeError("Failure-recovery qualification is incomplete")
        # Canonical JSON readback catches NaN/Infinity and non-serializable surprises.
        json.dumps(value, sort_keys=True, allow_nan=False)
        return True


def command_qualification_check(args) -> int:
    passed = ProductionGate(Path(args.certificate))(args.candidate_digest)
    print(json.dumps({"qualified": passed, "authority_granted": False}, indent=2))
    return 0

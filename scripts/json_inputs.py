"""Bounded, unambiguous JSON inputs for user-supplied research and policy files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON constant: {value}")


def load_json(path: Path, *, max_bytes: int) -> Any:
    with path.open("rb") as source:
        raw = source.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"JSON input exceeds {max_bytes} bytes")
    return json.loads(
        raw.decode("utf-8-sig"), object_pairs_hook=unique_object, parse_constant=_invalid_constant
    )

"""Strict numeric fields shared by research configuration records."""

from __future__ import annotations

import math
from numbers import Real


def require_integer_fields(config: object, names: tuple[str, ...]) -> None:
    for name in names:
        if type(getattr(config, name)) is not int:
            raise ValueError(f"{name} must be an integer")


def require_finite_fields(config: object, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{name} must be a finite number")
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"{name} must be a finite number")

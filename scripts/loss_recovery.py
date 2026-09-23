"""Compute durable loss-stop recovery deadlines in the U.S. exchange timezone."""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime, timedelta

from grande_alpha.portfolio_replay import EASTERN

RECOVERY_UNITS = frozenset({"manual", "minutes", "hours", "days", "weeks", "months", "years"})


def validate_recovery(value: int | None, unit: str) -> None:
    if unit not in RECOVERY_UNITS:
        raise ValueError("Loss recovery unit must be manual, minutes, hours, days, weeks, months, or years")
    if unit == "manual":
        if value is not None:
            raise ValueError("Manual loss recovery cannot have a delay")
    elif type(value) is not int or value <= 0:
        raise ValueError("Timed loss recovery requires a positive integer delay")


def recovery_deadline(start: datetime, value: int | None, unit: str) -> datetime | None:
    validate_recovery(value, unit)
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("Loss recovery start time must include an offset")
    if unit == "manual":
        return None
    if unit in {"minutes", "hours"}:
        duration = timedelta(minutes=value) if unit == "minutes" else timedelta(hours=value)
        return start.astimezone(UTC) + duration
    eastern = start.astimezone(EASTERN)
    if unit in {"days", "weeks"}:
        target = eastern.date() + timedelta(days=value * (7 if unit == "weeks" else 1))
    else:
        months = value * (12 if unit == "years" else 1)
        month_index = eastern.year * 12 + eastern.month - 1 + months
        year, zero_month = divmod(month_index, 12)
        month = zero_month + 1
        target = eastern.date().replace(year=year, month=month,
                                        day=min(eastern.day, monthrange(year, month)[1]))
    # Calendar units retain the wall-clock time across daylight-saving changes.
    return eastern.replace(year=target.year, month=target.month, day=target.day).astimezone(UTC)

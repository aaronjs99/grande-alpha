"""Algorithmic U.S. cash-equity regular-session calendar.

The rules in this module cover the recurring full-day holidays and scheduled
1:00 p.m. Eastern early closes shared by the NYSE and Nasdaq cash-equity
markets.  They cannot anticipate ad-hoc emergency closures, national days of
mourning, exchange-specific changes, provider outages, or symbol-level trading
halts.  Live execution must therefore still fail closed when current broker or
venue status cannot be confirmed.

New Year's Day is intentionally special-cased: when January 1 falls on a
Saturday, NYSE/Nasdaq do not observe it on the preceding Friday.  Sunday New
Year's Day is observed on Monday.  The other fixed-date holidays use the usual
Friday/Monday weekend observation convention.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, time, timedelta
from functools import cache

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _new_years_observed(year: int) -> date:
    holiday = date(year, 1, 1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _western_easter(year: int) -> date:
    """Return Gregorian Easter Sunday using the Anonymous Gregorian algorithm."""

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return date(year, month, day)


@cache
def us_equity_holidays(year: int) -> frozenset[date]:
    """Return scheduled NYSE/Nasdaq full-day cash-equity holidays for ``year``."""

    holidays = {
        _new_years_observed(year),
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday / Presidents Day
        _western_easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed_fixed_holiday(year, 7, 4),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
        _observed_fixed_holiday(year, 12, 25),  # Christmas Day
    }
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(year, 6, 19))  # Juneteenth
    return frozenset(day for day in holidays if day.year == year)


@cache
def us_equity_early_closes(year: int) -> frozenset[date]:
    """Return scheduled 1:00 p.m. Eastern cash-equity closes for ``year``."""

    early_closes: set[date] = set()

    # The July early close is July 3 when it is itself a Monday-Thursday
    # trading day.  It is not shifted to July 2 when July 3 is a weekend or the
    # observed Independence Day holiday.
    july_third = date(year, 7, 3)
    if july_third.weekday() < 4:
        early_closes.add(july_third)

    thanksgiving = _nth_weekday(year, 11, 3, 4)
    early_closes.add(thanksgiving + timedelta(days=1))

    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 4:
        early_closes.add(christmas_eve)

    holidays = us_equity_holidays(year)
    return frozenset(
        day for day in early_closes if day.weekday() < 5 and day not in holidays
    )


def regular_session_times(trade_date: date) -> tuple[time, time] | None:
    """Return the scheduled regular-session open/close, or ``None`` if closed."""

    if trade_date.weekday() >= 5 or trade_date in us_equity_holidays(trade_date.year):
        return None
    close = EARLY_CLOSE if trade_date in us_equity_early_closes(trade_date.year) else REGULAR_CLOSE
    return REGULAR_OPEN, close


def is_regular_trading_day(trade_date: date) -> bool:
    """Return whether a scheduled NYSE/Nasdaq cash-equity session exists."""

    return regular_session_times(trade_date) is not None

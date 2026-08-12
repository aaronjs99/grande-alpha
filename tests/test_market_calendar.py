from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from grande_alpha.market_calendar import (
    is_regular_trading_day,
    regular_session_times,
    us_equity_early_closes,
    us_equity_holidays,
)
from grande_alpha.policy import market_session_allowed, session_bounds

EASTERN = ZoneInfo("America/New_York")


def test_2026_exchange_calendar_matches_published_full_and_early_closes() -> None:
    assert us_equity_holidays(2026) == frozenset(
        {
            date(2026, 1, 1),
            date(2026, 1, 19),
            date(2026, 2, 16),
            date(2026, 4, 3),
            date(2026, 5, 25),
            date(2026, 6, 19),
            date(2026, 7, 3),
            date(2026, 9, 7),
            date(2026, 11, 26),
            date(2026, 12, 25),
        }
    )
    assert us_equity_early_closes(2026) == frozenset(
        {date(2026, 11, 27), date(2026, 12, 24)}
    )


def test_normal_2026_session_uses_four_pm_close() -> None:
    timestamp = datetime(2026, 8, 11, 12, 0, tzinfo=EASTERN)
    opened, closed = session_bounds(timestamp, "regular_hours")

    assert (opened.time(), closed.time()) == (time(9, 30), time(16, 0))
    assert market_session_allowed(timestamp, 0, 0, "regular_hours")


def test_day_after_thanksgiving_2026_closes_at_one_pm() -> None:
    before_close = datetime(2026, 11, 27, 12, 49, tzinfo=EASTERN)
    after_close = datetime(2026, 11, 27, 13, 1, tzinfo=EASTERN)
    opened, closed = session_bounds(before_close, "regular_hours")

    assert (opened.time(), closed.time()) == (time(9, 30), time(13, 0))
    assert market_session_allowed(before_close, 0, 10, "regular_hours")
    assert not market_session_allowed(after_close, 0, 0, "regular_hours")


def test_full_holidays_fail_closed() -> None:
    for holiday in (date(2026, 7, 3), date(2026, 12, 25)):
        midday = datetime.combine(holiday, time(12), tzinfo=EASTERN)
        opened, closed = session_bounds(midday, "regular_hours")
        assert opened == closed
        assert not is_regular_trading_day(holiday)
        assert not market_session_allowed(midday, 0, 0, "regular_hours")


def test_weekends_and_holidays_fail_closed_for_extended_and_overnight_equity_modes() -> None:
    for closed_date in (date(2026, 8, 9), date(2026, 12, 25)):
        midday = datetime.combine(closed_date, time(12), tzinfo=EASTERN)
        for market_hours in ("extended_hours", "all_day_hours"):
            opened, closed = session_bounds(midday, market_hours)
            assert opened == closed
            assert not market_session_allowed(midday, 0, 0, market_hours)


def test_observation_and_early_close_rules_extend_beyond_2026() -> None:
    assert date(2027, 6, 18) in us_equity_holidays(2027)
    assert date(2027, 7, 5) in us_equity_holidays(2027)
    assert date(2027, 12, 24) in us_equity_holidays(2027)
    assert date(2027, 12, 31) not in us_equity_holidays(2027)
    assert date(2028, 7, 3) in us_equity_early_closes(2028)
    assert regular_session_times(date(2021, 6, 18)) == (time(9, 30), time(16, 0))


def test_timezone_conversion_tracks_eastern_dst() -> None:
    winter = datetime(2026, 1, 2, 14, 35, tzinfo=UTC)
    summer = datetime(2026, 8, 11, 13, 35, tzinfo=UTC)
    winter_open, _ = session_bounds(winter, "regular_hours")
    summer_open, _ = session_bounds(summer, "regular_hours")

    assert winter_open.utcoffset() == -timedelta(hours=5)
    assert summer_open.utcoffset() == -timedelta(hours=4)
    assert market_session_allowed(winter, 5, 0, "regular_hours")
    assert market_session_allowed(summer, 5, 0, "regular_hours")

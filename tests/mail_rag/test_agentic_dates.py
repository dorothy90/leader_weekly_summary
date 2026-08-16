from datetime import UTC, datetime

import pytest

from app.retrieval.dates import resolve_time_range


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def test_last_week_uses_seoul_calendar_and_half_open_utc_range():
    resolved = resolve_time_range("지난주", now=NOW, timezone_name="Asia/Seoul")
    assert resolved.expression == "지난주"
    assert resolved.start_at_utc == datetime(2026, 8, 2, 15, tzinfo=UTC)
    assert resolved.end_at_utc == datetime(2026, 8, 9, 15, tzinfo=UTC)


def test_this_week_uses_seoul_monday_and_half_open_utc_range():
    resolved = resolve_time_range("이번주", now=NOW, timezone_name="Asia/Seoul")
    assert resolved.expression == "이번주"
    assert resolved.start_at_utc == datetime(2026, 8, 9, 15, tzinfo=UTC)
    assert resolved.end_at_utc == datetime(2026, 8, 16, 15, tzinfo=UTC)


def test_yesterday_and_last_month_are_deterministic():
    yesterday = resolve_time_range("어제", now=NOW)
    last_month = resolve_time_range("지난달", now=NOW)
    assert yesterday.start_at_utc == datetime(2026, 8, 14, 15, tzinfo=UTC)
    assert yesterday.end_at_utc == datetime(2026, 8, 15, 15, tzinfo=UTC)
    assert last_month.start_at_utc == datetime(2026, 6, 30, 15, tzinfo=UTC)
    assert last_month.end_at_utc == datetime(2026, 7, 31, 15, tzinfo=UTC)


def test_unknown_time_expression_does_not_guess():
    assert resolve_time_range("최근 적당한 때", now=NOW) is None


def test_naive_now_is_rejected_instead_of_using_host_timezone():
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_time_range("어제", now=datetime(2026, 8, 16, 12, 0))

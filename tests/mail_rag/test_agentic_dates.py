from datetime import UTC, date, datetime

import pytest

from app.retrieval.dates import resolve_time_scope


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def test_current_week_resolves_to_seoul_half_open_range():
    resolved = resolve_time_scope(
        "current_week", now=NOW, timezone_name="Asia/Seoul"
    )

    assert resolved.start_at_utc == datetime(2026, 8, 16, 15, tzinfo=UTC)
    assert resolved.end_at_utc == datetime(2026, 8, 23, 15, tzinfo=UTC)


def test_exact_date_resolves_to_one_local_calendar_day():
    resolved = resolve_time_scope(
        "exact_date",
        exact_date=date(2026, 8, 7),
        now=NOW,
        timezone_name="Asia/Seoul",
    )

    assert resolved.start_at_utc == datetime(2026, 8, 6, 15, tzinfo=UTC)
    assert resolved.end_at_utc == datetime(2026, 8, 7, 15, tzinfo=UTC)


def test_none_scope_has_no_range():
    assert resolve_time_scope("none", now=NOW) is None


def test_exact_date_scope_requires_date():
    with pytest.raises(ValueError, match="exact_date"):
        resolve_time_scope("exact_date", now=NOW)

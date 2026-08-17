from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.agentic import ResolvedTimeRange, TimeScope


def _local_midnight(value: datetime, zone: ZoneInfo) -> datetime:
    local = value.astimezone(zone)
    return datetime.combine(local.date(), time.min, tzinfo=zone)


def resolve_time_scope(
    scope: TimeScope,
    *,
    exact_date: date | None = None,
    now: datetime | None = None,
    timezone_name: str = "Asia/Seoul",
) -> ResolvedTimeRange | None:
    if scope == "none":
        if exact_date is not None:
            raise ValueError("exact_date is valid only for exact_date scope")
        return None
    if (scope == "exact_date") != (exact_date is not None):
        raise ValueError("exact_date must be present only for exact_date scope")
    zone = ZoneInfo(timezone_name)
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    today = _local_midnight(current, zone)
    if scope == "yesterday":
        start, end = today - timedelta(days=1), today
    elif scope == "previous_week":
        this_monday = today - timedelta(days=today.weekday())
        start, end = this_monday - timedelta(days=7), this_monday
    elif scope == "current_week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    elif scope == "previous_month":
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
    else:
        start = datetime.combine(exact_date, time.min, tzinfo=zone)
        end = datetime.combine(exact_date + timedelta(days=1), time.min, tzinfo=zone)
    return ResolvedTimeRange(
        scope=scope,
        start_at_utc=start.astimezone(UTC),
        end_at_utc=end.astimezone(UTC),
    )

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.agentic import ResolvedTimeRange


def _local_midnight(value: datetime, zone: ZoneInfo) -> datetime:
    local = value.astimezone(zone)
    return datetime.combine(local.date(), time.min, tzinfo=zone)


def resolve_time_range(
    expression: str | None,
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Seoul",
) -> ResolvedTimeRange | None:
    normalized = " ".join((expression or "").split())
    if not normalized:
        return None
    zone = ZoneInfo(timezone_name)
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(zone)
    today = _local_midnight(current, zone)
    if normalized == "어제":
        start, end = today - timedelta(days=1), today
    elif normalized == "지난주":
        this_monday = today - timedelta(days=today.weekday())
        start, end = this_monday - timedelta(days=7), this_monday
    elif normalized == "이번주":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    elif normalized == "지난달":
        first_this_month = today.replace(day=1)
        end = first_this_month
        start = (first_this_month - timedelta(days=1)).replace(day=1)
    else:
        try:
            explicit_date = date.fromisoformat(normalized)
        except ValueError:
            return None
        start = datetime.combine(explicit_date, time.min, tzinfo=zone)
        end = datetime.combine(
            explicit_date + timedelta(days=1),
            time.min,
            tzinfo=zone,
        )
    return ResolvedTimeRange(
        expression=normalized,
        start_at_utc=start.astimezone(UTC),
        end_at_utc=end.astimezone(UTC),
    )

"""Whitelisted plans and bounded, owner-bound conversational state."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Source = Literal["weekly_mail", "ews_mail", "ews_calendar", "wiki_summary"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class QuickConditions(Strict):
    teams: list[str] = Field(default_factory=list, max_length=10)
    weeks: list[str] = Field(default_factory=list, max_length=52)
    mail_type: Literal["weekly_report", "daily_report", "other"] | None = Field(
        default=None,
        description="Only when the user explicitly requests this report/mail category. Ordinary email is not other; default null.",
    )
    topic: str | None = Field(
        default=None,
        max_length=200,
        description="Exact wiki metadata category only when source_scope=wiki. For email, calendar, reports or broad questions, put subject terms in query instead.",
    )
    summary_type: str | None = Field(default=None, max_length=100)
    sender_name: str | None = Field(default=None, max_length=100)
    sender_email: str | None = Field(default=None, max_length=254)
    organizer_email: str | None = Field(default=None, max_length=254)
    attendee_email: str | None = Field(default=None, max_length=254)
    attachment_name: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    date_start: str | None = Field(default=None, max_length=40)
    date_end: str | None = Field(default=None, max_length=40)
    date_period: (
        Literal[
            "today", "yesterday", "this_week", "last_week", "this_month", "last_month"
        ]
        | None
    ) = None

    @field_validator("sender_email", "organizer_email", "attendee_email")
    @classmethod
    def email(cls, v):
        if v is not None:
            import re

            if not re.fullmatch("[^\\s@]+@[^\\s@]+\\.[^\\s@]+", v):
                raise ValueError("literal email required")
            return v.casefold()
        return v


class QuickPlan(Strict):
    intent: Literal["list", "answer", "general", "clarify", "statistics"] = "answer"
    source_scope: Literal["all", "mail", "calendar", "wiki", "report"] = "all"
    scope: Literal["new", "follow_up", "within_results"] = "new"
    query: str = Field(default="", max_length=1000)
    search_queries: list[str] = Field(
        default_factory=list,
        max_length=2,
        description="Up to two short alternative content queries preserving the topic; no added dates or identities. Empty for metadata-only lookup.",
    )
    conditions: QuickConditions = Field(default_factory=QuickConditions)
    recent: bool | None = None
    mail_type_expression: str = Field(
        default="",
        max_length=100,
        description="Server-validated explicit mail category phrase; empty if no mail type requested.",
    )
    temporal_expression: str = Field(
        default="",
        max_length=200,
        description="Exact current-question substring requesting a time restriction. Empty when no time restriction is requested; never use current time as evidence.",
    )
    clear_conditions: list[
        Literal[
            "query",
            "recent",
            "dates",
            "teams",
            "weeks",
            "mail_type",
            "topic",
            "summary_type",
            "sender_name",
            "sender_email",
            "organizer_email",
            "attendee_email",
            "attachment_name",
            "location",
            "date_start",
            "date_end",
            "date_period",
        ]
    ] = Field(default_factory=list, max_length=18)
    investigate: bool = Field(
        default=False,
        description="True for requests to investigate problems, causes, chronology, actions, or continue an unresolved investigation; false for simple lookup/summary.",
    )
    prefer_upcoming: bool = False
    general_answer: str = Field(default="", max_length=4000)
    clarification: str = Field(default="", max_length=500)


class QuickResultRef(Strict):
    source: Source
    id: str = Field(min_length=1, max_length=256)


class QuickMemory(Strict):
    user_id: str = Field(min_length=1, max_length=128)
    plan: QuickPlan
    result_ids: list[QuickResultRef] = Field(default_factory=list, max_length=10)
    search_queries: list[str] = Field(default_factory=list, max_length=12)
    last_answer: str = Field(default="", max_length=3000)
    unresolved: str = Field(default="", max_length=700)


class DateBounds(Strict):
    start: datetime | None = None
    end: datetime | None = None


def resolve_dates(plan: QuickPlan, now: datetime, timezone_name: str) -> DateBounds:
    c = plan.conditions
    zone = ZoneInfo(timezone_name)
    local = now.astimezone(zone)

    def parse(v, end=False):
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=zone)
        if len(v) == 10 and end:
            d += timedelta(days=1)
        return d.astimezone(timezone.utc)

    if c.date_start or c.date_end:
        bounds = DateBounds(
            start=parse(c.date_start) if c.date_start else None,
            end=parse(c.date_end, True) if c.date_end else None,
        )
        if bounds.start and bounds.end and (bounds.start >= bounds.end):
            raise ValueError("date range reversed")
        return bounds
    if c.date_period:
        day = local.replace(hour=0, minute=0, second=0, microsecond=0)
        p = c.date_period
        if p in ("today", "yesterday"):
            start = day - timedelta(days=p == "yesterday")
            end = start + timedelta(days=1)
        elif p in ("this_week", "last_week"):
            start = day - timedelta(days=day.weekday() + (7 if p == "last_week" else 0))
            end = start + timedelta(days=7)
        else:
            start = day.replace(day=1)
            if p == "last_month":
                end = start
                start = (start - timedelta(days=1)).replace(day=1)
            else:
                end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return DateBounds(
            start=start.astimezone(timezone.utc), end=end.astimezone(timezone.utc)
        )
    if plan.recent and plan.intent != "list":
        return DateBounds(start=now - timedelta(days=7), end=now)
    return DateBounds()


def normalize_content_topic(plan: QuickPlan) -> QuickPlan:
    """Business subjects are content queries outside explicit wiki category search."""
    if plan.conditions.topic and plan.source_scope != "wiki":
        plan = plan.model_copy(deep=True)
        topic = plan.conditions.topic
        if topic.casefold() not in plan.query.casefold():
            plan.query = (plan.query + " " + topic).strip()[:1000]
        plan.conditions.topic = None
    return plan

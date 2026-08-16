from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SourceName = Literal["domain_knowledge", "mail", "calendar"]
ToolName = Literal[
    "search_domain_knowledge",
    "search_mail",
    "search_calendar",
    "expand_calendar_event",
]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UTC endpoints must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError("UTC endpoints must use a zero UTC offset")
    return value.astimezone(UTC)


class ResolvedTimeRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expression: str = Field(min_length=1, max_length=100)
    start_at_utc: datetime
    end_at_utc: datetime

    @field_validator("start_at_utc", "end_at_utc")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_order(self):
        if self.start_at_utc >= self.end_at_utc:
            raise ValueError("start_at_utc must precede end_at_utc")
        return self


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    intent: str = Field(min_length=1, max_length=100)
    question_type: Literal[
        "general_chat",
        "domain_knowledge",
        "mail_search",
        "calendar_search",
        "multi_source",
        "follow_up",
    ]
    entities: dict[str, str] = Field(default_factory=dict)
    time_expression: str | None = Field(default=None, max_length=100)
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    information_needs: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("start_at_utc", "end_at_utc")
    @classmethod
    def validate_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_time_pair(self):
        if (self.start_at_utc is None) != (self.end_at_utc is None):
            raise ValueError("UTC range requires both endpoints")
        if self.start_at_utc and self.start_at_utc >= self.end_at_utc:
            raise ValueError("invalid UTC range")
        return self


class ToolAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool: ToolName
    query: str = Field(default="", max_length=1000)
    reason: str = Field(min_length=1, max_length=500)
    event_id: str | None = Field(default=None, max_length=256)
    content_kinds: list[Literal["body", "attachment", "event"]] = Field(
        default_factory=list, max_length=3
    )
    attachment_name: str | None = Field(default=None, max_length=500)
    organizer_email: str | None = Field(default=None, max_length=320)
    attendee_emails: list[str] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.tool == "expand_calendar_event" and not self.event_id:
            raise ValueError("event_id is required for event expansion")
        if self.tool != "expand_calendar_event" and not self.query:
            raise ValueError("query is required for semantic search")
        return self

    def fingerprint(self, analysis: QueryAnalysis) -> str:
        start = analysis.start_at_utc.isoformat() if analysis.start_at_utc else ""
        end = analysis.end_at_utc.isoformat() if analysis.end_at_utc else ""
        parts = (
            self.tool,
            " ".join(self.query.casefold().split()),
            self.event_id or "",
            ",".join(sorted(self.content_kinds)),
            self.attachment_name or "",
            self.organizer_email or "",
            ",".join(sorted(self.attendee_emails)),
            start,
            end,
        )
        return "|".join(parts)


class SearchDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: SourceName
    document_id: str = Field(min_length=1, max_length=256)
    source_id: str | None = Field(default=None, max_length=256)
    parent_event_id: str | None = Field(default=None, max_length=256)
    content_kind: Literal["body", "attachment", "event"] | None = None
    title: str = Field(default="", max_length=500)
    text: str = Field(min_length=1, max_length=8000)
    score: float
    metadata: dict[str, str | int | float | bool | list[str] | None] = Field(
        default_factory=dict
    )


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool: ToolName
    query: str = Field(default="", max_length=1000)
    documents: list[SearchDocument] = Field(default_factory=list, max_length=20)
    total_hits: int = Field(default=0, ge=0)
    retrieval_mode: Literal["hybrid", "bm25", "deterministic"] = "hybrid"
    disclosures: list[str] = Field(default_factory=list, max_length=4)
    error_code: str | None = Field(default=None, max_length=128)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ToolAction
    result: SearchResult
    extracted_entities: dict[str, str] = Field(default_factory=dict)


class JudgeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sufficient: bool
    reason: str = Field(min_length=1, max_length=500)
    missing_information: list[str] = Field(default_factory=list, max_length=8)
    recommended_action: ToolAction | None = None


class EventReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9_.:@+-]+$",
    )
    subject: str = Field(default="", max_length=500)
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None


class AgentMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entities: dict[str, str] = Field(default_factory=dict)
    current_topic: str | None = Field(default=None, max_length=500)
    search_history: list[str] = Field(default_factory=list, max_length=16)
    previous_event_reference: EventReference | None = None
    retrieved_source_refs: list[str] = Field(default_factory=list, max_length=16)
    unresolved_information: list[str] = Field(default_factory=list, max_length=8)


class AgentTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: list[str] = Field(default_factory=list, max_length=8)
    judge_decisions: list[str] = Field(default_factory=list, max_length=8)
    iteration_count: int = Field(default=0, ge=0, le=4)

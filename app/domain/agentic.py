from datetime import UTC, date, datetime, timedelta
import json
import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

SourceName = Literal["domain_knowledge", "mail", "calendar"]
TimeScope = Literal[
    "none",
    "yesterday",
    "previous_week",
    "current_week",
    "previous_month",
    "exact_date",
]
EventReferenceMode = Literal["none", "previous_event"]
AnalysisStatus = Literal["ready", "unavailable"]
ToolName = Literal[
    "search_domain_knowledge",
    "search_mail",
    "search_calendar",
    "expand_calendar_event",
]

_RESERVED_ENTITY_KEYS = {
    "employeeid",
    "userid",
    "owner",
    "ownerid",
    "tenantid",
    "index",
    "indexname",
    "tool",
    "filter",
    "acl",
    "opensearchdsl",
    "querydsl",
}

MAX_EVENT_ID_LENGTH = 256
STABLE_EVENT_ID_PATTERN = r"^[A-Za-z0-9_.:@+-]+$"
_STABLE_EVENT_ID = re.compile(STABLE_EVENT_ID_PATTERN)
MAX_METADATA_KEYS = 12
MAX_METADATA_KEY_LENGTH = 64
MAX_METADATA_STRING_LENGTH = 512
MAX_METADATA_LIST_LENGTH = 20
MAX_METADATA_LIST_ITEM_LENGTH = 320
MAX_METADATA_SERIALIZED_BYTES = 4096
MAX_METADATA_NUMBER_MAGNITUDE = 2**63 - 1

EntityKey = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=100,
    ),
]
EntityValue = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=500,
    ),
]
BoundedModelText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=500,
    ),
]
BoundedSourceReference = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=256,
    ),
]
BoundedEmail = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=320,
    ),
]
StableEventId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=MAX_EVENT_ID_LENGTH,
        pattern=STABLE_EVENT_ID_PATTERN,
    ),
]
MetadataKey = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=MAX_METADATA_KEY_LENGTH,
    ),
]
MetadataString = Annotated[
    str,
    StringConstraints(strict=True, max_length=MAX_METADATA_STRING_LENGTH),
]
MetadataListItem = Annotated[
    str,
    StringConstraints(strict=True, max_length=MAX_METADATA_LIST_ITEM_LENGTH),
]
MetadataStringList = Annotated[
    list[MetadataListItem],
    Field(strict=True, max_length=MAX_METADATA_LIST_LENGTH),
]
MetadataInteger = Annotated[
    int,
    Field(
        strict=True,
        ge=-MAX_METADATA_NUMBER_MAGNITUDE,
        le=MAX_METADATA_NUMBER_MAGNITUDE,
    ),
]


def _require_metadata_float(value: object) -> object:
    if type(value) is not float:
        raise ValueError("metadata float must be a float")
    return value


MetadataFloat = Annotated[
    float,
    BeforeValidator(_require_metadata_float),
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=-MAX_METADATA_NUMBER_MAGNITUDE,
        le=MAX_METADATA_NUMBER_MAGNITUDE,
    ),
]
MetadataValue = (
    MetadataString
    | MetadataInteger
    | MetadataFloat
    | StrictBool
    | MetadataStringList
    | None
)


def _validate_metadata_aggregate(
    value: dict[MetadataKey, MetadataValue],
) -> dict[MetadataKey, MetadataValue]:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be safely JSON serializable") from exc
    if len(serialized) > MAX_METADATA_SERIALIZED_BYTES:
        raise ValueError("metadata exceeds serialized size limit")
    return value


SearchMetadata = Annotated[
    dict[MetadataKey, MetadataValue],
    Field(strict=True, max_length=MAX_METADATA_KEYS),
    AfterValidator(_validate_metadata_aggregate),
]


def normalize_stable_event_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if not 1 <= len(value) <= MAX_EVENT_ID_LENGTH:
        return None
    if _STABLE_EVENT_ID.fullmatch(value) is None:
        return None
    return value


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UTC endpoints must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError("UTC endpoints must use a zero UTC offset")
    return value.astimezone(UTC)


class ResolvedTimeRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: TimeScope
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


class SourceRequest(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    source: SourceName
    query: str = Field(min_length=1, max_length=1000)


class IntentDecision(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    intent: str = Field(min_length=1, max_length=100)
    source_requests: list[SourceRequest] = Field(
        default_factory=list,
        max_length=3,
    )
    entities: dict[EntityKey, EntityValue] = Field(
        default_factory=dict,
        max_length=16,
    )
    time_scope: TimeScope = "none"
    exact_date: date | None = None
    event_reference: EventReferenceMode = "none"
    calendar_detail_required: StrictBool = False
    information_needs: list[BoundedModelText] = Field(
        default_factory=list,
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_semantics(self):
        sources = [item.source for item in self.source_requests]
        if len(sources) != len(set(sources)):
            raise ValueError("source_requests must contain unique sources")
        if (self.time_scope == "exact_date") != (self.exact_date is not None):
            raise ValueError("exact_date must be present only for exact_date scope")
        calendar_requested = "calendar" in sources
        if self.calendar_detail_required and not calendar_requested:
            raise ValueError("calendar detail requires a calendar source")
        if self.event_reference == "previous_event" and not calendar_requested:
            raise ValueError("previous event requires a calendar source")
        normalized_keys = {
            re.sub(r"[^a-z0-9]", "", key.casefold()) for key in self.entities
        }
        if normalized_keys & _RESERVED_ENTITY_KEYS:
            raise ValueError("entities contain a server-owned key")
        return self


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    analysis_status: AnalysisStatus = "ready"
    intent: str = Field(min_length=1, max_length=100)
    question_type: Literal[
        "general_chat",
        "domain_knowledge",
        "mail_search",
        "calendar_search",
        "multi_source",
        "follow_up",
    ]
    source_requests: list[SourceRequest] = Field(
        default_factory=list,
        max_length=3,
    )
    entities: dict[EntityKey, EntityValue] = Field(
        default_factory=dict,
        max_length=16,
    )
    time_scope: TimeScope = "none"
    exact_date: date | None = None
    event_reference: EventReferenceMode = "none"
    calendar_detail_required: StrictBool = False
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    information_needs: list[BoundedModelText] = Field(
        default_factory=list,
        max_length=8,
    )

    @classmethod
    def unavailable(cls) -> "QueryAnalysis":
        return cls(
            analysis_status="unavailable",
            intent="analysis_unavailable",
            question_type="general_chat",
        )

    @classmethod
    def from_intent(
        cls,
        decision: IntentDecision,
        *,
        now: datetime | None = None,
        timezone_name: str = "Asia/Seoul",
    ) -> "QueryAnalysis":
        from app.retrieval.dates import resolve_time_scope

        resolved = resolve_time_scope(
            decision.time_scope,
            exact_date=decision.exact_date,
            now=now,
            timezone_name=timezone_name,
        )
        sources = [item.source for item in decision.source_requests]
        if decision.event_reference == "previous_event":
            question_type = "follow_up"
        elif len(sources) > 1:
            question_type = "multi_source"
        elif not sources:
            question_type = "general_chat"
        else:
            question_type = {
                "mail": "mail_search",
                "calendar": "calendar_search",
                "domain_knowledge": "domain_knowledge",
            }[sources[0]]
        return cls(
            analysis_status="ready",
            intent=decision.intent,
            question_type=question_type,
            source_requests=decision.source_requests,
            entities=decision.entities,
            time_scope=decision.time_scope,
            exact_date=decision.exact_date,
            event_reference=decision.event_reference,
            calendar_detail_required=decision.calendar_detail_required,
            start_at_utc=resolved.start_at_utc if resolved else None,
            end_at_utc=resolved.end_at_utc if resolved else None,
            information_needs=decision.information_needs,
        )

    @field_validator("start_at_utc", "end_at_utc")
    @classmethod
    def validate_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_analysis_state(self):
        if (self.start_at_utc is None) != (self.end_at_utc is None):
            raise ValueError("UTC range requires both endpoints")
        if self.start_at_utc and self.start_at_utc >= self.end_at_utc:
            raise ValueError("invalid UTC range")
        if self.analysis_status == "unavailable" and (
            self.intent != "analysis_unavailable"
            or self.question_type != "general_chat"
            or self.entities
            or self.source_requests
            or self.time_scope != "none"
            or self.exact_date is not None
            or self.event_reference != "none"
            or self.calendar_detail_required
            or self.start_at_utc is not None
            or self.information_needs
        ):
            raise ValueError("unavailable analysis cannot carry semantic decisions")
        return self


class ToolAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool: ToolName
    query: str = Field(default="", max_length=1000)
    reason: str = Field(min_length=1, max_length=500)
    event_id: StableEventId | None = None
    content_kinds: list[Literal["body", "attachment", "event"]] = Field(
        default_factory=list, max_length=3
    )
    attachment_name: str | None = Field(default=None, max_length=500)
    organizer_email: str | None = Field(default=None, max_length=320)
    attendee_emails: list[BoundedEmail] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=10, ge=1, le=20)

    @field_validator("event_id", mode="before")
    @classmethod
    def validate_event_id(cls, value):
        if value is None:
            return None
        normalized = normalize_stable_event_id(value)
        if normalized is None:
            raise ValueError("invalid stable event id")
        return normalized

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
    metadata: SearchMetadata = Field(default_factory=dict)


def search_document_identity(
    item: SearchDocument,
) -> tuple[SourceName, str | None, str, str | None, str | None]:
    return (
        item.source_type,
        item.content_kind,
        item.document_id,
        item.source_id,
        item.parent_event_id,
    )


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool: ToolName
    query: str = Field(default="", max_length=1000)
    documents: list[SearchDocument] = Field(default_factory=list, max_length=20)
    total_hits: int = Field(default=0, ge=0)
    retrieval_mode: Literal["hybrid", "bm25", "deterministic"] = "hybrid"
    disclosures: list[BoundedModelText] = Field(default_factory=list, max_length=4)
    error_code: str | None = Field(default=None, max_length=128)
    retryable: bool = False


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ToolAction
    result: SearchResult
    extracted_entities: dict[EntityKey, EntityValue] = Field(
        default_factory=dict,
        max_length=16,
    )


class JudgeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sufficient: bool
    reason: str = Field(min_length=1, max_length=500)
    missing_information: list[BoundedModelText] = Field(
        default_factory=list,
        max_length=8,
    )
    recommended_action: ToolAction | None = None


class EventReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: StableEventId
    subject: str = Field(default="", max_length=500)
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None

    @field_validator("event_id", mode="before")
    @classmethod
    def validate_event_id(cls, value):
        normalized = normalize_stable_event_id(value)
        if normalized is None:
            raise ValueError("invalid stable event id")
        return normalized


class AgentMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entities: dict[EntityKey, EntityValue] = Field(
        default_factory=dict,
        max_length=16,
    )
    current_topic: str | None = Field(default=None, max_length=500)
    search_history: list[BoundedModelText] = Field(
        default_factory=list,
        max_length=16,
    )
    previous_event_reference: EventReference | None = None
    retrieved_source_refs: list[BoundedSourceReference] = Field(
        default_factory=list,
        max_length=16,
    )
    unresolved_information: list[BoundedModelText] = Field(
        default_factory=list,
        max_length=8,
    )


class AgentTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: list[BoundedModelText] = Field(default_factory=list, max_length=8)
    judge_decisions: list[BoundedModelText] = Field(default_factory=list, max_length=8)
    iteration_count: int = Field(default=0, ge=0, le=4)

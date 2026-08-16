from datetime import UTC, datetime, timedelta
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
ToolName = Literal[
    "search_domain_knowledge",
    "search_mail",
    "search_calendar",
    "expand_calendar_event",
]

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
    entities: dict[EntityKey, EntityValue] = Field(
        default_factory=dict,
        max_length=16,
    )
    time_expression: str | None = Field(default=None, max_length=100)
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    information_needs: list[BoundedModelText] = Field(
        default_factory=list,
        max_length=8,
    )

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

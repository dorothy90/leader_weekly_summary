# Multi-Source Agentic RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing `/v1/chat` Fast RAG path into a bounded Domain/Mail/Calendar agent and provide a deterministic, external-service-free dummy mode for the API, frontend, CLI, and automated tests.

**Architecture:** Keep `FastRAGWorkflow` as the compatibility facade and delegate retrieval questions to a focused `MultiSourceAgenticWorkflow`. Production and dummy search implementations share strict Pydantic actions/results, immutable backend ACL injection, a static alias registry, deterministic calendar expansion, and the same four-iteration LangGraph loop.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, LangGraph, OpenSearch 2.x, AsyncOpenAI-compatible gateway, MongoDB/Motor, pytest, React 19, TypeScript, Vite, Vitest

## Global Constraints

- Preserve the required `ChatRequest` fields and the existing `POST /v1/chat` response shape; no new client route is allowed.
- Treat request-body `user_id` as the trusted upstream employee identity and never allow a model action to set or override it.
- Every Mail query must inject `employee_id == policy.user_id` and `is_active == true` in backend code.
- Every Calendar query and event expansion must inject `employee_id == policy.user_id`, `is_active == true`, and `is_cancelled == false` in backend code.
- Use `syld_gpt`, `ews-mail-active`, and `ews-calendar-active` from settings/registry; application search code must not contain `ews-mail-v1` or `ews-calendar-v1`.
- The only allowed tools are `search_domain_knowledge`, `search_mail`, `search_calendar`, and `expand_calendar_event`.
- Natural-language dates are resolved deterministically with default timezone `Asia/Seoul`; the model never emits authoritative UTC values.
- OpenSearch hits must be normalized before model context, memory, tracing, citation validation, or public response construction.
- `MAX_ITERATIONS` is exactly `4`, and a normalized Tool + Query + Filter fingerprint must block duplicate backend calls.
- Invalid structured model output is retried once and then uses a deterministic fallback.
- Embedding failure keeps the existing exact BM25 disclosure string.
- Dummy mode must not require OpenSearch, MongoDB, embeddings, an LLM API key, or network access.
- Dummy fixtures must include a foreign-owner decoy, inactive Mail decoy, and cancelled Calendar decoy, and tests must prove all three remain invisible.
- Preserve the current Deep Research workflow; this plan changes only the Fast retrieval path and shared public reference types.
- Never return or log chain-of-thought, raw mail/calendar bodies in production traces, credentials, raw employee IDs, or unauthorized existence signals.

---

## File Structure

```text
app/
├── api/
│   ├── dependencies.py                 # production/demo composition roots
│   ├── main.py                         # demo ASGI export and existing app factory
│   └── routes/chat.py                  # persist agent memory updates
├── config/settings.py                  # aliases, timezone, demo switch
├── domain/
│   ├── agentic.py                      # strict analysis/action/result contracts
│   ├── chat.py                         # public source enum + private agent result data
│   └── evidence.py                     # extended evidence source enum
├── graphs/
│   ├── fast_rag.py                     # compatibility delegation only
│   └── multi_source.py                 # bounded explicit agent graph
├── llm/agentic.py                      # production adapter + deterministic model
├── persistence/conversations.py        # structured bounded agent memory
└── retrieval/
    ├── dates.py                        # Korean date expression resolver
    ├── multi_source.py                 # protocol + in-memory implementation
    ├── multi_source_opensearch.py      # production hybrid implementation
    └── source_registry.py              # static configured tool catalog
fixtures/multi_source_demo/corpus.json  # deterministic multi-owner corpus
scripts/run_multi_source_demo.py        # local canonical scenario runner
tests/mail_rag/
├── test_agentic_contracts.py
├── test_agentic_dates.py
├── test_agentic_memory.py
├── test_multi_source_dummy.py
├── test_multi_source_opensearch.py
├── test_multi_source_graph.py
└── test_multi_source_demo_api.py
frontend/src/
├── rag/types.ts
├── rag/ConversationPanel.tsx
├── rag/__tests__/RagLabApp.test.tsx
├── services/ragApiService.ts
└── services/ragApiService.test.ts
docs/multi_source_demo.md
```

Each new Python module has one responsibility. Do not move unrelated code out
of the existing large Fast graph or API route during this work.

---

### Task 1: Agent contracts, source registry, settings, and date resolution

**Files:**
- Create: `app/domain/agentic.py`
- Create: `app/retrieval/source_registry.py`
- Create: `app/retrieval/dates.py`
- Modify: `app/config/settings.py:17-42`
- Modify: `app/domain/evidence.py:34-57`
- Modify: `app/domain/chat.py:102-144`
- Create: `tests/mail_rag/test_agentic_contracts.py`
- Create: `tests/mail_rag/test_agentic_dates.py`
- Modify: `tests/mail_rag/test_settings.py:1-52`

**Interfaces:**
- Produces: `resolve_time_range(expression: str | None, *, now: datetime | None = None, timezone_name: str = "Asia/Seoul") -> ResolvedTimeRange | None`
- Produces: `SourceRegistry.from_settings(settings: Settings) -> SourceRegistry`
- Produces: `QueryAnalysis`, `ToolAction`, `SearchDocument`, `SearchResult`, `Observation`, `JudgeDecision`, `AgentMemoryUpdate`, and `AgentTrace`
- Produces: settings fields `multi_source_demo`, `domain_knowledge_index`, `mail_index_alias`, `calendar_index_alias`, and `default_user_timezone`

- [ ] **Step 1: Write failing contract, registry, settings, and date tests**

Create `tests/mail_rag/test_agentic_contracts.py`:

```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.domain.agentic import (
    QueryAnalysis,
    SearchDocument,
    SearchResult,
    ToolAction,
)
from app.retrieval.source_registry import SourceRegistry


def test_source_registry_uses_configured_aliases_and_no_physical_names():
    settings = Settings()
    registry = SourceRegistry.from_settings(settings)
    assert registry.index_for("search_domain_knowledge") == "syld_gpt"
    assert registry.index_for("search_mail") == "ews-mail-active"
    assert registry.index_for("search_calendar") == "ews-calendar-active"
    assert "ews-mail-v1" not in repr(registry)
    assert "ews-calendar-v1" not in repr(registry)


@pytest.mark.parametrize("tool", ["shell", "search_index", "ews-mail-v1"])
def test_tool_action_rejects_unapproved_tools(tool):
    with pytest.raises(ValidationError):
        ToolAction(tool=tool, query="NAND", reason="test")


def test_tool_action_has_no_owner_index_or_dsl_fields():
    with pytest.raises(ValidationError):
        ToolAction(
            tool="search_mail",
            query="NAND",
            reason="mail",
            employee_id="lee",
        )


def test_search_result_is_normalized_and_bounded():
    result = SearchResult(
        tool="search_calendar",
        query="NAND review",
        documents=[
            SearchDocument(
                source_type="calendar",
                document_id="event-1",
                source_id="event-1",
                parent_event_id="event-1",
                content_kind="event",
                title="NAND Yield Review",
                text="FDC 확인",
                score=1.0,
                metadata={"start_at_utc": "2026-08-12T01:00:00Z"},
            )
        ],
        total_hits=1,
    )
    assert result.documents[0].text == "FDC 확인"
    with pytest.raises(ValidationError):
        SearchDocument(
            source_type="mail",
            document_id="mail-1",
            text="x" * 8001,
            score=1,
        )


def test_query_analysis_accepts_backend_resolved_utc_range():
    analysis = QueryAnalysis(
        intent="knowledge_query",
        question_type="multi_source",
        entities={"product": "NAND"},
        time_expression="지난주",
        start_at_utc=datetime(2026, 8, 2, 15, tzinfo=UTC),
        end_at_utc=datetime(2026, 8, 9, 15, tzinfo=UTC),
        information_needs=["관련 메일", "관련 회의"],
    )
    assert analysis.start_at_utc < analysis.end_at_utc
```

Create `tests/mail_rag/test_agentic_dates.py`:

```python
from datetime import UTC, datetime

from app.retrieval.dates import resolve_time_range


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def test_last_week_uses_seoul_calendar_and_half_open_utc_range():
    resolved = resolve_time_range("지난주", now=NOW, timezone_name="Asia/Seoul")
    assert resolved.expression == "지난주"
    assert resolved.start_at_utc == datetime(2026, 8, 2, 15, tzinfo=UTC)
    assert resolved.end_at_utc == datetime(2026, 8, 9, 15, tzinfo=UTC)


def test_yesterday_and_last_month_are_deterministic():
    yesterday = resolve_time_range("어제", now=NOW)
    last_month = resolve_time_range("지난달", now=NOW)
    assert yesterday.start_at_utc == datetime(2026, 8, 14, 15, tzinfo=UTC)
    assert yesterday.end_at_utc == datetime(2026, 8, 15, 15, tzinfo=UTC)
    assert last_month.start_at_utc == datetime(2026, 6, 30, 15, tzinfo=UTC)
    assert last_month.end_at_utc == datetime(2026, 7, 31, 15, tzinfo=UTC)


def test_unknown_time_expression_does_not_guess():
    assert resolve_time_range("최근 적당한 때", now=NOW) is None
```

Append to `tests/mail_rag/test_settings.py`:

```python
def test_multi_source_alias_and_demo_defaults():
    settings = Settings()
    assert settings.multi_source_demo is False
    assert settings.domain_knowledge_index == "syld_gpt"
    assert settings.mail_index_alias == "ews-mail-active"
    assert settings.calendar_index_alias == "ews-calendar-active"
    assert settings.default_user_timezone == "Asia/Seoul"
```

- [ ] **Step 2: Run tests and verify missing-module failures**

Run:

```bash
python -m pytest tests/mail_rag/test_agentic_contracts.py tests/mail_rag/test_agentic_dates.py tests/mail_rag/test_settings.py -q
```

Expected: collection fails because `app.domain.agentic`,
`app.retrieval.source_registry`, and `app.retrieval.dates` do not exist.

- [ ] **Step 3: Implement strict contracts and extend public source types**

Create `app/domain/agentic.py` with frozen or `extra="forbid"` Pydantic
contracts. Use these exact fields and bounds:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceName = Literal["domain_knowledge", "mail", "calendar"]
ToolName = Literal[
    "search_domain_knowledge",
    "search_mail",
    "search_calendar",
    "expand_calendar_event",
]


class ResolvedTimeRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    expression: str = Field(min_length=1, max_length=100)
    start_at_utc: datetime
    end_at_utc: datetime

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
    action: ToolAction
    result: SearchResult
    extracted_entities: dict[str, str] = Field(default_factory=dict)


class JudgeDecision(BaseModel):
    sufficient: bool
    reason: str = Field(min_length=1, max_length=500)
    missing_information: list[str] = Field(default_factory=list, max_length=8)
    recommended_action: ToolAction | None = None


class EventReference(BaseModel):
    event_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9_.:@+-]+$",
    )
    subject: str = Field(default="", max_length=500)
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None


class AgentMemoryUpdate(BaseModel):
    entities: dict[str, str] = Field(default_factory=dict)
    current_topic: str | None = Field(default=None, max_length=500)
    search_history: list[str] = Field(default_factory=list, max_length=16)
    previous_event_reference: EventReference | None = None
    retrieved_source_refs: list[str] = Field(default_factory=list, max_length=16)
    unresolved_information: list[str] = Field(default_factory=list, max_length=8)


class AgentTrace(BaseModel):
    tool_calls: list[str] = Field(default_factory=list, max_length=8)
    judge_decisions: list[str] = Field(default_factory=list, max_length=8)
    iteration_count: int = Field(default=0, ge=0, le=4)
```

Extend both `Evidence.source_type` and `ChatReference.source_type` literals to:

```python
Literal["mail", "wiki", "statistic", "domain_knowledge", "calendar"]
```

Extend `FastRAGResult` with private orchestration data that the API consumes
but does not copy into `ChatResponse`:

```python
from app.domain.agentic import AgentMemoryUpdate, AgentTrace

class FastRAGResult(BaseModel):
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    quality: QualityStatus
    disclosures: list[str] = Field(default_factory=list)
    execution: ExecutionMetadata | None = None
    agent_memory: AgentMemoryUpdate | None = None
    agent_trace: AgentTrace | None = None
```

- [ ] **Step 4: Implement settings, registry, and date resolver**

Add to `Settings`:

```python
    multi_source_demo: bool = False
    domain_knowledge_index: str = "syld_gpt"
    mail_index_alias: str = "ews-mail-active"
    calendar_index_alias: str = "ews-calendar-active"
    default_user_timezone: str = "Asia/Seoul"
```

Create `app/retrieval/source_registry.py`:

```python
from collections.abc import Sequence
from dataclasses import dataclass

from app.config.settings import Settings
from app.domain.agentic import SourceName, ToolName


@dataclass(frozen=True)
class SourceDefinition:
    name: SourceName
    tool: ToolName
    index: str
    description: str


class SourceRegistry:
    def __init__(self, definitions: Sequence[SourceDefinition]):
        self.definitions = tuple(definitions)
        self._by_tool = {item.tool: item for item in self.definitions}

    @classmethod
    def from_settings(cls, settings: Settings) -> "SourceRegistry":
        return cls(
            (
                SourceDefinition(
                    "domain_knowledge",
                    "search_domain_knowledge",
                    settings.domain_knowledge_index,
                    "반도체 수율, 공정, defect 및 domain knowledge",
                ),
                SourceDefinition(
                    "mail",
                    "search_mail",
                    settings.mail_index_alias,
                    "Outlook 메일 본문과 첨부파일",
                ),
                SourceDefinition(
                    "calendar",
                    "search_calendar",
                    settings.calendar_index_alias,
                    "Outlook 일정, 회의, 회의 첨부파일",
                ),
            )
        )

    def index_for(self, tool: ToolName) -> str:
        if tool == "expand_calendar_event":
            return self._by_tool["search_calendar"].index
        return self._by_tool[tool].index

    def source_for(self, tool: ToolName) -> SourceName:
        if tool == "expand_calendar_event":
            return "calendar"
        return self._by_tool[tool].name
```

Create `app/retrieval/dates.py`:

```python
from datetime import UTC, datetime, time, timedelta
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
    current = (now or datetime.now(UTC)).astimezone(zone)
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
        return None
    return ResolvedTimeRange(
        expression=normalized,
        start_at_utc=start.astimezone(UTC),
        end_at_utc=end.astimezone(UTC),
    )
```

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
python -m pytest tests/mail_rag/test_agentic_contracts.py tests/mail_rag/test_agentic_dates.py tests/mail_rag/test_settings.py tests/mail_rag/test_domain_contracts.py -q
```

Expected: all focused tests pass.

Commit:

```bash
git add app/config/settings.py app/domain/agentic.py app/domain/chat.py app/domain/evidence.py app/retrieval/dates.py app/retrieval/source_registry.py tests/mail_rag/test_agentic_contracts.py tests/mail_rag/test_agentic_dates.py tests/mail_rag/test_settings.py
git commit -m "feat(rag): add multi-source contracts"
```

---

### Task 2: Structured conversation memory and event follow-up state

**Files:**
- Modify: `app/persistence/conversations.py:23-157`
- Create: `tests/mail_rag/test_agentic_memory.py`

**Interfaces:**
- Consumes: `AgentMemoryUpdate` and `EventReference` from Task 1
- Produces: extended `ConversationMemory`
- Produces: `apply_agent_memory_update(memory: ConversationMemory, update: AgentMemoryUpdate, policy: PolicyContext) -> ConversationMemory`

- [ ] **Step 1: Write failing bounded-memory and ownership tests**

Create `tests/mail_rag/test_agentic_memory.py`:

```python
import asyncio
from datetime import UTC, datetime

from app.domain.agentic import AgentMemoryUpdate, EventReference
from app.domain.policy import PolicyContext
from app.persistence.conversations import (
    ConversationMemory,
    InMemoryConversationStore,
    apply_agent_memory_update,
)


def test_agent_memory_update_is_bounded_and_sanitized():
    policy = PolicyContext.from_user_id("kim")
    update = AgentMemoryUpdate(
        entities={"product": "NAND", "issue": "Cell Leakage"},
        current_topic="NAND Cell Leakage",
        search_history=[f"search-{index}" for index in range(16)],
        previous_event_reference=EventReference(
            event_id="event-kim-1",
            subject="NAND Yield Review",
            start_at_utc=datetime(2026, 8, 12, 1, tzinfo=UTC),
        ),
        retrieved_source_refs=["mail-1", "event-kim-1"],
        unresolved_information=["추가 측정 결과"],
    )
    memory = apply_agent_memory_update(ConversationMemory(), update, policy)
    assert memory.entities == {"product": "NAND", "issue": "Cell Leakage"}
    assert memory.previous_event_reference.event_id == "event-kim-1"
    assert len(memory.search_history) == 16


def test_structured_memory_round_trips_through_owner_scoped_store():
    store = InMemoryConversationStore()
    policy = PolicyContext.from_user_id("kim")
    memory = apply_agent_memory_update(
        ConversationMemory(),
        AgentMemoryUpdate(
            current_topic="NAND",
            previous_event_reference=EventReference(event_id="event-kim-1"),
        ),
        policy,
    )
    asyncio.run(store.save("conversation-1", policy, memory))
    loaded = asyncio.run(store.load("conversation-1", policy))
    assert loaded.current_topic == "NAND"
    assert loaded.previous_event_reference.event_id == "event-kim-1"
```

- [ ] **Step 2: Run the test and verify missing-field/helper failures**

Run:

```bash
python -m pytest tests/mail_rag/test_agentic_memory.py -q
```

Expected: collection or assertions fail because structured agent memory is not
implemented.

- [ ] **Step 3: Add bounded fields, sanitization, and update helper**

Add these fields to `ConversationMemory`:

```python
from app.domain.agentic import AgentMemoryUpdate, EventReference

    entities: dict[str, str] = Field(default_factory=dict)
    current_topic: str | None = Field(default=None, max_length=500)
    search_history: list[str] = Field(default_factory=list, max_length=16)
    previous_event_reference: EventReference | None = None
    retrieved_source_refs: list[str] = Field(default_factory=list, max_length=16)
    unresolved_information: list[str] = Field(default_factory=list, max_length=8)
```

Extend `_sanitize_memory` by constructing these safe values before the final
`model_copy` and including them in its update dictionary:

```python
    entities = {
        sanitize_text(str(key))[:100]: sanitize_text(str(value))[:500]
        for key, value in validated.entities.items()
        if sanitize_text(str(key)) and sanitize_text(str(value))
    }
    event = validated.previous_event_reference
    safe_event = (
        event.model_copy(
            update={
                "event_id": event.event_id,
                "subject": sanitize_text(event.subject)[:500],
            }
        )
        if event is not None
        else None
    )
    structured_update = {
        "entities": dict(list(entities.items())[:16]),
        "current_topic": (
            sanitize_text(validated.current_topic)[:500]
            if validated.current_topic
            else None
        ),
        "search_history": [
            opaque_identifier(item) for item in validated.search_history[:16]
        ],
        "previous_event_reference": safe_event,
        "retrieved_source_refs": [
            opaque_identifier(item) for item in validated.retrieved_source_refs[:16]
        ],
        "unresolved_information": [
            sanitize_text(item)[:500]
            for item in validated.unresolved_information[:8]
            if sanitize_text(item)
        ],
    }
```

Add this public helper after `ConversationMemory`:

```python
def apply_agent_memory_update(
    memory: ConversationMemory,
    update: AgentMemoryUpdate,
    policy: PolicyContext,
) -> ConversationMemory:
    merged_entities = {**memory.entities, **update.entities}
    candidate = memory.model_copy(
        update={
            "entities": dict(list(merged_entities.items())[-16:]),
            "current_topic": update.current_topic or memory.current_topic,
            "search_history": list(
                dict.fromkeys([*memory.search_history, *update.search_history])
            )[-16:],
            "previous_event_reference": (
                update.previous_event_reference or memory.previous_event_reference
            ),
            "retrieved_source_refs": list(
                dict.fromkeys(
                    [*memory.retrieved_source_refs, *update.retrieved_source_refs]
                )
            )[-16:],
            "unresolved_information": update.unresolved_information[:8],
        }
    )
    return _sanitize_memory(candidate, policy)
```

- [ ] **Step 4: Run memory and existing conversation tests and commit**

Run:

```bash
python -m pytest tests/mail_rag/test_agentic_memory.py tests/mail_rag/test_conversations.py tests/mail_rag/test_conversation_graph.py -q
```

Expected: all tests pass, including existing owner and revision tests.

Commit:

```bash
git add app/persistence/conversations.py tests/mail_rag/test_agentic_memory.py
git commit -m "feat(memory): retain agent source context"
```

---

### Task 3: Deterministic dummy corpus and in-memory search tools

**Files:**
- Create: `fixtures/multi_source_demo/corpus.json`
- Create: `app/retrieval/multi_source.py`
- Create: `tests/mail_rag/test_multi_source_dummy.py`

**Interfaces:**
- Consumes: `ToolAction`, `QueryAnalysis`, `SearchDocument`, `SearchResult`, `PolicyContext`, and `SourceRegistry`
- Produces: `MultiSourceSearch.execute(action, policy, analysis) -> SearchResult`
- Produces: `InMemoryMultiSourceSearch.from_path(path: Path) -> InMemoryMultiSourceSearch`

- [ ] **Step 1: Add the explicit multi-owner dummy corpus**

Create `fixtures/multi_source_demo/corpus.json` with these records:

```json
[
  {
    "index": "syld_gpt",
    "document_id": "domain-cell-leakage",
    "source_type": "domain_knowledge",
    "content_kind": null,
    "source_id": "domain-cell-leakage",
    "parent_event_id": null,
    "employee_id": null,
    "is_active": true,
    "is_cancelled": false,
    "title": "NAND Cell Leakage 기술 해설",
    "text": "Cell Leakage는 NAND 셀의 저장 전하가 비정상적으로 누설되는 현상이다. 데이터 보존 마진과 임계전압 분포를 악화시켜 수율 저하로 이어질 수 있으며 공정 조건과 장비 FDC 추세 확인이 필요하다.",
    "occurred_at": null,
    "metadata": {}
  },
  {
    "index": "ews-mail-active",
    "document_id": "mail-kim-body-0",
    "source_type": "mail",
    "content_kind": "body",
    "source_id": "mail-kim-1",
    "parent_event_id": null,
    "employee_id": "kim",
    "is_active": true,
    "is_cancelled": false,
    "title": "NAND Cell Leakage 수율 이슈",
    "text": "김OO이 NAND Cell Leakage 증가와 수율 저하를 보고했고 NAND Yield Review 회의에서 논의할 예정이라고 알렸다.",
    "occurred_at": "2026-08-05T01:00:00Z",
    "metadata": {"chunk_index": 0, "sender_email": "kim.oo@example.com"}
  },
  {
    "index": "ews-mail-active",
    "document_id": "mail-kim-attachment-0",
    "source_type": "mail",
    "content_kind": "attachment",
    "source_id": "mail-kim-attachment-1",
    "parent_event_id": null,
    "employee_id": "kim",
    "is_active": true,
    "is_cancelled": false,
    "title": "cell_leakage_measurements.xlsx",
    "text": "Cell Leakage 불량률이 기준 대비 18퍼센트 증가했고 장비 A의 FDC 편차가 함께 관찰됐다.",
    "occurred_at": "2026-08-05T01:00:00Z",
    "metadata": {"chunk_index": 0, "attachment_name": "cell_leakage_measurements.xlsx"}
  },
  {
    "index": "ews-calendar-active",
    "document_id": "event-kim-1",
    "source_type": "calendar",
    "content_kind": "event",
    "source_id": "event-kim-1",
    "parent_event_id": "event-kim-1",
    "employee_id": "kim",
    "is_active": true,
    "is_cancelled": false,
    "title": "NAND Yield Review",
    "text": "NAND Cell Leakage 수율 이슈 검토 회의",
    "occurred_at": "2026-08-07T01:00:00Z",
    "metadata": {"calendar_item_id": "event-kim-1", "start_at_utc": "2026-08-07T01:00:00Z", "end_at_utc": "2026-08-07T02:00:00Z", "organizer_email": "lead@example.com", "attendee_emails": ["kim.oo@example.com"]}
  },
  {
    "index": "ews-calendar-active",
    "document_id": "event-kim-1-action",
    "source_type": "calendar",
    "content_kind": "attachment",
    "source_id": "event-kim-1-action",
    "parent_event_id": "event-kim-1",
    "employee_id": "kim",
    "is_active": true,
    "is_cancelled": false,
    "title": "NAND_Yield_Review_Action.pdf",
    "text": "회의 Action: 장비 A의 FDC 로그를 점검하고 공정 조건 split test를 8월 20일까지 수행한다.",
    "occurred_at": "2026-08-07T01:00:00Z",
    "metadata": {"attachment_id": "attachment-kim-1", "attachment_name": "NAND_Yield_Review_Action.pdf"}
  },
  {
    "index": "ews-mail-active",
    "document_id": "mail-lee-decoy",
    "source_type": "mail",
    "content_kind": "body",
    "source_id": "mail-lee-1",
    "parent_event_id": null,
    "employee_id": "lee",
    "is_active": true,
    "is_cancelled": false,
    "title": "NAND Cell Leakage 극비 결론",
    "text": "다른 사용자의 매우 높은 관련도 비밀 메일이며 kim에게 노출되면 안 된다.",
    "occurred_at": "2026-08-06T01:00:00Z",
    "metadata": {"chunk_index": 0}
  },
  {
    "index": "ews-mail-active",
    "document_id": "mail-kim-inactive",
    "source_type": "mail",
    "content_kind": "body",
    "source_id": "mail-kim-inactive",
    "parent_event_id": null,
    "employee_id": "kim",
    "is_active": false,
    "is_cancelled": false,
    "title": "NAND Cell Leakage 폐기 문서",
    "text": "비활성 문서는 검색되면 안 된다.",
    "occurred_at": "2026-08-06T01:00:00Z",
    "metadata": {"chunk_index": 0}
  },
  {
    "index": "ews-calendar-active",
    "document_id": "event-kim-cancelled",
    "source_type": "calendar",
    "content_kind": "event",
    "source_id": "event-kim-cancelled",
    "parent_event_id": "event-kim-cancelled",
    "employee_id": "kim",
    "is_active": true,
    "is_cancelled": true,
    "title": "NAND Cell Leakage 취소 회의",
    "text": "취소된 회의는 검색되면 안 된다.",
    "occurred_at": "2026-08-08T01:00:00Z",
    "metadata": {"calendar_item_id": "event-kim-cancelled"}
  }
]
```

- [ ] **Step 2: Write failing ACL, lifecycle, grouping, and expansion tests**

Create `tests/mail_rag/test_multi_source_dummy.py`:

```python
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.config.settings import Settings
from app.domain.agentic import QueryAnalysis, ToolAction
from app.domain.policy import PolicyContext
from app.retrieval.multi_source import InMemoryMultiSourceSearch
from app.retrieval.source_registry import SourceRegistry


FIXTURE = Path("fixtures/multi_source_demo/corpus.json")


def service():
    return InMemoryMultiSourceSearch.from_path(
        FIXTURE, SourceRegistry.from_settings(Settings())
    )


def run(action, owner="kim"):
    return asyncio.run(
        service().execute(
            action,
            PolicyContext.from_user_id(owner),
            QueryAnalysis(
                intent="knowledge_query",
                question_type="multi_source",
                information_needs=[],
            ),
        )
    )


def test_domain_search_is_shared_and_normalized():
    result = run(
        ToolAction(
            tool="search_domain_knowledge", query="Cell Leakage", reason="기술 의미"
        )
    )
    assert [item.document_id for item in result.documents] == [
        "domain-cell-leakage"
    ]
    assert result.retrieval_mode == "deterministic"


def test_mail_search_enforces_owner_active_and_content_kind():
    result = run(
        ToolAction(
            tool="search_mail",
            query="NAND Cell Leakage",
            reason="메일",
            content_kinds=["body"],
        )
    )
    ids = {item.document_id for item in result.documents}
    assert "mail-kim-body-0" in ids
    assert "mail-lee-decoy" not in ids
    assert "mail-kim-inactive" not in ids
    assert all(item.content_kind == "body" for item in result.documents)


def test_calendar_search_filters_cancelled_and_expands_event_bundle():
    found = run(
        ToolAction(
            tool="search_calendar", query="NAND Yield Review", reason="회의"
        )
    )
    assert "event-kim-cancelled" not in {item.document_id for item in found.documents}
    expanded = run(
        ToolAction(
            tool="expand_calendar_event",
            event_id="event-kim-1",
            reason="회의 내용과 Action",
        )
    )
    assert {item.document_id for item in expanded.documents} == {
        "event-kim-1",
        "event-kim-1-action",
    }


def test_foreign_event_expansion_does_not_reveal_existence():
    result = run(
        ToolAction(
            tool="expand_calendar_event",
            event_id="event-kim-1",
            reason="foreign",
        ),
        owner="lee",
    )
    assert result.documents == []
    assert result.total_hits == 0


def test_resolved_date_range_is_applied_to_dummy_mail_results():
    analysis = QueryAnalysis(
        intent="knowledge_query",
        question_type="mail",
        information_needs=[],
        start_at_utc=datetime(2026, 8, 5, tzinfo=UTC),
        end_at_utc=datetime(2026, 8, 6, tzinfo=UTC),
    )
    result = asyncio.run(
        service().execute(
            ToolAction(tool="search_mail", query="NAND", reason="날짜 필터"),
            PolicyContext.from_user_id("kim"),
            analysis,
        )
    )
    assert result.documents == []
```

- [ ] **Step 3: Run tests and verify missing implementation failure**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_dummy.py -q
```

Expected: collection fails because `InMemoryMultiSourceSearch` does not exist.

- [ ] **Step 4: Implement the protocol and deterministic backend**

Create `app/retrieval/multi_source.py` with:

```python
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.agentic import (
    QueryAnalysis,
    SearchDocument,
    SearchResult,
    ToolAction,
)
from app.domain.policy import PolicyContext
from app.retrieval.source_registry import SourceRegistry


class MultiSourceSearch(Protocol):
    async def execute(
        self,
        action: ToolAction,
        policy: PolicyContext,
        analysis: QueryAnalysis,
    ) -> SearchResult:
        raise NotImplementedError


class StoredDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: str
    document_id: str
    source_type: str
    content_kind: str | None = None
    source_id: str | None = None
    parent_event_id: str | None = None
    employee_id: str | None = None
    is_active: bool = True
    is_cancelled: bool = False
    title: str = ""
    text: str = Field(min_length=1, max_length=8000)
    occurred_at: str | None = None
    metadata: dict = Field(default_factory=dict)


def _tokens(text: str) -> set[str]:
    return {
        item.casefold()
        for item in re.findall(r"[A-Za-z0-9가-힣]+", text)
        if len(item) > 1
    }


class InMemoryMultiSourceSearch:
    def __init__(self, documents: list[StoredDocument], registry: SourceRegistry):
        self.documents = documents
        self.registry = registry
        self.calls: list[tuple[ToolAction, str]] = []

    @classmethod
    def from_path(
        cls, path: Path, registry: SourceRegistry
    ) -> "InMemoryMultiSourceSearch":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls([StoredDocument.model_validate(item) for item in payload], registry)

    def _allowed(
        self,
        item: StoredDocument,
        action: ToolAction,
        owner: str,
        analysis: QueryAnalysis,
    ) -> bool:
        expected_index = self.registry.index_for(action.tool)
        if item.index != expected_index:
            return False
        if item.source_type == "domain_knowledge":
            return item.is_active
        if item.employee_id != owner or not item.is_active:
            return False
        if item.source_type == "calendar" and item.is_cancelled:
            return False
        if analysis.start_at_utc and analysis.end_at_utc and item.occurred_at:
            occurred_at = datetime.fromisoformat(
                item.occurred_at.replace("Z", "+00:00")
            )
            if not analysis.start_at_utc <= occurred_at < analysis.end_at_utc:
                return False
        if action.content_kinds and item.content_kind not in action.content_kinds:
            return False
        if action.attachment_name:
            actual = str(item.metadata.get("attachment_name") or "")
            if action.attachment_name.casefold() not in actual.casefold():
                return False
        return True

    @staticmethod
    def _document(item: StoredDocument, score: float) -> SearchDocument:
        return SearchDocument(
            source_type=item.source_type,
            document_id=item.document_id,
            source_id=item.source_id,
            parent_event_id=item.parent_event_id,
            content_kind=item.content_kind,
            title=item.title,
            text=item.text,
            score=score,
            metadata=item.metadata,
        )

    async def execute(self, action, policy, analysis) -> SearchResult:
        self.calls.append((action, policy.user_id))
        allowed = [
            item
            for item in self.documents
            if self._allowed(item, action, policy.user_id, analysis)
        ]
        if action.tool == "expand_calendar_event":
            related = [
                item
                for item in allowed
                if item.document_id == action.event_id
                or item.parent_event_id == action.event_id
            ]
            documents = [self._document(item, 1.0) for item in related]
        else:
            query_tokens = _tokens(action.query)
            ranked = []
            for item in allowed:
                haystack = _tokens(f"{item.title} {item.text}")
                overlap = len(query_tokens & haystack)
                if overlap:
                    ranked.append((overlap / max(1, len(query_tokens)), item))
            ranked.sort(key=lambda pair: (-pair[0], pair[1].document_id))
            documents = [
                self._document(item, score)
                for score, item in ranked[: action.top_k]
            ]
        return SearchResult(
            tool=action.tool,
            query=action.query,
            documents=documents,
            total_hits=len(documents),
            retrieval_mode="deterministic",
        )
```

Add this helper and call it when `action.tool == "search_mail"` immediately
before constructing `SearchResult`:

```python
    @staticmethod
    def _reconstruct_mail(
        documents: list[SearchDocument], top_k: int
    ) -> list[SearchDocument]:
        grouped: dict[tuple[str, str | None], list[SearchDocument]] = defaultdict(list)
        for item in documents:
            grouped[(item.source_id or item.document_id, item.content_kind)].append(item)
        reconstructed = []
        for (_source_id, _kind), parts in grouped.items():
            ordered = sorted(
                parts,
                key=lambda item: int(item.metadata.get("chunk_index") or 0),
            )
            unique_text = list(dict.fromkeys(item.text for item in ordered))
            base = max(ordered, key=lambda item: item.score)
            reconstructed.append(
                base.model_copy(update={"text": "\n\n".join(unique_text)[:8000]})
            )
        reconstructed.sort(key=lambda item: (-item.score, item.document_id))
        return reconstructed[:top_k]
```

```python
        if action.tool == "search_mail":
            documents = self._reconstruct_mail(documents, action.top_k)
```

Add this direct helper test; it proves order and deduplication without
inflating the canonical fixture:

```python
def test_mail_chunks_are_sorted_deduplicated_and_reconstructed():
    from app.domain.agentic import SearchDocument

    documents = [
        SearchDocument(
            source_type="mail", document_id="chunk-2", source_id="mail-1",
            content_kind="body", text="두 번째", score=0.8,
            metadata={"chunk_index": 2},
        ),
        SearchDocument(
            source_type="mail", document_id="chunk-1", source_id="mail-1",
            content_kind="body", text="첫 번째", score=1.0,
            metadata={"chunk_index": 1},
        ),
        SearchDocument(
            source_type="mail", document_id="chunk-1-copy", source_id="mail-1",
            content_kind="body", text="첫 번째", score=0.5,
            metadata={"chunk_index": 1},
        ),
    ]
    result = InMemoryMultiSourceSearch._reconstruct_mail(documents, 10)
    assert len(result) == 1
    assert result[0].text == "첫 번째\n\n두 번째"
```

- [ ] **Step 5: Run dummy-search tests and commit**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_dummy.py -q
```

Expected: all tests pass and `service().calls` records only normalized actions
plus the policy owner.

Commit:

```bash
git add app/retrieval/multi_source.py fixtures/multi_source_demo/corpus.json tests/mail_rag/test_multi_source_dummy.py
git commit -m "feat(rag): add deterministic source tools"
```

---

### Task 4: Production OpenSearch multi-source adapter

**Files:**
- Create: `app/retrieval/multi_source_opensearch.py`
- Create: `tests/mail_rag/test_multi_source_opensearch.py`

**Interfaces:**
- Consumes: `OpenSearchGateway.search(index: str, body: dict)`, `EmbeddingGateway.embed(text: str)`, `SourceRegistry`, and the Task 1/3 contracts
- Produces: `OpenSearchMultiSourceSearch.execute(action, policy, analysis) -> SearchResult`
- Produces: deterministic `_mandatory_filters`, `_bm25_body`, `_vector_body`, `_expand_calendar`, `_normalize_hits`, and Mail reconstruction behavior

- [ ] **Step 1: Write failing query-contract and normalization tests**

Create `tests/mail_rag/test_multi_source_opensearch.py` with a recording
backend and fixed embedding:

```python
import asyncio

from app.config.settings import Settings
from app.domain.agentic import QueryAnalysis, ToolAction
from app.domain.policy import PolicyContext
from app.retrieval.multi_source_opensearch import OpenSearchMultiSourceSearch
from app.retrieval.source_registry import SourceRegistry


class RecordingBackend:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    async def search(self, index, body):
        self.calls.append((index, body))
        if self.responses:
            return self.responses.pop(0)
        return {"hits": {"hits": []}}


class FixedEmbedding:
    async def embed(self, text):
        return [0.1, 0.2]


def workflow(backend):
    return OpenSearchMultiSourceSearch(
        backend,
        FixedEmbedding(),
        SourceRegistry.from_settings(Settings()),
    )


def analysis():
    return QueryAnalysis(
        intent="knowledge_query",
        question_type="multi_source",
        information_needs=[],
    )


def filters_from(body):
    return body["query"]["bool"]["filter"]


def test_mail_bm25_and_vector_queries_use_alias_and_mandatory_filters():
    backend = RecordingBackend()
    asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_mail", query="NAND", reason="mail"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )
    assert [call[0] for call in backend.calls[:2]] == [
        "ews-mail-active",
        "ews-mail-active",
    ]
    for _index, body in backend.calls[:2]:
        assert {"term": {"employee_id": "kim"}} in filters_from(body)
        assert {"term": {"is_active": True}} in filters_from(body)


def test_calendar_queries_and_expansion_repeat_all_security_filters():
    backend = RecordingBackend()
    service = workflow(backend)
    policy = PolicyContext.from_user_id("kim")
    asyncio.run(
        service.execute(
            ToolAction(tool="search_calendar", query="NAND", reason="meeting"),
            policy,
            analysis(),
        )
    )
    asyncio.run(
        service.execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="action",
            ),
            policy,
            analysis(),
        )
    )
    for index, body in backend.calls:
        if index != "ews-calendar-active":
            continue
        filters = filters_from(body)
        assert {"term": {"employee_id": "kim"}} in filters
        assert {"term": {"is_active": True}} in filters
        assert {"term": {"is_cancelled": False}} in filters


def test_post_filter_drops_foreign_and_inactive_backend_hits():
    hits = {
        "hits": {
            "hits": [
                {"_id": "foreign", "_score": 99, "_source": {"employee_id": "lee", "is_active": True, "content_kind": "body", "source_id": "m1", "text": "NAND secret"}},
                {"_id": "inactive", "_score": 98, "_source": {"employee_id": "kim", "is_active": False, "content_kind": "body", "source_id": "m2", "text": "NAND old"}},
                {"_id": "allowed", "_score": 1, "_source": {"employee_id": "kim", "is_active": True, "content_kind": "body", "source_id": "m3", "text": "NAND allowed"}},
            ]
        }
    }
    backend = RecordingBackend([hits, hits])
    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_mail", query="NAND", reason="mail"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )
    assert [item.document_id for item in result.documents] == ["allowed"]
```

Add these filter and fallback tests to the same module:

```python
from datetime import UTC, datetime

from app.domain.chat import BM25_FALLBACK_DISCLOSURE


def test_calendar_optional_filters_and_date_range_are_backend_built():
    backend = RecordingBackend()
    current = analysis().model_copy(
        update={
            "start_at_utc": datetime(2026, 8, 2, 15, tzinfo=UTC),
            "end_at_utc": datetime(2026, 8, 9, 15, tzinfo=UTC),
        }
    )
    asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="search_calendar",
                query="NAND",
                reason="meeting",
                content_kinds=["event"],
                organizer_email="lead@example.com",
                attendee_emails=["kim@example.com"],
            ),
            PolicyContext.from_user_id("kim"),
            current,
        )
    )
    filters = filters_from(backend.calls[0][1])
    assert {"terms": {"content_kind": ["event"]}} in filters
    assert {"term": {"organizer_email": "lead@example.com"}} in filters
    assert {"terms": {"attendee_emails": ["kim@example.com"]}} in filters
    assert {"range": {"start_at_utc": {
        "gte": "2026-08-02T15:00:00+00:00",
        "lt": "2026-08-09T15:00:00+00:00",
    }}} in filters


def test_embedding_failure_returns_bm25_disclosure():
    class BrokenEmbedding:
        async def embed(self, text):
            raise TimeoutError("offline")

    backend = RecordingBackend()
    service = OpenSearchMultiSourceSearch(
        backend,
        BrokenEmbedding(),
        SourceRegistry.from_settings(Settings()),
    )
    result = asyncio.run(
        service.execute(
            ToolAction(tool="search_mail", query="NAND", reason="mail"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )
    assert result.retrieval_mode == "bm25"
    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]
    assert len(backend.calls) == 1
```

Add these concrete expansion and fusion tests:

```python
def hit(document_id, **source):
    return {"_id": document_id, "_score": 1, "_source": source}


def test_calendar_expansion_returns_same_owner_parent_and_sibling_only():
    response = {
        "hits": {
            "hits": [
                hit(
                    "event-1", employee_id="kim", is_active=True,
                    is_cancelled=False, calendar_item_id="event-1",
                    parent_event_id="event-1", content_kind="event",
                    subject="NAND Review", text="meeting",
                ),
                hit(
                    "attachment-1", employee_id="kim", is_active=True,
                    is_cancelled=False, parent_event_id="event-1",
                    content_kind="attachment", attachment_name="action.pdf",
                    text="FDC action",
                ),
                hit(
                    "foreign", employee_id="lee", is_active=True,
                    is_cancelled=False, parent_event_id="event-1",
                    content_kind="attachment", text="secret",
                ),
            ]
        }
    }
    backend = RecordingBackend([response])
    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="action",
            ),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )
    assert [item.document_id for item in result.documents] == [
        "event-1", "attachment-1"
    ]


def test_hybrid_rrf_order_is_deterministic():
    bm25 = {"hits": {"hits": [
        hit("b", text="NAND B"), hit("a", text="NAND A")
    ]}}
    vector = {"hits": {"hits": [
        hit("b", text="NAND B"), hit("a", text="NAND A")
    ]}}
    backend = RecordingBackend([bm25, vector])
    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="search_domain_knowledge", query="NAND", reason="domain"
            ),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )
    assert [item.document_id for item in result.documents] == ["b", "a"]


def test_production_mail_reconstruction_sorts_chunks():
    from app.domain.agentic import SearchDocument

    parts = [
        SearchDocument(
            source_type="mail", document_id="p2", source_id="mail-1",
            content_kind="body", text="second", score=0.8,
            metadata={"chunk_index": 2},
        ),
        SearchDocument(
            source_type="mail", document_id="p1", source_id="mail-1",
            content_kind="body", text="first", score=1,
            metadata={"chunk_index": 1},
        ),
    ]
    result = OpenSearchMultiSourceSearch._reconstruct_mail(parts, 10)
    assert result[0].text == "first\n\nsecond"
```

- [ ] **Step 2: Run tests and verify missing adapter failure**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_opensearch.py -q
```

Expected: collection fails because the production adapter does not exist.

- [ ] **Step 3: Implement mandatory filters and source-specific query bodies**

Create `OpenSearchMultiSourceSearch` with this constructor and dispatch:

```python
class OpenSearchMultiSourceSearch:
    def __init__(self, backend, embeddings, registry):
        self.backend = backend
        self.embeddings = embeddings
        self.registry = registry

    async def execute(self, action, policy, analysis):
        if action.tool == "expand_calendar_event":
            return await self._expand_calendar(action, policy)
        index = self.registry.index_for(action.tool)
        filters = self._mandatory_filters(action, policy, analysis)
        bm25 = self._bm25_body(action, filters)
        disclosures = []
        try:
            vector = await self.embeddings.embed(action.query)
        except Exception:
            responses = [await self.backend.search(index, bm25)]
            mode = "bm25"
            disclosures = [BM25_FALLBACK_DISCLOSURE]
        else:
            bm25_task = asyncio.create_task(self.backend.search(index, bm25))
            vector_task = asyncio.create_task(
                self.backend.search(index, self._vector_body(action, filters, vector))
            )
            responses = list(await asyncio.gather(bm25_task, vector_task))
            mode = "hybrid"
        hits = self._fuse(responses)
        documents = self._normalize_hits(hits, action, policy)
        if action.tool == "search_mail":
            documents = self._reconstruct_mail(documents, action.top_k)
        else:
            documents = documents[: action.top_k]
        return SearchResult(
            tool=action.tool,
            query=action.query,
            documents=documents,
            total_hits=len(documents),
            retrieval_mode=mode,
            disclosures=disclosures,
        )
```

Implement `_mandatory_filters` exactly as:

```python
    def _mandatory_filters(self, action, policy, analysis):
        source = self.registry.source_for(action.tool)
        filters = []
        if source in {"mail", "calendar"}:
            filters.extend(
                [
                    {"term": {"employee_id": policy.user_id}},
                    {"term": {"is_active": True}},
                ]
            )
        if source == "calendar":
            filters.append({"term": {"is_cancelled": False}})
        if analysis.start_at_utc and analysis.end_at_utc:
            field = "start_at_utc" if source == "calendar" else "received_at"
            filters.append(
                {
                    "range": {
                        field: {
                            "gte": analysis.start_at_utc.isoformat(),
                            "lt": analysis.end_at_utc.isoformat(),
                        }
                    }
                }
            )
        if action.content_kinds:
            filters.append({"terms": {"content_kind": action.content_kinds}})
        if action.attachment_name:
            filters.append(
                {"wildcard": {"attachment_name": f"*{action.attachment_name}*"}}
            )
        if action.organizer_email:
            filters.append({"term": {"organizer_email": action.organizer_email}})
        if action.attendee_emails:
            filters.append({"terms": {"attendee_emails": action.attendee_emails}})
        return filters
```

Add these complete query and fusion helpers. Import `RankedHit` and
`reciprocal_rank_fusion` from `app.retrieval.fusion`:

```python
    def _bm25_body(self, action, filters):
        fields = (
            ["subject^4", "location^2", "text^3", "organizer_email", "attendee_emails"]
            if action.tool == "search_calendar"
            else ["text^3"]
        )
        return {
            "size": action.top_k * 3,
            "query": {
                "bool": {
                    "filter": filters,
                    "must": [
                        {"multi_match": {"query": action.query, "fields": fields}}
                    ],
                }
            },
        }

    def _vector_body(self, action, filters, vector):
        return {
            "size": action.top_k * 3,
            "query": {
                "bool": {
                    "filter": filters,
                    "must": [
                        {
                            "knn": {
                                "embedding": {
                                    "vector": vector,
                                    "k": action.top_k * 3,
                                }
                            }
                        }
                    ],
                }
            },
        }

    @staticmethod
    def _fuse(responses):
        rankings = []
        for response in responses:
            ranking = []
            for rank, hit in enumerate(
                response.get("hits", {}).get("hits", []), 1
            ):
                if hit.get("_id") is not None:
                    ranking.append(RankedHit(str(hit["_id"]), rank, hit))
            rankings.append(ranking)
        return [
            {**item.raw, "_rrf_score": item.score}
            for item in reciprocal_rank_fusion(rankings)
        ]
```

- [ ] **Step 4: Implement fail-closed normalization and deterministic calendar expansion**

Use this allowlist and complete normalization/grouping helpers:

```python
SAFE_METADATA = {
    "received_at", "sent_at", "modified_at", "start_at_utc", "end_at_utc",
    "timezone", "organizer_email", "attendee_emails", "attachment_name",
    "calendar_item_id", "attachment_id", "occurrence_id", "series_master_id",
    "chunk_index",
}

    def _normalize_hits(self, hits, action, policy):
        source_type = self.registry.source_for(action.tool)
        documents = []
        for hit in hits:
            source = hit.get("_source", {})
            if source_type in {"mail", "calendar"}:
                if source.get("employee_id") != policy.user_id:
                    continue
                if source.get("is_active") is not True:
                    continue
            if source_type == "calendar" and source.get("is_cancelled") is not False:
                continue
            text = str(source.get("text") or "").strip()
            if not text or hit.get("_id") is None:
                continue
            metadata = {
                key: source[key]
                for key in SAFE_METADATA
                if key in source
            }
            documents.append(
                SearchDocument(
                    source_type=source_type,
                    document_id=str(hit["_id"]),
                    source_id=(
                        str(source["source_id"])
                        if source.get("source_id") is not None
                        else None
                    ),
                    parent_event_id=(
                        str(source["parent_event_id"])
                        if source.get("parent_event_id") is not None
                        else None
                    ),
                    content_kind=source.get("content_kind"),
                    title=str(source.get("subject") or source.get("title") or ""),
                    text=text[:8000],
                    score=float(hit.get("_rrf_score") or hit.get("_score") or 0),
                    metadata=metadata,
                )
            )
        return documents

    @staticmethod
    def _reconstruct_mail(documents, top_k):
        grouped = {}
        for item in documents:
            key = (item.source_id or item.document_id, item.content_kind)
            grouped.setdefault(key, []).append(item)
        result = []
        for parts in grouped.values():
            ordered = sorted(
                parts,
                key=lambda item: int(item.metadata.get("chunk_index") or 0),
            )
            base = max(ordered, key=lambda item: item.score)
            text = "\n\n".join(dict.fromkeys(item.text for item in ordered))[:8000]
            result.append(base.model_copy(update={"text": text}))
        return sorted(result, key=lambda item: (-item.score, item.document_id))[:top_k]

    @staticmethod
    def _deduplicate_documents(documents):
        unique = {}
        for item in documents:
            current = unique.get(item.document_id)
            if current is None or item.score > current.score:
                unique[item.document_id] = item
        return sorted(
            unique.values(), key=lambda item: (-item.score, item.document_id)
        )
```

Implement `_expand_calendar` with mandatory filters plus the relation clause:

```python
    async def _expand_calendar(self, action, policy):
        filters = [
            {"term": {"employee_id": policy.user_id}},
            {"term": {"is_active": True}},
            {"term": {"is_cancelled": False}},
            {
                "bool": {
                    "should": [
                        {"term": {"calendar_item_id": action.event_id}},
                        {"term": {"parent_event_id": action.event_id}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        ]
        body = {
            "size": 50,
            "query": {"bool": {"filter": filters}},
        }
        response = await self.backend.search(
            self.registry.index_for("expand_calendar_event"), body
        )
        hits = [
            {**hit, "_rrf_score": float(hit.get("_score") or 1)}
            for hit in response.get("hits", {}).get("hits", [])
        ]
        documents = self._deduplicate_documents(
            self._normalize_hits(hits, action, policy)
        )
        return SearchResult(
            tool=action.tool,
            query="",
            documents=documents,
            total_hits=len(documents),
            retrieval_mode="bm25",
        )
```

Return zero hits for absent, inactive, cancelled, or foreign bundles without
an authorization-specific error. Deduplicate on `document_id`.

- [ ] **Step 5: Run production adapter tests and commit**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_opensearch.py tests/mail_rag/test_retrieval_primitives.py tests/mail_rag/test_retrieval_service.py -q
```

Expected: all tests pass and every recorded Mail/Calendar body contains all
mandatory filters.

Commit:

```bash
git add app/retrieval/multi_source_opensearch.py tests/mail_rag/test_multi_source_opensearch.py
git commit -m "feat(rag): search mail calendar and domain"
```

---

### Task 5: Rule-based model and bounded LangGraph agent loop

**Files:**
- Create: `app/llm/agentic.py`
- Create: `app/graphs/multi_source.py`
- Modify: `app/graphs/fast_rag.py:197-216,462-510`
- Create: `tests/mail_rag/test_multi_source_graph.py`

**Interfaces:**
- Consumes: `MultiSourceSearch`, Task 1 contracts, `ConversationMemory`, `PolicyContext`, and current `CitationValidator`
- Produces: `RuleBasedAgentModel`
- Produces: `StructuredAgentModel(llm, *, timeout_seconds: float)` with one retry then fallback
- Produces: `MultiSourceAgenticWorkflow.invoke(request, policy, conversation) -> FastRAGResult`
- Preserves: `FastRAGWorkflow.invoke(request, policy, conversation) -> FastRAGResult`

- [ ] **Step 1: Write failing canonical-flow, loop, duplicate, fallback, and follow-up tests**

Create `tests/mail_rag/test_multi_source_graph.py`. Use the Task 3 fixture and
these assertions:

```python
import asyncio
from pathlib import Path

from app.config.settings import Settings
from app.domain.chat import ChatRequest
from app.domain.policy import PolicyContext
from app.graphs.multi_source import MAX_ITERATIONS, MultiSourceAgenticWorkflow
from app.llm.agentic import RuleBasedAgentModel, StructuredAgentModel
from app.persistence.conversations import ConversationMemory
from app.retrieval.multi_source import InMemoryMultiSourceSearch
from app.retrieval.source_registry import SourceRegistry


QUESTION = (
    "김OO이 지난주 메일에서 이야기한 NAND 수율 문제가 어떤 회의에서 "
    "논의됐고 어떤 Action을 하기로 했으며 기술적으로 어떤 의미인지 설명해줘."
)


def build():
    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    return MultiSourceAgenticWorkflow(search, RuleBasedAgentModel()), search


def test_mail_calendar_expand_domain_canonical_flow():
    workflow, search = build()
    result = asyncio.run(
        workflow.invoke(
            ChatRequest(user_id="kim", message=QUESTION),
            PolicyContext.from_user_id("kim"),
            ConversationMemory(),
        )
    )
    tools = [action.tool for action, _owner in search.calls]
    assert tools == [
        "search_mail",
        "search_calendar",
        "expand_calendar_event",
        "search_domain_knowledge",
    ]
    assert "FDC" in result.answer
    assert "Cell Leakage" in result.answer
    assert {item.source_type for item in result.evidence} == {
        "mail", "calendar", "domain_knowledge"
    }
    assert result.quality.citation_valid is True
    assert result.agent_trace.iteration_count <= MAX_ITERATIONS == 4


def test_domain_mail_and_calendar_single_source_questions():
    cases = [
        ("Cell Leakage가 뭐야?", ["search_domain_knowledge"]),
        ("NAND 관련 메일 찾아줘", ["search_mail"]),
        ("NAND Yield Review 회의 언제 했어?", ["search_calendar"]),
    ]
    for question, expected in cases:
        workflow, search = build()
        result = asyncio.run(
            workflow.invoke(
                ChatRequest(user_id="kim", message=question),
                PolicyContext.from_user_id("kim"),
                ConversationMemory(),
            )
        )
        assert [action.tool for action, _owner in search.calls] == expected
        assert result.evidence


def test_follow_up_expands_previous_event_before_semantic_search():
    workflow, search = build()
    memory = ConversationMemory.model_validate(
        {
            "previous_event_reference": {
                "event_id": "event-kim-1",
                "subject": "NAND Yield Review",
            }
        }
    )
    result = asyncio.run(
        workflow.invoke(
            ChatRequest(user_id="kim", message="그 회의에서 Action 뭐였어?"),
            PolicyContext.from_user_id("kim"),
            memory,
        )
    )
    assert search.calls[0][0].tool == "expand_calendar_event"
    assert "FDC" in result.answer


def test_duplicate_search_is_blocked_and_loop_is_bounded():
    class RepeatingModel(RuleBasedAgentModel):
        async def judge(self, state):
            action = state["current_action"]
            return {
                "sufficient": False,
                "reason": "repeat",
                "missing_information": ["never complete"],
                "recommended_action": action.model_dump(),
            }

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(search, RepeatingModel())
    result = asyncio.run(
        workflow.invoke(
            ChatRequest(user_id="kim", message="NAND 메일 찾아줘"),
            PolicyContext.from_user_id("kim"),
            ConversationMemory(),
        )
    )
    fingerprints = [
        (
            action.tool,
            " ".join(action.query.casefold().split()),
            action.event_id,
            tuple(sorted(action.content_kinds)),
        )
        for action, _owner in search.calls
    ]
    assert len(fingerprints) == len(set(fingerprints))
    assert result.agent_trace.iteration_count <= 4
    assert result.quality.limited_answer is True
```

Add this structured-output fallback test directly against the analysis adapter,
so the assertion counts only the invalid analysis call plus its single retry:

```python
def test_invalid_structured_output_retries_once_then_falls_back():
    class BrokenStructuredLLM:
        def __init__(self):
            self.calls = 0

        async def complete_model(self, system, user, schema):
            self.calls += 1
            raise ValueError("invalid structured output")

        async def complete_text(self, system, user):
            return ""

    llm = BrokenStructuredLLM()
    model = StructuredAgentModel(llm, fallback=RuleBasedAgentModel())
    analysis = asyncio.run(
        model.analyze(
            "Cell Leakage가 뭐야?",
            ConversationMemory(),
            "Asia/Seoul",
        )
    )
    assert llm.calls == 2
    assert analysis.question_type == "domain_knowledge"
```

- [ ] **Step 2: Run tests and verify missing graph/model failures**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_graph.py -q
```

Expected: collection fails because the model adapter and graph do not exist.

- [ ] **Step 3: Implement deterministic analysis, source discovery, planning, judging, and answer composition**

Create `app/llm/agentic.py`. `RuleBasedAgentModel.analyze` must:

- classify explicit Mail, Calendar/meeting, Domain/technical, cross-source,
  and follow-up terms;
- extract `NAND` and `Cell Leakage` when present;
- extract `지난주`, `어제`, `이번주`, or `지난달` and call
  `resolve_time_range`;
- add candidate information needs in Mail, Calendar, Domain order;
- choose `expand_calendar_event` first when the question contains `그 회의`
  and structured memory has a previous event.

Implement the module with these public methods and deterministic behavior:

```python
import asyncio
import json
import re
from typing import Protocol

from app.domain.agentic import (
    JudgeDecision,
    QueryAnalysis,
    SearchDocument,
    ToolAction,
)
from app.retrieval.dates import resolve_time_range

TOOL_ORDER = (
    "search_mail",
    "search_calendar",
    "search_domain_knowledge",
)
SOURCE_FOR_TOOL = {
    "search_mail": "mail",
    "search_calendar": "calendar",
    "expand_calendar_event": "calendar",
    "search_domain_knowledge": "domain_knowledge",
}
NEED_FOR_SOURCE = {
    "mail": "관련 메일",
    "calendar": "관련 회의와 Action",
    "domain_knowledge": "기술적 의미",
}


class AgentModel(Protocol):
    async def analyze(self, question, memory, timezone_name) -> QueryAnalysis:
        raise NotImplementedError

    async def plan(
        self, question, analysis, observations, memory
    ) -> ToolAction | None:
        raise NotImplementedError

    async def judge(self, state) -> JudgeDecision:
        raise NotImplementedError

    async def replan(self, state) -> ToolAction | None:
        raise NotImplementedError

    async def answer(
        self, question, analysis, documents, missing
    ) -> str:
        raise NotImplementedError


class RuleBasedAgentModel:
    @staticmethod
    def _query(question, analysis):
        values = [
            analysis.entities[key]
            for key in ("product", "issue", "meeting", "person")
            if key in analysis.entities
        ]
        return " ".join(dict.fromkeys(values)) or question

    @staticmethod
    def _required_sources(analysis):
        sources = []
        for need in analysis.information_needs:
            if "메일" in need and "mail" not in sources:
                sources.append("mail")
            if any(word in need for word in ("회의", "Action", "일정")) and "calendar" not in sources:
                sources.append("calendar")
            if any(word in need for word in ("기술", "의미", "원인")) and "domain_knowledge" not in sources:
                sources.append("domain_knowledge")
        return sources

    async def analyze(self, question, memory, timezone_name):
        text = question.casefold()
        entities = dict(getattr(memory, "entities", {}) or {})
        if "nand" in text:
            entities["product"] = "NAND"
        if "cell leakage" in text or "cell leakage" in question:
            entities["issue"] = "Cell Leakage"
        if "yield review" in text:
            entities["meeting"] = "NAND Yield Review"
        if "김oo" in text or "김oo" in question.casefold():
            entities["person"] = "김OO"

        follow_up = "그 회의" in question
        wants_mail = "메일" in question
        wants_calendar = any(
            term in question for term in ("회의", "일정", "Action", "액션")
        )
        wants_event_detail = any(
            term in question
            for term in ("Action", "액션", "결정", "무슨 얘기", "회의 내용", "첨부")
        )
        wants_domain = any(
            term in question
            for term in ("기술적", "기술적으로", "무슨 의미", "뭐야", "원인")
        )
        if not any((wants_mail, wants_calendar, wants_domain)):
            wants_domain = True

        needs = []
        if wants_mail:
            needs.append(NEED_FOR_SOURCE["mail"])
        if wants_calendar or follow_up:
            needs.append(
                "관련 회의와 Action" if wants_event_detail or follow_up else "관련 회의"
            )
        if wants_domain:
            needs.append(NEED_FOR_SOURCE["domain_knowledge"])

        expression = next(
            (
                item
                for item in ("지난주", "어제", "이번주", "지난달")
                if item in question
            ),
            None,
        )
        resolved = resolve_time_range(expression, timezone_name=timezone_name)
        count = sum((wants_mail, wants_calendar or follow_up, wants_domain))
        if follow_up:
            question_type = "follow_up"
        elif count > 1:
            question_type = "multi_source"
        elif wants_mail:
            question_type = "mail_search"
        elif wants_calendar:
            question_type = "calendar_search"
        else:
            question_type = "domain_knowledge"
        return QueryAnalysis(
            intent="knowledge_query",
            question_type=question_type,
            entities=entities,
            time_expression=expression,
            start_at_utc=resolved.start_at_utc if resolved else None,
            end_at_utc=resolved.end_at_utc if resolved else None,
            information_needs=needs,
        )

    async def plan(self, question, analysis, observations, memory):
        previous = getattr(memory, "previous_event_reference", None)
        if analysis.question_type == "follow_up" and previous and not observations:
            return ToolAction(
                tool="expand_calendar_event",
                event_id=previous.event_id,
                reason="이전 대화의 동일 사용자 회의 참조 우선 확장",
            )
        used = {
            SOURCE_FOR_TOOL[item.action.tool]
            for item in observations
            if item.result.documents
        }
        for tool in TOOL_ORDER:
            source = SOURCE_FOR_TOOL[tool]
            if source in self._required_sources(analysis) and source not in used:
                return ToolAction(
                    tool=tool,
                    query=self._query(question, analysis),
                    reason=f"미확보 정보 source 검색: {source}",
                )
        return None

    async def judge(self, state):
        found = {item.source_type for item in state.get("documents", [])}
        required = self._required_sources(state["analysis"])
        missing_sources = [source for source in required if source not in found]
        missing = [NEED_FOR_SOURCE[source] for source in missing_sources]
        action = None
        if missing and state.get("iteration_count", 0) < 4:
            action = await self.replan(state)
        return JudgeDecision(
            sufficient=not missing,
            reason=("모든 정보 요구 충족" if not missing else "추가 source 필요"),
            missing_information=missing,
            recommended_action=action,
        )

    async def replan(self, state):
        return await self.plan(
            state["request"].message,
            state["analysis"],
            state.get("observations", []),
            state.get("conversation"),
        )

    async def answer(self, question, analysis, documents, missing):
        if not documents:
            return "확인 가능한 검색 근거가 없어 답변할 수 없습니다."
        labels = {
            "mail": "메일",
            "calendar": "회의",
            "domain_knowledge": "기술적 의미",
        }
        lines = ["확인된 근거입니다."]
        for index, item in enumerate(documents[:8], 1):
            lines.append(
                f"- {labels[item.source_type]}: {item.text} [S{index}]"
            )
        if missing:
            lines.append("\n확인하지 못한 항목: " + ", ".join(missing))
        return "\n".join(lines)

    async def complete_text(self, system, user):
        return "더미 모드의 안전한 일반 안내입니다."

    async def complete_messages(self, system, messages):
        return "더미 모드의 안전한 일반 안내입니다."


class StructuredAgentModel:
    def __init__(self, llm, *, fallback=None, timeout_seconds=150):
        self.llm = llm
        self.fallback = fallback or RuleBasedAgentModel()
        self.timeout_seconds = timeout_seconds

    async def _structured(self, schema, system, user, fallback_call):
        for _attempt in range(2):
            try:
                value = await asyncio.wait_for(
                    self.llm.complete_model(system, user, schema),
                    timeout=self.timeout_seconds,
                )
                return schema.model_validate(value)
            except Exception:
                continue
        return await fallback_call()

    async def analyze(self, question, memory, timezone_name):
        safe_memory = {
            "entities": getattr(memory, "entities", {}),
            "current_topic": getattr(memory, "current_topic", None),
        }
        return await self._structured(
            QueryAnalysis,
            "질문을 구조화하되 employee_id, index, DSL을 출력하지 마세요.",
            json.dumps({"question": question, "memory": safe_memory}, ensure_ascii=False),
            lambda: self.fallback.analyze(question, memory, timezone_name),
        )

    async def plan(self, question, analysis, observations, memory):
        fallback = lambda: self.fallback.plan(
            question, analysis, observations, memory
        )
        return await self._structured(
            ToolAction,
            "허용된 네 도구 중 다음 한 동작만 선택하세요.",
            json.dumps(
                {
                    "question": question,
                    "analysis": analysis.model_dump(mode="json"),
                    "observations": [
                        item.model_dump(mode="json") for item in observations
                    ],
                },
                ensure_ascii=False,
            ),
            fallback,
        )

    async def judge(self, state):
        safe = {
            "analysis": state["analysis"].model_dump(mode="json"),
            "sources": [item.source_type for item in state.get("documents", [])],
            "iteration_count": state.get("iteration_count", 0),
        }
        return await self._structured(
            JudgeDecision,
            "정보 요구 충족 여부만 판단하고 숨은 추론은 출력하지 마세요.",
            json.dumps(safe, ensure_ascii=False),
            lambda: self.fallback.judge(state),
        )

    async def replan(self, state):
        decision = state.get("judge_result")
        if decision and decision.recommended_action:
            return decision.recommended_action
        return await self.fallback.replan(state)

    async def answer(self, question, analysis, documents, missing):
        allowed = {f"S{index}" for index in range(1, len(documents[:8]) + 1)}
        context = [
            {"id": f"S{index}", "source": item.source_type, "text": item.text}
            for index, item in enumerate(documents[:8], 1)
        ]
        try:
            answer = await asyncio.wait_for(
                self.llm.complete_text(
                    "제공된 근거 ID만 인용하고 근거 없는 사실을 만들지 마세요.",
                    json.dumps({"question": question, "evidence": context}, ensure_ascii=False),
                ),
                timeout=self.timeout_seconds,
            )
            cited = set(re.findall(r"\[(S\d+)\]", answer))
            if cited and cited <= allowed:
                return answer
        except Exception:
            pass
        return await self.fallback.answer(question, analysis, documents, missing)
```

- [ ] **Step 4: Implement the explicit LangGraph state machine**

Create `app/graphs/multi_source.py` with `MAX_ITERATIONS = 4`, a `TypedDict`
state containing request, policy, conversation, analysis, candidates,
current action, observations, documents, fingerprints, judge decision,
iteration count, answer, memory update, disclosures, and trace.

Build these nodes and edges:

```python
graph.add_node("query_analyzer", self._query_analyzer)
graph.add_node("source_discovery", self._source_discovery)
graph.add_node("planner", self._planner)
graph.add_node("tool_executor", self._tool_executor)
graph.add_node("observation", self._observation)
graph.add_node("judge", self._judge)
graph.add_node("replanner", self._replanner)
graph.add_node("final_answer", self._final_answer)
graph.add_node("save_memory", self._save_memory)
graph.add_edge(START, "query_analyzer")
graph.add_edge("query_analyzer", "source_discovery")
graph.add_edge("source_discovery", "planner")
graph.add_edge("planner", "tool_executor")
graph.add_edge("tool_executor", "observation")
graph.add_edge("observation", "judge")
graph.add_conditional_edges(
    "judge",
    self._after_judge,
    {"continue": "replanner", "finish": "final_answer"},
)
graph.add_edge("replanner", "tool_executor")
graph.add_edge("final_answer", "save_memory")
graph.add_edge("save_memory", END)
```

Implement the executor with duplicate blocking, semantic iteration counting,
and deterministic event expansion:

```python
    async def _tool_executor(self, state):
        action = ToolAction.model_validate(state["current_action"])
        analysis = QueryAnalysis.model_validate(state["analysis"])
        fingerprints = set(state.get("fingerprints", []))
        fingerprint = action.fingerprint(analysis)
        trace = AgentTrace.model_validate(state.get("trace") or {})
        if fingerprint in fingerprints:
            trace = trace.model_copy(
                update={
                    "judge_decisions": [
                        *trace.judge_decisions,
                        "duplicate_search_blocked",
                    ][-8:]
                }
            )
            return {
                "current_result": SearchResult(
                    tool=action.tool,
                    query=action.query,
                    error_code="DUPLICATE_SEARCH",
                ),
                "force_finish": True,
                "trace": trace,
            }

        result = await self.search.execute(
            action, state["policy"], analysis
        )
        fingerprints.add(fingerprint)
        semantic_increment = 0 if action.tool == "expand_calendar_event" else 1
        iteration_count = state.get("iteration_count", 0) + semantic_increment
        tool_calls = [*trace.tool_calls, action.tool]

        needs_detail = any(
            any(term in need for term in ("Action", "결정", "내용", "첨부"))
            for need in analysis.information_needs
        )
        documents = list(result.documents)
        if action.tool == "search_calendar" and needs_detail:
            event_ids = list(
                dict.fromkeys(
                    item.parent_event_id or item.document_id
                    for item in documents
                    if item.content_kind in {"event", "attachment"}
                )
            )
            for event_id in event_ids[:1]:
                expansion = ToolAction(
                    tool="expand_calendar_event",
                    event_id=event_id,
                    reason="회의 내용 요구에 따른 deterministic 확장",
                )
                expansion_fingerprint = expansion.fingerprint(analysis)
                if expansion_fingerprint in fingerprints:
                    continue
                expanded = await self.search.execute(
                    expansion, state["policy"], analysis
                )
                fingerprints.add(expansion_fingerprint)
                tool_calls.append(expansion.tool)
                documents.extend(expanded.documents)
                result = result.model_copy(
                    update={
                        "documents": self._deduplicate_documents(documents),
                        "total_hits": len(
                            self._deduplicate_documents(documents)
                        ),
                        "disclosures": list(
                            dict.fromkeys(
                                [*result.disclosures, *expanded.disclosures]
                            )
                        )[:4],
                    }
                )

        return {
            "current_result": result,
            "fingerprints": sorted(fingerprints),
            "iteration_count": min(iteration_count, MAX_ITERATIONS),
            "force_finish": iteration_count >= MAX_ITERATIONS,
            "trace": trace.model_copy(
                update={
                    "tool_calls": tool_calls[-8:],
                    "iteration_count": min(iteration_count, MAX_ITERATIONS),
                }
            ),
        }
```

Add these helpers:

```python
    @staticmethod
    def _deduplicate_documents(documents):
        unique = {}
        for item in documents:
            existing = unique.get(item.document_id)
            if existing is None or item.score > existing.score:
                unique[item.document_id] = item
        return sorted(
            unique.values(), key=lambda item: (-item.score, item.document_id)
        )[:20]

    @staticmethod
    def _after_judge(state):
        decision = JudgeDecision.model_validate(state["judge_result"])
        if (
            state.get("force_finish")
            or decision.sufficient
            or state.get("iteration_count", 0) >= MAX_ITERATIONS
            or decision.recommended_action is None
        ):
            return "finish"
        return "continue"
```

Convert documents to `Evidence` with fresh sequential `S1..S8`, the request
owner, current ACL decision ID, stable document ID, SHA-256 content hash, and
only safe locators. Run `CitationValidator` before returning
`FastRAGResult`. No evidence returns a limited `NO_EVIDENCE` result.

- [ ] **Step 5: Delegate through the existing Fast facade without changing its public methods**

Add optional `agentic=None` to `FastRAGWorkflow.__init__`, store it, and add at
the beginning of `invoke` before the existing graph path:

```python
        if self.agentic is not None:
            return await self.agentic.invoke(request, policy, conversation)
```

Leave `respond_general`, `respond_without_evidence`, and the old graph path
unchanged so existing tests that construct `FastRAGWorkflow` without the new
argument keep exercising the original behavior.

- [ ] **Step 6: Run graph and Fast regression tests and commit**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_graph.py tests/mail_rag/test_fast_rag.py tests/mail_rag/test_chat_api.py -q
```

Expected: canonical flow and all existing Fast/API tests pass.

Commit:

```bash
git add app/graphs/fast_rag.py app/graphs/multi_source.py app/llm/agentic.py tests/mail_rag/test_multi_source_graph.py
git commit -m "feat(rag): add bounded multi-source loop"
```

---

### Task 6: Demo composition root, API memory persistence, and CLI

**Files:**
- Modify: `app/api/dependencies.py:5-172`
- Modify: `app/api/routes/chat.py:204-262,321-591`
- Modify: `app/api/main.py:1-152`
- Create: `scripts/run_multi_source_demo.py`
- Create: `tests/mail_rag/test_multi_source_demo_api.py`

**Interfaces:**
- Consumes: Task 2 memory helper, Task 3 dummy search, Task 5 graph/model, existing `ServiceContainer` and `create_app`
- Produces: `build_demo_container(settings: Settings | None = None) -> ServiceContainer`
- Produces: `build_configured_app() -> FastAPI`
- Produces: demo ASGI object when `MULTI_SOURCE_DEMO=true`
- Produces: CLI exit code `0` only when canonical flow is grounded and complete

- [ ] **Step 1: Write failing demo API and same-conversation follow-up tests**

Create `tests/mail_rag/test_multi_source_demo_api.py`:

```python
import asyncio

import httpx

from app.api.dependencies import build_demo_container
from app.api.main import create_app


async def post(app, payload):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
        return await client.post("/v1/chat", json=payload)


def test_demo_api_runs_canonical_flow_without_external_services():
    app = create_app(build_demo_container())
    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "conversation_id": "demo-1",
                "message": "김OO이 메일에서 이야기한 NAND 수율 문제가 어떤 회의에서 논의됐고 어떤 Action을 하기로 했으며 기술적으로 어떤 의미인지 설명해줘.",
                "response_mode": "fast",
            },
        )
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "fast_rag"
    assert {item["source_type"] for item in body["references"]} == {
        "mail", "calendar", "domain_knowledge"
    }
    assert "FDC" in body["answer"]
    assert body["quality"]["citation_valid"] is True


def test_demo_api_follow_up_uses_saved_event_reference():
    app = create_app(build_demo_container())
    asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "conversation_id": "demo-follow-up",
                "message": "NAND Yield Review 회의 찾아줘",
                "response_mode": "fast",
            },
        )
    )
    follow_up = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "conversation_id": "demo-follow-up",
                "message": "그 회의에서 Action 뭐였어?",
                "response_mode": "fast",
            },
        )
    )
    assert follow_up.status_code == 200
    assert "FDC" in follow_up.json()["answer"]


def test_demo_readiness_is_ready():
    app = create_app(build_demo_container())
    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
            return await client.get("/ready")
    response = asyncio.run(request())
    assert response.status_code == 200
    assert set(response.json()["dependencies"].values()) == {"ready"}
```

- [ ] **Step 2: Run demo tests and verify missing builder failure**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_demo_api.py -q
```

Expected: collection fails because `build_demo_container` does not exist.

- [ ] **Step 3: Compose production and demo multi-source services**

In `build_container`, construct `SourceRegistry`,
`OpenSearchMultiSourceSearch`, `StructuredAgentModel`, and
`MultiSourceAgenticWorkflow`, then pass the workflow as `agentic=` to the
existing `FastRAGWorkflow`. Add the mail/calendar aliases to readiness checks.

Add `DemoReadiness` and `build_demo_container`:

```python
class DemoReadiness:
    async def check(self):
        return {
            "opensearch": "ready",
            "mongo": "ready",
            "aliases": "ready",
            "agent_model": "ready",
        }


def build_demo_container(settings=None):
    from pathlib import Path

    from app.config.settings import get_settings
    from app.domain.chat import RouteDecision
    from app.graphs.fast_rag import FastRAGWorkflow
    from app.graphs.multi_source import MultiSourceAgenticWorkflow
    from app.llm.agentic import RuleBasedAgentModel
    from app.persistence.conversations import InMemoryConversationStore
    from app.persistence.research_jobs import InMemoryResearchJobStore
    from app.retrieval.multi_source import InMemoryMultiSourceSearch
    from app.retrieval.source_registry import SourceRegistry

    current = settings or get_settings()
    registry = SourceRegistry.from_settings(current)
    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"), registry
    )
    agentic = MultiSourceAgenticWorkflow(
        search,
        RuleBasedAgentModel(),
        timezone_name=current.default_user_timezone,
    )

    class DemoRouter:
        async def route(self, request, conversation=None):
            general = request.message.casefold().strip() in {
                "안녕", "안녕하세요", "hello", "hi"
            }
            return RouteDecision(
                route="general" if general else "fast",
                reason_code="demo_rule",
                confidence=1,
                estimated_searches=0 if general else 1,
            )

        async def contextualize_request(self, request, conversation=None):
            return request

    return ServiceContainer(
        router=DemoRouter(),
        fast=FastRAGWorkflow(None, RuleBasedAgentModel(), agentic=agentic),
        deep=None,
        conversations=InMemoryConversationStore(),
        jobs=InMemoryResearchJobStore(),
        readiness=DemoReadiness(),
    )
```

Use the `complete_text` and `complete_messages` methods defined on
`RuleBasedAgentModel` in Task 5 so the existing general-chat facade returns its
fixed safe Korean demo response without an external gateway.

- [ ] **Step 4: Persist `FastRAGResult.agent_memory` in the existing save path**

Update `_save_turn` to accept `agent_memory=None`. Immediately before saving,
apply it to the loaded/new `ConversationMemory`:

```python
    if agent_memory is not None:
        from app.persistence.conversations import apply_agent_memory_update

        memory = apply_agent_memory_update(memory, agent_memory, policy)
```

Pass `result.agent_memory` from the Fast success/limited branches. Do not add
agent memory fields to `ChatResponse`; public references and safe node runs are
sufficient.

- [ ] **Step 5: Add the demo ASGI export and CLI**

At the end of `app/api/main.py`, add:

```python
def build_configured_app() -> FastAPI:
    from app.api.dependencies import build_container, build_demo_container
    from app.config.settings import Settings

    settings = Settings.from_env()
    container = (
        build_demo_container(settings)
        if settings.multi_source_demo
        else build_container(settings)
    )
    return create_app(container)


app = build_configured_app() if Settings.from_env().multi_source_demo else None
```

This keeps test imports free of production dependency construction and makes
the documented demo command export a real ASGI app when the flag is set.

Create `scripts/run_multi_source_demo.py`:

```python
import argparse
import asyncio

from app.api.dependencies import build_demo_container
from app.domain.chat import ChatRequest
from app.domain.policy import PolicyContext


DEFAULT_QUESTION = (
    "김OO이 지난주 메일에서 이야기한 NAND 수율 문제가 어떤 회의에서 "
    "논의됐고 회의에서 어떤 Action을 하기로 했으며 기술적으로 어떤 의미인지 설명해줘."
)


async def run(question: str, user_id: str) -> int:
    container = build_demo_container()
    request = ChatRequest(user_id=user_id, message=question, response_mode="fast")
    result = await container.fast.invoke(
        request,
        PolicyContext.from_user_id(user_id),
        None,
    )
    print(result.answer)
    print("\nSources:", ", ".join(item.source_type for item in result.evidence))
    if result.agent_trace:
        print("Tool calls:", " -> ".join(result.agent_trace.tool_calls))
        print("Iterations:", result.agent_trace.iteration_count)
    required = {"mail", "calendar", "domain_knowledge"}
    actual = {item.source_type for item in result.evidence}
    return 0 if required <= actual and result.quality.citation_valid else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--user-id", default="kim")
    args = parser.parse_args()
    return asyncio.run(run(args.question, args.user_id))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run API, CLI, and existing API tests and commit**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_demo_api.py tests/mail_rag/test_chat_api.py tests/mail_rag/test_conversations.py -q
python scripts/run_multi_source_demo.py
```

Expected: tests pass; CLI exits 0 and prints tool order containing Mail,
Calendar, event expansion, and Domain.

Commit:

```bash
git add app/api/dependencies.py app/api/main.py app/api/routes/chat.py scripts/run_multi_source_demo.py tests/mail_rag/test_multi_source_demo_api.py
git commit -m "feat(demo): run multi-source RAG locally"
```

---

### Task 7: Frontend domain/calendar reference support

**Files:**
- Modify: `frontend/src/rag/types.ts:70-84`
- Modify: `frontend/src/services/ragApiService.ts:30-42`
- Modify: `frontend/src/rag/ConversationPanel.tsx:1-130`
- Modify: `frontend/src/services/ragApiService.test.ts:1-320`
- Modify: `frontend/src/rag/__tests__/RagLabApp.test.tsx:90-135`

**Interfaces:**
- Consumes: unchanged `ChatResponse.references` JSON with extended `source_type`
- Produces: TypeScript union `'mail' | 'wiki' | 'statistic' | 'domain_knowledge' | 'calendar'`
- Produces: Korean labels `메일`, `Wiki`, `통계`, `도메인 지식`, and `일정/회의`

- [ ] **Step 1: Write failing service guard and rendering tests**

Add to `frontend/src/services/ragApiService.test.ts` a valid response whose
references include:

```typescript
references: [
  {
    evidence_id: 'S1',
    source_type: 'calendar',
    document_id: 'event-kim-1',
    title: 'NAND Yield Review',
    excerpt: 'FDC 로그를 확인한다.',
  },
  {
    evidence_id: 'S2',
    source_type: 'domain_knowledge',
    document_id: 'domain-cell-leakage',
    title: 'Cell Leakage',
    excerpt: '저장 전하 누설 현상이다.',
  },
],
```

Assert that the service accepts both references rather than returning
`INVALID_RESPONSE`.

Add a `RagLabApp` render case and assert:

```typescript
expect(screen.getByText('일정/회의')).toBeInTheDocument()
expect(screen.getByText('도메인 지식')).toBeInTheDocument()
```

- [ ] **Step 2: Run frontend tests and verify union/guard failures**

Run:

```bash
cd frontend && npm test -- --run src/services/ragApiService.test.ts src/rag/__tests__/RagLabApp.test.tsx
```

Expected: calendar/domain references are rejected or rendered as raw values.

- [ ] **Step 3: Extend types, runtime guard, and labels**

Set `ChatReference.source_type` in `frontend/src/rag/types.ts` to:

```typescript
source_type: 'mail' | 'wiki' | 'statistic' | 'domain_knowledge' | 'calendar'
```

Change the runtime allowlist in `ragApiService.ts` to:

```typescript
['mail', 'wiki', 'statistic', 'domain_knowledge', 'calendar'].includes(
  String(value.source_type),
)
```

Add to `ConversationPanel.tsx`:

```typescript
const SOURCE_LABELS: Record<ChatReference['source_type'], string> = {
  mail: '메일',
  wiki: 'Wiki',
  statistic: '통계',
  domain_knowledge: '도메인 지식',
  calendar: '일정/회의',
}
```

Render `SOURCE_LABELS[reference.source_type]` in the reference metadata chip
instead of the raw source type. Keep title/excerpt and team/week behavior.

- [ ] **Step 4: Run frontend tests/build and commit**

Run:

```bash
cd frontend && npm test -- --run
cd frontend && npm run build
```

Expected: all frontend tests pass and TypeScript production build succeeds.

Commit:

```bash
git add frontend/src/rag/types.ts frontend/src/services/ragApiService.ts frontend/src/rag/ConversationPanel.tsx frontend/src/services/ragApiService.test.ts frontend/src/rag/__tests__/RagLabApp.test.tsx
git commit -m "feat(ui): show calendar and domain sources"
```

---

### Task 8: Security acceptance, full regression, and runbook

**Files:**
- Create: `docs/multi_source_demo.md`
- Modify: `docs/api.md`
- Modify: `docs/operations.md`
- Modify: `tests/mail_rag/test_multi_source_demo_api.py`
- Modify: `tests/mail_rag/test_multi_source_opensearch.py`

**Interfaces:**
- Consumes: all previous tasks
- Produces: operator/user commands for dummy and production modes
- Produces: final requirement-by-requirement security and behavior evidence

- [ ] **Step 1: Add final acceptance tests for aliases, decoys, citations, and public contract**

Add tests that:

```python
from pathlib import Path


def test_application_search_code_contains_no_physical_ews_index_names():
    roots = [Path("app"), Path("scripts")]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root in roots
        for path in root.rglob("*.py")
    )
    assert "ews-mail-v1" not in text
    assert "ews-calendar-v1" not in text


def test_demo_response_never_contains_decoy_content():
    body = canonical_demo_response()
    serialized = str(body)
    assert "다른 사용자의" not in serialized
    assert "비활성 문서" not in serialized
    assert "취소된 회의" not in serialized


def test_every_answer_citation_exists_in_same_owner_references():
    body = canonical_demo_response()
    evidence_ids = {item["evidence_id"] for item in body["references"]}
    cited = set(re.findall(r"\[(S\d+)\]", body["answer"]))
    assert cited
    assert cited <= evidence_ids
```

Factor `canonical_demo_response()` inside the test module so all assertions use
a fresh demo app and `user_id="kim"`.

- [ ] **Step 2: Run acceptance tests and fix any failing invariant before documentation**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_dummy.py tests/mail_rag/test_multi_source_opensearch.py tests/mail_rag/test_multi_source_graph.py tests/mail_rag/test_multi_source_demo_api.py -q
```

Expected: every security, flow, loop, follow-up, citation, and alias assertion
passes.

- [ ] **Step 3: Write exact demo and production runbooks**

Create `docs/multi_source_demo.md` with:

```markdown
# Multi-Source Agentic RAG Demo

## Requirements

- Python dependencies from `requirements.txt`
- Node dependencies already installed with `cd frontend && npm ci` when needed
- no OpenSearch, MongoDB, embedding, or LLM service for demo mode

## CLI

```bash
python scripts/run_multi_source_demo.py
python scripts/run_multi_source_demo.py "지난주 NAND 회의에서 Action 뭐였어?"
```

The canonical run must show `search_mail`, `search_calendar`,
`expand_calendar_event`, and `search_domain_knowledge`, exit with status 0,
and print Mail, Calendar, and Domain citations.

## API and frontend

```bash
MULTI_SOURCE_DEMO=true uvicorn app.api.main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev
```

Use `user_id=kim` and Fast or Auto mode. No `.env` secrets are required.

## Production aliases

Configure `DOMAIN_KNOWLEDGE_INDEX`, `MAIL_INDEX_ALIAS`, and
`CALENDAR_INDEX_ALIAS`. The defaults are `syld_gpt`, `ews-mail-active`, and
`ews-calendar-active`. The service validates the Mail and Calendar aliases in
readiness and never accepts an index name from the model.

## Security checks

Mail is filtered by the current `employee_id` and `is_active=true`. Calendar
adds `is_cancelled=false`. Event expansion repeats the same filters. Missing or
foreign events return no bundle and do not reveal whether they exist.
```

Update `docs/api.md` with the two new reference source values and state that
the request contract is unchanged. Update `docs/operations.md` with the three
new alias environment variables, the demo flag, readiness expectations, and
the rule that alias cutover requires no agent code change.

- [ ] **Step 4: Run the complete verification matrix**

Run:

```bash
python -m pytest -q
python scripts/run_multi_source_demo.py
cd frontend && npm test -- --run
cd frontend && npm run build
git diff --check
git status --short
```

Expected:

- complete Python suite passes;
- CLI exits 0 with the canonical four-tool path;
- complete frontend suite passes;
- frontend production build succeeds;
- `git diff --check` emits no output;
- status contains only intended implementation/docs changes plus the user's
  untracked `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md` reference file.

- [ ] **Step 5: Commit documentation and final acceptance tests**

```bash
git add docs/api.md docs/operations.md docs/multi_source_demo.md tests/mail_rag/test_multi_source_demo_api.py tests/mail_rag/test_multi_source_opensearch.py
git commit -m "docs(rag): add multi-source demo runbook"
```

---

## Completion Audit

Before reporting completion, create a requirement table from the acceptance
criteria in
`docs/superpowers/specs/2026-08-16-multi-source-agentic-rag-design.md` and
record the exact test, command output, or source inspection proving each row.
Do not use the green full suite as the only evidence for source aliases, ACL
query bodies, cross-owner decoys, event expansion, four-step canonical flow,
duplicate blocking, iteration bound, follow-up resolution, or grounded
citations; cite their focused tests as well.

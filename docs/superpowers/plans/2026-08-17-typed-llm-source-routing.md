# Typed LLM Source Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove raw-question keyword routing from the multi-source agent so an LLM produces one validated typed intent decision and every later search, expansion, sufficiency, answer, and memory decision is deterministic over typed fields.

**Architecture:** `StructuredAgentModel` is reduced to a bounded natural-language analyzer that returns `QueryAnalysis`. A new `TypedAgentPolicy` maps validated logical source requests to allowlisted tools, resolves follow-up/detail behavior, judges evidence coverage, and composes the citation-bound extractive fallback without reading the original question or display-only `information_needs`. Free-form demo mode uses the configured LLM; offline tests and smoke commands inject named pre-authored typed decisions.

**Tech Stack:** Python 3.12, Pydantic v2, asyncio, LangGraph, OpenAI-compatible gateway/OpenRouter, pytest, FastAPI/httpx ASGI tests, React/Vite/Vitest.

**Approved design:** `docs/superpowers/specs/2026-08-17-typed-llm-source-routing-design.md`

## Global Constraints

- Follow red-green-refactor for every behavioral slice: add or migrate the focused test, run it and observe the expected failure, implement the smallest typed behavior, then rerun it.
- Do not add Korean/English keyword arrays, regular expressions, substring checks, exact-question maps, or model-free natural-language classifiers for intent, source, time, event reference, or detail selection.
- `information_needs` is display/trace data only. It must not select a source, tool, date, detail expansion, missing source, or memory action.
- LLM output may name only logical sources and bounded semantic queries. Physical aliases, owner IDs, filters, OpenSearch DSL, and tool names remain server-owned and fail closed.
- Model timeout, invalid JSON, schema failure, or missing model configuration must produce `analysis_status="unavailable"`, zero retrieval calls, a sanitized limited answer, and no inferred semantic intent.
- Keep the existing maximum of four tool calls, duplicate-action fingerprints, same-owner event authorization, cancellation/active filters, evidence normalization, citation validation, and recoverable-source behavior.
- Do not change `fixtures/multi_source_demo/corpus.json`, OpenSearch mappings, embeddings, ranking, or configured aliases.
- Do not touch or stage the user's untracked `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md`.
- The model-backed paraphrase evaluation is mandatory evidence. A fake analyzer may test orchestration but must never be reported as proof of natural-language understanding.
- Commit after each task only when its focused tests are green. Stage explicit paths, never `git add .`.

---

### Task 1: Add the typed intent and date contracts

**Files:**

- Modify: `app/domain/agentic.py`
- Modify: `app/retrieval/dates.py`
- Modify: `tests/mail_rag/test_agentic_contracts.py`
- Modify: `tests/mail_rag/test_agentic_dates.py`

**Interfaces:**

- `SourceRequest(source: SourceName, query: str)`
- `IntentDecision(intent, source_requests, entities, time_scope, exact_date, event_reference, calendar_detail_required, information_needs)`
- `QueryAnalysis.from_intent(decision, *, now, timezone_name) -> QueryAnalysis`
- `QueryAnalysis.unavailable() -> QueryAnalysis`
- `resolve_time_scope(scope, *, exact_date=None, now=None, timezone_name="Asia/Seoul") -> ResolvedTimeRange | None`

- [ ] **Step 1: Add failing contract tests for valid and hostile model output**

Append tests that use the public models rather than constructing `question_type` directly:

```python
from datetime import date

from pydantic import ValidationError

from app.domain.agentic import IntentDecision, QueryAnalysis, SourceRequest


def test_intent_decision_accepts_unique_bounded_logical_sources():
    decision = IntentDecision(
        intent="weekly_schedule",
        source_requests=[SourceRequest(source="calendar", query="팀 일정")],
        time_scope="current_week",
        calendar_detail_required=False,
    )

    analysis = QueryAnalysis.from_intent(
        decision, now=NOW, timezone_name="Asia/Seoul"
    )

    assert analysis.analysis_status == "ready"
    assert analysis.question_type == "calendar_search"
    assert [item.source for item in analysis.source_requests] == ["calendar"]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "intent": "duplicate",
            "source_requests": [
                {"source": "calendar", "query": "일정"},
                {"source": "calendar", "query": "회의"},
            ],
        },
        {
            "intent": "bad_exact_date",
            "source_requests": [{"source": "calendar", "query": "일정"}],
            "time_scope": "exact_date",
        },
        {
            "intent": "bad_detail",
            "source_requests": [{"source": "mail", "query": "NAND"}],
            "calendar_detail_required": True,
        },
        {
            "intent": "owner_injection",
            "source_requests": [{"source": "mail", "query": "NAND"}],
            "entities": {"employee_id": "lee"},
        },
        {
            "intent": "index_injection",
            "source_requests": [{"source": "mail", "query": "NAND"}],
            "entities": {"index_name": "ews-mail-v1"},
        },
        {
            "intent": "oversized_query",
            "source_requests": [{"source": "mail", "query": "x" * 1001}],
        },
        {
            "intent": "oversized_needs",
            "information_needs": [f"need-{index}" for index in range(9)],
        },
        {
            "intent": "coerced_boolean",
            "source_requests": [{"source": "calendar", "query": "일정"}],
            "calendar_detail_required": "false",
        },
    ],
)
def test_intent_decision_rejects_inconsistent_or_server_owned_fields(payload):
    with pytest.raises(ValidationError):
        IntentDecision.model_validate(payload)


def test_intent_decision_forbids_tool_and_owner_fields_at_every_level():
    with pytest.raises(ValidationError):
        IntentDecision.model_validate(
            {
                "intent": "hostile",
                "source_requests": [
                    {
                        "source": "mail",
                        "query": "NAND",
                        "tool": "search_mail",
                        "owner": "lee",
                    }
                ],
                "employee_id": "lee",
            }
        )
```

- [ ] **Step 2: Run the focused contract tests and confirm the red state**

Run:

```bash
python -m pytest tests/mail_rag/test_agentic_contracts.py -q
```

Expected: collection or test failures report that `IntentDecision`, `SourceRequest`, and `QueryAnalysis.from_intent` do not exist.

- [ ] **Step 3: Implement strict typed models and server-derived question type**

In `app/domain/agentic.py`, add the bounded literals and strict models. Keep existing shared entity bounds and reserve server-controlled entity names:

```python
TimeScope = Literal[
    "none", "yesterday", "previous_week", "current_week",
    "previous_month", "exact_date",
]
EventReferenceMode = Literal["none", "previous_event"]
AnalysisStatus = Literal["ready", "unavailable"]

_RESERVED_ENTITY_KEYS = {
    "employeeid", "userid", "owner", "ownerid", "tenantid", "index",
    "indexname", "tool", "filter", "acl", "opensearchdsl", "querydsl",
}


class SourceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    source: SourceName
    query: str = Field(min_length=1, max_length=1000)


class IntentDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    intent: str = Field(min_length=1, max_length=100)
    source_requests: list[SourceRequest] = Field(default_factory=list, max_length=3)
    entities: dict[EntityKey, EntityValue] = Field(default_factory=dict, max_length=16)
    time_scope: TimeScope = "none"
    exact_date: date | None = None
    event_reference: EventReferenceMode = "none"
    calendar_detail_required: StrictBool = False
    information_needs: list[BoundedModelText] = Field(default_factory=list, max_length=8)

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
```

Refactor `QueryAnalysis` to store `analysis_status`, `source_requests`, `time_scope`, `exact_date`, `event_reference`, and `calendar_detail_required`; preserve server-resolved UTC fields and display-only `information_needs`. Add `date` to the `datetime` import. Keep `question_type` as a server-derived compatibility field, never an LLM field:

```python
class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    analysis_status: AnalysisStatus = "ready"
    intent: str = Field(min_length=1, max_length=100)
    question_type: Literal[
        "general_chat", "domain_knowledge", "mail_search",
        "calendar_search", "multi_source", "follow_up",
    ]
    source_requests: list[SourceRequest] = Field(default_factory=list, max_length=3)
    entities: dict[EntityKey, EntityValue] = Field(default_factory=dict, max_length=16)
    time_scope: TimeScope = "none"
    exact_date: date | None = None
    event_reference: EventReferenceMode = "none"
    calendar_detail_required: StrictBool = False
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    information_needs: list[BoundedModelText] = Field(default_factory=list, max_length=8)

    @classmethod
    def unavailable(cls) -> "QueryAnalysis":
        return cls(
            analysis_status="unavailable",
            intent="analysis_unavailable",
            question_type="general_chat",
        )

    @classmethod
    def from_intent(cls, decision, *, now=None, timezone_name="Asia/Seoul"):
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

    @model_validator(mode="after")
    def validate_analysis_state(self):
        if (self.start_at_utc is None) != (self.end_at_utc is None):
            raise ValueError("UTC range requires both endpoints")
        if self.start_at_utc and self.start_at_utc >= self.end_at_utc:
            raise ValueError("invalid UTC range")
        if self.analysis_status == "unavailable" and (
            self.source_requests
            or self.time_scope != "none"
            or self.exact_date is not None
            or self.event_reference != "none"
            or self.calendar_detail_required
            or self.start_at_utc is not None
        ):
            raise ValueError("unavailable analysis cannot carry semantic decisions")
        return self
```

Ensure `QueryAnalysis` validation rejects sources when `analysis_status="unavailable"` and validates paired UTC endpoints.

- [ ] **Step 4: Replace natural-language date parsing with typed date arithmetic tests**

Replace direct string-based tests in `tests/mail_rag/test_agentic_dates.py` with:

```python
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
```

Run:

```bash
python -m pytest tests/mail_rag/test_agentic_dates.py -q
```

Expected before implementation: import failure for `resolve_time_scope`.

- [ ] **Step 5: Implement `resolve_time_scope` without inspecting user text**

Replace `resolve_time_range(expression, ...)` with a typed function. `ResolvedTimeRange.expression` should be renamed to `scope` and typed as `TimeScope` so no raw expression survives:

```python
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
```

- [ ] **Step 6: Run both focused suites and commit**

Run:

```bash
python -m pytest tests/mail_rag/test_agentic_contracts.py tests/mail_rag/test_agentic_dates.py -q
git diff --check
git add app/domain/agentic.py app/retrieval/dates.py tests/mail_rag/test_agentic_contracts.py tests/mail_rag/test_agentic_dates.py
git commit -m "refactor(rag): add typed intent contract"
```

Expected: focused suites pass and the commit contains no fixture changes.

---

### Task 2: Introduce a deterministic policy over typed fields

**Files:**

- Create: `app/domain/agentic_policy.py`
- Create: `tests/mail_rag/test_typed_agent_policy.py`

**Interfaces:**

- `TypedAgentPolicy.required_sources(analysis) -> list[SourceName]`
- `TypedAgentPolicy.next_action(analysis, observations, memory) -> ToolAction | None`
- `TypedAgentPolicy.missing_information(analysis, documents) -> list[str]`
- `TypedAgentPolicy.judge(analysis, observations, documents, memory, iteration_count) -> JudgeDecision`
- `TypedAgentPolicy.answer(documents, missing) -> str`

- [ ] **Step 1: Write policy tests proving question text and display text are irrelevant**

Create tests with a helper that takes only typed input:

```python
def typed_analysis(*requests, detail=False, event_reference="none"):
    return QueryAnalysis.from_intent(
        IntentDecision(
            intent="test",
            source_requests=list(requests),
            event_reference=event_reference,
            calendar_detail_required=detail,
            information_needs=["메일 일정 기술 의미 Action 뭐야"],
        ),
        now=NOW,
        timezone_name="Asia/Seoul",
    )


def test_required_sources_use_only_source_requests():
    analysis = typed_analysis(SourceRequest(source="calendar", query="팀 일정"))
    assert TypedAgentPolicy().required_sources(analysis) == ["calendar"]


def test_next_action_maps_logical_source_to_allowlisted_tool_and_query():
    analysis = typed_analysis(
        SourceRequest(source="mail", query="NAND 수율"),
        SourceRequest(source="calendar", query="NAND Yield Review"),
    )
    action = TypedAgentPolicy().next_action(analysis, [], ConversationMemory())
    assert action.tool == "search_mail"
    assert action.query == "NAND 수율"


def test_previous_event_expands_only_valid_saved_reference():
    analysis = typed_analysis(
        SourceRequest(source="calendar", query="이전 회의"),
        event_reference="previous_event",
    )
    memory = ConversationMemory(
        previous_event_reference=EventReference(
            event_id="event-kim-1", subject="NAND Yield Review"
        )
    )
    action = TypedAgentPolicy().next_action(analysis, [], memory)
    assert action.tool == "expand_calendar_event"
    assert action.event_id == "event-kim-1"


def test_previous_event_without_saved_reference_does_not_guess():
    analysis = typed_analysis(
        SourceRequest(source="calendar", query="이전 회의"),
        event_reference="previous_event",
    )
    assert TypedAgentPolicy().next_action(
        analysis, [], ConversationMemory()
    ) is None


def test_missing_information_uses_requested_source_and_detail_flag_only():
    analysis = typed_analysis(
        SourceRequest(source="calendar", query="팀 일정"), detail=True
    )
    event = SearchDocument(
        document_id="event-1",
        source_type="calendar",
        title="평범한 제목",
        text="평범한 본문",
        score=1,
        content_kind="event",
        source_id="event-1",
    )
    assert TypedAgentPolicy().missing_information(analysis, [event]) == [
        "관련 회의 상세 내용"
    ]
```

Also test source order, attempted-source skipping, unique missing labels, unavailable analysis, no documents, and extractive `[S1]` citation numbering. The graph-level one-expansion maximum remains in Task 4.

- [ ] **Step 2: Run the new suite and confirm the red state**

Run:

```bash
python -m pytest tests/mail_rag/test_typed_agent_policy.py -q
```

Expected: collection fails because `app.domain.agentic_policy` does not exist.

- [ ] **Step 3: Implement the typed allowlist policy**

Create the module with fixed logical mappings, not phrase inference:

```python
SOURCE_TO_TOOL: dict[SourceName, ToolName] = {
    "mail": "search_mail",
    "calendar": "search_calendar",
    "domain_knowledge": "search_domain_knowledge",
}
TOOL_TO_SOURCE: dict[ToolName, SourceName] = {
    tool: source for source, tool in SOURCE_TO_TOOL.items()
}
SOURCE_LABELS: dict[SourceName, str] = {
    "mail": "관련 메일",
    "calendar": "관련 회의",
    "domain_knowledge": "기술적 의미",
}


class TypedAgentPolicy:
    @staticmethod
    def required_sources(analysis: QueryAnalysis) -> list[SourceName]:
        if analysis.analysis_status != "ready":
            return []
        return [item.source for item in analysis.source_requests]

    @staticmethod
    def next_action(analysis, observations, memory):
        if analysis.analysis_status != "ready":
            return None
        if analysis.event_reference == "previous_event" and not observations:
            previous = getattr(memory, "previous_event_reference", None)
            if previous is None:
                return None
            return ToolAction(
                tool="expand_calendar_event",
                event_id=previous.event_id,
                reason="validated previous event reference",
            )
        attempted = {
            TOOL_TO_SOURCE[item.action.tool]
            for item in observations
            if item.action.tool != "expand_calendar_event"
        }
        for request in analysis.source_requests:
            if request.source not in attempted:
                return ToolAction(
                    tool=SOURCE_TO_TOOL[request.source],
                    query=request.query,
                    reason=f"typed source request: {request.source}",
                )
        return None
```

Implement `missing_information` by comparing `analysis.source_requests[].source` with `documents[].source_type`. For `calendar_detail_required`, treat a normalized Calendar attachment returned by authorized event expansion as detail evidence; do not inspect titles or text for “Action”. `judge` recommends only `next_action`, and `answer` remains sanitized, extractive, and citation-numbered from normalized documents.

- [ ] **Step 4: Run policy and contract tests and commit**

Run:

```bash
python -m pytest tests/mail_rag/test_typed_agent_policy.py tests/mail_rag/test_agentic_contracts.py -q
git diff --check
git add app/domain/agentic_policy.py tests/mail_rag/test_typed_agent_policy.py
git commit -m "refactor(rag): add typed agent policy"
```

Expected: all selected tests pass.

---

### Task 3: Make the structured model the only natural-language interpreter

**Files:**

- Modify: `app/llm/agentic.py`
- Modify: `tests/mail_rag/test_multi_source_graph.py`

**Interfaces:**

- `AgentAnalyzer.analyze(question, memory, timezone_name) -> QueryAnalysis`
- `StructuredAgentModel(llm, *, timeout_seconds=150, now=None)`
- No `fallback`, `plan`, `judge`, `replan`, or `answer` methods on the analyzer contract.

- [ ] **Step 1: Add failing analyzer tests for success, timeout, malformed output, and no merge**

Use fakes that return a whole typed decision independent of the question string:

```python
class FixedIntentLLM:
    def __init__(self, decision):
        self.decision = decision
        self.schemas = []

    async def complete_model(self, system, user, schema):
        self.schemas.append(schema)
        return self.decision


def test_structured_analyzer_requests_intent_decision_and_does_not_add_sources():
    llm = FixedIntentLLM(
        IntentDecision(
            intent="weekly_schedule",
            source_requests=[SourceRequest(source="calendar", query="팀 일정")],
            time_scope="current_week",
        )
    )
    model = StructuredAgentModel(llm, now=NOW)

    analysis = asyncio.run(
        model.analyze(
            "메일 기술 원인 뭐야 같은 단어가 있어도 모델 결정만 사용",
            ConversationMemory(),
            "Asia/Seoul",
        )
    )

    assert llm.schemas == [IntentDecision]
    assert [item.source for item in analysis.source_requests] == ["calendar"]
    assert analysis.time_scope == "current_week"


@pytest.mark.parametrize("failure", [ValueError("bad json"), TimeoutError()])
def test_structured_analyzer_failure_is_unavailable_without_keyword_fallback(failure):
    model = StructuredAgentModel(RaisingIntentLLM(failure), timeout_seconds=0.01)
    analysis = asyncio.run(
        model.analyze("이번주 일정알려줘", ConversationMemory(), "Asia/Seoul")
    )
    assert analysis.analysis_status == "unavailable"
    assert analysis.source_requests == []
```

Add an assertion that only bounded safe memory (`entities`, `current_topic`) is serialized and that a hostile extra owner/index/tool field fails schema validation and ends unavailable after exactly two attempts.

- [ ] **Step 2: Run the focused analyzer tests and confirm the red state**

Run the selected test node names:

```bash
python -m pytest tests/mail_rag/test_multi_source_graph.py -q -k "structured_analyzer"
```

Expected: tests fail because the model still requests `QueryAnalysis`, merges `RuleBasedAgentModel`, and accepts a fallback.

- [ ] **Step 3: Replace `app/llm/agentic.py` with the bounded analyzer**

Delete `TIME_EXPRESSIONS`, `SAME_EVENT_REFERENCES`, `ISO_DATE`, `_time_expression`, `_saved_event_action`, `RuleBasedAgentModel`, all source/detail keyword scans, and the multi-step model planner/judge. Keep a narrow protocol and implementation:

```python
class AgentAnalyzer(Protocol):
    async def analyze(
        self, question: str, memory: object, timezone_name: str
    ) -> QueryAnalysis: ...


class StructuredAgentModel:
    def __init__(self, llm, *, timeout_seconds=150, now=None):
        self.llm = llm
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.now = now

    async def analyze(self, question, memory, timezone_name):
        safe_memory = {
            "entities": dict(list((getattr(memory, "entities", {}) or {}).items())[-16:]),
            "current_topic": getattr(memory, "current_topic", None),
        }
        user = json.dumps(
            {"question": question, "memory": safe_memory}, ensure_ascii=False
        )
        for _attempt in range(2):
            try:
                decision = await asyncio.wait_for(
                    self.llm.complete_model(
                        INTENT_SYSTEM_PROMPT, user, IntentDecision
                    ),
                    timeout=self.timeout_seconds,
                )
                validated = IntentDecision.model_validate(decision)
                return QueryAnalysis.from_intent(
                    validated, now=self.now, timezone_name=timezone_name
                )
            except Exception:
                continue
        return QueryAnalysis.unavailable()
```

The system prompt must describe semantic responsibilities and every enum, state that `information_needs` is display-only, forbid owner/index/filter/tool/DSL output, and require the smallest sufficient set of logical sources. It must not contain phrase-to-label examples that amount to exact-question routing.

- [ ] **Step 4: Run analyzer tests and source-level regression scan**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_graph.py -q -k "structured_analyzer"
rg -n "TIME_EXPRESSIONS|SAME_EVENT_REFERENCES|RuleBasedAgentModel|_required_sources|_time_expression|뭐야.*domain|in question|in need" app/llm/agentic.py
```

Expected: tests pass and `rg` prints no matches.

- [ ] **Step 5: Commit the analyzer replacement**

Run:

```bash
git diff --check
git add app/llm/agentic.py tests/mail_rag/test_multi_source_graph.py
git commit -m "refactor(rag): rely on typed llm analysis"
```

---

### Task 4: Migrate the LangGraph workflow to `TypedAgentPolicy`

**Files:**

- Modify: `app/graphs/multi_source.py`
- Modify: `tests/mail_rag/test_multi_source_graph.py`
- Modify: `tests/mail_rag/test_multi_source_opensearch.py`
- Modify: `tests/mail_rag/test_multi_source_dummy.py`

**Interfaces:**

- `MultiSourceAgenticWorkflow(search, analyzer, *, policy=None, timezone_name=None)`
- `MultiSourceState.candidates: list[SourceName]`
- `ANALYSIS_UNAVAILABLE_MESSAGE` and `ANALYSIS_UNAVAILABLE_DISCLOSURE`

- [ ] **Step 1: Introduce a static typed analyzer test helper and migrate fixture construction**

At the top of `tests/mail_rag/test_multi_source_graph.py`, add:

```python
class StaticIntentAnalyzer:
    def __init__(self, decision, *, now=NOW):
        self.decision = decision
        self.now = now

    async def analyze(self, question, memory, timezone_name):
        return QueryAnalysis.from_intent(
            self.decision, now=self.now, timezone_name=timezone_name
        )


def analyzer(*requests, **decision_fields):
    return StaticIntentAnalyzer(
        IntentDecision(source_requests=list(requests), intent="test", **decision_fields)
    )
```

Replace every `RuleBasedAgentModel` subclass/fake with either `StaticIntentAnalyzer`, an unavailable analyzer, or a direct `TypedAgentPolicy` unit test. Change `analysis()` helpers in OpenSearch and dummy tests to use `QueryAnalysis.from_intent`; retrieval tests should remain focused on filters and aliases.

- [ ] **Step 2: Add graph-level failing tests for safe failure and typed-only behavior**

Add:

```python
class UnavailableAnalyzer:
    async def analyze(self, question, memory, timezone_name):
        return QueryAnalysis.unavailable()


def test_unavailable_analysis_performs_zero_searches_and_returns_limited_result():
    search = RecordingSearch()
    workflow = MultiSourceAgenticWorkflow(search, UnavailableAnalyzer())

    result = asyncio.run(invoke(workflow, "이번주 일정 뭐야?"))

    assert search.calls == []
    assert result.evidence == []
    assert result.quality.limited_answer is True
    assert result.execution.status == "limited"
    assert result.execution.failure_stage == "planning"
    assert result.execution.error_code == "ANALYSIS_UNAVAILABLE"
    assert "질문을 안전하게 구조화하지 못했습니다" in result.answer


def test_calendar_only_typed_decision_never_adds_domain_search():
    search = FixtureSearch()
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(
            SourceRequest(source="calendar", query="팀 일정"),
            time_scope="current_week",
            information_needs=["뭐야 기술 원인 메일"],
        ),
    )
    result = asyncio.run(invoke(workflow, "문구는 정책 입력이 아니다"))
    assert [action.tool for action, _owner in search.calls] == ["search_calendar"]
    assert result.quality.limited_answer is False
```

Retain and adapt tests for four-action maximum, duplicate fingerprints, stable event authorization, owner/inactive/cancelled decoys, citation fallback, recoverable errors, Calendar range boundaries, and memory ownership.

- [ ] **Step 3: Run the graph suite and confirm failures identify rule dependencies**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_graph.py -q
```

Expected: failures reference removed `RuleBasedAgentModel` methods and the graph still searches display text.

- [ ] **Step 4: Inject and use `TypedAgentPolicy` throughout the graph**

Change construction and node behavior:

```python
class MultiSourceAgenticWorkflow:
    def __init__(self, search, analyzer, *, policy=None, timezone_name=None):
        self.search = search
        self.analyzer = analyzer
        self.policy = policy or TypedAgentPolicy()
        ...

    async def _source_discovery(self, state):
        return {"candidates": self.policy.required_sources(state["analysis"])}

    async def _planner(self, state):
        action = self.policy.next_action(
            state["analysis"],
            state.get("observations", []),
            state["conversation"],
        )
        return {"current_action": action}
```

In `_tool_executor`, replace the `information_needs` scan with `analysis.calendar_detail_required`; authorize at most one stable event expansion and keep fingerprint/action bounds. In `_judge` and `_replanner`, call only policy methods. In `_final_answer`, call `policy.answer`; if analysis is unavailable, bypass retrieval-specific wording and return the fixed sanitized analysis-unavailable message with `failure_stage="planning"`, `error_code="ANALYSIS_UNAVAILABLE"`, `search_count=0`, and `include_in_llm_history=False`. In `_save_memory`, derive unresolved information from `policy.missing_information` and never pass the request text.

The final graph source must not call the analyzer after `_query_analyzer` and must not read `state["request"].message` outside that analyzer node.

- [ ] **Step 5: Run graph, retrieval, dummy, and security regression tests**

Run:

```bash
python -m pytest \
  tests/mail_rag/test_multi_source_graph.py \
  tests/mail_rag/test_multi_source_opensearch.py \
  tests/mail_rag/test_multi_source_dummy.py \
  tests/mail_rag/test_agentic_memory.py -q
rg -n "RuleBasedAgentModel|information_needs.*for|for .*information_needs|request.*message" app/graphs/multi_source.py
```

Expected: all suites pass; `rg` may show the single `_query_analyzer` request message access and no rule model or `information_needs` iteration.

- [ ] **Step 6: Commit the graph migration**

Run:

```bash
git diff --check
git add app/graphs/multi_source.py tests/mail_rag/test_multi_source_graph.py tests/mail_rag/test_multi_source_opensearch.py tests/mail_rag/test_multi_source_dummy.py
git commit -m "refactor(rag): drive graph from typed policy"
```

---

### Task 5: Separate free-form LLM demo mode from explicit offline scenarios

**Files:**

- Create: `app/llm/demo_scenarios.py`
- Modify: `app/api/dependencies.py`
- Modify: `scripts/run_multi_source_demo.py`
- Modify: `tests/mail_rag/test_multi_source_demo_api.py`
- Modify: `tests/mail_rag/test_ai_dependencies.py`

**Interfaces:**

- `ScenarioName = Literal["canonical", "weekly-calendar", "event-action", "followup"]`
- `scenario_decisions(name) -> tuple[IntentDecision, ...]`
- `StaticIntentAnalyzer(decisions, *, now)` consumes decisions by call order only.
- `build_demo_container(settings=None, *, agent_model=None, router=None)`
- CLI `--offline-scenario {canonical,weekly-calendar,event-action,followup}`

- [ ] **Step 1: Add failing demo wiring and CLI tests**

Replace secret-free free-form assertions with explicit scenario assertions:

```python
def test_demo_container_accepts_injected_typed_analyzer_without_external_services(monkeypatch):
    static = StaticIntentAnalyzer(scenario_decisions("weekly-calendar"), now=NOW)
    container = build_demo_container(
        Settings(openrouter_api_key=""), agent_model=static
    )
    assert container.fast.agentic.analyzer is static
    assert isinstance(container.fast.agentic.search, InMemoryMultiSourceSearch)


def test_demo_cli_weekly_calendar_offline_scenario_is_calendar_only():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_multi_source_demo.py",
            "--offline-scenario",
            "weekly-calendar",
        ],
        cwd=ROOT,
        env={**os.environ, "OPENROUTER_API_KEY": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Tool calls: search_calendar" in result.stdout
    assert "search_domain_knowledge" not in result.stdout


def test_free_form_demo_without_llm_key_fails_safely():
    result = subprocess.run(
        [sys.executable, "scripts/run_multi_source_demo.py", "이번주 일정 뭐야?"],
        cwd=ROOT,
        env={**os.environ, "OPENROUTER_API_KEY": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "OPENROUTER_API_KEY" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
```

Add a two-turn follow-up test that injects the two pre-authored decisions and verifies the second turn expands only the saved same-owner event.

- [ ] **Step 2: Run demo tests and confirm the red state**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_demo_api.py tests/mail_rag/test_ai_dependencies.py -q
```

Expected: failures show `RuleBasedAgentModel` wiring and the missing scenario CLI option.

- [ ] **Step 3: Implement explicit typed scenarios with no question lookup**

Create `app/llm/demo_scenarios.py`. Scenario selection is an explicit CLI/API fixture choice, not natural-language classification:

```python
SCENARIOS: dict[str, tuple[IntentDecision, ...]] = {
    "canonical": (
        IntentDecision(
            intent="cross_source_investigation",
            source_requests=[
                SourceRequest(source="mail", query="김OO NAND 수율 Cell Leakage"),
                SourceRequest(source="calendar", query="NAND Yield Review"),
                SourceRequest(source="domain_knowledge", query="NAND Cell Leakage"),
            ],
            entities={"person": "김OO", "product": "NAND", "issue": "Cell Leakage"},
            time_scope="previous_week",
            calendar_detail_required=True,
        ),
    ),
    "weekly-calendar": (
        IntentDecision(
            intent="weekly_schedule",
            source_requests=[SourceRequest(source="calendar", query="일정")],
            time_scope="current_week",
        ),
    ),
    "event-action": (
        IntentDecision(
            intent="event_action",
            source_requests=[SourceRequest(source="calendar", query="NAND Yield Review")],
            time_scope="exact_date",
            exact_date=date(2026, 8, 7),
            calendar_detail_required=True,
        ),
    ),
    "followup": (
        IntentDecision(
            intent="find_event",
            source_requests=[SourceRequest(source="calendar", query="NAND Yield Review")],
        ),
        IntentDecision(
            intent="previous_event_detail",
            source_requests=[SourceRequest(source="calendar", query="previous event")],
            event_reference="previous_event",
            calendar_detail_required=True,
        ),
    ),
}
```

`StaticIntentAnalyzer` must pop the next decision in sequence and never receive a mapping from question text to decision.

- [ ] **Step 4: Rewire demo construction and CLI modes**

Change `build_demo_container` to accept injected analyzer/router. If no analyzer is injected and the API key is configured, build `OpenAILLMGateway` plus `StructuredAgentModel`; if the key is absent, inject an `UnavailableAnalyzer` and report `agent_model="unavailable"` in readiness. Keep in-memory search and persistence.

Do not use a semantic greeting classifier in `DemoRouter`; because the CLI and demo API already request `response_mode="fast"`, return a fixed fast route with `reason_code="demo_fast"` for this container. General chat remains the production router's concern.

Update `scripts/run_multi_source_demo.py` so:

```python
parser.add_argument(
    "--offline-scenario",
    choices=("canonical", "weekly-calendar", "event-action", "followup"),
)
```

- explicit scenarios construct `StaticIntentAnalyzer` and use their own display question(s);
- a positional free-form question requires a non-empty configured key and uses `StructuredAgentModel`;
- no positional question and no scenario defaults to `canonical` offline for reproducible smoke testing;
- missing credentials for free-form input return exit code 2 with a safe configuration message;
- the `followup` scenario invokes two turns against one conversation ID and checks that turn two uses `expand_calendar_event`.

- [ ] **Step 5: Run demo/API tests and smoke all offline scenarios**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_demo_api.py tests/mail_rag/test_ai_dependencies.py -q
python scripts/run_multi_source_demo.py --offline-scenario canonical
python scripts/run_multi_source_demo.py --offline-scenario weekly-calendar
python scripts/run_multi_source_demo.py --offline-scenario event-action
python scripts/run_multi_source_demo.py --offline-scenario followup
```

Expected: all tests and commands pass without `OPENROUTER_API_KEY`; weekly-calendar prints only `search_calendar`.

- [ ] **Step 6: Commit demo separation**

Run:

```bash
git diff --check
git add app/llm/demo_scenarios.py app/api/dependencies.py scripts/run_multi_source_demo.py tests/mail_rag/test_multi_source_demo_api.py tests/mail_rag/test_ai_dependencies.py
git commit -m "refactor(demo): separate typed offline scenarios"
```

---

### Task 6: Add a real model-backed paraphrase evaluation gate

**Files:**

- Create: `evals/datasets/agent_intent_paraphrases.json`
- Create: `scripts/evaluate_agent_intent.py`
- Create: `tests/mail_rag/test_agent_intent_evaluation.py`

**Interface:**

- `python scripts/evaluate_agent_intent.py [--dataset PATH]`
- Exit `0` only when every phrase produces exactly the expected typed source/time/detail decision.
- Exit `2` on missing model credentials; exit `1` on evaluation mismatch or model failure.

- [ ] **Step 1: Add the dataset and failing evaluator tests**

Create the JSON dataset:

```json
{
  "cases": [
    {
      "question": "이번주 일정알려줘",
      "expected_sources": ["calendar"],
      "expected_time_scope": "current_week",
      "calendar_detail_required": false
    },
    {
      "question": "이번주 일정 뭐야?",
      "expected_sources": ["calendar"],
      "expected_time_scope": "current_week",
      "calendar_detail_required": false
    },
    {
      "question": "이번주 일정이 뭔지 알려줘",
      "expected_sources": ["calendar"],
      "expected_time_scope": "current_week",
      "calendar_detail_required": false
    },
    {
      "question": "이번 주 스케줄 보여줘",
      "expected_sources": ["calendar"],
      "expected_time_scope": "current_week",
      "calendar_detail_required": false
    }
  ]
}
```

In unit tests, validate dataset schema, exact comparison logic, safe JSON output, and missing-key exit behavior. A fake model response is permitted only for evaluator mechanics:

```python
def test_evaluator_requires_configured_model_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main([]) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_compare_rejects_extra_source():
    expected = EvaluationCase(
        question="이번주 일정 뭐야?",
        expected_sources=["calendar"],
        expected_time_scope="current_week",
        calendar_detail_required=False,
    )
    actual = QueryAnalysis.from_intent(
        IntentDecision(
            intent="wrong",
            source_requests=[
                SourceRequest(source="calendar", query="일정"),
                SourceRequest(source="domain_knowledge", query="일정 의미"),
            ],
            time_scope="current_week",
        ),
        now=NOW,
    )
    assert compare_case(expected, actual) == [
        "sources: expected ['calendar'], got ['calendar', 'domain_knowledge']"
    ]
```

- [ ] **Step 2: Run evaluator unit tests and confirm the red state**

Run:

```bash
python -m pytest tests/mail_rag/test_agent_intent_evaluation.py -q
```

Expected: collection fails because the evaluator module and dataset do not exist.

- [ ] **Step 3: Implement the evaluator using the configured real gateway**

The script must load `Settings`, refuse an empty key, build `AsyncOpenAI`, `OpenAILLMGateway`, and `StructuredAgentModel`, analyze every case with a fresh `ConversationMemory`, and compare:

```python
def compare_case(expected, actual):
    failures = []
    actual_sources = [item.source for item in actual.source_requests]
    if actual.analysis_status != "ready":
        failures.append(f"analysis_status: {actual.analysis_status}")
    if actual_sources != expected.expected_sources:
        failures.append(
            f"sources: expected {expected.expected_sources}, got {actual_sources}"
        )
    if actual.time_scope != expected.expected_time_scope:
        failures.append(
            f"time_scope: expected {expected.expected_time_scope}, got {actual.time_scope}"
        )
    if actual.calendar_detail_required != expected.calendar_detail_required:
        failures.append(
            "calendar_detail_required: expected "
            f"{expected.calendar_detail_required}, got {actual.calendar_detail_required}"
        )
    return failures
```

Print one sanitized PASS/FAIL record per case and an aggregate count; do not print the key, raw provider response, hidden prompt, memory, or traceback.

- [ ] **Step 4: Run unit tests and the required real-model gate**

First run:

```bash
python -m pytest tests/mail_rag/test_agent_intent_evaluation.py -q
```

Then export an actual `OPENROUTER_API_KEY` in the current shell and run:

```bash
python scripts/evaluate_agent_intent.py
```

Expected: four PASS records, `4/4 passed`, exit code 0. In the current workspace this command is blocked because no key is configured; implementation is not acceptance-complete until this exact real-model gate passes. Do not substitute the fake unit test.

- [ ] **Step 5: Commit the evaluator after the real-model gate passes**

Run:

```bash
git diff --check
git add evals/datasets/agent_intent_paraphrases.json scripts/evaluate_agent_intent.py tests/mail_rag/test_agent_intent_evaluation.py
git commit -m "test(rag): add model intent evaluation"
```

---

### Task 7: Remove legacy rule claims, document operation, and run full acceptance

**Files:**

- Modify: `docs/multi_source_demo.md`
- Modify: `tests/mail_rag/test_multi_source_graph.py`
- Modify: `tests/mail_rag/test_multi_source_demo_api.py`
- Verify: `app/domain/agentic.py`
- Verify: `app/retrieval/dates.py`
- Verify: `app/llm/agentic.py`
- Verify: `app/graphs/multi_source.py`
- Verify: `app/api/dependencies.py`
- Verify: `tests/mail_rag/test_agentic_contracts.py`
- Verify: `tests/mail_rag/test_agentic_dates.py`
- Verify: `tests/mail_rag/test_multi_source_dummy.py`
- Verify: `tests/mail_rag/test_multi_source_opensearch.py`

- [ ] **Step 1: Add a source-level regression test for forbidden intent classification**

Add a narrow architectural test that reads only the production analyzer and graph:

```python
def test_production_agent_has_no_raw_text_intent_classifier():
    paths = [ROOT / "app/llm/agentic.py", ROOT / "app/graphs/multi_source.py"]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    forbidden = (
        "RuleBasedAgentModel",
        "TIME_EXPRESSIONS",
        "SAME_EVENT_REFERENCES",
        "_required_sources",
        "_time_expression",
    )
    assert all(token not in text for token in forbidden)
```

Keep behavioral tests as the primary proof: display-only `information_needs` containing misleading words cannot alter calls, and unavailable analysis performs zero searches.

- [ ] **Step 2: Update demo and operations documentation**

Document these exact behaviors in `docs/multi_source_demo.md`:

- `python scripts/run_multi_source_demo.py` runs the reproducible offline canonical scenario;
- `--offline-scenario weekly-calendar` tests the August 17–21 Calendar-only answer without a model;
- positional free-form questions require `OPENROUTER_API_KEY` and use typed LLM analysis;
- missing/invalid model analysis produces zero searches and a limited result;
- offline scenarios are pre-authored typed inputs, not evidence that arbitrary Korean is understood;
- `python scripts/evaluate_agent_intent.py` is the required real-model paraphrase gate;
- the August fixture remains business-day data for August 3–31, 2026, with the current week resolving to August 17–23 as a half-open local week and returning business-day events August 17–21.

Remove statements that arbitrary free-form questions work without an LLM and all mentions of `RuleBasedAgentModel` as demo behavior.

- [ ] **Step 3: Verify constructor migration and run the backend suite**

Confirm the earlier tasks removed all legacy production symbols and test constructors:

```bash
rg -n "RuleBasedAgentModel|resolve_time_range|time_expression=" app scripts
rg -n "RuleBasedAgentModel\\(|resolve_time_range|time_expression=|QueryAnalysis\\(" tests scripts
```

Expected: no matches. If a match remains, return to the task that owns that exact file and complete its `IntentDecision`/`SourceRequest` migration. Do not silence failures by adding defaults that recreate semantic inference.

Run:

```bash
python -m pytest tests/mail_rag -q
```

Expected: all backend tests pass with no external credentials required; the paraphrase evaluator is tested mechanically but its real-model invocation remains a separate gate.

- [ ] **Step 4: Run frontend regression checks**

Run:

```bash
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: Vitest, ESLint, TypeScript, and Vite build all succeed.

- [ ] **Step 5: Run offline, live-ASGI, security, and real-model acceptance**

Run offline scenarios:

```bash
python scripts/run_multi_source_demo.py --offline-scenario canonical
python scripts/run_multi_source_demo.py --offline-scenario weekly-calendar
python scripts/run_multi_source_demo.py --offline-scenario event-action
python scripts/run_multi_source_demo.py --offline-scenario followup
```

Run the demo ASGI integration test and security-focused selections:

```bash
python -m pytest tests/mail_rag/test_multi_source_demo_api.py -q -k "asgi or decoy or citation or owner or followup"
python -m pytest tests/mail_rag/test_multi_source_opensearch.py -q -k "owner or active or cancelled or alias or filter"
```

With an actual `OPENROUTER_API_KEY` already exported in the current shell, run the real model gate and one configured free-form smoke:

```bash
python scripts/evaluate_agent_intent.py
python scripts/run_multi_source_demo.py "이번주 일정 뭐야?"
```

Expected: the evaluator reports `4/4 passed`; the free-form smoke calls `search_calendar` only, returns the August 17–21 evidence, and does not call `search_domain_knowledge`.

- [ ] **Step 6: Scan for regressions and inspect the final diff**

Run:

```bash
rg -n "RuleBasedAgentModel|TIME_EXPRESSIONS|SAME_EVENT_REFERENCES|_required_sources|resolve_time_range" app scripts docs/multi_source_demo.md
rg -n "T[B]D|T[O]DO|F[I]XME|implement l[a]ter|skip for n[o]w" app tests scripts docs/multi_source_demo.md evals/datasets/agent_intent_paraphrases.json
git diff --check
git status --short
git diff --stat 250d229..HEAD
```

Expected: no legacy rule-routing matches, no implementation placeholders introduced, clean diff checks, and `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md` remains untracked and untouched.

- [ ] **Step 7: Commit documentation and final migrations**

Run:

```bash
git add docs/multi_source_demo.md tests/mail_rag/test_multi_source_graph.py tests/mail_rag/test_multi_source_demo_api.py
git commit -m "docs(rag): document typed source routing"
git status --short
```

Expected: only the user's pre-existing untracked `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md` remains. Do not merge or publish without a separate user request.

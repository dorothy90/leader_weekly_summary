# Single Multi-Source Chat Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `/v1/chat` request invoke `MultiSourceAgenticWorkflow` directly, with no Fast/Deep/General/Diagnostic/Corpus routing layer or route-shaped frontend contract.

**Architecture:** The chat endpoint loads owner policy and conversation memory, invokes one injected multi-source workflow, validates citations, stores the turn and agent memory, and returns execution diagnostics. Production and demo containers expose that workflow directly. The explicit `/v1/research/*` job API remains independent.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, LangGraph, pytest, React 19, TypeScript 6, Vitest, Testing Library, Vite 8.

## Global Constraints

- `POST /v1/chat` has exactly one execution path: `MultiSourceAgenticWorkflow`.
- Remove `response_mode`, `mode`, and `routing` from the public chat contract; stale `response_mode` requests must fail validation.
- Do not synthesize a `fast` route for compatibility.
- Keep source selection typed and model-backed; do not add raw-question keyword classification.
- Preserve deterministic owner, lifecycle, alias, date, tool-budget, duplicate-search, event-expansion, and citation controls.
- Analysis failure performs zero searches and returns a limited result.
- Keep `/v1/research/*` job-management endpoints unchanged.
- Preserve the user's untracked `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md` and Task 6 evaluator files.

---

## File Structure

- `app/domain/chat.py`: public single-entry request/response contracts.
- `app/persistence/conversations.py`: route-free stored turn contract.
- `app/api/routes/chat.py`: direct multi-source HTTP orchestration only.
- `app/api/dependencies.py`: production/demo construction of the direct agentic dependency.
- `app/graphs/multi_source.py`: unchanged execution engine consumed by chat.
- `app/graphs/router.py`, `app/graphs/general_intents.py`, `app/graphs/fast_rag.py`: delete after active consumers are removed.
- `scripts/run_multi_source_demo.py`: send the new route-free request contract.
- `frontend/src/rag/types.ts`: route-free TypeScript API types, including bounded `AgentTrace`.
- `frontend/src/services/ragApiService.ts`: validate the new chat response.
- `frontend/src/rag/RequestPanel.tsx`: request scope without execution-mode controls.
- `frontend/src/rag/ConversationPanel.tsx`: answer/execution rendering without route steps.
- `frontend/src/rag/InspectorPanel.tsx`: actual agent execution and trace diagnostics.
- Backend and frontend tests: replace route assertions with direct-agent assertions.

---

### Task 1: Route-Free Domain and Memory Contracts

**Files:**
- Modify: `tests/mail_rag/test_domain_contracts.py`
- Modify: `tests/mail_rag/test_conversations.py`
- Modify: `app/domain/chat.py`
- Modify: `app/persistence/conversations.py`

**Interfaces:**
- Produces: `ChatRequest(user_id, message, conversation_id?, filters)` with no `response_mode`.
- Produces: `ChatResponse(..., agent_trace: AgentTrace | None, execution: ExecutionMetadata | None)` with no `mode` or `routing`.
- Produces: `TurnRecord` containing content, execution, trace, quality, disclosures, and citations without route fields.

- [ ] **Step 1: Write failing contract tests**

Add tests equivalent to:

```python
def test_chat_contract_is_single_entry_and_rejects_response_mode():
    request = ChatRequest(user_id="kim", message="이번 주 일정")
    assert "response_mode" not in request.model_dump()
    with pytest.raises(ValidationError):
        ChatRequest(user_id="kim", message="질문", response_mode="fast")


def test_chat_response_exposes_agent_trace_without_route_envelope():
    response = ChatResponse(
        conversation_id="conversation-1",
        answer="답변",
        references=[],
        quality=QualityStatus(citation_valid=None, retrieval_mode="deterministic"),
        disclosures=[],
        trace_id="trace-1",
        agent_trace=AgentTrace(tool_calls=[], judge_decisions=["no_action"]),
    )
    dumped = response.model_dump()
    assert "mode" not in dumped
    assert "routing" not in dumped
    assert dumped["agent_trace"]["judge_decisions"] == ["no_action"]
```

Update conversation tests to construct `TurnRecord` without `route`,
`executed_system`, or `reason_code` and assert sanitized persistence still retains
execution, citations, and trace IDs.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
python -m pytest tests/mail_rag/test_domain_contracts.py tests/mail_rag/test_conversations.py -q
```

Expected: failures because `response_mode`, routing fields, and route-bearing
`TurnRecord` are still required.

- [ ] **Step 3: Implement the minimal route-free contracts**

In `app/domain/chat.py`, remove `response_mode`, `RouteDecision`, and
`RoutingDiagnostics`; define the public response shape as:

```python
class ChatResponse(BaseModel):
    conversation_id: str
    answer: str | None = None
    references: list[ChatReference] = Field(default_factory=list)
    quality: QualityStatus | None = None
    disclosures: list[str] = Field(default_factory=list)
    trace_id: str
    agent_trace: AgentTrace | None = None
    execution: ExecutionMetadata | None = None
```

In `app/persistence/conversations.py`, remove the three route-only fields and their
sanitization branches:

```python
class TurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_content: str = Field(min_length=1, max_length=4000)
    assistant_content: str | None = Field(default=None, max_length=8000)
    execution: ExecutionMetadata
    trace_id: str | None = Field(default=None, max_length=128)
    quality: QualityStatus | None = None
    disclosures: list[str] = Field(default_factory=list, max_length=4)
    cited_evidence: list[Evidence] = Field(default_factory=list, max_length=8)
```

- [ ] **Step 4: Run the contract tests and confirm GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/domain/chat.py app/persistence/conversations.py \
  tests/mail_rag/test_domain_contracts.py tests/mail_rag/test_conversations.py
git commit -m "refactor(api)!: remove chat route contract" \
  -m "BREAKING CHANGE: /v1/chat no longer accepts response_mode or returns mode and routing."
```

---

### Task 2: Direct Multi-Source Chat Handler

**Files:**
- Modify: `tests/mail_rag/test_chat_api.py`
- Modify: `app/api/routes/chat.py`

**Interfaces:**
- Consumes: `services.agentic.invoke(request, policy, conversation) -> FastRAGResult`.
- Produces: one `ChatResponse` path with safe references, agent trace, execution,
  disclosures, and memory persistence.

- [ ] **Step 1: Replace route-specific API tests with a failing direct-agent test**

Use a recording fake with only the required interface:

```python
class RecordingAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def invoke(self, request, policy, conversation):
        self.calls.append((request, policy, conversation))
        return self.result


def test_every_chat_request_invokes_the_agentic_workflow_directly():
    agent = RecordingAgent(success_result())
    response = client(agentic=agent).post(
        "/v1/chat",
        json={"user_id": "kim", "message": "이번 주 일정", "filters": {}},
    )
    assert response.status_code == 200
    assert len(agent.calls) == 1
    assert response.json()["agent_trace"]["tool_calls"] == ["search_calendar"]
    assert "routing" not in response.json()
```

Retain focused tests for owner validation, citation filtering, failed/limited
execution, disclosure preservation, conversation ownership, memory update, and save
failure. Delete tests whose only subject is selecting General, Clarify, Deep,
Diagnostic, or Corpus routes.

- [ ] **Step 2: Run the direct API test and confirm RED**

```bash
python -m pytest tests/mail_rag/test_chat_api.py::test_every_chat_request_invokes_the_agentic_workflow_directly -q
```

Expected: failure because the handler still calls `services.router.route` and the
container has no direct `agentic` path.

- [ ] **Step 3: Collapse `chat.py` to one execution path**

Remove router diagnostics, diagnostic/corpus answer helpers, general fallback,
Deep Research execution, and route branches. Keep the safe result and memory
helpers, rename Fast-specific helpers to agent-neutral names, and make the endpoint
body equivalent to:

```python
memory, context_disclosures = await _load_memory(
    services, conversation_id, policy, supplied=supplied_id
)
result = await services.agentic.invoke(payload, policy, memory)
answer, references, quality, disclosures, owned = _safe_agent_result(result, policy)
execution = _execution_for(result, evidence_count=len(owned))
public_answer = None if execution.status == "failed" else answer
saved = await _save_turn(
    services,
    conversation_id,
    policy,
    payload,
    memory,
    public_answer,
    execution,
    disclosures,
    owned,
    trace_id=trace_id,
    quality=quality,
    agent_memory=result.agent_memory if execution.status != "failed" else None,
)
return ChatResponse(
    conversation_id=conversation_id,
    answer=public_answer,
    references=references,
    quality=quality,
    disclosures=[*context_disclosures, *disclosures],
    trace_id=trace_id,
    agent_trace=result.agent_trace,
    execution=execution,
)
```

Update `_save_turn` to create the route-free `TurnRecord` from Task 1.

- [ ] **Step 4: Run focused chat API tests and confirm GREEN**

```bash
python -m pytest tests/mail_rag/test_chat_api.py -q
```

Expected: all retained single-entry chat tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/api/routes/chat.py tests/mail_rag/test_chat_api.py
git commit -m "refactor(api): invoke multi-source chat directly"
```

---

### Task 3: Direct Production and Demo Dependency Wiring

**Files:**
- Modify: `tests/mail_rag/test_multi_source_demo_api.py`
- Modify: `tests/mail_rag/test_integration_fixes.py`
- Modify: `tests/mail_rag/test_mail_content_api.py`
- Modify: `tests/mail_rag/test_research_api.py`
- Modify: `tests/mail_rag/test_tracing.py`
- Modify: `app/api/dependencies.py`
- Delete: `app/graphs/router.py`
- Delete: `app/graphs/general_intents.py`
- Delete: `app/graphs/fast_rag.py`
- Delete: `tests/mail_rag/test_router.py`
- Delete: `tests/mail_rag/test_fast_rag.py`

**Interfaces:**
- Produces: `ServiceContainer(agentic, conversations, jobs, ...)`.
- Produces: `build_container()` and `build_demo_container()` with direct agentic
  wiring and no router injection argument.

- [ ] **Step 1: Write failing container wiring tests**

Update demo and dependency tests to assert:

```python
container = build_demo_container(settings, agent_model=typed_model)
assert isinstance(container.agentic, MultiSourceAgenticWorkflow)
assert not hasattr(container, "router")
assert not hasattr(container, "fast")
assert not hasattr(container, "deep")
```

Update every `ServiceContainer(...)` test fixture to pass named route-free fields.

- [ ] **Step 2: Run the wiring tests and confirm RED**

```bash
python -m pytest \
  tests/mail_rag/test_multi_source_demo_api.py \
  tests/mail_rag/test_integration_fixes.py \
  tests/mail_rag/test_mail_content_api.py \
  tests/mail_rag/test_research_api.py -q
```

Expected: failures because `ServiceContainer` still requires `router`, `fast`, and
`deep`, and demo construction wraps the agent.

- [ ] **Step 3: Implement direct service wiring**

Change the container boundary to:

```python
@dataclass(frozen=True)
class ServiceContainer:
    agentic: Any
    conversations: Any
    jobs: Any
    mail_content: Any = None
    traces: Any = None
    readiness: Any = None
```

In production, build `OpenSearchMultiSourceSearch` and `StructuredAgentModel` as
before, then assign the `MultiSourceAgenticWorkflow` directly. Remove
`RetrievalService`, `FastRAGWorkflow`, `route_request`, `RouterService`,
`CorpusInfoService`, and chat-only `DeepResearchWorkflow` construction.

In demo mode, remove the `router` parameter and `DemoRouter`; assign the in-memory
agent directly. Keep jobs and readiness because their explicit endpoints still
consume them.

- [ ] **Step 4: Remove dead chat routing/workflow modules and their tests**

Delete the three legacy graph modules only after `rg` confirms no application
imports remain:

```bash
rg -n "graphs\.(router|general_intents|fast_rag)|RouteDecision|FastRAGWorkflow" app
```

Expected before deletion: no active imports outside the files being deleted.
Remove route-only tests and Fast-workflow tracing assertions; keep tracing tests for
multi-source and Deep Research code that remains independently active.

- [ ] **Step 5: Run dependency, graph, content, research, and tracing tests**

```bash
python -m pytest \
  tests/mail_rag/test_multi_source_graph.py \
  tests/mail_rag/test_multi_source_demo_api.py \
  tests/mail_rag/test_integration_fixes.py \
  tests/mail_rag/test_mail_content_api.py \
  tests/mail_rag/test_research_api.py \
  tests/mail_rag/test_tracing.py -q
```

Expected: all selected tests pass and no deleted module is imported.

- [ ] **Step 6: Commit**

```bash
git add app/api/dependencies.py app/graphs tests/mail_rag
git commit -m "refactor(rag): remove legacy chat workflows"
```

---

### Task 4: Route-Free Demo and Backend Sweep

**Files:**
- Modify: `scripts/run_multi_source_demo.py`
- Modify: `tests/mail_rag/test_multi_source_demo_api.py`
- Modify: any backend test fixture still constructing the old public contract.

**Interfaces:**
- Consumes: route-free `ChatRequest` and `ChatResponse`.
- Produces: CLI and API demo requests with no `response_mode`.

- [ ] **Step 1: Add a failing demo request-shape assertion**

Capture the CLI/demo request and assert:

```python
assert request.model_dump() == {
    "user_id": "kim",
    "message": "이번 주 일정 알려줘",
    "conversation_id": None,
    "filters": {"teams": [], "weeks": [], "mail_type": None},
}
```

- [ ] **Step 2: Run the demo tests and confirm RED**

```bash
python -m pytest tests/mail_rag/test_multi_source_demo_api.py -q
```

Expected: failure because demo requests still set `response_mode="fast"`.

- [ ] **Step 3: Remove legacy mode values from demo code and fixtures**

Construct requests with only owner, message, optional conversation, and filters.
Update response assertions to use `agent_trace`, `execution`, `quality`, and
references.

- [ ] **Step 4: Run backend legacy-term sweep and focused tests**

```bash
rg -n "response_mode|services\.router|services\.fast|services\.deep|RoutingDiagnostics" app scripts tests/mail_rag
python -m pytest tests/mail_rag/test_multi_source_demo_api.py tests/mail_rag/test_chat_api.py -q
```

Expected: `rg` reports no active chat contract or execution references; retained
historical prose, if any, is reviewed manually. Tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_multi_source_demo.py tests/mail_rag
git commit -m "refactor(demo): use single chat entry"
```

---

### Task 5: Route-Free Frontend API Contract

**Files:**
- Modify: `frontend/src/services/ragApiService.test.ts`
- Modify: `frontend/src/rag/types.ts`
- Modify: `frontend/src/services/ragApiService.ts`

**Interfaces:**
- Produces: `ChatPayload` without `response_mode`.
- Produces: `ChatResponse` without `mode`/`routing` and with optional
  `agent_trace`.

- [ ] **Step 1: Write failing frontend service tests**

Use a valid response shaped like:

```typescript
const response = {
  conversation_id: 'conversation-1',
  answer: '일정 답변 [S1]',
  references: [],
  quality: { citation_valid: true, limited_answer: false, retrieval_mode: 'hybrid' },
  disclosures: [],
  trace_id: 'trace-1',
  agent_trace: {
    tool_calls: ['search_calendar'],
    judge_decisions: ['sufficient'],
    iteration_count: 1,
  },
  execution: {
    status: 'succeeded',
    retryable: false,
    search_count: 1,
    evidence_count: 1,
    duration_ms: 12,
    include_in_llm_history: true,
    node_runs: [],
  },
}
```

Assert that this response is accepted without route fields and that the recorded
request body contains no `response_mode`. Assert that a response containing only
the old route envelope is rejected.

- [ ] **Step 2: Run the service tests and confirm RED**

```bash
cd frontend && npm test -- src/services/ragApiService.test.ts
```

Expected: the new response is rejected because the validator still requires
`mode` and `routing`.

- [ ] **Step 3: Implement TypeScript types and runtime validation**

Remove `ExecutionMode` and `RoutingDiagnostics`. Define:

```typescript
export interface AgentTrace {
  tool_calls: string[]
  judge_decisions: string[]
  iteration_count: number
}

export interface ChatPayload {
  user_id: string
  message: string
  conversation_id?: string
  filters: RetrievalFilters
}

export interface ChatResponse {
  conversation_id: string
  answer?: string | null
  references: ChatReference[]
  quality?: QualityStatus | null
  disclosures: string[]
  trace_id: string
  agent_trace?: AgentTrace | null
  execution?: ExecutionMetadata | null
}
```

Replace `isRouting` with an `isAgentTrace` validator that enforces arrays of
strings, `iteration_count` between 0 and 4, at most eight tool calls, and at most
eight judge decisions. Update `isChatResponse` accordingly.

- [ ] **Step 4: Run the service tests and confirm GREEN**

Run the command from Step 2. Expected: all service tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/rag/types.ts frontend/src/services/ragApiService.ts \
  frontend/src/services/ragApiService.test.ts
git commit -m "refactor(frontend)!: remove chat routing fields" \
  -m "BREAKING CHANGE: chat requests and responses use the single multi-source contract."
```

---

### Task 6: Single-Agent Frontend Controls and Diagnostics

**Files:**
- Modify: `frontend/src/rag/__tests__/RequestPanel.test.tsx`
- Modify: `frontend/src/rag/__tests__/RagLabApp.test.tsx`
- Modify: `frontend/src/rag/__tests__/InspectorPanel.test.tsx`
- Modify: `frontend/src/rag/RequestPanel.tsx`
- Modify: `frontend/src/rag/RagLabApp.tsx`
- Modify: `frontend/src/rag/ConversationPanel.tsx`
- Modify: `frontend/src/rag/InspectorPanel.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: route-free types from Task 5.
- Produces: one chat request path and actual agent/execution diagnostics.

- [ ] **Step 1: Write failing UI tests for the single-agent experience**

Assert the request panel has no execution-system field or Auto/Fast/Deep buttons,
and settings contain no mode:

```typescript
expect(screen.queryByRole('group', { name: '실행 시스템' })).not.toBeInTheDocument()
expect(onSettingsChange).toHaveBeenLastCalledWith(
  expect.objectContaining({ userId: 'kim', filters: expect.any(Object) }),
)
expect(onSettingsChange.mock.calls.at(-1)?.[0]).not.toHaveProperty('mode')
```

Assert the conversation and inspector show actual search count, evidence count,
tool calls, judge decisions, and execution status, with no `Router`, requested
mode, route, or executed-system text.

- [ ] **Step 2: Run the focused UI tests and confirm RED**

```bash
cd frontend && npm test -- \
  src/rag/__tests__/RequestPanel.test.tsx \
  src/rag/__tests__/RagLabApp.test.tsx \
  src/rag/__tests__/InspectorPanel.test.tsx
```

Expected: failures because the mode controls and route diagnostics still render.

- [ ] **Step 3: Remove mode selection and request serialization**

Delete mode state and mode buttons from `RequestPanel`. Change
`RagRequestSettings` to contain only owner, optional conversation, and filters.
Build chat payloads in `RagLabApp` without `response_mode`:

```typescript
const payload: ChatPayload = {
  user_id: settings.userId,
  message,
  ...(settings.conversationId ? { conversation_id: settings.conversationId } : {}),
  filters: settings.filters,
}
```

Remove chat-created research-job adaptation because chat no longer returns job
fields. Keep explicit research API client types and methods intact.

- [ ] **Step 4: Replace route UI with agent execution UI**

In `ConversationPanel`, render one execution block using `chat.execution` and
`chat.agent_trace`; render quality whenever present instead of checking
`executed_system === "fast_rag"`. Use a fixed result title such as
`Multi-source answer` for chat results.

In `InspectorPanel`, identify chat responses by `conversation_id`/`trace_id`, remove
mode and routing rows, and add rows for actual searches, evidence, tool calls,
judge decisions, and iterations. Remove unused routing CSS selectors or rename them
to agent-execution selectors.

- [ ] **Step 5: Run UI tests, lint, and build**

```bash
cd frontend && npm test -- \
  src/rag/__tests__/RequestPanel.test.tsx \
  src/rag/__tests__/RagLabApp.test.tsx \
  src/rag/__tests__/InspectorPanel.test.tsx
cd frontend && npm run lint
cd frontend && npm run build
```

Expected: tests pass, ESLint reports no errors, and TypeScript/Vite build exits 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/rag frontend/src/styles.css
git commit -m "refactor(frontend): show multi-source execution"
```

---

### Task 7: Full Regression and Architecture Verification

**Files:**
- Modify: `docs/multi_source_demo.md`

**Interfaces:**
- Validates all earlier tasks; produces no new runtime abstraction.

- [ ] **Step 1: Run the architecture sweep**

```bash
rg -n "response_mode|RoutingDiagnostics|services\.router|services\.fast|services\.deep|FastRAGWorkflow|route_request" app frontend/src scripts tests/mail_rag
```

Expected: no active chat execution, UI, demo, or test reference. Any match in
historical design documents is outside this command and intentionally preserved.

- [ ] **Step 2: Run the full backend suite**

```bash
python -m pytest tests/mail_rag -q
```

Expected: zero failures.

- [ ] **Step 3: Run the full frontend verification**

```bash
cd frontend && npm test
cd frontend && npm run lint
cd frontend && npm run build
```

Expected: zero test or lint failures and a successful production build.

- [ ] **Step 4: Run offline and configured-model smoke checks**

```bash
python scripts/run_multi_source_demo.py --scenario calendar_current_week
python scripts/evaluate_agent_intent.py
```

Expected: offline Calendar scenario succeeds. The model evaluator passes when
`OPENROUTER_API_KEY` is configured; otherwise record its explicit dependency block
without claiming that gate passed.

- [ ] **Step 5: Inspect the final diff and protected untracked files**

```bash
git diff --check
git status --short
git diff --stat 9a4db9b..HEAD
```

Expected: no whitespace errors; only intended implementation files and commits are
present; `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md` and the uncommitted evaluator work
remain unmodified unless a later explicit task owns them.

- [ ] **Step 6: Commit any final documentation-only cleanup**

```bash
git add docs/multi_source_demo.md
git commit -m "docs(rag): document single chat workflow"
```

Skip this commit when Step 1 finds no active operational documentation to update.

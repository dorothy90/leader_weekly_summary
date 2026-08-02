# RAG Routing Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the verification console default to automatic routing and display the server's authoritative requested mode, routing decision, execution path, reason, confidence, search estimate, and actual retrieval usage.

**Architecture:** Add a constrained `RoutingDiagnostics` object to every successful chat response and preserve the existing top-level mode for compatibility. Build the diagnostics once from the request and final `RouteDecision`, then render it in the existing result and inspector panels. General and clarification paths report `retrieval_mode="not_used"`; Fast and Deep remain separate systems with explicit overrides.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest 8, React 19, TypeScript 6, Vite 8, Vitest 4, Testing Library

## Global Constraints

- UI mode order is `Auto`, `Fast 강제`, `Deep 강제`; Auto is selected by default.
- Routing values are authoritative server results, never inferred from question text or top-level mode.
- `requested_mode` is `auto | fast | deep`.
- `route` is `general | fast | deep | clarify`.
- `executed_system` is `general | fast_rag | deep_research | clarification`.
- Explicit overrides retain `reason_code="explicit_mode"`.
- Router exceptions use a safe `router_error_deterministic_*` reason without exception text.
- General and clarification use `retrieval_mode="not_used"`; Fast uses `hybrid | bm25`.
- Existing response fields, Fast/Deep separation, exact request-body `user_id` filtering, BM25 disclosure, secret redaction, and Deep job tracking remain unchanged.
- Do not edit the external `.env`, virtual environment, OpenSearch indices, mappings, aliases, or stored embeddings.

---

## File Structure

- Modify `app/domain/chat.py`: define `RoutingDiagnostics`, extend retrieval mode, and require routing on `ChatResponse`.
- Modify `app/graphs/router.py`: label only exception-based deterministic fallback with `router_error_`.
- Modify `app/api/routes/chat.py`: build and attach authoritative diagnostics for every successful branch.
- Modify `tests/mail_rag/test_router.py`, `tests/mail_rag/test_domain_contracts.py`, and `tests/mail_rag/test_chat_api.py`: prove backend contract and all route mappings.
- Modify `frontend/src/rag/types.ts` and `frontend/src/services/ragApiService.ts`: model and validate the additive API contract.
- Modify `frontend/src/rag/RequestPanel.tsx`: add Auto and explicit override controls.
- Modify `frontend/src/rag/ConversationPanel.tsx`: show routing flow and hide retrieval badges when unused.
- Modify `frontend/src/rag/InspectorPanel.tsx`: show the complete routing decision.
- Modify frontend tests and `frontend/src/index.css`: verify and style the diagnostic presentation.

### Task 1: Backend Routing Contract and Safe Fallback Reason

**Files:**
- Modify: `tests/mail_rag/test_domain_contracts.py`
- Modify: `tests/mail_rag/test_router.py`
- Modify: `app/domain/chat.py`
- Modify: `app/graphs/router.py`

**Interfaces:**
- Produces: `RoutingDiagnostics(requested_mode, route, executed_system, reason_code, confidence, estimated_searches)` and `QualityStatus.retrieval_mode: Literal["hybrid", "bm25", "not_used"]`.
- Produces: exception fallback reason `router_error_<deterministic reason>`.

- [ ] **Step 1: Write failing domain and router tests**

Add to `tests/mail_rag/test_domain_contracts.py`:

```python
from app.domain.chat import QualityStatus, RoutingDiagnostics


def test_routing_diagnostics_and_not_used_retrieval_are_bounded():
    routing = RoutingDiagnostics(
        requested_mode="auto",
        route="general",
        executed_system="general",
        reason_code="deterministic_general",
        confidence=1,
        estimated_searches=0,
    )
    quality = QualityStatus(citation_valid=True, retrieval_mode="not_used")

    assert routing.route == "general"
    assert quality.retrieval_mode == "not_used"
```

Change the failure assertion in `tests/mail_rag/test_router.py`:

```python
    assert decision.reason_code == "router_error_deterministic_long_period"
    assert "router unavailable" not in decision.reason_code
```

Also strengthen the greeting test:

```python
    assert decision.route == "general"
    assert decision.reason_code == "deterministic_general"
    assert decision.estimated_searches == 0
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag/test_domain_contracts.py tests/mail_rag/test_router.py -q`

Expected: FAIL because `RoutingDiagnostics`, `not_used`, and the safe fallback prefix are absent.

- [ ] **Step 3: Implement constrained models and fallback labeling**

Add to `app/domain/chat.py`:

```python
class RoutingDiagnostics(BaseModel):
    requested_mode: Literal["auto", "fast", "deep"]
    route: Literal["fast", "deep", "clarify", "general"]
    executed_system: Literal[
        "general", "fast_rag", "deep_research", "clarification"
    ]
    reason_code: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0, le=1)
    estimated_searches: int = Field(ge=0, le=24)
```

Extend `QualityStatus.retrieval_mode` to include `not_used` and add `routing: RoutingDiagnostics` to `ChatResponse`.

Change only the exception branch in `route_request`:

```python
    except Exception:
        fallback = _deterministic_fallback(request)
        return fallback.model_copy(
            update={"reason_code": f"router_error_{fallback.reason_code}"}
        )
```

In `_apply_deterministic_policy`, the `non_mail` update also sets `estimated_searches: 0` because the general branch performs no retrieval.

- [ ] **Step 4: Verify GREEN**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag/test_domain_contracts.py tests/mail_rag/test_router.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/domain/chat.py app/graphs/router.py tests/mail_rag/test_domain_contracts.py tests/mail_rag/test_router.py
git commit -m "feat(rag): expose routing diagnostic contract"
```

### Task 2: Attach Diagnostics to Every Successful Chat Branch

**Files:**
- Modify: `tests/mail_rag/test_chat_api.py`
- Modify: `app/api/routes/chat.py`

**Interfaces:**
- Consumes: `RoutingDiagnostics` from Task 1 and the existing `ChatRequest`/`RouteDecision`.
- Produces: `_routing_diagnostics(payload, decision, executed_system) -> RoutingDiagnostics`; every `ChatResponse` contains its result.

- [ ] **Step 1: Write failing API mapping tests**

Make `FakeRouter` accept a complete decision, then add parameterized response assertions:

```python
@pytest.mark.parametrize(
    ("requested_mode", "route", "executed_system", "expected_status"),
    [
        ("auto", "general", "general", 200),
        ("fast", "fast", "fast_rag", 200),
        ("deep", "deep", "deep_research", 202),
        ("auto", "clarify", "clarification", 200),
    ],
)
def test_chat_returns_authoritative_routing_diagnostics(
    requested_mode, route, executed_system, expected_status
):
    router = FakeRouter(route)
    response = client(router=router, deep=FakeDeep()).post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "hi" if route == "general" else "질문",
            "response_mode": requested_mode,
        },
    )

    assert response.status_code == expected_status
    assert response.json()["routing"] == {
        "requested_mode": requested_mode,
        "route": route,
        "executed_system": executed_system,
        "reason_code": "test",
        "confidence": 1.0,
        "estimated_searches": 1,
    }
    if route in {"general", "clarify"}:
        assert response.json()["quality"]["retrieval_mode"] == "not_used"
```

The clarify fake decision supplies `clarification_question="범위를 알려주세요."`.

- [ ] **Step 2: Verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag/test_chat_api.py -q`

Expected: FAIL because successful responses omit required `routing` and non-retrieval branches still report `hybrid`.

- [ ] **Step 3: Add one mapper and use it in all branches**

Add to `app/api/routes/chat.py`:

```python
def _routing_diagnostics(payload, decision, executed_system):
    return RoutingDiagnostics(
        requested_mode=payload.response_mode,
        route=decision.route,
        executed_system=executed_system,
        reason_code=sanitize_text(decision.reason_code),
        confidence=decision.confidence,
        estimated_searches=decision.estimated_searches,
    )
```

Attach the mapper result to all four `ChatResponse` constructors using their exact executed-system value. For general and clarify, replace quality with a copy using `retrieval_mode="not_used"`; clarification constructs `QualityStatus(citation_valid=True, limited_answer=False, retrieval_mode="not_used")`.

- [ ] **Step 4: Verify GREEN and backend regressions**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag/test_chat_api.py tests/mail_rag/test_fast_rag.py tests/mail_rag/test_deep_research.py tests/mail_rag/test_research_api.py -q`

Expected: all tests PASS; ACL and BM25 disclosure tests remain green.

- [ ] **Step 5: Commit**

```bash
git add app/api/routes/chat.py tests/mail_rag/test_chat_api.py
git commit -m "feat(api): return authoritative routing diagnostics"
```

### Task 3: Frontend Contract and Auto-First Request Controls

**Files:**
- Modify: `frontend/src/rag/types.ts`
- Modify: `frontend/src/services/ragApiService.ts`
- Modify: `frontend/src/services/ragApiService.test.ts`
- Modify: `frontend/src/rag/RequestPanel.tsx`
- Modify: `frontend/src/rag/__tests__/RequestPanel.test.tsx`

**Interfaces:**
- Produces: `ExecutionMode = 'auto' | 'fast' | 'deep'`, `RoutingDiagnostics`, and required `ChatResponse.routing`.
- The API validator accepts `not_used` and rejects a successful chat response without valid routing diagnostics.

- [ ] **Step 1: Write failing request and validation tests**

Update the RequestPanel test to submit without selecting a mode and assert:

```typescript
expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')
expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ mode: 'auto' }))
```

Add service tests proving a response with the following object is accepted and the same response without `routing` becomes `INVALID_RESPONSE`:

```typescript
routing: {
  requested_mode: 'auto',
  route: 'general',
  executed_system: 'general',
  reason_code: 'deterministic_general',
  confidence: 1,
  estimated_searches: 0,
},
quality: { citation_valid: true, limited_answer: false, retrieval_mode: 'not_used' },
```

- [ ] **Step 2: Verify RED**

Run: `npm test -- --run src/rag/__tests__/RequestPanel.test.tsx src/services/ragApiService.test.ts` from `frontend`.

Expected: FAIL because Auto, the routing type/validator, and `not_used` are absent.

- [ ] **Step 3: Implement types, validators, and controls**

Add to `types.ts`:

```typescript
export type ExecutionMode = 'auto' | 'fast' | 'deep'
export interface RoutingDiagnostics {
  requested_mode: ExecutionMode
  route: 'general' | 'fast' | 'deep' | 'clarify'
  executed_system: 'general' | 'fast_rag' | 'deep_research' | 'clarification'
  reason_code: string
  confidence: number
  estimated_searches: number
}
```

Require `routing: RoutingDiagnostics` on `ChatResponse` and allow `not_used` on `QualityStatus.retrieval_mode`. Add bounded routing validation to `ragApiService.ts` and require it in `isChatResponse`.

In `RequestPanel.tsx`, initialize with `useState<ExecutionMode>('auto')`, render `['auto', 'fast', 'deep']`, and label them `Auto`, `Fast 강제`, and `Deep 강제`.

- [ ] **Step 4: Verify GREEN**

Run: `npm test -- --run src/rag/__tests__/RequestPanel.test.tsx src/services/ragApiService.test.ts` from `frontend`.

Expected: all focused tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/rag/types.ts frontend/src/services/ragApiService.ts frontend/src/services/ragApiService.test.ts frontend/src/rag/RequestPanel.tsx frontend/src/rag/__tests__/RequestPanel.test.tsx
git commit -m "feat(ui): default RAG console to Auto routing"
```

### Task 4: Render Routing Flow and Context-Correct Quality

**Files:**
- Modify: `frontend/src/rag/ConversationPanel.tsx`
- Modify: `frontend/src/rag/InspectorPanel.tsx`
- Modify: `frontend/src/rag/__tests__/RagLabApp.test.tsx`
- Modify: `frontend/src/rag/__tests__/InspectorPanel.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: required `ChatResponse.routing` and `retrieval_mode="not_used"` from Task 3.
- Produces: visible requested-mode -> route -> execution flow, full inspector diagnostics, and quality badges only for `fast_rag` execution.

- [ ] **Step 1: Write failing presentation tests**

Add a general-response UI test with `routing.route="general"`, `executed_system="general"`, and `retrieval_mode="not_used"`. Assert:

```typescript
expect(screen.getByText('요청 Auto')).toBeInTheDocument()
expect(screen.getByText('Router general')).toBeInTheDocument()
expect(screen.getByText('실행 general')).toBeInTheDocument()
expect(screen.getByText('deterministic_general')).toBeInTheDocument()
expect(screen.queryByText('인용 유효')).not.toBeInTheDocument()
expect(screen.queryByText('not_used')).not.toBeInTheDocument()
```

Extend InspectorPanel assertions to require `auto`, `general`, `deterministic_general`, `100%`, `0`, and `not_used` from the supplied routing/quality object.

- [ ] **Step 2: Verify RED**

Run: `npm test -- --run src/rag/__tests__/RagLabApp.test.tsx src/rag/__tests__/InspectorPanel.test.tsx` from `frontend`.

Expected: FAIL because routing flow and diagnostics are not rendered and general still shows quality badges.

- [ ] **Step 3: Render routing and condition quality badges**

In `ConversationPanel`, render:

```tsx
{chat ? (
  <div className="routing-flow" aria-label="라우팅 결과">
    <span>요청 {modeLabel(chat.routing.requested_mode)}</span>
    <span>Router {chat.routing.route}</span>
    <span>실행 {chat.routing.executed_system}</span>
    <small>{chat.routing.reason_code} · 신뢰도 {Math.round(chat.routing.confidence * 100)}% · 예상 검색 {chat.routing.estimated_searches}회</small>
  </div>
) : null}
```

Render the existing quality row only when `chat.routing.executed_system === 'fast_rag'` and quality exists.

In `InspectorPanel`, add routing fields and display retrieval from quality or `서버 미제공`. Add compact `.routing-flow` styles consistent with existing diagnostic chips; do not restructure the page.

- [ ] **Step 4: Verify GREEN and complete frontend checks**

Run from `frontend`:

```bash
npm test
npm run lint
npm run build
```

Expected: all tests PASS, ESLint exits 0, and TypeScript/Vite build exits 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/rag/ConversationPanel.tsx frontend/src/rag/InspectorPanel.tsx frontend/src/rag/__tests__/RagLabApp.test.tsx frontend/src/rag/__tests__/InspectorPanel.test.tsx frontend/src/index.css
git commit -m "feat(ui): display authoritative RAG routing"
```

### Task 5: Full Verification and Live UI Handoff

**Files:**
- Verify only; no repository changes expected.

**Interfaces:**
- Consumes: completed backend and frontend routing diagnostics.
- Produces: a running Cloudflare-backed backend on 8000 and existing RAG UI on 5180 for user testing.

- [ ] **Step 1: Run all automated checks**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag -q
cd frontend && npm test && npm run lint && npm run build
```

Expected: all backend/frontend tests PASS, lint exits 0, and build exits 0.

- [ ] **Step 2: Restart only the backend**

Stop the current backend process on 8000, then start it with:

```bash
PYTHONPATH=/tmp/weekly-mail-rag-runtime-20260802 \
/Users/daehwankim/Documents/weekly_mail_agent/.venv/bin/python -c '
import uvicorn
from app.config.settings import Settings
from app.api.dependencies import build_container
from app.api.main import create_app
settings = Settings(_env_file="/Users/daehwankim/Documents/weekly_mail_agent/.env")
uvicorn.run(create_app(build_container(settings)), host="127.0.0.1", port=8000)
'
```

Expected: backend listens on 8000 without logging credentials. Keep the existing Vite UI on 5180 and unrelated Route Master on 5173 running.

- [ ] **Step 3: Verify three routing cases through the UI proxy**

Post to `http://127.0.0.1:5180/api/v1/chat`:

- `{"user_id":"kim","message":"hi","response_mode":"auto"}` -> HTTP 200, route `general`, execution `general`, retrieval `not_used`
- `{"user_id":"kim","message":"최근 수율 이슈를 알려줘","response_mode":"auto"}` -> HTTP 200, route `fast`, execution `fast_rag`
- `{"user_id":"kim","message":"최근 4주 추세 보고서를 작성해줘","response_mode":"auto","filters":{"teams":[],"weeks":["2026-28","2026-29","2026-30","2026-31"]}}` -> HTTP 202, route `deep`, execution `deep_research`

Expected: all response payloads include bounded routing diagnostics and no credentials. Leave both testing servers running.

- [ ] **Step 4: Confirm repository and server state**

Run: `git diff --check && git status --short && lsof -nP -iTCP:8000 -sTCP:LISTEN && lsof -nP -iTCP:5180 -sTCP:LISTEN`

Expected: only the pre-existing untracked `docs/deep_agent_mail_chatbot_review.md` remains, backend and UI are listening, and the live UI is available at `http://127.0.0.1:5180/rag`.

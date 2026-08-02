# Intent-First RAG Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make structured Auto routing authoritative, removing exact General sentence allowlisting while retaining explicit modes, Deep complexity overrides, and safe router-error fallback.

**Architecture:** `route_request` accepts a valid structured route after server-owned Deep checks. Deterministic fallback becomes a separate degraded path: clear retrieval filters or paired mail-search signals select Fast, otherwise General. API diagnostics continue to expose the actual decision and retrieval usage.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, LangGraph, pytest 8

## Global Constraints

- Fast and Deep remain distinct execution systems.
- `response_mode=fast|deep` remains authoritative.
- Four weeks, three teams, and research-output indicators retain deterministic Deep routing.
- A valid structured `general|fast|deep|clarify` decision is not replaced by a sentence allowlist.
- Router-error fallback uses Fast only for filters or paired mail-domain and retrieval-action signals; all other input falls back to General.
- Request-body `user_id`, owner filtering, ACL, BM25 fallback disclosure, and secret redaction remain unchanged.
- General and clarification use `retrieval_mode=not_used`; General uses zero estimated searches.
- Do not edit the external `.env`, OpenSearch data, aliases, mappings, or stored embeddings.

---

## File Structure

- Modify `app/graphs/router.py`: separate valid-decision policy from router-error fallback and add bounded mail-retrieval signal detection.
- Modify `app/llm/prompts.py`: describe all four router outcomes and conversational General scope.
- Modify `tests/mail_rag/test_router.py`: replace allowlist-shaped expectations with category and fallback tests.
- Modify `tests/mail_rag/test_chat_api.py`: prove the API executes General without retrieval for an unseen personal statement.

### Task 1: Make Valid Structured Routing Authoritative

**Files:**
- Modify: `tests/mail_rag/test_router.py`
- Modify: `app/graphs/router.py`

**Interfaces:**
- Consumes: `ChatRequest`, `RouteDecision`, and `llm.complete_model(...)`.
- Produces: `_has_mail_retrieval_intent(request: ChatRequest) -> bool`, `_deterministic_fallback(request: ChatRequest) -> RouteDecision`, and `route_request(request, llm) -> RouteDecision` with intent-first semantics.

- [ ] **Step 1: Write failing category tests**

Replace tests that require every model-selected General route to become Fast. Add:

```python
@pytest.mark.parametrize(
    "message",
    [
        "내 이름은 대환",
        "고마워",
        "무슨 일을 할 수 있어?",
        "Fast와 Deep의 차이가 뭐야?",
        "오늘 기분 어때?",
    ],
)
def test_valid_model_general_decision_is_authoritative_for_unseen_conversation(message):
    llm = RecordingLLM(
        RouteDecision(
            route="general",
            reason_code="conversation",
            confidence=0.99,
            estimated_searches=3,
        )
    )

    decision = asyncio.run(
        route_request(ChatRequest(user_id="kim", message=message), llm)
    )

    assert decision.route == "general"
    assert decision.reason_code == "model_general"
    assert decision.estimated_searches == 0
```

Also prove the server does not second-guess a valid structured decision:

```python
def test_valid_model_general_is_not_reclassified_by_sentence_rules():
    llm = RecordingLLM(
        RouteDecision(
            route="general",
            reason_code="model_general",
            confidence=0.9,
            estimated_searches=1,
        )
    )

    decision = asyncio.run(
        route_request(
            ChatRequest(user_id="kim", message="지난주 수율 이슈 알려줘"), llm
        )
    )

    assert decision.route == "general"
    assert decision.reason_code == "model_general"
    assert decision.estimated_searches == 0
```

Add router-error fallback tests:

```python
@pytest.mark.parametrize("message", ["내 이름은 대환", "고마워", "오늘 기분 어때?"])
def test_router_failure_defaults_unseen_non_mail_input_to_general(message):
    decision = asyncio.run(
        route_request(
            ChatRequest(user_id="kim", message=message),
            RecordingLLM(error=TimeoutError("router unavailable")),
        )
    )

    assert decision.route == "general"
    assert decision.reason_code == "router_error_deterministic_general"
    assert decision.estimated_searches == 0


@pytest.mark.parametrize(
    "message",
    [
        "지난주 수율 이슈 알려줘",
        "김대환이 보낸 메일 찾아줘",
        "최근 이메일을 요약해줘",
    ],
)
def test_router_failure_uses_fast_for_clear_mail_retrieval(message):
    decision = asyncio.run(
        route_request(
            ChatRequest(user_id="kim", message=message),
            RecordingLLM(error=TimeoutError("router unavailable")),
        )
    )

    assert decision.route == "fast"
    assert decision.reason_code == "router_error_deterministic_fast"
    assert decision.estimated_searches == 1


def test_router_failure_uses_fast_when_retrieval_filters_are_present():
    decision = asyncio.run(
        route_request(
            ChatRequest(
                user_id="kim",
                message="확인해줘",
                filters={"weeks": ["2026-31"]},
            ),
            RecordingLLM(error=TimeoutError("router unavailable")),
        )
    )

    assert decision.route == "fast"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag/test_router.py -q
```

Expected: failures show unseen General is changed to `fast`, and router-error fallback defaults non-mail input to `fast`.

- [ ] **Step 3: Implement intent-first policy**

In `app/graphs/router.py`, add bounded fallback signals:

```python
_MAIL_OBJECTS = (
    "메일",
    "이메일",
    "발신자",
    "수신자",
    "보낸 사람",
    "받은 사람",
    "수율",
    "주간 보고",
    "이슈",
    "현황",
)
_RETRIEVAL_ACTIONS = (
    "찾아",
    "검색",
    "알려",
    "보여",
    "요약",
    "비교",
    "분석",
    "최근",
    "지난주",
    "이번주",
)


def _has_mail_retrieval_intent(request: ChatRequest) -> bool:
    if request.filters.teams or request.filters.weeks:
        return True
    text = request.message.casefold()
    return any(term in text for term in _MAIL_OBJECTS) and any(
        term in text for term in _RETRIEVAL_ACTIONS
    )
```

Change `_deterministic_fallback` after existing Deep checks:

```python
    if _has_mail_retrieval_intent(request):
        return RouteDecision(
            route="fast",
            reason_code="deterministic_fast",
            confidence=1,
            estimated_searches=1,
            requested_output=requested_output,
        )
    return RouteDecision(
        route="general",
        reason_code="deterministic_general",
        confidence=1,
        estimated_searches=0,
        requested_output=requested_output,
    )
```

Replace `_apply_deterministic_policy` sentence rules with:

```python
def _apply_deterministic_policy(
    request: ChatRequest, decision: RouteDecision
) -> RouteDecision:
    deterministic = _deterministic_fallback(request)
    if deterministic.route == "deep":
        return deterministic
    if decision.route == "general":
        return decision.model_copy(update={"estimated_searches": 0})
    return decision
```

Keep the existing exception prefixing logic. Remove the import of
`is_identity_question` from `app.graphs.general_intents`; identity-specific logic
remains only in the deterministic General response generator.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag/test_router.py -q
```

Expected: all router tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/graphs/router.py tests/mail_rag/test_router.py
git commit -m "fix(rag): make structured routing authoritative"
```

### Task 2: Strengthen Router Semantics at the API Boundary

**Files:**
- Modify: `app/llm/prompts.py`
- Modify: `tests/mail_rag/test_router.py`
- Modify: `tests/mail_rag/test_chat_api.py`

**Interfaces:**
- Consumes: `ROUTER_SYSTEM`, `route_request`, `ServiceContainer`, and `/v1/chat`.
- Produces: router instructions covering General/Fast/Deep/clarify and an API regression proving unseen conversation executes General without retrieval.

- [ ] **Step 1: Write failing prompt and API tests**

Add to `tests/mail_rag/test_router.py`:

```python
def test_router_prompt_defines_conversation_and_retrieval_boundaries():
    llm = RecordingLLM(
        RouteDecision(
            route="general",
            reason_code="conversation",
            confidence=1,
            estimated_searches=0,
        )
    )

    asyncio.run(
        route_request(ChatRequest(user_id="kim", message="내 이름은 대환"), llm)
    )

    system = llm.inputs[0][0].casefold()
    assert "personal statements" in system
    assert "mail retrieval" in system
    assert "deep" in system
    assert "clarify" in system
```

Add to `tests/mail_rag/test_chat_api.py`:

```python
def test_unseen_personal_statement_executes_general_without_retrieval():
    class GeneralRouter:
        async def route(self, request):
            return RouteDecision(
                route="general",
                reason_code="model_general",
                confidence=0.99,
                estimated_searches=0,
            )

    fast = FakeFast(
        FastRAGResult(
            answer="이름을 기억할게요.",
            evidence=[],
            quality=QualityStatus(
                citation_valid=True,
                retrieval_mode="not_used",
            ),
        )
    )
    response = client(router=GeneralRouter(), fast=fast).post(
        "/v1/chat",
        json={"user_id": "kim", "message": "내 이름은 대환"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["routing"]["route"] == "general"
    assert body["routing"]["executed_system"] == "general"
    assert body["quality"]["retrieval_mode"] == "not_used"
    assert fast.calls[0][1:] == (None, None)
```

Import `RouteDecision` at module scope in `test_chat_api.py`.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  tests/mail_rag/test_router.py::test_router_prompt_defines_conversation_and_retrieval_boundaries \
  tests/mail_rag/test_chat_api.py::test_unseen_personal_statement_executes_general_without_retrieval -q
```

Expected: prompt assertion fails because the current prompt limits General to greetings and product usage. API test may already pass and records the existing API behavior while the prompt test supplies the RED gate for this task.

- [ ] **Step 3: Update structured router instructions**

Replace `ROUTER_SYSTEM` in `app/llm/prompts.py` with:

```python
ROUTER_SYSTEM = (
    "Return one structured route. Use general for conversation, greetings, gratitude, "
    "personal statements, identity, and product usage that need no mail evidence. Use "
    "fast for a bounded mail retrieval question. Use deep for multi-step research, "
    "multi-period or multi-team synthesis, reports, presentations, and trend or root-cause "
    "analysis. Use clarify only when information required to choose or execute a route is "
    "missing. Fast and Deep are separate execution modes."
)
```

- [ ] **Step 4: Run backend regression tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag -q
```

Expected: all backend tests pass, including ACL, BM25 disclosure, Fast, and Deep tests.

- [ ] **Step 5: Commit**

```bash
git add app/llm/prompts.py tests/mail_rag/test_router.py tests/mail_rag/test_chat_api.py
git commit -m "test(rag): cover intent-first API routing"
```

### Task 3: Full Verification and Live Handoff

**Files:**
- No production file changes expected.

**Interfaces:**
- Consumes: UI proxy at `http://127.0.0.1:5180/api`, backend at port `8000`, and external environment file `/Users/daehwankim/Documents/weekly_mail_agent/.env`.
- Produces: verified live General/Fast/Deep routing while leaving the RAG UI and backend running.

- [ ] **Step 1: Run full automated verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag -q
cd frontend
npm test
npm run lint
npm run build
```

Expected: all commands exit zero.

- [ ] **Step 2: Restart only the backend**

Resolve the exact listener on port `8000`, stop it gracefully, then run:

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

Do not stop Route Master on `5173` or the RAG UI on `5180`.

- [ ] **Step 3: Verify live route categories through the UI proxy**

General:

```bash
curl -sS --max-time 90 -X POST http://127.0.0.1:5180/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"kim","message":"내 이름은 대환","response_mode":"auto"}'
```

Expected: `route=general`, `executed_system=general`, `retrieval_mode=not_used`.

Fast:

```bash
curl -sS --max-time 90 -X POST http://127.0.0.1:5180/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"kim","message":"지난주 수율 메일 찾아줘","response_mode":"auto"}'
```

Expected: `route=fast`, `executed_system=fast_rag`.

Deep:

```bash
curl -sS --max-time 90 -X POST http://127.0.0.1:5180/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"kim","message":"최근 4주 추세 보고서를 작성해줘","response_mode":"auto","filters":{"teams":[],"weeks":["2026-28","2026-29","2026-30","2026-31"]}}'
```

Expected: HTTP 202, `route=deep`, `executed_system=deep_research`.

- [ ] **Step 4: Inspect final state**

Run:

```bash
git diff --check
git status --short
lsof -nP -iTCP:5173 -sTCP:LISTEN
lsof -nP -iTCP:5180 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Expected: only the pre-existing untracked `docs/deep_agent_mail_chatbot_review.md` remains; all three listeners remain active.

# Single-Source Conversation Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make persisted public messages the sole chat-history source and use one resolved standalone question throughout every Fast RAG stage.

**Architecture:** `ConversationMemory.messages` supplies all model conversation context; `turns` remains a diagnostics and evidence envelope only. Every user request and every non-null sanitized public answer enters the message ledger. Fast and Deep share bounded contextualization with a deterministic fallback, and Fast generation consumes the resulting `standalone_question`.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, LangGraph, LangChain messages, pytest

## Global Constraints

- Preserve exact `user_id` and `conversation_id` owner scoping.
- Sanitize persisted content and model inputs with `sanitize_text`.
- Keep cited evidence reusable only from successful owner-scoped turns.
- Preserve the existing Mongo document and HTTP response schemas.
- Retrieval filters remain request-scoped; do not add implicit inheritance.
- Preserve unrelated pre-existing working-tree changes; do not commit dirty code files wholesale.

---

### Task 1: Make messages the canonical conversation ledger

**Files:**
- Modify: `app/graphs/conversation.py:37-76`
- Modify: `app/api/routes/chat.py:203-259`
- Test: `tests/mail_rag/test_conversation_graph.py`
- Test: `tests/mail_rag/test_chat_api.py`

**Interfaces:**
- Consumes: `ConversationMemory.messages: list[dict[str, str]]`
- Produces: `build_conversation_state(request, conversation) -> ConversationState` using only `messages`
- Produces: `_save_turn(...)` that always appends the safe user message and appends the assistant message when `answer` is non-null

- [ ] **Step 1: Write failing canonical-history tests**

Replace the turn-status history test with a conflict test proving `messages` wins:

```python
def test_conversation_state_uses_messages_as_the_canonical_history():
    limited = TurnRecord(
        user_content="장례식장 정보 알려줘",
        assistant_content="일반 선택 기준을 안내합니다.",
        route="fast",
        executed_system="fast_rag",
        execution=ExecutionMetadata(
            status="limited",
            failure_stage="retrieval",
            error_code="NO_EVIDENCE",
            include_in_llm_history=True,
        ),
    )
    state = build_conversation_state(
        ChatRequest(user_id="kim", message="강남역에서 제일 가까운 데는?"),
        SimpleNamespace(
            messages=[
                {"role": "user", "content": limited.user_content},
                {"role": "assistant", "content": limited.assistant_content},
            ],
            turns=[limited],
        ),
    )
    assert message_dicts(state) == [
        {"role": "user", "content": "장례식장 정보 알려줘"},
        {"role": "assistant", "content": "일반 선택 기준을 안내합니다."},
        {"role": "user", "content": "강남역에서 제일 가까운 데는?"},
    ]
```

Update the failed-turn API assertion so its accepted user message remains:

```python
assert memory.messages == [{"role": "user", "content": "검색해줘"}]
assert memory.turns[-1].assistant_content is None
```

Extend the no-evidence fallback API test to load the stored conversation and assert the public pair is present even though execution remains limited.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/mail_rag/test_conversation_graph.py::test_conversation_state_uses_messages_as_the_canonical_history \
  tests/mail_rag/test_chat_api.py::test_failed_turn_is_diagnosable_but_excluded_from_llm_history \
  tests/mail_rag/test_chat_api.py::test_auto_fast_no_evidence_uses_labelled_general_fallback
```

Expected: the state omits the limited fallback and failed-turn `memory.messages` is empty.

- [ ] **Step 3: Implement canonical message loading and saving**

In `build_conversation_state`, replace turn-derived history with:

```python
stored = getattr(conversation, "messages", None) or []
history = [message for item in stored if (message := _safe_message(item))]
```

In `_save_turn`, replace the execution-gated append with:

```python
prior.append({"role": "user", "content": safe_user})
if safe_answer:
    prior.append({"role": "assistant", "content": safe_answer})
```

Keep `TurnRecord`, execution metadata, evidence, revision handling, sanitation, and the 20-message bound unchanged.

- [ ] **Step 4: Run focused tests to verify they pass**

Run the command from Step 2. Expected: all selected tests pass.

---

### Task 2: Use one bounded resolved question through Fast RAG

**Files:**
- Modify: `app/graphs/conversation.py:117-137`
- Modify: `app/graphs/fast_rag.py:248-265,403-420`
- Test: `tests/mail_rag/test_conversation_graph.py`
- Test: `tests/mail_rag/test_fast_rag.py`

**Interfaces:**
- Produces: `deterministic_contextualized_request(request, conversation) -> ChatRequest`
- Updates: `contextualize_request(...) -> ChatRequest` to return the deterministic result if the model fails or returns empty text
- Consumes: `FastState["standalone_question"]` in `_generate`

- [ ] **Step 1: Write failing fallback and generation tests**

Add a deterministic fallback test:

```python
def test_contextualization_failure_keeps_the_previous_user_subject():
    class BrokenLLM:
        async def complete_messages(self, system, messages):
            raise TimeoutError("unavailable")

    request = asyncio.run(
        contextualize_request(
            BrokenLLM(),
            ChatRequest(user_id="kim", message="제일 가까운 데는?"),
            SimpleNamespace(messages=[
                {"role": "user", "content": "장례식장 정보 알려줘"},
                {"role": "assistant", "content": "선택 기준을 안내합니다."},
            ]),
        )
    )
    assert "장례식장 정보 알려줘" in request.message
    assert "제일 가까운 데는?" in request.message
```

Add a Fast generation test:

```python
def test_fast_generation_uses_the_standalone_question():
    llm = ScriptedLLM(tasks=[], texts=["답변 [S1]"])
    workflow = FastRAGWorkflow(None, llm)

    result = asyncio.run(
        workflow._generate(
            {
                "request": ChatRequest(
                    user_id="kim", message="그중 제일 가까운 데는?"
                ),
                "standalone_question": "강남역에서 가장 가까운 장례식장은?",
                "evidence": [_evidence("mail-1")],
                "sufficient": True,
                "missing_information": [],
            }
        )
    )

    prompt = llm.text_calls[-1][1]
    assert "Question: 강남역에서 가장 가까운 장례식장은?" in prompt
    assert "Question: 그중 제일 가까운 데는?" not in prompt
    assert result == {"answer": "답변 [S1]"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/mail_rag/test_conversation_graph.py::test_contextualization_failure_keeps_the_previous_user_subject \
  tests/mail_rag/test_fast_rag.py::test_fast_generation_uses_the_standalone_question
```

Expected: contextualization raises `TimeoutError` and generation captures the raw follow-up.

- [ ] **Step 3: Implement deterministic contextualization fallback**

Add a bounded helper in `app/graphs/conversation.py`:

```python
def deterministic_contextualized_request(
    request: ChatRequest, conversation: object | None
) -> ChatRequest:
    prior_user = next(
        (
            sanitize_text(item.get("content", ""))
            for item in reversed(getattr(conversation, "messages", None) or [])
            if item.get("role") == "user"
        ),
        "",
    )
    if not prior_user:
        return request
    combined = f"{prior_user}\n후속 요청: {sanitize_text(request.message)}"
    return request.model_copy(update={"message": combined[-4000:]})
```

Build messages once in `contextualize_request`. If there is no history, return the original request. Wrap the model call in `try/except Exception`; on an exception or empty sanitized output, return `deterministic_contextualized_request(request, conversation)`.

- [ ] **Step 4: Use the standalone question in final generation**

Change the Fast generation prompt to:

```python
f"Question: {sanitize_text(state['standalone_question'])}\n"
```

Keep evidence, missing-information, validation, and citation behavior unchanged.

- [ ] **Step 5: Run focused tests to verify they pass**

Run the command from Step 2. Expected: both tests pass.

---

### Task 3: Verify API, persistence, routing, and frontend compatibility

**Files:**
- Verify only: `app/**`, `tests/**`, `frontend/**`

**Interfaces:**
- Consumes: the behavior delivered by Tasks 1 and 2
- Produces: evidence that backend and frontend contracts remain compatible

- [ ] **Step 1: Run the conversation and RAG backend suites**

```bash
PYTHONPATH=. uv run pytest -q \
  tests/mail_rag/test_conversation_graph.py \
  tests/mail_rag/test_conversations.py \
  tests/mail_rag/test_router.py \
  tests/mail_rag/test_fast_rag.py \
  tests/mail_rag/test_chat_api.py
```

Expected: all tests pass with no collection errors.

- [ ] **Step 2: Run the complete backend suite**

```bash
PYTHONPATH=. uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run frontend tests, lint, and production build**

```bash
npm test -- --run
npm run lint
npm run build
```

Run from `frontend/`. Expected: all commands exit 0.

- [ ] **Step 4: Review the final diff**

```bash
git diff --check
git diff -- app/graphs/conversation.py app/graphs/fast_rag.py app/api/routes/chat.py tests/mail_rag/test_conversation_graph.py tests/mail_rag/test_fast_rag.py tests/mail_rag/test_chat_api.py
```

Expected: no whitespace errors and no unrelated changes introduced by this implementation.

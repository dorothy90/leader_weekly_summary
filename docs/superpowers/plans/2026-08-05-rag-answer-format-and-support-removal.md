# RAG Answer Format and Support Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the post-answer claim-support LLM call and make every Fast RAG and Deep Research answer use `요약`, `상세설명`, and `핵심결론` sections without adding another model request.

**Architecture:** Add one deterministic Markdown contract helper shared by Fast and Deep. Generation and revision prompts request the contract, while the helper guarantees it for malformed model output and deterministic fallbacks. Keep `CitationValidator` as the only final answer gate and delete `ClaimSupportDecision` calls.

**Tech Stack:** Python 3.11+, LangGraph, Pydantic v2, pytest

## Global Constraints

- Fast and Deep answers contain exactly one `### 요약`, `### 상세설명`, and `### 핵심결론` heading in that order.
- General, Clarification, Diagnostic, and Corpus Info responses do not change.
- Existing `[S#]` citation existence, uniqueness, and owner checks remain active.
- No new LLM request, dependency, feature flag, or unrelated refactor is introduced.
- Historical `support_validation` execution metadata remains readable, but new runs do not emit it.

---

### Task 1: Shared deterministic answer contract

**Files:**
- Create: `app/llm/answer_format.py`
- Create: `tests/test_production_rag_workflows.py`

**Interfaces:**
- Produces: `RAG_ANSWER_STRUCTURE_INSTRUCTION: str`
- Produces: `ensure_rag_answer_structure(answer: str) -> str`
- Produces: `prepend_summary_notice(answer: str, notice: str) -> str`

- [ ] **Step 1: Write failing formatter tests**

```python
from app.llm.answer_format import (
    ensure_rag_answer_structure,
    prepend_summary_notice,
)


HEADINGS = ("### 요약", "### 상세설명", "### 핵심결론")


def assert_section_contract(answer: str) -> None:
    assert answer.startswith(HEADINGS[0])
    assert all(answer.count(heading) == 1 for heading in HEADINGS)
    assert [answer.index(heading) for heading in HEADINGS] == sorted(
        answer.index(heading) for heading in HEADINGS
    )


def test_formatter_preserves_valid_three_section_answer():
    answer = "### 요약\n요약\n\n### 상세설명\n근거 [S1]\n\n### 핵심결론\n결론 [S1]"
    assert ensure_rag_answer_structure(answer) == answer


def test_formatter_wraps_malformed_answer_without_losing_citation():
    answer = ensure_rag_answer_structure("확인된 사실입니다 [S1]")
    assert_section_contract(answer)
    assert "확인된 사실입니다 [S1]" in answer


def test_summary_notice_stays_inside_summary_section():
    answer = prepend_summary_notice("근거가 부족합니다.", "확인 범위가 제한적입니다.")
    assert_section_contract(answer)
    assert answer.index("확인 범위가 제한적입니다.") < answer.index("### 상세설명")
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_production_rag_workflows.py`

Expected: FAIL because `app.llm.answer_format` does not exist.

- [ ] **Step 3: Implement the shared formatter**

```python
RAG_SECTION_HEADINGS = ("### 요약", "### 상세설명", "### 핵심결론")
RAG_ANSWER_STRUCTURE_INSTRUCTION = """Return exactly these Markdown sections in this order:

### 요약
Briefly summarize the retrieved findings.

### 상세설명
Explain the findings using only the supplied evidence. Cite every factual claim with an existing [S#].

### 핵심결론
State the most important supported conclusion and any evidence limitations.

Do not add any other sections."""


def ensure_rag_answer_structure(answer: str) -> str:
    text = answer.strip()
    positions = [text.find(heading) for heading in RAG_SECTION_HEADINGS]
    valid = (
        text.startswith(RAG_SECTION_HEADINGS[0])
        and all(text.count(heading) == 1 for heading in RAG_SECTION_HEADINGS)
        and positions == sorted(positions)
    )
    if valid:
        return text
    detail = text or "확인 가능한 내용이 없습니다."
    return (
        "### 요약\n요청 결과를 아래와 같이 정리합니다.\n\n"
        f"### 상세설명\n{detail}\n\n"
        "### 핵심결론\n상세설명에 제시된 확인 범위를 참고해주세요."
    )


def prepend_summary_notice(answer: str, notice: str) -> str:
    formatted = ensure_rag_answer_structure(answer)
    safe_notice = notice.strip()
    if not safe_notice:
        return formatted
    return formatted.replace(
        "### 요약\n",
        f"### 요약\n{safe_notice}\n\n",
        1,
    )
```

- [ ] **Step 4: Run formatter tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_production_rag_workflows.py`

Expected: 3 passed.

- [ ] **Step 5: Commit the shared contract**

```bash
git add app/llm/answer_format.py tests/test_production_rag_workflows.py
git commit -m "feat(rag): add answer section contract"
```

### Task 2: Fast RAG formatting and support-check removal

**Files:**
- Modify: `app/llm/prompts.py:37-50`
- Modify: `app/graphs/fast_rag.py:49-685`
- Modify: `tests/test_production_rag_workflows.py`

**Interfaces:**
- Consumes: `RAG_ANSWER_STRUCTURE_INSTRUCTION`, `ensure_rag_answer_structure`, and `prepend_summary_notice` from Task 1.
- Produces: Fast results that retain deterministic citation validation but do not call a semantic support checker.

- [ ] **Step 1: Add a failing Fast regression test**

Create a valid owned `Evidence`, replace `workflow.graph` with an async stub returning a citation-valid state, and use an LLM stub whose `complete_model` raises `AssertionError`. Assert that `asyncio.run(workflow._invoke(...))` returns `execution.status == "succeeded"`, retains `[S1]`, and satisfies `assert_section_contract`. Before the production change, the caught support-check exception changes the result to `UNSUPPORTED_ANSWER`, so the test must fail.

- [ ] **Step 2: Run the focused Fast test**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_production_rag_workflows.py -k fast`

Expected: FAIL because the current post-graph support check rejects the result.

- [ ] **Step 3: Update prompts and Fast workflow**

```python
# app/llm/prompts.py
from app.llm.answer_format import RAG_ANSWER_STRUCTURE_INSTRUCTION

GENERATE_SYSTEM = (
    "Write a grounded answer using only the supplied evidence. Cite every factual "
    "claim with an existing [S#] target. Remove every unsupported factual claim. "
    "When evidence is incomplete, answer only the covered scope and explicitly state "
    "the limitation. Never expose credentials, raw filesystem paths, or internal "
    f"chain-of-thought.\n\n{RAG_ANSWER_STRUCTURE_INSTRUCTION}"
)
```

Apply the same structure instruction to `REVISE_SYSTEM`. In `fast_rag.py`:

- delete `ClaimSupportDecision`;
- wrap generated, revised, timeout, dependency-error, invalid, and no-evidence answers with `ensure_rag_answer_structure`;
- make `_with_limitation` call `prepend_summary_notice` so limitations remain inside `요약`;
- delete the `support_valid`, `support_failed`, and `complete_model(... ClaimSupportDecision)` block;
- preserve the existing final `CitationValidator.validate(...)` call and `CITATION_INVALID` execution result.

- [ ] **Step 4: Run Fast and formatter tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_production_rag_workflows.py -k 'fast or formatter or summary'`

Expected: PASS.

- [ ] **Step 5: Commit Fast behavior**

```bash
git add app/llm/prompts.py app/graphs/fast_rag.py tests/test_production_rag_workflows.py
git commit -m "feat(rag): format fast answers without support check"
```

### Task 3: Deep Research formatting and support-check removal

**Files:**
- Modify: `app/graphs/deep_research.py:24-626`
- Modify: `tests/test_production_rag_workflows.py`

**Interfaces:**
- Consumes: shared answer format interfaces from Task 1.
- Produces: Deep reports with the same three sections and deterministic citation validation only.

- [ ] **Step 1: Add a failing Deep regression test**

Use a Deep LLM stub whose `complete_text` returns a citation-valid but unstructured report and whose `complete_model` raises `AssertionError`. Call `asyncio.run(workflow._synthesize(state))` with one successful owned evidence branch. Assert `citation_valid is True`, `[S1]` remains present, evidence remains present, and the report satisfies `assert_section_contract`. Before the production change, the support-check exception clears the report and evidence, so the test must fail.

- [ ] **Step 2: Run the focused Deep test**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_production_rag_workflows.py -k deep`

Expected: FAIL because the current claim-support check rejects the report.

- [ ] **Step 3: Update Deep synthesis**

In `deep_research.py`:

- import `RAG_ANSWER_STRUCTURE_INSTRUCTION` and `ensure_rag_answer_structure`;
- delete `ClaimSupportDecision`;
- append the structure instruction to initial and revision synthesis instructions;
- normalize model reports before `CitationValidator` runs;
- normalize `RESEARCH_ABSTENTION` and `INVALID_REPORT` fallbacks before returning;
- delete the semantic support `complete_model` block;
- preserve the cited-evidence filtering derived from `validation.cited_ids`.

- [ ] **Step 4: Run all new workflow tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_production_rag_workflows.py`

Expected: PASS.

- [ ] **Step 5: Commit Deep behavior**

```bash
git add app/graphs/deep_research.py tests/test_production_rag_workflows.py
git commit -m "feat(rag): format deep reports without support check"
```

### Task 4: Regression verification

**Files:**
- Verify: `app/llm/answer_format.py`
- Verify: `app/llm/prompts.py`
- Verify: `app/graphs/fast_rag.py`
- Verify: `app/graphs/deep_research.py`
- Verify: `tests/test_production_rag_workflows.py`

**Interfaces:**
- Consumes: completed Fast and Deep behavior.
- Produces: evidence that the implementation is complete and regression-safe.

- [ ] **Step 1: Confirm removed production symbols**

Run: `rg -n "ClaimSupportDecision|claim-support|Check each factual claim|Check every factual claim" app`

Expected: no matches.

- [ ] **Step 2: Run formatting and workflow tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_production_rag_workflows.py`

Expected: PASS.

- [ ] **Step 3: Run API and legacy RAG regressions**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_api_entrypoint.py tests/test_hybrid_rag.py tests/test_rag_api_agentic.py`

Expected: PASS.

- [ ] **Step 4: Run the complete backend suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests`

Expected: PASS.

- [ ] **Step 5: Review the final diff**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only the planned production and test files are changed by this implementation.

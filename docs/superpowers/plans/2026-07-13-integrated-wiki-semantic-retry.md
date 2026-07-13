# Integrated Wiki Semantic Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retry schema-valid but semantically invalid Wiki analysis and narrative responses with exact validator feedback, then regenerate all 23 canonical pages.

**Architecture:** Add one generic retry helper in `integrated_wiki_builder.py`. `build_integrated_pages` derives issue decisions from validated timelines and child digests, then passes LLM claims and drafts through semantic validation. OpenSearch persistence remains unchanged and receives only successfully validated pages.

**Tech Stack:** Python 3.13, Pydantic v2, LangChain `ChatOpenAI`, OpenRouter `z-ai/glm-4.7`, OpenSearch, pytest

## Global Constraints

- Initial generation plus at most two corrective retries per analysis or draft.
- Retry only `NarrativeValidationError`; schema validation retains its existing retry path.
- Derive issue decisions only from deterministic timeline and child evidence.
- Never synthesize missing factual claims or citations in deterministic code.
- Set `KNOWLEDGE_LLM_REASONING_EFFORT=none` for GLM-4.7 generation.
- Never persist a page that still fails validation.
- Keep API keys in process environment only.
- Preserve existing indexes, page schema, citations, issue continuity, and child-before-parent ordering.

---

## Approved Architecture Amendment

Live GLM-4.7 smoke tests showed that asking the model to restate every issue
decision was not reliable, even with validation feedback. The approved final
design therefore supersedes any later step in this plan that expects the LLM to
create or repair `issue_decisions`:

- `deterministic_issue_decisions()` creates ongoing, resolved, and reopened
  decisions from `issue_timelines` and validated child digests.
- The generated `PageAnalysis.issue_decisions` value is replaced before
  validation; the LLM still owns supported claims, contradictions, review
  items, outline, and prose.
- `KNOWLEDGE_LLM_REASONING_EFFORT` maps to OpenRouter's `reasoning.effort` body.
- Semantic retries continue to handle invalid claims, citations, and empty
  structured responses.

---

### Task 1: Semantic validation feedback retry

**Files:**
- Modify: `integrated_wiki_builder.py:6-189,747-963`
- Test: `tests/test_integrated_wiki_builder.py`

**Interfaces:**
- Consumes: existing `AnalysisFn`, `DraftFn`, `NarrativeValidationError`, `validate_stage1_evidence`, `validate_issue_decisions`, and `validate_draft`.
- Produces: `generate_with_semantic_retry(generator, validator, context, attempts=3) -> tuple[GeneratedT, ValidatedT]` and retry-aware page generation.

- [ ] **Step 1: Write failing retry and integration tests**

Add `generate_with_semantic_retry` to the test imports and add these tests:

```python
def single_lotcd_taxonomy(taxonomy):
    domain = next(item for item in taxonomy.domains if item.name == "DRAM")
    tech = next(item for item in domain.techs if item.name == "Spica")
    lotcd = next(item for item in tech.lotcds if item.code == "4SA")
    return taxonomy.model_copy(
        update={
            "domains": [
                domain.model_copy(
                    update={
                        "techs": [
                            tech.model_copy(update={"lotcds": [lotcd]})
                        ]
                    }
                )
            ]
        }
    )


def one_open_agenda():
    return {
        "agenda_id": "agenda-28",
        "mail_id": "mail-28",
        "week": "2026-W28",
        "state": "open",
        "review_status": "confirmed",
        "issue_id": "issue-open",
        "summary": "4SA 수율 하락",
        "subject": "4SA weekly",
        "topic": "yield",
        "source_doc_ids": ["chunk-28"],
        "target_paths": [
            {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
        ],
        "candidate_paths": [],
    }


def test_analysis_semantic_retry_supplies_validator_feedback(taxonomy):
    contexts = []

    def analyze(context):
        contexts.append(context)
        if context["node"]["id"] == "lotcd:4sa" and len(contexts) == 1:
            return PageAnalysis(outline=["개요"])
        return analysis_with_expected_issues(context)

    result = build_integrated_pages(
        single_lotcd_taxonomy(taxonomy),
        [one_open_agenda()],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    lotcd_contexts = [
        item for item in contexts if item["node"]["id"] == "lotcd:4sa"
    ]
    assert not result.failures
    assert len(lotcd_contexts) == 2
    assert "missing issue decision" in lotcd_contexts[1]["validation_feedback"]


def test_draft_semantic_retry_supplies_validator_feedback(taxonomy):
    contexts = []

    def make_draft(context, analysis):
        if context["node"]["id"] == "lotcd:4sa":
            contexts.append(context)
            if len(contexts) == 1:
                return empty_draft().model_copy(
                    update={"overview": "인용 없는 사실"}
                )
        return empty_draft()

    result = build_integrated_pages(
        single_lotcd_taxonomy(taxonomy),
        [one_open_agenda()],
        {},
        as_of_week="2026-W28",
        analyze=analysis_with_expected_issues,
        draft=make_draft,
    )

    assert not result.failures
    assert len(contexts) == 2
    assert "uncited factual unit" in contexts[1]["validation_feedback"]


def test_semantic_retry_stops_after_three_invalid_analyses(taxonomy):
    calls = []

    def analyze(context):
        if context["node"]["id"] == "lotcd:4sa":
            calls.append(context)
            return PageAnalysis(outline=["개요"])
        return analysis_with_expected_issues(context)

    result = build_integrated_pages(
        single_lotcd_taxonomy(taxonomy),
        [one_open_agenda()],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert len(calls) == 3
    assert result.pages == []
    assert "missing issue decision" in result.failures["lotcd:4sa"]
    assert result.failures["tech:dram:spica"] == "required child failed"
    assert result.failures["domain:dram"] == "required child failed"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_integrated_wiki_builder.py::test_analysis_semantic_retry_supplies_validator_feedback \
  tests/test_integrated_wiki_builder.py::test_draft_semantic_retry_supplies_validator_feedback \
  tests/test_integrated_wiki_builder.py::test_semantic_retry_stops_after_three_invalid_analyses
```

Expected: first two tests fail because no `validation_feedback` retry occurs; the exhaustion test reports only one analysis call.

- [ ] **Step 3: Implement the retry helper**

Add `TypeVar` to imports and add this helper after `invoke_structured`:

```python
GeneratedT = TypeVar("GeneratedT")
ValidatedT = TypeVar("ValidatedT")


def generate_with_semantic_retry(
    generator: Callable[[dict[str, Any]], GeneratedT],
    validator: Callable[[GeneratedT], ValidatedT],
    context: dict[str, Any],
    *,
    attempts: int = 3,
) -> tuple[GeneratedT, ValidatedT]:
    retry_context = context
    for attempt in range(attempts):
        generated = generator(retry_context)
        try:
            return generated, validator(generated)
        except NarrativeValidationError as exc:
            if attempt == attempts - 1:
                raise
            retry_context = {**context, "validation_feedback": str(exc)}
    raise AssertionError("unreachable")
```

Strengthen both system prompts with these exact rules:

```python
ANALYSIS_SYSTEM_PROMPT = """당신은 반도체 수율 Wiki 편집자입니다.
이전 문서와 허용된 근거를 비교해 새 사실, 유지 사실, 낡은 사실, 이슈 상태 전환,
모순, 검토 항목, 문서 목차를 구조화하십시오. mail_id와 agenda_id가 없는 주장은
SupportedClaim으로 만들지 마십시오. 하위 digest는 원본 mail_id가 추적되는 주장만 사용하십시오.
issue_decisions는 deterministic pipeline이 채우므로 빈 배열로 반환하십시오.
validation_feedback이 있으면 기존의 유효한 근거를 버리지 말고 해당 오류를 수정하십시오."""

DRAFT_SYSTEM_PROMPT = """당신은 통합 서술형 반도체 수율 Wiki 작성자입니다.
승인된 분석과 근거만 사용해 여섯 개 현재 본문 섹션과 이번 주 이력을 한국어로
작성하십시오. 사실 문단마다 [mail:<mail_id>]를 붙이십시오. 과거 이력은 작성하지
마십시오. 메일에 없는 원인, 수치, 담당자, 해결 여부를 만들지 마십시오.
validation_feedback이 있으면 기존의 유효한 인용을 유지하며 해당 오류를 수정하십시오."""
```

- [ ] **Step 4: Wire both validated stages through the helper**

Replace direct analysis generation and validation with:

```python
available_reopened_evidence = _reopened_evidence_ids(
    timeline_agendas, child_digests
)
expected_issue_statuses = _expected_issue_statuses(
    compact_timelines, child_digests
)

def validate_analysis(candidate: PageAnalysis) -> dict[str, list[str]]:
    evidence = validate_stage1_evidence(candidate, validation_agendas)
    validate_issue_decisions(
        candidate,
        validation_agendas,
        expected_issue_statuses,
        available_reopened_evidence,
    )
    return evidence

analysis, evidence_by_mail = generate_with_semantic_retry(
    analyze,
    validate_analysis,
    context,
)
```

Replace direct draft generation and validation with:

```python
narrative, validated_citations = generate_with_semantic_retry(
    lambda retry_context: draft(retry_context, analysis),
    lambda candidate: validate_draft(candidate, evidence_by_mail),
    context,
)
current_citation_map = [*validated_citations, *legacy_citations]
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
pytest -q \
  tests/test_integrated_wiki_builder.py::test_analysis_semantic_retry_supplies_validator_feedback \
  tests/test_integrated_wiki_builder.py::test_draft_semantic_retry_supplies_validator_feedback \
  tests/test_integrated_wiki_builder.py::test_semantic_retry_stops_after_three_invalid_analyses
```

Expected: `3 passed`.

- [ ] **Step 6: Run the full Python regression suite**

Run:

```bash
pytest -q
```

Expected: all Python tests pass with no failures.

- [ ] **Step 7: Commit the implementation**

```bash
git add integrated_wiki_builder.py tests/test_integrated_wiki_builder.py
git commit -m "fix(wiki): retry semantic validation"
```

---

### Task 2: Regenerate and verify canonical pages

**Files:**
- Modify: OpenSearch documents in `category_wiki_pages`; no repository file changes.

**Interfaces:**
- Consumes: `integrated_wiki_builder.py` CLI, existing `mail_agendas`, taxonomy fixture, and runtime OpenRouter credentials.
- Produces: 23 validated `latest` canonical documents plus their `2026-W28` snapshots.

- [ ] **Step 1: Run a 4SA branch smoke generation without persistence**

Use the existing OpenRouter runtime secret, `OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`, and `KNOWLEDGE_LLM_MODEL=z-ai/glm-4.7`. Invoke `build_integrated_pages` with a taxonomy reduced to DRAM/Spica/4SA and do not call `save_integrated_pages`.

Expected output:

```text
{'pages': 3, 'failures': {}}
```

- [ ] **Step 2: Regenerate all canonical pages**

Run with the OpenRouter key already present in process environment:

```bash
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1 \
KNOWLEDGE_LLM_MODEL=z-ai/glm-4.7 \
KNOWLEDGE_LLM_REASONING_EFFORT=none \
python integrated_wiki_builder.py \
  --week 2026-28 \
  --allow-external-llm \
  --allow-dummy-taxonomy
```

Expected output:

```json
{"pages": 23, "failed": 0, "expected_pages": 23}
```

- [ ] **Step 3: Verify DRAM canonical API content**

Run:

```bash
python - <<'PY'
import json
from urllib.request import urlopen

url = "http://127.0.0.1:8002/api/knowledge/wiki/pages/DRAM"
page = json.load(urlopen(url))
headings = [
    "## 개요",
    "## 현재 상태와 주요 변화",
    "## 원인과 영향 관계",
    "## 조치와 효과",
    "## 펜딩 이슈와 의사결정",
    "## 누적 지식",
]
assert page["doc_type"] == "canonical"
assert page["current_body_markdown"]
assert all(item in page["current_body_markdown"] for item in headings)
assert page["weekly_history"]
assert "[mail:" in page["body_markdown"]
assert page["citation_map"]
print(
    {
        "canonical_id": page["canonical_id"],
        "week": page["as_of_week"],
        "history_entries": len(page["weekly_history"]),
        "citations": len(page["citation_map"]),
    }
)
PY
```

Expected: assertions pass and the printed summary reports `canonical_id` as `dram`.

- [ ] **Step 4: Verify every latest page is integrated**

Run:

```bash
python - <<'PY'
from category_wiki_builder import PAGE_INDEX
from embed_vectordb import get_opensearch_client

response = get_opensearch_client().search(
    index=PAGE_INDEX,
    body={
        "size": 100,
        "query": {"term": {"page_kind": "latest"}},
        "_source": [
            "category_id",
            "doc_type",
            "current_body_markdown",
            "weekly_history",
            "citation_map",
        ],
    },
)
pages = [item["_source"] for item in response["hits"]["hits"]]
assert len(pages) == 23
assert all(page["doc_type"] == "canonical" for page in pages)
assert all(page["current_body_markdown"] for page in pages)
assert all(page["weekly_history"] for page in pages)
print({"integrated_pages": len(pages)})
PY
```

Expected: `{'integrated_pages': 23}`.

- [ ] **Step 5: Confirm repository cleanliness**

Run:

```bash
git status --short
```

Expected: only the pre-existing untracked `.superpowers/` directory appears.

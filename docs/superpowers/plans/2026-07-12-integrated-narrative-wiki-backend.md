# Integrated Narrative Wiki Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a test-first, bottom-up LLM Wiki generator that stores one cited canonical narrative page per Domain, Tech, and LOTCD and exposes it through stable knowledge APIs.

**Architecture:** Keep `weekly_mail`, `mail_agendas`, agenda extraction, and existing report builders unchanged. Add `integrated_wiki_builder.py`, reuse the current taxonomy/OpenSearch helpers, run a structured analysis pass followed by a narrative draft pass, validate citations and state transitions, then persist canonical pages and snapshots. Extend the existing Pydantic/API contract without breaking legacy Category Wiki pages during migration.

**Tech Stack:** Python 3.11+, Pydantic v2, LangChain `ChatOpenAI.with_structured_output`, OpenSearch, FastAPI, pytest.

## Global Constraints

- Do not chunk or embed data in the Wiki Builder; `embed_vectordb.py` remains the only embedding stage.
- Do not change `weekly_mail`, `mail_agendas`, weekly reports, monthly reports, or `wiki_summaries` behavior.
- Keep `category_wiki_builder.py` available during transition.
- Generate in strict order: LOTCD, then Tech, then Domain.
- Preserve all prior weekly history deterministically; the LLM writes only current prose and the new week entry.
- Require `[mail:<mail_id>]` on factual prose and reject invalid or out-of-scope citations.
- Do not resolve an issue without a terminal-state agenda.
- A failed child blocks its parent update; the prior canonical page remains untouched.
- Add no SQLite dependency and no vector field to Category Wiki mappings.

---

## File structure

- Create `integrated_wiki_builder.py`: narrative schemas, prompt adapters, validation, bottom-up orchestration, persistence, CLI.
- Create `tests/test_integrated_wiki_builder.py`: unit and orchestration coverage for the new builder.
- Modify `knowledge_models.py`: backward-compatible canonical page, citation, history, summary, and citation-detail response models.
- Modify `knowledge_api.py`: canonical page list and citation-detail endpoints.
- Modify `tests/test_knowledge_api.py`: API contract and legacy compatibility tests.
- Modify `run_pipeline.py`: switch only the enabled Category Wiki phase to the integrated builder.
- Modify `docs/category_wiki_builder.md`: document the new production command and unchanged embedding/report boundaries.

### Task 1: Add structured narrative contracts and index fields

**Files:**
- Create: `integrated_wiki_builder.py`
- Create: `tests/test_integrated_wiki_builder.py`
- Modify: `knowledge_models.py:198-219`

**Interfaces:**
- Consumes: `CategoryNode`, `CategoryPath`, and `TERMINAL_STATES` from `category_wiki_builder.py`.
- Produces: `SupportedClaim`, `IssueDecision`, `PageAnalysis`, `NarrativeDraft`, `WeeklyHistoryEntry`, `CitationMapEntry`, `ChildDigest`, and `integrated_page_index_definition()`.

- [ ] **Step 1: Write failing schema and mapping tests**

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from integrated_wiki_builder import (
    NarrativeDraft,
    SupportedClaim,
    integrated_page_index_definition,
)


def test_supported_claim_requires_mail_and_agenda_evidence():
    with pytest.raises(ValidationError):
        SupportedClaim(text="4SA 수율이 하락했다", mail_ids=[], agenda_ids=[])


def test_narrative_draft_has_the_approved_sections():
    draft = NarrativeDraft(
        overview="개요 [mail:mail-1]",
        current_status="현재 상태 [mail:mail-1]",
        cause_and_impact="원인 분석 [mail:mail-1]",
        actions_and_effects="조치 결과 [mail:mail-1]",
        pending_and_decisions="후속 확인 [mail:mail-1]",
        accumulated_knowledge="누적 패턴 [mail:mail-1]",
        weekly_update="이번 주 변경 [mail:mail-1]",
        confidence="high",
    )
    assert draft.weekly_update.startswith("이번 주")


def test_integrated_mapping_adds_structured_fields_without_vectors():
    properties = integrated_page_index_definition()["mappings"]["properties"]
    assert properties["doc_type"]["type"] == "keyword"
    assert properties["weekly_history"]["type"] == "nested"
    assert properties["citation_map"]["type"] == "nested"
    assert "embedding" not in properties
```

- [ ] **Step 2: Run the tests and confirm import failure**

Run: `pytest tests/test_integrated_wiki_builder.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'integrated_wiki_builder'`.

- [ ] **Step 3: Add the contracts and additive OpenSearch mapping**

```python
# integrated_wiki_builder.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from category_wiki_builder import page_index_definition


class WikiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupportedClaim(WikiModel):
    text: str = Field(min_length=1)
    mail_ids: list[str]
    agenda_ids: list[str]

    @field_validator("mail_ids", "agenda_ids")
    @classmethod
    def require_evidence(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("supported claims require evidence")
        return value


class IssueDecision(WikiModel):
    issue_id: str
    status: Literal["ongoing", "resolved", "reopened"]
    summary: str
    mail_ids: list[str]
    agenda_ids: list[str]


class PageAnalysis(WikiModel):
    new_claims: list[SupportedClaim] = Field(default_factory=list)
    retained_claims: list[SupportedClaim] = Field(default_factory=list)
    stale_claims: list[str] = Field(default_factory=list)
    issue_decisions: list[IssueDecision] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    review_items: list[str] = Field(default_factory=list)
    outline: list[str]


class NarrativeDraft(WikiModel):
    overview: str
    current_status: str
    cause_and_impact: str
    actions_and_effects: str
    pending_and_decisions: str
    accumulated_knowledge: str
    weekly_update: str
    confidence: Literal["low", "medium", "high"]


class WeeklyHistoryEntry(WikiModel):
    week: str
    body_markdown: str
    source_mail_ids: list[str] = Field(default_factory=list)


class CitationMapEntry(WikiModel):
    mail_id: str
    agenda_ids: list[str]
    used_in_sections: list[str]
    category_paths: list[str]


class ChildDigest(WikiModel):
    canonical_id: str
    as_of_week: str
    summary: str
    claims: list[SupportedClaim]
    issues: list[IssueDecision]
    contradictions: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]


AnalysisFn = Callable[[dict[str, Any]], PageAnalysis]
DraftFn = Callable[[dict[str, Any], PageAnalysis], NarrativeDraft]


def integrated_page_index_definition() -> dict[str, Any]:
    definition = page_index_definition()
    definition["mappings"]["properties"].update(
        {
            "doc_type": {"type": "keyword"},
            "canonical_id": {"type": "keyword"},
            "aliases": {"type": "keyword"},
            "current_body_markdown": {
                "type": "text",
                "analyzer": "category_korean",
            },
            "weekly_history": {
                "type": "nested",
                "properties": {
                    "week": {"type": "keyword"},
                    "body_markdown": {"type": "text", "analyzer": "category_korean"},
                    "source_mail_ids": {"type": "keyword"},
                },
            },
            "citation_map": {
                "type": "nested",
                "properties": {
                    "mail_id": {"type": "keyword"},
                    "agenda_ids": {"type": "keyword"},
                    "used_in_sections": {"type": "keyword"},
                    "category_paths": {"type": "keyword"},
                },
            },
            "child_page_ids": {"type": "keyword"},
            "confidence": {"type": "keyword"},
            "open_issue_count": {"type": "integer"},
            "resolved_issue_count": {"type": "integer"},
            "contradictions": {"type": "text", "analyzer": "category_korean"},
            "generation_review_items": {"type": "text", "analyzer": "category_korean"},
            "updated_at": {"type": "date"},
        }
    )
    return definition
```

Extend `CategoryWikiPage` in `knowledge_models.py` with defaults so an old `page_kind=latest` document remains readable before the first integrated run:

```python
class WeeklyHistoryRecord(StrictModel):
    week: str
    body_markdown: str
    source_mail_ids: list[str] = Field(default_factory=list)


class WikiCitationRecord(StrictModel):
    mail_id: str
    agenda_ids: list[str] = Field(default_factory=list)
    used_in_sections: list[str] = Field(default_factory=list)
    category_paths: list[str] = Field(default_factory=list)


class CategoryWikiPage(StrictModel):
    category_id: str
    page_kind: Literal["latest", "snapshot"]
    doc_type: Literal["canonical", "snapshot"] = "canonical"
    canonical_id: str = ""
    level: Literal["domain", "tech", "lotcd"]
    domain: Literal["DRAM", "NAND"]
    tech: str | None = None
    lotcd: str | None = None
    title: str
    product: str | None = None
    fab_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    as_of_week: str
    current_body_markdown: str = ""
    weekly_history: list[WeeklyHistoryRecord] = Field(default_factory=list)
    body_markdown: str
    citation_map: list[WikiCitationRecord] = Field(default_factory=list)
    child_page_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    agenda_count: int
    open_issue_ids: list[str] = Field(default_factory=list)
    resolved_issue_ids: list[str] = Field(default_factory=list)
    open_issue_count: int = 0
    resolved_issue_count: int = 0
    contradictions: list[str] = Field(default_factory=list)
    generation_review_items: list[str] = Field(default_factory=list)
    review_agenda_ids: list[str] = Field(default_factory=list)
    source_agenda_ids: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
    source_hash: str
    taxonomy_version: int
    generated_at: datetime
    updated_at: datetime | None = None
```

- [ ] **Step 4: Run the focused tests**

Run: `pytest tests/test_integrated_wiki_builder.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit the contracts**

```bash
git add integrated_wiki_builder.py knowledge_models.py tests/test_integrated_wiki_builder.py
git commit -m "feat(wiki): add narrative page contracts"
```

### Task 2: Preserve history and classify direct evidence

**Files:**
- Modify: `integrated_wiki_builder.py`
- Modify: `tests/test_integrated_wiki_builder.py`

**Interfaces:**
- Consumes: `CategoryNode`, normalized agenda dictionaries, and previous canonical page dictionaries.
- Produces: `canonical_path(node)`, `direct_agendas_for_node(node, agendas)`, `merge_weekly_history(previous, current)`, `render_current_body(draft)`, and `assemble_body(current_body, history)`.

- [ ] **Step 1: Add failing evidence-scope and history tests**

```python
from category_wiki_builder import CategoryNode
from integrated_wiki_builder import (
    WeeklyHistoryEntry,
    assemble_body,
    direct_agendas_for_node,
    merge_weekly_history,
)


def test_tech_direct_evidence_excludes_lotcd_agenda():
    node = CategoryNode("tech:dram:spica", "tech", "DRAM", "Spica", None, "Spica")
    agendas = [
        {"agenda_id": "tech", "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": None}]},
        {"agenda_id": "lot", "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}]},
    ]
    assert [item["agenda_id"] for item in direct_agendas_for_node(node, agendas)] == ["tech"]


def test_history_replaces_same_week_and_preserves_older_entries():
    old = [
        WeeklyHistoryEntry(week="2026-W27", body_markdown="W27", source_mail_ids=["m27"]),
        WeeklyHistoryEntry(week="2026-W26", body_markdown="W26", source_mail_ids=["m26"]),
    ]
    current = WeeklyHistoryEntry(week="2026-W27", body_markdown="W27 fixed", source_mail_ids=["m27b"])
    merged = merge_weekly_history(old, current)
    assert [item.week for item in merged] == ["2026-W27", "2026-W26"]
    assert merged[0].body_markdown == "W27 fixed"
    assert "W26" in assemble_body("## 개요\n현재", merged)
```

- [ ] **Step 2: Verify the new tests fail**

Run: `pytest tests/test_integrated_wiki_builder.py -q`

Expected: import errors for the four new functions.

- [ ] **Step 3: Implement deterministic scope and history helpers**

```python
def canonical_path(node: CategoryNode) -> str:
    return "/".join(
        value.lower() for value in (node.domain, node.tech, node.lotcd) if value
    )


def direct_agendas_for_node(
    node: CategoryNode, agendas: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    def matches(path: dict[str, Any]) -> bool:
        if path.get("domain") != node.domain:
            return False
        if node.level == "domain":
            return path.get("tech") is None and path.get("lotcd") is None
        if node.level == "tech":
            return path.get("tech") == node.tech and path.get("lotcd") is None
        return path.get("tech") == node.tech and path.get("lotcd") == node.lotcd

    return [
        agenda
        for agenda in agendas
        if agenda.get("review_status") == "confirmed"
        and any(matches(path) for path in agenda.get("target_paths", []))
    ]


def merge_weekly_history(
    previous: list[WeeklyHistoryEntry], current: WeeklyHistoryEntry
) -> list[WeeklyHistoryEntry]:
    by_week = {item.week: item for item in previous}
    by_week[current.week] = current
    return [by_week[week] for week in sorted(by_week, reverse=True)]


def render_current_body(draft: NarrativeDraft) -> str:
    sections = (
        ("개요", draft.overview),
        ("현재 상태와 주요 변화", draft.current_status),
        ("원인과 영향 관계", draft.cause_and_impact),
        ("조치와 효과", draft.actions_and_effects),
        ("펜딩 이슈와 의사결정", draft.pending_and_decisions),
        ("누적 지식", draft.accumulated_knowledge),
    )
    return "\n\n".join(f"## {title}\n\n{body.strip()}" for title, body in sections)


def assemble_body(
    current_body: str, history: list[WeeklyHistoryEntry]
) -> str:
    entries = "\n\n".join(
        f"### {item.week}\n\n{item.body_markdown.strip()}" for item in history
    )
    return f"{current_body.strip()}\n\n## 주차별 업데이트 이력\n\n{entries}".strip()
```

- [ ] **Step 4: Run the focused tests**

Run: `pytest tests/test_integrated_wiki_builder.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit deterministic assembly**

```bash
git add integrated_wiki_builder.py tests/test_integrated_wiki_builder.py
git commit -m "feat(wiki): preserve canonical page history"
```

### Task 3: Add the two-stage structured LLM adapter

**Files:**
- Modify: `integrated_wiki_builder.py`
- Modify: `tests/test_integrated_wiki_builder.py`

**Interfaces:**
- Consumes: a JSON-safe page context with node metadata, previous current prose, issue state, direct agendas, and child digests.
- Produces: `build_llm_generators() -> tuple[AnalysisFn, DraftFn]` and `invoke_structured(runnable, messages)`.

- [ ] **Step 1: Write a failing adapter test with fake structured runnables**

```python
from integrated_wiki_builder import PageAnalysis, invoke_structured


def test_structured_invocation_retries_once_after_validation_error():
    valid = PageAnalysis(outline=["개요"])

    class FakeRunnable:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise ValidationError.from_exception_data("PageAnalysis", [])
            return valid

    runnable = FakeRunnable()
    assert invoke_structured(runnable, [{"role": "user", "content": "evidence"}]) == valid
    assert runnable.calls == 2
```

- [ ] **Step 2: Run the adapter test and confirm failure**

Run: `pytest tests/test_integrated_wiki_builder.py::test_structured_invocation_retries_once_after_validation_error -q`

Expected: FAIL because `invoke_structured` is missing.

- [ ] **Step 3: Implement retry and structured generators**

```python
import json

from pydantic import ValidationError

from agenda_extract import llm_connection


ANALYSIS_SYSTEM_PROMPT = """당신은 반도체 수율 Wiki 편집자입니다.
이전 문서와 허용된 근거를 비교해 새 사실, 유지 사실, 낡은 사실, 이슈 상태 전환,
모순, 검토 항목, 문서 목차를 구조화하십시오. mail_id와 agenda_id가 없는 주장은
SupportedClaim으로 만들지 마십시오. 하위 digest는 원본 mail_id가 추적되는 주장만 사용하십시오."""

DRAFT_SYSTEM_PROMPT = """당신은 통합 서술형 반도체 수율 Wiki 작성자입니다.
승인된 분석과 근거만 사용해 여섯 개 현재 본문 섹션과 이번 주 이력을 한국어로
작성하십시오. 사실 문단마다 [mail:<mail_id>]를 붙이십시오. 과거 이력은 작성하지
마십시오. 메일에 없는 원인, 수치, 담당자, 해결 여부를 만들지 마십시오."""


def invoke_structured(runnable, messages: list[dict[str, str]]):
    current = list(messages)
    for attempt in range(2):
        try:
            return runnable.invoke(current)
        except ValidationError as exc:
            if attempt == 1:
                raise
            current.append(
                {
                    "role": "user",
                    "content": f"스키마 오류를 수정해 다시 반환하십시오: {exc}",
                }
            )
    raise AssertionError("unreachable")


def build_llm_generators() -> tuple[AnalysisFn, DraftFn]:
    from langchain_openai import ChatOpenAI

    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
    )
    analysis_llm = llm.with_structured_output(PageAnalysis, method="function_calling")
    draft_llm = llm.with_structured_output(NarrativeDraft, method="function_calling")

    def analyze(context: dict[str, Any]) -> PageAnalysis:
        return invoke_structured(
            analysis_llm,
            [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
        )

    def draft(context: dict[str, Any], analysis: PageAnalysis) -> NarrativeDraft:
        payload = {"context": context, "analysis": analysis.model_dump()}
        return invoke_structured(
            draft_llm,
            [
                {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )

    return analyze, draft
```

- [ ] **Step 4: Run all builder tests**

Run: `pytest tests/test_integrated_wiki_builder.py -q`

Expected: all tests pass without making an external LLM call.

- [ ] **Step 5: Commit the LLM adapter**

```bash
git add integrated_wiki_builder.py tests/test_integrated_wiki_builder.py
git commit -m "feat(wiki): add two-stage narrative prompts"
```

### Task 4: Validate citations and build pages bottom-up

**Files:**
- Modify: `integrated_wiki_builder.py`
- Modify: `tests/test_integrated_wiki_builder.py`

**Interfaces:**
- Consumes: `AnalysisFn`, `DraftFn`, taxonomy nodes, agendas through `as_of_week`, and previous canonical pages keyed by `category_id`.
- Produces: `validate_draft`, `build_child_digest`, `build_integrated_pages`, and `BuildResult`.

- [ ] **Step 1: Add failing citation, resolution, order, and child-failure tests**

```python
from integrated_wiki_builder import (
    BuildResult,
    NarrativeValidationError,
    PageAnalysis,
    build_integrated_pages,
    validate_draft,
)


def test_invalid_mail_citation_is_rejected():
    draft = NarrativeDraft(
        overview="근거 없는 주장 [mail:missing]",
        current_status="", cause_and_impact="", actions_and_effects="",
        pending_and_decisions="", accumulated_knowledge="", weekly_update="",
        confidence="low",
    )
    with pytest.raises(NarrativeValidationError, match="missing"):
        validate_draft(draft, allowed_agendas=[{"mail_id": "mail-1", "agenda_id": "a1", "state": "open"}])


def test_resolved_decision_requires_terminal_agenda():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[IssueDecision(
            issue_id="issue-1", status="resolved", summary="해결", mail_ids=["mail-1"], agenda_ids=["a1"]
        )],
    )
    with pytest.raises(NarrativeValidationError, match="terminal"):
        validate_issue_decisions(analysis, [{"mail_id": "mail-1", "agenda_id": "a1", "state": "open"}])


def test_generation_order_is_lotcd_then_tech_then_domain(taxonomy):
    calls = []

    def analyze(context):
        calls.append(context["node"]["level"])
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy, [], {}, as_of_week="2026-W28",
        analyze=analyze, draft=lambda context, analysis: empty_draft(),
    )
    assert calls == ["lotcd"] * 14 + ["tech"] * 7 + ["domain"] * 2
    assert len(result.pages) == 23


def test_failed_lotcd_blocks_its_tech_and_domain(taxonomy):
    def analyze(context):
        if context["node"].get("lotcd") == "4SA":
            raise RuntimeError("generation failed")
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy, [], {}, as_of_week="2026-W28",
        analyze=analyze, draft=lambda context, analysis: empty_draft(),
    )
    assert "lotcd:4sa" in result.failures
    assert "tech:dram:spica" in result.failures
    assert "domain:dram" in result.failures
```

Add `taxonomy` and `empty_draft()` fixtures to the same test file using `fixtures/knowledge/taxonomy.json`; `empty_draft()` returns seven empty strings and `confidence="low"`.

- [ ] **Step 2: Run the new tests and verify failures**

Run: `pytest tests/test_integrated_wiki_builder.py -q`

Expected: FAIL for missing validation and orchestration symbols.

- [ ] **Step 3: Implement validation and bottom-up orchestration**

Implement these exact public signatures:

```python
import re
from dataclasses import dataclass, field

from category_wiki_builder import (
    CategoryNode,
    TERMINAL_STATES,
    category_nodes,
    issue_timelines,
)
from knowledge_models import TaxonomyDocument

MAIL_CITATION = re.compile(r"\[mail:([^\]]+)\]")


class NarrativeValidationError(ValueError):
    pass


@dataclass
class BuildResult:
    pages: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)


def validate_issue_decisions(
    analysis: PageAnalysis, allowed_agendas: list[dict[str, Any]]
) -> None:
    agendas = {str(item["agenda_id"]): item for item in allowed_agendas}
    for decision in analysis.issue_decisions:
        if decision.status != "resolved":
            continue
        cited = [agendas.get(agenda_id) for agenda_id in decision.agenda_ids]
        if not any(
            item and str(item.get("state", "")).casefold() in TERMINAL_STATES
            for item in cited
        ):
            raise NarrativeValidationError(
                f"resolved issue {decision.issue_id} has no terminal agenda"
            )


def validate_draft(
    draft: NarrativeDraft, allowed_agendas: list[dict[str, Any]]
) -> list[CitationMapEntry]:
    allowed_mail_ids = {str(item["mail_id"]) for item in allowed_agendas}
    agendas_by_mail: dict[str, list[str]] = {}
    for item in allowed_agendas:
        agendas_by_mail.setdefault(str(item["mail_id"]), []).append(str(item["agenda_id"]))
    section_values = {
        "개요": draft.overview,
        "현재 상태와 주요 변화": draft.current_status,
        "원인과 영향 관계": draft.cause_and_impact,
        "조치와 효과": draft.actions_and_effects,
        "펜딩 이슈와 의사결정": draft.pending_and_decisions,
        "누적 지식": draft.accumulated_knowledge,
        "주차별 업데이트 이력": draft.weekly_update,
    }
    used: dict[str, set[str]] = {}
    for section, body in section_values.items():
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
        for paragraph in paragraphs:
            citations = MAIL_CITATION.findall(paragraph)
            if not citations:
                raise NarrativeValidationError(f"uncited paragraph in {section}")
            for mail_id in citations:
                if mail_id not in allowed_mail_ids:
                    raise NarrativeValidationError(f"unknown mail citation: {mail_id}")
                used.setdefault(mail_id, set()).add(section)
    return [
        CitationMapEntry(
            mail_id=mail_id,
            agenda_ids=sorted(agendas_by_mail[mail_id]),
            used_in_sections=sorted(sections),
            category_paths=[],
        )
        for mail_id, sections in sorted(used.items())
    ]
```

Implement `build_child_digest(page, analysis)` by copying only `analysis.new_claims + analysis.retained_claims`, `analysis.issue_decisions`, contradictions, page identity/week, `draft.overview`, and confidence. Implement `build_integrated_pages(...)` with this deterministic order and failure rule:

```python
order = {"lotcd": 0, "tech": 1, "domain": 2}
nodes = sorted(category_nodes(taxonomy), key=lambda node: (order[node.level], node.id))
```

For each node, compute configured direct children from `nodes`; if any child ID is in `failures`, record `required child failed` and skip the node. Build compact issue timelines from all descendant agendas, but send prose evidence only for the current week plus agenda IDs referenced by the previous page's citation map. Include the previous `current_body_markdown`, recent two history records, and child digests. For a parent, resolve every child digest `agenda_id` against the global agenda map and add those records to `allowed_agendas` before `validate_draft`; this preserves original-mail validation through the hierarchy. After analysis and draft validation, render current body, merge the current week entry, and create the compatibility fields from `category_wiki_builder.build_page_documents` plus the new canonical fields. Set `citation_map[*].category_paths` to `[canonical_path(node)]`, store `analysis.contradictions` and `analysis.review_items`, derive aliases from the matching Tech/LOTCD taxonomy record, and keep classification-pending agenda IDs in `review_agenda_ids` before serialization.

- [ ] **Step 4: Run builder tests**

Run: `pytest tests/test_integrated_wiki_builder.py -q`

Expected: all tests pass and the 23-node order assertion is exact.

- [ ] **Step 5: Commit validated orchestration**

```bash
git add integrated_wiki_builder.py tests/test_integrated_wiki_builder.py
git commit -m "feat(wiki): synthesize pages bottom-up"
```

### Task 5: Persist canonical pages and switch the pipeline

**Files:**
- Modify: `integrated_wiki_builder.py`
- Modify: `tests/test_integrated_wiki_builder.py`
- Modify: `run_pipeline.py:86-102`
- Modify: `docs/category_wiki_builder.md`

**Interfaces:**
- Consumes: existing OpenSearch client, taxonomy path, week, and two generated callables.
- Produces: `fetch_previous_pages`, `validate_source_documents`, `save_integrated_pages`, `run`, and CLI `main`.

- [ ] **Step 1: Write failing persistence tests**

```python
def test_save_writes_one_canonical_and_one_snapshot_per_success(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "integrated_wiki_builder.helpers.bulk",
        lambda client, actions: captured.extend(actions),
    )
    page = sample_integrated_page("lotcd:4sa", "2026-W28")
    count = save_integrated_pages(FakeClient(), [page])
    assert count == 1
    assert [item["_id"] for item in captured] == ["lotcd:4sa", "lotcd:4sa:2026-W28"]
    assert captured[0]["_source"]["doc_type"] == "canonical"
    assert captured[1]["_source"]["doc_type"] == "snapshot"


def test_fetch_previous_pages_returns_only_latest_documents():
    client = SearchClient([
        {"_id": "lotcd:4sa", "_source": sample_integrated_page("lotcd:4sa", "2026-W27")}
    ])
    pages = fetch_previous_pages(client)
    assert set(pages) == {"lotcd:4sa"}
    assert client.query["query"] == {"term": {"page_kind": "latest"}}


def test_missing_weekly_mail_chunk_blocks_persistence():
    page = sample_integrated_page("lotcd:4sa", "2026-W28")
    page["source_doc_ids"] = ["missing-chunk"]
    with pytest.raises(NarrativeValidationError, match="missing-chunk"):
        validate_source_documents(SourceMgetClient(found_ids=[]), [page])
```

- [ ] **Step 2: Run the persistence tests and verify failure**

Run: `pytest tests/test_integrated_wiki_builder.py -q`

Expected: FAIL because persistence functions are missing.

- [ ] **Step 3: Implement fetch, save, run, and CLI**

Use the existing `PAGE_INDEX`, `AGENDA_INDEX`, `SOURCE_INDEX`, `ensure_index`, `fetch_agendas`, `load_taxonomy`, and OpenSearch client factory. `validate_source_documents(client, pages)` must collect unique `source_doc_ids`, call `client.mget(index=SOURCE_INDEX, body={"ids": ids})`, and raise `NarrativeValidationError` listing every `_id` whose response has `found=false`. `save_integrated_pages` must call that validator before bulk, write only successful `BuildResult.pages`, keep `_id=category_id` for API compatibility, and clone each page with `page_kind="snapshot"`, `doc_type="snapshot"` for the weekly snapshot. `run` must:

1. reject dummy taxonomy unless explicitly allowed;
2. reject external LLM use unless explicitly acknowledged;
3. ensure only the additive page mapping;
4. read existing `mail_agendas` without extracting or embedding;
5. choose `as_of_week` from the requested or available weeks;
6. read prior canonical pages;
7. call `build_integrated_pages`;
8. save successful pages unless `dry_run`;
9. return `{"pages": N, "failed": M}`.

Expose CLI flags `--week`, `--taxonomy`, `--allow-external-llm`, `--allow-dummy-taxonomy`, and `--dry-run`. Do not add agenda extraction or deterministic production modes to the new CLI; tests inject `AnalysisFn` and `DraftFn` directly.

- [ ] **Step 4: Switch only the Category Wiki pipeline call**

Replace the enabled Category Wiki block in `run_pipeline.py` with:

```python
if category_wiki_enabled:
    print(f"\n[{step}/{total_steps}] 통합 서술형 분류 Wiki 생성 (week={week})")
    import integrated_wiki_builder

    category_stats = integrated_wiki_builder.run(
        weeks=[week],
        allow_external_llm=True,
        allow_dummy_taxonomy=False,
        client=embed_vectordb.get_opensearch_client(),
    )
    if category_stats["failed"]:
        raise RuntimeError(f"통합 Wiki 생성 실패: {category_stats['failed']}개 분류")
    step += 1
```

Update `docs/category_wiki_builder.md` with the production command `python integrated_wiki_builder.py --week 2026-W28 --allow-external-llm`, the bottom-up order, and the explicit statement that the builder reads `mail_agendas` and never embeds.

- [ ] **Step 5: Run persistence and existing Category Wiki tests**

Run: `pytest tests/test_integrated_wiki_builder.py tests/test_category_wiki_builder.py -q`

Expected: all tests pass; the legacy builder suite remains unchanged.

- [ ] **Step 6: Commit persistence and pipeline integration**

```bash
git add integrated_wiki_builder.py tests/test_integrated_wiki_builder.py run_pipeline.py docs/category_wiki_builder.md
git commit -m "feat(wiki): persist integrated canonical pages"
```

### Task 6: Expose page summaries and verified citation detail

**Files:**
- Modify: `knowledge_models.py:198-245`
- Modify: `knowledge_api.py:61-83,227-255`
- Modify: `tests/test_knowledge_api.py`

**Interfaces:**
- Consumes: canonical page `citation_map`, existing `_get_opensearch_agenda`, and OpenSearch page index.
- Produces: `GET /api/knowledge/wiki/pages` and `GET /api/knowledge/wiki/citations/{mail_id}?category_id=<category_id>`.

- [ ] **Step 1: Add failing API tests**

```python
def test_wiki_page_list_returns_canonical_summaries(monkeypatch):
    monkeypatch.setattr(
        knowledge_api,
        "_list_category_wiki_pages",
        lambda: [{
            "category_id": "lotcd:4sa", "canonical_id": "dram/spica/4sa",
            "level": "lotcd", "domain": "DRAM", "tech": "Spica", "lotcd": "4SA",
            "title": "4SA", "as_of_week": "2026-W28", "open_issue_count": 2,
            "resolved_issue_count": 1, "confidence": "high",
        }],
    )
    response = request("GET", "/api/knowledge/wiki/pages")
    assert response.status_code == 200
    assert response.json()["items"][0]["open_issue_count"] == 2


def test_wiki_citation_returns_only_page_mapped_agendas(monkeypatch):
    page = canonical_page_fixture()
    page["citation_map"] = [{
        "mail_id": "mail-1", "agenda_ids": ["agenda-1"],
        "used_in_sections": ["원인과 영향 관계"], "category_paths": ["dram/spica/4sa"],
    }]
    monkeypatch.setattr(knowledge_api, "_get_category_wiki_page", lambda category_id: page)
    monkeypatch.setattr(knowledge_api, "_get_opensearch_agenda", lambda agenda_id: agenda_detail_fixture())
    response = request(
        "GET", "/api/knowledge/wiki/citations/mail-1",
        params={"category_id": "lotcd:4sa"},
    )
    assert response.status_code == 200
    assert response.json()["agendas"][0]["id"] == "agenda-1"
    assert response.json()["used_in_sections"] == ["원인과 영향 관계"]
```

- [ ] **Step 2: Run the API tests and confirm route failures**

Run: `pytest tests/test_knowledge_api.py -q`

Expected: the two new tests fail with missing routes or helpers.

- [ ] **Step 3: Add response models**

```python
class WikiPageSummary(StrictModel):
    category_id: str
    canonical_id: str
    level: Literal["domain", "tech", "lotcd"]
    domain: Literal["DRAM", "NAND"]
    tech: str | None = None
    lotcd: str | None = None
    title: str
    as_of_week: str
    open_issue_count: int
    resolved_issue_count: int
    confidence: Literal["low", "medium", "high"]
    review_item_count: int = 0


class WikiPageSummaryResponse(StrictModel):
    items: list[WikiPageSummary]


class WikiCitationDetail(StrictModel):
    mail: Mail
    agendas: list[AgendaView]
    used_in_sections: list[str]
```

- [ ] **Step 4: Implement canonical list and citation routes**

`_list_category_wiki_pages()` must search `PAGE_INDEX` with `term: {page_kind: latest}`, select only summary fields, derive `review_item_count` as `len(review_agenda_ids) + len(generation_review_items)`, and validate each hit as `WikiPageSummary`. The citation route must load the requested page, find an exact `mail_id` in its citation map, load only its mapped agenda IDs through `_get_opensearch_agenda`, verify every returned detail has the requested mail ID, and return the shared mail plus agenda views. Return 404 for an unmapped citation and 409 if mapped agendas disagree on mail identity.

Register static `/wiki/pages` before the three parameterized page routes so FastAPI does not treat `pages` as a domain value.

- [ ] **Step 5: Run API and model tests**

Run: `pytest tests/test_knowledge_api.py tests/test_category_wiki_builder.py tests/test_integrated_wiki_builder.py -q`

Expected: all tests pass, including the legacy page fixture with defaulted new fields.

- [ ] **Step 6: Commit API contracts**

```bash
git add knowledge_models.py knowledge_api.py tests/test_knowledge_api.py
git commit -m "feat(api): expose wiki citations and summaries"
```

### Task 7: Run backend regression and a dummy integrated build

**Files:**
- Modify only if a failure is caused by this feature: `integrated_wiki_builder.py`, `knowledge_models.py`, `knowledge_api.py`, `run_pipeline.py`, or their focused tests.

**Interfaces:**
- Consumes: completed backend implementation.
- Produces: verified backend and an OpenSearch dummy-data smoke result.

- [ ] **Step 1: Run formatting-independent static checks**

Run: `python -m compileall integrated_wiki_builder.py knowledge_models.py knowledge_api.py run_pipeline.py`

Expected: exit code 0.

- [ ] **Step 2: Run the full Python suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Run a no-write build with injected fake generators in the focused integration test**

Run: `pytest tests/test_integrated_wiki_builder.py -q -k "generation_order or persistence"`

Expected: all selected tests pass and no external LLM request occurs.

- [ ] **Step 4: Check that report and embedding files were not changed by the feature commits**

Run: `git diff --name-only HEAD~6..HEAD`

Expected: output does not include `embed_vectordb.py`, `generate_outlook_report.py`, or `generate_monthly_report.py`.

- [ ] **Step 5: Commit only regression fixes if required**

```bash
git add integrated_wiki_builder.py knowledge_models.py knowledge_api.py run_pipeline.py tests
git commit -m "fix(wiki): close backend regression gaps"
```

Skip this commit when no regression fix was needed.

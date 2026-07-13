# Incremental Merged Yield Wiki Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed-section Category Wiki generation with an OpenSearch-only incremental merge that updates affected LOTCD, Tech, and Domain pages from each week's new and changed Agenda evidence.

**Architecture:** Keep weekly_mail and mail_agendas as production inputs. Resolve Agenda evidence into a structured OpenSearch Issue Ledger, then make one dynamic page-merge LLM call per affected taxonomy node in LOTCD, Tech, Domain order. Validate claim coverage, Issue state, and raw-mail citations before saving each page and its idempotent weekly snapshot.

**Tech Stack:** Python 3.13, Pydantic v2, LangChain ChatOpenAI structured output, OpenSearch 3.x, FastAPI, React 19, TypeScript 7, react-markdown, Vitest, pytest

## Global Constraints

- Production Wiki generation reads only OpenSearch weekly_mail and mail_agendas.
- Do not read SQLite or fixtures in the production builder path.
- Do not add a second chunking or embedding pass.
- Keep wiki_builder.py, wiki_summarizer.py, weekly reports, and monthly reports unchanged.
- Keep current Wiki API routes stable.
- Latest page IDs remain category IDs; snapshot IDs remain category ID plus normalized week.
- No fixed current-body outline. Required knowledge is validated by Claim and Issue coverage.
- Add weekly history only when the category has a new or changed Agenda.
- Same-week reruns replace history and snapshot documents.
- Persist each validated page immediately; resume failed parents without rebuilding successful children.
- Initial migration ignores fixed-section latest bodies and rebuilds from OpenSearch evidence.
- Every factual Markdown unit must resolve to weekly_mail source documents.
- Use no more than one initial page-merge call per affected page, excluding correction retries.

---

## File Structure

- Create wiki_issue_ledger.py: Issue models, state transitions, Agenda-to-Issue resolution, OpenSearch mapping and persistence.
- Modify category_wiki_builder.py: Agenda version metadata required for weekly delta detection.
- Modify integrated_wiki_builder.py: dynamic merge contract, validation, affected-node selection, bottom-up incremental orchestration, immediate persistence, CLI.
- Modify knowledge_models.py: additive history, citation, Issue, and generation-strategy fields.
- Modify knowledge_api.py: OpenSearch-only Wiki citation hydration.
- Modify run_pipeline.py: accept incremental build statistics instead of requiring all 23 pages every week.
- Modify web/src/types.ts: additive canonical page fields.
- Modify web/src/components/CategoryWikiReader.tsx: derive outline from actual Markdown headings.
- Modify web/src/components/CategoryWikiReader.test.tsx: dynamic heading, metrics, and weekly history coverage.
- Modify web/src/components/WikiDocumentMetaPane.test.tsx: Issue Ledger count contract.
- Modify docs/category_wiki_builder.md: initial rebuild and weekly incremental commands.
- Modify tests/test_category_wiki_builder.py: Agenda version metadata.
- Create tests/test_wiki_issue_ledger.py: Issue identity and state machine.
- Modify tests/test_integrated_wiki_builder.py: dynamic merge, delta, propagation, resume, mapping, persistence.
- Modify tests/test_knowledge_api.py: raw OpenSearch citation behavior.
- Modify tests/test_run_pipeline.py: incremental pipeline success and failure rules.

### Task 1: Version Agenda Documents for Weekly Delta Detection

**Files:**
- Modify: category_wiki_builder.py:78-145, 330-413
- Modify: tests/test_category_wiki_builder.py

**Interfaces:**
- Consumes: extracted Agenda dictionaries already produced by build_agenda_documents.
- Produces: agenda_content_hash(document: dict[str, Any]) -> str, apply_agenda_version(document: dict[str, Any], previous: dict[str, Any] | None, *, now: datetime, observed_week: str | None = None) -> dict[str, Any].

- [ ] **Step 1: Write failing tests for new, unchanged, and corrected Agenda metadata**

Add:

~~~python
from datetime import UTC, datetime

from category_wiki_builder import apply_agenda_version


def test_apply_agenda_version_marks_new_agenda():
    current = {
        "agenda_id": "agenda-29",
        "mail_id": "2026-W29:Spica:mail-1",
        "week": "2026-W29",
        "summary": "4SA chamber A 원복",
        "source_quote": "조건을 원복했습니다.",
        "state": "in_progress",
        "topic": "action",
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "source_doc_ids": ["chunk-29"],
    }
    versioned = apply_agenda_version(
        current,
        None,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert versioned["created_at"] == "2026-07-14T00:00:00+00:00"
    assert versioned["updated_at"] == "2026-07-14T00:00:00+00:00"
    assert versioned["updated_week"] == "2026-W29"
    assert len(versioned["content_hash"]) == 64


def test_apply_agenda_version_preserves_unchanged_metadata():
    previous = {
        "agenda_id": "agenda-29",
        "mail_id": "2026-W29:Spica:mail-1",
        "week": "2026-W29",
        "summary": "4SA chamber A 원복",
        "source_quote": "조건을 원복했습니다.",
        "state": "in_progress",
        "topic": "action",
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "source_doc_ids": ["chunk-29"],
        "created_at": "2026-07-13T00:00:00+00:00",
        "updated_at": "2026-07-13T00:00:00+00:00",
        "updated_week": "2026-W29",
    }
    first = apply_agenda_version(
        previous,
        None,
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )
    unchanged = apply_agenda_version(
        previous,
        first,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert unchanged["created_at"] == first["created_at"]
    assert unchanged["updated_at"] == first["updated_at"]
    assert unchanged["updated_week"] == first["updated_week"]
    assert unchanged["content_hash"] == first["content_hash"]


def test_apply_agenda_version_marks_correction_in_requested_week():
    previous = apply_agenda_version(
        {
            "agenda_id": "agenda-28",
            "mail_id": "2026-W28:Spica:mail-1",
            "week": "2026-W28",
            "summary": "4SA 원인 분석",
            "source_quote": "원인 분석 중입니다.",
            "state": "investigating",
            "topic": "yield",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "source_doc_ids": ["chunk-28"],
        },
        None,
        now=datetime(2026, 7, 7, tzinfo=UTC),
    )
    corrected = {
        **previous,
        "summary": "4SA chamber A 원인 확인",
        "state": "confirmed",
        "updated_week": "2026-W29",
    }

    versioned = apply_agenda_version(
        corrected,
        previous,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert versioned["created_at"] == previous["created_at"]
    assert versioned["updated_at"] == "2026-07-14T00:00:00+00:00"
    assert versioned["updated_week"] == "2026-W29"
    assert versioned["content_hash"] != previous["content_hash"]
~~~

- [ ] **Step 2: Run tests and verify they fail**

Run:

~~~bash
pytest tests/test_category_wiki_builder.py -q
~~~

Expected: FAIL because apply_agenda_version is not defined.

- [ ] **Step 3: Add strict mapping fields and minimal version helpers**

Add these mapping properties to agenda_index_definition:

~~~python
"updated_at": {"type": "date"},
"updated_week": {"type": "keyword"},
"content_hash": {"type": "keyword"},
"issue_id_source": {"type": "keyword"},
~~~

Add:

~~~python
AGENDA_HASH_FIELDS = (
    "mail_id",
    "week",
    "summary",
    "source_quote",
    "state",
    "topic",
    "target_paths",
    "candidate_paths",
    "source_doc_ids",
    "review_status",
)


def agenda_content_hash(document: dict[str, Any]) -> str:
    payload = {
        field: document.get(field)
        for field in AGENDA_HASH_FIELDS
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def apply_agenda_version(
    document: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    now: datetime,
    observed_week: str | None = None,
) -> dict[str, Any]:
    current = dict(document)
    current_hash = agenda_content_hash(current)
    previous_hash = (
        str(previous.get("content_hash") or agenda_content_hash(previous))
        if previous
        else None
    )
    if previous and current_hash == previous_hash:
        return {
            **current,
            "created_at": previous["created_at"],
            "updated_at": previous["updated_at"],
            "updated_week": previous["updated_week"],
            "content_hash": previous_hash,
        }
    timestamp = now.isoformat()
    change_week = str(
        observed_week
        or current.get("updated_week")
        or current["week"]
    )
    return {
        **current,
        "created_at": previous.get("created_at", timestamp) if previous else timestamp,
        "updated_at": timestamp,
        "updated_week": change_week,
        "content_hash": current_hash,
    }
~~~

Update build_agenda_documents to set issue_id_source to derived. Update
replace_mail_agendas to mget current Agenda IDs before deletion, accept the
pipeline's observed_week, call apply_agenda_version(...,
observed_week=observed_week) for every replacement, delete stale Agenda IDs for
that mail, and bulk-write only versioned documents. Thread the requested weekly
run's normalized week into observed_week; a backfill therefore records the
explicit requested week rather than the machine clock.

- [ ] **Step 4: Run focused tests**

Run:

~~~bash
pytest tests/test_category_wiki_builder.py -q
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add category_wiki_builder.py tests/test_category_wiki_builder.py
git commit -m "feat(wiki): version agenda evidence"
~~~

### Task 2: Add the OpenSearch Issue Ledger

**Files:**
- Create: wiki_issue_ledger.py
- Create: tests/test_wiki_issue_ledger.py

**Interfaces:**
- Consumes: list[dict[str, Any]] confirmed Agenda records, existing dict[str, WikiIssue], requested week, optional IssueSuggestionFn.
- Produces: resolve_issue_ledger(...) -> IssueLedgerResult, issue_index_definition() -> dict[str, Any], fetch_issue_ledger(client) -> dict[str, WikiIssue], save_issue_ledger(client, issues) -> int.

- [ ] **Step 1: Write failing state and identity tests**

Create tests/test_wiki_issue_ledger.py:

~~~python
from wiki_issue_ledger import (
    IssueSuggestion,
    resolve_issue_ledger,
)


def agenda(
    agenda_id: str,
    week: str,
    topic: str,
    state: str,
    summary: str,
    subject: str = "[Spica] 4SA 주간 수율",
):
    return {
        "agenda_id": agenda_id,
        "mail_id": "mail-" + agenda_id,
        "week": week,
        "updated_week": week,
        "topic": topic,
        "state": state,
        "summary": summary,
        "subject": subject,
        "review_status": "confirmed",
        "confidence": 0.99,
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "source_doc_ids": ["chunk-" + agenda_id],
    }


def test_reply_subject_events_form_one_issue_and_resolve():
    result = resolve_issue_ledger(
        [
            agenda("004", "2026-W28", "yield", "investigating", "수율 1.2%p 하락"),
            agenda("028", "2026-W29", "root_cause", "confirmed", "chamber A 원인 확인", "RE: [Spica] 4SA 주간 수율"),
            agenda("029", "2026-W29", "action", "in_progress", "조건 원복 후 재측정", "RE: [Spica] 4SA 주간 수율"),
            agenda("041", "2026-W30", "yield", "resolved", "수율 정상화", "RE: [Spica] 4SA 주간 수율"),
        ],
        {},
        as_of_week="2026-W30",
    )

    assert len(result.issues) == 1
    issue = next(iter(result.issues.values()))
    assert issue.current_status == "resolved"
    assert issue.agenda_ids == ["004", "028", "029", "041"]
    assert issue.resolved_week == "2026-W30"


def test_later_open_evidence_reopens_resolved_issue():
    resolved = resolve_issue_ledger(
        [
            agenda("004", "2026-W28", "yield", "open", "수율 하락"),
            agenda("041", "2026-W29", "yield", "resolved", "수율 정상화"),
        ],
        {},
        as_of_week="2026-W29",
    )
    reopened = resolve_issue_ledger(
        [agenda("052", "2026-W30", "yield", "open", "수율 재하락")],
        resolved.issues,
        as_of_week="2026-W30",
        suggest=lambda context: IssueSuggestion(
            agenda_id="052",
            issue_id=next(iter(resolved.issues)),
            decision="link",
            confidence="high",
            reason="same LOTCD yield issue reopened after resolution",
        ),
    )

    issue = next(iter(reopened.issues.values()))
    assert issue.current_status == "reopened"
    assert issue.reopened_count == 1


def test_ambiguous_suggestion_creates_provisional_issue_and_review():
    result = resolve_issue_ledger(
        [agenda("099", "2026-W30", "defect", "open", "Edge defect 증가", "공통 개선안")],
        {},
        as_of_week="2026-W30",
        suggest=lambda context: IssueSuggestion(
            agenda_id="099",
            issue_id=None,
            decision="review",
            confidence="low",
            reason="two active defect issues are plausible",
        ),
    )

    issue = next(iter(result.issues.values()))
    assert issue.review_required is True
    assert result.review_items == ["099: two active defect issues are plausible"]
~~~

- [ ] **Step 2: Run tests and verify they fail**

Run:

~~~bash
pytest tests/test_wiki_issue_ledger.py -q
~~~

Expected: FAIL because wiki_issue_ledger does not exist.

- [ ] **Step 3: Implement models, state derivation, and conservative matching**

Create wiki_issue_ledger.py with:

~~~python
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from typing import Any, Literal

from opensearchpy import OpenSearch, helpers
from pydantic import BaseModel, ConfigDict, Field


ISSUE_INDEX = os.getenv("WIKI_ISSUE_INDEX", "wiki_issue_ledger")
TERMINAL_STATES = {"resolved", "closed", "completed", "stable", "positive", "normal"}


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueStateEvent(LedgerModel):
    week: str
    agenda_id: str
    state: str
    event_type: Literal["created", "updated", "resolved", "reopened"]


class WikiIssue(LedgerModel):
    issue_id: str
    category_paths: list[str]
    subject_keys: list[str] = Field(default_factory=list)
    topic: str
    title: str
    current_status: Literal["ongoing", "resolved", "reopened"]
    agenda_ids: list[str]
    state_history: list[IssueStateEvent]
    first_seen_week: str
    last_updated_week: str
    resolved_week: str | None = None
    reopened_count: int = 0
    confidence: Literal["low", "medium", "high"] = "medium"
    review_required: bool = False


class IssueSuggestion(LedgerModel):
    agenda_id: str
    issue_id: str | None
    decision: Literal["link", "new", "review"]
    confidence: Literal["low", "medium", "high"]
    reason: str


class IssueLedgerResult(LedgerModel):
    issues: dict[str, WikiIssue]
    agenda_to_issue: dict[str, str]
    changed_issue_ids: list[str]
    review_items: list[str] = Field(default_factory=list)


IssueSuggestionFn = Callable[[dict[str, Any]], IssueSuggestion]


def normalize_subject(value: str) -> str:
    return re.sub(r"^(?:\s*(?:re|fw|fwd)\s*:\s*)+", "", value, flags=re.I).strip().casefold()


def category_tokens(agenda: dict[str, Any]) -> list[str]:
    return sorted(
        "/".join(
            value
            for value in (path["domain"], path.get("tech"), path.get("lotcd"))
            if value
        ).casefold()
        for path in agenda.get("target_paths", [])
    )


def provisional_issue_id(agenda: dict[str, Any]) -> str:
    payload = "|".join([
        *category_tokens(agenda),
        normalize_subject(str(agenda.get("subject", ""))),
        str(agenda["agenda_id"]),
    ])
    return "issue:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
~~~

Use this deterministic state-machine shape. Exact Agenda membership wins, then a
normalized reply subject seen in the current batch, then a high-confidence
structured suggestion. Anything ambiguous becomes a provisional review Issue:

~~~python
def resolve_issue_ledger(
    agendas: list[dict[str, Any]],
    existing: dict[str, WikiIssue],
    *,
    as_of_week: str,
    suggest: IssueSuggestionFn | None = None,
) -> IssueLedgerResult:
    issues = {
        issue_id: WikiIssue.model_validate(issue.model_dump())
        for issue_id, issue in existing.items()
    }
    agenda_to_issue = {
        agenda_id: issue_id
        for issue_id, issue in issues.items()
        for agenda_id in issue.agenda_ids
    }
    subject_to_issue = {
        subject_key: issue_id
        for issue_id, issue in issues.items()
        for subject_key in issue.subject_keys
    }
    changed: set[str] = set()
    review_items: list[str] = []

    for agenda in sorted(
        agendas,
        key=lambda item: (str(item["week"]), str(item["agenda_id"])),
    ):
        agenda_id = str(agenda["agenda_id"])
        if agenda_id in agenda_to_issue:
            continue
        subject_key = normalize_subject(str(agenda.get("subject", "")))
        issue_id = subject_to_issue.get(subject_key) if subject_key else None
        suggestion = None
        if issue_id is None and suggest is not None:
            suggestion = suggest(
                {
                    "agenda": agenda,
                    "candidate_issues": [
                        item.model_dump(mode="json")
                        for item in issues.values()
                        if set(item.category_paths) & set(category_tokens(agenda))
                    ],
                }
            )
            if (
                suggestion.decision == "link"
                and suggestion.confidence == "high"
                and suggestion.issue_id in issues
            ):
                issue_id = suggestion.issue_id

        review_required = bool(
            suggestion
            and (
                suggestion.decision == "review"
                or (
                    suggestion.decision == "link"
                    and suggestion.confidence != "high"
                )
            )
        )
        if issue_id is None:
            issue_id = provisional_issue_id(agenda)
            if issue_id not in issues:
                issues[issue_id] = WikiIssue(
                    issue_id=issue_id,
                    category_paths=category_tokens(agenda),
                    subject_keys=[subject_key] if subject_key else [],
                    topic=str(agenda.get("topic", "other")),
                    title=str(agenda.get("summary", agenda_id)),
                    current_status="ongoing",
                    agenda_ids=[],
                    state_history=[],
                    first_seen_week=str(agenda["week"]),
                    last_updated_week=str(agenda["week"]),
                    confidence=(
                        suggestion.confidence if suggestion else "medium"
                    ),
                    review_required=review_required,
                )
        issue = issues[issue_id]
        terminal = str(agenda.get("state", "")).casefold() in TERMINAL_STATES
        reopened = issue.current_status == "resolved" and not terminal
        event_type = (
            "created"
            if not issue.state_history
            else "resolved"
            if terminal
            else "reopened"
            if reopened
            else "updated"
        )
        current_status = (
            "resolved"
            if terminal
            else "reopened"
            if reopened or issue.current_status == "reopened"
            else "ongoing"
        )
        paths = sorted({*issue.category_paths, *category_tokens(agenda)})
        issues[issue_id] = issue.model_copy(
            update={
                "category_paths": paths,
                "subject_keys": sorted(
                    {
                        *issue.subject_keys,
                        *([subject_key] if subject_key else []),
                    }
                ),
                "current_status": current_status,
                "agenda_ids": [*issue.agenda_ids, agenda_id],
                "state_history": [
                    *issue.state_history,
                    IssueStateEvent(
                        week=str(agenda["week"]),
                        agenda_id=agenda_id,
                        state=str(agenda.get("state", "")),
                        event_type=event_type,
                    ),
                ],
                "last_updated_week": str(
                    agenda.get("updated_week") or agenda["week"]
                ),
                "resolved_week": (
                    str(agenda["week"]) if terminal else issue.resolved_week
                ),
                "reopened_count": issue.reopened_count + int(reopened),
                "review_required": issue.review_required or review_required,
            }
        )
        agenda_to_issue[agenda_id] = issue_id
        if subject_key:
            subject_to_issue[subject_key] = issue_id
        changed.add(issue_id)
        if review_required and suggestion is not None:
            review_items.append(agenda_id + ": " + suggestion.reason)

    return IssueLedgerResult(
        issues=issues,
        agenda_to_issue=agenda_to_issue,
        changed_issue_ids=sorted(changed),
        review_items=review_items,
    )
~~~

- [ ] **Step 4: Add mapping and persistence tests**

Add:

~~~python
from wiki_issue_ledger import issue_index_definition


def test_issue_index_is_structured_without_embedding():
    properties = issue_index_definition()["mappings"]["properties"]
    assert properties["state_history"]["type"] == "nested"
    assert properties["agenda_ids"]["type"] == "keyword"
    assert "embedding" not in properties
~~~

Add the structured index and persistence functions. There is deliberately no
vector field:

~~~python
def issue_index_definition() -> dict[str, Any]:
    return {
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "issue_id": {"type": "keyword"},
                "category_paths": {"type": "keyword"},
                "subject_keys": {"type": "keyword"},
                "topic": {"type": "keyword"},
                "title": {"type": "text"},
                "current_status": {"type": "keyword"},
                "agenda_ids": {"type": "keyword"},
                "state_history": {
                    "type": "nested",
                    "properties": {
                        "week": {"type": "keyword"},
                        "agenda_id": {"type": "keyword"},
                        "state": {"type": "keyword"},
                        "event_type": {"type": "keyword"},
                    },
                },
                "first_seen_week": {"type": "keyword"},
                "last_updated_week": {"type": "keyword"},
                "resolved_week": {"type": "keyword"},
                "reopened_count": {"type": "integer"},
                "confidence": {"type": "keyword"},
                "review_required": {"type": "boolean"},
            },
        }
    }


def fetch_issue_ledger(
    client: OpenSearch,
    *,
    page_size: int = 500,
) -> dict[str, WikiIssue]:
    after = None
    issues: dict[str, WikiIssue] = {}
    while True:
        body: dict[str, Any] = {
            "size": page_size,
            "query": {"match_all": {}},
            "sort": [{"_id": "asc"}],
        }
        if after is not None:
            body["search_after"] = after
        response = client.search(index=ISSUE_INDEX, body=body)
        hits = response.get("hits", {}).get("hits", [])
        for hit in hits:
            issue = WikiIssue.model_validate(hit["_source"])
            issues[issue.issue_id] = issue
        if len(hits) < page_size:
            return issues
        after = hits[-1]["sort"]


def save_issue_ledger(
    client: OpenSearch,
    issues: dict[str, WikiIssue],
) -> int:
    actions = [
        {
            "_index": ISSUE_INDEX,
            "_id": issue_id,
            "_source": issues[issue_id].model_dump(mode="json"),
        }
        for issue_id in sorted(issues)
    ]
    if actions:
        helpers.bulk(client, actions)
        client.indices.refresh(index=ISSUE_INDEX)
    return len(actions)
~~~

- [ ] **Step 5: Run tests**

Run:

~~~bash
pytest tests/test_wiki_issue_ledger.py -q
~~~

Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add wiki_issue_ledger.py tests/test_wiki_issue_ledger.py
git commit -m "feat(wiki): add issue ledger"
~~~

### Task 3: Replace Fixed Narrative Fields with a Dynamic Merge Contract

**Files:**
- Modify: integrated_wiki_builder.py:57-259, 423-512, 1107-1133
- Modify: knowledge_models.py:198-245
- Modify: tests/test_integrated_wiki_builder.py

**Interfaces:**
- Consumes: existing current Markdown, supported Claims, canonical Issues, weekly Agenda delta, child digests.
- Produces: MergedWikiDocument, validate_merged_document(document, context) -> list[CitationMapEntry], render_current_body(document) -> str.

- [ ] **Step 1: Replace fixed-section tests with dynamic-document tests**

Delete test_narrative_draft_has_the_approved_sections and add:

~~~python
from integrated_wiki_builder import (
    MergeValidationContext,
    MergedWikiDocument,
    validate_merged_document,
)


def test_dynamic_document_accepts_content_specific_headings():
    document = MergedWikiDocument(
        title="4SA",
        current_body_markdown=(
            "## Chamber A 편차와 수율 하락\n\n"
            "수율 하락 원인은 chamber A 편차로 확인됐다. [mail:mail-28]\n\n"
            "## 조건 원복 후 검증\n\n"
            "조건 원복 후 재측정 중이다. [mail:mail-29]"
        ),
        used_claim_ids=["claim-root-cause", "claim-action"],
        used_issue_ids=["issue-4sa-yield"],
        weekly_delta="원인 확인과 조건 원복이 진행됐다. [mail:mail-28][mail:mail-29]",
        confidence="high",
        review_items=[],
    )
    context = MergeValidationContext(
        evidence_by_mail={
            "mail-28": ["agenda-28"],
            "mail-29": ["agenda-29"],
        },
        source_docs_by_mail={
            "mail-28": ["chunk-28"],
            "mail-29": ["chunk-29"],
        },
        required_claim_ids={"claim-root-cause", "claim-action"},
        required_issue_ids={"issue-4sa-yield"},
        resolved_issue_ids=set(),
        reopened_issue_ids=set(),
        stale_claim_texts=[],
    )

    citations = validate_merged_document(document, context)

    assert [item.mail_id for item in citations] == ["mail-28", "mail-29"]
    assert {item for citation in citations for item in citation.source_doc_ids} == {
        "chunk-28",
        "chunk-29",
    }


def test_dynamic_document_rejects_missing_claim_coverage():
    document = MergedWikiDocument(
        title="4SA",
        current_body_markdown="## 상태\n\n조건 원복 중이다. [mail:mail-29]",
        used_claim_ids=["claim-action"],
        used_issue_ids=["issue-4sa-yield"],
        weekly_delta="조건 원복 중이다. [mail:mail-29]",
        confidence="medium",
        review_items=[],
    )
    context = MergeValidationContext(
        evidence_by_mail={"mail-29": ["agenda-29"]},
        source_docs_by_mail={"mail-29": ["chunk-29"]},
        required_claim_ids={"claim-root-cause", "claim-action"},
        required_issue_ids={"issue-4sa-yield"},
        resolved_issue_ids=set(),
        reopened_issue_ids=set(),
        stale_claim_texts=[],
    )

    with pytest.raises(NarrativeValidationError, match="missing claim coverage"):
        validate_merged_document(document, context)
~~~

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py -q
~~~

Expected: FAIL because MergedWikiDocument and MergeValidationContext are undefined.

- [ ] **Step 3: Replace NarrativeDraft and two-call generator**

Replace NarrativeDraft with:

~~~python
class MergedWikiDocument(WikiModel):
    title: str
    current_body_markdown: str
    used_claim_ids: list[str] = Field(default_factory=list)
    used_issue_ids: list[str] = Field(default_factory=list)
    weekly_delta: str
    confidence: Literal["low", "medium", "high"]
    review_items: list[str] = Field(default_factory=list)


class MergeValidationContext(WikiModel):
    evidence_by_mail: dict[str, list[str]]
    source_docs_by_mail: dict[str, list[str]]
    required_claim_ids: set[str]
    required_issue_ids: set[str]
    resolved_issue_ids: set[str]
    reopened_issue_ids: set[str]
    stale_claim_texts: list[str]


class WeeklyHistoryEntry(WikiModel):
    week: str
    body_markdown: str
    source_mail_ids: list[str] = Field(default_factory=list)
    agenda_ids: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)


class CitationMapEntry(WikiModel):
    mail_id: str
    agenda_ids: list[str]
    source_doc_ids: list[str]
    used_in_sections: list[str]
    category_paths: list[str]


class ChildDigest(WikiModel):
    category_id: str
    canonical_id: str
    current_body_markdown: str
    weekly_delta: str
    used_claim_ids: list[str]
    used_issue_ids: list[str]
    citation_map: list[CitationMapEntry]
    source_hash: str
    confidence: Literal["low", "medium", "high"]
~~~

Replace the two-call generator with this single structured page merger:

~~~python
MERGE_SYSTEM_PROMPT = """당신은 반도체 수율 Wiki 편집자입니다.
기존 현재 문서와 이번 주 근거를 병합해 하나의 완결된 최신 문서를 작성하십시오.
내용에 맞는 동적 H2 목차를 사용하고 고정 목차를 강제하지 마십시오.
제공된 Claim과 Issue를 빠짐없이 반영하되, 사실 문단마다 [mail:<mail_id>]를
붙이십시오. 더 최신의 명확한 근거가 있으면 현재 본문은 최신 상태로 바꾸고,
모호한 충돌은 양쪽 주장을 각각 인용해 유지하십시오. weekly_delta에는 이번 주
변경점만 쓰십시오. 과거 weekly_history는 입력일 뿐이며 다시 쓰지 마십시오.
메일에 없는 원인, 수치, 담당자, 해결 여부를 만들지 마십시오.
validation_feedback이 있으면 유효한 인용은 유지하고 지적된 오류만 수정하십시오."""


def build_page_merger() -> Callable[[dict[str, Any]], MergedWikiDocument]:
    from langchain_openai import ChatOpenAI

    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
        extra_body=_llm_extra_body(),
    )
    runnable = llm.with_structured_output(
        MergedWikiDocument,
        method="function_calling",
    )

    def merge(context: dict[str, Any]) -> MergedWikiDocument:
        if (
            not context["evidence_by_mail"]
            and not context["previous_current_body_markdown"].strip()
        ):
            return MergedWikiDocument(
                title=context["node"]["title"],
                current_body_markdown="## 문서 범위",
                used_claim_ids=[],
                used_issue_ids=[],
                weekly_delta="",
                confidence="low",
                review_items=[],
            )
        return invoke_structured(
            runnable,
            [
                {"role": "system", "content": MERGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ],
        )

    return merge
~~~

- [ ] **Step 4: Implement dynamic semantic validation**

Implement validate_merged_document with these exact checks:

~~~python
def validate_merged_document(
    document: MergedWikiDocument,
    context: MergeValidationContext,
) -> list[CitationMapEntry]:
    missing_claims = context.required_claim_ids - set(document.used_claim_ids)
    if missing_claims:
        raise NarrativeValidationError(
            "missing claim coverage: " + ", ".join(sorted(missing_claims))
        )
    missing_issues = context.required_issue_ids - set(document.used_issue_ids)
    if missing_issues:
        raise NarrativeValidationError(
            "missing issue coverage: " + ", ".join(sorted(missing_issues))
        )
    headings = re.findall(r"^##\s+(.+)$", document.current_body_markdown, re.M)
    heading_ids = [
        re.sub(r"[^\w가-힣-]", "", re.sub(r"\s+", "-", heading.casefold()))
        for heading in headings
    ]
    if not headings or len(heading_ids) != len(set(heading_ids)):
        raise NarrativeValidationError("current body requires unique dynamic headings")
    for stale in context.stale_claim_texts:
        if stale and stale in document.current_body_markdown:
            raise NarrativeValidationError("stale claim remains in current body")
    used: dict[str, set[str]] = {}
    sections = [
        *_factual_units_by_heading(document.current_body_markdown),
        *(
            ("주차별 업데이트 이력", unit)
            for unit in _factual_units(document.weekly_delta)
        ),
    ]
    for section, unit in sections:
        mail_ids = MAIL_CITATION.findall(unit)
        if not mail_ids:
            raise NarrativeValidationError("uncited factual unit")
        for mail_id in mail_ids:
            if mail_id not in context.evidence_by_mail:
                raise NarrativeValidationError("unknown mail citation: " + mail_id)
            if not context.source_docs_by_mail.get(mail_id):
                raise NarrativeValidationError("citation has no raw source: " + mail_id)
            used.setdefault(mail_id, set()).add(section)
    cited_claim_ids = {
        "claim:" + agenda_id
        for mail_id in used
        for agenda_id in context.evidence_by_mail[mail_id]
    }
    uncited_claims = context.required_claim_ids - cited_claim_ids
    if uncited_claims:
        raise NarrativeValidationError(
            "claims declared used but not cited: "
            + ", ".join(sorted(uncited_claims))
        )
    return [
        CitationMapEntry(
            mail_id=mail_id,
            agenda_ids=context.evidence_by_mail[mail_id],
            source_doc_ids=context.source_docs_by_mail[mail_id],
            used_in_sections=sorted(sections),
            category_paths=[],
        )
        for mail_id, sections in sorted(used.items())
    ]
~~~

Add the heading-aware factual-unit splitter used above. It never invents fixed
section names; it reports the actual H2 label enclosing each paragraph:

~~~python
def _factual_units_by_heading(markdown: str) -> list[tuple[str, str]]:
    section = "본문"
    buffer: list[str] = []
    result: list[tuple[str, str]] = []

    def flush() -> None:
        if not buffer:
            return
        result.extend((section, unit) for unit in _factual_units("\n".join(buffer)))
        buffer.clear()

    for line in markdown.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            flush()
            section = heading.group(1)
        else:
            buffer.append(line)
    flush()
    return result
~~~

Keep explicit resolved and reopened evidence checks in the deterministic Issue validation path. render_current_body now returns document.current_body_markdown unchanged.
Replace _merge_citation_maps with the source-complete variant below. A historical
mail without Agenda or raw-source IDs raises instead of producing a broken
citation:

~~~python
def _merge_citation_maps(
    current: list[CitationMapEntry],
    previous: list[CitationMapEntry],
    history: list[WeeklyHistoryEntry],
) -> list[CitationMapEntry]:
    history_sections: dict[str, set[str]] = {}
    for entry in history:
        for mail_id in entry.source_mail_ids:
            history_sections.setdefault(mail_id, set()).add(
                _normalize_week(entry.week)
            )
    merged: dict[str, dict[str, set[str]]] = {}

    def add(citation: CitationMapEntry, sections: list[str]) -> None:
        record = merged.setdefault(
            citation.mail_id,
            {
                "agenda_ids": set(),
                "source_doc_ids": set(),
                "used_in_sections": set(),
            },
        )
        record["agenda_ids"].update(citation.agenda_ids)
        record["source_doc_ids"].update(citation.source_doc_ids)
        record["used_in_sections"].update(sections)

    for citation in current:
        add(citation, citation.used_in_sections)
    for citation in previous:
        if citation.mail_id in history_sections:
            add(citation, sorted(history_sections[citation.mail_id]))

    missing = sorted(set(history_sections) - set(merged))
    if missing:
        raise NarrativeValidationError(
            "retained history has no citation mapping: " + ", ".join(missing)
        )
    incomplete = sorted(
        mail_id
        for mail_id, record in merged.items()
        if not record["agenda_ids"] or not record["source_doc_ids"]
    )
    if incomplete:
        raise NarrativeValidationError(
            "citation evidence is incomplete: " + ", ".join(incomplete)
        )
    return [
        CitationMapEntry(
            mail_id=mail_id,
            agenda_ids=sorted(record["agenda_ids"]),
            source_doc_ids=sorted(record["source_doc_ids"]),
            used_in_sections=sorted(record["used_in_sections"]),
            category_paths=[],
        )
        for mail_id, record in sorted(merged.items())
    ]
~~~

- [ ] **Step 5: Extend API-compatible Pydantic models additively**

Update knowledge_models.py:

~~~python
class WeeklyHistoryRecord(StrictModel):
    week: str
    body_markdown: str
    source_mail_ids: list[str] = Field(default_factory=list)
    agenda_ids: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)


class WikiCitationRecord(StrictModel):
    mail_id: str
    agenda_ids: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
    used_in_sections: list[str] = Field(default_factory=list)
    category_paths: list[str] = Field(default_factory=list)
~~~

Add issue_ids, schema_version, and generation_strategy defaults to CategoryWikiPage so legacy pages remain readable:

~~~python
issue_ids: list[str] = Field(default_factory=list)
schema_version: int = 1
generation_strategy: Literal["legacy", "fixed_sections", "incremental_merge"] = "legacy"
~~~

- [ ] **Step 6: Run tests**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py tests/test_knowledge_api.py -q
~~~

Expected: PASS after updating affected fixtures to include additive defaults.

- [ ] **Step 7: Commit**

~~~bash
git add integrated_wiki_builder.py knowledge_models.py tests/test_integrated_wiki_builder.py tests/test_knowledge_api.py
git commit -m "refactor(wiki): use dynamic merge documents"
~~~

### Task 4: Select Weekly Delta and Affected Taxonomy Nodes

**Files:**
- Modify: integrated_wiki_builder.py
- Modify: tests/test_integrated_wiki_builder.py

**Interfaces:**
- Consumes: all normalized Agenda records, requested week, previous page hashes, taxonomy nodes.
- Produces: select_weekly_delta(agendas, week) -> list[dict[str, Any]], affected_node_ids(taxonomy, delta, rebuild_all=False) -> set[str].

- [ ] **Step 1: Write failing selection tests**

Add:

~~~python
from integrated_wiki_builder import affected_node_ids, select_weekly_delta


def test_weekly_delta_contains_new_and_corrected_agendas_only():
    agendas = [
        {**one_open_agenda(), "week": "2026-W28", "updated_week": "2026-W28"},
        {
            **one_open_agenda(),
            "agenda_id": "agenda-corrected",
            "week": "2026-W28",
            "updated_week": "2026-W29",
        },
        {
            **one_open_agenda(),
            "agenda_id": "agenda-new",
            "week": "2026-W29",
            "updated_week": "2026-W29",
        },
    ]

    delta = select_weekly_delta(agendas, "2026-W29")

    assert [item["agenda_id"] for item in delta] == [
        "agenda-corrected",
        "agenda-new",
    ]


def test_lotcd_delta_affects_only_lotcd_and_ancestors(taxonomy):
    ids = affected_node_ids(
        taxonomy,
        [
            {
                **one_open_agenda(),
                "target_paths": [
                    {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
                ],
            }
        ],
    )

    assert ids == {"lotcd:4sa", "tech:dram:spica", "domain:dram"}


def test_no_delta_affects_no_pages(taxonomy):
    assert affected_node_ids(taxonomy, []) == set()
~~~

- [ ] **Step 2: Run tests and verify failure**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py -q
~~~

Expected: FAIL because selection functions do not exist.

- [ ] **Step 3: Implement exact delta and ancestor expansion**

Add:

~~~python
def select_weekly_delta(
    agendas: list[dict[str, Any]],
    week: str,
) -> list[dict[str, Any]]:
    normalized = _normalize_week(week)
    return sorted(
        (
            agenda
            for agenda in agendas
            if agenda.get("review_status", "confirmed") == "confirmed"
            and (
                _normalize_week(agenda.get("week")) == normalized
                or _normalize_week(agenda.get("updated_week")) == normalized
            )
        ),
        key=lambda agenda: str(agenda["agenda_id"]),
    )


def affected_node_ids(
    taxonomy: TaxonomyDocument,
    delta: list[dict[str, Any]],
    *,
    rebuild_all: bool = False,
) -> set[str]:
    nodes = category_nodes(taxonomy)
    if rebuild_all:
        return {node.id for node in nodes}
    selected: set[str] = set()
    for agenda in delta:
        for node in nodes:
            if agenda_matches_node(agenda, node):
                selected.add(node.id)
                if node.level == "lotcd":
                    selected.add("tech:" + node.domain.casefold() + ":" + str(node.tech).casefold())
                    selected.add("domain:" + node.domain.casefold())
                elif node.level == "tech":
                    selected.add("domain:" + node.domain.casefold())
    return selected
~~~

Use taxonomy lookup IDs rather than string reconstruction when implementation reveals a configured ID differs from the lowercase name. The returned IDs must always be actual CategoryNode.id values.

- [ ] **Step 4: Run focused tests**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py -q
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add integrated_wiki_builder.py tests/test_integrated_wiki_builder.py
git commit -m "feat(wiki): select affected page chain"
~~~

### Task 5: Build Pages Bottom-Up with One Incremental Merge Call

**Files:**
- Modify: integrated_wiki_builder.py:490-1105
- Modify: tests/test_integrated_wiki_builder.py

**Interfaces:**
- Consumes: taxonomy, all Agenda evidence, weekly delta, IssueLedgerResult, previous pages, MergeFn, rebuild_all.
- Produces: build_incremental_pages(...) -> BuildResult with pages, failures, skipped node IDs, pending parent IDs; on_page callback receives each validated page immediately.

- [ ] **Step 1: Write a two-week propagation and call-count test**

Add:

~~~python
from typing import Any

from category_wiki_builder import AGENDA_INDEX, PAGE_INDEX, SOURCE_INDEX
from integrated_wiki_builder import (
    MergedWikiDocument,
    build_incremental_pages,
)
from wiki_issue_ledger import (
    IssueLedgerResult,
    IssueStateEvent,
    WikiIssue,
)


def complete_page_fixture(
    category_id: str,
    domain: str,
    tech: str | None,
    lotcd: str | None,
) -> dict[str, Any]:
    level = "lotcd" if lotcd else "tech" if tech else "domain"
    title = lotcd or tech or domain
    canonical_id = "/".join(
        value.casefold() for value in (domain, tech, lotcd) if value
    )
    return {
        "category_id": category_id,
        "page_kind": "latest",
        "doc_type": "canonical",
        "canonical_id": canonical_id,
        "level": level,
        "domain": domain,
        "tech": tech,
        "lotcd": lotcd,
        "title": title,
        "product": None,
        "fab_id": lotcd[:1] if lotcd else None,
        "aliases": [],
        "as_of_week": "2026-W28",
        "current_body_markdown": "## 기존 상태\n\n수율 분석 중이다. [mail:mail-28]",
        "weekly_history": [],
        "body_markdown": "## 기존 상태\n\n수율 분석 중이다. [mail:mail-28]",
        "citation_map": [
            {
                "mail_id": "mail-28",
                "agenda_ids": ["agenda-28"],
                "source_doc_ids": ["chunk-28"],
                "used_in_sections": ["기존 상태"],
                "category_paths": [canonical_id],
            }
        ],
        "child_page_ids": [],
        "confidence": "medium",
        "agenda_count": 1,
        "open_issue_ids": ["issue:4sa-yield"],
        "resolved_issue_ids": [],
        "open_issue_count": 1,
        "resolved_issue_count": 0,
        "contradictions": [],
        "generation_review_items": [],
        "review_agenda_ids": [],
        "source_agenda_ids": ["agenda-28"],
        "source_doc_ids": ["chunk-28"],
        "source_hash": "old",
        "taxonomy_version": 1,
        "schema_version": 2,
        "generation_strategy": "incremental_merge",
        "generated_at": "2026-07-07T00:00:00+00:00",
        "updated_at": "2026-07-07T00:00:00+00:00",
    }


def w29_4sa_agenda() -> dict[str, Any]:
    return {
        **one_open_agenda(),
        "agenda_id": "agenda-29",
        "mail_id": "mail-29",
        "week": "2026-W29",
        "updated_week": "2026-W29",
        "summary": "chamber A 원인 확인 후 조건 원복",
        "topic": "root_cause",
        "state": "in_progress",
        "source_doc_ids": ["chunk-29"],
        "content_hash": "w29",
    }


def previous_page_chain() -> dict[str, dict[str, Any]]:
    return {
        "lotcd:4sa": complete_page_fixture(
            "lotcd:4sa", "DRAM", "Spica", "4SA"
        ),
        "tech:dram:spica": complete_page_fixture(
            "tech:dram:spica", "DRAM", "Spica", None
        ),
        "domain:dram": complete_page_fixture(
            "domain:dram", "DRAM", None, None
        ),
    }


def issue_result_for(agendas: list[dict[str, Any]]) -> IssueLedgerResult:
    issue_id = "issue:4sa-yield"
    agenda_ids = [str(item["agenda_id"]) for item in agendas]
    events = [
        IssueStateEvent(
            week=str(item["week"]),
            agenda_id=str(item["agenda_id"]),
            state=str(item["state"]),
            event_type="created" if index == 0 else "updated",
        )
        for index, item in enumerate(agendas)
    ]
    issue = WikiIssue(
        issue_id=issue_id,
        category_paths=["dram/spica/4sa"],
        topic="yield",
        title="4SA 수율 하락",
        current_status="ongoing",
        agenda_ids=agenda_ids,
        state_history=events,
        first_seen_week=str(agendas[0]["week"]),
        last_updated_week=str(agendas[-1]["updated_week"]),
    )
    return IssueLedgerResult(
        issues={issue_id: issue},
        agenda_to_issue={agenda_id: issue_id for agenda_id in agenda_ids},
        changed_issue_ids=[issue_id],
        review_items=[],
    )


def valid_dynamic_document(context: dict[str, Any]) -> MergedWikiDocument:
    mail_ids = sorted(context["evidence_by_mail"])
    citations = "".join("[mail:" + mail_id + "]" for mail_id in mail_ids)
    body = "## 이번 주 상태\n\n"
    if citations:
        body += "근거가 반영됐다. " + citations
    return MergedWikiDocument(
        title=context["node"]["title"],
        current_body_markdown=body,
        used_claim_ids=sorted(context["required_claim_ids"]),
        used_issue_ids=sorted(context["required_issue_ids"]),
        weekly_delta=(
            "이번 주 근거가 반영됐다. " + citations
            if context["has_weekly_change"]
            else ""
        ),
        confidence="high" if citations else "low",
        review_items=[],
    )


def test_w29_merges_only_4sa_spica_dram_and_keeps_dynamic_headings(taxonomy):
    calls = []
    saved = []
    agendas = [
        {
            **one_open_agenda(),
            "updated_week": "2026-W28",
            "content_hash": "w28",
        },
        {
            **one_open_agenda(),
            "agenda_id": "agenda-29",
            "mail_id": "mail-29",
            "week": "2026-W29",
            "updated_week": "2026-W29",
            "summary": "chamber A 원인 확인 후 조건 원복",
            "topic": "root_cause",
            "state": "in_progress",
            "source_doc_ids": ["chunk-29"],
            "content_hash": "w29",
        },
    ]
    previous = {
        "lotcd:4sa": {
            **complete_page_fixture("lotcd:4sa", "DRAM", "Spica", "4SA"),
            "current_body_markdown": "## 수율 하락\n\n원인 분석 중이다. [mail:mail-28]",
            "source_hash": "old-lot",
        },
        "tech:dram:spica": {
            **complete_page_fixture("tech:dram:spica", "DRAM", "Spica", None),
            "source_hash": "old-tech",
        },
        "domain:dram": {
            **complete_page_fixture("domain:dram", "DRAM", None, None),
            "source_hash": "old-domain",
        },
    }

    def merge(context):
        node_id = context["node"]["id"]
        calls.append(node_id)
        mail_ids = sorted(context["evidence_by_mail"])
        citations = "".join("[mail:" + mail_id + "]" for mail_id in mail_ids)
        return MergedWikiDocument(
            title=context["node"]["title"],
            current_body_markdown=(
                "## Chamber A 원인과 조건 원복\n\n"
                "원인 확인 후 조건 원복을 진행 중이다. " + citations
            ),
            used_claim_ids=sorted(context["required_claim_ids"]),
            used_issue_ids=sorted(context["required_issue_ids"]),
            weekly_delta="이번 주 원인과 조치가 갱신됐다. " + citations,
            confidence="high",
            review_items=[],
        )

    result = build_incremental_pages(
        taxonomy,
        agendas,
        previous,
        issue_result_for(agendas),
        as_of_week="2026-W29",
        merge=merge,
        on_page=saved.append,
    )

    assert calls == ["lotcd:4sa", "tech:dram:spica", "domain:dram"]
    assert [page["category_id"] for page in saved] == calls
    assert not result.failures
    assert "## 개요" not in saved[0]["current_body_markdown"]
    assert saved[0]["weekly_history"][0]["week"] == "2026-W29"
~~~

- [ ] **Step 2: Write failure-resume and no-change tests**

Add:

~~~python
def test_parent_failure_keeps_saved_child_and_marks_ancestor_pending(taxonomy):
    saved = []

    def first_merge(context):
        if context["node"]["id"] == "tech:dram:spica":
            raise RuntimeError("provider timeout")
        return valid_dynamic_document(context)

    agenda = w29_4sa_agenda()
    issue_result = issue_result_for([agenda])
    first = build_incremental_pages(
        taxonomy,
        [agenda],
        previous_page_chain(),
        issue_result,
        as_of_week="2026-W29",
        merge=first_merge,
        on_page=saved.append,
    )

    assert [page["category_id"] for page in saved] == ["lotcd:4sa"]
    assert first.failures == {"tech:dram:spica": "provider timeout"}
    assert first.pending == {"domain:dram": "required child failed"}

    resumed_previous = previous_page_chain()
    resumed_previous["lotcd:4sa"] = saved[0]
    resumed_calls = []

    def resumed_merge(context):
        resumed_calls.append(context["node"]["id"])
        return valid_dynamic_document(context)

    second = build_incremental_pages(
        taxonomy,
        [agenda],
        resumed_previous,
        issue_result,
        as_of_week="2026-W29",
        merge=resumed_merge,
    )

    assert resumed_calls == ["tech:dram:spica", "domain:dram"]
    assert second.skipped == ["lotcd:4sa"]
    assert second.failures == {}
    assert second.pending == {}


def test_category_without_delta_gets_no_history_entry(taxonomy):
    def unexpected_merge(context):
        pytest.fail("merge must not run when no category is affected")

    result = build_incremental_pages(
        taxonomy,
        [],
        previous_page_chain(),
        IssueLedgerResult(
            issues={},
            agenda_to_issue={},
            changed_issue_ids=[],
            review_items=[],
        ),
        as_of_week="2026-W29",
        merge=unexpected_merge,
    )

    assert result.pages == []
    assert result.failures == {}
    assert result.pending == {}
    assert result.skipped == []
~~~

- [ ] **Step 3: Run tests and verify failure**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py -q
~~~

Expected: FAIL because build_incremental_pages is undefined.

- [ ] **Step 4: Implement context assembly and bottom-up orchestration**

Replace build_integrated_pages with build_incremental_pages using this control shape:

~~~python
@dataclass
class BuildResult:
    pages: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


MergeFn = Callable[[dict[str, Any]], MergedWikiDocument]


def build_incremental_pages(
    taxonomy: TaxonomyDocument,
    agendas: list[dict[str, Any]],
    previous_pages: dict[str, dict[str, Any]],
    issue_result: IssueLedgerResult,
    *,
    as_of_week: str,
    merge: MergeFn,
    rebuild_all: bool = False,
    on_page: Callable[[dict[str, Any]], None] | None = None,
) -> BuildResult:
    week = _normalize_week(as_of_week)
    eligible_agendas = [
        agenda
        for agenda in agendas
        if not agenda.get("week")
        or _normalize_week(agenda["week"]) <= week
    ]
    delta = select_weekly_delta(eligible_agendas, week)
    selected_ids = affected_node_ids(taxonomy, delta, rebuild_all=rebuild_all)
    compatibility_pages = {
        page["category_id"]: page
        for page in build_page_documents(
            taxonomy,
            eligible_agendas,
            as_of_week=week,
        )
    }
    order = {"lotcd": 0, "tech": 1, "domain": 2}
    nodes = sorted(
        category_nodes(taxonomy),
        key=lambda node: (order[node.level], node.id),
    )
    result = BuildResult()
    digests: dict[str, ChildDigest] = {}
    for node in nodes:
        if node.id not in selected_ids:
            continue
        children = [
            child
            for child in _direct_children(node, nodes)
            if child.id in selected_ids
        ]
        failed_children = [
            child.id
            for child in children
            if child.id in result.failures or child.id in result.pending
        ]
        if failed_children:
            result.pending[node.id] = "required child failed"
            continue
        try:
            context = build_merge_context(
                node=node,
                week=week,
                agendas=eligible_agendas,
                delta=delta,
                previous={} if rebuild_all else previous_pages.get(node.id, {}),
                issues=issue_result.issues,
                agenda_to_issue=issue_result.agenda_to_issue,
                child_digests=[digests[child.id] for child in children],
                base_page=compatibility_pages[node.id],
            )
            source_hash = merge_source_hash(context)
            previous = previous_pages.get(node.id, {})
            if not rebuild_all and previous.get("source_hash") == source_hash:
                result.skipped.append(node.id)
                digests[node.id] = child_digest_from_page(previous)
                continue
            document, citations = generate_with_semantic_retry(
                lambda retry_context: merge(retry_context),
                lambda candidate: validate_merged_document(
                    candidate,
                    retry_context_to_validation_context(context),
                ),
                context,
            )
            page = build_page_from_merge(
                node,
                document,
                citations,
                context,
                previous,
                source_hash,
            )
            result.pages.append(page)
            digests[node.id] = child_digest_from_page(page)
            if on_page:
                on_page(page)
        except Exception as exc:
            result.failures[node.id] = str(exc)
    return result
~~~

Use these focused helpers. Parent context contains direct Agenda evidence and only
validated child digests; unvalidated child output is never passed upward:

~~~python
import hashlib
from datetime import UTC, datetime


def build_merge_context(
    *,
    node: CategoryNode,
    week: str,
    agendas: list[dict[str, Any]],
    delta: list[dict[str, Any]],
    previous: dict[str, Any],
    issues: dict[str, WikiIssue],
    agenda_to_issue: dict[str, str],
    child_digests: list[ChildDigest],
    base_page: dict[str, Any],
) -> dict[str, Any]:
    direct = direct_agendas_for_node(node, agendas)
    direct_ids = {str(item["agenda_id"]) for item in direct}
    direct_delta = [
        item for item in delta if str(item["agenda_id"]) in direct_ids
    ]
    evidence_by_mail: dict[str, set[str]] = {}
    source_docs_by_mail: dict[str, set[str]] = {}
    for agenda in direct:
        mail_id = str(agenda["mail_id"])
        evidence_by_mail.setdefault(mail_id, set()).add(str(agenda["agenda_id"]))
        source_docs_by_mail.setdefault(mail_id, set()).update(
            str(item) for item in agenda.get("source_doc_ids", [])
        )
    previous_citations = [
        item
        if isinstance(item, CitationMapEntry)
        else CitationMapEntry.model_validate(item)
        for item in previous.get("citation_map", [])
    ]
    for citation in previous_citations:
        evidence_by_mail.setdefault(citation.mail_id, set()).update(
            citation.agenda_ids
        )
        source_docs_by_mail.setdefault(citation.mail_id, set()).update(
            citation.source_doc_ids
        )
    for child in child_digests:
        for citation in child.citation_map:
            evidence_by_mail.setdefault(citation.mail_id, set()).update(
                citation.agenda_ids
            )
            source_docs_by_mail.setdefault(citation.mail_id, set()).update(
                citation.source_doc_ids
            )

    required_claim_ids = {
        "claim:" + agenda_id
        for agenda_ids in evidence_by_mail.values()
        for agenda_id in agenda_ids
    }
    required_claim_ids.update(
        claim_id
        for child in child_digests
        for claim_id in child.used_claim_ids
    )
    required_issue_ids = {
        agenda_to_issue[str(agenda["agenda_id"])]
        for agenda in direct
        if str(agenda["agenda_id"]) in agenda_to_issue
    }
    required_issue_ids.update(
        issue_id
        for child in child_digests
        for issue_id in child.used_issue_ids
    )
    required_issue_ids.update(
        str(issue_id) for issue_id in previous.get("issue_ids", [])
    )
    relevant_issues = {
        issue_id: issues[issue_id].model_dump(mode="json")
        for issue_id in sorted(required_issue_ids)
    }
    return {
        "node": asdict(node),
        "week": week,
        "base_page": base_page,
        "previous_current_body_markdown": previous.get(
            "current_body_markdown", ""
        ),
        "previous_weekly_history": previous.get("weekly_history", []),
        "previous_citation_map": previous.get("citation_map", []),
        "direct_agendas": direct,
        "weekly_delta_agendas": direct_delta,
        "child_digests": [item.model_dump(mode="json") for item in child_digests],
        "evidence_by_mail": {
            key: sorted(value) for key, value in sorted(evidence_by_mail.items())
        },
        "source_docs_by_mail": {
            key: sorted(value)
            for key, value in sorted(source_docs_by_mail.items())
        },
        "required_claim_ids": sorted(required_claim_ids),
        "required_issue_ids": sorted(required_issue_ids),
        "issues": relevant_issues,
        "has_weekly_change": bool(direct_delta)
        or any(child.weekly_delta.strip() for child in child_digests),
    }


def merge_source_hash(context: dict[str, Any]) -> str:
    payload = {
        "strategy": "incremental_merge:v2",
        "node_id": context["node"]["id"],
        "week": context["week"],
        "agendas": [
            {
                "agenda_id": item["agenda_id"],
                "content_hash": item.get("content_hash", ""),
            }
            for item in context["direct_agendas"]
        ],
        "issues": context["issues"],
        "children": [
            {
                "category_id": item["category_id"],
                "source_hash": item["source_hash"],
            }
            for item in context["child_digests"]
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def retry_context_to_validation_context(
    context: dict[str, Any],
) -> MergeValidationContext:
    validate_canonical_issue_states(context["issues"])
    resolved = {
        issue_id
        for issue_id, issue in context["issues"].items()
        if issue["current_status"] == "resolved"
    }
    reopened = {
        issue_id
        for issue_id, issue in context["issues"].items()
        if issue["current_status"] == "reopened"
    }
    return MergeValidationContext(
        evidence_by_mail=context["evidence_by_mail"],
        source_docs_by_mail=context["source_docs_by_mail"],
        required_claim_ids=set(context["required_claim_ids"]),
        required_issue_ids=set(context["required_issue_ids"]),
        resolved_issue_ids=resolved,
        reopened_issue_ids=reopened,
        stale_claim_texts=[],
    )


def validate_canonical_issue_states(
    issues: dict[str, dict[str, Any]],
) -> None:
    for issue_id, issue in issues.items():
        events = sorted(
            issue["state_history"],
            key=lambda item: (_normalize_week(item["week"]), item["agenda_id"]),
        )
        terminal_positions = [
            index
            for index, event in enumerate(events)
            if str(event["state"]).casefold() in TERMINAL_STATES
        ]
        if issue["current_status"] == "resolved":
            if not events or len(events) - 1 not in terminal_positions:
                raise NarrativeValidationError(
                    "resolved issue has no latest terminal evidence: " + issue_id
                )
        if issue["current_status"] == "reopened":
            if not terminal_positions or terminal_positions[-1] >= len(events) - 1:
                raise NarrativeValidationError(
                    "reopened issue has no later non-terminal evidence: " + issue_id
                )


def build_page_from_merge(
    node: CategoryNode,
    document: MergedWikiDocument,
    citations: list[CitationMapEntry],
    context: dict[str, Any],
    previous: dict[str, Any],
    source_hash: str,
) -> dict[str, Any]:
    path = canonical_path(node)
    current_citations = [
        item.model_copy(update={"category_paths": [path]}) for item in citations
    ]
    history = [
        item
        if isinstance(item, WeeklyHistoryEntry)
        else WeeklyHistoryEntry.model_validate(item)
        for item in previous.get("weekly_history", [])
    ]
    if context["has_weekly_change"]:
        weekly_mail_ids = sorted(
            item.mail_id
            for item in current_citations
            if "주차별 업데이트 이력" in item.used_in_sections
        )
        history = merge_weekly_history(
            history,
            WeeklyHistoryEntry(
                week=context["week"],
                body_markdown=document.weekly_delta,
                source_mail_ids=weekly_mail_ids,
                agenda_ids=sorted(
                    {
                        agenda_id
                        for item in current_citations
                        if item.mail_id in weekly_mail_ids
                        for agenda_id in item.agenda_ids
                    }
                ),
                source_doc_ids=sorted(
                    {
                        source_id
                        for item in current_citations
                        if item.mail_id in weekly_mail_ids
                        for source_id in item.source_doc_ids
                    }
                ),
            ),
        )
    previous_citations = [
        item
        if isinstance(item, CitationMapEntry)
        else CitationMapEntry.model_validate(item)
        for item in previous.get("citation_map", [])
    ]
    citation_map = [
        item.model_copy(update={"category_paths": [path]})
        for item in _merge_citation_maps(
            current_citations,
            previous_citations,
            history,
        )
    ]
    source_agenda_ids = sorted(
        {agenda_id for item in citation_map for agenda_id in item.agenda_ids}
    )
    source_doc_ids = sorted(
        {source_id for item in citation_map for source_id in item.source_doc_ids}
    )
    issue_ids = sorted(document.used_issue_ids)
    resolved_ids = sorted(
        issue_id
        for issue_id in issue_ids
        if context["issues"][issue_id]["current_status"] == "resolved"
    )
    open_ids = sorted(set(issue_ids) - set(resolved_ids))
    base = context["base_page"]
    now = datetime.now(UTC).isoformat()
    return {
        **base,
        "doc_type": "canonical",
        "canonical_id": path,
        "current_body_markdown": document.current_body_markdown,
        "weekly_history": [item.model_dump() for item in history],
        "body_markdown": assemble_body(document.current_body_markdown, history),
        "citation_map": [item.model_dump() for item in citation_map],
        "child_page_ids": [
            item["canonical_id"] for item in context["child_digests"]
        ],
        "confidence": document.confidence,
        "agenda_count": len(source_agenda_ids),
        "issue_ids": issue_ids,
        "open_issue_ids": open_ids,
        "resolved_issue_ids": resolved_ids,
        "open_issue_count": len(open_ids),
        "resolved_issue_count": len(resolved_ids),
        "generation_review_items": document.review_items,
        "source_agenda_ids": source_agenda_ids,
        "source_doc_ids": source_doc_ids,
        "source_hash": source_hash,
        "schema_version": 2,
        "generation_strategy": "incremental_merge",
        "updated_at": now,
    }


def child_digest_from_page(page: dict[str, Any]) -> ChildDigest:
    history = page.get("weekly_history", [])
    weekly_delta = next(
        (
            str(item["body_markdown"])
            for item in history
            if _normalize_week(item["week"])
            == _normalize_week(page["as_of_week"])
        ),
        "",
    )
    citation_map = [
        item
        if isinstance(item, CitationMapEntry)
        else CitationMapEntry.model_validate(item)
        for item in page.get("citation_map", [])
    ]
    agenda_ids = sorted(
        {agenda_id for item in citation_map for agenda_id in item.agenda_ids}
    )
    return ChildDigest(
        category_id=str(page["category_id"]),
        canonical_id=str(page["canonical_id"]),
        current_body_markdown=str(page["current_body_markdown"]),
        weekly_delta=weekly_delta,
        used_claim_ids=["claim:" + agenda_id for agenda_id in agenda_ids],
        used_issue_ids=[str(item) for item in page.get("issue_ids", [])],
        citation_map=citation_map,
        source_hash=str(page["source_hash"]),
        confidence=str(page.get("confidence", "low")),
    )
~~~

- [ ] **Step 5: Make weekly history idempotent and change-only**

Use the extended WeeklyHistoryEntry in build_page_from_merge only when the node has direct weekly delta or a selected child delta. Same-week entries replace by normalized week. assemble_body continues to append immutable history below current_body_markdown.

- [ ] **Step 6: Run focused tests**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py -q
~~~

Expected: PASS.

- [ ] **Step 7: Commit**

~~~bash
git add integrated_wiki_builder.py tests/test_integrated_wiki_builder.py
git commit -m "feat(wiki): merge affected pages incrementally"
~~~

### Task 6: Persist Issues and Pages Incrementally and Add Rebuild CLI

**Files:**
- Modify: integrated_wiki_builder.py:1134-1345
- Modify: run_pipeline.py:87-112
- Modify: tests/test_integrated_wiki_builder.py
- Modify: tests/test_run_pipeline.py
- Modify: docs/category_wiki_builder.md

**Interfaces:**
- Consumes: validated pages and IssueLedgerResult.
- Produces: save_integrated_page(client, page) -> None, run(..., rebuild_all=False) -> dict[str, int].

- [ ] **Step 1: Write mapping and immediate-save tests**

Add save_integrated_page to the existing integrated_wiki_builder import list,
then add:

~~~python
def test_incremental_mapping_adds_strategy_issue_and_source_fields():
    properties = integrated_page_index_definition()["mappings"]["properties"]
    assert properties["issue_ids"]["type"] == "keyword"
    assert properties["schema_version"]["type"] == "integer"
    assert properties["generation_strategy"]["type"] == "keyword"
    assert properties["weekly_history"]["properties"]["agenda_ids"]["type"] == "keyword"
    assert properties["weekly_history"]["properties"]["source_doc_ids"]["type"] == "keyword"
    assert properties["citation_map"]["properties"]["source_doc_ids"]["type"] == "keyword"
    assert "embedding" not in properties


def test_save_integrated_page_writes_latest_and_same_week_snapshot(
    monkeypatch,
):
    client = SourceMgetClient(found_ids=["chunk-28"])
    actions = []
    monkeypatch.setattr(
        wiki_builder_module.helpers,
        "bulk",
        lambda actual_client, batch: actions.extend(batch),
    )
    page = complete_page_fixture("lotcd:4sa", "DRAM", "Spica", "4SA")

    save_integrated_page(client, page)

    assert [item["_id"] for item in actions] == [
        "lotcd:4sa",
        "lotcd:4sa:2026-W28",
    ]
    assert actions[0]["_source"]["page_kind"] == "latest"
    assert actions[1]["_source"]["page_kind"] == "snapshot"
    assert client.indices.refreshed == [PAGE_INDEX]
~~~

- [ ] **Step 2: Run focused tests and verify failure**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py tests/test_run_pipeline.py -q
~~~

Expected: FAIL because incremental mapping, save function, and stats are absent.

- [ ] **Step 3: Extend mappings and save one page atomically**

Add fields from the test to integrated_page_index_definition. Implement:

~~~python
def save_integrated_page(
    client: OpenSearch,
    page: dict[str, Any],
) -> None:
    validate_source_documents(client, [page])
    canonical = {**page, "page_kind": "latest", "doc_type": "canonical"}
    snapshot = {**page, "page_kind": "snapshot", "doc_type": "snapshot"}
    helpers.bulk(
        client,
        [
            {
                "_index": PAGE_INDEX,
                "_id": page["category_id"],
                "_source": canonical,
            },
            {
                "_index": PAGE_INDEX,
                "_id": page["category_id"] + ":" + page["as_of_week"],
                "_source": snapshot,
            },
        ],
    )
    client.indices.refresh(index=PAGE_INDEX)
~~~

Keep save_integrated_pages as a compatibility wrapper that calls save_integrated_page for each page. Do not batch the full tree before persistence.

- [ ] **Step 4: Update run and CLI**

Change run parameters to accept merge, issue_suggester, and rebuild_all. Ensure
PAGE_INDEX and ISSUE_INDEX exist. Load current Issues, resolve the requested
weekly delta, save the Issue Ledger, then call build_incremental_pages with
save_integrated_page as on_page. Compute selected_ids once and return this exact
result shape:

Add CLI:

~~~python
parser.add_argument(
    "--rebuild-all",
    action="store_true",
    help="Ignore current fixed-section bodies and rebuild every taxonomy page",
)
~~~

~~~python
{
    "affected_pages": len(selected_ids),
    "saved_pages": len(result.pages),
    "skipped_pages": len(result.skipped),
    "failed": len(result.failures),
    "pending": len(result.pending),
}
~~~

Print one concise progress line for page start, save, retry, failure, and pending parent. Preserve the existing external-LLM data-policy gate.

- [ ] **Step 5: Update weekly pipeline success criteria**

Replace the all-23-page equality check with:

~~~python
if category_stats["failed"] > 0 or category_stats["pending"] > 0:
    raise RuntimeError(
        "통합 Wiki 생성 실패: "
        f"{category_stats['failed']}개 실패, "
        f"{category_stats['pending']}개 상위 문서 대기"
    )
~~~

Replace the old pages-versus-expected_pages parametrization with these exact
incremental outcomes. Reuse the existing setup monkeypatches in the test file;
for the success case, make wiki_export.run_export write
tmp_path/2026-W28_전체요약.md, make convert write the HTML path, and stub
send_report:

~~~python
def test_pipeline_accepts_three_page_incremental_update(monkeypatch, tmp_path):
    configure_pipeline(
        monkeypatch,
        tmp_path,
        {
            "affected_pages": 3,
            "saved_pages": 3,
            "skipped_pages": 0,
            "failed": 0,
            "pending": 0,
        },
        complete_downstream=True,
    )

    assert run_pipeline.main() == 0


@pytest.mark.parametrize(
    "stats",
    [
        {
            "affected_pages": 3,
            "saved_pages": 2,
            "skipped_pages": 0,
            "failed": 1,
            "pending": 0,
        },
        {
            "affected_pages": 3,
            "saved_pages": 1,
            "skipped_pages": 0,
            "failed": 1,
            "pending": 1,
        },
    ],
)
def test_pipeline_rejects_failed_or_pending_incremental_update(
    stats,
    monkeypatch,
    tmp_path,
):
    configure_pipeline(monkeypatch, tmp_path, stats)

    with pytest.raises(RuntimeError, match="통합 Wiki 생성 실패"):
        run_pipeline.main()
~~~

Define configure_pipeline directly above those tests by moving the existing
monkeypatch setup into it. Its final scoped branch is:

~~~python
def configure_pipeline(
    monkeypatch,
    tmp_path,
    stats,
    *,
    complete_downstream=False,
):
    monkeypatch.setenv("ENABLE_CATEGORY_WIKI", "true")
    monkeypatch.setenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", "true")
    monkeypatch.setattr(run_pipeline, "wait_for_opensearch", lambda: None)
    monkeypatch.setattr(
        run_pipeline.fetch_mail,
        "get_week_string",
        lambda today: "2026-W28",
        raising=False,
    )
    monkeypatch.setattr(run_pipeline.fetch_mail, "main", lambda: None, raising=False)
    monkeypatch.setattr(
        run_pipeline.process_attachment,
        "process_all",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        run_pipeline.process_vision,
        "process_all",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        run_pipeline.embed_vectordb,
        "process_all",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        run_pipeline.embed_vectordb,
        "get_opensearch_client",
        lambda: object(),
        raising=False,
    )
    monkeypatch.setattr(integrated_wiki_builder, "run", lambda **kwargs: stats)
    monkeypatch.setattr(run_pipeline, "OVERVIEW_DIR", tmp_path)
    if complete_downstream:
        markdown = tmp_path / "2026-W28_전체요약.md"
        monkeypatch.setattr(
            run_pipeline.wiki_export,
            "run_export",
            lambda **kwargs: markdown.write_text("# summary", encoding="utf-8"),
            raising=False,
        )
        monkeypatch.setattr(
            run_pipeline.generate_outlook_report,
            "convert",
            lambda source, target: target.write_text("html", encoding="utf-8"),
            raising=False,
        )
        monkeypatch.setattr(
            run_pipeline.send_report,
            "send_report",
            lambda *args: None,
            raising=False,
        )
~~~

- [ ] **Step 6: Document initial and weekly commands**

Add:

~~~bash
python integrated_wiki_builder.py \
  --rebuild-all \
  --allow-external-llm

python integrated_wiki_builder.py \
  --week 2026-W29 \
  --allow-external-llm
~~~

Document that initial rebuild ignores fixed-section latest bodies, while weekly mode updates only Agenda-affected category chains. State that both modes read only OpenSearch.

- [ ] **Step 7: Run focused tests**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py tests/test_run_pipeline.py -q
~~~

Expected: PASS.

- [ ] **Step 8: Commit**

~~~bash
git add integrated_wiki_builder.py run_pipeline.py tests/test_integrated_wiki_builder.py tests/test_run_pipeline.py docs/category_wiki_builder.md
git commit -m "feat(wiki): persist resumable page updates"
~~~

### Task 7: Make Wiki Citations OpenSearch-Only and Raw-Source Complete

**Files:**
- Modify: knowledge_api.py:180-237, 348-375
- Modify: tests/test_knowledge_api.py:321-425

**Interfaces:**
- Consumes: page citation_map, mail_agendas Agenda IDs, weekly_mail source_doc_ids.
- Produces: existing GET /api/knowledge/wiki/citations/{mail_id} response with merged raw mail body and mapped Agenda records.

- [ ] **Step 1: Write failing raw-source citation tests**

Replace monkeypatched Agenda-detail tests with a fake OpenSearch client:

~~~python
def test_wiki_citation_loads_mapped_agendas_and_raw_chunks(monkeypatch):
    page = canonical_page_fixture()
    page["citation_map"] = [
        {
            "mail_id": "mail-1",
            "agenda_ids": ["agenda-1"],
            "source_doc_ids": ["chunk-1", "chunk-2"],
            "used_in_sections": ["Chamber A 원인"],
            "category_paths": ["dram/spica/4sa"],
        }
    ]
    monkeypatch.setattr(knowledge_api, "_get_category_wiki_page", lambda category_id: page)
    monkeypatch.setattr(
        knowledge_api,
        "_get_opensearch_agendas",
        lambda agenda_ids: [
            {
                "agenda_id": "agenda-1",
                "mail_id": "mail-1",
                "source_quote": "4SA 수율 하락",
                "summary": "4SA 수율 하락",
                "scope": "lotcd",
                "target_paths": [
                    {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
                ],
                "candidate_paths": [],
                "topic": "yield",
                "state": "open",
                "confidence": 0.99,
                "review_status": "confirmed",
                "subject": "Spica 주간 수율",
                "sender_team": "Spica수율",
                "received_at": "2026-07-06T00:00:00Z",
                "source_doc_ids": ["chunk-1", "chunk-2"],
            }
        ],
    )
    monkeypatch.setattr(
        knowledge_api,
        "_get_weekly_mail_parts",
        lambda source_ids: [
            {"_id": "chunk-1", "_source": {"text": "4SA 수율 하락.", "part_index": 0}},
            {"_id": "chunk-2", "_source": {"text": "chamber A 편차 확인.", "part_index": 1}},
        ],
    )

    response = request(
        "GET",
        "/api/knowledge/wiki/citations/mail-1",
        params={"category_id": "lotcd:4sa"},
    )

    assert response.status_code == 200
    assert response.json()["mail"]["body"] == "4SA 수율 하락.\nchamber A 편차 확인."
    assert [item["id"] for item in response.json()["agendas"]] == ["agenda-1"]
    assert response.json()["used_in_sections"] == ["Chamber A 원인"]


def test_wiki_citation_rejects_missing_raw_source(monkeypatch):
    page = canonical_page_fixture()
    page["citation_map"] = [
        {
            "mail_id": "mail-1",
            "agenda_ids": ["agenda-1"],
            "source_doc_ids": [],
            "used_in_sections": ["상태"],
            "category_paths": ["dram/spica/4sa"],
        }
    ]
    monkeypatch.setattr(knowledge_api, "_get_category_wiki_page", lambda category_id: page)

    response = request(
        "GET",
        "/api/knowledge/wiki/citations/mail-1",
        params={"category_id": "lotcd:4sa"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Citation has no weekly_mail source documents"
~~~

- [ ] **Step 2: Run tests and verify failure**

Run:

~~~bash
pytest tests/test_knowledge_api.py -q
~~~

Expected: FAIL because the helpers and raw-source guard do not exist.

- [ ] **Step 3: Split OpenSearch Agenda and source loading**

Implement:

~~~python
def _get_opensearch_agendas(agenda_ids: list[str]) -> list[dict[str, Any]]:
    if not agenda_ids:
        return []
    client = get_opensearch_client()
    response = client.mget(index=AGENDA_INDEX, body={"ids": agenda_ids})
    found = [
        {"_id": item["_id"], **item["_source"]}
        for item in response.get("docs", [])
        if item.get("found")
    ]
    if len(found) != len(set(agenda_ids)):
        raise HTTPException(status_code=409, detail="Citation Agenda evidence is incomplete")
    return found


def _get_weekly_mail_parts(source_doc_ids: list[str]) -> list[dict[str, Any]]:
    if not source_doc_ids:
        raise HTTPException(
            status_code=409,
            detail="Citation has no weekly_mail source documents",
        )
    client = get_opensearch_client()
    response = client.mget(index=SOURCE_INDEX, body={"ids": source_doc_ids})
    found = [item for item in response.get("docs", []) if item.get("found")]
    if len(found) != len(set(source_doc_ids)):
        raise HTTPException(status_code=409, detail="Citation raw mail evidence is incomplete")
    return sorted(found, key=lambda item: item["_source"].get("part_index", 0))
~~~

Hydrate AgendaView from Agenda documents and one Mail from the merged source parts. Check every Agenda mail_id equals the requested mail_id. Do not call get_store or use SQLite in this endpoint.

- [ ] **Step 4: Run API tests**

Run:

~~~bash
pytest tests/test_knowledge_api.py -q
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add knowledge_api.py tests/test_knowledge_api.py
git commit -m "fix(wiki): resolve citations from OpenSearch"
~~~

### Task 8: Render Dynamic Headings and Issue Counts in the Web Reader

**Files:**
- Modify: web/src/types.ts:130-177
- Modify: web/src/components/CategoryWikiReader.tsx
- Modify: web/src/components/CategoryWikiReader.test.tsx
- Modify: web/src/components/WikiDocumentMetaPane.test.tsx

**Interfaces:**
- Consumes: arbitrary current_body_markdown headings and additive page metadata.
- Produces: extractWikiHeadings(markdown: string) -> WikiHeading[], dynamic document outline, collapsed change-only weekly history.

- [ ] **Step 1: Rewrite the reader test around actual dynamic headings**

Replace the fixed-section assertion with:

~~~tsx
it('derives outline from dynamic markdown headings', () => {
  const onOutlineChange = vi.fn()
  const dynamicPage = {
    ...page,
    current_body_markdown: [
      '## Chamber A 편차와 수율 하락',
      '',
      '원인이 확인됐다. [mail:mail-1]',
      '',
      '## 조건 원복 후 검증',
      '',
      '재측정 중이다. [mail:mail-2]',
    ].join('\n'),
  }

  render(
    <CategoryWikiReader
      page={dynamicPage}
      loading={false}
      error={null}
      onSelectCitation={vi.fn()}
      onOutlineChange={onOutlineChange}
    />,
  )

  expect(onOutlineChange).toHaveBeenCalledWith([
    { id: 'chamber-a-편차와-수율-하락', label: 'Chamber A 편차와 수율 하락' },
    { id: '조건-원복-후-검증', label: '조건 원복 후 검증' },
  ])
  expect(screen.queryByRole('heading', { name: '개요' })).not.toBeInTheDocument()
})
~~~

Add a metric assertion:

~~~tsx
expect(screen.getByText('진행 1건 · 해결 0건')).toBeInTheDocument()
~~~

Use open_issue_count and resolved_issue_count, not Issue ID array lengths.

- [ ] **Step 2: Run Web tests and verify failure**

Run:

~~~bash
cd web
npm test -- --run src/components/CategoryWikiReader.test.tsx
~~~

Expected: FAIL because SECTION_LABELS still supplies the fixed outline.

- [ ] **Step 3: Implement heading extraction and dynamic rendering**

Delete SECTION_LABELS and add:

~~~tsx
export function extractWikiHeadings(markdown: string): WikiHeading[] {
  return markdown
    .split('\n')
    .map((line) => line.match(/^##\s+(.+?)\s*$/)?.[1])
    .filter((label): label is string => Boolean(label))
    .map((label) => ({ id: headingId(label), label }))
}
~~~

Build headings from page.current_body_markdown or legacy readerMarkdown fallback. Append weekly-history-title only when weekly_history is non-empty. Keep ReactMarkdown h2 IDs based on the same headingId function. The backend validator guarantees unique H2 IDs.

Change the header metric to:

~~~tsx
<p>
  {page.agenda_count}개 Agenda · 진행 {page.open_issue_count}건 · 해결 {page.resolved_issue_count}건
</p>
~~~

- [ ] **Step 4: Extend TypeScript fields additively**

Add:

~~~ts
export interface WeeklyHistoryRecord {
  week: string
  body_markdown: string
  source_mail_ids: string[]
  agenda_ids: string[]
  source_doc_ids: string[]
}

export interface WikiCitationRecord {
  mail_id: string
  agenda_ids: string[]
  source_doc_ids: string[]
  used_in_sections: string[]
  category_paths: string[]
}
~~~

Add issue_ids, schema_version, and generation_strategy to CategoryWikiPage. Update test fixtures with exact values:

~~~ts
issue_ids: ['issue:4sa-yield'],
schema_version: 2,
generation_strategy: 'incremental_merge',
~~~

- [ ] **Step 5: Run Web tests and build**

Run:

~~~bash
cd web
npm test
npm run build
~~~

Expected: all tests PASS and Vite production build exits 0.

- [ ] **Step 6: Commit**

~~~bash
git add web/src/types.ts web/src/components/CategoryWikiReader.tsx web/src/components/CategoryWikiReader.test.tsx web/src/components/WikiDocumentMetaPane.test.tsx
git commit -m "feat(web): render dynamic wiki outlines"
~~~

### Task 9: Add Two-Week OpenSearch Integration Coverage and Final Verification

**Files:**
- Modify: tests/test_integrated_wiki_builder.py
- Modify: tests/test_knowledge_api.py
- Modify only if failures reveal a scoped defect: integrated_wiki_builder.py, wiki_issue_ledger.py, category_wiki_builder.py, knowledge_api.py, knowledge_models.py, run_pipeline.py, web/src/components/CategoryWikiReader.tsx, web/src/types.ts

**Interfaces:**
- Consumes: complete W28 and W29 in-memory OpenSearch documents.
- Produces: one reproducible end-to-end test proving initial rebuild, weekly merge, citations, affected-chain call count, and idempotent rerun.

- [ ] **Step 1: Write an end-to-end fake OpenSearch test**

Add the complete in-memory OpenSearch contract and test:

~~~python
import integrated_wiki_builder as wiki_builder_module
import wiki_issue_ledger as issue_ledger_module


class StatefulIndices:
    def __init__(self, owner):
        self.owner = owner
        self.refreshed: list[str] = []

    def exists(self, *, index):
        return index in self.owner.documents

    def create(self, *, index, body):
        self.owner.documents[index] = {}
        self.owner.mappings[index] = body

    def get_mapping(self, *, index):
        return {index: self.owner.mappings[index]}

    def put_mapping(self, *, index, body):
        properties = self.owner.mappings[index]["mappings"]["properties"]
        properties.update(body["properties"])

    def refresh(self, *, index):
        self.refreshed.append(index)


class StatefulWikiOpenSearch:
    def __init__(self, *, weekly_mail, mail_agendas):
        self.documents = {
            SOURCE_INDEX: dict(weekly_mail),
            AGENDA_INDEX: dict(mail_agendas),
        }
        self.mappings = {
            SOURCE_INDEX: {"mappings": {"properties": {}}},
            AGENDA_INDEX: {"mappings": {"properties": {}}},
        }
        self.indices = StatefulIndices(self)

    def search(self, *, index, body):
        records = sorted(self.documents.get(index, {}).items())
        page_kind = (
            body.get("query", {}).get("term", {}).get("page_kind")
        )
        if page_kind:
            records = [
                (document_id, source)
                for document_id, source in records
                if source.get("page_kind") == page_kind
            ]
        search_after = body.get("search_after")
        if search_after:
            records = [
                item for item in records if item[0] > str(search_after[0])
            ]
        hits = [
            {"_id": document_id, "_source": source, "sort": [document_id]}
            for document_id, source in records[: body.get("size", 10)]
        ]
        return {"hits": {"hits": hits}}

    def mget(self, *, index, body):
        records = self.documents.get(index, {})
        return {
            "docs": [
                (
                    {
                        "_id": document_id,
                        "found": True,
                        "_source": records[document_id],
                    }
                    if document_id in records
                    else {"_id": document_id, "found": False}
                )
                for document_id in body["ids"]
            ]
        }

    def get(self, *, index, id):
        return {"_id": id, "_source": self.documents[index][id], "found": True}

    def apply_bulk(self, actual_client, actions):
        assert actual_client is self
        batch = list(actions)
        for action in batch:
            self.documents.setdefault(action["_index"], {})[action["_id"]] = dict(
                action["_source"]
            )
        return len(batch), []

    def latest_page(self, category_id):
        return self.documents[PAGE_INDEX][category_id]


def source_part(mail_id: str, week: str, text: str) -> dict[str, Any]:
    return {
        "mail_id": mail_id,
        "week": week,
        "text": text,
        "part_index": 0,
        "subject": "[Spica] 4SA 주간 수율",
    }


def agenda_document(
    agenda_id: str,
    mail_id: str,
    source_doc_id: str,
    week: str,
    state: str,
) -> dict[str, Any]:
    return {
        "agenda_id": agenda_id,
        "mail_id": mail_id,
        "week": week,
        "updated_week": week,
        "summary": "4SA " + state,
        "source_quote": "4SA " + state,
        "state": state,
        "topic": "yield",
        "scope": "lotcd",
        "subject": (
            "[Spica] 4SA 주간 수율"
            if week == "2026-W28"
            else "RE: [Spica] 4SA 주간 수율"
        ),
        "target_paths": [
            {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
        ],
        "candidate_paths": [],
        "review_status": "confirmed",
        "confidence": 0.99,
        "source_doc_ids": [source_doc_id],
        "content_hash": agenda_id + ":" + state,
        "created_at": "2026-07-07T00:00:00+00:00",
        "updated_at": "2026-07-07T00:00:00+00:00",
        "issue_id_source": "derived",
    }


def recording_merger(calls: list[str]):
    def merge(context: dict[str, Any]) -> MergedWikiDocument:
        calls.append(context["node"]["id"])
        citations = "".join(
            "[mail:" + mail_id + "]"
            for mail_id in sorted(context["evidence_by_mail"])
        )
        current_body = "## 통합 현황"
        if citations:
            current_body += "\n\n근거를 통합했다. " + citations
        return MergedWikiDocument(
            title=context["node"]["title"],
            current_body_markdown=current_body,
            used_claim_ids=sorted(context["required_claim_ids"]),
            used_issue_ids=sorted(context["required_issue_ids"]),
            weekly_delta=(
                "이번 주 근거를 갱신했다. " + citations
                if context["has_weekly_change"]
                else ""
            ),
            confidence="high" if citations else "low",
            review_items=[],
        )

    return merge


def test_rebuild_then_weekly_merge_is_idempotent_and_cited(monkeypatch, taxonomy):
    client = StatefulWikiOpenSearch(
        weekly_mail={
            "chunk-28": source_part("mail-28", "2026-W28", "4SA 수율 1.2%p 하락"),
            "chunk-29": source_part("mail-29", "2026-W29", "chamber A 원인 확인 후 조건 원복"),
        },
        mail_agendas={
            "agenda-28": agenda_document(
                "agenda-28", "mail-28", "chunk-28", "2026-W28", "investigating"
            ),
            "agenda-29": agenda_document(
                "agenda-29", "mail-29", "chunk-29", "2026-W29", "in_progress"
            ),
        },
    )
    calls = []
    monkeypatch.setattr(
        wiki_builder_module.helpers,
        "bulk",
        client.apply_bulk,
    )
    monkeypatch.setattr(
        issue_ledger_module.helpers,
        "bulk",
        client.apply_bulk,
    )

    first = run(
        weeks=["2026-W28"],
        allow_external_llm=True,
        allow_dummy_taxonomy=True,
        rebuild_all=True,
        client=client,
        merge=recording_merger(calls),
    )
    calls.clear()
    second = run(
        weeks=["2026-W29"],
        allow_external_llm=True,
        allow_dummy_taxonomy=True,
        client=client,
        merge=recording_merger(calls),
    )
    calls_after_second = list(calls)
    calls.clear()
    third = run(
        weeks=["2026-W29"],
        allow_external_llm=True,
        allow_dummy_taxonomy=True,
        client=client,
        merge=recording_merger(calls),
    )

    assert first["failed"] == 0
    assert second["saved_pages"] == 3
    assert calls_after_second == ["lotcd:4sa", "tech:dram:spica", "domain:dram"]
    assert third["saved_pages"] == 0
    assert calls == []
    page = client.latest_page("lotcd:4sa")
    assert [entry["week"] for entry in page["weekly_history"]] == [
        "2026-W29",
        "2026-W28",
    ]
    assert page["citation_map"][0]["source_doc_ids"]
    assert page["generation_strategy"] == "incremental_merge"
~~~

- [ ] **Step 2: Run the new test and fix only scoped defects**

Run:

~~~bash
pytest tests/test_integrated_wiki_builder.py::test_rebuild_then_weekly_merge_is_idempotent_and_cited -q
~~~

Expected: PASS.

- [ ] **Step 3: Run full Python verification**

Run:

~~~bash
pytest -q
python -m compileall integrated_wiki_builder.py wiki_issue_ledger.py category_wiki_builder.py knowledge_models.py knowledge_api.py run_pipeline.py
~~~

Expected: all tests PASS; compileall exits 0.

- [ ] **Step 4: Run full Web verification**

Run:

~~~bash
cd web
npm test
npm run build
~~~

Expected: all Vitest tests PASS; TypeScript and Vite build exit 0.

- [ ] **Step 5: Run secret and scope checks**

Run from repository root:

~~~bash
git diff --check
rg -n "sk-or-v1-|OPENROUTER_API_KEY=" . \
  -g '!node_modules/**' \
  -g '!.git/**' \
  -g '!.superpowers/**'
git status --short
~~~

Expected: git diff --check produces no output; secret scan produces no output; status contains only intended implementation files and the pre-existing untracked .superpowers directory.

- [ ] **Step 6: Commit integration coverage**

~~~bash
git add tests/test_integrated_wiki_builder.py tests/test_knowledge_api.py
git commit -m "test(wiki): cover two-week incremental merge"
~~~

- [ ] **Step 7: Review final branch diff**

Run:

~~~bash
git log --oneline --decorate -12
git diff --stat HEAD~9..HEAD
git status --short
~~~

Expected: nine scoped implementation commits after the design commit, no secret files, and only the pre-existing .superpowers directory untracked.

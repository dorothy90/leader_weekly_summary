# LOTCD Classification Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build an explainable, rerunnable weekly LOTCD classification backend with aggregate handling, correction history, alias learning, and explicit week approval.

**Architecture:** Keep LLM agenda extraction separate from deterministic LOTCD resolution. Persist classification runs, candidate traces, diagnostics, decisions, and week state in the existing SQLite knowledge store, then expose additive FastAPI endpoints. Confirm at most one LOTCD per LOTCD-specific item and derive Tech and Device/Domain from taxonomy.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, FastAPI, LangChain structured output, pytest.

## Global Constraints

- LOTCD is the only inferred taxonomy level; derive Tech and Device/Domain from taxonomy.
- Multi-LOTCD is a decision condition, never a taxonomy category.
- Split separate per-LOTCD values into separate items; store combined values as aggregate without LOTCD targets.
- Store match traces and failure diagnostics as data, not console-only logs.
- Alias changes rerun only the active week; approved weeks become revalidation_required and are never silently rewritten.
- A week cannot be approved while unclassified, conflict, or review_required items exist.
- Do not change Wiki generation, embedding, or report behavior.
- Do not send mail data to an external LLM unless KNOWLEDGE_LLM_DATA_POLICY_ACK=true.

---

## File structure

- Create classification_workbench.py: pure decision models, deterministic LOTCD classifier, run comparison, and week orchestration.
- Modify agenda_extract.py: explicit item kind in LLM output and traceable deterministic decisions.
- Modify knowledge_models.py: run, decision, trace, week, correction, and alias context contracts.
- Modify knowledge_store.py: additive SQLite migration and persistence.
- Modify process_agendas.py: one-week classification run lifecycle.
- Modify knowledge_api.py: Workbench query and mutation endpoints.
- Create tests/test_classification_workbench.py and extend existing extraction, store, process, OpenSearch, and API tests.
- Modify docs/category_wiki_builder.md: classification-first operator workflow.

### Task 1: Add decision contracts and deterministic hierarchy lookup

**Files:**
- Create: classification_workbench.py
- Modify: knowledge_models.py:65-164
- Create: tests/test_classification_workbench.py

**Interfaces:**
- Consumes: TaxonomyDocument and CategoryPath.
- Produces: CandidateMatch, ClassificationDecision, ClassificationRun, WeekClassificationSummary, lotcd_path(), and classify_context().

- [ ] **Step 1: Write failing tests**

~~~~python
# tests/test_classification_workbench.py
from pathlib import Path

from classification_workbench import classify_context, lotcd_path
from knowledge_models import TaxonomyDocument

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"


def taxonomy():
    return TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )


def test_lotcd_path_derives_upper_hierarchy():
    assert lotcd_path(taxonomy(), "4SA").model_dump() == {
        "domain": "DRAM", "tech": "Spica", "lotcd": "4SA"
    }


def test_single_code_is_confirmed_with_trace():
    decision = classify_context("4SA 수율 하락", "lotcd_specific", taxonomy())
    assert decision.status == "confirmed"
    assert decision.target_path.lotcd == "4SA"
    assert decision.matches[0].rule_id == "canonical:4SA"
~~~~

- [ ] **Step 2: Verify the tests fail**

Run: pytest tests/test_classification_workbench.py -q

Expected: collection fails with ModuleNotFoundError for classification_workbench.

- [ ] **Step 3: Add strict models to knowledge_models.py**

~~~~python
ItemKind = Literal["lotcd_specific", "aggregate", "unknown"]
DecisionStatus = Literal[
    "confirmed", "aggregate", "unclassified", "conflict",
    "review_required", "manually_corrected", "excluded",
]
DiagnosticCode = Literal[
    "NO_LOTCD_MATCH", "MULTIPLE_LOTCD_CONFLICT",
    "UNKNOWN_LOTCD_CODE", "AGGREGATE_METRIC",
    "CONTEXT_MISSING", "ALIAS_COLLISION",
]


class CandidateMatch(StrictModel):
    phrase: str
    lotcd: str
    match_type: Literal["canonical", "alias"]
    rule_id: str
    score: float = Field(ge=0, le=1)


class ClassificationDecision(StrictModel):
    status: DecisionStatus
    target_path: CategoryPath | None = None
    matches: list[CandidateMatch] = Field(default_factory=list)
    diagnostics: list[DiagnosticCode] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
~~~~

~~~~python
class ClassificationRun(StrictModel):
    id: str
    week: str
    status: Literal["processing", "completed", "failed"]
    prompt_version: str
    classifier_version: str
    taxonomy_version: int
    alias_version: int
    prior_run_id: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class WeekClassificationSummary(StrictModel):
    week: str
    workflow_state: Literal[
        "not_started", "processing", "review_in_progress",
        "ready_for_approval", "approved",
        "revalidation_required", "failed",
    ]
    active_run_id: str | None = None
    counts: dict[DecisionStatus, int] = Field(default_factory=dict)
~~~~

- [ ] **Step 4: Implement the minimal classifier**

~~~~python
# classification_workbench.py
from __future__ import annotations
import re
from knowledge_models import CandidateMatch, CategoryPath, ClassificationDecision, TaxonomyDocument

CLASSIFIER_VERSION = "lotcd-v1"


def _contains(text: str, phrase: str) -> bool:
    return bool(re.search(
        rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])",
        text, re.IGNORECASE,
    ))


def lotcd_path(taxonomy: TaxonomyDocument, code: str) -> CategoryPath:
    for domain in taxonomy.domains:
        for tech in domain.techs:
            for lotcd in tech.lotcds:
                if lotcd.code.casefold() == code.casefold():
                    return CategoryPath(
                        domain=domain.name, tech=tech.name, lotcd=lotcd.code
                    )
    raise ValueError(f"Unknown LOTCD: {code}")


def classify_context(text, item_kind, taxonomy, aliases=None):
    if item_kind == "aggregate":
        return ClassificationDecision(
            status="aggregate",
            diagnostics=["AGGREGATE_METRIC"],
            confidence=1.0,
        )
    matches = []
    for domain in taxonomy.domains:
        for tech in domain.techs:
            for lotcd in tech.lotcds:
                if _contains(text, lotcd.code):
                    matches.append(CandidateMatch(
                        phrase=lotcd.code,
                        lotcd=lotcd.code,
                        match_type="canonical",
                        rule_id=f"canonical:{lotcd.code}",
                        score=1.0,
                    ))
    codes = sorted({match.lotcd for match in matches})
    if len(codes) == 1:
        return ClassificationDecision(
            status="confirmed",
            target_path=lotcd_path(taxonomy, codes[0]),
            matches=matches,
            confidence=1.0,
        )
    if len(codes) > 1:
        return ClassificationDecision(
            status="conflict",
            matches=matches,
            diagnostics=["MULTIPLE_LOTCD_CONFLICT"],
            confidence=0.0,
        )
    return ClassificationDecision(
        status="unclassified",
        diagnostics=["NO_LOTCD_MATCH"],
        confidence=0.0,
    )
~~~~

- [ ] **Step 5: Verify and commit**

Run: pytest tests/test_classification_workbench.py -q

Expected: 2 passed.

~~~~bash
git add classification_workbench.py knowledge_models.py tests/test_classification_workbench.py
git commit -m "feat(classification): add decision contracts"
~~~~

### Task 2: Add alias, aggregate, and extraction behavior

**Files:**
- Modify: classification_workbench.py
- Modify: agenda_extract.py:30-79,287-367
- Modify: tests/test_classification_workbench.py
- Modify: tests/test_agenda_extract.py

**Interfaces:**
- Consumes: classify_context() and active AliasRecord values.
- Produces: AgendaDraft.item_kind and ExtractedAgenda.decision without multi-target fan-out.

- [ ] **Step 1: Add failing cases**

~~~~python
def test_group_metric_never_fans_out():
    decision = classify_context(
        "4SA/6SA 수율 종합지수 93.2", "aggregate", taxonomy()
    )
    assert decision.status == "aggregate"
    assert decision.target_path is None


def test_multiple_codes_require_review():
    decision = classify_context(
        "4SA와 6SA 조건 비교", "unknown", taxonomy()
    )
    assert decision.status == "conflict"
    assert decision.target_path is None
~~~~

~~~~python
def test_single_target_alias_records_rule_id():
    alias = AliasRecord(
        id=7,
        value="SP 24G",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
    )
    result = classify_context(
        "SP 24G Edge defect", "lotcd_specific", taxonomy(), [alias]
    )
    assert result.target_path.lotcd == "4SA"
    assert result.matches[0].rule_id == "alias:7"


def test_group_alias_is_a_conflict_not_a_multi_target():
    alias = AliasRecord(
        id=8,
        value="SP family",
        target_paths=[
            CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA"),
            CategoryPath(domain="DRAM", tech="Spica", lotcd="6SA"),
        ],
    )
    result = classify_context(
        "SP family 품질지수", "unknown", taxonomy(), [alias]
    )
    assert result.status == "conflict"
    assert result.diagnostics == ["ALIAS_COLLISION"]
~~~~

- [ ] **Step 2: Verify the alias test fails**

Run: pytest tests/test_classification_workbench.py -q

Expected: alias case fails because aliases are not inspected.

- [ ] **Step 3: Extend LLM and extraction models**

Add item_kind to AgendaDraft and ExtractedAgenda. Add decision: ClassificationDecision to ExtractedAgenda.

~~~~python
item_kind: Literal["lotcd_specific", "aggregate", "unknown"]
~~~~

Add these splitter prompt rules verbatim:

~~~~text
9. LOTCD별 값이 나뉜 표나 목록은 LOTCD 행마다 별도 agenda로 분리합니다.
10. 여러 LOTCD를 합친 하나의 수율·품질 지수는 item_kind=aggregate로 둡니다.
11. 개별 LOTCD 사실은 item_kind=lotcd_specific, 불명확하면 item_kind=unknown입니다.
~~~~

- [ ] **Step 4: Implement alias resolution**

Inspect active aliases after canonical codes. Accept only aliases whose targets resolve to one LOTCD. A group alias produces conflict with ALIAS_COLLISION. Deduplicate decisions by LOTCD while retaining every matching phrase in matches.

- [ ] **Step 5: Map decisions to legacy fields safely**

In extract_mail(), call classify_context() and map only a single target:

~~~~python
target_paths = [decision.target_path] if decision.target_path else []
review_required = decision.status in {"unclassified", "conflict", "review_required"}
scope = "lotcd" if decision.target_path else "unknown"
~~~~

Never return scope=multi_lotcd from automatic classification.

- [ ] **Step 6: Update and run extractor tests**

Add item_kind="lotcd_specific" to current AgendaDraft fixtures. Add one aggregate fixture and assert target_paths == [].

Run: pytest tests/test_agenda_extract.py tests/test_classification_workbench.py -q

Expected: all tests pass.

- [ ] **Step 7: Commit**

~~~~bash
git add agenda_extract.py classification_workbench.py knowledge_models.py tests/test_agenda_extract.py tests/test_classification_workbench.py
git commit -m "fix(classification): prevent multi-LOTCD fan-out"
~~~~

### Task 3: Persist runs, traces, and week state

**Files:**
- Modify: knowledge_store.py:50-172,500-638,857-982
- Modify: knowledge_models.py
- Modify: tests/test_seed_knowledge_db.py
- Modify: tests/test_classification_workbench.py

**Interfaces:**
- Produces: start_classification_run(), finish_classification_run(), fail_classification_run(), save_classified_extraction(), classification_items(), week_summary(), week_summaries(), and approve_week().

- [ ] **Step 1: Write failing lifecycle tests**

~~~~python
def test_run_and_trace_survive_reload(tmp_path):
    db_path = tmp_path / "knowledge.db"
    store = SQLiteKnowledgeStore(db_path)
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    store.finish_classification_run(run.id)
    reloaded = SQLiteKnowledgeStore(db_path)
    assert reloaded.week_summary("2026-01").active_run_id == run.id


def test_approval_rejects_unresolved_items(store_with_unresolved_run):
    with pytest.raises(ValueError, match="unresolved"):
        store_with_unresolved_run.approve_week("2026-01", "reviewer")
~~~~

- [ ] **Step 2: Verify missing methods**

Run: pytest tests/test_seed_knowledge_db.py tests/test_classification_workbench.py -q

Expected: failures for missing lifecycle methods.

- [ ] **Step 3: Add additive schema**

~~~~sql
CREATE TABLE IF NOT EXISTS classification_run (
  id TEXT PRIMARY KEY,
  week TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('processing','completed','failed')),
  prompt_version TEXT NOT NULL,
  classifier_version TEXT NOT NULL,
  taxonomy_version INTEGER NOT NULL,
  alias_version INTEGER NOT NULL,
  prior_run_id TEXT REFERENCES classification_run(id),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS week_classification (
  week TEXT PRIMARY KEY,
  active_run_id TEXT REFERENCES classification_run(id),
  workflow_state TEXT NOT NULL,
  approved_at TEXT,
  approved_by TEXT
);

CREATE TABLE IF NOT EXISTS classification_trace (
  agenda_id TEXT PRIMARY KEY REFERENCES agenda(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL REFERENCES classification_run(id),
  item_kind TEXT NOT NULL,
  decision_status TEXT NOT NULL,
  target_path_json TEXT,
  matches_json TEXT NOT NULL,
  diagnostics_json TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  taxonomy_version INTEGER NOT NULL,
  alias_version INTEGER NOT NULL
);
~~~~

Seed knowledge_meta.alias_version and knowledge_meta.classifier_schema_version to 1.

- [ ] **Step 4: Implement atomic save**

Refactor current save_extraction SQL into _save_extraction(connection, ...). save_classified_extraction() must save agenda and classification_trace in one transaction. Use uuid.uuid4().hex for run IDs.

- [ ] **Step 5: Implement state derivation and approval**

Use unresolved statuses {"unclassified", "conflict", "review_required"}. finish_classification_run() selects ready_for_approval only when their total is zero; otherwise review_in_progress. approve_week() rejects unresolved counts and records approved_at/approved_by.

- [ ] **Step 6: Verify persistence**

Run: pytest tests/test_seed_knowledge_db.py tests/test_classification_workbench.py -q

Expected: all tests pass after reopening the SQLite database.

- [ ] **Step 7: Commit**

~~~~bash
git add knowledge_models.py knowledge_store.py tests/test_seed_knowledge_db.py tests/test_classification_workbench.py
git commit -m "feat(classification): persist runs and traces"
~~~~

### Task 4: Add corrections, item splitting, and alias learning

**Files:**
- Modify: knowledge_models.py
- Modify: knowledge_store.py:546-856
- Modify: tests/test_classification_workbench.py

**Interfaces:**
- Produces: correct_classification(), set_item_disposition(), split_classification_item(), and create_learned_alias().

- [ ] **Step 1: Write failing correction tests**

~~~~python
def test_manual_correction_records_one_lotcd_and_revision(store_with_run):
    item = store_with_run.correct_classification(
        "agenda_027", "4SA", "reviewer", "원문 확인"
    )
    assert item.decision.status == "manually_corrected"
    assert item.decision.target_path.lotcd == "4SA"
    assert store_with_run.revisions("agenda_027")[0].changed_by == "reviewer"


def test_alias_marks_approved_weeks_for_revalidation(store_with_approved_week):
    store_with_approved_week.create_learned_alias(
        value="SP 24G",
        lotcd="4SA",
        origin_agenda_id="agenda_027",
        changed_by="reviewer",
    )
    assert store_with_approved_week.week_summary(
        "2026-01"
    ).workflow_state == "revalidation_required"
~~~~

- [ ] **Step 2: Verify missing correction methods**

Run: pytest tests/test_classification_workbench.py -q

Expected: failures for missing methods.

- [ ] **Step 3: Add request contracts**

~~~~python
class WorkbenchCorrection(StrictModel):
    lotcd: str
    reason: str = Field(min_length=1, max_length=500)


class ItemDispositionUpdate(StrictModel):
    status: Literal["aggregate", "excluded"]
    reason: str = Field(min_length=1, max_length=500)


class LearnedAliasCreate(StrictModel):
    value: str = Field(min_length=1, max_length=200)
    lotcd: str
    origin_agenda_id: str
    context_domain: Literal["DRAM", "NAND"] | None = None
    context_tech: str | None = None


class ItemSplitPart(StrictModel):
    source_quote: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=240)
    lotcd: str


class ItemSplitRequest(StrictModel):
    parts: list[ItemSplitPart] = Field(min_length=2)
    reason: str = Field(min_length=1, max_length=500)
~~~~

Add origin_agenda_id, context_domain, and context_tech columns to alias. Extend AliasRecord with optional defaults for backward compatibility.

- [ ] **Step 4: Implement correction and disposition**

correct_classification() resolves lotcd_path(), stores exactly one agenda_target, sets manually_corrected and confidence=1.0, and writes before/after/reason to classification_revision. set_item_disposition() deletes agenda targets and sets aggregate with AGGREGATE_METRIC or excluded with no diagnostic.

- [ ] **Step 5: Implement deterministic item splitting**

split_classification_item() verifies every part source_quote is an exact substring
of the original classification_context, resolves each LOTCD through lotcd_path(),
and derives each child ID from SHA-256(original agenda ID, source quote, LOTCD).
Create one manually_corrected agenda and trace per part, mark the original item
excluded, and record the split request in the original classification revision.
Reject overlapping source ranges, duplicate child IDs, unknown LOTCDs, or fewer
than two parts without changing stored data.

- [ ] **Step 6: Implement learned alias impact**

create_learned_alias() accepts one LOTCD, increments alias_version, records provenance, and changes every approved week to revalidation_required. It does not rerun approved weeks.

When an alias has context_domain or context_tech, classify_context() applies it
only when the domain/Tech name or one of its taxonomy aliases occurs in the
classification context or sender team. An unconstrained alias keeps the existing
global matching behavior.

- [ ] **Step 7: Verify and commit**

Run: pytest tests/test_classification_workbench.py tests/test_knowledge_api.py -q

Expected: all tests pass.

~~~~bash
git add knowledge_models.py knowledge_store.py tests/test_classification_workbench.py tests/test_knowledge_api.py
git commit -m "feat(classification): add correction workflow"
~~~~

### Task 5: Orchestrate one-week runs and compare reruns

**Files:**
- Modify: classification_workbench.py
- Modify: process_agendas.py:62-144
- Modify: knowledge_models.py
- Modify: knowledge_store.py
- Modify: tests/test_process_agendas.py
- Modify: tests/test_classification_workbench.py

**Interfaces:**
- Produces: run_week_classification(...) -> WeekClassificationSummary and compare_runs(old_run_id, new_run_id) -> RunComparison.

- [ ] **Step 1: Write failing orchestration tests**

~~~~python
def test_failed_splitter_marks_run_failed(tmp_path):
    def splitter(_text):
        raise RuntimeError("structured output failed")
    summary = run_week_classification(
        week="2026-01",
        store=SQLiteKnowledgeStore(tmp_path / "knowledge.db"),
        mail_directories=[fixture_mail_dir],
        splitter=splitter,
    )
    assert summary.workflow_state == "failed"


def test_comparison_reports_lotcd_change(store_with_two_runs):
    result = store_with_two_runs.compare_runs("run-old", "run-new")
    assert result.changed[0].before_lotcd == "6SA"
    assert result.changed[0].after_lotcd == "4SA"
~~~~

- [ ] **Step 2: Verify missing orchestration**

Run: pytest tests/test_process_agendas.py tests/test_classification_workbench.py -q

Expected: failures for missing run_week_classification and compare_runs.

- [ ] **Step 3: Implement run orchestration**

The function starts a run, reads only data/<week>/**/combined.txt, extracts and classifies each mail, atomically saves each result, then finishes the run. On exception, store stage, mail directory, exception type, and message and retain the prior successful run. It must never import embed_vectordb, category_wiki_builder, or Wiki modules.

- [ ] **Step 4: Add comparison models and query**

~~~~python
class RunItemChange(StrictModel):
    agenda_id: str
    before_status: DecisionStatus | None
    after_status: DecisionStatus | None
    before_lotcd: str | None
    after_lotcd: str | None


class RunComparison(StrictModel):
    old_run_id: str
    new_run_id: str
    changed: list[RunItemChange]
    unchanged_count: int
~~~~

Compare stable agenda IDs and include added/removed IDs with one side None.

- [ ] **Step 5: Update the CLI**

Retain existing flags, add --rerun, and require --week for a persisted Workbench run. Keep --dry-run on the legacy diagnostic path.

- [ ] **Step 6: Verify and commit**

Run: pytest tests/test_process_agendas.py tests/test_classification_workbench.py -q

Expected: all tests pass and none imports Wiki or embedding code.

~~~~bash
git add classification_workbench.py process_agendas.py knowledge_models.py knowledge_store.py tests/test_process_agendas.py tests/test_classification_workbench.py
git commit -m "feat(classification): orchestrate weekly runs"
~~~~

### Task 6: Expose Workbench API

**Files:**
- Modify: knowledge_api.py:226-631
- Modify: knowledge_models.py
- Modify: tests/test_knowledge_api.py

**Interfaces:**
- Produces: /api/knowledge/classification endpoints for the web plan.

- [ ] **Step 1: Add failing endpoint tests**

~~~~python
def test_item_response_contains_trace_and_derived_hierarchy():
    response = request(
        "GET",
        "/api/knowledge/classification/weeks/2026-01/items",
        params={"status": "confirmed", "lotcd": "4SA"},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["decision"]["target_path"]["tech"] == "Spica"
    assert item["decision"]["matches"][0]["phrase"]


def test_approval_is_blocked_with_unresolved_items():
    response = request(
        "POST", "/api/knowledge/classification/weeks/2026-01/approve"
    )
    assert response.status_code == 409
~~~~

- [ ] **Step 2: Verify 404 responses**

Run: pytest tests/test_knowledge_api.py -q

Expected: new endpoint tests fail with 404.

- [ ] **Step 3: Add read endpoints**

~~~~text
GET /api/knowledge/classification/weeks
GET /api/knowledge/classification/weeks/{week}/items
GET /api/knowledge/classification/items/{agenda_id}
GET /api/knowledge/classification/runs/{old_run_id}/comparison/{new_run_id}
~~~~

The item list supports lotcd, status, and q filters.

- [ ] **Step 4: Add editor endpoints**

~~~~text
POST  /api/knowledge/classification/weeks/{week}/run
POST  /api/knowledge/classification/weeks/{week}/approve
PATCH /api/knowledge/classification/items/{agenda_id}
PATCH /api/knowledge/classification/items/{agenda_id}/disposition
POST  /api/knowledge/classification/items/{agenda_id}/split
POST  /api/knowledge/classification/aliases
~~~~

Use Depends(require_editor) for all mutations. Return 409 when data-policy acknowledgement is missing or approval is blocked, 422 for unknown LOTCD, and 404 for unknown week/item.

- [ ] **Step 5: Verify and commit**

Run: pytest tests/test_knowledge_api.py -q

Expected: all new and existing API tests pass.

~~~~bash
git add knowledge_api.py knowledge_models.py tests/test_knowledge_api.py
git commit -m "feat(api): expose classification workbench"
~~~~

### Task 7: Protect downstream indexing and document operations

**Files:**
- Modify: agenda_opensearch.py:81-116
- Modify: tests/test_agenda_opensearch.py
- Modify: docs/category_wiki_builder.md

**Interfaces:**
- Consumes: persisted decision status.
- Produces: confirmed single-LOTCD documents only; exact operator instructions.

- [ ] **Step 1: Add a failing indexing regression**

~~~~python
def test_indexing_excludes_aggregate_and_unresolved_items(tmp_path):
    store = seeded_workbench_store(tmp_path)
    documents = agenda_opensearch.build_documents(store, "mail-with-mixed-items")
    assert {doc["decision_status"] for doc in documents} <= {
        "confirmed", "manually_corrected"
    }
    assert all(len(doc["target_paths"]) == 1 for doc in documents)
~~~~

- [ ] **Step 2: Verify failure before filtering**

Run: pytest tests/test_agenda_opensearch.py -q

Expected: aggregate or unresolved documents are returned.

- [ ] **Step 3: Filter safely**

Include only confirmed and manually_corrected decisions with exactly one LOTCD target. Keep legacy rows without classification_trace working during migration.

- [ ] **Step 4: Document commands**

~~~~bash
KNOWLEDGE_LLM_DATA_POLICY_ACK=true python process_agendas.py \
  --week 2026-01 \
  --allow-external-llm

python -m uvicorn knowledge_preview:app --host 127.0.0.1 --port 8002
~~~~

State that these commands do not run Wiki or embedding.

- [ ] **Step 5: Run backend verification**

~~~~bash
pytest tests/test_classification_workbench.py \
  tests/test_agenda_extract.py \
  tests/test_process_agendas.py \
  tests/test_seed_knowledge_db.py \
  tests/test_agenda_opensearch.py \
  tests/test_knowledge_api.py -q
python -m compileall classification_workbench.py agenda_extract.py \
  knowledge_models.py knowledge_store.py process_agendas.py knowledge_api.py
git diff --check
~~~~

Expected: all tests pass, compilation succeeds, and diff check is clean.

- [ ] **Step 6: Commit**

~~~~bash
git add agenda_opensearch.py tests/test_agenda_opensearch.py docs/category_wiki_builder.md
git commit -m "docs(classification): add operator workflow"
~~~~

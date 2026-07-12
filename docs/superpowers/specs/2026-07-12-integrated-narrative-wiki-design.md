# Integrated Narrative LLM Wiki Builder Design

**Date:** 2026-07-12
**Status:** Approved design; implementation not started

## 1. Purpose

The current Category Wiki pages organize weekly mail agendas by DRAM/NAND, Tech, and LOTCD, but their bodies read like event fragments joined together. The new builder will maintain one canonical, continuously updated narrative document for every taxonomy node:

- Domain: DRAM or NAND
- Tech: Spica, Canopus, Lucy, Procyon, Heraion, Colosseum, Petra, and others
- LOTCD: 4SA, 6SA, 4SS, 6E2, 6EN, and others

Each document must explain the current state, causal relationships, actions, outcomes, pending issues, and accumulated knowledge as one coherent article. It must preserve all weekly history inside the same document and make factual claims traceable to source mail.

## 2. Design influences

This design adapts, but does not copy, the following references:

- `/Users/daehwankim/yield-agent/08-YieldAgent/wiki_summarizer.py`
  - Pydantic structured outputs
  - multi-source synthesis
  - claim-level citations
  - confidence and contradiction handling
  - post-generation citation enrichment and validation
- [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki)
  - separate analysis and page-generation passes
  - previous Wiki context as an input to incremental maintenance
  - source boundaries and review items for contradictions or missing knowledge
- [STORM](https://github.com/stanford-oval/storm)
  - evidence gathering and outline planning before full article generation
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/index/overview/)
  - bottom-up summaries from lower-level evidence into higher-level communities

The design remains specific to the existing OpenSearch mail and agenda pipeline. It does not introduce a separate knowledge graph, SQLite database, or a second embedding pass.

## 3. Goals and non-goals

### Goals

1. Produce one user-visible canonical Wiki document for each Domain, Tech, and LOTCD.
2. Generate coherent prose instead of concatenating agenda entries.
3. Synthesize documents bottom-up: LOTCD, then Tech, then Domain.
4. Maintain pending, resolved, and reopened issue continuity across weeks.
5. Preserve all weekly update history inside the canonical document.
6. Attach a valid inline mail citation to each factual claim.
7. Keep the existing embedding, weekly report, monthly report, and agenda-review features working.
8. Present the result through a document-centered three-pane Web UI.

### Non-goals

- Re-embedding `combined.txt` inside the Wiki Builder
- Replacing the existing `weekly_mail` or `mail_agendas` indexes
- Rebuilding the weekly or monthly report generators
- Deleting the current Category Wiki Builder during the initial transition
- Building a generic graph-RAG platform
- Automatically merging uncertain issue identities or resolving unsupported issues

## 4. End-to-end data flow

The existing ingestion and embedding pipeline remains authoritative:

```text
combined.txt
  -> existing embed_vectordb
  -> OpenSearch weekly_mail
  -> mail-level agenda extraction and taxonomy mapping
  -> OpenSearch mail_agendas
  -> LOTCD narrative generation
  -> Tech bottom-up synthesis
  -> DRAM/NAND bottom-up synthesis
  -> citation, issue-state, and document validation
  -> OpenSearch category_wiki_pages
```

The Wiki Builder reads the already indexed source and agenda documents. It does not split or embed them again.

## 5. Canonical document identity

Every taxonomy path has one stable canonical identity:

```text
dram
dram/spica
dram/spica/4sa
nand
nand/heraion
...
```

The URL and `canonical_id` remain stable across weekly updates. The article body changes in place. Historical snapshots may still be stored for rollback and audit, but they are system revisions rather than separate Wiki articles and are excluded from normal Wiki navigation and search.

## 6. Bottom-up synthesis

### 6.1 LOTCD documents

Inputs:

- confirmed agendas mapped to the LOTCD
- the previous canonical LOTCD document, if any
- the new week's mail evidence
- taxonomy metadata and aliases

The result explains the specific product and fab context, current yield state, suspected or confirmed causes, actions, observed effects, pending issues, and reusable knowledge.

Aliases such as `4SA`, `6SA`, `Spica LPDDR5 24G`, and `SP LPDDR5 24G` must resolve through taxonomy data rather than prompt inference. Fab-specific differences must remain explicit.

### 6.2 Tech documents

Inputs:

- agendas mapped directly to the Tech
- successfully validated structured child LOTCD digests from the same run
- the previous canonical Tech document

The Tech page must synthesize common mechanisms, LOTCD-specific deviations, comparative impact, action effectiveness, and shared risks. It must not concatenate child page summaries.

### 6.3 Domain documents

Inputs:

- agendas mapped directly to DRAM or NAND
- successfully validated structured child Tech digests from the same run
- the previous canonical Domain document

The Domain page provides an executive and technical overview across Techs. It emphasizes cross-Tech patterns, material risks, decisions, and direction while linking to lower-level pages for detail.

### 6.4 Child synthesis contract

Parent prompts do not receive every child Markdown page in full. Each validated child produces a compact structured digest containing:

- current summary
- material supported claims and their source mail IDs
- ongoing, resolved, and reopened issues
- important causes, actions, and outcomes
- contradictions and confidence
- the child `canonical_id` and update week

This prevents prompt growth and stops a parent from treating an unsupported sentence in child prose as new primary evidence. The parent can promote only claims whose original mail citations remain traceable.

## 7. Two-stage LLM workflow

### 7.1 Stage 1: structured update analysis

The first LLM call returns a Pydantic-validated analysis object, not prose. It identifies:

- new facts supported this week
- established facts that remain valid
- stale statements that need revision or removal
- new, ongoing, resolved, and reopened issues
- contradictions between sources
- important child findings to promote to a parent page
- a proposed article outline
- the evidence IDs assigned to each planned section
- items requiring human review

The analysis is given the previous canonical page, the current evidence pack, and, for parent nodes, validated child synthesis inputs. The prompt must keep direct evidence distinct from child-level synthesis.

### 7.2 Stage 2: full narrative rewrite

The second LLM call receives only validated analysis and allowed evidence. It rewrites the complete current article rather than appending fragments.

Rules:

- Update the current-state sections to reflect the latest evidence.
- Move resolved items out of pending sections and into actions/outcomes.
- Record the week's state changes in the weekly history.
- Preserve prior weekly history without rewriting its meaning.
- Integrate repeated facts into existing prose instead of adding duplicate sentences.
- Label inference as `추정` or `검토 필요` unless direct evidence supports it.
- Do not introduce facts, values, owners, causes, or resolution states absent from allowed evidence.

### 7.3 Deterministic history assembly

The canonical page is handled as two logical parts:

1. current narrative sections
2. immutable prior weekly history entries

The LLM rewrites the current narrative and creates only the new week's history entry. Application code then appends the untouched prior entries in descending week order and renders one complete `body_markdown` document. Old history is not placed in the rewrite output and is never regenerated merely to preserve it.

The analysis prompt receives the current narrative, structured issue state, the new evidence, and only the recent history needed to interpret a transition. It does not receive the full lifetime history by default. This keeps weekly execution bounded while preserving the entire history in storage and in the user-visible article.

## 8. Canonical article structure

All levels share a recognizable structure while applying a different scope.

```markdown
# <Document title>

## 개요
Current integrated summary.

## 현재 상태와 주요 변화
Latest state integrated with established knowledge.

## 원인과 영향 관계
Observed symptom, cause, evidence strength, and impact relationships.

## 조치와 효과
Actions, outcomes, effectiveness, and follow-up.

## 펜딩 이슈와 의사결정
Unresolved issues, next evidence conditions, owners when sourced, and decisions.

## 누적 지식
Repeated patterns and reusable learning established across weeks.

## 주차별 업데이트 이력
### 2026-W28
Changes, additions, resolutions, and reopenings for the week.

### 2026-W27
Prior preserved history.
```

Content rules:

- Paragraphs are the default presentation.
- Tables are used only for comparisons that become clearer in tabular form.
- Chronological events are reorganized into cause, action, and outcome relationships.
- An agenda is not repeated across multiple sections without a distinct reason.
- Domain and Tech pages are independently readable, not tables of links.
- Older weekly sections remain in the Markdown but are collapsed by default in the Web reader.

## 9. Inline citation model

Every factual claim must include one or more inline references:

```markdown
4SA의 수율 저하는 공정 조건 변경 이후 확대되었다 [mail:MAIL-001].
```

The stored page also contains a structured citation map:

```json
{
  "mail_id": "MAIL-001",
  "agenda_ids": ["AGENDA-001-02"],
  "used_in_sections": ["원인과 영향 관계", "2026-W28"],
  "category_paths": ["dram/spica/4sa"]
}
```

Validation requirements:

1. Every cited `mail_id` exists in `weekly_mail`.
2. Every cited agenda exists in `mail_agendas`.
3. The agenda is confirmed for the document path or is explicitly marked for review rather than treated as fact.
4. Resolution claims cite a terminal-state source.
5. Parent claims cite either direct agendas or traceable child evidence.
6. Uncited factual claims fail validation.

The Web client resolves the citation to a source drawer. The Wiki body does not duplicate the complete raw mail.

## 10. Issue-state continuity

Existing business-state normalization remains in effect:

```text
pending/open/investigating/planned/monitoring -> ongoing
resolved/closed/completed/stable/positive/normal -> resolved
resolved followed by a later non-terminal state -> reopened
```

Rules:

- An issue must not resolve merely because it is absent from this week's mail.
- A terminal transition requires new source evidence.
- Reopened issues return to the pending section and are recorded in that week's history.
- Similar descriptions are not automatically merged when identity is uncertain.
- Uncertain matching becomes a human-review item.
- Agenda `review_status` represents classification review and must remain separate from business issue state.

## 11. OpenSearch storage

No new vector index is introduced.

### 11.1 Existing indexes

- `weekly_mail`: raw mail, metadata, and existing embeddings
- `mail_agendas`: extracted agenda records and taxonomy mapping
- `category_wiki_pages`: canonical pages and operational snapshots

### 11.2 Canonical page shape

```json
{
  "doc_type": "canonical",
  "canonical_id": "dram/spica/4sa",
  "category_level": "lotcd",
  "category_path": ["DRAM", "Spica", "4SA"],
  "title": "4SA — Spica LPDDR5 24G",
  "aliases": ["4SA", "SP LPDDR5 24G"],
  "as_of_week": "2026-W28",
  "current_body_markdown": "...",
  "weekly_history": [
    {"week": "2026-W28", "body_markdown": "...", "source_mail_ids": ["MAIL-014"]}
  ],
  "body_markdown": "...",
  "source_mail_ids": ["MAIL-001", "MAIL-014"],
  "citation_map": [],
  "child_page_ids": [],
  "open_issue_count": 2,
  "resolved_issue_count": 4,
  "confidence": "high",
  "updated_at": "..."
}
```

`child_page_ids` records which validated child documents contributed to Tech and Domain synthesis.

`body_markdown` is a deterministic rendering of `current_body_markdown` plus every `weekly_history` entry. The structured fields avoid reparsing Markdown during later updates and allow the Web reader to collapse old weeks without deleting them.

### 11.3 Snapshot shape

```json
{
  "doc_type": "snapshot",
  "canonical_id": "dram/spica/4sa",
  "snapshot_week": "2026-W28",
  "body_markdown": "..."
}
```

Snapshots are excluded from default page lists and Wiki search. The visible Wiki still has one logical document per taxonomy node.

## 12. Failure and consistency rules

- Retry a schema-invalid structured response once with validation feedback.
- Do not store a page containing an invalid or out-of-scope citation.
- Do not accept a resolution without terminal-state evidence.
- Do not update a parent page if a required child page failed in the same run.
- Leave the previous canonical page untouched on failure.
- Create a weekly snapshot only for a successfully validated canonical update.
- Log the `canonical_id`, generation stage, and relevant evidence IDs for each failure.

This produces per-branch consistency: an unsuccessful child update cannot silently feed a newly generated parent page.

## 13. Web Wiki experience

The reader follows the structure of the existing `/Users/daehwankim/yield-agent/08-YieldAgent/wiki_frontend` while retaining this project's taxonomy and management features.

### Left pane: taxonomy navigation

- DRAM/NAND, Tech, and LOTCD tree
- current-page highlight
- category and alias search
- compact pending-issue count

### Center pane: canonical article

- stable breadcrumb and title
- latest week and confidence
- readable, width-limited Markdown prose
- synchronized headings
- optional comparison tables
- recent weekly history expanded and older weeks collapsed

### Right pane: document context

- table of contents
- open and resolved counts
- parent and child pages
- source count
- classification-review items
- administrative links to snapshots and agenda management

### Citation drawer

Clicking `[mail:MAIL-001]` opens a drawer without leaving the document. It displays:

- subject, sender team, and received date
- supporting agenda
- the relevant evidence span from the raw mail
- classification path and review state
- a link to the complete mail detail

### Stable routes

```text
/wiki/docs/dram
/wiki/docs/dram/spica
/wiki/docs/dram/spica/4sa
```

The route does not include the week because the canonical document persists across updates.

## 14. Implementation boundary

The implementation will add a new `integrated_wiki_builder.py` and retain `category_wiki_builder.py` during transition.

Expected surgical changes:

- `integrated_wiki_builder.py`: structured analysis, bottom-up synthesis, validation, and persistence
- `run_pipeline.py`: switch only the Category Wiki phase to the new builder after tests pass
- `knowledge_store.py` and `knowledge_api.py`: canonical filtering and mail-evidence detail
- Web types, reader components, and styles: article outline, collapsible history, and citation drawer
- focused backend and frontend tests

The implementation must not refactor unrelated report, embedding, or ingestion code.

## 15. Test strategy

Implementation will proceed test-first.

1. **Analysis schema and state transitions**
   - ongoing, resolved, reopened, and unmentioned ongoing issues
2. **Bottom-up ordering**
   - LOTCD before Tech; Tech before Domain
   - parents consume only validated children
3. **Citation validation**
   - reject missing, out-of-scope, or unsupported citations
4. **History preservation**
   - current prose changes while every previous weekly entry remains
5. **OpenSearch and API behavior**
   - one canonical page per path
   - snapshots hidden from normal navigation
   - source detail resolves to original mail evidence
6. **Web reader behavior**
   - taxonomy navigation, outline, history disclosure, and source drawer
7. **Regression coverage**
   - existing backend and frontend suites
   - weekly and monthly report behavior remains unchanged

## 16. Acceptance criteria

The work is complete when:

- Every configured Domain, Tech, and LOTCD has exactly one user-visible canonical page.
- Every parent page contains a coherent synthesis of its child scope.
- The main body reads as an article rather than an agenda event list.
- Every factual claim has a validated inline mail citation.
- Ongoing, resolved, and reopened issues update correctly across weekly runs.
- All weekly history remains within the canonical page and older weeks are collapsed in the UI.
- The Web reader uses the approved three-pane document layout and source drawer.
- The existing embedding, weekly report, monthly report, and classification-review features continue to pass regression tests.

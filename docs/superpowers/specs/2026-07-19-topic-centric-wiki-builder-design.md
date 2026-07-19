# Topic-Centric Wiki Builder Design

**Date:** 2026-07-19
**Status:** Proposed for implementation planning
**Scope:** Approved weekly Agenda classification through Wiki build and read APIs

## 1. Objective

Build a Wiki after Agenda classification for roughly one year of weekly reports from more than 30 teams. Each team report can contain several unrelated subjects, and the same subject can continue across weeks, teams, Techs, and LOTCDs.

The Wiki must not treat a LOTCD, team report, or week as one narrative subject. It must preserve each classified Agenda as evidence, connect related Agendas into stable Topics, and let users read the same knowledge through four modes:

1. Topic
2. LOTCD, under the existing Domain → Tech → LOTCD taxonomy
3. Team
4. Week

Only Topic is a canonical accumulated knowledge document. LOTCD, Team, and Week are projections over Topics and their source Agendas.

## 2. Existing system boundaries

The existing pipeline remains authoritative through classification:

```text
combined.txt
  → existing mail ingestion and embedding
  → weekly_mail
  → Agenda extraction
  → deterministic Domain / Tech / LOTCD classification
  → operator review and week approval
  → Topic-Centric Wiki Builder
```

The builder consumes only an approved classification run. It does not:

- split or embed the source mail again;
- infer Tech or Domain independently when LOTCD already determines them;
- silently accept unresolved classification conflicts;
- replace weekly or monthly report generation;
- rewrite raw mail or Agenda evidence;
- use the old assumption that every taxonomy node owns one monolithic narrative page.

The existing `category_wiki_builder.py` remains available during migration. The new builder runs in parallel until its outputs and APIs are verified.

## 3. Options considered

### 3.1 One canonical narrative per Domain, Tech, and LOTCD

This matches the previous integrated narrative design but does not fit the data. A LOTCD can accumulate many independent subjects from many teams over a year. One article would grow without a stable subject boundary, repeat evidence, and exceed practical LLM context limits.

### 3.2 Materialize four separate page sets

This would generate Topic, LOTCD, Team, and Week documents independently. It makes each mode easy to render but duplicates summaries and state. The four copies can disagree after partial failure or regeneration.

### 3.3 Canonical Topics with three projection modes — selected

Persist Topic pages and Topic relations as the accumulated knowledge. Build LOTCD, Team, and Week screens from structured Topic and Agenda fields. Small overview summaries may be generated and cached, but the lists, counts, states, citations, and relationships are deterministic projections.

This keeps one source of truth, supports cross-team and cross-LOTCD subjects, and limits each LLM update to affected Topics.

## 4. Core data model

### 4.1 Source Report

A team report for one week. It remains immutable source material.

Required identity:

```text
report_id, week, team, source_doc_ids
```

### 4.2 Agenda

An atomic evidence unit extracted from a report. An Agenda contains one independently classifiable observation, issue, action, experiment, decision, or plan.

Relevant fields:

```yaml
agenda_id: A-2026-W30-YIELD-003
week: 2026-W30
team: Yield
summary: 4SA D1 불량이 조건 원복 후 일부 감소
state: monitoring
target_paths:
  - domain: DRAM
    tech: Spica
    lotcd: 4SA
source_mail_ids: [MAIL-001]
source_quote: "..."
review_status: confirmed
```

An Agenda can target multiple taxonomy paths only when the approved classification explicitly represents a shared or aggregate item. The Wiki Builder does not fan out an ambiguous aggregate into unsupported LOTCD facts.

### 4.3 Topic

A stable, subject-specific thread that can accumulate Agendas across teams and weeks.

Examples:

- `4SA D1 불량 증가`
- `C12 PM 일정 지연`
- `D1 검사 기준 변경`
- `4SA 출하 일정 결정`

Required Topic fields:

```yaml
topic_id: T-000123
title: 4SA D1 불량 증가
topic_kind: issue
primary_area: yield_defect
secondary_areas: [process_equipment, quality_analysis]
state: monitoring
importance: high
first_seen_week: 2026-W29
last_updated_week: 2026-W30
target_paths:
  - domain: DRAM
    tech: Spica
    lotcd: 4SA
teams: [Yield, Process, Quality, Product]
source_agenda_ids: [A-001, A-004, A-007, A-009, A-011]
related_topic_ids: [T-000124, T-000125]
```

`topic_kind` uses a controlled initial vocabulary:

```text
issue, observation, change, experiment, action, decision, plan, knowledge
```

`primary_area` uses a controlled initial vocabulary:

```text
yield_defect
process_equipment
quality_analysis
experiment_validation
product_production
schedule_delivery
decision_action
other
```

The primary area determines placement in the LOTCD table of contents. Secondary areas are filters and backlinks, not duplicate placements.

### 4.4 Topic relation

Topics remain separate when they describe different subjects, even when connected. Relations are typed edges:

```text
possible_cause
affects
measurement_effect
comparison
follow_up
supports
contradicts
shares_condition
```

Every relation stores supporting Agenda IDs, confidence, creation source, and review state. LLM-suggested relations below the auto-accept threshold remain review candidates and are not presented as facts.

### 4.5 Topic revision

Each successful weekly update creates an immutable revision containing:

- previous and new state;
- added Agenda IDs;
- added, removed, or changed claims;
- relation changes;
- the build run and prompt versions;
- generated summary and full current body;
- validation results.

The current Topic document points to the latest valid revision. A failed build never overwrites the last valid revision.

## 5. Topic identity and linking

Topic linking is the critical operation. Similar wording is not enough to merge two subjects.

### 5.1 Candidate retrieval

For each new confirmed Agenda, retrieve a small set of existing Topic candidates using:

1. taxonomy overlap;
2. normalized terms and aliases;
3. title and body BM25 match;
4. topic kind and primary area compatibility;
5. shared entities, equipment, defect names, actions, or issue identifiers;
6. recency as a weak signal, not a merge requirement.

No second embedding stage is introduced in the first implementation. Existing search infrastructure and structured metadata provide candidates. Semantic embeddings can be evaluated later without changing Topic identity contracts.

### 5.2 Structured link decision

The LLM receives the Agenda and only the retrieved candidates. It returns one of:

```text
attach_to_existing
create_new_topic
needs_review
```

An `attach_to_existing` decision must explain the shared subject and identify the evidence. It must not merge solely because two items share a LOTCD or team.

### 5.3 Deterministic guardrails

Force `needs_review` when:

- candidate Topics have incompatible taxonomy paths without an explicit cross-path subject;
- two candidates score similarly;
- issue identity depends on an unsupported inferred cause;
- the proposed match would join different equipment, defect, customer, experiment, or decision identities;
- the Agenda is aggregate but the candidate Topic is LOTCD-specific;
- the new state would resolve or reopen a Topic without supporting terminal or renewed evidence.

Operator decisions are stored and reused as identity aliases or negative-match rules in later runs.

## 6. Weekly build flow

The builder runs only after classification approval for a week.

```text
approved weekly Agendas
  → select new or changed Agendas
  → retrieve Topic candidates
  → decide attach / create / review
  → persist review queue and accepted assignments
  → update affected Topic analyses
  → generate affected Topic narratives
  → validate evidence, state, and relations
  → publish Topic revisions atomically
  → refresh LOTCD / Team / Week projections and cached summaries
  → record build run
```

### 6.1 Idempotency

The input hash includes:

- approved classification run ID;
- sorted Agenda content hashes;
- taxonomy and alias versions;
- Topic assignment version;
- prompt and builder versions.

An identical successful input is skipped. A rerun after a review correction updates only affected Topics and projections.

### 6.2 Two-stage Topic generation

Stage 1 returns structured update analysis:

- new and retained supported claims;
- stale claims;
- state transition proposal;
- actions and decisions;
- contradictions;
- relation proposals;
- open questions;
- review items.

Stage 2 rewrites the Topic's current narrative from validated analysis and allowed evidence. It does not regenerate the full lifetime history. Application code appends a new immutable weekly revision event.

### 6.3 Validation and publication

Before publication:

1. every factual claim references valid Agenda and mail IDs;
2. every Agenda belongs to an approved classification run;
3. each claim's taxonomy scope is compatible with the Topic;
4. resolved states require terminal evidence;
5. reopened states require newer non-terminal evidence;
6. relations reference existing Topics and supporting evidence;
7. no unsupported LOTCD fan-out occurs;
8. the rendered Markdown and structured fields agree.

Each affected Topic revision publishes atomically and independently. If one Topic fails validation, its previous revision remains current while other valid Topic revisions may publish. The weekly build records `partially_failed`, and projections use only the latest valid revision of each Topic. Dependent summaries exclude failed drafts and expose the stale Topic status to operators.

## 7. Four read modes

### 7.1 Topic mode

Topic mode reads the canonical accumulated knowledge document.

The stable layout is:

```text
Current state
Observed facts
Cause and impact
Actions and decisions
Differences by LOTCD
Contributions by team
Related Topics
Open questions
Timeline
Evidence
```

Sections with no supported content are omitted. The Topic page can span many teams and weeks, but it remains bounded by one subject identity.

### 7.2 LOTCD mode

LOTCD mode is a deterministic projection over Topics that target the selected taxonomy path. It is not a single generated article.

The top-level table of contents is fixed:

```text
1. 현황 요약
2. 주요 변화
3. 진행 중 Topic
4. 지식 영역
5. 조치와 의사결정
6. 연관 LOTCD
7. 해결·종료된 Topic
8. 출처와 활동 이력
```

Rules:

- `현황 요약` is a short cached summary generated only from the current projection data.
- `주요 변화` includes state, importance, relation, or material claim changes within the configured recent window, initially four weeks.
- `진행 중 Topic` groups unresolved Topics by urgent, investigating, action in progress, monitoring, and review required.
- `지식 영역` groups each Topic once by `primary_area`.
- `조치와 의사결정` projects structured action and decision records from Topics.
- `연관 LOTCD` ranks other LOTCDs by reviewed shared Topics and typed relations, not name similarity alone.
- `해결·종료된 Topic` groups closed Topics by quarter.
- `출처와 활동 이력` lists Agenda contributions by week and team.

Within each section, ordering uses importance, unresolved state, recent material change, number of contributing teams, evidence count, and reviewed relation degree. The API returns the reasons used for ranking.

Domain and Tech use the same projection mechanism at broader scope. They are taxonomy navigation levels, not additional canonical narrative documents.

### 7.3 Team mode

Team mode shows:

- Topics to which the team contributed;
- this week's and recent Agenda contributions;
- Topics shared with other teams;
- active actions or decisions owned by the team when ownership is sourced;
- Domain, Tech, and LOTCD coverage;
- report and evidence links.

It does not duplicate Topic narrative text.

### 7.4 Week mode

Week mode is a stable snapshot of knowledge change:

- new Topics;
- Topics with material updates;
- resolved or reopened Topics;
- new reviewed relations;
- important decisions and actions;
- review-required assignments and contradictions;
- contributing teams and taxonomy coverage.

Unlike LOTCD and Team views, a published Week view is retained as an audit snapshot. Later corrections create a new revision of that Week view rather than silently changing history.

## 8. Persistence and API boundaries

### 8.1 Authoritative records

- Existing source and Agenda stores remain authoritative for evidence and classification.
- SQLite knowledge workflow tables store build runs, Topic assignment decisions, review decisions, negative-match rules, and current revision pointers.
- OpenSearch stores searchable canonical Topic revisions and relation documents.
- LOTCD and Team views are computed from structured fields and may use invalidatable summary caches.
- Week views are versioned stored snapshots.

This separation keeps operator workflow transactional while using OpenSearch for full-text search and aggregations.

### 8.2 Initial APIs

```text
GET  /api/knowledge/wiki/topics
GET  /api/knowledge/wiki/topics/{topic_id}
GET  /api/knowledge/wiki/topics/{topic_id}/revisions
GET  /api/knowledge/wiki/topics/{topic_id}/relations

GET  /api/knowledge/wiki/lotcd/{domain}/{tech}/{lotcd}
GET  /api/knowledge/wiki/teams/{team}
GET  /api/knowledge/wiki/weeks/{week}

GET  /api/knowledge/wiki/reviews
POST /api/knowledge/wiki/reviews/{review_id}/attach
POST /api/knowledge/wiki/reviews/{review_id}/create
POST /api/knowledge/wiki/reviews/{review_id}/hold

POST /api/knowledge/wiki/builds/{week}
GET  /api/knowledge/wiki/builds/{run_id}
```

Build endpoints require an approved classification week and edit permission. Read endpoints preserve the existing authentication model.

## 9. Web navigation

The Wiki shell exposes four top-level modes:

```text
[주제] [LOTCD] [팀] [주차] [통합 검색]
```

Stable routes:

```text
/wiki/topics/{topic_id}
/wiki/lotcd/{domain}/{tech}/{lotcd}
/wiki/teams/{team}
/wiki/weeks/{week}
```

The existing Domain → Tech → LOTCD tree is reused in LOTCD mode. Selecting a Topic from any mode opens the same Topic detail route, preserving the current mode as return context.

The first implementation extends the existing React/Vite knowledge Web app. It does not introduce a separate desktop shell or copy the `llm_wiki` Tauri application.

## 10. Failure handling and review

Failures are explicit workflow states:

```text
not_started
linking
review_required
generating
validating
published
partially_failed
failed
```

- A classification conflict blocks the affected Agenda before Wiki linking.
- An uncertain Topic identity creates a review item; it does not create a guessed Topic silently.
- LLM or validation failure preserves the previous valid Topic revision.
- Projection summary failure does not hide deterministic Topic lists and counts.
- A Topic relation can be pending without blocking the underlying Topic update.
- Build logs contain identifiers and diagnostics but not full raw mail bodies.
- External LLM use remains gated by the existing data-policy acknowledgement.

## 11. Testing strategy

### 11.1 Unit tests

- strict Topic, relation, revision, and review models;
- candidate retrieval and taxonomy compatibility;
- aggregate Agenda guardrails;
- attach, create, and review decision thresholds;
- Topic state transitions including resolved and reopened;
- citation and relation validation;
- input hashing and idempotent reruns;
- LOTCD table-of-contents grouping and ranking;
- Team and Week projection aggregation.

### 11.2 Integration tests

Use a fixture with multiple teams, weeks, Topics, LOTCDs, and cross-Topic relations. Verify:

- several team Agendas update one Topic;
- similar wording does not merge distinct Topics;
- one Topic can span multiple reviewed LOTCD paths;
- one Agenda never produces unsupported per-LOTCD facts;
- a review correction rebuilds only affected Topics;
- a failed draft leaves the previous revision current;
- all four read modes point to the same canonical Topic data;
- a Week snapshot remains auditable after later updates.

### 11.3 API and Web tests

- response contracts and permission checks;
- mode routes and deep links;
- fixed LOTCD table of contents with dynamic content;
- Topic citations open the correct Agenda and source evidence;
- loading, empty, review-required, partial-failure, and stale-cache states;
- navigation from LOTCD, Team, or Week back to the same Topic.

### 11.4 Evaluation fixtures

Maintain a small gold dataset of manually reviewed Agenda-to-Topic assignments and relations. Report precision separately for automatic attachment, automatic creation, and relation acceptance. The initial release prioritizes incorrect-merge prevention over automatic attachment recall.

## 12. Delivery sequence

Implementation planning should split the work into these dependency-ordered increments:

1. Topic and relation contracts, persistence, and gold fixtures.
2. Candidate retrieval and reviewable Topic assignment.
3. Structured Topic analysis, narrative generation, validation, and revisions.
4. Deterministic LOTCD, Team, and Week projections and APIs.
5. Four-mode Web navigation and evidence drill-down.
6. Pipeline activation, migration comparison, evaluation, and operational documentation.

Each increment must be deployable behind a feature flag. The old Category Wiki remains readable until the new build has passed fixture evaluation and at least one approved-week comparison run.

## 13. Success criteria

The design is successful when:

1. every published factual claim resolves to an approved Agenda and source mail;
2. the same Topic accumulates supported updates across teams and weeks without copying narrative into other modes;
3. distinct subjects sharing a LOTCD remain distinct Topics;
4. LOTCD pages retain a stable table of contents while their Topic membership changes dynamically;
5. Team and Week modes expose contributions and changes without becoming independent sources of truth;
6. uncertain Topic assignments and relations are reviewable and reproducible;
7. rerunning identical inputs is idempotent;
8. partial failure never replaces a previously valid Topic revision;
9. the previous classification, report, embedding, and legacy Wiki paths continue to work during migration.

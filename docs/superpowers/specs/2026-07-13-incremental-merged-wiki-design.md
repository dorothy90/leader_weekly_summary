# Incremental Merged Yield Wiki Design

**Date:** 2026-07-13
**Status:** Approved design
**Scope:** Replace the fixed-section narrative inside `integrated_wiki_builder.py`
with an OpenSearch-only, incremental page merge. Existing report builders and the
embedding pipeline remain unchanged.

## 1. Goal

Build one current, cohesive Wiki document for every taxonomy node:

- Domain: `DRAM`, `NAND`
- Tech: `Spica`, `Canopus`, `Lucy`, `Procyon`, `Heraion`, `Colosseum`, `Petra`
- LOTCD: `4SA`, `6SA`, `4SS`, `6SS`, and the other configured LOTCDs

Each weekly ingest updates existing Agenda records, adds new Agenda records, and
merges the resulting knowledge into the affected Wiki documents. A document has
no fixed body outline. Its headings and organization follow the actual issues,
causes, actions, results, and decisions present in the evidence.

The current narrative is rewritten as one latest document. A separate weekly
history preserves only weeks that changed that category. OpenSearch snapshots
preserve prior complete page versions.

## 2. Core Decisions

1. Production input and output are OpenSearch only.
2. `weekly_mail` is the immutable raw-mail evidence index.
3. `mail_agendas` is the extracted and classified Agenda index.
4. `category_wiki_pages` stores latest pages and weekly snapshots.
5. A new `wiki_issue_ledger` index stores canonical issue identity and state.
6. The Issue Ledger is structured data only; it receives no embedding pass.
7. SQLite and demo fixtures are not part of the production builder path.
8. Fixed sections such as `overview`, `cause_and_impact`, and
   `pending_and_decisions` are removed from the LLM output contract.
9. Required knowledge is validated, but heading names and ordering are dynamic.
10. A LOTCD change propagates bottom-up to its Tech and Domain documents.
11. Pages without new or updated Agenda evidence receive no `no change` entry.
12. Existing weekly/monthly report builders are unchanged.

## 3. Non-Goals

- Do not change `combined.txt` parsing or `embed_vectordb` behavior.
- Do not perform a second chunking or embedding pass for Wiki generation.
- Do not replace `wiki_builder.py`, `wiki_summarizer.py`, or their report data.
- Do not introduce SQLite state for Wiki generation.
- Do not create a knowledge graph as part of this change.
- Do not force one universal Markdown outline across all page levels.
- Do not regenerate all pages merely because one page failed.

## 4. OpenSearch Input Contract

### 4.1 `weekly_mail`

Every raw mail is already split and embedded by the existing pipeline. Wiki
citations use these existing documents; the builder does not re-index them.

Required evidence identity:

- OpenSearch document `_id`
- canonical `mail_id`
- `week`
- `subject`
- `team`
- `part_index`
- raw `text`

### 4.2 `mail_agendas`

Every Agenda consumed by the builder must contain:

- stable `agenda_id`
- `mail_id`
- `week`
- `created_at`
- `updated_at`
- `updated_week`
- `received_at`
- `summary`
- `source_quote`
- `source_doc_ids`
- `topic`
- `state`
- `target_paths`
- `review_status`
- `confidence`

`source_doc_ids` must reference documents that exist in `weekly_mail`. Missing
raw evidence is a preflight error. The builder does not fall back to SQLite or a
fixture.

New mail evidence creates a new Agenda ID. A correction or review change to an
existing Agenda keeps its stable ID and advances `updated_at` and
`updated_week`. For requested week `W`, the delta contains Agenda records whose
evidence `week` is `W` or whose `updated_week` is `W`. A previously unknown
`agenda_id` is new; an already processed stable ID with a changed content hash
is an update.

## 5. Components

### 5.1 `WeeklyAgendaDeltaReader`

Responsibilities:

- read new and updated Agenda documents for the requested week;
- validate raw evidence references with a batched `mget` against `weekly_mail`;
- distinguish new Agenda records from updates to stable Agenda IDs;
- return exact affected taxonomy paths;
- compute an idempotent input hash for each affected category.

It does not write Wiki prose and does not call an LLM.

### 5.2 `IssueResolver`

An Agenda is an evidence fragment. An Issue is the durable operational problem
that can move through multiple Agenda updates, for example:

```text
yield drop
root cause confirmed
condition rollback in progress
remeasurement complete
yield normalized
```

The resolver first uses explicit and deterministic relationships:

- an existing upstream `issue_id`, if present;
- an Agenda update with the same stable `agenda_id`;
- an explicit reply/supersession relationship;
- compatible taxonomy path, subject identity, topic, and chronology.

If deterministic evidence does not identify one Issue, the LLM may return a
structured suggestion of one candidate or `new issue`. Ambiguous suggestions do
not auto-merge. They remain separate provisional Issues and create a review item.

Issue state is derived from chronological evidence rather than Wiki prose:

- planned/open/investigating/confirmed/in-progress evidence keeps the Issue open;
- explicit terminal evidence changes it to resolved;
- later open evidence after resolution changes it to reopened;
- a cause confirmation alone never resolves an Issue.

### 5.3 `WikiPageMerger`

Agenda extraction already provides the structured analysis stage. The builder
therefore performs one page-merge LLM call per affected document instead of a
second analysis call followed by a draft call.

LOTCD merge context contains:

- existing `current_body_markdown`, except during the initial rebuild;
- new and updated Agenda evidence for the week;
- current canonical Issue records relevant to the page;
- retained supported claims;
- stale or superseded claims;
- contradictions and review items;
- exact mail and raw source identifiers;
- recent weekly deltas needed to understand transitions.

Tech and Domain merge context additionally contains the newly validated child
page digests. Parent prose may summarize children but must retain the original
mail evidence IDs behind every factual claim.

The model receives these rules:

- output one coherent latest document, not concatenated Agenda notes;
- preserve every still-valid supported fact;
- remove redundancy;
- replace superseded current state with the latest supported state;
- keep prior state in weekly history, not the current narrative;
- keep conflicting claims separate and attributed when chronology is unclear;
- organize headings according to the topic;
- omit headings for knowledge that has no evidence;
- never invent causes, values, owners, actions, results, or resolution.

### 5.4 `WikiValidator`

The validator checks structure and semantics before persistence:

- each factual Markdown unit has one or more `[mail:<mail_id>]` citations;
- each citation maps through `citation_map` to Agenda and `weekly_mail` source IDs;
- every cited raw source exists;
- every current open Issue required by the page appears in `used_issue_ids`;
- every newly resolved or reopened Issue appears with the correct state;
- every retained claim appears in `used_claim_ids`;
- stale claims do not remain as current facts;
- a resolved statement has explicit terminal evidence;
- a reopened statement has later evidence than the resolution;
- ambiguous contradictions stay separated and create review items;
- the output remains one complete Markdown document with dynamic headings.

Validation failure is returned to the model for at most two correction attempts.
If correction still fails, the page is not saved.

### 5.5 `WikiPageStore`

The store writes only validated objects:

- latest page ID: the existing category ID, such as `lotcd:4sa`;
- snapshot ID: category ID plus normalized week;
- Issue Ledger ID: stable `issue_id`.

Each page is committed immediately after validation. This makes the build
resumable. A failed parent does not roll back a successful child.

## 6. Output Models

### 6.1 `MergedWikiDocument`

```text
title: string
current_body_markdown: string
used_claim_ids: list[string]
used_issue_ids: list[string]
weekly_delta: string
confidence: low | medium | high
review_items: list[string]
```

`current_body_markdown` contains the dynamically organized current narrative only.
`weekly_delta` contains only changes supported by the requested week's evidence.

### 6.2 Canonical page fields

`category_wiki_pages` retains the API-compatible identity and taxonomy fields and
stores:

```text
current_body_markdown
weekly_history
body_markdown
issue_ids
citation_map
source_agenda_ids
source_doc_ids
source_hash
schema_version
generation_strategy = incremental_merge
generated_at
updated_at
```

`body_markdown` is assembled deterministically from the current narrative and
weekly history. The LLM does not rewrite prior weekly history.

### 6.3 Weekly history

One entry per changed category and week:

```text
week
body_markdown
mail_ids
agenda_ids
source_doc_ids
```

Re-running the same week replaces that week's entry. It never appends a
duplicate. A category with no new or updated Agenda has no entry for that week.

### 6.4 `wiki_issue_ledger`

```text
issue_id
category_paths
topic
title
current_status
agenda_ids
state_history
first_seen_week
last_updated_week
resolved_week
reopened_count
confidence
review_required
```

Parent documents reference the same canonical Issue IDs. They do not create
duplicate parent-level Issues for child problems.

## 7. Initial Rebuild

The first run of the new strategy ignores the bodies of all existing fixed-
section latest pages.

1. Read all valid OpenSearch Agenda evidence.
2. Build the Issue Ledger.
3. Generate all LOTCD pages from evidence.
4. Generate Tech pages from direct Tech evidence and validated LOTCD digests.
5. Generate Domain pages from direct Domain evidence and validated Tech digests.
6. Save latest pages and normalized weekly snapshots.

Old snapshots remain available for audit. Existing latest IDs are replaced only
after their new page passes validation.

Independent LOTCDs may run with bounded concurrency. Tech pages wait for their
LOTCD children; Domain pages wait for their Tech children. Every page reports
start, completion, validation retry, and failure progress.

## 8. Weekly Incremental Flow

For week `W`:

1. Read new and updated `mail_agendas` for `W`.
2. Validate every referenced `weekly_mail` source document.
3. Update existing Issues and create new or provisional Issues.
4. Calculate affected exact taxonomy paths and their ancestors.
5. Merge affected LOTCD pages.
6. Merge affected Tech pages from validated child results.
7. Merge affected Domain pages from validated Tech results.
8. Replace the `W` history entry and `W` snapshot idempotently.

Example:

```text
W28: 4SA yield down, investigating
W29: chamber A confirmed; rollback and remeasurement in progress
W30: yield normalized, resolved
```

The W30 current page states the normalized latest condition. W28 and W29 remain
in weekly history and snapshots. The open and resolved counts come from the
Issue Ledger, not the number of Agenda documents.

## 9. Change Propagation

An exact LOTCD update uses this dependency order:

```text
4SA
Spica
DRAM
```

A direct Tech Agenda updates that Tech and its Domain. A direct Domain Agenda
updates only that Domain. Multiple changed children may build independently,
then their parent receives all validated child deltas in one merge call.

The builder never performs a full 23-page rerun because one branch failed.
Successful pages are cached by source hash and persisted immediately.

## 10. Conflict and State Rules

- Later evidence with a clear timestamp may supersede current state.
- Superseded state remains in weekly history and snapshots.
- Same-time or unclear-priority contradictions remain explicit in the current
  document with separate citations.
- Ambiguous Issue identity is not silently merged.
- No LLM-written phrase can change the structured Issue state.
- Resolution and reopening are calculated before prose generation.
- Parent summaries cannot upgrade a child's confidence or resolution state.

## 11. Failure and Resume Behavior

Every LLM call has a hard timeout. Every page has at most the initial attempt and
two correction attempts.

If `4SA` succeeds and `Spica` fails:

- save the new `4SA` latest page and snapshot;
- keep the previous `Spica` latest page;
- keep the previous `DRAM` latest page;
- mark the `Spica` branch pending;
- resume from `Spica` on the next run;
- do not call the model for unchanged successful `4SA` again.

The command returns a non-zero status when any required branch remains failed,
while preserving all independently validated progress.

## 12. API and Web Behavior

Existing Wiki page routes remain stable. Citation detail is changed to an
OpenSearch-only path:

1. load the page citation mapping;
2. load mapped Agenda records from `mail_agendas`;
3. load mapped raw chunks from `weekly_mail`;
4. merge overlapping raw chunks for display;
5. return the mail body and only Agenda records used by that page.

The Web reader:

- renders arbitrary Markdown headings;
- creates an in-page table of contents from actual headings;
- removes assumptions about the former fixed seven sections;
- shows weekly history in a collapsed section below the current narrative;
- omits weeks with no change;
- displays Issue Ledger open and resolved counts;
- opens inline citations to the raw OpenSearch evidence;
- preserves Domain, Tech, and LOTCD navigation.

## 13. Compatibility

- `wiki_builder.py` remains unchanged.
- `wiki_summarizer.py` remains unchanged.
- weekly and monthly report flows remain unchanged.
- `embed_vectordb` remains unchanged.
- existing Wiki API paths remain stable.
- OpenSearch mappings receive additive fields and one new structured index.
- legacy latest pages remain readable until replaced by the initial rebuild.

## 14. Testing Strategy

### Unit tests

- distinguish new Agenda from stable-ID update;
- validate `source_doc_ids` against `weekly_mail`;
- connect chronological Agenda evidence to one Issue;
- refuse ambiguous Issue merges;
- transition ongoing to resolved and resolved to reopened;
- calculate Issue counts independently of Agenda counts;
- accept dynamic headings and reject uncited factual units;
- preserve all required Claim and Issue IDs;
- replace same-week history without duplication;
- omit history when a category has no Agenda delta.

### Integration tests

- seed OpenSearch-like test doubles with W28 and W29 raw mail and Agenda data;
- build W28 pages, then incrementally merge W29;
- verify `4SA`, `Spica`, and `DRAM` update in dependency order;
- verify unrelated NAND pages do not invoke the model;
- verify a same-week correction replaces the existing delta and snapshot;
- verify citation detail returns the exact raw mail and mapped Agenda records;
- verify a failed parent resumes without rebuilding its successful child;
- verify the initial rebuild ignores legacy fixed-section prose.

### Web tests

- render unknown dynamic heading combinations;
- generate the in-page table of contents from actual headings;
- show only changed weekly history entries;
- open an inline citation using OpenSearch source IDs;
- display Issue Ledger counts rather than Agenda counts.

### Full verification

- run Python tests;
- run Web tests and production build;
- run a two-week OpenSearch smoke build;
- confirm every canonical page uses `generation_strategy=incremental_merge`;
- confirm every citation resolves to at least one `weekly_mail` source document;
- confirm one changed LOTCD performs no more than one initial page-merge call per
  affected page, excluding explicit correction retries.

## 15. Acceptance Criteria

1. No canonical page requires the former seven fixed headings.
2. A new week rewrites the affected current documents as cohesive articles.
3. Existing Agenda updates and new Agenda records both affect the weekly merge.
4. Categories without Agenda changes receive no history entry.
5. Related Agenda records count as one Issue when evidence supports identity.
6. Resolved and reopened states appear correctly in current prose and history.
7. `4SA` evidence updates `4SA`, `Spica`, and `DRAM`, but not unrelated pages.
8. Every factual statement has a working raw-mail citation.
9. Same-week reruns are idempotent.
10. Failed parents resume without re-running successful children.
11. Existing weekly/monthly reports and embedding behavior remain unchanged.

## 16. Reference Principle

This design adapts the relevant behavior from
[`nashsu/llm_wiki`](https://github.com/nashsu/llm_wiki): a page is identified by
its subject, new evidence is merged into the existing page body, distinct facts
are preserved, redundancy is removed, and the page is reorganized logically
instead of accumulating disconnected notes. The taxonomy and weekly history in
this project remain domain-specific additions.

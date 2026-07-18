# LOTCD Classification Workbench Design

**Date:** 2026-07-18
**Status:** Approved for implementation planning

## 1. Purpose

Build a weekly classification workflow that makes LOTCD classification accurate,
explainable, and correctable before any Wiki generation uses the result.

The workflow starts with the first available week and proceeds one week at a
time. A reviewer can inspect every extracted agenda grouped by LOTCD, correct a
single result, or add a reusable phrase-to-LOTCD alias. Corrections improve
subsequent weeks without silently changing weeks that were already approved.

Wiki generation, embedding, and Wiki Docs are outside this implementation.

## 2. Current problem

The current resolver scans an agenda's complete `classification_context` and
adds every matched LOTCD to `target_paths`. The Category Wiki Builder then
matches that agenda to every target path. A top-of-report aggregate yield or
quality metric that mentions several LOTCDs is therefore copied into every
LOTCD page as if it were an individual fact.

The current result also lacks a durable classification trace. An incorrect or
unknown classification does not sufficiently explain which phrase, taxonomy
entry, alias, or conflict produced the outcome. This makes prompt and alias
improvements difficult to evaluate.

## 3. Classification authority

LOTCD is the only inferred taxonomy level.

```text
confirmed LOTCD -> taxonomy Tech -> taxonomy Device/Domain (DRAM or NAND)
```

Tech and Device/Domain are never independently inferred. They are derived from
the confirmed LOTCD relationship, so a valid LOTCD classification produces a
deterministic upper hierarchy.

## 4. End-to-end workflow

```text
select week
  -> extract agenda and metric items
  -> detect LOTCD candidates
  -> persist classification decision and trace
  -> review in Classification Workbench
  -> correct one item or add a reusable alias
  -> reclassify the current week
  -> resolve all pending items
  -> explicitly approve the week
  -> continue to the next week
```

The implementation keeps extraction, candidate detection, decision, and review
as separate stages. Each stage has a persisted output that can be inspected
without rerunning later stages.

## 5. Item extraction

The extraction stage identifies independent agenda statements and metric rows
from the active mail body. It does not make the final LOTCD decision.

Each extracted item stores:

- stable item and mail IDs;
- week and source metadata;
- exact source quote and source offsets;
- classification context;
- concise agenda summary;
- initial item kind: `lotcd_specific`, `aggregate`, or `unknown`;
- extraction prompt version and execution ID.

When a table or list contains separate values per LOTCD, each LOTCD row becomes
an independent item. A single combined value covering several LOTCDs remains
one aggregate item.

## 6. Candidate detection and decision

Candidate detection compares the classification context against canonical
LOTCD codes and the active alias dictionary. It records all candidates before
making a decision.

For every candidate, the trace stores:

- matched source phrase;
- candidate LOTCD;
- match type: canonical code or alias;
- matching rule or alias ID;
- taxonomy and alias-dictionary versions;
- candidate score.

The decision has one of these statuses:

- `confirmed`: one LOTCD is sufficiently supported;
- `aggregate`: a combined metric that must not be copied to LOTCDs;
- `unclassified`: no usable LOTCD candidate was found;
- `conflict`: incompatible LOTCD candidates matched;
- `review_required`: a candidate exists but is not safe to confirm;
- `manually_corrected`: a reviewer explicitly selected the result;
- `excluded`: the extracted item is not a classification-worthy agenda.

The decision records one or more diagnostic reason codes:

- `NO_LOTCD_MATCH`;
- `MULTIPLE_LOTCD_CONFLICT`;
- `UNKNOWN_LOTCD_CODE`;
- `AGGREGATE_METRIC`;
- `CONTEXT_MISSING`;
- `ALIAS_COLLISION`.

Successful decisions also retain their positive match reason. Diagnostics are
part of stored data and API responses, not console-only logs.

## 7. Multi-LOTCD behavior

`Multi-LOTCD` is a classification condition, not a taxonomy category. The
system does not create a Multi-LOTCD node or document.

- If separate LOTCD values are present, extraction splits them into separate
  items and each item receives one LOTCD.
- If one combined metric covers multiple LOTCDs, the item is classified as
  `aggregate` and is not assigned to individual LOTCDs.
- If one statement mentions multiple LOTCDs but its intended scope is unclear,
  it is marked `conflict` or `review_required` until a reviewer decides whether
  to split, aggregate, correct, or exclude it.

If Wiki generation is connected in a later project, LOTCD-specific items will
feed their LOTCD pages. Aggregate items may feed the nearest common Tech or
Device/Domain page, but never each child LOTCD page by duplication.

## 8. Corrections and alias learning

The Workbench provides two distinct correction actions:

1. **Correct this item only** changes the selected item's LOTCD and records a
   classification revision.
2. **Add reusable alias** creates an active phrase-to-LOTCD rule tied to the
   source item that justified it.

An alias contains:

- phrase and normalized phrase;
- target LOTCD;
- optional Tech or Device/Domain context constraint;
- active state;
- originating item and reviewer;
- creation and revision timestamps.

Adding or changing an alias immediately marks the current in-progress week for
reclassification. Previously approved weeks are not automatically rewritten.
They receive a `revalidation_required` marker and change impact summary. A
reviewer may explicitly rerun one of those weeks later.

Manual corrections, alias revisions, and reruns preserve before and after
values, actor, timestamp, and reason.

## 9. Classification run and weekly state

Every execution creates a `classification_run` containing:

- run ID and week;
- start and completion timestamps;
- extraction prompt version;
- classifier version;
- taxonomy version;
- alias-dictionary version;
- item counts by decision status;
- prior run ID when the execution is a rerun.

A week has one of these workflow states:

- `not_started`;
- `processing`;
- `review_in_progress`;
- `ready_for_approval`;
- `approved`;
- `revalidation_required`;
- `failed`.

A week becomes `ready_for_approval` only when it has zero `unclassified`,
`conflict`, and `review_required` items. Final approval is always an explicit
reviewer action.

## 10. Workbench UI

The Workbench uses a simple three-column layout.

### Left: navigation

- weeks in chronological order with workflow state;
- LOTCD filters and item counts;
- aggregate, unclassified, conflict, and review-required filters.

### Center: classification list

- LOTCD or aggregate label;
- agenda summary;
- matched phrase and rule;
- confidence and decision status;
- text search and status filter;
- week approval action with unresolved-item count.

### Right: selected-item detail

- exact source quote and surrounding context;
- detected candidates;
- selected LOTCD and derived Tech/Device;
- diagnostic reasons and rule trace;
- correct item, add alias, classify as aggregate, split, or exclude actions;
- revision history.

The initial implementation prioritizes inspection and correction. It does not
add Wiki preview, graph navigation, or dashboard analytics.

## 11. API boundary

The API supports:

- listing weeks and workflow states;
- starting or rerunning classification for one week;
- listing classification items with LOTCD and status filters;
- reading an item with source context and classification trace;
- correcting an item;
- classifying an item as aggregate or excluded;
- creating and revising aliases;
- listing run and item revisions;
- approving a week after validation passes.

The API rejects week approval while unresolved items exist. It also rejects a
confirmed LOTCD that does not exist in the current taxonomy.

## 12. Failure handling and observability

Failures are isolated by stage and mail. A failed extraction or classification
does not erase the prior successful run. The failed stage stores its error type,
message, source mail, and run ID so the Workbench can show where processing
stopped.

The following boundaries emit structured counts and diagnostics:

- mails read -> items extracted;
- items extracted -> candidates detected;
- candidates -> decisions;
- decisions -> review state;
- rerun -> changed and unchanged decisions.

The rerun comparison shows which items moved between LOTCDs, became aggregate,
became unresolved, or were unchanged. This is the primary feedback for judging
whether a prompt or alias modification improved classification.

## 13. Testing strategy

Unit tests cover:

- LOTCD code and alias matching;
- deterministic Tech and Device derivation;
- separate per-LOTCD metric extraction;
- aggregate metrics not receiving LOTCD targets;
- collision and unknown reason codes;
- manual correction and alias revision history;
- approved-week revalidation behavior.

Fixture evaluation covers representative weekly-report headers, tables, lists,
aliases, unknown codes, and mixed LOTCD contexts. Expected classifications are
stored independently of classifier output.

API tests cover filters, correction actions, alias creation, rerun comparison,
approval validation, and persistence after reload.

UI tests cover week selection, LOTCD grouping, unresolved filters, source and
trace display, correction actions, and blocked versus successful approval.

## 14. Acceptance criteria

The implementation is complete when:

1. A reviewer can process weeks sequentially starting from week 1.
2. Every extracted item is visible under a LOTCD, aggregate, unresolved, or
   excluded group.
3. Every automatic decision explains the matched phrase and rule, while every
   failure explains its diagnostic reason.
4. Per-LOTCD metrics become separate items.
5. Combined multi-LOTCD metrics never become duplicated LOTCD assignments.
6. Correcting one item does not silently create a reusable global rule.
7. Adding an alias reruns the current week and reports classification changes.
8. Approved prior weeks remain unchanged and are marked for optional
   revalidation.
9. Tech and Device/Domain are derived only from the confirmed LOTCD taxonomy.
10. A week cannot be approved with unresolved classification items.
11. Existing Wiki, embedding, and report behavior remains unchanged.

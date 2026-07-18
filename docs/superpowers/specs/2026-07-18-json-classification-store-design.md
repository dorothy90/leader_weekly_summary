# JSON Classification Store Design

## Goal

Replace SQLite with a file-only persistence model for the single-user LOTCD
Classification Workbench. Remove legacy Knowledge Wiki, Explorer, Mapping, and
SQLite-specific code instead of reimplementing those deferred features.

## Source of truth

`config/classification_rules.json` is the only source of classification rules.
It contains:

- Domain, Tech, and LOTCD hierarchy
- canonical codes and aliases
- group aliases and their candidate LOTCDs
- direct Tech and Domain scope phrases
- classification policies for aggregate, conflict, unknown, and parent derivation
- a monotonically increasing rule version

The UI may update this file. Writes use a temporary sibling file followed by an
atomic replace. Git provides human-readable rule history.

## Runtime data

The current state of each week is stored in:

`classification_data/{week}.json`

Each file contains the week workflow state, active run metadata, classified
items, source text, matches, diagnostics, corrections, and approval provenance.

Before a rerun replaces the active result, its complete previous state is copied
to:

`classification_data/history/{week}/{run_id}.json`

Run comparison loads the two JSON snapshots and compares stable agenda IDs.
There is no database migration; classification starts from new JSON files.

## Classification model

Each item stores one of these scopes:

- `lotcd`: exact Domain / Tech / LOTCD path
- `tech`: exact Domain / Tech path with no LOTCD
- `domain`: exact Domain path with no Tech or LOTCD
- `aggregate`: combined metric with no forced LOTCD fan-out
- `unknown`: unresolved and review-required

One LOTCD deterministically derives Tech and Domain. Direct Tech/Domain phrases
are resolved from the rule file and never fabricated from ambiguous product-group
phrases. Multiple LOTCD candidates remain conflict unless a rule explicitly
marks the phrase as aggregate or a direct parent scope.

## API and UI

Keep only the routes required by `/classification`:

- session and taxonomy/rules reads
- week list, item list/detail, run, comparison, correction, disposition, split,
  alias/rule creation, and approval
- static SPA delivery for `/classification`

Remove Wiki, Explorer, Mapping, graph, OpenSearch indexing, and SQLite seeding
routes/scripts/tests. The Workbench UI keeps its current three-column behavior.

## File writes and recovery

All JSON documents are validated with Pydantic before saving. Writes use
`file.tmp` plus `os.replace`, so an interrupted write cannot partially overwrite
the active file. A failed classification run records structured failure metadata
in the week file while retaining the prior snapshot in history.

## Deletions

Delete:

- all `.db` files created for Knowledge/Classification
- `sqlite3` imports and `SQLiteKnowledgeStore`
- SQLite schema/seeding and database-only tests
- deferred Wiki/Explorer/Mapping/OpenSearch persistence code and routes
- temporary SDD reports/diffs and generated demo artifacts not required at runtime

Preserve:

- original mail data
- classification fixtures used by tests/demo
- classification extraction, deterministic decision logic, Workbench API/UI,
  and model configuration

## Verification

- no tracked Python source imports `sqlite3`
- no application path references `.db` or `SQLiteKnowledgeStore`
- JSON store tests cover atomic persistence, reload, rerun history, corrections,
  scope rules, and approval
- classification API tests and web tests pass
- `/classification` loads one-team dummy JSON data without SQLite

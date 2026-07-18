# Backend Task 4 Report

## Status

Implemented corrections, dispositions, deterministic item splitting, learned aliases, contextual alias matching, and the shared alias lifecycle invariant.

## Changes

- Added strict request contracts for correction, disposition, learned aliases, and split requests.
- Added backward-compatible alias provenance/context fields and classification revision reasons, including additive SQLite migrations.
- Added `correct_classification()` and `set_item_disposition()` with one-target/no-target trace updates, revision recording, and active-week readiness recalculation.
- Added `split_classification_item()` with pre-mutation validation, deterministic SHA-256 child IDs, one manually corrected agenda/trace per part, original exclusion, and the full split request in the original revision.
- Added `create_learned_alias()` with exactly one resolved LOTCD target and provenance/context persistence.
- Centralized alias impact so `create_alias()`, `update_alias()`, `delete_alias()`, and `create_learned_alias()` atomically increment `alias_version` and move approved weeks to `revalidation_required` without changing approval fields, active runs, or traces.
- Added optional sender-team context to `classify_context()` and constrained learned-alias matching by domain/Tech names and Tech taxonomy aliases.

## TDD Evidence

- Initial RED: `python -m pytest tests/test_classification_workbench.py -q` failed collection because `ItemSplitPart` did not exist.
- Behavioral RED after adding only request contracts: 12 failures, 20 passes. Failures were the missing correction, disposition, split, learned-alias, alias-impact, and contextual-matching behavior.
- Focused GREEN: `python -m pytest tests/test_classification_workbench.py tests/test_knowledge_api.py -q` -> 59 passed, 8 warnings.
- Full Python suite: `python -m pytest -q` -> 91 passed, 8 warnings.

Warnings are pre-existing dependency deprecations from Pydantic v1 compatibility and Starlette multipart imports.

## Self-review

- Confirmed invalid splits validate before writes or roll back transactionally; tests cover fewer than two parts, overlaps, unknown LOTCDs, duplicate child IDs, and missing source quotes.
- Confirmed an injected revalidation failure rolls back the learned alias row and alias-version increment together.
- Confirmed approved-week `active_run_id`, `approved_at`, `approved_by`, and trace rows remain unchanged after alias mutation.
- Confirmed unconstrained aliases retain global behavior and constrained aliases require taxonomy context in classification text or the optional sender-team input.

## Scope note

`classification_workbench.py` was necessarily modified although omitted from the brief's file list, because Step 6 explicitly requires changing `classify_context()`. No extraction/process orchestration, API, Wiki, embedding, report-generation, or Web code was changed.

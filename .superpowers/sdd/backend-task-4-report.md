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

`classification_workbench.py` was necessarily modified although omitted from the brief's file list, because Step 6 explicitly requires changing `classify_context()`. The review follow-up also required the single sender-team propagation change in `agenda_extract.py`. No other process orchestration, API, Wiki, embedding, report-generation, or Web code was changed.

## Review Fixes

All Task 4 review findings were addressed with regression tests:

- Correction, disposition, and split now take an immediate write transaction, check the owning week's state before operation-specific work, and reject `approved` or `revalidation_required` items. Tests compare the full agenda, targets, trace, week row (including active run and approval provenance), and revision count before and after rejection.
- Readiness recalculation reads the persisted workflow state and returns without changing either protected state.
- `extract_mail()` now forwards `mail.sender_team` into `classify_context()`. An extraction-level regression confirms an alias constrained to Spica matches when only the sender team supplies that context.
- Split context is located as the unique mail-body occurrence containing the original agenda source range. Child offsets are calculated from the already validated in-context ranges; a repeated-identical-text regression confirms children point to the second occurrence selected by the original agenda.
- Revision history snapshots omit derived `revision_count`, avoiding stale before/after values after the revision insert.

### Review TDD Evidence

- Review RED: focused command produced 11 failures and 72 passes. Nine failures reproduced the product findings; two exposed a test-only row-factory mismatch in the direct private-helper test, which was corrected before evaluating behavior.
- Review GREEN: `python -m pytest tests/test_classification_workbench.py tests/test_agenda_extract.py tests/test_knowledge_api.py -q` -> 83 passed, 8 warnings.
- Review full suite: `python -m pytest -q` -> 102 passed, 8 warnings.

## Final Review Fix

The shared mutation guard now requires both:

- the item's `classification_trace.run_id` equals the week's `active_run_id`; and
- the workflow state is exactly `review_in_progress` or `ready_for_approval`.

This rejects obsolete-run items and active processing-run items before correction, disposition, or split can mutate stored data. Regression snapshots confirm agenda, targets, trace, week state/active run, approval provenance, and revision count remain unchanged.

### Final Review TDD Evidence

- RED: `python -m pytest tests/test_classification_workbench.py tests/test_knowledge_api.py -q` -> 2 failed, 69 passed. The failures reproduced obsolete-run correction and processing-run disposition mutation.
- GREEN: the same focused command -> 71 passed, 8 warnings.
- Full suite: `python -m pytest -q` -> 104 passed, 8 warnings.

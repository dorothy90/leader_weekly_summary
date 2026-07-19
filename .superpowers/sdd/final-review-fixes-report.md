# Final whole-branch review fix report

## Status

DONE

## Scope and design

- Kept the implementation JSON-only. No SQLite, OpenSearch, or embedding stage was added.
- `classification_data` remains read-only. Every approved build now archives the exact approved Week document fields plus immutable per-Agenda evidence under `wiki_data/history/evidence/<week>/<classification_run_id>/`.
- New Topic revisions contain immutable evidence refs and explicit state, Agenda, claim, relation, build, prompt, builder, summary/body, model, and validation provenance.
- Existing pre-contract Wiki revisions must be rebuilt from approved weeks. Runtime evidence reads intentionally do not fall back to mutable classification JSON.
- Topic remains canonical. LOTCD, Team, and Week remain projections. Cross-LOTCD accepted evidence extends one Topic's path union without Topic fan-out.

## Critical fixes

1. Immutable approved evidence
   - Added immutable approved Week and Agenda archive contracts/store methods.
   - Topic detail, LOTCD activity, and Team activity resolve current revision refs from the archive.
   - Archive writes are write-once and Topic revision history rejects mismatched overwrites.
   - Tests cover changed rerun payload, removed mutable evidence, and stable archived reads.
2. Multi-week accumulation
   - Affected Topics load prior authorized archived evidence and merge it with new accepted evidence.
   - Previous non-stale claims are retained; missing retained citation prose is carried into the new full body.
   - Tests prove a second-week revision retains the prior claim and citation.

## Important fixes

- Topic target paths are the deterministic union of reviewed existing paths and newly accepted evidence paths before compatibility validation.
- Week views now derive new/changed/resolved/reopened Topics, accepted relation changes, contradictions, and week-scoped pending assignments from persisted revision/build deltas.
- Assignment/relation review resolutions use a JSON journal under the exclusive store lock. Startup recovery replays target+review writes idempotently; fault-injection tests cover a crash between files.
- Relation provenance includes evidence IDs, confidence bounds, creator/source/time/build, review actor/time/state. Review resolution retains authenticated actor/action/time.
- Added backend Team and persisted Week indexes; root Team/Week routes render selectable values.
- Inline `[agenda:ID]` section citations are accessible evidence buttons; Wiki review no longer nests `<main>`; stale-lock wording and immutable-evidence migration notes were corrected.
- Added focused source-metadata, approval-guard, and defensive-copy classification tests.

## RED / GREEN evidence

- RED: `tests/test_wiki_history_integrity.py` initially failed collection because `ArchivedApprovedEvidence` did not exist.
- GREEN backend: `python -m pytest -q tests/test_wiki_history_integrity.py tests/test_topic_wiki_builder.py tests/test_wiki_projections.py tests/test_topic_linker.py tests/test_wiki_store.py tests/test_json_classification_store.py tests/test_knowledge_api.py` -> 86 passed.
- GREEN Web: focused Topic/Team/Week/Review/AppRoutes/API tests -> 27 passed.
- GREEN production Web build: `npm run build` completed (`tsc --noEmit` and Vite).
- `git diff --check` passed.

## Migration and concerns

- No in-place migration can reconstruct immutable historical approval evidence safely from mutable current JSON. Rebuild any pre-contract Wiki data from approved classification weeks; the runbook documents this.
- Focused tests and the Web production build were run as requested. The controller retains the final full-suite gate.

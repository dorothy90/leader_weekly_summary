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

## Second re-review fix wave

### Behavior changes

- Prior prose retention now operates on validated factual chunks. A chunk is retained only when all of its citations support retained structured claims and none support a stale claim; an entire prior section is never concatenated.
- `resolved` and `closed` transitions require terminal hints from the revision's derived `added_agenda_ids`, not accumulated evidence. `reopened` requires a terminal prior state and newly added evidence with an accepted nonterminal hint that is newer by archived Week/`received_at` ordering.
- `added_agenda_ids` is always derived as current authorized evidence minus the previous revision's immutable evidence/source IDs. The builder no longer accepts a caller-supplied delta.
- Relation accept/reject journals now include an immutable Week update. The originating Week receives one accepted/rejected review event, a new Week revision, and immediate history preservation; only accept adds the relation to `new_relation_ids`. Journal replay is invoked before review resolution so a same-process retry converges after a partial write.
- Team and Week root index failures now render alerts. The runbook includes explicit recoverable backup/move, chronological approved-week rebuild, verification, and rollback steps with bounded deployment paths.

### RED / GREEN evidence

- RED: relation accept/reject tests initially observed the unchanged originating Week revision. Stale-section and lifecycle regressions were authored first against the identified whole-section/accumulated-evidence branches; their separate pre-fix output was not retained because the targeted selector ran the relation cases.
- GREEN backend affected set: 90 passed across Topic builder, projections, linker, store, history integrity, and API tests; a final narrowed confirmation after recency/retry hardening passed 59 tests.
- GREEN Web affected set: Team/Week tests passed 10 tests, including root-index alert behavior.
- GREEN production Web build: `npm run build` passed TypeScript checking and Vite production bundling.
- `git diff --check` passed.

### Migration note

- The immutable-evidence rollout remains rebuild-only for pre-contract Wiki data. The runbook now requires an explicit backup move of `/srv/weekly-mail-agent/wiki_data`, a separately created replacement directory, chronological rebuild of a verified approved-week list, and a reversible rollback move. No recursive deletion is part of the procedure.

## Third focused re-review wave

### Claim/prose identity

- Retained claims now use a deterministic identity of normalized claim text plus ordered Agenda IDs. Citation union alone never satisfies retention.
- If the LLM draft lacks that exact identity, the application renders the exact structured claim text followed by its ordered evidence citations in the observations section. It never copies prior free-form prose.
- Final validation recomputes rendered claim identities and requires every retained claim independently. A stale claim sharing the same Agenda neither suppresses nor satisfies a retained claim.
- RED captured: the shared-Agenda regression produced no retained claim text while a different claim's citation incorrectly satisfied the old check.

### Concurrent relation review events

- Review journals now store only a relation acceptance/rejection event delta: relation ID/kind, origin Week, actor, action, and review time. They no longer carry a precomputed complete Week snapshot.
- Under the exclusive store lock, replay loads the latest Week, idempotently sorts/merges events and relation/contradiction sets, versions the latest current snapshot, then finishes relation/review records. Recovery uses the same merge.
- The interleaving regression prepares two deltas from the same base Week, commits both in forward and reverse order, replays one, and proves both orders reach the same final revision containing both relations, one contradiction, no duplicate events, and valid historical predecessors.
- RED captured: the old API rejected event deltas and could only accept a stale complete Week document.

### Runbook and verification

- Every rollout invocation passes `--wiki-data-dir` explicitly.
- The rollout script parses the emitted build JSON, advances only on `published`, pauses on `review_required`, and rejects every other status. The operator must resolve and rerun the same Week before advancing.
- A bounded target verification checks the resolved path, exact Week list, Topics, revision evidence refs, and archive resolution before writers restart.
- GREEN backend affected tests: 92 passed. Week Web contract test: 5 passed. TypeScript/Vite production build passed. Python compile, rollout-doc assertions, and `git diff --check` passed.

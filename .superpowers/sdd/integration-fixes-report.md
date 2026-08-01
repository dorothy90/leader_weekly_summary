# Integration fixes report

Date: 2026-08-01
Base: `1d53c8c`

## Review disposition

All Critical/Important findings were technically valid. Two plan sketches were intentionally superseded by the design requirements:

- Owner backfill now requires an immutable explicit partition predicate, approved expected count, identity-bound checkpoint, abort-on-conflict behavior, and complete reconciliation. The original ownerless-only query was not safe for a mixed corpus.
- Parent/child migration now groups compatible ordered mail/section chunks into bounded parents and rechunks those parents. The original one-legacy-chunk-per-parent sketch did not create larger answer context.

## Implemented

1. Pinned and locked `motor==3.7.1` with `pymongo==4.15.5`; added isolated real-import and container-construction smoke coverage that performs no network request.
2. Applied deterministic Deep/general/mail policy after every parsed model route decision. Long-period, multi-team, and research/report requests override the model; only a deterministic non-mail allowlist reaches general mode.
3. Enforced the configured Fast end-to-end deadline and limited timeout response. Independent planned retrieval tasks run concurrently while the exact six-search, two-rewrite, one-revision, eight-evidence, and context caps remain intact.
4. Added explicit failure semantics: Mongo degradation discloses single-turn context unavailability; OpenSearch failures become retryable `INDEX_UNAVAILABLE`; Deep total branch outage fails transiently while partial branch success may synthesize; the exact BM25 disclosure remains unchanged.
5. Persisted Deep stage, progress, completed subquestions, rounds, safe compressed evidence, and a lease/owner-protected checkpoint. Reclaimed jobs resume from completed research without repeating it; stale lease/cancel protections remain CAS-bound.
6. Reworked owner backfill to be dry-run-first and require partition field/value, expected count, checkpoint identity, zero conflicts, and before/updated/conflicts/after reconciliation. Mixed-corpus coverage verifies foreign partitions and already-owned records do not change.
7. Reworked parent/child migration to group compatible ordered legacy chunks into bounded mail/section parents and bounded children. A legacy embedding is copied only when the new child text exactly equals the legacy text. Canonical collision, target preflight, checkpoint identity, and owner allowlist checks remain.
8. Added bounded structured claim-to-evidence support checks before Fast and Deep publication. Valid citation syntax alone cannot publish an arbitrary unsupported claim; checker failures return deterministic limited/invalid output. Citation target existence and ownership validation remains independent.
9. Added `/ready` for Mongo ping, OpenSearch cluster health, and configured alias readiness while retaining `/health` as liveness.

## Verification evidence

- Focused integration set: `137 passed`.
- Mail-RAG cumulative suite after all changes: `213 passed` during the cumulative gate.
- Full repository: `python -m pytest -q` -> `320 passed, 9 warnings`.
- Compile: `python -m compileall -q app scripts` -> exit 0.
- Formatting: Black reformatted the changed Python files; subsequent verification used formatted sources.
- Whitespace: `git diff --check` -> exit 0.
- Dependency/container smoke: `motor=3.7.1 pymongo=4.15.5`, constructed `FastRAGWorkflow` and `MongoResearchJobStore` without connecting to external services.
- Lock: `uv lock` resolved 152 packages and updated Motor 3.3.2 -> 3.7.1 and PyMongo 4.16.0 -> 4.15.5.

Live OpenSearch, MongoDB, embedding, and LLM quality/latency gates remain environment-dependent and are not claimed by this local verification.

## Follow-up integration review

Commit base: `5090f46510f635129b939c902f5eeb67baacfa53`

- Owner backfill dry-run now persists an identity-bound `planned` checkpoint with partition, eligible, and owned-before counts. Apply rejects missing/mismatched/consumed plans, verifies the planned counts again, aborts on conflicts, requires `eligible_after == 0` and `owned_after == owned_before + updated`, and marks the checkpoint `applied`; repeated matching apply is a no-op. Partition fields are restricted to an explicit immutable keyword allowlist.
- Parent/child splitting now preserves Unicode text exactly without trimming or lossy decode. Oversized legacy chunks split at code-point boundaries into parents no larger than 12,000 UTF-8 bytes and children no larger than 4,000 bytes, with exact parent/child reconstruction. Legacy embeddings survive only on byte-identical children.
- Migration compatibility grouping now includes team, week, mail type, embedding model, source parser/chunker versions, section/source type, and validated safe content locator identity, preventing conflicting metadata from being merged or inherited.
- Deep research now checkpoints after every completed branch through the existing owner/lease CAS. Checkpoints contain successful branches plus failed/unstarted pending queries. Resume excludes completed questions, retries total-outage originals, filters completed questions from gap follow-ups, and preserves the concurrency cap.
- API and operations documentation now state the configured Fast end-to-end deadline coverage and the stronger migration/checkpoint contracts.

Follow-up verification: focused migration/Deep tests `53 passed`; mail-RAG suite `220 passed`; full repository `327 passed, 9 warnings`; compile, Black check, and diff check exited cleanly.

## Final Deep checkpoint review

- Deep checkpoints now retain a stable `round_id` while a round has pending queries. Completed-round accounting remains unchanged across branch crashes and increments exactly once when the final pending query succeeds; a restored completed round can still open the second gap round.
- Each retrieval batch is durably reserved before its calls start, including failed attempts. Repeated total-outage reclaims therefore stop at the hard 12-search budget rather than resetting the counter, while the existing owner/lease checkpoint CAS and four-call concurrency ceiling remain unchanged.
- Removed the obsolete operations text that incorrectly described `FAST_DEADLINE_SECONDS` as unenforced.

Final verification: Deep/worker focused tests `39 passed`; mail-RAG suite `222 passed`; full repository `329 passed, 9 warnings`; compile, Black check, and diff check exited cleanly.

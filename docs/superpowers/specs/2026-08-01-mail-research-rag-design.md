# Mail Research RAG Design

## Objective

Build a new mail chatbot from the already embedded OpenSearch corpus. The new implementation does not import or call `rag_api_opensearch_v3.py`. It may reuse proven ideas, fixtures, and pure helpers, but API, retrieval, workflow, persistence, and validation boundaries are implemented under a new `app/` package.

The chatbot has two execution paths:

- Fast RAG answers bounded factual, statistical, and conversational follow-up questions synchronously.
- Deep Agent performs multi-team, long-period, causal, trend, and report-oriented research as a persistent asynchronous job.

Both paths share the same retrieval, evidence, owner-filtering, and citation-validation services.

## Scope

The implementation includes:

- `POST /v1/chat` with request-body `user_id`
- deterministic and model-assisted Fast/Deep routing
- vector and BM25 retrieval with Reciprocal Rank Fusion (RRF)
- parent/child retrieval and migration from the existing embedded index
- typed evidence and deterministic citation validation
- request-owner filtering on every data access
- bounded multi-turn conversation state
- persistent Deep Agent jobs, progress, cancellation, retry, and reports
- structured tracing and evaluation runners
- explicit BM25-only degradation when the embedding service is unavailable

The implementation does not include:

- web search at chatbot runtime
- group, team, role, or classification-based authorization
- mail sending
- a general-purpose filesystem or shell agent
- replacement of the EWS fetcher or attachment parsers beyond propagating `user_id`

## Source Patterns

The design adapts, rather than copies, the following upstream patterns:

- Chat LangChain: bounded parallel search/read rounds, tool boundaries, guardrails, and citation-target validation
- Open Deep Research: research brief, supervisor, parallel researchers, evidence compression, gap detection, and final synthesis
- GPT Researcher: explicit breadth/depth/concurrency budgets, context compression, and source-linked learnings
- Deep Agents: isolated research contexts and bounded tools without exposing general filesystem capabilities

## Security Boundary

### Request identity

`user_id` remains a required field in the `POST /v1/chat` request body. The application validates that it is a non-empty bounded identifier and creates an immutable `PolicyContext` for the request.

This application does not authenticate the claimed `user_id`. Deployment therefore assumes that an upstream gateway restricts callers and prevents user impersonation. This trust boundary must be stated in API and operations documentation.

### Owner filter

Every indexed mail chunk and derived Wiki document contains a keyword `user_id` field. Every mail, Wiki, parent, statistics, evidence-restore, and source-document lookup includes an exact `term` filter for `PolicyContext.user_id`.

The filter is fail-closed:

- a query cannot execute without a valid `PolicyContext`
- documents without `user_id` are not returned
- parent expansion repeats the owner filter instead of trusting the child hit
- cached and remembered evidence is reauthorized on every request
- final citations must belong to the same request owner

No group ACL, team ACL, or role hierarchy is part of this release. `team` remains a search facet and is not an authorization field.

### Ingestion and migration

The mailbox pipeline reads a required `MAIL_USER_ID`, writes it to `meta.json`, and propagates it to every child, parent, and Wiki document. An explicit backfill command assigns one supplied owner to a selected legacy corpus. It never guesses ownership from titles, teams, recipients, or filesystem paths.

Backfill supports dry-run, checkpointing, count reconciliation, and rejection of an empty owner. Until backfill is complete, legacy documents without `user_id` remain invisible.

## Architecture

```text
POST /v1/chat
  -> validate request and create PolicyContext
  -> contextualize the turn
  -> classify complexity
  -> apply deterministic routing policy
      -> Fast RAG graph
      -> Deep research job

Fast RAG graph
  -> plan bounded retrieval
  -> parallel vector/BM25 searches
  -> RRF, parent expansion, deduplication, reranking
  -> grade evidence
  -> optional query rewrite
  -> generate answer
  -> validate citations and ownership
  -> finalize

Deep Agent graph
  -> build research brief
  -> plan sub-questions
  -> run bounded parallel researchers
  -> compress evidence while retaining evidence IDs
  -> detect gaps and conflicts
  -> optional additional research round
  -> synthesize report
  -> validate citations and ownership
  -> persist result
```

### API layer

The new FastAPI application exposes:

- `POST /v1/chat`: returns a Fast RAG answer or a queued Deep Agent job descriptor
- `POST /v1/research/{job_id}/status`: returns owner-scoped job status, progress, and completed output
- `POST /v1/research/{job_id}/events`: streams owner-scoped progress events
- `POST /v1/research/{job_id}/cancel`: requests owner-scoped cancellation
- `POST /v1/research/{job_id}/retry`: creates or resumes an idempotent retry where policy permits
- `GET /health`: reports process health and dependency readiness without secrets

`POST /v1/chat` accepts `user_id`, `message`, optional `conversation_id`, optional team/week/mail-type filters, and `response_mode` (`auto`, `fast`, or `deep`). A Fast result contains `mode`, `answer`, typed references, quality status, fallback disclosures, and trace ID. A Deep result contains `mode`, `job_id`, `status`, plan summary, and trace ID.

Every research job endpoint requires `user_id` in its request body and compares it with the stored owner. It never exposes a job solely because the caller knows its ID. The events endpoint uses a streaming POST response so this body contract remains consistent.

### Router

The model produces a typed `RouteDecision` containing route, reason code, confidence, estimated searches, clarification requirement, and requested output. Deterministic policy then overrides unsafe or inconsistent classifications.

Fast RAG handles greetings, statistics, a single team/week/fact, source requests, and resolvable follow-ups. Deep Agent handles three or more teams, four or more weeks of trend analysis, cross-source causal synthesis, and report or presentation requests. Question length alone never selects Deep Agent.

### Shared retrieval service

Only `RetrievalService` may query the mail and Wiki indices. It accepts `PolicyContext` separately from model-generated search input so the model cannot remove or replace the owner.

For each search task it:

1. normalizes team, week, and mail-type facets
2. builds the mandatory `user_id` filter
3. runs vector and BM25 searches concurrently
4. fuses independent rankings with RRF, initially using `k=60`
5. groups child hits by parent and limits duplicate children
6. fetches parent text with the same owner filter
7. reranks and selects a diverse evidence set within the context budget

If the v2 parent index is not active, the compatibility adapter expands a hit using adjacent chunks with the same `user_id`, `mail_id`, and neighboring `part_index` values. This permits the new system to use the existing embeddings during migration.

### Parent/child indices

The target index layout remains:

- `weekly_mail_child_v2`: small vector/BM25 retrieval chunks
- `weekly_mail_parent_v2`: larger answer-context sections
- `wiki_summaries_v2`: owner-scoped derived summaries

Versioned physical indices sit behind read aliases. Backfill writes v2 documents without changing the current index, shadow evaluation compares v1 and v2, and alias cutover is reversible.

### Evidence contract

OpenSearch hits are never passed directly to a model. They are normalized to a typed `Evidence` object containing:

- stable evidence ID
- source type and document ID
- parent ID when present
- title and bounded excerpt
- team, week, and opaque source locator
- retrieval and rerank scores
- owner `user_id`
- owner-decision ID and content hash

The model sees stable evidence IDs but does not receive internal filesystem paths or credentials.

### Fast RAG graph

Fast RAG is synchronous and bounded:

- at most six search tasks
- at most two query rewrites
- at most one answer revision
- at most eight final evidence objects
- at most 16,000 context tokens

It generates a limited answer when evidence is absent or incomplete. Statistics use owner-filtered OpenSearch aggregations rather than asking the model to count retrieved documents.

### Deep Agent graph

Deep Agent runs as a persistent research job. A planner generates objective sub-questions, researchers run with isolated contexts, and only compressed evidence plus evidence IDs returns to the supervisor. Default limits are configurable but always finite: sub-question count, search breadth, research depth, concurrency, tokens, elapsed time, and evidence count.

The job store persists policy owner, status, plan, attempts, progress, evidence references, result, error code, and cancellation state. A separate worker claims jobs with a lease so process restarts do not lose work. Cancellation and retry are idempotent.

### Conversation memory

Conversation records are owned by `(conversation_id, user_id)`. They retain a bounded recent window, structured topic/team/week/mail-type filters, and at most eight cited evidence snapshots. Full mail bodies are not copied into long-term memory.

Accessing a conversation ID owned by another user returns an authorization error. Reusing prior evidence requires existence, content-hash, owner, and relevance checks.

## Failure Behavior

- Empty or invalid `user_id`: return `INVALID_USER_ID` without retrieval.
- Missing owner filter: reject the retrieval call before OpenSearch execution.
- Embedding service unavailable: run BM25-only retrieval and include this exact user-visible disclosure in the answer: `임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다.`
- Wiki unavailable: continue with owner-filtered mail retrieval.
- MongoDB unavailable during synchronous chat: use single-turn mode and mark multi-turn context unavailable.
- OpenSearch unavailable: return a transient error; never generate an ungrounded answer.
- LLM timeout after bounded retry: return an evidence-only limited response when evidence exists.
- Validator failure: remove unsupported claims or return a limited answer; never expose an answer that cites nonexistent or differently owned evidence.
- Partial Deep Agent branch failure: synthesize from successful evidence and identify the uncovered scope.
- Worker restart: reclaim expired job leases and resume from persisted state.

Error results use stable codes such as `INVALID_USER_ID`, `UNAUTHORIZED_RESOURCE`, `INDEX_UNAVAILABLE`, `EMBEDDING_UNAVAILABLE`, `RETRIEVAL_TIMEOUT`, `NO_EVIDENCE`, `BUDGET_EXCEEDED`, and `JOB_CANCELLED`.

## Observability

Every request and research job records a trace ID, owner hash, route, filters without raw query content where unnecessary, retrieval mode, index/model/prompt versions, evidence document IDs, latency, attempts, token usage, validation result, and error code. Raw mail content and secrets are excluded from audit logs.

Streaming or polling progress exposes only verifiable stages and counts. Internal chain-of-thought is never returned.

## Testing and Evaluation

### Unit tests

- `user_id` validation and mandatory filter construction
- week/team/mail-type normalization
- RRF calculation and deterministic tie handling
- parent/child grouping, adjacent legacy expansion, and deduplication
- deterministic router overrides
- evidence compression retaining source IDs
- citation existence and owner validation
- bounded rewrite, revision, research breadth, depth, and cancellation

### Contract and security tests

- vector, BM25, Wiki, parent, aggregation, and evidence-restore query bodies all contain the same owner filter
- a differently owned top-scoring document never reaches retrieval results, model context, logs, memory, or citations
- missing `user_id` metadata fails closed
- conversation and research job ownership is enforced
- embedding failure uses BM25 and includes the required disclosure

### Integration and scenario tests

- index a synthetic multi-owner corpus into test OpenSearch
- validate vector/BM25/RRF ordering and parent expansion
- exercise Fast factual, statistics, follow-up, no-evidence, and fallback scenarios
- exercise Deep multi-team, multi-week, partial failure, gap research, cancellation, retry, and final-report scenarios
- verify Wiki and MongoDB degradation behavior

### Evaluation runner

The repository contains a versioned gold-case format and runner for 30–50 or more cases. A case records expected route, owner, allowed document IDs, relevant parents, answer facts, forbidden facts, and expected fallback mode. The runner measures retrieval Recall@10, citation precision, unsupported-claim rate, router accuracy, multi-turn accuracy, and owner leakage.

Owner leakage must be zero. Citation validation failures cannot be averaged away. Quality and latency targets from `docs/deep_agent_mail_chatbot_review.md` remain release gates when a representative corpus and configured model services are available.

## Delivery Sequence

1. Domain contracts, owner filter, and retrieval query contracts
2. Existing-index compatibility retrieval with hybrid RRF and evidence normalization
3. New API, router, and Fast RAG graph
4. conversation ownership and evidence reauthorization
5. Deep Agent job store, worker, graph, cancellation, and report API
6. parent/child templates, backfill, shadow evaluation, and alias cutover tooling
7. Wiki owner propagation, observability, evaluation datasets, and operations documentation

Each behavior is developed test-first. External service integration is behind protocols and exercised with deterministic fakes before optional live integration tests.

## Acceptance Criteria

- The new application does not import or call the legacy RAG API module.
- Both Fast RAG and Deep Agent paths operate through the shared owner-filtered retrieval service.
- Every retrieval and restored-evidence access is scoped to request-body `user_id`.
- Existing embedded data can be searched through the compatibility adapter after explicit owner backfill.
- Vector and BM25 rankings are fused with RRF; relevant parent context is expanded without cross-owner access.
- Every factual answer or report claim uses a valid, existing, same-owner citation.
- Insufficient evidence produces a limited answer rather than speculation.
- Embedding failure produces BM25-only results and the required disclosure.
- Fast RAG loops and Deep Agent research are bounded and observable.
- Deep jobs persist, cancel, retry, and recover idempotently.
- Multi-turn conversations cannot be read or continued by a different `user_id`.
- Automated tests cover the security invariant and both workflows.
- Evaluation tooling reports all release metrics and treats nonzero owner leakage as failure.

# Mail Research RAG Operations

## Architecture and hard limits

`POST /v1/chat` has one execution path: `MultiSourceAgenticWorkflow`. The
typed agent policy chooses among allowlisted Mail, Calendar, Wiki, statistics, and
domain-knowledge tools; clients cannot choose a route or index. Execution is
bounded to four agent iterations and eight evidence objects. Each OpenRouter
request has a 150-second default safety limit. These limits are safety ceilings,
not production latency guarantees.

Every OpenSearch query and MongoDB lookup is scoped by exact request-body `user_id`. `team` is only a facet. Missing owners remain invisible; never infer an owner from team, index, mail text, or path.

## Environment

Set secrets through the process environment or secret manager, never command-line literals or committed `.env` files.

```text
OPENSEARCH_HOST
OPENSEARCH_PORT
OPENSEARCH_USER
OPENSEARCH_PASSWORD
OPENSEARCH_USE_SSL
OPENSEARCH_VERIFY_CERTS
MAIL_CHILD_INDEX
MAIL_PARENT_INDEX
WIKI_INDEX
DOMAIN_KNOWLEDGE_INDEX
MAIL_INDEX_ALIAS
CALENDAR_INDEX_ALIAS
DEFAULT_USER_TIMEZONE
MULTI_SOURCE_DEMO
OPENROUTER_API_KEY
OPENROUTER_BASE_URL
OPENROUTER_LLM_MODEL
OPENROUTER_EMBEDDING_MODEL
OPENROUTER_REQUEST_TIMEOUT_SECONDS
MONGO_URI
MONGO_DB
MAIL_CONTENT_ROOT
```

The multi-source defaults are:

```dotenv
DOMAIN_KNOWLEDGE_INDEX=syld_gpt
MAIL_INDEX_ALIAS=ews-mail-active
CALENDAR_INDEX_ALIAS=ews-calendar-active
DEFAULT_USER_TIMEZONE=Asia/Seoul
MULTI_SOURCE_DEMO=false
```

`MAIL_INDEX_ALIAS` and `CALENDAR_INDEX_ALIAS` must be read aliases, not
physical versioned index names. Agent/model output cannot select an index,
owner, or raw OpenSearch DSL. Backend code always injects
`employee_id == policy.user_id` and `is_active == true` for Mail; Calendar
search and expansion also inject `is_cancelled == false`. Calendar expansion
first verifies a same-owner, active, non-cancelled parent event by its validated
canonical ID without applying requested attachment/output filters. It queries
the related bundle only after that parent gate succeeds.

`OPENROUTER_API_KEY`, a reachable `MONGO_URI`, and reachable OpenRouter and OpenSearch endpoints are required for normal service operation. The default AI endpoint, models, and per-request timeout are:

```dotenv
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_LLM_MODEL=google/gemma-4-26b-a4b-it:free
OPENROUTER_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
OPENROUTER_REQUEST_TIMEOUT_SECONDS=150
```

The OpenSearch index and query path must use the same Qwen3-Embedding-8B output dimension. If OpenRouter embedding generation fails, retrieval degrades to owner-filtered BM25 and includes the embedding-unavailable disclosure.

When `OPENSEARCH_USER` is configured, provide `OPENSEARCH_PASSWORD`. Production requires TLS (`OPENSEARCH_USE_SSL=true`) and certificate verification (`OPENSEARCH_VERIFY_CERTS=true`) against a trusted CA. Plain HTTP is for explicitly isolated local development only. Do not disable certificate verification to work around trust failures.

Mail collection separately requires `MAIL_USER_ID`. It is the explicit owner written into `meta.json`; it is not derived from team membership.

## Start processes

Install the repository requirements, then start the API from the repository root:

```bash
python - <<'PY'
import uvicorn
from app.api.dependencies import build_container
from app.api.main import create_app

uvicorn.run(create_app(build_container()), host="127.0.0.1", port=8000)
PY
```

For the local multi-source demo, no OpenSearch, MongoDB, embedding service, LLM
credential, or `.env` file is required:

```bash
MULTI_SOURCE_DEMO=true uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

The CLI and alternate-question commands are documented in
[`multi_source_demo.md`](multi_source_demo.md).

`POST /v1/chat` never enqueues a research job and does not require a research
worker. The `/v1/research/*` endpoints remain available for independently
created legacy jobs; operating a legacy worker requires separate, explicit
workflow wiring and is outside the direct chat service container.

Terminate API processes gracefully during deployment. Set
`OPENROUTER_REQUEST_TIMEOUT_SECONDS` to the per-request LLM and embedding
safety limit.

## RAG verification console

The existing React/Vite frontend includes a development verification console at
`/rag`. It calls the real FastAPI service and displays safe request/response
JSON, HTTP status, latency, trace/conversation identifiers, agent tool calls and
judge decisions, citation status, references, disclosures, and execution node
events. It never authenticates callers and is not an authorization boundary;
the upstream gateway must still bind the verified principal to request-body
`user_id`.

Start the API as shown above, then start the frontend in another terminal:

```bash
cd frontend
npm install
npm run dev:rag
```

Open `http://127.0.0.1:5180/rag`. The dedicated `dev:rag` command uses strict port `5180`, so it fails visibly instead of silently opening another application when a port is occupied. Vite proxies `/api/*` to `http://127.0.0.1:8000` by default. To target another development API without adding permissive CORS middleware:

```bash
cd frontend
RAG_API_TARGET=http://127.0.0.1:8010 npm run dev:rag
```

The console has no route or execution-mode selector. Every request uses the
multi-source agent. The inspector shows server-provided execution status,
stage, safe error code, retryability, actual search/evidence counts, duration,
tool calls, judge decisions, retrieval mode, nullable citation state,
references, disclosures, and an ordered Nodes timeline. Node diagnostics
contain only timing, counts, fallback state, and exception class. Requests,
responses, `user_id`, and event payloads remain in React memory only and are
not written to browser storage.

Mongo conversation records are dual-read compatible. Existing `messages` remain
readable; new writes also include bounded `turns`. Only successful
history-eligible turns are projected into model context. Limited/failed turns
are excluded from model history. No OpenSearch reindex or embedding migration
is required for this change. Verify every request body contains the
authenticated principal's `user_id`; the application still depends on the
upstream gateway to bind it.

## Health and recovery

Process liveness:

```bash
curl --fail --silent http://127.0.0.1:8000/health
curl --fail --silent http://127.0.0.1:8000/ready
```

`/health` returns `{"status":"ok"}` and is liveness only. In production,
`/ready` returns success only after MongoDB ping, OpenSearch cluster health,
and configured alias checks pass, including `MAIL_INDEX_ALIAS` and
`CALENDAR_INDEX_ALIAS`. With `MULTI_SOURCE_DEMO=true`, readiness instead
reports the in-memory OpenSearch, Mongo, alias, and rule-based agent
dependencies as ready without external probes. Before production cutover,
also verify one controlled embedding and LLM request without logging inputs or
outputs.

For a failed or cancelled job, call `POST /v1/research/{job_id}/retry` with the verified owner in the body. For a stuck running job, first confirm that no worker still owns its lease; the worker will reclaim it after expiry. Do not edit lease tokens or job owners manually. Cancellation is requested through the API so queued/running transitions remain consistent.

## Owner backfill

The user backfill targets only documents where `user_id` is missing. Always run the default dry-run first and reconcile its eligible count to an approved owner manifest:

```bash
python scripts/backfill_user_id.py --index weekly_mail_v1 --user-id kim \
  --partition-field corpus_id --partition-value legacy-2026-h1 \
  --expected-count 1240 --checkpoint var/migrations/kim-owner.json
python scripts/backfill_user_id.py --index weekly_mail_v1 --user-id kim \
  --partition-field corpus_id --partition-value legacy-2026-h1 \
  --expected-count 1240 --checkpoint var/migrations/kim-owner.json --apply
```

Dry-run writes a `planned` checkpoint containing the immutable identity plus partition, eligible, and already-owned counts. Apply refuses a missing, changed, or non-planned checkpoint, aborts on conflicts, and verifies that eligible documents reach zero and the owned count increases by exactly the update count before marking the checkpoint `applied`. Repeating apply with that same applied identity is a no-op. Only the CLI's approved immutable keyword partition fields are accepted. Run a separate approved command per owner partition. A team-to-owner guess is not an owner manifest. Stop on any unexpected count; documents not explicitly backfilled remain invisible.

## Parent/child migration and shadow comparison

Create the strict v2 indices from `index_templates/weekly_mail_parent_v2.json` and `index_templates/weekly_mail_child_v2.json`. Parent/child migration is also dry-run by default. The same command with `--apply` writes only after collision preflight succeeds:

```bash
python scripts/backfill_parent_child.py \
  --source-index weekly_mail_v1 \
  --parent-index weekly_mail_parent_v2_20260801 \
  --child-index weekly_mail_child_v2_20260801 \
  --owner kim \
  --checkpoint var/migrations/kim-parent-child.json

python scripts/backfill_parent_child.py \
  --source-index weekly_mail_v1 \
  --parent-index weekly_mail_parent_v2_20260801 \
  --child-index weekly_mail_child_v2_20260801 \
  --owner kim \
  --checkpoint var/migrations/kim-parent-child.json \
  --apply
```

The checkpoint identity binds source/target indices, owner-manifest digest, parser/chunker versions, and batch size. A completed checkpoint cannot be reused with different inputs. Compatible legacy chunks are grouped only when owner, mail/section identity, facets, embedding model/version metadata, and safe content locator agree. Unicode splitting preserves every character exactly; parents and children remain within their byte budgets, and a legacy embedding is retained only for a byte-identical child.

Prepare a UTF-8 file with one synthetic or approved query per line, then compare v1/v2 rankings. Output contains a query hash, owner-filtered document IDs, overlap, and timings—never query text or mail evidence:

```bash
python scripts/shadow_retrieval.py \
  --user-id kim \
  --queries evals/shadow_queries.txt \
  --output var/shadow-kim.jsonl \
  --v1-index weekly_mail_v1 \
  --v2-index weekly_mail_child_v2_20260801 \
  --v2-parent-index weekly_mail_parent_v2_20260801
```

Review zero owner leakage, collisions, missing-owner quarantine, retrieval overlap, fallback rate, and latency before alias changes.

## Alias cutover

Multi-source Mail and Calendar tools resolve their targets from
`MAIL_INDEX_ALIAS` and `CALENDAR_INDEX_ALIAS` on every service construction.
An atomic alias cutover therefore requires no agent code change. Update an
alias only after owner isolation, lifecycle filtering, event-expansion, and
citation smoke tests pass; then restart the service if its configured alias
name changed.

Set explicit names and perform one atomic Alias cutover through the configured client. Never point both owner-filtered reads and unowned legacy reads at the same alias.

```bash
export OLD_MAIL_CHILD_INDEX=weekly_mail_v1
export NEW_MAIL_CHILD_INDEX=weekly_mail_child_v2_20260801
export MAIL_CHILD_ALIAS=weekly_mail
python - <<'PY'
import os
from app.api.dependencies import build_opensearch_client

client = build_opensearch_client()
client.indices.update_aliases(body={"actions": [
    {"remove": {"index": os.environ["OLD_MAIL_CHILD_INDEX"], "alias": os.environ["MAIL_CHILD_ALIAS"]}},
    {"add": {"index": os.environ["NEW_MAIL_CHILD_INDEX"], "alias": os.environ["MAIL_CHILD_ALIAS"]}},
]})
PY
```

Repeat atomically for the parent and Wiki aliases only after their independent validation. Restart API/worker processes if their configured alias names changed, then run owner isolation and citation smoke tests.

## Rollback

Rollback is an atomic reverse alias update; do not delete v2 indices during an incident. Stop new API traffic and workers, set the explicit old/new names above, and run:

```bash
python - <<'PY'
import os
from app.api.dependencies import build_opensearch_client

client = build_opensearch_client()
client.indices.update_aliases(body={"actions": [
    {"remove": {"index": os.environ["NEW_MAIL_CHILD_INDEX"], "alias": os.environ["MAIL_CHILD_ALIAS"]}},
    {"add": {"index": os.environ["OLD_MAIL_CHILD_INDEX"], "alias": os.environ["MAIL_CHILD_ALIAS"]}},
]})
PY
```

Restore the prior application configuration, restart processes, verify the alias target and owner filtering, and retain v2 indices/checkpoints for analysis. MongoDB conversation and research-job stores are separate from index aliases; rollback does not rewrite their owners or job states.

## Evaluation and release gate

Produce a JSON result for every case in `evals/datasets/mail_rag_gold.json`, then run:

```bash
python scripts/evaluate_mail_rag.py \
  --cases evals/datasets/mail_rag_gold.json \
  --corpus evals/datasets/mail_rag_synthetic_corpus.json \
  --results var/mail-rag-results.json
```

The committed corpus contains the synthetic source records and is the ownership manifest. Every source record declares `document_id`, optional `parent_id`, exact `user_id`, source type, text, and canonical facts. The gold dataset may reference only IDs owned by that case owner.

The results file is a strict JSON array keyed by case `id`. Every row must supply `route`, retrieved `document_ids` and `parent_ids`, cited document IDs in `citations`, `answer_kind`, final `answer`, `answer_facts`, `fallback_mode` (`hybrid` or `bm25`), `disclosures`, and `multiturn_passed` for multi-turn cases. Citation entries are document IDs, not `[S#]` display labels. Extra fields, missing fields, duplicate case IDs, unknown case IDs, and malformed values increment `malformed_results` and fail the release.

Fact reporting uses a deterministic adapter contract. A supported result has `answer_kind="supported"`, a nonempty unique ordered `answer_facts` list, and `answer` composed only of the same ordered markers, for example `"[F:ALPHA 수율 저하] [F:온도 센서 교정]"`. An abstention has `answer_kind="abstention"`, `answer="[ABSTAIN]"`, and an empty fact list. This evaluation representation is derived from the user-facing response; arbitrary prose is invalid because it could hide unreported claims.

The command reports retrieval recall, citation precision, unsupported-claim rate, overall/Fast/Deep router accuracy, multi-turn accuracy, fallback accuracy, malformed result count, and owner leakage. Every returned parent ID, document ID, and citation is checked against both the committed owner manifest and the case allowlist. Foreign, unrecognized, or disallowed IDs count as leakage. The command exits nonzero unless every deterministic metric meets the strict baseline. Any owner leakage greater than zero is an unconditional release failure.

The embedding failure case must use BM25 only and include this exact disclosure:

> 임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다.

Local unit tests and the synthetic evaluator do not establish production latency or quality. Record live index/corpus versions, command output, and service endpoints only after a configured integration run; otherwise mark live-service verification as pending.

### Verification record: 2026-08-01

End-to-end live-service verification still requires reachable OpenSearch, MongoDB, and OpenRouter services. Production latency, retrieval quality, live index versions, and live corpus versions must be verified in the deployment environment.

The deterministic evaluator contract was exercised with a strict oracle adapter generated from the committed gold expectations. This checks evaluator behavior, owner-leakage rejection, and exact fallback-disclosure enforcement; it is not application-output or live-quality evidence. Exact evaluator command:

```bash
python scripts/evaluate_mail_rag.py \
  --cases evals/datasets/mail_rag_gold.json \
  --corpus evals/datasets/mail_rag_synthetic_corpus.json \
  --results /tmp/mail-rag-eval.WVIOJp/results.json
```

Recorded output:

```json
{
  "cases": 30,
  "retrieval_recall": 1.0,
  "citation_precision": 1.0,
  "unsupported_claim_rate": 0.0,
  "router_accuracy": 1.0,
  "fast_route_accuracy": 1.0,
  "deep_route_accuracy": 1.0,
  "multiturn_accuracy": 1.0,
  "fallback_accuracy": 1.0,
  "owner_leakage": 0,
  "malformed_results": 0,
  "passed": true
}
```

Committed artifact versions used by that offline check:

```text
mail-rag-synthetic-v1
mail_rag_gold.json sha256=e86b00a6a9ecff1d99070daf4724b80c4a8377bfad8c869c9f378b063a521b91
mail_rag_synthetic_corpus.json sha256=ece617ba2616632fb2ff3d1982e13a96bd530f17489e82a0aa1ba3eaafc5ee7a
weekly_mail_child_v2.json sha256=5332fd8db81421d00cf7a6ccdc99c6cae3d606bdea3de11faeed587815b5a3ea
weekly_mail_parent_v2.json sha256=fb376570b9a7666a5aadfd717e7e785d02961207b58cc7c68ec67ee152ba3a15
wiki_summaries_v2.json sha256=cb92c697633128a8fe768cb36bd8c528f76648461980a359c17220b4d9aad56f
```

## Tracing and logging

API middleware, shared retrieval, Fast/General/Deep workflows, and research workers emit through an injected trace sink. `build_container(trace_sink=...)` connects one sink to the API, retrieval, and Fast workflow; the worker bootstrap passes the same sink to `DeepResearchWorkflow` and `ResearchWorker`. The default is an explicit no-op sink, and sink failures never fail requests or jobs.

Emit only allowlisted `TraceEvent` fields: opaque trace ID, fixed node/status labels, duration, SHA-256 index/prompt/model version hashes, SHA-256 owner/query/document/job hashes, counts, mode, route, attempt, and error class. Never pass or log raw `user_id`, query or mail text, evidence/excerpts, citations containing raw content, filesystem paths, credentials, configured index/model strings, exception messages, prompts/responses, or chain-of-thought. Hash values before constructing the event; sinks receive only validated `TraceEvent` instances.

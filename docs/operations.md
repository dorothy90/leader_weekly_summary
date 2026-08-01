# Mail Research RAG Operations

## Architecture and hard limits

Fast RAG and Deep Research are separate systems that share owner-filtered retrieval and citation validation. Run the API and the research worker as separate processes. Fast RAG is synchronous and bounded to six searches, two rewrites, one answer revision, eight evidence objects, and a configured 16,000 context tokens budget that is enforced conservatively as 16,000 UTF-8 bytes.

Deep Research is persistent and bounded to eight initial sub-questions, four follow-up questions, 12 searches, two rounds, four concurrent searches, 32 evidence objects, 32,000 model-input bytes, one report revision, an 8,000-byte report, and 120 seconds elapsed time. The worker lease defaults to 180 seconds. These limits are safety ceilings, not production latency guarantees.

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
OPENROUTER_API_KEY
OPENROUTER_BASE_URL
EMBEDDING_MODEL
LLM_MODEL
MONGO_URI
MONGO_DB
FAST_DEADLINE_SECONDS
MAIL_CONTENT_ROOT
```

`OPENROUTER_API_KEY`, a reachable `MONGO_URI`, and reachable model endpoints are required for normal service operation. When `OPENSEARCH_USER` is configured, provide `OPENSEARCH_PASSWORD`. Production requires TLS (`OPENSEARCH_USE_SSL=true`) and certificate verification (`OPENSEARCH_VERIFY_CERTS=true`) against a trusted CA. Plain HTTP is for explicitly isolated local development only. Do not disable certificate verification to work around trust failures.

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

Start one or more separate worker processes. This command uses the same retrieval and LLM objects as Fast RAG while consuming persistent Deep jobs:

```bash
python - <<'PY'
import asyncio
from app.api.dependencies import build_container
from app.graphs.deep_research import DeepResearchWorkflow
from app.workers.research import ResearchWorker

async def main():
    services = build_container()
    workflow = DeepResearchWorkflow(
        services.fast.retrieval,
        services.fast.llm,
        trace_sink=services.traces,
    )
    worker = ResearchWorker(services.jobs, workflow, trace_sink=services.traces)
    while True:
        if not await worker.run_once():
            await asyncio.sleep(1)

asyncio.run(main())
PY
```

Terminate both processes gracefully during deployment. Running jobs are recovered after their lease expires; a stale worker cannot complete a job claimed under a newer lease token.

Set `FAST_DEADLINE_SECONDS` to the synchronous end-to-end Fast RAG deadline. The deadline covers planning, all bounded retrieval rounds, generation, citation validation, and structured claim-support validation; timeout returns a limited response.

## Health and recovery

Process liveness:

```bash
curl --fail --silent http://127.0.0.1:8000/health
curl --fail --silent http://127.0.0.1:8000/ready
```

`/health` returns `{"status":"ok"}` and is liveness only. `/ready` returns success only after MongoDB ping, OpenSearch cluster health, and configured alias checks pass. Before cutover, also verify one controlled embedding and LLM request without logging inputs or outputs.

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

Live-service verification is **pending**. The verification environment had no configured `OPENSEARCH_HOST`, `OPENSEARCH_PASSWORD`, `MONGO_URI`, `OPENROUTER_API_KEY`, or `OPENROUTER_BASE_URL`, and the repository has no registered integration-test marker. No live OpenSearch, MongoDB, embedding, or LLM request was attempted. Production latency, retrieval quality, live index versions, and live corpus versions are therefore unverified.

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

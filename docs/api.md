# Mail Research RAG API

## Trust and ownership boundary

Every owner-sensitive endpoint takes `user_id` from the request body. The service does not authenticate that value. A trusted upstream gateway must authenticate the caller, overwrite or bind the request-body `user_id` to the verified principal, and reject requests that cannot be bound. Client identity headers are ignored by the application.

team is a search facet, not an authorization boundary. Every retrieval, conversation, mail-content, Wiki, statistics, and research-job lookup still requires exact `user_id` ownership. Documents without `user_id` are invisible until an explicit owner backfill. Cross-owner and nonexistent resources intentionally share the same not-found response.

All JSON requests reject undeclared fields. `user_id` is 1–128 characters after trimming and must satisfy the application owner format. `conversation_id`, when supplied, must match `^[A-Za-z0-9_.:-]+$` and is owner-scoped.

## POST /v1/chat

Request body:

```json
{
  "user_id": "kim",
  "message": "최근 4주 YIELD팀 추세를 조사해줘",
  "conversation_id": "optional-owner-scoped-id",
  "filters": {
    "teams": ["YIELD팀"],
    "weeks": ["2026-27", "2026-28", "2026-29", "2026-30"],
    "mail_type": "weekly_report"
  },
  "response_mode": "auto"
}
```

`response_mode` is `auto`, `fast`, or `deep`. `mail_type` is `weekly_report`, `daily_report`, `other`, or null. Fast and Deep stay separate executors; `auto` only selects one route. The same synchronous endpoint returns the final result.

The development RAG verification console can send `auto`, `fast`, or `deep`. It is available at the Vite frontend's `/rag` path and proxies API calls through `/api`. The console is a QA client, not an authentication or authorization layer.

Fast and Deep have no workflow-wide deadline. Each OpenRouter LLM or embedding request has the configured `OPENROUTER_REQUEST_TIMEOUT_SECONDS` safety limit, which defaults to 150 seconds.

```json
{
  "conversation_id": "9fd...",
  "mode": "fast_rag",
  "answer": "확인된 답변 [S1]",
  "references": [{
    "evidence_id": "S1",
    "source_type": "mail",
    "document_id": "opaque-id",
    "title": "주간 보고",
    "excerpt": "검증된 발췌",
    "team": "YIELD팀",
    "week": "2026-30"
  }],
  "quality": {
    "citation_valid": true,
    "limited_answer": false,
    "retrieval_mode": "hybrid"
  },
  "disclosures": [],
  "trace_id": "opaque-trace-id",
  "execution": {
    "status": "succeeded",
    "failure_stage": null,
    "error_code": null,
    "retryable": false,
    "search_count": 2,
    "evidence_count": 1,
    "duration_ms": 842,
    "include_in_llm_history": true,
    "node_runs": [{
      "sequence": 1,
      "node_name": "router.route",
      "status": "ok",
      "started_ms": 0,
      "duration_ms": 4180,
      "attempt": 1,
      "input": {"history_messages": 2},
      "output": {"task_count": 1},
      "error_class": null
    }]
  },
  "job_id": null,
  "status": null,
  "plan_summary": null
}
```

Deep routing returns HTTP 200 after the bounded Deep workflow completes. `mode` is
`deep_research`; `answer`, `references`, and `quality` are populated in the same
response while `job_id`, `status`, and `plan_summary` remain null. General, Fast,
and Deep therefore share one `POST /v1/chat` request-response contract.

Automatic routing also has two deterministic system routes. Questions such as “왜 답변을 못했어?” use `diagnostic` and read the previous persisted execution state. Questions such as “뭐가 임베딩돼 있어?” use `corpus_info` and run exact-`user_id` OpenSearch aggregations for counts, teams, weeks, mail types, recent titles, and embedding-model metadata. Neither route asks the LLM to invent operational facts.

`execution.status` is `succeeded`, `limited`, or `failed`. Completed execution failures still return HTTP 200 with `answer: null`; `failure_stage` and `error_code` explain the bounded failure. `quality.citation_valid` is null when citation validation did not run, and `retrieval_mode` is `not_started` when search never began. Embedding API failure continues with BM25 and includes the exact Korean fallback disclosure.

`execution.node_runs` contains at most 64 request-correlated Router, Fast, Deep, embedding, and OpenSearch steps ordered by start sequence. It exposes timing, safe counts, retrieval mode, fallback state, attempt, and exception class only. It never contains raw questions, prompts, mail content, document IDs, credentials, exception messages, or reasoning.

Conversation storage writes a bounded turn envelope and compatibility messages. Only successful turns with `include_in_llm_history=true` are sent back to Router/General/Fast/Deep models. Limited and failed turns remain available for deterministic diagnostics but cannot contaminate later model context. Verified evidence may be reused only after exact owner validation and deduplication.

## Research job endpoints

Each endpoint below requires `{"user_id":"kim"}` in the request body and applies exact owner filtering.

- `POST /v1/research/{job_id}/status` returns job status and any completed result.
- `POST /v1/research/{job_id}/cancel` idempotently requests cancellation. Queued work becomes `cancelled`; running work becomes `cancelling` until a worker acknowledges it.
- `POST /v1/research/{job_id}/retry` requeues only a `failed` or `cancelled` job.
- `POST /v1/research/{job_id}/events` returns `text/event-stream`; each event contains only opaque `job_id`, `status`, and `progress`.

Status, cancel, and retry responses use this schema:

```json
{
  "job_id": "opaque-id",
  "status": "completed",
  "progress": 100,
  "plan_summary": "4개 주 조사",
  "result_markdown": "근거 기반 보고서 [S1]",
  "references": [],
  "disclosures": [],
  "error_code": null
}
```

Valid statuses are `queued`, `running`, `completed`, `failed`, `cancelled`, and `cancelling`.

## Mail content and health

`POST /v1/mail-content/{content_id}` takes `{"user_id":"kim"}` and returns owner-approved HTML as a sandboxed `text/html` file. The `content_id` is opaque; raw filesystem paths are neither accepted nor returned. Missing, malformed, and cross-owner content all fail closed.

`GET /health` returns `{"status":"ok"}`. It is a process liveness probe only; it does not prove OpenSearch, MongoDB, embedding, or LLM readiness.

`GET /ready` checks MongoDB ping, OpenSearch cluster health, and all configured mail-parent/Wiki aliases. It returns `200` with `status=ready` only when every check succeeds; otherwise it returns `503` with safe per-dependency availability states.

## Errors

Application errors use one safe envelope and include the same trace ID exposed in the `x-trace-id` response header:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "요청을 확인할 수 없습니다.",
    "retryable": false
  },
  "trace_id": "opaque-trace-id"
}
```

Codes and HTTP statuses are: `INVALID_REQUEST`/422, `INVALID_USER_ID`/422, `UNAUTHORIZED_RESOURCE`/404, `NO_EVIDENCE`/404, `JOB_CANCELLED`/409, `BUDGET_EXCEEDED`/429, `INDEX_UNAVAILABLE`/503, `EMBEDDING_UNAVAILABLE`/503, `DEPENDENCY_UNAVAILABLE`/503, `RETRIEVAL_TIMEOUT`/504, and unexpected `INTERNAL_ERROR`/500. Validation details, exception text, credentials, raw paths, mail content, and chain-of-thought are never included.

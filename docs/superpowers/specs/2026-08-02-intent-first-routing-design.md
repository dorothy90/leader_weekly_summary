# Intent-First RAG Routing Design

## Goal

Replace sentence allowlisting with intent-first routing. A valid structured router
decision must remain authoritative unless a small server-owned policy requires an
explicit mode or a clearly complex Deep request.

## Problem

The current post-policy converts every model-selected `general` route to `fast`
unless the exact input appears in a small allowlist. This makes ordinary dialogue
such as `내 이름은 대환`, `고마워`, or product questions run mail retrieval and
return a missing-evidence answer.

Adding more allowed sentences cannot cover natural language. It also duplicates
classification already performed by the structured router.

## Open-Source Patterns

- LlamaIndex `RouterQueryEngine` selects an engine from query plus engine metadata,
  executes the selected engine, and preserves the selector result in response
  metadata. It does not apply a second sentence allowlist.
- Haystack `ConditionalRouter` routes a typed intent such as `search` or `chat`
  directly and supports an explicit default route.
- LangGraph Adaptive RAG uses structured routing output as a conditional graph edge.
- Adaptive-RAG selects no-retrieval, single-step, or multi-step strategy from query
  complexity rather than treating every unknown input as retrieval.

References:

- https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/query_engine/router_query_engine.py
- https://github.com/deepset-ai/haystack/blob/main/haystack/components/routers/conditional_router.py
- https://github.com/langchain-ai/langgraph/blob/main/docs/docs/tutorials/rag/langgraph_adaptive_rag.ipynb
- https://github.com/AsH1605/Adaptive-RAG

## Routing Policy

Policy order is fixed:

1. An explicit `response_mode=fast|deep` is authoritative and returns
   `reason_code=explicit_mode`.
2. Server-owned Deep constraints override Auto decisions:
   - four or more requested weeks;
   - three or more teams;
   - report, presentation, trend, root-cause/action, or comprehensive-analysis
     output indicators.
3. A valid structured Auto decision is accepted:
   - `general` executes General with zero estimated searches;
   - `fast` executes Fast RAG;
   - `deep` executes Deep Research;
   - `clarify` returns clarification.
4. A router exception uses deterministic fallback:
   - Deep constraints still select Deep;
   - explicit retrieval filters or clear mail-search intent select Fast;
   - all other inputs select General.

No General sentence allowlist remains. No post-policy changes a valid General
decision merely because the text is unfamiliar.

## Deterministic Mail Fallback

The fallback mail detector exists only for router failure. It must not override a
valid General decision.

It selects Fast when either condition holds:

- the request contains at least one team or week filter; or
- the text contains a mail-domain object and a retrieval action.

Mail-domain objects include mail/email, sender/recipient, yield, weekly report, and
issue/status terms. Retrieval actions include find/search, show/tell, summarize,
compare, analyze, and recent/weekly scope terms. A single generic word is
insufficient.

This reversed default prevents unseen conversation from becoming retrieval while
still providing useful degraded behavior during router outages.

## Diagnostics

- Valid model decisions expose `model_<route>`.
- Server Deep overrides expose existing `deterministic_*` codes.
- Router failure exposes `router_error_deterministic_general|fast|...`.
- General and clarification expose `retrieval_mode=not_used` and zero searches.
- Fast and Deep remain distinct systems. No automatic workload merging is added.

## Security

Routing does not grant data access. Request-body `user_id` remains authoritative,
and Fast/Deep retrieval keeps exact owner metadata filtering and ACL enforcement.
General routing performs no retrieval. Existing BM25 fallback disclosure remains
unchanged.

## Test Design

Router tests cover categories rather than individual exceptions:

- unseen conversation and personal statements remain General;
- greetings, gratitude, capability questions, and Fast/Deep product questions
  remain General;
- clear mail retrieval remains Fast;
- multi-week/report requests remain Deep;
- explicit Fast and Deep remain authoritative;
- router failure defaults unknown conversation to General;
- router failure uses Fast for filters or paired mail-search signals;
- General never calls retrieval and reports `not_used`;
- ACL, user ownership, BM25 disclosure, and Deep job tests remain green.

Live verification uses the UI proxy with:

- `내 이름은 대환` for General;
- `지난주 수율 메일 찾아줘` for Fast;
- a four-week trend report for Deep.

## Acceptance Criteria

- No exact General sentence allowlist exists in routing code.
- A valid model-selected General route is not changed to Fast.
- Deterministic fallback defaults unfamiliar non-mail input to General.
- Explicit modes and Deep complexity overrides keep current behavior.
- UI diagnostics match the path actually executed.
- Full backend and frontend regression suites pass.

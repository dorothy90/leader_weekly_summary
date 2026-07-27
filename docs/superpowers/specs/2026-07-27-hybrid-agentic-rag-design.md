# Hybrid Agentic RAG Design

## Goal

Upgrade `rag_api_opensearch_v3.py` from a routed two-step RAG pipeline to a controlled, observable Hybrid Agentic RAG workflow while preserving the `/chat/v2` request and response contract and the existing knowledge web integration.

## Current State

The current graph is `START -> router -> retrieve|statistics|llm_answer -> llm_answer -> END`. Search always queries Wiki, weekly mail, and the technical-document index with fixed parameters. It does not grade retrieval, retry a deficient search, validate the generated answer, or protect Wiki accumulation with quality gates.

The API passes `team` and `week` into `chat_with_agent`, but the graph input omits them. `retrieve_document` also passes `team=None` to Wiki and mail search. Router failures default to `general`, which can produce an ungrounded response for a domain question.

## Compatibility Constraints

- Keep `POST /chat/v2` request fields and `ChatV2Response` fields unchanged.
- Keep `rag_api_opensearch_v3.py` as the executable API entry point.
- Preserve existing statistics endpoints, deep-mining endpoints, knowledge API router, knowledge web mount, MongoDB history, and Streamlit client behavior.
- Preserve current user changes in `rag_api_opensearch_v3.py`.
- Add only internal state and helper interfaces; do not require a new external service or model dependency.

## File Boundaries

- `rag_api_opensearch_v3.py`: OpenSearch adapters, LLM calls, LangGraph nodes and edges, API integration, logging, history, and response conversion.
- `hybrid_rag.py`: JSON-serializable TypedDict/Pydantic schemas and pure functions for fallback routing, planning fallback, normalization, deduplication, deterministic reranking, context construction, citation checks, and bounded-loop decisions.
- `tests/test_hybrid_rag.py`: Pure unit tests without network services.
- `tests/test_rag_api_agentic.py`: Integration tests using fake LLM and fake OpenSearch clients.

## State Design

`GraphState` becomes `TypedDict(total=False)` and retains all existing fields. It adds:

- Request constraints: `team`, `week`, `start_week`, `end_week`
- Planning: `rewritten_query`, `sub_questions`, `selected_sources`, `retrieval_plan`
- Evidence: `retrieval_results`, `reranked_results`, `context`
- Retrieval control: `retrieval_grade`, `retrieval_score`, `missing_information`, `search_attempts`, `rewrite_count`, `executed_search_keys`, `retrieval_error`
- Answer control: `draft_answer`, `final_answer`, `groundedness_score`, `completeness_score`, `citation_valid`, `answer_revision_count`, `answer_evaluation`
- Observability: `trace`

All new state values are JSON-serializable dictionaries, lists, strings, numbers, booleans, or nulls. Existing `messages` remains a LangGraph message list for compatibility with conversation history.

## Target Graph

```text
START
  -> router
     -> general: llm_answer -> finalize -> END
     -> statistics: statistics -> llm_answer -> evaluate_answer
     -> search: plan_retrieval -> execute_searches -> rerank -> grade_retrieval
          -> sufficient: generate_answer -> evaluate_answer
          -> retry available: rewrite_query -> execute_searches
          -> retry exhausted: generate_limited_answer -> evaluate_answer
  evaluate_answer
     -> pass: finalize
     -> revision available and evidence sufficient: revise_answer -> finalize
     -> evidence insufficient and search retry available: rewrite_query
     -> otherwise: finalize
  finalize -> END
```

Search attempts are capped at `MAX_SEARCH_ATTEMPTS=2`, query rewrites at `MAX_QUERY_REWRITES=2`, and answer revisions at `MAX_ANSWER_REVISIONS=1`. Every conditional route has an explicit terminal path.

## Phase 1: Correctness Foundation

- Put API `team` and normalized `week` into initial graph state.
- Preserve explicit API filters when router output is merged.
- Pass team/week to Wiki and mail OpenSearch calls.
- Technical documents currently have no team/week metadata. Record `filter_applicable=false` rather than pretending a filter was enforced.
- Use deterministic fallback routing: clear greetings/help are `general`, clear count/submission questions are `statistics`, everything else is `search`.
- On retrieval failure or empty results, return a grounded limitation message rather than treating the question as general conversation.
- Emit structured JSON logs containing event, route, query, filters, result counts, elapsed time, and fallback reason without document bodies.

## Phase 2: Evidence Pipeline

Normalize every source to `RetrievedDocument` with stable `document_id`, `source_type`, `title`, `content`, team/week, source score, rerank score, and metadata. Deduplicate first by document ID and then by normalized-content fingerprint. Limit repeated chunks from the same mail.

Initial reranking is deterministic and dependency-free: normalized source score plus lexical query overlap and exact metadata boosts for team/week. The interface is isolated so a cross-encoder can replace it later. Select at most `FINAL_DOCUMENT_LIMIT=8` documents and enforce `MAX_FINAL_CONTEXT_TOKENS` while building context. Stable citations use `[S1]`, `[S2]`, and so on.

## Phase 3: Closed Retrieval Loop

Grade whether evidence is relevant and sufficient. Prefer structured LLM output, with a deterministic fallback that checks result existence, team/week coverage, comparison-target coverage, and numeric evidence for numeric questions. On insufficient evidence, rewrite specifically for `missing_information`, merge new evidence with prior evidence, deduplicate, rerank, and stop when attempts or rewrites are exhausted. Identical query/filters/source combinations are skipped through `executed_search_keys`.

## Phase 4: Planning and Parallel Search

`RetrievalPlan` chooses intent, sources, sub-questions, filters, search strategy, and weights. A Pydantic-validated LLM JSON response is preferred; a rule fallback handles statistics, team comparisons, recent-week trends, and technical terms. Simple questions create one task. Comparisons and trends create bounded tasks, capped by `MAX_SUB_QUESTIONS`.

Independent search tasks execute concurrently through a bounded thread pool. Each result preserves sub-question, source, team, week, and search key. Individual task failures are collected and do not fail the entire request.

## Phase 5: Answer Validation and Wiki Policy

Generate answers from reranked evidence only. Every evidence item receives a stable citation. Evaluate groundedness, completeness, citation validity, unsupported claims, missing answers, and request-condition coverage. A failed answer may be revised once. If evidence is insufficient, retain only supported content and state the limitation.

Set `AUTO_WIKI_SAVE_ENABLED=False`. Even when enabled, accumulation is only eligible after answer evaluation passes, citations are valid, groundedness exceeds the configured threshold, enough sources exist, and the answer is not explicitly uncertain. Saving remains a separate function so review-queue behavior can replace direct persistence.

## Observability

Each node appends compact trace events and emits the same event as one-line JSON. Events include filters, plan, selected sources, sub-questions, per-source counts, dedup count, rank changes, retrieval grade, rewrite, attempt counts, selected document IDs, answer scores, citation validity, elapsed time, and fallback errors. Document bodies are excluded.

## Testing Strategy

- Pure tests cover fallback routing, normalization, fingerprints, deduplication, reranking limits, citation validity, loop limits, identical-query prevention, and Wiki default policy.
- Adapter tests inspect generated OpenSearch bool filters.
- Node tests fake LLM/OpenSearch dependencies to verify filter propagation, source selection, retries, limited answers, and answer revision limits.
- Scenario tests cover greeting, single-team recent issues, multi-team four-week comparison, statistics, missing equipment, router failure, and unsupported numeric claims.
- Each phase runs its targeted tests followed by `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests`.

## Known Constraint

The current `syldgpt` technical-document mapping contains no team or week fields. Phase 1 cannot enforce metadata filters on that source without an index migration. The planner therefore uses that source for technical background and records that request filters are not applicable. Mail and Wiki filters remain strict.

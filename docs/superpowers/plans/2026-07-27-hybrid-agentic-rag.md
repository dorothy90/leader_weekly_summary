# Hybrid Agentic RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the current routed RAG API into a bounded Hybrid Agentic RAG workflow with correct filters, adaptive retrieval, evidence grading, answer validation, and safe Wiki accumulation.

**Architecture:** Preserve `/chat/v2` and keep service adapters and graph wiring in `rag_api_opensearch_v3.py`. Put JSON-serializable schemas and deterministic evidence-processing logic in `hybrid_rag.py`, then compose them as bounded LangGraph nodes and conditional loops.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, LangChain, LangGraph, OpenSearch, pytest

## Global Constraints

- Preserve the current `/chat/v2` request and response schema.
- Preserve current knowledge API/web changes and unrelated working-tree changes.
- Maximum search attempts: 2.
- Maximum query rewrites: 2.
- Maximum answer revisions: 1.
- Do not log full document bodies.
- Do not add a model or library dependency for the initial reranker.
- Technical-document filters are reported as not applicable until the index contains team/week metadata.

---

### Task 1: Phase 1 filter and fallback foundation

**Files:**
- Modify: `rag_api_opensearch_v3.py`
- Create: `hybrid_rag.py`
- Create: `tests/test_hybrid_rag.py`
- Create: `tests/test_rag_api_agentic.py`

**Interfaces:**
- Produces: `fallback_route(question: str) -> Literal["general", "statistics", "search"]`
- Produces: `normalize_weeks(value: str | list[str] | None) -> list[str] | None`
- Produces: `append_trace(trace, event, **data) -> list[dict]`
- Extends: `GraphState` with request filters and JSON trace fields.

- [ ] Write failing pure tests for deterministic fallback routing and week normalization.
- [ ] Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_hybrid_rag.py` and confirm failures are missing interfaces.
- [ ] Implement the minimal helpers in `hybrid_rag.py` and rerun the tests.
- [ ] Write failing node tests proving API team/week reach mail and Wiki searches and explicit filters survive router output.
- [ ] Run the targeted tests and confirm failures show `team=None` or missing initial state.
- [ ] Extend `GraphState`, graph input, router merge policy, and retrieval calls; add compact structured logging.
- [ ] Add failing tests for LLM exception/malformed output routing and total retrieval failure.
- [ ] Implement safe fallback and grounded retrieval-failure response.
- [ ] Run targeted tests, compile the modules, then run the complete `tests/` suite.

### Task 2: Phase 2 common evidence and reranking

**Files:**
- Modify: `hybrid_rag.py`
- Modify: `rag_api_opensearch_v3.py`
- Modify: `tests/test_hybrid_rag.py`
- Modify: `tests/test_rag_api_agentic.py`

**Interfaces:**
- Produces: `RetrievedDocument`
- Produces: `normalize_result(source_type, raw, task) -> RetrievedDocument`
- Produces: `deduplicate_documents(documents) -> tuple[list[RetrievedDocument], int]`
- Produces: `rerank_documents(question, documents, filters, limit) -> list[RetrievedDocument]`
- Produces: `build_context(documents, token_limit) -> tuple[str, list[RetrievedDocument]]`

- [ ] Write failing tests for stable IDs, content fingerprint deduplication, repeated-mail chunk limits, metadata boosts, final-document limits, and context limits.
- [ ] Run targeted tests and confirm the missing evidence interfaces fail.
- [ ] Implement normalization and deduplication with SHA-256 content fingerprints.
- [ ] Implement deterministic score normalization, lexical overlap, team/week boosts, stable ranking, and final limit.
- [ ] Implement `[S#]` context construction with a token budget.
- [ ] Change retrieval state to retain normalized/reranked evidence and generate context only from final evidence.
- [ ] Verify targeted and full tests.

### Task 3: Phase 3 retrieval grading and bounded retry

**Files:**
- Modify: `hybrid_rag.py`
- Modify: `rag_api_opensearch_v3.py`
- Modify: `tests/test_hybrid_rag.py`
- Modify: `tests/test_rag_api_agentic.py`

**Interfaces:**
- Produces: `RetrievalGrade`
- Produces: `fallback_grade_retrieval(question, documents, filters, sub_questions) -> RetrievalGrade`
- Produces: `make_search_key(query, source, filters) -> str`
- Adds nodes: `grade_retrieval`, `rewrite_query`
- Adds conditional router: `route_after_retrieval`

- [ ] Write failing tests for relevant/sufficient grading, missing comparison sides, numeric evidence, repeated search keys, and maximum attempts.
- [ ] Implement deterministic grading and search-key deduplication.
- [ ] Add structured LLM grading with Pydantic validation and deterministic fallback.
- [ ] Add rewrite logic driven by `missing_information`, rejecting identical rewrites.
- [ ] Wire `execute_searches -> rerank -> grade_retrieval`, bounded retry, and limited-answer branch.
- [ ] Test merge-on-retry behavior and prove the graph terminates at the configured limits.
- [ ] Verify targeted and full tests.

### Task 4: Phase 4 retrieval planning, decomposition, and parallel execution

**Files:**
- Modify: `hybrid_rag.py`
- Modify: `rag_api_opensearch_v3.py`
- Modify: `tests/test_hybrid_rag.py`
- Modify: `tests/test_rag_api_agentic.py`

**Interfaces:**
- Produces: `RetrievalPlan`
- Produces: `SearchTask`
- Produces: `fallback_retrieval_plan(question, filters) -> RetrievalPlan`
- Adds nodes: `plan_retrieval`, `execute_searches`

- [ ] Write failing planning tests for statistics, single-team issue, technical concept, team comparison, and recent-week trend.
- [ ] Implement Pydantic plan validation and bounded rule fallback with dynamic weights.
- [ ] Write failing tests for bounded sub-questions, unique search keys, partial task failures, and preserved task metadata.
- [ ] Implement search-task generation and bounded concurrent execution.
- [ ] Call only selected sources and skip decomposition for simple questions.
- [ ] Verify the four primary scenarios and full tests.

### Task 5: Phase 5 citation-aware answers and safe Wiki policy

**Files:**
- Modify: `hybrid_rag.py`
- Modify: `rag_api_opensearch_v3.py`
- Modify: `tests/test_hybrid_rag.py`
- Modify: `tests/test_rag_api_agentic.py`

**Interfaces:**
- Produces: `AnswerEvaluation`
- Produces: `validate_citations(answer, documents) -> tuple[bool, list[str]]`
- Produces: `wiki_save_eligible(state) -> bool`
- Adds nodes: `evaluate_answer`, `revise_answer`, `finalize`

- [ ] Write failing tests for valid/invalid citations, unsupported citation IDs, completeness, revision cap, and Wiki default disabled.
- [ ] Change answer prompts to use stable `[S#]` citations and final evidence only.
- [ ] Implement structured answer evaluation with deterministic citation validation fallback.
- [ ] Wire pass, one-revision, evidence-retry, and terminal limited-response routes.
- [ ] Set `AUTO_WIKI_SAVE_ENABLED=False` and require the full quality gate when enabled.
- [ ] Update reference extraction to map `[S#]` citations to mail documents while preserving API response fields.
- [ ] Verify scenario tests and full tests.

### Task 6: Completion audit and documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-07-27-hybrid-agentic-rag-design.md` only if implementation evidence requires a correction.

**Interfaces:**
- Verifies every explicit requirement in the attached specification against code and test evidence.

- [ ] Run `python -m compileall -q hybrid_rag.py rag_api_opensearch_v3.py streamlit_chat_v2.py`.
- [ ] Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_hybrid_rag.py tests/test_rag_api_agentic.py`.
- [ ] Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests`.
- [ ] Inspect the final graph nodes and edges and confirm every loop has a bounded exit.
- [ ] Inspect the diff to confirm unrelated user changes remain untouched.
- [ ] Report the old/new graph, files, state, nodes, edges, filter flow, reranking, grading, validation, Wiki policy, settings, tests, and remaining limitations.

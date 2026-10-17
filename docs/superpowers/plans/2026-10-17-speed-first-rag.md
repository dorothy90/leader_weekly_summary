# Speed-first four-source RAG Implementation Plan

> For agentic workers: use subagent-driven-development with TDD and independent review.

Goal: Replace the active /v1/chat iterative agent with one structured interpretation and at most one answer-generation call, retaining API compatibility.
Architecture: QuickRAGWorkflow consumes a typed plan, resolves metadata with server-owned rules, parallel queries four configured source adapters, and returns FastRAGResult. Persistent typed QuickMemory retains applied constraints and result IDs for follow-ups. No statistics, deep route, judge, replanning or revision loop.

## Global constraints
- Existing EWS linked worktree /Users/daehwankim/orca/workspaces/weekly_mail_agent/ews; preserve unrelated modifications.
- Four logical sources weekly_mail, ews_mail, ews_calendar, wiki_summary. Aliases configurable; actual wiki is wiki_summaries.
- EWS employee_id mandatory; weekly/wiki user_id mandatory unless explicit shared-source configuration authorized by user.
- Ambiguous subject questions search all four; explicit source constraints narrow by server allowlist. Never drop unsupported person/date filters silently.
- Interpretation receives bounded recent messages, typed confirmed state, current clock/timezone and source capabilities. No user/LLM arbitrary DSL/index or owner overrides.
- Recent lists: latest 10 without date cutoff; recent summaries: rolling seven days. Explicit dates override. Calendar overlap bounds. NEW is a term, not a date.
- Identity resolver uses actual owner-scoped source values, rejects ambiguous names; no guessed emails. Respect roles.
- Result-set follow-ups re-fetch prior IDs with current authorization and constraints; new topic resets state. Old assistant text is not evidence.
- List metadata queries do not call embeddings or generator. Text queries use BM25/vector RRF with bounded candidates, independent source timeouts and disclosed failures/fallbacks. No unsupported claims of semantic validation.
- Inline API uses at most interpretation + generation, short output; no streamed response requirement.

## Tasks
1. Core schema, prompt, date/source/identity/filter adapters, workflow + focused tests (implementer). Write failing tests, run RED then implement GREEN. Files app/domain/quick_rag.py, app/llm/quick_rag.py, app/retrieval/quick_rag.py, app/graphs/quick_rag.py; tests/mail_rag/test_quick_rag.py.
2. Integrate config/container/persistence/API with QuickRAGWorkflow; preserve response contract, persist QuickMemory, remove active research route, ensure only interpret/answer gateways built. Add API/wiring regression tests. Update dummy seed sender_name. Controller handles these files.
3. Run focused and full backend tests plus real read-only OpenSearch smoke (no paid model calls without need). Independently review complete diff; fix findings. Record measured call counts and disclose performance not benchmarked. Deliver plan, change summary and patch in outputs; no production deploy.

## Confirmed during implementation
User explicitly approved shared weekly_mail/wiki_summaries access; quick_shared_sources defaults true. EWS retains employee_id ACL.

## Completion ledger
- Task1 core: implemented and reviewed; strict plan, metadata retrieval, bounded workflow and25 focused regression tests.
- Task2 integration: implemented; API10 references, owned memory, bounded storage I/O, quick container, research route removal,9 integration tests.
- Task3: all five independent review findings fixed and re-reviewed; real four-source read smoke succeeds after user-approved weekly_mail index reopen (green,360 documents). User-approved one-call dummy live model test returned10 cited mails:13.523s total,13.500s interpretation,19ms search.
- Remaining known limits: vector sources default disabled until embedding provenance verified; upcoming preference covers bounded candidates; current openrouter/free model interpretation dominates latency.
- No service deployment, commit, push or merge. Existing unrelated files preserved.

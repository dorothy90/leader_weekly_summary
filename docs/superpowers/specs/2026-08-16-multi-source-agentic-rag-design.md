# Multi-Source Agentic RAG Design

## Purpose

Extend the existing mail-oriented Fast RAG path into a bounded multi-source
agent that can search domain knowledge, Outlook mail, and Outlook calendar
data in sequence. The implementation must preserve the current `/v1/chat`
request and response contract as far as possible and provide an entirely
local dummy mode that is usable from the existing frontend, the API, and a
CLI without OpenSearch, MongoDB, an embedding service, or an LLM credential.

The source requirements are defined in
`MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md`. This design resolves its open
integration choices against the current repository.

## Current System and Gaps

The current application already provides useful foundations:

- `app/api/routes/chat.py` owns the stable `/v1/chat` orchestration contract.
- `FastRAGWorkflow` implements bounded planning, concurrent retrieval,
  grading, query rewriting, answer generation, and citation validation.
- `RetrievalService` owns hybrid BM25/vector retrieval, RRF, mail context
  expansion, owner filtering, and BM25 fallback disclosure.
- `PolicyContext`, conversation stores, and citation validation fail closed on
  the current `user_id` owner.
- the frontend already renders route, execution, quality, and reference data.

The current implementation is still mail-centric. `SearchTask` supports only
`mail`, `wiki`, and `statistics`; plans are generated as a fixed list; there
is no calendar tool or `parent_event_id` expansion; aliases from the source
plan are not modeled; and conversation memory does not retain structured
entities, a previous event reference, search history, or unresolved needs.

The relevant baseline is green: the domain, retrieval, Fast RAG, and
conversation test subset contains 72 passing tests before this change.

## Selected Approach

Keep `FastRAGWorkflow` as the compatibility facade and general-conversation
handler. Delegate retrieval questions to a focused
`MultiSourceAgenticWorkflow` that returns the existing `FastRAGResult`
contract. This keeps `/v1/chat` and the frontend stable while avoiding further
growth of the existing 800-line Fast workflow.

The multi-source workflow reuses the current policy, OpenSearch gateway,
embedding gateway, RRF implementation, evidence safety checks, tracing, and
conversation stores. New source-specific search tools sit behind a common
protocol, so the production OpenSearch implementation and deterministic
in-memory implementation exercise the same graph.

Rejected alternatives:

- A separate router branch with an unrelated multi-source response path would
  protect existing code but duplicate retrieval, memory, and citation logic.
- A standalone demo application would be quick to show but would not prove
  compatibility with the operational API and frontend.

## Architecture

```text
POST /v1/chat
    |
    +-- existing route/general/deep handling
    |
    `-- FastRAGWorkflow compatibility facade
            |
            `-- MultiSourceAgenticWorkflow
                    query_analyzer
                          |
                    source_discovery
                          |
                       planner
                          |
                    tool_executor <---------+
                          |                  |
                    observation             |
                          |                  |
                        judge               |
                          |                  |
              +-----------+-----------+     |
              |                       |     |
          sufficient             insufficient
              |                       |
         final_answer              replanner-+
              |
          save_memory
```

The loop has a hard `MAX_ITERATIONS = 4`. A normalized fingerprint of tool,
query, and filters prevents repeated searches. Every graph output is bounded
and Pydantic-validated.

## Components and Boundaries

### Domain contracts

`app/domain/agentic.py` owns the types shared by the graph and tool layer:

- `QueryAnalysis`: question type, intent, entities, natural-language time,
  resolved UTC range, and information needs.
- `SourceName`: `domain_knowledge`, `mail`, or `calendar`.
- `ToolName`: the three semantic search tools plus
  `expand_calendar_event`.
- `ToolAction`: validated tool, query, reason, optional safe filters, and
  optional event ID. It never accepts an employee ID, index name, or raw DSL.
- `SearchDocument`: normalized, bounded text and provenance metadata.
- `SearchResult`: tool, query, normalized documents, total hit count,
  retrieval mode, and safe error/disclosure data.
- `Observation`, `JudgeDecision`, and `AgenticRAGResult`.

Existing public `Evidence` and `ChatReference` source type enums are extended
to include `domain_knowledge` and `calendar`. Calendar metadata exposed to the
client is limited to opaque IDs, subject/title, timestamps, content kind, and
bounded excerpts.

### Static source registry

`app/retrieval/source_registry.py` defines exactly three sources:

| Source | Tool | Configured target |
| --- | --- | --- |
| Domain knowledge | `search_domain_knowledge` | `syld_gpt` |
| Mail | `search_mail` | `ews-mail-active` |
| Calendar | `search_calendar` | `ews-calendar-active` |

The mail and calendar targets are aliases. Physical names such as
`ews-mail-v1` and `ews-calendar-v1` are forbidden outside dummy fixture
descriptions and tests that verify their absence from application code.

The settings are environment-configurable as `DOMAIN_KNOWLEDGE_INDEX`,
`MAIL_INDEX_ALIAS`, and `CALENDAR_INDEX_ALIAS`, with the exact defaults in the
table. The registry is static in this release; a searchable catalog and MCP
tool server are explicit non-goals.

### Search tool layer

`app/retrieval/multi_source.py` defines one protocol used by both backends:

```python
class MultiSourceSearch(Protocol):
    async def execute(
        self,
        action: ToolAction,
        policy: PolicyContext,
        analysis: QueryAnalysis,
    ) -> SearchResult: ...
```

`OpenSearchMultiSourceSearch` builds deterministic query bodies, delegates
network calls to `OpenSearchGateway`, uses the current embedding gateway and
RRF helper, normalizes results, and never exposes raw hits to the graph.

`InMemoryMultiSourceSearch` uses the same actions, policy, normalization, and
event-expansion rules against JSON fixtures. It performs deterministic token
matching and scoring so the demonstration requires no model service.

### Date resolver

`app/retrieval/dates.py` converts `어제`, `지난주`, `이번주`, `지난달`, and
explicit ISO dates into half-open UTC ranges. The default user timezone is
`Asia/Seoul`, configurable as `DEFAULT_USER_TIMEZONE`. The analyzer records
the original expression but cannot invent final timestamps. Unknown
expressions remain unresolved and do not create a guessed range filter.

### Agent model boundary

The production workflow accepts the current LLM gateway behind a focused
agent-model adapter for analysis, planning, judging, query rewriting, and
grounded answer generation. Structured calls are parsed into the domain
contracts. One invalid structured response is retried once; a second failure
uses deterministic rules.

`RuleBasedAgentModel` provides all decisions and final answer composition in
dummy mode. It is deterministic and shares the same contracts with the
production adapter.

## Security Invariants

The existing request-body `user_id` is treated as the authenticated employee
identifier supplied by the trusted upstream gateway. It is converted to
`PolicyContext` before the graph runs. For compatibility the HTTP field is not
renamed, but internally search filters use the source schema's `employee_id`.

The following filters are injected in code and cannot appear in a model
action:

- Mail: `employee_id == policy.user_id` and `is_active == true`.
- Calendar: `employee_id == policy.user_id`, `is_active == true`, and
  `is_cancelled == false`.
- Calendar expansion repeats all three filters for the parent event and every
  attachment lookup.

Domain knowledge is shared and has no employee filter in this release.

The executor accepts only the four declared tools. It maps tools to registry
targets and rejects any action carrying an index name, raw DSL, or employee
identifier. Post-query normalization checks owner and lifecycle fields again,
so a malformed or malicious backend response cannot enter model context,
memory, logs, or citations.

## Search Behavior

### Domain knowledge

Search `text` with BM25 and `embedding` with vector search, fuse rankings with
the existing RRF implementation, and return bounded domain evidence. Optional
reranking remains behind the existing model boundary and is not required for
dummy mode.

### Mail

Search only `ews-mail-active`. Apply mandatory owner and active filters plus
resolved date, content-kind, and attachment-name filters. BM25 and vector
rankings are fused. Hits are grouped by `source_id`, deduplicated, sorted by
`chunk_index`, and reconstructed into one bounded source context. Body and
attachment documents remain distinguishable through `content_kind`.

The initial implementation uses available metadata and `text`. Sender,
recipient, subject, and thread fields are used only when present; changing the
mail mapping is not required for the first release.

### Calendar

Search only `ews-calendar-active`. Apply mandatory owner, active, and
not-cancelled filters plus resolved date, content-kind, organizer, and attendee
filters. BM25 searches `subject`, `location`, `text`, `organizer_email`, and
`attendee_emails`; vector search uses `embedding`.

When an attachment matches, `parent_event_id` deterministically loads the
parent event and its sibling attachments. When an event matches and the user
needs meeting content, decisions, actions, or attachments, its bundle is also
expanded. Series and occurrence identifiers remain in metadata so recurring
meetings are not combined accidentally.

## Agent Flow

1. `query_analyzer` combines the current question with safe structured memory,
   classifies the question, extracts entities and time expression, and invokes
   the date resolver.
2. `source_discovery` selects candidate registry sources. It uses deterministic
   keywords as a lower bound so an LLM cannot omit an explicitly requested
   source.
3. `planner` chooses only the first next action. For cross-source questions the
   default order is Mail, Calendar, Domain because earlier observations provide
   entities for later searches.
4. `tool_executor` validates the allowlist, blocks duplicate fingerprints,
   injects policy filters, executes one action, and automatically expands
   calendar bundles when required.
5. `observation` stores a compact summary and normalized documents without raw
   backend responses.
6. `judge` compares accumulated evidence with every information need. Code
   enforces source-coverage and loop constraints around the model decision.
7. `replanner` uses missing needs and observed entities to choose a different
   source or rewritten query. It cannot schedule a duplicate.
8. `final_answer` creates cited claims from same-owner normalized evidence.
   Missing information is stated explicitly.
9. `save_memory` returns bounded structured memory updates to the existing
   conversation persistence path.

At iteration four the workflow stops even if the judge asks to continue. It
answers only from accumulated evidence and marks unresolved information.

## Conversation Memory

`ConversationMemory` gains bounded fields for:

- normalized entities (`product`, `issue`, `person`, and meeting labels);
- `current_topic`;
- safe search-history fingerprints and tool names, without raw result bodies;
- `previous_event_reference` containing only the same-owner opaque event ID,
  subject, and timestamps;
- `retrieved_source_refs`;
- `unresolved_information`.

Existing owner checks, revision control, redaction, and bounded recent messages
remain in force. A follow-up such as `그 회의에서 Action 뭐였어?` first tries
the previous event reference through `expand_calendar_event`. If it is absent,
invalid, or no longer accessible, normal calendar discovery runs instead.

## API and Frontend Compatibility

The HTTP request remains `ChatRequest`; no required field is added. The
response remains `ChatResponse` with `mode="fast_rag"`. Existing mail, Wiki,
and statistic references keep their shape. Domain and calendar references use
the same public fields with the extended source type.

Execution metadata remains bounded. New agent information is represented by
node-run names and safe counts rather than chain-of-thought. The frontend adds
labels for domain and calendar references but does not require a new page or
route.

## Dummy Mode and Data

`fixtures/multi_source_demo/` contains a small multi-owner Korean corpus:

- domain knowledge explaining NAND Cell Leakage and its yield meaning;
- an active mail body reporting the issue and mentioning a planned review;
- an active mail attachment with supporting measurements;
- a calendar event for the NAND Yield Review;
- a meeting attachment with the agreed equipment/FDC action;
- a second user's highly similar mail and calendar documents;
- an inactive mail and a cancelled calendar event that would otherwise score
  highly.

`MULTI_SOURCE_DEMO=true` makes dependency construction use the in-memory
search, rule-based agent model, and in-memory conversation/job stores. The
existing API and frontend therefore work without external services. The demo
container advertises ready dependencies rather than attempting OpenSearch or
MongoDB health checks.

`scripts/run_multi_source_demo.py` runs the canonical cross-source question,
prints the final answer, citations, ordered tool calls, judge decisions, and
iteration count, and supports a custom question and follow-up conversation.

## Failure Behavior

- Invalid structured model output is retried once and then falls back to
  deterministic analysis, planning, judging, or rewriting.
- Embedding failure continues with BM25 and preserves the existing exact
  fallback disclosure.
- An unavailable source records a safe typed error. If other evidence covers
  part of the question, the workflow produces a limited answer and names the
  uncovered need.
- Failure of every required source produces a typed limited or failed result;
  it never triggers an ungrounded general answer.
- Empty results trigger a different query or source when an unused action
  exists.
- A duplicate action is skipped before the backend call. If no alternative
  remains, the workflow terminates with a limited answer.
- Calendar expansion with a missing or inaccessible parent omits the bundle
  and records the gap without leaking its existence across owners.
- MAX_ITERATIONS always wins over a model request to continue.

## Observability

Each request records safe graph events for query analysis, selected sources,
tool name, hashed or bounded query information, enforced filter categories,
hit count, top scores, iteration, judge sufficiency, missing needs, latency,
final source IDs, fallback mode, and error class. Raw mail/calendar bodies,
employee IDs, credentials, and hidden reasoning are never logged.

The CLI may display full dummy content because it is repository-owned fixture
data; production traces still use the safe representation.

## Test Strategy

Implementation is test-driven. Focused unit and integration tests cover:

- strict agent contracts, tool allowlist, registry aliases, and absence of
  physical index names in application code;
- deterministic Korean date range resolution and UTC conversion;
- mandatory Mail and Calendar filters in BM25, vector, expansion, and fallback
  query bodies;
- mail body/attachment grouping, deduplication, and chunk ordering;
- calendar event search, attachment search, parent expansion, sibling
  expansion, recurring-event identity, cancellation, and inactivity filters;
- domain-only, mail-only, calendar-only, Mail-to-Calendar, and
  Mail-to-Calendar-to-Domain flows;
- insufficient-evidence replanning, invalid structured output fallback,
  duplicate blocking, and the four-iteration limit;
- same-owner follow-up event resolution and foreign-owner reference rejection;
- citation validation and grounded limited answers;
- the current `/v1/chat` contract in demo mode and frontend rendering of new
  source types;
- the dummy CLI canonical scenario.

The dummy corpus deliberately includes cross-owner, inactive, and cancelled
high-scoring decoys. Any security filter regression therefore fails observable
scenario assertions rather than relying only on query-body inspection.

After focused tests pass, the complete Python and frontend suites run. The
three user-facing verification paths are:

```bash
python -m pytest
MULTI_SOURCE_DEMO=true uvicorn app.api.main:app
python scripts/run_multi_source_demo.py
```

## Acceptance Criteria

The implementation is complete only when all of the following are evidenced:

- Application search code uses `syld_gpt`, `ews-mail-active`, and
  `ews-calendar-active` from settings/registry and never hardcodes the mail or
  calendar physical index.
- Mail and Calendar searches inject immutable backend ACL and lifecycle
  filters for every query and expansion.
- Mail body and attachment documents are searchable and reconstructed safely.
- Calendar events and attachments are searchable in both directions through
  `parent_event_id`.
- One question can call several source tools sequentially, and observations
  influence the next action.
- Insufficient evidence triggers a different source or query when possible.
- Duplicate actions and loops beyond four iterations are impossible.
- Follow-ups can reuse a same-owner previous event/topic/entity without
  trusting stale or foreign evidence.
- Every factual claim is grounded in an existing allowed evidence item;
  missing facts remain explicitly unresolved.
- Existing API and frontend flows remain compatible.
- The canonical dummy question automatically executes Mail, Calendar, event
  expansion, and Domain search and returns cited output without external
  services.
- Unit, integration, security, API, CLI, frontend, and full regression tests
  pass.

## Non-Goals

- Introducing multiple agents, MCP, or a dynamic OpenSearch source catalog.
- Requiring a production mail index mapping migration for sender, recipient,
  subject, or thread metadata.
- Replacing the existing Deep Research workflow.
- Implementing authentication inside this service; the trusted upstream
  identity contract remains unchanged.

# Typed LLM Source Routing Design

**Date:** 2026-08-17
**Status:** Superseded by the all-LLM semantic pipeline
**Scope:** Multi-source natural-language routing, planning, sufficiency judging,
answer generation, demo dependency wiring, and tests. Retrieval backends and the
August fixture corpus remain unchanged.

## Problem

The current agent does not merely use rules as a demo stub. It inspects raw Korean
and English substrings to classify Mail, Calendar, domain, detail, follow-up, and
time intent. `StructuredAgentModel` first runs that rule analyzer and merges its
result into the LLM response, while the graph invokes rule-derived source discovery,
planning, judging, fallback answering, and memory updates.

This makes wording part of application control flow. For example, `뭐야` adds domain
knowledge even when the question only asks for a schedule. Adding more variants to
the keyword lists would move the failure rather than remove the coupling.

## Goal

Make the structured model the sole interpreter of arbitrary natural language. All
downstream application logic must consume validated typed fields instead of scanning
the raw question or human-readable `information_needs`. If model analysis is
unavailable or invalid, the system must make no semantic search decision and return
a safe limited response.

## Chosen Approach

Use one structured model adapter for every semantic stage. The LLM converts natural
language to a validated intent, selects one allowlisted tool at a time, judges the
retrieved evidence, and generates the cited answer. Server code validates those
outputs, resolves dates, injects access controls, and enforces action bounds without
making semantic decisions.

This replaces the production use of `RuleBasedAgentModel`; it does not replace
deterministic security, date arithmetic, event expansion, citation validation, or
evidence filtering.

## Typed Analysis Contract

Add an LLM-facing `IntentDecision` with bounded fields:

- `intent`: the semantic intent label for diagnostics;
- `source_requests`: at most three unique `SourceRequest` objects;
- `entities`: the existing bounded entity map;
- `time_scope`: one of `none`, `yesterday`, `previous_week`, `current_week`,
  `previous_month`, or `exact_date`;
- `exact_date`: an ISO date required only for `exact_date`;
- `event_reference`: `none` or `previous_event`;
- `calendar_detail_required`: a boolean;
- `information_needs`: bounded display and trace text only.

Each `SourceRequest` contains:

- `source`: `mail`, `calendar`, or `domain_knowledge`;
- `query`: the model-produced semantic search query, bounded to the existing query
  limit.

Pydantic validation rejects duplicate sources, empty queries, mismatched exact-date
fields, Calendar-only flags without a Calendar request, and a previous-event
reference without Calendar intent. No model field accepts an employee ID, physical
index name, OpenSearch DSL, owner filter, or arbitrary tool name.

`QueryAnalysis` stores the validated source requests and server-resolved UTC range.
It also stores `analysis_status` as `ready` or `unavailable`. The server derives the
existing `question_type` from typed source count and `event_reference`: no sources is
general chat, one source maps to its bounded search type, multiple sources is
multi-source, and `previous_event` is follow-up.
`information_needs` remains available for user-facing missing-information text but
must never select a source or tool.

## Data Flow

1. `StructuredAgentModel.analyze` sends the current question plus bounded safe
   conversation entities and topic to the LLM as `IntentDecision` input.
2. The validated decision is converted to `QueryAnalysis`. Server code maps the
   typed `time_scope` to the existing half-open UTC range using the configured user
   timezone. It never searches the original question for relative-date text.
3. Source discovery exposes the validated logical sources without choosing tools.
4. The planner LLM receives the question, analysis, bounded observations, and safe
   memory, then returns one validated `ToolAction` or `null`.
5. The executor rejects non-allowlisted, duplicate, over-budget, or unauthorized
   actions and injects physical aliases and owner filters itself.
6. The judge LLM receives normalized evidence and returns sufficiency, missing
   information, and at most one validated recommended action.
7. A recommended action loops through the executor and judge within the fixed
   iteration bound.
8. The answer LLM receives only normalized authorized evidence and emits cited
   prose; citation IDs are validated by the server.

## Model Failure and Safe Fallback

Remove keyword analysis as the fallback for structured failures. After the existing
bounded retry count, return an analysis-unavailable result with no source requests.
The graph performs zero searches, marks execution limited, and returns a sanitized
message explaining that the question could not be structured safely.

Planning or judging output that is invalid, repeats an action, or exceeds the action
bound is rejected by fail-closed server validation. It may reduce the answer to a
limited result but may not broaden retrieval.

Date arithmetic, owner filters, alias selection, cancellation checks, stable-event
authorization, metadata limits, evidence normalization, and citation validation
remain deterministic fail-closed controls.

## Production and Demo Wiring

Production uses the configured LLM gateway for four semantic stages: routing,
planning, judging, and answer generation. No keyword baseline or typed semantic
policy is created or merged. Replanning repeats the judge stage only when the model
requests another validated tool action.

Arbitrary natural-language demo questions require a configured LLM. This is an
intentional change from the secret-free free-form demo: semantic understanding
cannot be both model-free and free of language rules.

Offline automated tests inject a fake model that returns explicit typed decisions;
the fake does not inspect question substrings. If an offline human smoke path is
retained, it must be an explicitly named scenario that supplies a pre-authored typed
decision, not a parser that maps keywords or exact phrases to decisions.

`build_demo_container` accepts an injected agent model and router for tests. Its
interactive default builds the same structured model path as production while
keeping in-memory conversations, fixture retrieval, and no OpenSearch or MongoDB.
Readiness reports the configured model dependency honestly.

## Security

The LLM controls only logical source requests and bounded semantic query text. The
source registry still selects physical aliases, and policy context still injects
the authenticated owner. Model output cannot weaken owner, active, cancelled,
content-kind, date, or request-filter constraints.

Saved-event expansion remains same-owner and requires an authorized parent before
loading related content. Typed `previous_event` is ignored when no validated memory
reference exists, producing a limited response rather than semantic guessing.

## Testing

Implementation follows red-green TDD and must prove:

1. `IntentDecision` accepts valid typed multi-source decisions and rejects duplicate,
   inconsistent, oversized, owner-bearing, index-bearing, and tool-bearing output;
2. `이번주 일정알려줘`, `이번주 일정 뭐야?`, `이번주 일정이 뭔지 알려줘`, and
   other paraphrases all produce the same Calendar-only typed decision in a
   model-backed integration evaluation;
3. application unit tests use injected typed decisions and contain no production
   keyword-intent tables or raw-question substring classification;
4. source discovery, planner, judge, missing-information reporting, and memory use
   typed fields only;
5. structured analysis timeout, malformed output, or unavailable model causes zero
   search calls and a limited response;
6. typed current-week and exact-date scopes preserve Asia/Seoul half-open ranges;
7. typed previous-event follow-up expands only the same-owner saved event;
8. hostile model output cannot select physical indices, owner IDs, undeclared
   sources, duplicate actions, or more than four actions;
9. August fixture, owner/inactive/cancelled decoy, canonical multi-source, and
   Calendar boundary tests remain green;
10. the full backend suite, frontend tests, lint, build, configured-LLM CLI smoke,
    offline typed-scenario smoke if retained, and live two-turn ASGI smoke pass.

The paraphrase evaluation is a model integration gate, not a mocked unit assertion.
Unit tests validate typed orchestration; they do not claim to measure language
understanding.

## Documentation Changes

Update demo documentation to state that free-form natural-language mode requires the
configured LLM. Document any explicit offline scenario separately and remove claims
that arbitrary questions work without an LLM. Operations documentation must describe
safe limited behavior when structured analysis is unavailable.

## Non-Goals

- changing the August business-day fixture records;
- changing OpenSearch mappings, embeddings, ranking, or index aliases;
- allowing the LLM to author filters, owner IDs, aliases, or DSL;
- replacing deterministic date arithmetic or security validation with model output;
- generating final factual prose without evidence and citation checks;
- adding a local language model or classifier dependency in this change.

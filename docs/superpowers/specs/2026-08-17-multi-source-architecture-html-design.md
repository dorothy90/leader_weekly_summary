# Multi-Source Agentic RAG Architecture HTML Design

**Date:** 2026-08-17  
**Status:** Approved direction; implementation pending written-spec review  
**Artifact:** `MultiSource_Agentic_RAG_Architecture.html`

## Purpose

Create a self-contained Korean HTML explainer that lets the project owner inspect
how one chat request moves through the multi-source agent, which decisions are made
by an LLM, which controls remain deterministic Python, what each LLM prompt receives
and returns, and how multi-turn memory is persisted and reused.

This is an inspection aid, not a marketing page or a replacement for source-code
documentation. It must expose the current implementation accurately, including its
limitations.

## Audience and primary job

The sole primary audience is the project owner. The page's job is to answer these
questions without requiring source-code navigation:

1. What happens after `/v1/chat` receives a request?
2. Why are there four LLM calls for the simple Calendar example?
3. What exact prompt, input payload, and structured schema does each call use?
4. Which parts are semantic LLM decisions and which parts are Python safety controls?
5. How do Domain, Mail, and Calendar indexes participate?
6. What does the current multi-turn implementation remember and omit?
7. What did the verified `이번 주 일정 뭐야?` run actually do?

## Chosen format

Use one responsive, self-contained HTML file with embedded CSS and JavaScript. It
must not require a build step, framework, web server, external CDN, remote font, or
network access. Opening the file locally must display the complete document.

Three presentation approaches were considered:

- a static technical document, which is printable but makes the four related LLM
  contracts difficult to compare;
- a raw code/schema reference, which is exact but forces the reader to reconstruct
  the execution flow;
- an interactive inspection board, which keeps the full flow visible while exposing
  detailed prompt contracts on demand.

The interactive inspection board is selected because it best supports personal
understanding and repeated architecture checks.

## Information architecture

### 1. Orientation header

Open with the real system thesis rather than a generic title:

> One question, four semantic LLM calls, one bounded agent loop.

Show the verified simple-run facts beside it:

- question: `이번 주 일정 뭐야?`
- LLM calls: 4
- tool calls: 1 (`search_calendar`)
- evidence: 5 Calendar records
- result: succeeded with valid citation IDs

These are explicitly labeled as the verified August 17, 2026 dummy-index run, not
universal performance metrics.

### 2. End-to-end system map

Show a horizontally readable flow that wraps into a vertical flow on small screens:

```text
Client
  -> POST /v1/chat
  -> owner-scoped conversation load
  -> routing LLM
  -> planner LLM
  -> allowlisted multi-index tool executor
  -> judge LLM
       -> optional bounded tool loop
  -> answer LLM
  -> citation/ownership validation
  -> conversation save
  -> ChatResponse
```

The map visually distinguishes four responsibilities:

- LLM semantics;
- Python orchestration;
- data stores and retrieval;
- security and validation.

The old Fast/Deep/Diagnostic router does not appear in this path. A clear callout
states that the current entry point is the single multi-source graph.

### 3. Four-call LLM rail

The page's signature interaction is a four-stop execution rail:

```text
[1 Routing] -> [2 Planner] -> [3 Judge] -> [4 Answer]
```

Selecting a stop updates a detail panel without navigation. Each panel contains:

- purpose;
- exact current system prompt;
- sanitized example input payload;
- Pydantic output model;
- downstream consumer;
- failure behavior;
- whether the call can repeat.

Keyboard users can move through the stops as standard buttons. The selected button
uses `aria-pressed`, and the detail panel uses a stable labeled region.

The contracts are:

- Routing: question plus bounded memory -> `IntentDecision`;
- Planner: question, `QueryAnalysis`, observations, safe memory ->
  `PlanningDecision` containing one `ToolAction` or `null`;
- Judge: question, analysis, observations, evidence, safe memory, iteration count ->
  `JudgeDecision`;
- Answer: question, analysis, evidence, missing information, conversation topic ->
  `AnswerDecision { answer }`.

The answer panel explicitly states that the LLM writes the prose. Pydantic only
validates the response envelope and bounds; Python then sanitizes text, normalizes
citation labels, validates citation targets and ownership, and adds limited-result
disclosures when required.

### 4. LLM versus Python boundary

Use a two-column responsibility ledger instead of a vague comparison chart.

LLM-owned semantic decisions:

- interpret the user's intent;
- select logical sources and search wording;
- choose the next allowlisted tool action;
- judge evidence sufficiency and recommend a next action;
- write the final cited Korean answer.

Python-owned controls:

- resolve relative dates to a configured timezone range;
- inject authenticated owner filters and physical aliases;
- validate Pydantic schemas and allowlisted tool shapes;
- block duplicate actions and cap the loop at four iterations;
- normalize evidence and enforce owner isolation;
- validate that citation IDs exist and are owner-authorized;
- persist bounded conversation memory.

A warning states that citation validation currently checks target existence and
ownership, not semantic entailment between a sentence and its cited evidence.

### 5. Multi-index retrieval map

Show the source registry branching from the executor:

- Domain Knowledge -> `syld_gpt`;
- Mail -> `ews-mail-active`;
- Calendar -> `ews-calendar-active`;
- Calendar event expansion -> Calendar alias with validated stable event relation.

The diagram states that the model sees logical source/tool schemas but cannot select
a physical index, owner ID, ACL filter, or OpenSearch DSL. Hybrid BM25/vector search
and the OpenRouter embedding model are shown beneath the source branches.

### 6. Multi-turn memory map

Show two turns sharing one server-issued `conversation_id`:

```text
Turn 1: 이번 주 일정 뭐야?
  -> save messages, turns, evidence, entities, topic, previous event reference

Turn 2: 그중 첫 번째 일정의 상세 내용과 참석자를 알려줘
  -> owner-scoped load
  -> bounded structured memory enters semantic stages
```

The map must distinguish stored memory from prompt-visible memory. The store keeps
up to 20 messages and 20 turn records, but the current structured agent does not send
the complete message history to its four semantic calls. It supplies bounded
entities, current topic, and, where applicable, the previous event reference. This
limitation must be prominent rather than buried in a footnote.

### 7. Verified execution trace

Reproduce the verified production-notebook trace as a readable event strip:

```text
LLM: routing -> planner -> judge -> answer
Tool: search_calendar
Evidence: 5
Execution: succeeded
```

Include the five August 17-21 dummy events as compact evidence cards and one sample
final answer. Clearly label dates and data as fixtures. Do not embed credentials,
hostnames, trace IDs, user secrets, or private infrastructure configuration.

### 8. Current limitations and source map

End with a short, direct list:

- complete conversation history is stored but not passed to all semantic calls;
- citation ID validation is not semantic entailment checking;
- a free routed LLM may have variable latency and output quality;
- additional search rounds add judge calls;
- offline scripted scenarios are test/demo fixtures, not the production path.

Add clickable local-path labels for the principal implementation files, displayed as
plain text in the standalone HTML because browser security may block direct local
file navigation.

## Visual direction

The subject is a running decision system, so the design uses the visual language of
an engineering signal desk rather than a dashboard template.

### Palette

- Drafting paper: `#F3F7F8`
- Carbon ink: `#17242D`
- Signal blue: `#2357D9`
- Retrieval teal: `#167C72`
- Judge amber: `#C97816`
- Boundary red: `#B83A3A`

Color never carries meaning alone; every semantic category also has a text label,
shape, or line treatment.

### Typography

Use a local system Korean sans-serif stack for body readability and a local
monospace stack for prompts, schemas, trace stages, and evidence IDs. No remote font
request is allowed. Display headings use tight tracking and asymmetric line breaks
to keep the page technical without imitating a generic admin dashboard.

### Layout

Use a wide central canvas with a sticky local section index on desktop and a compact
horizontal index on mobile. Cards use small corner cuts and signal-line connectors
instead of uniform rounded rectangles. The four-call rail is the only animated
element: a short trace pulse moves once on initial view and stops. Reduced-motion
users see a static highlighted path.

### Signature element

The memorable element is the LLM execution rail. Its selected stage controls the
prompt/schema inspection panel, making the difference between semantic generation
and deterministic validation visible without changing pages.

## Interaction and accessibility

- All controls are native buttons or links.
- Focus indicators meet contrast requirements and are never removed.
- Prompt details remain accessible without JavaScript through the first panel and
  embedded fallback content; JavaScript enhances switching between stages.
- A “show all prompts” control expands all four contracts for printing or scanning.
- Code blocks wrap or scroll within their own container on narrow screens.
- `prefers-reduced-motion` disables trace animation and smooth scrolling.
- The document remains usable at 320 CSS pixels width.

## Data and security constraints

The HTML is a static explanatory artifact. It does not call the API, read `.env`,
connect to OpenSearch or MongoDB, or execute prompts. Every displayed payload is a
sanitized example derived from the public dummy dataset.

No API key, password, raw Mongo URI, private hostname, real email address, or live
trace identifier may appear. Physical aliases may be shown because they are already
documented configuration names, but owner values and infrastructure endpoints are
omitted.

## Error representation

The page documents rather than simulates failures. Each LLM-stage panel includes the
bounded behavior for invalid structured output. The overall map includes limited
states for unavailable analysis, no evidence, insufficient evidence, retrieval
failure, and invalid citations. Error examples use stable public error codes and do
not display raw provider exceptions.

## Verification

Implementation verification must include:

1. a structural test that parses the HTML and confirms the eight required sections,
   four LLM stage controls, exact current prompt headings, and no external resources;
2. a secret-pattern scan for credential assignments and private endpoints;
3. browser rendering at desktop and mobile sizes;
4. keyboard interaction checks for the LLM rail and “show all prompts” control;
5. a screenshot review for overflow, clipped diagrams, contrast, and information
   hierarchy;
6. the existing Mail RAG test suite to ensure the documentation-only artifact does
   not change runtime behavior.

## Non-goals

- changing the agent graph, prompts, schemas, API, or notebook;
- creating a live operations console;
- exposing chain-of-thought or hidden provider reasoning;
- adding an HTML build pipeline or frontend dependency;
- claiming semantic citation verification that the current code does not perform.

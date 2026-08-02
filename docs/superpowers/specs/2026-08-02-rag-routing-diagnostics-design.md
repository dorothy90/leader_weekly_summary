# RAG Routing Diagnostics Design

## Goal

Make the RAG verification console prove whether a request was routed to general conversation, Fast RAG, Deep Research, or clarification. The console must distinguish automatic routing from an explicit Fast or Deep override and show the server's actual decision rather than inferring it from the final response mode.

## Root Cause

The verification UI currently defaults to Fast and always sends `response_mode: "fast"`. The router treats explicit Fast or Deep mode as authoritative, so a greeting such as `hi` bypasses automatic general-conversation routing and is forced through mail retrieval.

The API then returns only `mode: "fast_rag"` or `mode: "deep_research"`. Both an automatically routed general response and an explicitly forced Fast response use `mode: "fast_rag"`, so the UI cannot distinguish them. The router's `route`, `reason_code`, `confidence`, and `estimated_searches` values are currently discarded at the API boundary.

This was reproduced against the live Cloudflare-backed API:

- `response_mode: "auto"`, message `hi` returned a normal greeting.
- `response_mode: "fast"`, message `hi` returned a limited mail-evidence answer.
- Both responses reported `mode: "fast_rag"`.

## Approaches Considered

### Selected: Return Routing Diagnostics in the Chat Response

Add a backward-compatible `routing` object to `ChatResponse` and render it in the verification console. The API reports the router decision already used for execution, so the UI displays authoritative data.

### Rejected: Infer Routing in the UI

The UI could compare the requested mode with `mode`, message text, and job presence. This cannot distinguish general from Fast because both currently report `fast_rag`, and it cannot recover the server's reason, confidence, or search estimate.

### Rejected: Add a Separate Router-Diagnostics Endpoint

A dry-run route endpoint could expose the decision, but its result could differ from the subsequent chat request because it would invoke routing twice. It also adds a second API workflow solely for a test console.

## API Contract

Add the following required object to every successful `ChatResponse`:

```json
{
  "routing": {
    "requested_mode": "auto",
    "route": "general",
    "executed_system": "general",
    "reason_code": "deterministic_general",
    "confidence": 0.97,
    "estimated_searches": 0
  }
}
```

Fields:

- `requested_mode`: exact request value, one of `auto`, `fast`, or `deep`
- `route`: final router decision, one of `general`, `fast`, `deep`, or `clarify`
- `executed_system`: actual path taken, one of `general`, `fast_rag`, `deep_research`, or `clarification`
- `reason_code`: final safe router reason code
- `confidence`: final router confidence from 0 through 1
- `estimated_searches`: final non-negative search estimate

The existing top-level `mode` remains unchanged for backward compatibility. Existing clients that ignore unknown response fields continue to work.

`QualityStatus.retrieval_mode` adds `not_used`. General and clarification responses use `not_used` because neither path performs mail retrieval. Fast results continue to report `hybrid` or `bm25`. Deep job creation has no completed retrieval result yet, so its existing optional quality behavior remains unchanged.

## Router Error Diagnostics

Explicit Fast and Deep requests retain `reason_code: "explicit_mode"`.

When the LLM router raises or returns an unusable result and deterministic fallback is used, the router returns a safe reason code prefixed with `router_error_`, followed by the deterministic reason. For example, `router_error_deterministic_fast`. Raw provider errors, prompts, credentials, and exception text are never returned.

Deterministic policy overrides that occur after a valid LLM decision retain their existing `deterministic_*` reason codes. This distinguishes a valid policy override from an error fallback without exposing sensitive details.

When a greeting or help request is normalized to `general`, `estimated_searches` is normalized to `0` because that branch performs no retrieval.

## Server Components and Data Flow

### Domain Models

`app/domain/chat.py` owns the routing diagnostics response model and the expanded retrieval-mode literal. The routing object uses the same constrained values already accepted by `ChatRequest` and `RouteDecision`.

### Router

`app/graphs/router.py` continues to make exactly one routing decision. It only changes the safe reason code used when an exception triggers deterministic fallback.

### Chat Route

`app/api/routes/chat.py` creates routing diagnostics immediately after obtaining `RouteDecision`. A small pure helper maps `requested_mode`, final decision, and the chosen response branch to the response model. Every successful branch includes it:

- clarification -> `executed_system: "clarification"`
- general -> `executed_system: "general"`
- deep -> `executed_system: "deep_research"`
- fast -> `executed_system: "fast_rag"`

The request-body `user_id`, policy construction, owner filtering, persistence, and Fast/Deep execution remain unchanged.

## Verification Console

### Request Controls

The mode control order becomes:

1. Auto
2. Fast 강제
3. Deep 강제

Auto is the default. Fast and Deep remain explicit overrides; the two execution systems are not merged and no runtime workload adjustment is introduced.

### Result Presentation

The result panel shows a routing flow before answer-quality badges:

`요청 Auto -> Router general -> 실행 general`

It also shows the reason code, confidence, and estimated search count. Labels state when a mode was explicitly forced.

General and clarification responses do not show citation, hybrid/BM25, or limited-answer badges because those are retrieval-result concepts. Fast responses keep those badges. Deep responses keep job status, progress, controls, and eventual report presentation.

### Inspector

The Summary tab adds:

- requested mode
- router decision
- executed system
- reason code
- confidence
- estimated searches
- actual retrieval mode, including `not_used`

The JSON tab continues to show the unmodified request and response payloads. The Events tab continues to show Deep SSE events.

## Error Handling

- Missing or invalid `routing` in a nominally successful API response is treated as an invalid response by the verification client, because the console cannot verify routing without it.
- API error envelopes retain their existing handling; no routing object is fabricated for failed requests.
- Router fallback returns only safe reason codes, never raw exception messages.
- Deep event-stream failures retain reconnect and polling fallback behavior.
- Existing BM25 fallback disclosure remains unchanged and appears only when retrieval actually falls back.

## Testing

### Backend

- `auto + hi` returns route `general`, executed system `general`, and retrieval `not_used`.
- `auto + a complex research request` returns route `deep` and executed system `deep_research`.
- `fast + hi` returns route `fast`, executed system `fast_rag`, and reason `explicit_mode`.
- `deep + a simple question` returns route `deep`, executed system `deep_research`, and reason `explicit_mode`.
- clarification includes executed system `clarification` and retrieval `not_used` when quality is present.
- LLM router failure uses a `router_error_deterministic_*` safe reason without raw exception text.
- Existing exact request-body `user_id`, ACL, BM25, Fast, and Deep tests continue to pass.

### Frontend

- Auto is selected by default and sends `response_mode: "auto"`.
- Fast 강제 and Deep 강제 send their exact explicit modes.
- the routing flow and all diagnostic fields render from the server response.
- general and clarification hide retrieval-quality badges.
- Fast displays citation, retrieval, and limited-answer badges where applicable.
- Deep job tracking and SSE/polling behavior remain unchanged.
- the API response validator requires a valid routing object and accepts retrieval mode `not_used`.

### Live Verification

After automated tests, restart the backend with the external environment file, retain the RAG UI on port 5180, and verify through the UI proxy:

- `hi` in Auto displays general routing and a normal greeting without retrieval badges.
- a simple mail question in Auto displays Fast routing.
- a multi-week or report-style request in Auto displays Deep routing and job tracking.
- manual Fast and Deep overrides display `explicit_mode`.

## Security and Compatibility

- Routing diagnostics contain only bounded enums, safe reason codes, numeric confidence, and search estimates.
- No credentials, raw exceptions, prompts, document content, or cross-owner identifiers are added.
- Existing response fields are retained; `routing` is additive.
- Request-body `user_id` remains authoritative and every retrieval path retains exact owner filtering.

## Acceptance Criteria

- The UI defaults to Auto and clearly distinguishes requested mode, router decision, and executed system.
- `hi` in Auto does not enter mail retrieval and does not display retrieval-quality badges.
- Explicit Fast and Deep overrides are visibly identified.
- Router reason, confidence, estimated searches, trace ID, and actual retrieval usage are visible in the inspector.
- Deep job progress and events remain testable.
- API and UI tests pass without weakening ACL, BM25 fallback, or secret handling.

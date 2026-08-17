# Production Environment Notebook Design

## Goal

Create one Jupyter notebook that exercises the same production application
path as `POST /v1/chat` while remaining convenient to run interactively. The
notebook must use the configured OpenRouter LLM and embedding models, real
OpenSearch aliases, MongoDB conversation storage, owner policy, API validation,
and the single `MultiSourceAgenticWorkflow` entry point.

## Artifact

The repository root will contain
`MultiSource_Production_Environment_Test.ipynb`. It will be a focused production
integration notebook, not a tutorial and not an alternate implementation of
the agent.

## Execution Architecture

The notebook will construct the application in-process using the same project
factories as the deployed API:

```text
.env / process environment
        |
        v
Settings.from_env()
        |
        v
build_container(settings)
        |
        v
create_app(container)
        |
        v
httpx.AsyncClient(ASGITransport)
        |
        v
POST /v1/chat
        |
        v
MultiSourceAgenticWorkflow
```

This covers request validation, trace middleware, owner policy, conversation
loading and saving, citation filtering, and response serialization without
requiring a separately managed Uvicorn process. `MULTI_SOURCE_DEMO` must be
false; the notebook will fail its preflight if demo mode is enabled.

## Notebook Sections

1. Explain prerequisites and require a kernel with the repository dependencies.
2. Locate the repository root robustly and add it to `sys.path` only when
   necessary.
3. Load `Settings.from_env()` and display a sanitized configuration summary:
   provider, base URL, model identifiers, timeout, index aliases, MongoDB
   database, timezone, and whether the API key is set. Secret values, MongoDB
   credentials, and OpenSearch credentials must never be printed.
4. Validate required configuration before constructing the container:
   `OPENROUTER_API_KEY` must be nonempty and demo mode must be disabled.
5. Build the production `ServiceContainer` and in-process FastAPI application.
6. Call `GET /ready` and display each dependency state. A non-ready result must
   stop the chat cells with a clear message rather than continuing into a
   misleading test.
7. Provide one input cell for `USER_ID`, `QUESTION`, and typed API filters.
8. Send a route-free `POST /v1/chat` request and render the HTTP status, trace
   ID, answer, citations, disclosures, agent tool calls, judge decisions,
   iteration count, execution status, actual search count, evidence count, and
   duration.
9. Assert that the public request and response contain no `response_mode`,
   `mode`, or `routing` fields.
10. Send an optional follow-up using the first response's `conversation_id` to
    verify real MongoDB-backed conversation continuity and owner scoping.

## Data and Security Boundaries

- The notebook uses a user-supplied `USER_ID`; it does not infer identity from
  team names, documents, or environment state.
- Filters are passed through the public `ChatRequest` shape and cannot select
  physical OpenSearch indices.
- All queries continue to receive the production owner and lifecycle filters
  from backend code.
- The notebook displays only the already-sanitized public API response.
- Configuration output redacts all credential values.
- No `.env` file or secret is created, modified, or embedded in notebook output.
- Notebook cells ship without saved execution output so credentials, evidence,
  and local service details cannot be committed accidentally.

## Error Handling

Preflight failures will distinguish missing API credentials, demo-mode
misconfiguration, unavailable MongoDB, unavailable OpenSearch, and missing
aliases using safe readiness output. HTTP failures will display the API's safe
error envelope and trace ID, then raise an assertion to make the failed gate
visible. The notebook will not fall back to dummy data or a rule-based analyzer
because that would no longer represent the requested production environment.

## Verification

Repository-side verification will:

- parse the notebook as valid JSON;
- compile every Python code cell to catch syntax errors;
- verify that code cells import and call `Settings.from_env`,
  `build_container`, `create_app`, `ASGITransport`, `/ready`, and `/v1/chat`;
- verify the request is route-free and the notebook checks the response for
  obsolete route fields;
- verify all committed cell outputs and execution counts are empty;
- run the existing backend test suite to guard the production factories used by
  the notebook.

Live execution of the LLM, OpenSearch, and MongoDB cells is an environment gate,
not a repository-only test. It can pass only where the configured external
services and aliases are reachable.

# Manus Lite LLM Provider Design

**Date:** 2026-08-17
**Status:** Approved design, pending implementation plan

## Goal

Use the Manus API for agent analysis while keeping document embeddings on
OpenRouter. During the current Manus campaign, request `manus-1.6-lite`; the
provider may report `manus-1.6` as the actual execution profile. Keep provider
selection configurable so the LLM can later return to an OpenAI-compatible
endpoint without changing retrieval or workflow code.

## Confirmed live behavior

- OpenRouter accepted an embedding request for `qwen/qwen3-embedding-8b` and
  returned a numeric vector with 4,096 dimensions.
- Direct `manus-1.6` task creation returned HTTP 200, but the resulting task
  remained unavailable from `task.detail` and `task.listMessages`.
- A `manus-1.6-lite` task completed successfully. `task.detail` reported the
  actual profile as `manus-1.6`, and the task reported zero credit usage.

The application will therefore request `manus-1.6-lite` for the current test
period and will preserve the provider-reported profile in diagnostics.

## Considered approaches

### 1. Provider adapter with separate LLM and embedding settings — selected

Add a Manus-specific LLM gateway behind the existing model-completion
interface. Keep the embedding gateway on OpenRouter. Select the LLM provider
through configuration.

This isolates the asynchronous Manus task protocol and allows a later switch
to an OpenAI-compatible LLM without altering retrieval or orchestration.

### 2. Replace the existing gateway with Manus only

This is smaller initially, but a later OpenAI-compatible migration would
require another code change and would couple application configuration to one
provider. It is not selected.

### 3. Put an OpenAI-compatible proxy in front of Manus

This would hide the Manus protocol from the application, but it introduces a
new deployed service and operational boundary solely for protocol conversion.
It is unnecessary for the current scope.

## Configuration boundary

Create `app/config/ai.py` as the single owner of AI-provider configuration.
Define `AISettings` there and make the existing `Settings` class inherit those
fields. This preserves the current single `.env` loading path while moving all
AI-provider definitions out of `app/config/settings.py`.

The configuration supports:

- `LLM_PROVIDER=manus` for the current test environment.
- `MANUS_API_KEY` with no default and no logging.
- `MANUS_BASE_URL=https://api.manus.ai`.
- `MANUS_AGENT_PROFILE=manus-1.6-lite`.
- Bounded Manus polling interval and task timeout values.
- `LLM_PROVIDER=openai_compatible` for a later migration.
- Separate OpenAI-compatible LLM key, base URL, model, and timeout values.
- Existing `OPENROUTER_API_KEY`, base URL, embedding model, and timeout for
  embeddings only.

There is no automatic fallback between Manus and an OpenAI-compatible LLM.
Provider changes are explicit environment changes so failures are observable.

## Gateway architecture

`build_ai_gateways` becomes a small factory:

- For `LLM_PROVIDER=manus`, construct `ManusLLMGateway`.
- For `LLM_PROVIDER=openai_compatible`, construct the existing
  `OpenAILLMGateway` with the configured compatible endpoint.
- Always construct `OpenAIEmbeddingGateway` against OpenRouter.

Both LLM gateways implement the existing `complete_model(system, user,
schema)` contract. Workflow and retrieval components do not branch on provider
names.

## Manus request lifecycle

For each structured completion:

1. Submit `POST /v2/task.create` with `agent_profile=manus-1.6-lite`,
   `interactive_mode=false`, `hide_in_task_list=true`, and private visibility.
2. Include a Manus-compatible structured-output schema derived from the
   requested Pydantic response model.
3. Poll `GET /v2/task.detail` and `GET /v2/task.listMessages` until the task is
   stopped, waiting, errored, or timed out.
4. Accept only a successful `structured_output_result`; validate its value with
   the original Pydantic model.
5. Record requested profile, provider-reported actual profile, status, latency,
   and credit usage in safe diagnostics. Never record credentials or full
prompts.

Schema conversion recursively enforces Manus requirements: every object lists
all properties as required, objects reject additional properties, and optional
values use nullable types. Unsupported validation keywords are removed while
the original Pydantic model remains the final authority after retrieval.

Transient 404 responses immediately after creation receive bounded retries.
The gateway fails closed on timeout, waiting status, provider error, invalid
structured output, or repeated 404 responses. It does not silently call another
LLM provider.

To limit campaign usage and latency, Manus analysis receives one task attempt
per application request. Existing application-level unavailable behavior
handles a failed analysis without keyword routing or hard-coded intent rules.

## Notebook behavior

Update `MultiSource_Production_Environment_Test.ipynb` to use the same
configuration and gateway factory as the application.

The notebook will contain separate, clearly labeled checks for:

1. Safe configuration summary showing only whether each key is present.
2. OpenRouter embedding connectivity, returned model, numeric-vector status,
   and vector dimension.
3. Manus task creation and completion using `manus-1.6-lite`, including the
   provider-reported actual profile and credit usage.
4. Application-level structured agent analysis through the configured gateway.
5. Existing multi-index retrieval and chat workflow checks.

Live cells fail with actionable messages when keys or services are missing.
Notebook source and saved outputs must not contain API keys, task URLs, task
IDs, full provider responses, or endpoint credentials.

## Error handling and readiness

- Missing provider keys fail during gateway construction or an explicit
  readiness check; they do not produce unauthenticated calls.
- Manus authentication, rate-limit, task, and timeout failures use distinct
  safe error categories.
- Readiness identifies the LLM provider and embedding provider independently.
- A successful `task.create` alone is not considered healthy; health requires a
  retrievable completed task or a dedicated non-consuming endpoint if Manus
  later provides one.

## Tests

Implementation follows test-first development.

- Configuration tests cover provider selection, Lite default, secret
  separation, and OpenAI-compatible future settings.
- Factory tests prove Manus is used only for LLM calls and OpenRouter only for
  embeddings.
- Manus gateway tests cover create, transient 404, running, stopped,
  structured-output validation, waiting, error, timeout, and safe diagnostics.
- Notebook contract tests prove it imports application configuration, keeps the
  two live checks separate, requests Lite, reports actual profile, and contains
  no embedded secrets.
- Existing `tests/mail_rag` remains the regression suite.
- Live tests are optional and run only when their required environment keys are
  present.

## Out of scope

- Automatic provider fallback.
- Keyword-based intent or source routing.
- Manus connectors, browser automation, generated media, and Max profile.
- Changes to OpenSearch indexes, retrieval ranking, MongoDB, or the frontend.
- Persisting Manus task IDs in application data.

## Acceptance criteria

- The default test LLM request profile is `manus-1.6-lite`.
- The application can report that Manus executed the task with a different
  actual profile without treating it as an error.
- Embeddings continue to use OpenRouter and `qwen/qwen3-embedding-8b`.
- LLM and embedding credentials/configuration are defined separately.
- Switching to an OpenAI-compatible LLM requires configuration changes only.
- The production-environment notebook independently verifies both providers
  without exposing secrets.
- Mocked provider tests and the complete mail RAG regression suite pass.

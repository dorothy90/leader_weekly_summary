# OpenRouter Free Multi-Index Test Design

## Goal

Use OpenRouter for both intent analysis and embeddings during the production
notebook test. Remove Manus from the default and notebook test paths. Exercise
the real application container and verify that one chat request reaches Mail,
Calendar, and Domain Knowledge search.

## Selected approach

Add `openrouter` as a first-class LLM provider and make it the default. Use the
existing `OPENROUTER_API_KEY` and `OPENROUTER_BASE_URL` for both LLM and
embedding clients. Set the default LLM model to `openrouter/free` and keep the
embedding model at `qwen/qwen3-embedding-8b`.

This is preferred over the existing `openai_compatible` provider because it
does not require copying the same OpenRouter credential into a second setting.
It is preferred over a notebook-only override because notebook and application
configuration must match.

Manus configuration and gateway code remain available for later explicit use,
but there is no automatic fallback to Manus.

## Configuration

The default configuration becomes:

```dotenv
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<secret>
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_LLM_MODEL=openrouter/free
OPENROUTER_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
OPENROUTER_REQUEST_TIMEOUT_SECONDS=150
```

`LLMProvider` accepts `openrouter`, `manus`, and `openai_compatible`.
`resolve_llm_endpoint()` returns the OpenRouter key, base URL, LLM model, and
timeout when `LLM_PROVIDER=openrouter`. `resolve_embedding_endpoint()` remains
OpenRouter-only.

## Runtime flow

`build_llm_gateway()` constructs `OpenAILLMGateway` for `openrouter` using the
same OpenRouter settings as the embedding gateway. Manus still constructs
`ManusLLMGateway` only when explicitly selected. OpenAI-compatible endpoints
keep their existing separate settings.

OpenRouter analysis keeps two bounded attempts. A failed or invalid free-model
response produces the existing unavailable-analysis result. It never switches
to Manus or keyword routing.

## Notebook flow

Remove the standalone Manus task cell and its saved error output. Remove the
standalone embedding smoke cell because the real application request already
uses the production embedding gateway.

Notebook order:

1. Load settings and print only safe endpoint/model metadata and key presence.
2. Assert `LLM_PROVIDER=openrouter` and `OPENROUTER_LLM_MODEL=openrouter/free`.
3. Build the real production container and verify `/ready`.
4. Send one cross-source question through `/v1/chat`.
5. Require `search_mail`, `search_calendar`, and `search_domain_knowledge` in
   `agent_trace.tool_calls`.
6. Require Mail, Calendar, and Domain Knowledge in returned references and
   require valid citations.
7. Run the existing conversation-continuity check.

The cross-source question asks which meeting discussed a mail-reported NAND
yield issue, what action was agreed, and what the issue means technically. It
contains no route, index, or tool hints.

Committed notebook cells have `execution_count: null` and empty outputs. No
credential, task identifier, raw provider response, or physical index name is
saved.

## Test coverage

- Settings tests cover OpenRouter defaults and independent provider switching.
- Factory tests prove OpenRouter LLM and embedding clients use the same key but
  remain separate clients.
- Notebook contract tests reject Manus calls, require `openrouter/free`, and
  require real multi-index tool/reference assertions.
- Existing route-free, owner-isolation, readiness, and conversation tests stay
  enabled.
- Live notebook execution remains manual because it needs configured
  OpenSearch, MongoDB, OpenRouter, and populated indices.

## Known trade-off

`openrouter/free` can select a different free model per request. Availability,
latency, and JSON-following quality can vary. This is acceptable for the current
integration test. A specific `:free` model can replace it later if repeatability
becomes more important than automatic availability.

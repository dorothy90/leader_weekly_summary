# Split Cerebras and Scaleway Provider Design

## Goal

Run every chat-completion call through Cerebras `zai-glm-4.7` and every query-embedding call through Scaleway `qwen3-embedding-8b`. Preserve the existing `POST /v1/chat` contract, Fast and Deep workflows, conversation behavior, OpenSearch retrieval, request-body `user_id` filtering, and BM25 fallback disclosure.

## Context

The application currently resolves one AI provider and creates one `AsyncOpenAI` client shared by the LLM and embedding gateways. That coupling prevents the requested combination because Cerebras provides the selected LLM while Scaleway provides the selected embedding model.

Both services expose OpenAI-compatible APIs. Cerebras uses `https://api.cerebras.ai/v1` with model ID `zai-glm-4.7`. Scaleway uses `https://api.scaleway.ai/v1` with model ID `qwen3-embedding-8b`.

The OpenSearch vectors must have been created with the same Qwen3 embedding model and output dimension used for query embeddings. This change does not recreate or mutate the index.

## Chosen Approach

Create independent immutable endpoint configurations and independent OpenAI-compatible clients.

Alternatives considered:

1. Repoint the existing shared provider configuration to one service. This cannot use Cerebras and Scaleway simultaneously.
2. Expand the existing provider-selection enum with paired combinations. This embeds deployment combinations in application logic and recreates coupling.
3. Resolve an LLM endpoint and an embedding endpoint separately. This is selected because each dependency has one purpose, credentials remain isolated, and future provider changes do not affect the other dependency.

## Configuration

Replace shared AI-provider selection with the following explicit settings:

- `CEREBRAS_API_KEY`, stored as `SecretStr`, default empty
- `CEREBRAS_BASE_URL`, default `https://api.cerebras.ai/v1`
- `CEREBRAS_LLM_MODEL`, default `zai-glm-4.7`
- `SCALEWAY_API_KEY`, stored as `SecretStr`, default empty
- `SCALEWAY_BASE_URL`, default `https://api.scaleway.ai/v1`
- `SCALEWAY_EMBEDDING_MODEL`, default `qwen3-embedding-8b`

The old combined `AI_PROVIDER`, OpenRouter, and Cloudflare provider-resolution fields are removed from the RAG dependency path. No silent fallback to another LLM or embedding provider occurs.

Settings expose two pure resolvers:

- `resolve_llm_endpoint()` returns the Cerebras key, base URL, and LLM model.
- `resolve_embedding_endpoint()` returns the Scaleway key, base URL, and embedding model.

Resolved endpoint objects must redact their secret values in representations.

## Dependency Assembly and Data Flow

`build_ai_gateways()` creates two `AsyncOpenAI` clients:

1. The Cerebras client is passed only to `OpenAILLMGateway` with `zai-glm-4.7`.
2. The Scaleway client is passed only to `OpenAIEmbeddingGateway` with `qwen3-embedding-8b`.

All router, general, clarification, Fast RAG, and Deep RAG LLM calls continue to use the single injected LLM gateway and therefore reach Cerebras. Retrieval continues to use the single injected embedding gateway and therefore reaches Scaleway.

No graph, route, request, response, conversation, or frontend contract changes are required.

## Error Handling

- Missing or rejected Cerebras credentials retain the existing LLM error mapping and must not fall back to Scaleway, OpenRouter, or Cloudflare.
- Missing or rejected Scaleway credentials trigger the existing BM25-only retrieval path.
- Every BM25-only result retains the existing Korean disclosure that the embedding service was unavailable.
- Provider failures must not weaken the exact request-body `user_id` filter on BM25 or OpenSearch vector queries.
- API keys must not appear in logs, traces, exceptions returned to clients, diagnostic payloads, or object representations.

## Testing

Use test-driven development:

1. Add settings tests for independent Cerebras and Scaleway endpoint resolution, defaults, overrides, and secret redaction; run them and confirm failure against the shared-provider design.
2. Add dependency tests proving two distinct clients are constructed with the correct credentials, base URLs, and model assignments; run them and confirm failure.
3. Implement the minimal settings and dependency changes.
4. Run focused settings and dependency tests, followed by the full backend test suite.

Existing retrieval tests remain responsible for verifying embedding failure to BM25 fallback, disclosure text, and `user_id` isolation.

If both real API keys are available, run non-secret smoke calls against Cerebras and Scaleway. Otherwise report live connectivity as unverified without weakening automated-test results.

## Documentation

Update deployment documentation with the six new environment variables and state that the index/query embedding model and dimension must match. Do not put actual credentials in tracked files.

## Out of Scope

- Re-embedding OpenSearch documents
- Changing OpenSearch mappings, aliases, or stored data
- Adding runtime provider selection to the request body or frontend
- Adding provider failover
- Changing Fast/Deep routing or multi-turn behavior
- Editing an external `.env` file without explicit credential values and authorization

## Acceptance Criteria

- All LLM calls use a Cerebras client configured with `zai-glm-4.7`.
- All embedding calls use a distinct Scaleway client configured with `qwen3-embedding-8b`.
- The application no longer requires one provider to serve both capabilities.
- `POST /v1/chat` and frontend behavior remain unchanged.
- Embedding failure still degrades to owner-filtered BM25 with the required disclosure.
- Secrets remain redacted.
- Focused and full backend tests pass.

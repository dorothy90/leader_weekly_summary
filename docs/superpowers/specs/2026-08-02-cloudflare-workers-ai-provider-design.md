# Cloudflare Workers AI Provider Design

## Goal

Use the Cloudflare Workers AI credentials already present in the external environment file to run both chat completion and query embedding through the existing OpenAI-compatible gateways. Preserve the separate Fast and Deep RAG systems, request-body `user_id` owner filtering, and the existing BM25 degradation disclosure.

This change covers AI-provider selection and retrieval degradation only. It does not create or alter OpenSearch indices, aliases, mappings, or stored embeddings.

## Context

The application currently constructs one `AsyncOpenAI` client from `OPENROUTER_API_KEY` and `OPENROUTER_BASE_URL`, then shares that client between `OpenAILLMGateway` and `OpenAIEmbeddingGateway`. The configured OpenRouter account currently returns HTTP 402.

The external environment file also contains `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. Cloudflare Workers AI exposes OpenAI-compatible chat-completion and embedding endpoints, so the existing gateway interfaces can be retained.

The current OpenSearch mail vectors have 4,096 dimensions. The selected Cloudflare embedding model may return a different vector dimension. This must degrade to BM25 rather than make the chat request fail.

## Chosen Approach

Extend configuration resolution and reuse the existing OpenAI-compatible gateways.

Alternatives considered:

1. Build Cloudflare-specific HTTP gateways. This duplicates request, response, error-handling, and test code without providing a capability needed by this change.
2. Require an explicit `AI_PROVIDER` variable. This is unambiguous but adds deployment configuration even though the presence of a complete Cloudflare credential pair already expresses the intended provider.
3. Reuse the OpenAI-compatible gateways and resolve the provider from credentials. This is the selected approach because it is the smallest change, preserves current interfaces, and remains independently testable.

## Configuration and Provider Resolution

Add the following settings:

- `CLOUDFLARE_ACCOUNT_ID`, default empty
- `CLOUDFLARE_API_TOKEN`, stored as `SecretStr`, default empty
- `CLOUDFLARE_LLM_MODEL`, default `@cf/openai/gpt-oss-120b`
- `CLOUDFLARE_EMBEDDING_MODEL`, default `@cf/qwen/qwen3-embedding-0.6b`

Resolve an immutable AI-provider configuration containing `provider`, `api_key`, `base_url`, `llm_model`, and `embedding_model`.

Resolution rules are deterministic:

1. When both Cloudflare account ID and token are non-empty, select Cloudflare.
2. Build its base URL as `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1`.
3. Use the Cloudflare token as the OpenAI-compatible API key and use the two Cloudflare model settings.
4. When either Cloudflare credential is missing, ignore the incomplete Cloudflare configuration and retain the existing OpenRouter key, base URL, `LLM_MODEL`, and `EMBEDDING_MODEL` behavior.

The resolved provider configuration is the only source used to construct the shared `AsyncOpenAI` client and both gateways. Fast and Deep routing behavior does not change.

## Components and Boundaries

### Settings

`app/config/settings.py` owns environment parsing and provider resolution. The resolver is a pure operation over `Settings`, so precedence and endpoint construction can be tested without network access.

### Dependency Assembly

`app/api/dependencies.py` asks the settings object for the resolved provider configuration. It creates the existing `AsyncOpenAI` client with the resolved API key and base URL, then supplies the resolved model names to the existing LLM and embedding gateways.

No Cloudflare-specific behavior is added to graph nodes, API routes, or domain models.

### Retrieval Degradation

`RetrievalService` continues to run hybrid vector and BM25 retrieval when embeddings and the index mapping are compatible.

It switches to BM25-only retrieval when either condition occurs:

- the embedding API call fails; or
- the vector-search request fails, including an OpenSearch vector-dimension or mapping incompatibility.

In the second case, the already available BM25 request is used as the fallback. A vector failure must not hide a BM25 failure: if BM25 also fails, the existing index-unavailable error behavior remains in effect.

Every BM25-only result includes this exact disclosure once:

`임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다.`

The fallback does not weaken access control. BM25, vector, Wiki, parent expansion, and all other retrieval queries continue to apply the request body's exact `user_id` through the existing owner filters.

## Data Flow

At startup:

1. Load settings from the configured environment source.
2. Resolve Cloudflare or OpenRouter using the credential precedence rules.
3. Construct one OpenAI-compatible asynchronous client.
4. Construct the LLM and embedding gateways with their resolved models.

For a RAG request:

1. The API creates policy context from the request-body `user_id`.
2. Fast or Deep processing creates search tasks without changing provider selection.
3. Retrieval requests a query embedding and prepares the owner-filtered BM25 query.
4. If embedding generation fails, retrieval runs BM25 only.
5. If embedding succeeds, retrieval runs vector and BM25 searches; a vector-search incompatibility causes a BM25-only result.
6. The answer carries the exact fallback disclosure when BM25-only mode was used.

## Error Handling and Observability

- Incomplete Cloudflare credentials select the existing OpenRouter configuration instead of producing a partially configured Cloudflare client.
- Chat-completion failures retain the current application error mapping and do not silently change providers during a request.
- Embedding and vector-search incompatibility produce a traceable BM25-only result with `mode="bm25"`, the existing embedding-unavailable marker, and the underlying error class where available.
- BM25 or general OpenSearch failures retain the existing retryable index-unavailable behavior.
- No API key or token value may appear in logs, traces, exceptions returned by the API, diagnostic UI payloads, or object representations introduced by this change.

## Testing

Automated tests will verify:

- complete Cloudflare credentials take precedence over complete OpenRouter credentials;
- the Cloudflare endpoint is built exactly from the account ID;
- Cloudflare LLM and embedding model defaults are selected;
- a missing Cloudflare account ID or token falls back to the existing OpenRouter settings;
- dependency assembly passes only the resolved endpoint, models, and secret value to the existing gateways;
- secret-bearing settings and resolved configurations do not expose token values in their representations;
- embedding API failure uses owner-filtered BM25 retrieval and includes the exact disclosure once;
- vector-search dimension or mapping failure uses the successful BM25 result and includes the exact disclosure once;
- simultaneous vector and BM25 failure is not misreported as a successful fallback;
- successful compatible vector and BM25 searches retain hybrid retrieval;
- existing Fast/Deep separation and exact request-body `user_id` owner-filter tests continue to pass.

After unit and integration tests, a local smoke test will start the API with the external environment file and verify that the request reaches Cloudflare without printing credentials. A successful Cloudflare call and a BM25-only response are both acceptable outcomes for the smoke test, because the existing 4,096-dimensional index may be incompatible with the new embedding model.

## Out of Scope and Follow-up

The existing OpenSearch deployment lacks the configured parent and Wiki aliases, and its stored vector dimension may not match the Cloudflare embedding model. This change will not mutate that deployment. After Cloudflare connectivity is verified, index aliases, mappings, and re-embedding can be designed and performed as a separate migration with explicit approval.

The external `.env` file and the existing project virtual environment will not be edited by this change.

## Acceptance Criteria

- With both Cloudflare credentials present, all LLM and embedding calls use the Cloudflare OpenAI-compatible endpoint and configured Cloudflare models.
- With an incomplete Cloudflare pair, the current OpenRouter behavior remains available.
- An embedding call failure or vector-search incompatibility returns BM25-only evidence when BM25 succeeds.
- Every BM25-only answer includes the required Korean disclosure exactly once.
- Retrieval continues to isolate every query by the request-body `user_id`.
- No secrets are exposed and no OpenSearch data or aliases are mutated.

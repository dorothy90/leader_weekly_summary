# OpenRouter Gemma Timeout Design

## Goal

Run both chat generation and query embeddings through the funded OpenRouter account while allowing the free Gemma model enough time to answer.

## Provider configuration

- LLM endpoint: `https://openrouter.ai/api/v1`
- LLM model: `google/gemma-4-26b-a4b-it:free`
- Embedding endpoint: `https://openrouter.ai/api/v1`
- Embedding model: `qwen/qwen3-embedding-8b`
- Credential: `OPENROUTER_API_KEY`; never log or return it.

## Timeout behavior

- Remove the 20-second workflow-wide Fast and Deep deadlines.
- Apply a 150-second timeout to each OpenRouter HTTP request.
- Preserve cancellation so a disconnected/cancelled API request stops its in-flight model request.
- Report provider failures as availability errors and actual 150-second expirations as `LLM_TIMEOUT`.

## Empty retrieval behavior

When retrieval returns no evidence, return the existing limited/no-evidence response without invoking LLM grading or query rewriting. Preserve the owner `user_id` filter and BM25 fallback disclosure.

## Verification

- Unit-test OpenRouter LLM and embedding client configuration.
- Unit-test that a workflow can exceed the former 20-second total duration without being cancelled.
- Unit-test that no-evidence retrieval does not invoke rewrite/generation.
- Run the complete `tests/mail_rag` suite and one live OpenRouter structured planning request.

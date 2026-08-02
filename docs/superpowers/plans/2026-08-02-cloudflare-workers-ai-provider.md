# Cloudflare Workers AI Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route chat completion and query embedding through Cloudflare Workers AI when its complete credential pair is configured, while degrading incompatible vector searches to owner-filtered BM25 with the required disclosure.

**Architecture:** Resolve one immutable OpenAI-compatible provider configuration from `Settings`, then use it at the existing dependency-composition boundary to build the shared client and both gateways. Keep Fast/Deep graphs and API contracts unchanged. Extend retrieval orchestration so vector and BM25 searches remain concurrent, but a vector-only failure uses the successful BM25 result.

**Tech Stack:** Python 3.11+, Pydantic Settings 2.11, OpenAI Python SDK 1.58+, asyncio, OpenSearch, pytest 8

## Global Constraints

- Cloudflare is selected only when both `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are non-empty.
- The Cloudflare base URL is exactly `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1`.
- The default Cloudflare LLM is `@cf/openai/gpt-oss-120b`.
- The default Cloudflare embedding model is `@cf/qwen/qwen3-embedding-0.6b`.
- An incomplete Cloudflare credential pair retains the existing OpenRouter configuration and models.
- The exact BM25 disclosure is `임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다.`
- Every retrieval query retains the exact request-body `user_id` owner filter.
- Never expose API keys or tokens in logs, traces, API responses, diagnostics, or object representations.
- Do not edit the external `.env`, the existing virtual environment, OpenSearch indices, aliases, mappings, or stored embeddings.

---

## File Structure

- Modify `app/config/settings.py`: define the immutable resolved-provider value and deterministic Cloudflare/OpenRouter selection.
- Create `tests/mail_rag/test_settings.py`: verify precedence, endpoint and model selection, incomplete-pair fallback, and secret-safe representations.
- Modify `app/api/dependencies.py`: construct the existing client and gateways from the resolved provider through a focused helper.
- Create `tests/mail_rag/test_ai_dependencies.py`: verify the dependency boundary receives the resolved endpoint, token, and models without network access.
- Modify `app/retrieval/service.py`: distinguish vector-search failure from BM25 failure while retaining concurrent hybrid retrieval.
- Modify `tests/mail_rag/test_retrieval_service.py`: verify dimension/mapping fallback, owner filtering, disclosure, hybrid success, and dual-search failure.

### Task 1: Resolve the AI Provider Safely

**Files:**
- Create: `tests/mail_rag/test_settings.py`
- Modify: `app/config/settings.py`

**Interfaces:**
- Consumes: environment-backed `Settings` fields already used by dependency assembly.
- Produces: `AIProviderConfig(provider: Literal["cloudflare", "openrouter"], api_key: SecretStr, base_url: str, llm_model: str, embedding_model: str)` and `Settings.resolve_ai_provider() -> AIProviderConfig`.

- [ ] **Step 1: Write failing provider-resolution tests**

```python
from pydantic import SecretStr

from app.config.settings import Settings


def test_complete_cloudflare_credentials_take_precedence():
    settings = Settings(
        cloudflare_account_id="account-123",
        cloudflare_api_token=SecretStr("cf-secret-token"),
        openrouter_api_key=SecretStr("openrouter-secret-token"),
        openrouter_base_url="https://openrouter.example/v1",
        llm_model="openrouter-llm",
        embedding_model="openrouter-embedding",
    )

    provider = settings.resolve_ai_provider()

    assert provider.provider == "cloudflare"
    assert provider.base_url == (
        "https://api.cloudflare.com/client/v4/accounts/account-123/ai/v1"
    )
    assert provider.api_key.get_secret_value() == "cf-secret-token"
    assert provider.llm_model == "@cf/openai/gpt-oss-120b"
    assert provider.embedding_model == "@cf/qwen/qwen3-embedding-0.6b"
    assert "cf-secret-token" not in repr(settings)
    assert "cf-secret-token" not in repr(provider)


def test_incomplete_cloudflare_pair_uses_existing_openrouter_configuration():
    settings = Settings(
        cloudflare_account_id="account-123",
        cloudflare_api_token=SecretStr(""),
        openrouter_api_key=SecretStr("openrouter-secret-token"),
        openrouter_base_url="https://openrouter.example/v1",
        llm_model="openrouter-llm",
        embedding_model="openrouter-embedding",
    )

    provider = settings.resolve_ai_provider()

    assert provider.provider == "openrouter"
    assert provider.base_url == "https://openrouter.example/v1"
    assert provider.api_key.get_secret_value() == "openrouter-secret-token"
    assert provider.llm_model == "openrouter-llm"
    assert provider.embedding_model == "openrouter-embedding"
    assert "openrouter-secret-token" not in repr(provider)
```

- [ ] **Step 2: Run the tests and verify the resolver is missing**

Run: `python -m pytest tests/mail_rag/test_settings.py -q`

Expected: FAIL because the Cloudflare fields or `resolve_ai_provider` do not exist.

- [ ] **Step 3: Add the immutable provider configuration and resolver**

Add imports and the value object to `app/config/settings.py`:

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AIProviderConfig:
    provider: Literal["cloudflare", "openrouter"]
    api_key: SecretStr
    base_url: str
    llm_model: str
    embedding_model: str
```

Add these fields and method to `Settings`:

```python
    cloudflare_account_id: str = ""
    cloudflare_api_token: SecretStr = SecretStr("")
    cloudflare_llm_model: str = "@cf/openai/gpt-oss-120b"
    cloudflare_embedding_model: str = "@cf/qwen/qwen3-embedding-0.6b"

    def resolve_ai_provider(self) -> AIProviderConfig:
        account_id = self.cloudflare_account_id.strip()
        token = self.cloudflare_api_token.get_secret_value().strip()
        if account_id and token:
            return AIProviderConfig(
                provider="cloudflare",
                api_key=SecretStr(token),
                base_url=(
                    "https://api.cloudflare.com/client/v4/accounts/"
                    f"{account_id}/ai/v1"
                ),
                llm_model=self.cloudflare_llm_model,
                embedding_model=self.cloudflare_embedding_model,
            )
        return AIProviderConfig(
            provider="openrouter",
            api_key=self.openrouter_api_key,
            base_url=self.openrouter_base_url,
            llm_model=self.llm_model,
            embedding_model=self.embedding_model,
        )
```

- [ ] **Step 4: Run focused and existing configuration tests**

Run: `python -m pytest tests/mail_rag/test_settings.py tests/mail_rag/test_integration_fixes.py -q`

Expected: all tests PASS and neither secret appears in pytest output.

- [ ] **Step 5: Commit the provider resolver**

```bash
git add app/config/settings.py tests/mail_rag/test_settings.py
git commit -m "feat(rag): resolve Cloudflare AI provider"
```

### Task 2: Assemble Existing Gateways from the Resolved Provider

**Files:**
- Create: `tests/mail_rag/test_ai_dependencies.py`
- Modify: `app/api/dependencies.py`

**Interfaces:**
- Consumes: `Settings.resolve_ai_provider() -> AIProviderConfig` from Task 1.
- Produces: `build_ai_gateways(settings: Settings) -> tuple[OpenAILLMGateway, OpenAIEmbeddingGateway]`; `build_container` consumes that tuple.

- [ ] **Step 1: Write a failing isolated dependency-assembly test**

```python
from pydantic import SecretStr

from app.api.dependencies import build_ai_gateways
from app.config.settings import Settings


def test_ai_gateways_use_resolved_cloudflare_client_and_models(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(
        cloudflare_account_id="account-123",
        cloudflare_api_token=SecretStr("cf-secret-token"),
        openrouter_api_key=SecretStr("unused-openrouter-token"),
    )

    llm, embeddings = build_ai_gateways(settings)

    assert captured == {
        "api_key": "cf-secret-token",
        "base_url": (
            "https://api.cloudflare.com/client/v4/accounts/account-123/ai/v1"
        ),
    }
    assert llm.client is embeddings.client
    assert llm.model == "@cf/openai/gpt-oss-120b"
    assert embeddings.model == "@cf/qwen/qwen3-embedding-0.6b"
    assert "cf-secret-token" not in repr(settings.resolve_ai_provider())
```

- [ ] **Step 2: Run the test and verify the helper is missing**

Run: `python -m pytest tests/mail_rag/test_ai_dependencies.py -q`

Expected: FAIL with an import error for `build_ai_gateways`.

- [ ] **Step 3: Extract AI gateway construction and use it in the container**

Add this focused helper to `app/api/dependencies.py`:

```python
def build_ai_gateways(settings):
    from openai import AsyncOpenAI

    from app.llm.gateway import OpenAILLMGateway
    from app.retrieval.embedding import OpenAIEmbeddingGateway

    provider = settings.resolve_ai_provider()
    ai = AsyncOpenAI(
        api_key=provider.api_key.get_secret_value(),
        base_url=provider.base_url or None,
    )
    return (
        OpenAILLMGateway(ai, provider.llm_model),
        OpenAIEmbeddingGateway(ai, provider.embedding_model),
    )
```

Replace direct client/model assembly inside `build_container` with:

```python
    llm, embeddings = build_ai_gateways(current)
```

Remove the now-unused local imports of `AsyncOpenAI`, `OpenAILLMGateway`, and `OpenAIEmbeddingGateway` from `build_container`.

- [ ] **Step 4: Run dependency and provider tests**

Run: `python -m pytest tests/mail_rag/test_ai_dependencies.py tests/mail_rag/test_settings.py tests/mail_rag/test_integration_fixes.py -q`

Expected: all tests PASS; no network request is made.

- [ ] **Step 5: Commit dependency assembly**

```bash
git add app/api/dependencies.py tests/mail_rag/test_ai_dependencies.py
git commit -m "feat(rag): use resolved AI provider"
```

### Task 3: Degrade Vector-Search Incompatibility to BM25

**Files:**
- Modify: `tests/mail_rag/test_retrieval_service.py`
- Modify: `app/retrieval/service.py`

**Interfaces:**
- Consumes: `OpenSearchGateway.search(index: str, body: dict) -> dict` and the existing `BM25_FALLBACK_DISCLOSURE` constant.
- Produces: unchanged `RetrievalService.search(...) -> RetrievalResult`, with `mode="bm25"` when embedding generation or vector search fails while BM25 succeeds.

- [ ] **Step 1: Write failing vector-only and dual-failure tests**

Add a backend fake and tests to `tests/mail_rag/test_retrieval_service.py`:

```python
class VectorMismatchSearch(FakeSearch):
    async def search(self, index, body):
        if "knn" in str(body):
            self.calls.append((index, deepcopy(body)))
            raise ValueError("vector dimension mismatch with secret-vector-detail")
        return await super().search(index, body)


def test_vector_dimension_failure_uses_owner_scoped_bm25_and_disclosure():
    backend = VectorMismatchSearch()
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(SearchTask(query="수율"), PolicyContext.from_user_id("kim"))
    )

    assert result.mode == "bm25"
    assert result.embedding_error == "EMBEDDING_UNAVAILABLE"
    assert result.embedding_error_class == "ValueError"
    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]
    assert "secret-vector-detail" not in result.model_dump_json()
    assert all(
        _owner_filter(body) == {"term": {"user_id": "kim"}}
        for _, body in backend.calls
    )
    assert any("knn" in str(body) for _, body in backend.calls)
    assert any('"match"' in str(body).replace("'", '"') for _, body in backend.calls)


def test_vector_and_bm25_failure_remains_retryable_index_unavailable():
    service = RetrievalService(BrokenSearch(), FakeEmbedding(), child_index="mail")

    with pytest.raises(AppError) as error:
        asyncio.run(
            service.search(SearchTask(query="q"), PolicyContext.from_user_id("kim"))
        )

    assert error.value.code == ErrorCode.INDEX_UNAVAILABLE
    assert error.value.retryable is True
```

- [ ] **Step 2: Run the focused tests and verify vector mismatch currently fails**

Run: `python -m pytest tests/mail_rag/test_retrieval_service.py -q`

Expected: the new dimension-failure test FAILS with retryable `INDEX_UNAVAILABLE`; existing hybrid and embedding-failure tests still pass.

- [ ] **Step 3: Classify concurrent search results independently**

Replace the successful-embedding branch in `RetrievalService._search` with:

```python
        else:
            vector_body = self._vector_body(task, policy, vector)
            vector_result, bm25_result = await asyncio.gather(
                self.backend.search(index_name, vector_body),
                self.backend.search(index_name, bm25_body),
                return_exceptions=True,
            )
            if isinstance(bm25_result, Exception):
                raise bm25_result
            if isinstance(vector_result, Exception):
                rankings = [self._rank(bm25_result)]
                mode = "bm25"
                embedding_error = "EMBEDDING_UNAVAILABLE"
                embedding_error_class = type(vector_result).__name__
                disclosures = [BM25_FALLBACK_DISCLOSURE]
            else:
                rankings = [self._rank(vector_result), self._rank(bm25_result)]
                mode = "hybrid"
```

This preserves parallel requests, requires BM25 success for fallback, avoids returning raw error text, and leaves task cancellation outside the ordinary `Exception` fallback path.

- [ ] **Step 4: Run retrieval and workflow regression tests**

Run: `python -m pytest tests/mail_rag/test_retrieval_service.py tests/mail_rag/test_fast_rag.py tests/mail_rag/test_deep_research.py tests/mail_rag/test_chat_api.py -q`

Expected: all tests PASS, including existing hybrid ordering, exact owner filters, and disclosure de-duplication.

- [ ] **Step 5: Commit vector incompatibility fallback**

```bash
git add app/retrieval/service.py tests/mail_rag/test_retrieval_service.py
git commit -m "fix(rag): fall back when vector search fails"
```

### Task 4: Full Verification and Cloudflare Smoke Test

**Files:**
- Verify only; no repository file changes expected.

**Interfaces:**
- Consumes: completed provider resolution, dependency assembly, and retrieval fallback.
- Produces: test evidence and a local runtime connection check without environment or OpenSearch mutation.

- [ ] **Step 1: Run the complete backend suite**

Run: `python -m pytest tests/mail_rag -q`

Expected: all tests PASS with no failed, errored, or skipped tests introduced by this change.

- [ ] **Step 2: Check formatting errors and secret literals in the diff**

Run: `git diff --check HEAD~3..HEAD && ! git diff HEAD~3..HEAD | rg 'CLOUDFLARE_API_TOKEN\s*='`

Expected: exit status 0 and no output. Test-only synthetic secret strings remain fixtures; no external credential value is copied into repository files.

- [ ] **Step 3: Start the API with the external environment and isolated dependency overlay**

Run from the repository root:

```bash
PYTHONPATH=/tmp/weekly-mail-rag-runtime-20260802 \
/Users/daehwankim/Documents/weekly_mail_agent/.venv/bin/python -c '
import uvicorn
from app.config.settings import Settings
from app.api.dependencies import build_container
from app.api.main import create_app
settings = Settings(_env_file="/Users/daehwankim/Documents/weekly_mail_agent/.env")
uvicorn.run(create_app(build_container(settings)), host="127.0.0.1", port=8000)
'
```

Expected: Uvicorn listens on `127.0.0.1:8000`; startup output contains no credential values. Keep this process only for the following smoke requests.

- [ ] **Step 4: Verify liveness and one Fast chat request**

Run in a second shell:

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"kim","message":"최근 수율 이슈를 요약해줘","response_mode":"fast"}'
```

Expected: `/health` returns `{"status":"ok"}`. The chat request reaches Cloudflare rather than returning the previous OpenRouter HTTP 402. It may return a normal answer, the exact BM25 disclosure, or a separate existing-index/alias error; no response may expose a credential.

- [ ] **Step 5: Stop only the temporary backend and record final status**

Send Ctrl-C to the API process started in Step 3. Do not stop the Vite RAG UI on port 5180 or the unrelated Route Master process on port 5173.

Run: `git status --short && git log -4 --oneline`

Expected: only the pre-existing untracked `docs/deep_agent_mail_chatbot_review.md` remains; the three implementation commits and this plan commit are visible.

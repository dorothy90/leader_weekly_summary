# Manus Lite LLM Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route structured agent analysis through Manus `manus-1.6-lite`, keep embeddings on OpenRouter, and provide an output-free notebook that independently verifies both live providers.

**Architecture:** AI configuration moves to `app/config/ai.py`. A provider factory selects a new asynchronous `ManusLLMGateway` or the existing OpenAI-compatible gateway while embeddings always use OpenRouter. The Manus gateway submits one task, polls its lifecycle, validates a strict structured-output envelope with Pydantic, and returns only safe diagnostics; the notebook invokes these same application components.

**Tech Stack:** Python 3.13, Pydantic 2, pydantic-settings, HTTPX, OpenAI Python SDK, pytest, pytest-asyncio, Jupyter nbformat 4 JSON.

## Global Constraints

- Default `LLM_PROVIDER` is `manus` and default `MANUS_AGENT_PROFILE` is exactly `manus-1.6-lite`.
- Embeddings remain on OpenRouter model `qwen/qwen3-embedding-8b`.
- Never log, print, commit, or persist API keys, task IDs, task URLs, full provider responses, or full prompts.
- Do not add automatic provider fallback, keyword routing, or hard-coded intent decisions.
- A Manus analysis request creates at most one provider task; application-level retries are disabled for Manus.
- Preserve current working-tree edits in `MultiSource_Production_Environment_Test.ipynb`; patch the current file and never restore it from HEAD.
- Do not stage `.env`, `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md`, `evals/datasets/agent_intent_paraphrases.json`, `scripts/evaluate_agent_intent.py`, or `tests/mail_rag/test_agent_intent_evaluation.py`.

---

### Task 1: Separate AI provider configuration

**Files:**
- Create: `app/config/ai.py`
- Modify: `app/config/settings.py`
- Modify: `tests/mail_rag/test_settings.py`

**Interfaces:**
- Produces: `AISettings`, `LLMEndpointConfig`, `EmbeddingEndpointConfig`.
- Produces: `Settings.resolve_llm_endpoint() -> LLMEndpointConfig` and `Settings.resolve_embedding_endpoint() -> EmbeddingEndpointConfig` through inheritance from `AISettings`.
- Consumes: the existing root `.env` file and uppercase environment names through Pydantic Settings.

- [ ] **Step 1: Replace OpenRouter-only setting assertions with provider-separated assertions**

Add tests equivalent to:

```python
from pydantic import SecretStr

from app.config.settings import Settings


def test_manus_llm_and_openrouter_embedding_defaults_are_independent():
    settings = Settings(
        manus_api_key=SecretStr("manus-secret"),
        openrouter_api_key=SecretStr("openrouter-secret"),
    )

    llm = settings.resolve_llm_endpoint()
    embedding = settings.resolve_embedding_endpoint()

    assert llm.provider == "manus"
    assert llm.base_url == "https://api.manus.ai"
    assert llm.model == "manus-1.6-lite"
    assert llm.api_key.get_secret_value() == "manus-secret"
    assert llm.request_timeout_seconds == 30
    assert llm.completion_timeout_seconds == 150
    assert llm.poll_interval_seconds == 2
    assert embedding.provider == "openrouter"
    assert embedding.base_url == "https://openrouter.ai/api/v1"
    assert embedding.model == "qwen/qwen3-embedding-8b"
    assert embedding.api_key.get_secret_value() == "openrouter-secret"
    assert "manus-secret" not in repr(llm)
    assert "openrouter-secret" not in repr(embedding)


def test_openai_compatible_llm_can_be_selected_without_changing_embedding():
    settings = Settings(
        llm_provider="openai_compatible",
        openai_compatible_llm_api_key=SecretStr("llm-secret"),
        openai_compatible_llm_base_url="https://llm.example/v1",
        openai_compatible_llm_model="future-model",
        openrouter_api_key=SecretStr("embed-secret"),
    )

    llm = settings.resolve_llm_endpoint()
    embedding = settings.resolve_embedding_endpoint()

    assert (llm.provider, llm.base_url, llm.model) == (
        "openai_compatible",
        "https://llm.example/v1",
        "future-model",
    )
    assert embedding.provider == "openrouter"
    assert embedding.model == "qwen/qwen3-embedding-8b"
```

- [ ] **Step 2: Run the settings tests and confirm RED**

Run:

```bash
python -m pytest tests/mail_rag/test_settings.py -q
```

Expected: failure because `AISettings` and Manus/OpenAI-compatible fields do not exist.

- [ ] **Step 3: Create the dedicated AI settings module**

Create `app/config/ai.py` with these public definitions and values:

```python
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["manus", "openai_compatible"]
ManusProfile = Literal["manus-1.6", "manus-1.6-lite", "manus-1.6-max"]


@dataclass(frozen=True)
class LLMEndpointConfig:
    provider: LLMProvider
    api_key: SecretStr
    base_url: str
    model: str
    request_timeout_seconds: float
    completion_timeout_seconds: float
    poll_interval_seconds: float


@dataclass(frozen=True)
class EmbeddingEndpointConfig:
    provider: Literal["openrouter"]
    api_key: SecretStr
    base_url: str
    model: str
    timeout_seconds: float


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: LLMProvider = "manus"
    manus_api_key: SecretStr = SecretStr("")
    manus_base_url: str = "https://api.manus.ai"
    manus_agent_profile: ManusProfile = "manus-1.6-lite"
    manus_request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    manus_task_timeout_seconds: int = Field(default=150, ge=1, le=600)
    manus_poll_interval_seconds: float = Field(default=2, ge=0.1, le=30)

    openai_compatible_llm_api_key: SecretStr = SecretStr("")
    openai_compatible_llm_base_url: str = ""
    openai_compatible_llm_model: str = ""
    openai_compatible_llm_timeout_seconds: int = Field(
        default=150, ge=1, le=600
    )

    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_embedding_model: str = "qwen/qwen3-embedding-8b"
    openrouter_request_timeout_seconds: int = Field(
        default=150, ge=1, le=600
    )

    def resolve_llm_endpoint(self) -> LLMEndpointConfig:
        if self.llm_provider == "manus":
            return LLMEndpointConfig(
                provider="manus",
                api_key=self.manus_api_key,
                base_url=self.manus_base_url,
                model=self.manus_agent_profile,
                request_timeout_seconds=float(
                    self.manus_request_timeout_seconds
                ),
                completion_timeout_seconds=float(
                    self.manus_task_timeout_seconds
                ),
                poll_interval_seconds=float(
                    self.manus_poll_interval_seconds
                ),
            )
        return LLMEndpointConfig(
            provider="openai_compatible",
            api_key=self.openai_compatible_llm_api_key,
            base_url=self.openai_compatible_llm_base_url,
            model=self.openai_compatible_llm_model,
            request_timeout_seconds=float(
                self.openai_compatible_llm_timeout_seconds
            ),
            completion_timeout_seconds=float(
                self.openai_compatible_llm_timeout_seconds
            ),
            poll_interval_seconds=0.0,
        )

    def resolve_embedding_endpoint(self) -> EmbeddingEndpointConfig:
        return EmbeddingEndpointConfig(
            provider="openrouter",
            api_key=self.openrouter_api_key,
            base_url=self.openrouter_base_url,
            model=self.openrouter_embedding_model,
            timeout_seconds=float(self.openrouter_request_timeout_seconds),
        )
```

Modify `app/config/settings.py` so `Settings` inherits `AISettings`, remove the
duplicated AI fields and endpoint dataclass, and keep all non-AI fields intact:

```python
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from app.config.ai import AISettings


class Settings(AISettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_user: str = ""
    opensearch_password: SecretStr = SecretStr("")
    opensearch_use_ssl: bool = False
    opensearch_verify_certs: bool = True
    mail_child_index: str = "weekly_mail"
    mail_parent_index: str = "weekly_mail_parent_read"
    wiki_index: str = "wiki_summaries_v2"
    multi_source_demo: bool = False
    domain_knowledge_index: str = "syld_gpt"
    mail_index_alias: str = "ews-mail-active"
    calendar_index_alias: str = "ews-calendar-active"
    default_user_timezone: str = "Asia/Seoul"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "weekly_mail_agent"
    mail_content_root: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
```

- [ ] **Step 4: Run settings tests and confirm GREEN**

Run:

```bash
python -m pytest tests/mail_rag/test_settings.py -q
```

Expected: all settings tests pass.

- [ ] **Step 5: Commit the configuration boundary**

```bash
git add app/config/ai.py app/config/settings.py tests/mail_rag/test_settings.py
git commit -m "refactor(config): separate AI providers"
```

---

### Task 2: Implement the asynchronous Manus gateway

**Files:**
- Create: `app/llm/manus.py`
- Create: `tests/mail_rag/test_manus_gateway.py`

**Interfaces:**
- Consumes: `httpx.AsyncClient`, a Manus API key, base URL, requested profile, request timeout, task timeout, and poll interval.
- Produces: `ManusLLMGateway.complete_model(system: str, user: str, schema: type[T]) -> T`, matching `LLMGateway`.
- Produces: `ManusLLMGateway.complete_model_with_diagnostics(system: str, user: str, schema: type[T]) -> ManusCompletion[T]` for notebook verification.
- Produces: secret-safe `ManusTaskDiagnostics` without task identifiers or prompt content.

- [ ] **Step 1: Write gateway contract and lifecycle tests**

Use `httpx.MockTransport` and real Pydantic models. The primary success test must
assert the outgoing task profile and strict structured envelope, return one
transient 404, then `running`, then `stopped`, and finally a successful
`structured_output_result`:

```python
from datetime import date
import json

import httpx
import pytest

from app.domain.agentic import IntentDecision
from app.llm.manus import ManusLLMGateway


@pytest.mark.asyncio
async def test_manus_gateway_completes_structured_model_after_transient_404():
    detail_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal detail_calls
        if request.url.path == "/v2/task.create":
            body = json.loads(request.content)
            assert body["agent_profile"] == "manus-1.6-lite"
            assert body["interactive_mode"] is False
            assert body["hide_in_task_list"] is True
            envelope = body["structured_output_schema"]
            assert envelope["required"] == ["payload_json"]
            assert envelope["additionalProperties"] is False
            return httpx.Response(200, json={"ok": True, "task_id": "A" * 22})
        if request.url.path == "/v2/task.detail":
            detail_calls += 1
            if detail_calls == 1:
                return httpx.Response(
                    404,
                    json={"ok": False, "error": {"code": "not_found"}},
                )
            status = "running" if detail_calls == 2 else "stopped"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "task": {
                        "status": status,
                        "agent_profile": "manus-1.6",
                        "credit_usage": 0,
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {
                        "type": "structured_output_result",
                        "structured_output_result": {
                            "success": True,
                            "value": {
                                "payload_json": json.dumps(
                                    {
                                        "intent": "일정 확인",
                                        "source_requests": [
                                            {"source": "calendar", "query": "이번 주 일정"}
                                        ],
                                        "entities": {},
                                        "time_scope": "current_week",
                                        "exact_date": None,
                                        "event_reference": "none",
                                        "calendar_detail_required": False,
                                        "information_needs": [],
                                    },
                                    ensure_ascii=False,
                                )
                            },
                            "error": None,
                        },
                    }
                ],
            },
        )

    client = httpx.AsyncClient(
        base_url="https://api.manus.ai",
        transport=httpx.MockTransport(handler),
    )
    gateway = ManusLLMGateway(
        client,
        profile="manus-1.6-lite",
        poll_interval_seconds=0,
        task_timeout_seconds=1,
    )

    completion = await gateway.complete_model_with_diagnostics(
        "system", "user", IntentDecision
    )

    assert completion.value.time_scope == "current_week"
    assert completion.diagnostics.requested_profile == "manus-1.6-lite"
    assert completion.diagnostics.actual_profile == "manus-1.6"
    assert completion.diagnostics.credit_usage == 0
```

Add focused tests for these independent outcomes. Each test supplies a complete
`MockTransport` sequence and uses `pytest.raises(ManusLLMError)`:

- `test_manus_gateway_rejects_unsuccessful_structured_output` returns create
  success, stopped detail, then `structured_output_result.success=false`; it
  asserts `captured.value.code == "structured_output_failed"`.
- `test_manus_gateway_fails_closed_on_waiting_status` returns create success
  then `task.status="waiting"`; it asserts
  `captured.value.code == "task_waiting"` and proves listMessages was not
  called.
- `test_manus_gateway_fails_closed_on_error_status` returns create success then
  `task.status="error"`; it asserts
  `captured.value.code == "task_error"`.
- `test_manus_gateway_times_out_repeated_not_found` returns create success and
  only 404 detail responses with `task_timeout_seconds=0.01` and
  `poll_interval_seconds=0`; it asserts
  `captured.value.code == "task_timeout"`.
- `test_manus_diagnostics_exclude_task_and_prompt_values` completes a task,
  serializes `completion.diagnostics` with `repr`, and asserts the task ID,
  `task_url`, API key, system prompt, and user prompt are absent.

- [ ] **Step 2: Run gateway tests and confirm RED**

Run:

```bash
python -m pytest tests/mail_rag/test_manus_gateway.py -q
```

Expected: import failure because `app.llm.manus` does not exist.

- [ ] **Step 3: Implement the gateway and strict JSON-string envelope**

Create these public types in `app/llm/manus.py`:

```python
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar

import asyncio
import httpx
from pydantic import BaseModel

from app.security.redaction import sanitize_text

T = TypeVar("T", bound=BaseModel)


class ManusLLMError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ManusTaskDiagnostics:
    requested_profile: str
    actual_profile: str | None
    status: str
    credit_usage: int | None
    duration_ms: int


@dataclass(frozen=True)
class ManusCompletion(Generic[T]):
    value: T
    diagnostics: ManusTaskDiagnostics
```

`ManusLLMGateway` implements all four `LLMGateway` methods. Structured calls
use this provider schema, which stays valid even when the Pydantic model has a
free-form dictionary such as `IntentDecision.entities`:

```python
def _structured_envelope(schema: type[BaseModel]) -> dict:
    compact_schema = schema.model_json_schema()
    return {
        "type": "object",
        "properties": {
            "payload_json": {
                "type": "string",
                "description": (
                    "A JSON object that validates against this application "
                    f"schema: {compact_schema}"
                ),
            }
        },
        "required": ["payload_json"],
        "additionalProperties": False,
    }
```

The create/poll implementation follows this exact state policy:

```python
payload = {
    "message": {"content": safe_prompt},
    "agent_profile": self.profile,
    "interactive_mode": False,
    "hide_in_task_list": True,
    "share_visibility": "private",
    "structured_output_schema": _structured_envelope(schema),
}
created = await self._request("POST", "/v2/task.create", json=payload)
task_id = created.get("task_id")
if not created.get("ok") or not isinstance(task_id, str):
    raise ManusLLMError("task_create_failed")

deadline = monotonic() + self.task_timeout_seconds
while monotonic() < deadline:
    status_code, detail = await self._request_allowing_status(
        "GET", "/v2/task.detail", params={"task_id": task_id}
    )
    if status_code == 404:
        await asyncio.sleep(self.poll_interval_seconds)
        continue
    status = (detail.get("task") or {}).get("status")
    if status == "running":
        await asyncio.sleep(self.poll_interval_seconds)
        continue
    if status == "waiting":
        raise ManusLLMError("task_waiting")
    if status == "error":
        raise ManusLLMError("task_error")
    if status == "stopped":
        break
    raise ManusLLMError("task_status_invalid")
else:
    raise ManusLLMError("task_timeout")
```

After `stopped`, poll `/v2/task.listMessages` within the same deadline. Select
the newest `structured_output_result`, require `success is True`, require a
string `value.payload_json`, and return:

```python
validated = schema.model_validate_json(payload_json)
return ManusCompletion(
    value=validated,
    diagnostics=ManusTaskDiagnostics(
        requested_profile=self.profile,
        actual_profile=task.get("agent_profile"),
        status="stopped",
        credit_usage=task.get("credit_usage"),
        duration_ms=int((monotonic() - started) * 1000),
    ),
)
```

All provider HTTP/JSON failures map to stable codes such as
`authentication_failed`, `rate_limited`, `provider_unavailable`,
`response_invalid`, and `structured_output_invalid`. Do not place raw provider
messages in raised exceptions.

- [ ] **Step 4: Run gateway tests and confirm GREEN**

Run:

```bash
python -m pytest tests/mail_rag/test_manus_gateway.py -q
```

Expected: all Manus gateway tests pass.

- [ ] **Step 5: Commit the gateway**

```bash
git add app/llm/manus.py tests/mail_rag/test_manus_gateway.py
git commit -m "feat(llm): add Manus task gateway"
```

---

### Task 3: Wire provider selection into application factories

**Files:**
- Modify: `app/api/dependencies.py`
- Modify: `app/llm/agentic.py`
- Modify: `scripts/run_multi_source_demo.py`
- Modify: `tests/mail_rag/test_ai_dependencies.py`
- Modify: `tests/mail_rag/test_multi_source_graph.py`
- Modify: `tests/mail_rag/test_multi_source_demo_api.py`
- Modify: `tests/mail_rag/test_integration_fixes.py`

**Interfaces:**
- Consumes: `Settings.resolve_llm_endpoint()` and `Settings.resolve_embedding_endpoint()`.
- Produces: `build_llm_gateway(settings)` and the existing `build_ai_gateways(settings)` tuple.
- Produces: `StructuredAgentModel(llm, *, timeout_seconds: float = 150, attempts: int = 2, now=None)` with one attempt for Manus and two for OpenAI-compatible providers.

- [ ] **Step 1: Write failing factory and retry-policy tests**

Update dependency tests to inject fake `httpx.AsyncClient` and `AsyncOpenAI`
clients, then assert:

```python
def test_ai_gateways_use_manus_for_llm_and_openrouter_for_embeddings(monkeypatch):
    settings = Settings(
        manus_api_key=SecretStr("manus-secret"),
        openrouter_api_key=SecretStr("openrouter-secret"),
    )

    llm, embeddings = build_ai_gateways(settings)

    assert isinstance(llm, ManusLLMGateway)
    assert llm.profile == "manus-1.6-lite"
    assert isinstance(embeddings, OpenAIEmbeddingGateway)
    assert embeddings.model == "qwen/qwen3-embedding-8b"


def test_openai_compatible_selection_builds_existing_llm_gateway(monkeypatch):
    settings = Settings(
        llm_provider="openai_compatible",
        openai_compatible_llm_api_key=SecretStr("llm-secret"),
        openai_compatible_llm_base_url="https://llm.example/v1",
        openai_compatible_llm_model="future-model",
        openrouter_api_key=SecretStr("embedding-secret"),
    )

    llm, embeddings = build_ai_gateways(settings)

    assert isinstance(llm, OpenAILLMGateway)
    assert llm.model == "future-model"
    assert embeddings.model == "qwen/qwen3-embedding-8b"
```

Add a model test proving `attempts=1` calls a failing gateway once and returns
`QueryAnalysis.unavailable()`, while the default still permits two calls.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
python -m pytest \
  tests/mail_rag/test_ai_dependencies.py \
  tests/mail_rag/test_multi_source_graph.py -q
```

Expected: failures because the factory is OpenRouter-only and
`StructuredAgentModel` has no `attempts` argument.

- [ ] **Step 3: Implement the provider factory and one-task Manus policy**

Add this factory split in `app/api/dependencies.py`:

```python
def build_llm_gateway(settings):
    from openai import AsyncOpenAI
    import httpx

    from app.llm.gateway import OpenAILLMGateway
    from app.llm.manus import ManusLLMGateway

    endpoint = settings.resolve_llm_endpoint()
    api_key = endpoint.api_key.get_secret_value().strip()
    if not api_key:
        required = (
            "MANUS_API_KEY"
            if endpoint.provider == "manus"
            else "OPENAI_COMPATIBLE_LLM_API_KEY"
        )
        raise RuntimeError(f"{required} is required")
    if endpoint.provider == "manus":
        client = httpx.AsyncClient(
            base_url=endpoint.base_url.rstrip("/"),
            headers={"x-manus-api-key": api_key},
            timeout=endpoint.request_timeout_seconds,
        )
        return ManusLLMGateway(
            client,
            profile=endpoint.model,
            poll_interval_seconds=endpoint.poll_interval_seconds,
            task_timeout_seconds=endpoint.completion_timeout_seconds,
        )
    if not endpoint.base_url.strip() or not endpoint.model.strip():
        raise RuntimeError("OpenAI-compatible LLM endpoint is incomplete")
    return OpenAILLMGateway(
        AsyncOpenAI(
            api_key=api_key,
            base_url=endpoint.base_url,
            timeout=endpoint.request_timeout_seconds,
        ),
        endpoint.model,
    )
```

`build_ai_gateways` calls `build_llm_gateway(settings)` and separately builds
only the embedding client from `resolve_embedding_endpoint()`. Production and
demo containers use `endpoint.completion_timeout_seconds`; they pass
`attempts=1` for Manus and `attempts=2` otherwise.

Modify the analyzer constructor and loop:

```python
class StructuredAgentModel:
    def __init__(self, llm, *, timeout_seconds=150, attempts=2, now=None):
        self.llm = llm
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.attempts = max(1, int(attempts))
        self.now = now

    async def analyze(self, question, memory, timezone_name):
        safe_memory = {
            "entities": dict(
                list((getattr(memory, "entities", {}) or {}).items())[-16:]
            ),
            "current_topic": getattr(memory, "current_topic", None),
        }
        user = json.dumps(
            {"question": question, "memory": safe_memory},
            ensure_ascii=False,
        )
        for _attempt in range(self.attempts):
            try:
                decision = await asyncio.wait_for(
                    self.llm.complete_model(
                        INTENT_SYSTEM_PROMPT,
                        user,
                        IntentDecision,
                    ),
                    timeout=self.timeout_seconds,
                )
                validated = IntentDecision.model_validate(decision)
                return QueryAnalysis.from_intent(
                    validated,
                    now=self.now,
                    timezone_name=timezone_name,
                )
            except Exception:
                continue
        return QueryAnalysis.unavailable()
```

Update the free-form demo key gate to inspect the selected endpoint and report
`MANUS_API_KEY` for the default provider. Offline deterministic scenarios remain
key-free.

- [ ] **Step 4: Run factory, analyzer, demo, and construction tests**

Run:

```bash
python -m pytest \
  tests/mail_rag/test_ai_dependencies.py \
  tests/mail_rag/test_multi_source_graph.py \
  tests/mail_rag/test_multi_source_demo_api.py \
  tests/mail_rag/test_integration_fixes.py -q
```

Expected: all focused tests pass without making network calls.

- [ ] **Step 5: Commit application wiring**

```bash
git add app/api/dependencies.py app/llm/agentic.py \
  scripts/run_multi_source_demo.py \
  tests/mail_rag/test_ai_dependencies.py \
  tests/mail_rag/test_multi_source_graph.py \
  tests/mail_rag/test_multi_source_demo_api.py \
  tests/mail_rag/test_integration_fixes.py
git commit -m "feat(rag): route analysis through Manus"
```

---

### Task 4: Update the production notebook for independent live checks

**Files:**
- Modify: `tests/mail_rag/test_production_environment_notebook.py`
- Modify: `MultiSource_Production_Environment_Test.ipynb`

**Interfaces:**
- Consumes: `Settings.from_env()`, `build_llm_gateway(settings)`, `build_ai_gateways(settings)`, and `IntentDecision`.
- Produces: separate live Manus and OpenRouter cells before the existing application readiness/chat cells.

- [ ] **Step 1: Extend the notebook contract test**

Require these source fragments in addition to the existing route-free checks:

```python
for required in (
    '"MANUS_API_KEY configured"',
    '"OPENROUTER_API_KEY configured"',
    'assert llm_endpoint.model == "manus-1.6-lite"',
    "build_llm_gateway(settings)",
    "complete_model_with_diagnostics",
    '"actual_profile"',
    '"credit_usage"',
    "embedding_gateway.embed",
    '"vector_dimension"',
):
    assert required in source

for forbidden in (
    "MANUS_API_KEY=",
    "OPENROUTER_API_KEY=",
    '"task_id"',
    '"task_url"',
):
    assert forbidden not in source
```

Retain the existing checks for empty outputs, safe endpoint rendering,
`build_container(settings)`, `/ready`, `/v1/chat`, and obsolete route fields.

- [ ] **Step 2: Run the notebook contract and confirm RED**

Run:

```bash
python -m pytest tests/mail_rag/test_production_environment_notebook.py -q
```

Expected: failure because the notebook has no Manus or independent embedding
live cells.

- [ ] **Step 3: Patch the current notebook without discarding its local edits**

The safe configuration cell resolves both endpoints and prints only safe
metadata:

```python
llm_endpoint = settings.resolve_llm_endpoint()
embedding_endpoint = settings.resolve_embedding_endpoint()
safe_settings = {
    "llm_provider": llm_endpoint.provider,
    "llm_model": llm_endpoint.model,
    "llm_base_url": safe_endpoint_description(llm_endpoint.base_url),
    "embedding_provider": embedding_endpoint.provider,
    "embedding_model": embedding_endpoint.model,
    "embedding_base_url": safe_endpoint_description(embedding_endpoint.base_url),
    "MANUS_API_KEY configured": bool(
        settings.manus_api_key.get_secret_value().strip()
    ),
    "OPENROUTER_API_KEY configured": bool(
        settings.openrouter_api_key.get_secret_value().strip()
    ),
}
pprint(safe_settings)
assert llm_endpoint.provider == "manus"
assert llm_endpoint.model == "manus-1.6-lite"
assert safe_settings["MANUS_API_KEY configured"]
assert safe_settings["OPENROUTER_API_KEY configured"]
```

Add a Manus live cell using the application gateway and safe diagnostics:

```python
from app.api.dependencies import build_ai_gateways, build_llm_gateway
from app.domain.agentic import IntentDecision
from app.llm.manus import ManusLLMGateway

llm_gateway = build_llm_gateway(settings)
assert isinstance(llm_gateway, ManusLLMGateway)
manus_completion = await llm_gateway.complete_model_with_diagnostics(
    "Return the requested structured intent without using external tools.",
    "이번 주 일정을 확인하고 싶습니다.",
    IntentDecision,
)
pprint(
    {
        "status": manus_completion.diagnostics.status,
        "requested_profile": manus_completion.diagnostics.requested_profile,
        "actual_profile": manus_completion.diagnostics.actual_profile,
        "credit_usage": manus_completion.diagnostics.credit_usage,
        "intent_valid": bool(manus_completion.value.intent),
    }
)
```

Add a separate OpenRouter embedding live cell:

```python
_unused_llm, embedding_gateway = build_ai_gateways(settings)
embedding = await embedding_gateway.embed(
    "weekly mail agent OpenRouter embedding connectivity test"
)
pprint(
    {
        "embedding_model": embedding_gateway.model,
        "vector_dimension": len(embedding),
        "numeric_vector": bool(embedding)
        and all(isinstance(value, (int, float)) for value in embedding),
    }
)
assert embedding
```

Keep every code cell at `execution_count: null` with `outputs: []`. Do not save
live output in the committed notebook.

- [ ] **Step 4: Run notebook contract and confirm GREEN**

Run:

```bash
python -m pytest tests/mail_rag/test_production_environment_notebook.py -q
```

Expected: all notebook contract tests pass.

- [ ] **Step 5: Commit notebook integration**

Stage only the notebook and its tracked contract test after reviewing the
working-tree diff for preserved local edits:

```bash
git add MultiSource_Production_Environment_Test.ipynb \
  tests/mail_rag/test_production_environment_notebook.py
git commit -m "test(rag): verify Manus and embeddings"
```

---

### Task 5: Document operation and run complete verification

**Files:**
- Modify: `docs/operations.md`

**Interfaces:**
- Documents: selected LLM provider variables, Manus Lite test profile,
  OpenRouter embedding variables, and future OpenAI-compatible switch.
- Verifies: no credentials or output artifacts are staged.

- [ ] **Step 1: Update operator configuration examples**

Document this environment contract without values:

```dotenv
LLM_PROVIDER=manus
MANUS_API_KEY=<secret>
MANUS_AGENT_PROFILE=manus-1.6-lite
OPENROUTER_API_KEY=<secret>
OPENROUTER_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
```

Document the future switch separately:

```dotenv
LLM_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_LLM_API_KEY=<secret>
OPENAI_COMPATIBLE_LLM_BASE_URL=https://provider.example/v1
OPENAI_COMPATIBLE_LLM_MODEL=provider-model
```

State that there is no automatic fallback and that notebook outputs must be
cleared before committing.

- [ ] **Step 2: Run focused and full regression tests**

Run:

```bash
python -m pytest \
  tests/mail_rag/test_settings.py \
  tests/mail_rag/test_manus_gateway.py \
  tests/mail_rag/test_ai_dependencies.py \
  tests/mail_rag/test_production_environment_notebook.py -q
python -m pytest tests/mail_rag -q
```

Expected: zero failures. If an untracked user test conflicts with the approved
provider contract, report it separately and do not overwrite that file.

- [ ] **Step 3: Verify notebook and secret hygiene**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path("MultiSource_Production_Environment_Test.ipynb")
notebook = json.loads(path.read_text(encoding="utf-8"))
assert all(
    cell.get("execution_count") is None and cell.get("outputs") == []
    for cell in notebook["cells"]
    if cell["cell_type"] == "code"
)
source = path.read_text(encoding="utf-8")
for forbidden in ("MANUS_API_KEY=", "OPENROUTER_API_KEY=", '"task_id"', '"task_url"'):
    assert forbidden not in source
print("notebook hygiene: ok")
PY
git diff --check
git status --short
```

Expected: `notebook hygiene: ok`, no whitespace errors, `.env` absent from
status, and only known user-owned untracked files remain.

- [ ] **Step 4: Run optional live provider cells manually**

Open `MultiSource_Production_Environment_Test.ipynb` with the project Python
kernel, run the safe configuration, Manus, and embedding cells in order, and
confirm:

- Manus status is `stopped`.
- Requested profile is `manus-1.6-lite`.
- Actual profile is reported without requiring it to equal the requested value.
- Credit usage is displayed as a number or `None` without exposing task IDs.
- OpenRouter returns a nonempty numeric embedding vector.

Clear all outputs after the manual run.

- [ ] **Step 5: Commit operations documentation**

```bash
git add docs/operations.md
git commit -m "docs(rag): document split AI providers"
```

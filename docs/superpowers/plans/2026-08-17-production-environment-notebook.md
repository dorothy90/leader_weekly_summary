# Production Environment Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Jupyter notebook that invokes the real production-configured FastAPI `/v1/chat` path against OpenRouter, OpenSearch, MongoDB, and configured Mail/Calendar aliases.

**Architecture:** The notebook changes its working directory to the repository root, loads `Settings.from_env()`, constructs the production `ServiceContainer` with `build_container(settings)`, wraps `create_app(container)` in `httpx.ASGITransport`, and sends route-free HTTP requests in-process. A repository test statically validates notebook syntax, required production calls, secret-safe structure, obsolete route-field checks, and empty committed outputs; live external dependency checks remain explicit notebook gates.

**Tech Stack:** Jupyter Notebook nbformat 4 JSON, Python 3, FastAPI, HTTPX ASGI transport, Pydantic Settings, pytest.

## Global Constraints

- Create exactly one runnable notebook at `MultiSource_Production_Environment_Test.ipynb`.
- Use `Settings.from_env`, `build_container`, `create_app`, `GET /ready`, and `POST /v1/chat`; do not reproduce agent or retrieval logic in notebook cells.
- Require `MULTI_SOURCE_DEMO=false` and a configured `OPENROUTER_API_KEY`; never fall back to demo data or a rule-based analyzer.
- Never print API keys, MongoDB credentials, OpenSearch credentials, raw prompts, or chain-of-thought.
- Send no `response_mode`, `mode`, or `routing` request field, and assert those obsolete fields are absent from the public response.
- Commit the notebook with `execution_count: null` and empty `outputs` for every code cell.
- Preserve all existing untracked user files.

---

### Task 1: Production Integration Notebook

**Files:**
- Create: `tests/mail_rag/test_production_environment_notebook.py`
- Create: `MultiSource_Production_Environment_Test.ipynb`

**Interfaces:**
- Consumes: `Settings.from_env() -> Settings`, `build_container(settings) -> ServiceContainer`, `create_app(container) -> FastAPI`.
- Produces: a route-free in-process `POST /v1/chat` integration notebook with configurable `USER_ID`, `QUESTION`, `FILTERS`, and `FOLLOW_UP` cells.

- [ ] **Step 1: Write the failing notebook contract test**

Create `tests/mail_rag/test_production_environment_notebook.py` with:

```python
import ast
import json
from pathlib import Path


NOTEBOOK = Path("MultiSource_Production_Environment_Test.ipynb")


def _notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cells(notebook: dict) -> list[dict]:
    return [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]


def test_production_notebook_is_valid_output_free_python():
    notebook = _notebook()
    assert notebook["nbformat"] == 4
    assert notebook["nbformat_minor"] >= 5
    assert notebook["metadata"]["kernelspec"]["language"] == "python"

    cells = _code_cells(notebook)
    assert cells
    for index, cell in enumerate(cells):
        source = "".join(cell["source"])
        compile(
            source,
            f"{NOTEBOOK}#cell-{index}",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )
        assert cell["execution_count"] is None
        assert cell["outputs"] == []


def test_production_notebook_uses_the_real_route_free_application_path():
    source = "\n".join(
        "".join(cell["source"]) for cell in _code_cells(_notebook())
    )
    for required in (
        "Settings.from_env()",
        "build_container(settings)",
        "create_app(container)",
        "httpx.ASGITransport",
        'client.get("/ready")',
        'client.post("/v1/chat"',
        '"conversation_id": conversation_id',
        '"OPENROUTER_API_KEY configured"',
    ):
        assert required in source

    assert '"response_mode":' not in source
    assert '"mode":' not in source
    assert '"routing":' not in source
    assert 'obsolete_fields = {"response_mode", "mode", "routing"}' in source
    assert "settings.multi_source_demo is False" in source
```

- [ ] **Step 2: Run the contract test and confirm RED**

Run:

```bash
python -m pytest tests/mail_rag/test_production_environment_notebook.py -q
```

Expected: failure because `MultiSource_Production_Environment_Test.ipynb` does not exist.

- [ ] **Step 3: Create the output-free notebook**

Create a valid nbformat 4.5 notebook with Python 3 kernelspec metadata and the
following cells in order.

Markdown cell 1 explains that the notebook uses production OpenRouter,
OpenSearch, MongoDB, and aliases and that a trusted test `USER_ID` is required.

Code cell 1 locates the repository root before importing application modules:

```python
import os
import sys
from pathlib import Path


def find_repository_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "app" / "api" / "main.py").is_file():
            return candidate
    raise RuntimeError("Repository root containing app/api/main.py was not found")


REPOSITORY_ROOT = find_repository_root(Path.cwd().resolve())
os.chdir(REPOSITORY_ROOT)
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
print(f"Repository: {REPOSITORY_ROOT}")
```

Markdown cell 2 states that secrets are read from process environment or the
root `.env` and never printed.

Code cell 2 loads and validates the production settings while displaying only
safe values:

```python
from pprint import pprint

from app.config.settings import Settings


settings = Settings.from_env()
safe_settings = {
    "provider": settings.resolve_llm_endpoint().provider,
    "base_url": settings.openrouter_base_url,
    "llm_model": settings.openrouter_llm_model,
    "embedding_model": settings.openrouter_embedding_model,
    "timeout_seconds": settings.openrouter_request_timeout_seconds,
    "mongo_db": settings.mongo_db,
    "mail_alias": settings.mail_index_alias,
    "calendar_alias": settings.calendar_index_alias,
    "timezone": settings.default_user_timezone,
    "MULTI_SOURCE_DEMO": settings.multi_source_demo,
    "OPENROUTER_API_KEY configured": bool(
        settings.openrouter_api_key.get_secret_value().strip()
    ),
}
pprint(safe_settings)
assert settings.multi_source_demo is False, "Set MULTI_SOURCE_DEMO=false"
assert safe_settings["OPENROUTER_API_KEY configured"], (
    "Set OPENROUTER_API_KEY in the process environment or repository .env"
)
```

Markdown cell 3 explains that construction and readiness use the same production
factories and checks as the API service.

Code cell 3 constructs the application and checks dependencies:

```python
import httpx

from app.api.dependencies import build_container
from app.api.main import create_app


container = build_container(settings)
application = create_app(container)
transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
client = httpx.AsyncClient(transport=transport, base_url="http://production-notebook")

readiness_response = await client.get("/ready")
readiness_body = readiness_response.json()
print("readiness HTTP", readiness_response.status_code)
pprint(readiness_body)
assert readiness_response.status_code == 200, (
    "Production dependencies are not ready; fix the reported dependency before chat"
)
assert readiness_body.get("status") == "ready"
```

Markdown cell 4 explains the editable owner, question, public filters, and
follow-up values.

Code cell 4 defines only public request inputs:

```python
USER_ID = "kim"
QUESTION = "이번 주 일정 알려줘"
FILTERS = {
    "teams": [],
    "weeks": [],
}
FOLLOW_UP = "그중 내가 준비해야 할 항목만 정리해줘"
```

Code cell 5 defines safe response rendering and calls `/v1/chat` without a route
field:

```python
obsolete_fields = {"response_mode", "mode", "routing"}


def show_chat_result(response: httpx.Response) -> dict:
    body = response.json()
    print("HTTP", response.status_code)
    print("x-trace-id", response.headers.get("x-trace-id", "missing"))
    if response.status_code != 200:
        pprint(body)
        raise AssertionError("Chat request failed; inspect the safe error and trace ID")

    assert obsolete_fields.isdisjoint(body)
    print("answer:", body.get("answer"))
    print("references:")
    for reference in body.get("references", []):
        print(
            f"- [{reference['evidence_id']}] {reference['source_type']} | "
            f"{reference['title']} | {reference['excerpt']}"
        )
    print("disclosures:", body.get("disclosures", []))
    print("agent_trace:", body.get("agent_trace"))
    execution = body.get("execution") or {}
    print(
        "execution:",
        {
            "status": execution.get("status"),
            "search_count": execution.get("search_count"),
            "evidence_count": execution.get("evidence_count"),
            "duration_ms": execution.get("duration_ms"),
        },
    )
    return body


chat_request = {
    "user_id": USER_ID,
    "message": QUESTION,
    "filters": FILTERS,
}
assert obsolete_fields.isdisjoint(chat_request)
chat_response = await client.post("/v1/chat", json=chat_request)
chat_body = show_chat_result(chat_response)
conversation_id = chat_body["conversation_id"]
```

Markdown cell 5 explains that the next request validates MongoDB-backed
conversation continuity under the same owner.

Code cell 6 sends the follow-up with the server-issued conversation ID:

```python
follow_up_request = {
    "user_id": USER_ID,
    "message": FOLLOW_UP,
    "conversation_id": conversation_id,
    "filters": FILTERS,
}
assert obsolete_fields.isdisjoint(follow_up_request)
follow_up_response = await client.post("/v1/chat", json=follow_up_request)
follow_up_body = show_chat_result(follow_up_response)
assert follow_up_body["conversation_id"] == conversation_id
```

Code cell 7 closes the notebook HTTP client:

```python
await client.aclose()
print("Notebook HTTP client closed")
```

Every code cell must have `execution_count: null`, `outputs: []`, and no saved
widget metadata.

- [ ] **Step 4: Run the notebook contract test and confirm GREEN**

Run:

```bash
python -m pytest tests/mail_rag/test_production_environment_notebook.py -q
```

Expected: two tests pass.

- [ ] **Step 5: Run full repository verification**

Run:

```bash
python -m pytest tests/mail_rag -q
git diff --check
```

Expected: all backend tests pass and no whitespace errors are reported. Do not
claim the live OpenRouter/OpenSearch/MongoDB gate passed unless the notebook was
executed with configured reachable services.

- [ ] **Step 6: Inspect notebook safety and commit**

Run:

```bash
git status --short
git diff --stat
```

Confirm the notebook contains no saved output or secret value and the user's
existing untracked files remain unmodified. Then commit only the notebook and
its contract test:

```bash
git add MultiSource_Production_Environment_Test.ipynb \
  tests/mail_rag/test_production_environment_notebook.py
git commit -m "test(rag): add production environment notebook"
```

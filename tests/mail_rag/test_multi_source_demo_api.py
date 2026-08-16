import asyncio
import os
from pathlib import Path
import subprocess
import sys

import httpx

from app.api.dependencies import build_demo_container
from app.api.main import create_app
from app.config.settings import Settings
from app.domain.policy import PolicyContext
from app.graphs.multi_source import MultiSourceAgenticWorkflow
from app.llm.agentic import RuleBasedAgentModel
from app.persistence.conversations import InMemoryConversationStore
from app.retrieval.multi_source import InMemoryMultiSourceSearch


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_QUESTION = (
    "김OO이 지난주 메일에서 이야기한 NAND 수율 문제가 어떤 회의에서 "
    "논의됐고 회의에서 어떤 Action을 하기로 했으며 기술적으로 어떤 "
    "의미인지 설명해줘."
)


async def post(app, payload):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
        return await client.post("/v1/chat", json=payload)


def test_demo_container_uses_only_in_memory_and_rule_based_dependencies(monkeypatch):
    monkeypatch.setattr(
        "app.api.dependencies.build_ai_gateways",
        lambda _settings: (_ for _ in ()).throw(AssertionError("external AI")),
    )
    monkeypatch.setattr(
        "app.api.dependencies.build_opensearch_client",
        lambda _settings: (_ for _ in ()).throw(AssertionError("OpenSearch")),
    )

    container = build_demo_container(Settings(openrouter_api_key=""))

    assert isinstance(container.conversations, InMemoryConversationStore)
    assert isinstance(container.fast.llm, RuleBasedAgentModel)
    assert isinstance(container.fast.agentic, MultiSourceAgenticWorkflow)
    assert isinstance(container.fast.agentic.search, InMemoryMultiSourceSearch)
    assert container.deep is None


def test_demo_api_runs_canonical_flow_in_canonical_tool_order():
    container = build_demo_container()
    app = create_app(container)

    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": CANONICAL_QUESTION,
                "response_mode": "fast",
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "fast_rag"
    assert {item["source_type"] for item in body["references"]} == {
        "mail",
        "calendar",
        "domain_knowledge",
    }
    assert [action.tool for action, _owner in container.fast.agentic.search.calls] == [
        "search_mail",
        "search_calendar",
        "expand_calendar_event",
        "search_domain_knowledge",
    ]
    assert "FDC" in body["answer"]
    assert body["quality"]["citation_valid"] is True


def test_demo_api_follow_up_uses_saved_raw_event_reference():
    container = build_demo_container()
    app = create_app(container)
    first = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": "NAND Yield Review 회의 찾아줘",
                "response_mode": "fast",
            },
        )
    )
    conversation_id = first.json()["conversation_id"]

    memory = asyncio.run(
        container.conversations.load(conversation_id, PolicyContext.from_user_id("kim"))
    )
    assert memory.previous_event_reference.event_id == "event-kim-1"

    follow_up = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "conversation_id": conversation_id,
                "message": "그 회의에서 Action 뭐였어?",
                "response_mode": "fast",
            },
        )
    )

    assert follow_up.status_code == 200
    assert "FDC" in follow_up.json()["answer"]
    action = container.fast.agentic.search.calls[-1][0]
    assert action.tool == "expand_calendar_event"
    assert action.event_id == "event-kim-1"


def test_demo_api_public_response_does_not_expose_private_agent_state():
    app = create_app(build_demo_container())

    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": "NAND Yield Review 회의에서 Action 뭐였어?",
                "response_mode": "fast",
            },
        )
    )

    body = response.json()
    serialized = response.text
    assert response.status_code == 200
    assert set(body) == {
        "conversation_id",
        "mode",
        "answer",
        "references",
        "quality",
        "disclosures",
        "trace_id",
        "routing",
        "job_id",
        "status",
        "plan_summary",
        "execution",
    }
    for private_name in (
        "agent_memory",
        "agent_trace",
        "analysis",
        "tool_calls",
        "iteration_count",
        "previous_event_reference",
    ):
        assert private_name not in serialized


def test_demo_api_rejects_cross_user_conversation_reuse_without_leaking_it():
    app = create_app(build_demo_container())
    first = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": "NAND Yield Review 회의 찾아줘",
                "response_mode": "fast",
            },
        )
    )
    conversation_id = first.json()["conversation_id"]

    foreign = asyncio.run(
        post(
            app,
            {
                "user_id": "lee",
                "conversation_id": conversation_id,
                "message": "그 회의에서 Action 뭐였어?",
                "response_mode": "fast",
            },
        )
    )

    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "UNAUTHORIZED_RESOURCE"
    assert conversation_id not in foreign.text
    assert "FDC" not in foreign.text


def test_demo_readiness_is_ready():
    app = create_app(build_demo_container())

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://demo"
        ) as client:
            return await client.get("/ready")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert set(response.json()["dependencies"].values()) == {"ready"}


def test_asgi_export_requires_explicit_demo_flag_and_builds_demo_app():
    code = """
from app.api import main
from app.persistence.conversations import InMemoryConversationStore
assert main.app is not None
assert isinstance(main.app.state.container.conversations, InMemoryConversationStore)
"""
    environment = {
        **os.environ,
        "MULTI_SOURCE_DEMO": "true",
        "OPENROUTER_API_KEY": "",
        "MONGO_URI": "mongodb://127.0.0.1:1",
    }

    enabled = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    disabled = subprocess.run(
        [sys.executable, "-c", "from app.api import main; assert main.app is None"],
        cwd=ROOT,
        env={**environment, "MULTI_SOURCE_DEMO": "false"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert enabled.returncode == 0, enabled.stderr
    assert disabled.returncode == 0, disabled.stderr


def test_demo_cli_runs_canonical_scenario_without_external_services():
    result = subprocess.run(
        [sys.executable, "scripts/run_multi_source_demo.py"],
        cwd=ROOT,
        env={**os.environ, "OPENROUTER_API_KEY": ""},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "FDC" in result.stdout
    assert "Sources:" in result.stdout
    assert (
        "Tool calls: search_mail -> search_calendar -> "
        "expand_calendar_event -> search_domain_knowledge"
    ) in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_demo_cli_reports_invalid_input_without_traceback_or_raw_state():
    result = subprocess.run(
        [sys.executable, "scripts/run_multi_source_demo.py", ""],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "요청을 처리할 수 없습니다." in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "ValidationError" not in result.stdout + result.stderr

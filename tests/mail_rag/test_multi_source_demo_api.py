import asyncio
from datetime import UTC, date, datetime
import os
from pathlib import Path
import re
import subprocess
import sys

import httpx
import pytest

from app.api.dependencies import build_demo_container
from app.api.main import create_app
from app.config.settings import Settings
from app.domain.agentic import IntentDecision, SourceRequest
from app.domain.policy import PolicyContext
from app.graphs.multi_source import MultiSourceAgenticWorkflow
from app.llm.demo_scenarios import StaticIntentAnalyzer, scenario_decisions
from app.persistence.conversations import InMemoryConversationStore
from app.retrieval.multi_source import InMemoryMultiSourceSearch


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 17, 0, tzinfo=UTC)
CANONICAL_NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)
CANONICAL_QUESTION = (
    "김OO이 지난주 메일에서 이야기한 NAND 수율 문제가 어떤 회의에서 "
    "논의됐고 회의에서 어떤 Action을 하기로 했으며 기술적으로 어떤 "
    "의미인지 설명해줘."
)


def demo_container(scenario="canonical"):
    return build_demo_container(
        Settings(openrouter_api_key=""),
        agent_model=StaticIntentAnalyzer(
            scenario_decisions(scenario),
            now=CANONICAL_NOW if scenario == "canonical" else NOW,
        ),
    )


def exact_date_container(day):
    return build_demo_container(
        Settings(openrouter_api_key=""),
        agent_model=StaticIntentAnalyzer(
            (
                IntentDecision(
                    intent="daily_schedule",
                    source_requests=[
                        SourceRequest(source="calendar", query="일정")
                    ],
                    time_scope="exact_date",
                    exact_date=date.fromisoformat(day),
                ),
            ),
            now=NOW,
        ),
    )


async def post(app, payload):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
        return await client.post("/v1/chat", json=payload)


def canonical_demo_response():
    app = create_app(demo_container())
    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": CANONICAL_QUESTION,
            },
        )
    )
    assert response.status_code == 200
    return response.json()


def test_application_search_code_contains_no_physical_ews_index_names():
    roots = [ROOT / "app", ROOT / "scripts"]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root in roots
        for path in root.rglob("*.py")
    )
    assert "ews-mail-v1" not in text
    assert "ews-calendar-v1" not in text


def test_demo_response_never_contains_decoy_content():
    body = canonical_demo_response()
    serialized = str(body)
    assert "다른 사용자의" not in serialized
    assert "비활성 문서" not in serialized
    assert "취소된 회의" not in serialized


def test_every_answer_citation_exists_in_same_response_references():
    body = canonical_demo_response()
    evidence_ids = {item["evidence_id"] for item in body["references"]}
    cited = set(re.findall(r"\[(S\d+)\]", body["answer"]))
    assert cited
    assert cited <= evidence_ids


def test_demo_container_accepts_injected_typed_analyzer_without_external_services(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.dependencies.build_ai_gateways",
        lambda _settings: (_ for _ in ()).throw(AssertionError("external AI")),
    )
    monkeypatch.setattr(
        "app.api.dependencies.build_opensearch_client",
        lambda _settings: (_ for _ in ()).throw(AssertionError("OpenSearch")),
    )

    static = StaticIntentAnalyzer(
        scenario_decisions("weekly-calendar"), now=NOW
    )
    container = build_demo_container(
        Settings(openrouter_api_key=""), agent_model=static
    )

    assert isinstance(container.conversations, InMemoryConversationStore)
    assert container.agentic.analyzer is static
    assert isinstance(container.agentic, MultiSourceAgenticWorkflow)
    assert isinstance(container.agentic.search, InMemoryMultiSourceSearch)
    assert not hasattr(container, "router")
    assert not hasattr(container, "fast")
    assert not hasattr(container, "deep")


def test_demo_api_runs_canonical_flow_in_canonical_tool_order():
    container = demo_container()
    app = create_app(container)

    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": CANONICAL_QUESTION,
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent_trace"]["tool_calls"] == [
        "search_mail",
        "search_calendar",
        "expand_calendar_event",
        "search_domain_knowledge",
    ]
    assert {item["source_type"] for item in body["references"]} == {
        "mail",
        "calendar",
        "domain_knowledge",
    }
    assert [action.tool for action, _owner in container.agentic.search.calls] == [
        "search_mail",
        "search_calendar",
        "expand_calendar_event",
        "search_domain_knowledge",
    ]
    assert "FDC" in body["answer"]
    assert body["quality"]["citation_valid"] is True
    assert body["quality"]["limited_answer"] is False


def test_demo_api_returns_all_current_week_events_for_generic_schedule_question():
    container = demo_container("weekly-calendar")
    app = create_app(container)

    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": "이번주 일정알려줘",
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["document_id"] for item in body["references"]} == {
        "event-kim-20260817",
        "event-kim-20260818",
        "event-kim-20260819",
        "event-kim-20260820",
        "event-kim-20260821",
    }
    assert body["quality"]["citation_valid"] is True
    assert body["quality"]["limited_answer"] is False
    assert [
        action.tool for action, _owner in container.agentic.search.calls
    ] == ["search_calendar"]
    assert container.agentic.search.calls[0][0].query == "일정"


@pytest.mark.parametrize(
    ("day", "expected_event_id"),
    [
        ("2026-08-03", "event-kim-20260803"),
        ("2026-08-31", "event-kim-20260831"),
    ],
)
def test_demo_api_retrieves_beginning_and_end_of_month_events(
    day,
    expected_event_id,
):
    app = create_app(exact_date_container(day))

    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": f"{day} 일정 알려줘",
            },
        )
    )

    assert response.status_code == 200
    assert {
        item["document_id"] for item in response.json()["references"]
    } == {expected_event_id}


def test_demo_api_follow_up_uses_saved_raw_event_reference():
    container = demo_container("followup")
    app = create_app(container)
    first = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": "NAND Yield Review 회의 찾아줘",
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
            },
        )
    )

    assert follow_up.status_code == 200
    assert "FDC" in follow_up.json()["answer"]
    action = container.agentic.search.calls[-1][0]
    assert action.tool == "expand_calendar_event"
    assert action.event_id == "event-kim-1"
    assert container.agentic.search.calls[-1][1] == "kim"
    assert [
        item.tool for item, _owner in container.agentic.search.calls
    ] == ["search_calendar", "expand_calendar_event"]


def test_demo_api_public_response_exposes_only_bounded_agent_trace():
    app = create_app(demo_container("event-action"))

    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": "NAND Yield Review 회의에서 Action 뭐였어?",
            },
        )
    )

    body = response.json()
    serialized = response.text
    assert response.status_code == 200
    assert set(body) == {
        "conversation_id",
        "answer",
        "references",
        "quality",
        "disclosures",
        "trace_id",
        "agent_trace",
        "execution",
    }
    assert set(body["agent_trace"]) == {
        "tool_calls",
        "judge_decisions",
        "iteration_count",
    }
    assert len(body["agent_trace"]["tool_calls"]) <= 8
    assert len(body["agent_trace"]["judge_decisions"]) <= 8
    assert body["agent_trace"]["iteration_count"] <= 4
    for private_name in (
        "agent_memory",
        "analysis",
        "previous_event_reference",
    ):
        assert private_name not in serialized


def test_demo_api_rejects_cross_user_conversation_reuse_without_leaking_it():
    app = create_app(demo_container("followup"))
    first = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": "NAND Yield Review 회의 찾아줘",
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
            },
        )
    )

    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "UNAUTHORIZED_RESOURCE"
    assert conversation_id not in foreign.text
    assert "FDC" not in foreign.text


def test_demo_readiness_is_ready():
    app = create_app(demo_container())

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
        "MANUS_API_KEY": "",
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
        env={**os.environ, "MANUS_API_KEY": "", "OPENROUTER_API_KEY": ""},
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


def test_demo_cli_weekly_calendar_offline_scenario_is_calendar_only():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_multi_source_demo.py",
            "--offline-scenario",
            "weekly-calendar",
        ],
        cwd=ROOT,
        env={**os.environ, "MANUS_API_KEY": "", "OPENROUTER_API_KEY": ""},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Tool calls: search_calendar" in result.stdout
    assert "search_domain_knowledge" not in result.stdout


def test_free_form_demo_without_llm_key_fails_safely():
    result = subprocess.run(
        [sys.executable, "scripts/run_multi_source_demo.py", "이번주 일정 뭐야?"],
        cwd=ROOT,
        env={**os.environ, "MANUS_API_KEY": "", "OPENROUTER_API_KEY": ""},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "MANUS_API_KEY" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_offline_demo_reports_invalid_request_without_traceback():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_multi_source_demo.py",
            "--offline-scenario",
            "weekly-calendar",
            "--user-id",
            "",
        ],
        cwd=ROOT,
        env={**os.environ, "MANUS_API_KEY": "", "OPENROUTER_API_KEY": ""},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "요청을 처리할 수 없습니다." in result.stderr
    assert "Traceback" not in result.stdout + result.stderr

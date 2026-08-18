import asyncio
from datetime import UTC, datetime

from pydantic import SecretStr

from app.api.dependencies import (
    build_ai_gateways,
    build_demo_container,
    build_stage_llm_gateways,
)
from app.config.settings import Settings
from app.domain.agentic import IntentDecision, QueryAnalysis, SourceRequest
from app.llm.agentic import StructuredAgentModel
from app.llm.demo_scenarios import StaticIntentAnalyzer, UnavailableAnalyzer
from app.llm.gateway import OpenAILLMGateway
from app.llm.manus import ManusLLMGateway
from app.persistence.conversations import ConversationMemory
from app.retrieval.embedding import OpenAIEmbeddingGateway


def test_ai_gateways_use_openrouter_free_for_llm_and_embeddings(
    monkeypatch,
):
    clients = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            clients.append(self)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(
        openrouter_api_key=SecretStr("openrouter-secret"),
    )

    llm, embeddings = build_ai_gateways(settings)

    assert isinstance(llm, OpenAILLMGateway)
    assert llm.client is clients[0]
    assert llm.model == "openrouter/free"
    assert llm.native_structured_output is True
    assert isinstance(embeddings, OpenAIEmbeddingGateway)
    assert [client.kwargs for client in clients] == [
        {
            "api_key": "openrouter-secret",
            "base_url": "https://openrouter.ai/api/v1",
            "timeout": 150.0,
        },
        {
            "api_key": "openrouter-secret",
            "base_url": "https://openrouter.ai/api/v1",
            "timeout": 150.0,
        },
    ]
    assert embeddings.client is clients[1]
    assert embeddings.model == "qwen/qwen3-embedding-8b"


def test_stage_llm_gateways_share_one_client_and_use_independent_models(
    monkeypatch,
):
    clients = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            clients.append(self)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(
        openrouter_api_key=SecretStr("openrouter-secret"),
        openrouter_routing_model="cheap-routing",
        openrouter_planner_model="cheap-planner",
        openrouter_judge_model="cheap-judge",
        openrouter_answer_model="quality-answer",
    )

    gateways = build_stage_llm_gateways(settings)

    assert list(gateways) == ["routing", "planner", "judge", "answer"]
    assert [gateway.model for gateway in gateways.values()] == [
        "cheap-routing",
        "cheap-planner",
        "cheap-judge",
        "quality-answer",
    ]
    assert len(clients) == 1
    assert all(gateway.client is clients[0] for gateway in gateways.values())


def test_openai_compatible_selection_builds_existing_llm_gateway(
    monkeypatch,
):
    clients = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            clients.append(self)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
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
    assert llm.native_structured_output is False
    assert llm.client is clients[0]
    assert clients[0].kwargs == {
        "api_key": "llm-secret",
        "base_url": "https://llm.example/v1",
        "timeout": 150.0,
    }
    assert embeddings.model == "qwen/qwen3-embedding-8b"
    assert embeddings.client is clients[1]


def test_demo_container_builds_manus_analyzer_for_configured_llm_key(
    monkeypatch,
):
    clients = []

    class FakeAsyncHTTPClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            clients.append(self)

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncHTTPClient)
    settings = Settings(
        llm_provider="manus",
        manus_api_key=SecretStr("configured-key"),
    )

    container = build_demo_container(settings)

    analyzer = container.agentic.analyzer
    assert isinstance(analyzer, StructuredAgentModel)
    assert isinstance(analyzer.llm, ManusLLMGateway)
    assert analyzer.llm.client is clients[0]
    assert clients[0].kwargs == {
        "base_url": "https://api.manus.ai",
        "headers": {"x-manus-api-key": "configured-key"},
        "timeout": 30.0,
    }
    assert analyzer.attempts == 1


def test_demo_container_wires_each_configured_stage_model(monkeypatch):
    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(
        openrouter_api_key=SecretStr("configured-key"),
        openrouter_routing_model="cheap-routing",
        openrouter_planner_model="cheap-planner",
        openrouter_judge_model="cheap-judge",
        openrouter_answer_model="quality-answer",
    )

    analyzer = build_demo_container(settings).agentic.analyzer

    assert analyzer.routing_llm.model == "cheap-routing"
    assert analyzer.planner_llm.model == "cheap-planner"
    assert analyzer.judge_llm.model == "cheap-judge"
    assert analyzer.answer_llm.model == "quality-answer"


def test_demo_container_without_llm_key_reports_unavailable_analyzer():
    container = build_demo_container(Settings(openrouter_api_key=""))

    assert isinstance(container.agentic.analyzer, UnavailableAnalyzer)
    status = asyncio.run(container.readiness.check())
    assert status["agent_model"] == "unavailable"
    analysis = asyncio.run(
        container.agentic.analyzer.analyze(
            "free-form question",
            object(),
            "Asia/Seoul",
        )
    )
    assert analysis == QueryAnalysis.unavailable()


def test_static_intent_analyzer_consumes_decisions_by_call_order_not_question():
    analyzer = StaticIntentAnalyzer(
        (
            IntentDecision(
                intent="first",
                source_requests=[
                    SourceRequest(source="calendar", query="일정")
                ],
            ),
            IntentDecision(
                intent="second",
                source_requests=[
                    SourceRequest(source="mail", query="수율")
                ],
            ),
        ),
        now=datetime(2026, 8, 17, 0, tzinfo=UTC),
    )

    first = asyncio.run(
        analyzer.analyze("identical question", ConversationMemory(), "Asia/Seoul")
    )
    second = asyncio.run(
        analyzer.analyze("identical question", ConversationMemory(), "Asia/Seoul")
    )

    assert first.intent == "first"
    assert second.intent == "second"

import asyncio
from datetime import UTC, datetime

from pydantic import SecretStr

from app.api.dependencies import build_ai_gateways, build_demo_container
from app.config.settings import Settings
from app.domain.agentic import IntentDecision, QueryAnalysis, SourceRequest
from app.llm.agentic import StructuredAgentModel
from app.llm.demo_scenarios import StaticIntentAnalyzer, UnavailableAnalyzer
from app.llm.gateway import OpenAILLMGateway
from app.persistence.conversations import ConversationMemory


def test_ai_gateways_use_separate_openrouter_clients_with_150_second_timeout(
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
    assert llm.client is clients[0]
    assert embeddings.client is clients[1]
    assert llm.client is not embeddings.client
    assert llm.model == "google/gemma-4-26b-a4b-it:free"
    assert embeddings.model == "qwen/qwen3-embedding-8b"


def test_demo_container_builds_structured_analyzer_for_configured_llm_key(
    monkeypatch,
):
    clients = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            clients.append(self)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(openrouter_api_key=SecretStr("configured-key"))

    container = build_demo_container(settings)

    analyzer = container.fast.agentic.analyzer
    assert isinstance(analyzer, StructuredAgentModel)
    assert isinstance(analyzer.llm, OpenAILLMGateway)
    assert analyzer.llm.client is clients[0]
    assert clients[0].kwargs == {
        "api_key": "configured-key",
        "base_url": "https://openrouter.ai/api/v1",
        "timeout": 150.0,
    }


def test_demo_container_without_llm_key_reports_unavailable_analyzer():
    container = build_demo_container(Settings(openrouter_api_key=""))

    assert isinstance(container.fast.agentic.analyzer, UnavailableAnalyzer)
    status = asyncio.run(container.readiness.check())
    assert status["agent_model"] == "unavailable"
    analysis = asyncio.run(
        container.fast.agentic.analyzer.analyze(
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

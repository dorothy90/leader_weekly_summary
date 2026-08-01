import asyncio
from types import SimpleNamespace

import pytest

from app.domain.chat import ChatRequest, RouteDecision
from app.graphs.router import route_request
from app.llm.gateway import OpenAILLMGateway


class RecordingLLM:
    def __init__(
        self, decision: RouteDecision | None = None, error: Exception | None = None
    ):
        self.decision = decision
        self.error = error
        self.calls = 0
        self.inputs = []

    async def complete_model(self, system, user, schema):
        self.calls += 1
        self.inputs.append((system, user, schema))
        if self.error:
            raise self.error
        assert schema is RouteDecision
        return self.decision


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def test_llm_gateway_sends_deterministic_system_and_user_messages():
    completions = FakeCompletions("답변")
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = asyncio.run(
        OpenAILLMGateway(client, "model-1").complete_text("system", "user")
    )

    assert result == "답변"
    assert completions.calls == [
        {
            "model": "model-1",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "temperature": 0,
        }
    ]


def test_llm_gateway_validates_fenced_json_against_requested_schema():
    completions = FakeCompletions(
        """```json
        {"route":"fast","reason_code":"bounded","confidence":1,"estimated_searches":1}
        ```"""
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = asyncio.run(
        OpenAILLMGateway(client, "model-1").complete_model(
            "route",
            "question",
            RouteDecision,
        )
    )

    assert result.route == "fast"
    assert "RouteDecision" in completions.calls[0]["messages"][0]["content"]


def test_structured_fast_decision_is_not_auto_upgraded_to_deep():
    llm = RecordingLLM(
        RouteDecision(
            route="fast",
            reason_code="model_fast",
            confidence=0.9,
            estimated_searches=1,
        )
    )
    request = ChatRequest(
        user_id="kim",
        message="4주 추세 보고서",
        filters={"weeks": ["2026-05", "2026-06", "2026-07", "2026-08"]},
    )

    decision = asyncio.run(route_request(request, llm))

    assert decision.route == "fast"
    assert decision.reason_code == "model_fast"


def test_router_redacts_credentials_and_file_uris_before_model_call():
    llm = RecordingLLM(
        RouteDecision(
            route="fast",
            reason_code="model_fast",
            confidence=1,
            estimated_searches=1,
        )
    )

    asyncio.run(
        route_request(
            ChatRequest(
                user_id="kim",
                message="client_secret=route-secret file:/srv/private/mail.eml 질문",
            ),
            llm,
        )
    )

    model_input = llm.inputs[0][1]
    assert "route-secret" not in model_input
    assert "file:/srv" not in model_input


@pytest.mark.parametrize("mode", ["fast", "deep"])
def test_explicit_response_mode_keeps_fast_and_deep_separate(mode):
    llm = RecordingLLM(error=AssertionError("router model must not be called"))

    decision = asyncio.run(
        route_request(
            ChatRequest(user_id="kim", message="질문", response_mode=mode),
            llm,
        )
    )

    assert decision.route == mode
    assert decision.reason_code == "explicit_mode"
    assert llm.calls == 0


def test_router_failure_uses_deterministic_fallback_decision():
    llm = RecordingLLM(error=TimeoutError("router unavailable"))
    request = ChatRequest(
        user_id="kim",
        message="4주 추세 보고서",
        filters={"weeks": ["2026-05", "2026-06", "2026-07", "2026-08"]},
    )

    decision = asyncio.run(route_request(request, llm))

    assert decision == RouteDecision(
        route="deep",
        reason_code="deterministic_long_period",
        confidence=1,
        estimated_searches=4,
        requested_output="report",
    )


@pytest.mark.parametrize(
    ("message", "requested_output"),
    [
        ("PPT로 만들어줘", "presentation"),
        ("보고서 작성", "report"),
        ("최근 추세", "answer"),
    ],
)
def test_router_failure_uses_all_research_output_indicators(
    message,
    requested_output,
):
    decision = asyncio.run(
        route_request(
            ChatRequest(user_id="kim", message=message),
            RecordingLLM(error=TimeoutError("router unavailable")),
        )
    )

    assert decision.route == "deep"
    assert decision.reason_code == "deterministic_research_output"
    assert decision.requested_output == requested_output

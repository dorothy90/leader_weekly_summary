import asyncio
from types import SimpleNamespace
import httpx
import pytest
from openai import InternalServerError
from app.llm.gateway import OpenAILLMGateway, LLMResponseError
from app.domain.quick_rag import QuickPlan
from app.graphs.quick_rag import QuickRAGWorkflow
from app.domain.chat import ChatRequest
from app.domain.policy import PolicyContext


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,finish,code",
    [
        ("", "length", "LLM_OUTPUT_TRUNCATED"),
        ("", "stop", "LLM_EMPTY_RESPONSE"),
        ("{broken", "stop", "LLM_INVALID_FORMAT"),
    ],
)
async def test_invalid_provider_output_classified_without_retry(content, finish, code):
    calls = []

    async def create(**kw):
        calls.append(kw)
        return SimpleNamespace(
            model="fixture",
            choices=[
                SimpleNamespace(
                    finish_reason=finish, message=SimpleNamespace(content=content)
                )
            ],
        )

    gateway = OpenAILLMGateway(
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        "fixture",
        native_structured_output=True,
        reasoning_enabled=False,
    )
    with pytest.raises(LLMResponseError) as error:
        await gateway.complete_model("system", "synthetic", QuickPlan)
    assert error.value.code == code
    assert len(calls) == 1
    assert calls[0]["extra_body"]["reasoning"] == {"enabled": False}
    assert calls[0]["extra_body"]["provider"]["require_parameters"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected", [("json", "LLM_INVALID_FORMAT"), ("provider", "LLM_UNAVAILABLE")]
)
async def test_workflow_reports_model_failure_instead_of_bad_user_question(
    kind, expected
):
    class Broken:
        async def complete_model(self, *args):
            if kind == "json":
                raise LLMResponseError("LLM_INVALID_FORMAT")
            raise InternalServerError(
                "private upstream payload",
                response=httpx.Response(
                    503, request=httpx.Request("POST", "https://test.invalid")
                ),
                body=None,
            )

    class Search:
        def capabilities(self):
            return {}

        async def search(self, *args, **kwargs):
            raise AssertionError("no search on invalid plan")

    result = await QuickRAGWorkflow(Search(), Broken(), Broken()).invoke(
        ChatRequest(user_id="fixture", message="일정"),
        PolicyContext.from_user_id("fixture"),
    )
    assert result.execution.error_code == expected
    assert result.execution.retryable
    assert result.execution.search_count == 0
    assert "질문이나 검색 조건" not in result.answer
    assert "private" not in result.answer
    assert result.agent_trace.llm_calls == ["interpret"]


def test_quick_free_router_is_pinned_without_changing_explicit_models(monkeypatch):
    import app.api.dependencies as dep
    from app.config.settings import Settings

    monkeypatch.setattr("openai.AsyncOpenAI", lambda **kw: SimpleNamespace())
    settings = Settings(_env_file=None, openrouter_api_key="fixture", openrouter_routing_model="")
    interpreter, answer = dep.build_quick_llm_gateways(settings)
    assert interpreter.model == settings.quick_openrouter_free_model
    assert interpreter.model != "openrouter/free"
    assert interpreter.reasoning_enabled is False
    settings.openrouter_routing_model = "explicit-model"
    interpreter, _ = dep.build_quick_llm_gateways(settings)
    assert interpreter.model == "explicit-model"
    assert interpreter.reasoning_enabled is None

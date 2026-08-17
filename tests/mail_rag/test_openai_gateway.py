from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.llm.gateway import OpenAILLMGateway


class _Decision(BaseModel):
    intent: str
    source: str = "calendar"


class _Completions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"intent":"calendar"}')
                )
            ]
        )


@pytest.mark.asyncio
async def test_openrouter_structured_completion_sends_json_schema():
    completions = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    gateway = OpenAILLMGateway(
        client,
        "openrouter/free",
        native_structured_output=True,
    )

    result = await gateway.complete_model("Classify intent.", "이번 주 일정", _Decision)

    assert result == _Decision(intent="calendar")
    expected_schema = _Decision.model_json_schema()
    expected_schema["required"] = ["intent", "source"]
    assert completions.kwargs["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "_Decision",
            "strict": True,
            "schema": expected_schema,
        },
    }
    assert completions.kwargs["max_tokens"] == 4096
    assert completions.kwargs["extra_body"] == {
        "provider": {"require_parameters": True}
    }


@pytest.mark.asyncio
async def test_prompt_structured_completion_keeps_compatible_request_shape():
    completions = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    gateway = OpenAILLMGateway(client, "future-model")

    result = await gateway.complete_model("Classify intent.", "이번 주 일정", _Decision)

    assert result == _Decision(intent="calendar")
    assert "response_format" not in completions.kwargs
    assert "extra_body" not in completions.kwargs

import json

import httpx
import pytest

from app.domain.agentic import IntentDecision
from app.llm.manus import ManusLLMError, ManusLLMGateway


TASK_ID = "A" * 22


def _intent_payload() -> str:
    return json.dumps(
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


def _created() -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "task_id": TASK_ID})


def _detail(status: str = "stopped") -> httpx.Response:
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


def _messages(*, success: bool = True) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "ok": True,
            "messages": [
                {
                    "type": "structured_output_result",
                    "structured_output_result": {
                        "success": success,
                        "value": {"payload_json": _intent_payload()},
                        "error": None if success else "invalid output",
                    },
                }
            ],
        },
    )


def _gateway(handler, *, task_timeout_seconds: float = 1):
    client = httpx.AsyncClient(
        base_url="https://api.manus.ai",
        headers={"x-manus-api-key": "api-secret"},
        transport=httpx.MockTransport(handler),
    )
    return ManusLLMGateway(
        client,
        profile="manus-1.6-lite",
        poll_interval_seconds=0,
        task_timeout_seconds=task_timeout_seconds,
    )


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
            assert body["share_visibility"] == "private"
            envelope = body["structured_output_schema"]
            assert envelope["required"] == ["payload_json"]
            assert envelope["additionalProperties"] is False
            assert "IntentDecision" in envelope["properties"][
                "payload_json"
            ]["description"]
            return _created()
        if request.url.path == "/v2/task.detail":
            detail_calls += 1
            if detail_calls == 1:
                return httpx.Response(
                    404,
                    json={"ok": False, "error": {"code": "not_found"}},
                )
            return _detail("running" if detail_calls == 2 else "stopped")
        assert request.url.path == "/v2/task.listMessages"
        return _messages()

    completion = await _gateway(handler).complete_model_with_diagnostics(
        "system", "user", IntentDecision
    )

    assert completion.value.time_scope == "current_week"
    assert completion.diagnostics.requested_profile == "manus-1.6-lite"
    assert completion.diagnostics.actual_profile == "manus-1.6"
    assert completion.diagnostics.credit_usage == 0


@pytest.mark.asyncio
async def test_manus_gateway_rejects_unsuccessful_structured_output():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/task.create":
            return _created()
        if request.url.path == "/v2/task.detail":
            return _detail()
        return _messages(success=False)

    with pytest.raises(ManusLLMError) as captured:
        await _gateway(handler).complete_model("system", "user", IntentDecision)

    assert captured.value.code == "structured_output_failed"


@pytest.mark.asyncio
async def test_manus_gateway_fails_closed_on_waiting_status():
    messages_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal messages_called
        if request.url.path == "/v2/task.create":
            return _created()
        if request.url.path == "/v2/task.detail":
            return _detail("waiting")
        messages_called = True
        return _messages()

    with pytest.raises(ManusLLMError) as captured:
        await _gateway(handler).complete_model("system", "user", IntentDecision)

    assert captured.value.code == "task_waiting"
    assert messages_called is False


@pytest.mark.asyncio
async def test_manus_gateway_fails_closed_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/task.create":
            return _created()
        return _detail("error")

    with pytest.raises(ManusLLMError) as captured:
        await _gateway(handler).complete_model("system", "user", IntentDecision)

    assert captured.value.code == "task_error"


@pytest.mark.asyncio
async def test_manus_gateway_times_out_repeated_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/task.create":
            return _created()
        return httpx.Response(404, json={"ok": False})

    with pytest.raises(ManusLLMError) as captured:
        await _gateway(
            handler,
            task_timeout_seconds=0.01,
        ).complete_model("system", "user", IntentDecision)

    assert captured.value.code == "task_timeout"


@pytest.mark.asyncio
async def test_manus_diagnostics_exclude_task_and_prompt_values():
    task_url = f"https://manus.im/app/{TASK_ID}"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/task.create":
            return httpx.Response(
                200,
                json={"ok": True, "task_id": TASK_ID, "task_url": task_url},
            )
        if request.url.path == "/v2/task.detail":
            response = _detail()
            response.json()["task"]["task_url"] = task_url
            return response
        return _messages()

    completion = await _gateway(handler).complete_model_with_diagnostics(
        "private-system-prompt",
        "private-user-prompt",
        IntentDecision,
    )
    rendered = repr(completion.diagnostics)

    for forbidden in (
        TASK_ID,
        task_url,
        "api-secret",
        "private-system-prompt",
        "private-user-prompt",
    ):
        assert forbidden not in rendered

import asyncio
from types import SimpleNamespace

import httpx

from app.api.main import create_app
from app.api.routes.chat import CONTEXT_UNAVAILABLE_DISCLOSURE
from app.domain.agentic import AgentMemoryUpdate, AgentTrace
from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ExecutionMetadata,
    FastRAGResult,
    QualityStatus,
)
from app.domain.evidence import Evidence
from app.persistence.conversations import InMemoryConversationStore


class RecordingAgent:
    def __init__(self, result: FastRAGResult):
        self.result = result
        self.calls = []

    async def invoke(self, request, policy, conversation):
        self.calls.append((request, policy, conversation))
        return self.result


class ASGIClient:
    def __init__(self, app):
        self.app = app

    def post(self, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(
                app=self.app,
                raise_app_exceptions=False,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as session:
                return await session.post(path, **kwargs)

        return asyncio.run(send())


def _evidence(owner="kim") -> Evidence:
    return Evidence(
        evidence_id="S1",
        source_type="calendar",
        document_id="/private/calendar-1.json",
        title="주간 회의",
        excerpt="월요일 오전 10시 주간 회의",
        score=1,
        user_id=owner,
        acl_decision_id="acl-1",
        content_hash="hash-1",
    )


def _success_result(**updates) -> FastRAGResult:
    base = FastRAGResult(
        answer="이번 주 일정입니다 [S1]",
        evidence=[_evidence()],
        quality=QualityStatus(citation_valid=True),
        execution=ExecutionMetadata(
            status="succeeded",
            search_count=1,
            evidence_count=1,
        ),
        agent_trace=AgentTrace(
            tool_calls=["search_calendar"],
            judge_decisions=["sufficient"],
            iteration_count=1,
        ),
    )
    return base.model_copy(update=updates)


def _limited_result(**updates) -> FastRAGResult:
    base = FastRAGResult(
        answer="검증된 근거만으로 답변을 제공할 수 없습니다.",
        evidence=[],
        quality=QualityStatus(
            citation_valid=None,
            limited_answer=True,
            retrieval_mode="deterministic",
        ),
        execution=ExecutionMetadata(
            status="limited",
            failure_stage="retrieval",
            error_code="NO_EVIDENCE",
            include_in_llm_history=False,
        ),
        agent_trace=AgentTrace(
            tool_calls=[],
            judge_decisions=["no_action"],
        ),
    )
    return base.model_copy(update=updates)


def _client(agent, conversations=None):
    container = SimpleNamespace(
        agentic=agent,
        conversations=conversations,
        jobs=None,
        traces=None,
    )
    return ASGIClient(create_app(container))


def _post(client, **updates):
    body = {
        "user_id": "kim",
        "message": "이번 주 일정",
        "filters": {},
    }
    body.update(updates)
    return client.post("/v1/chat", json=body)


def test_every_chat_request_invokes_the_agentic_workflow_directly():
    agent = RecordingAgent(_success_result())
    response = _post(
        _client(agent),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(agent.calls) == 1
    assert agent.calls[0][1].user_id == "kim"
    assert body["agent_trace"]["tool_calls"] == ["search_calendar"]
    assert body["execution"]["search_count"] == 1
    assert "mode" not in body
    assert "routing" not in body


def test_response_mode_is_rejected_before_agent_execution():
    agent = RecordingAgent(_success_result())
    response = _post(_client(agent), response_mode="fast")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert agent.calls == []


def test_failed_agent_execution_never_returns_the_internal_answer():
    result = _limited_result(
        answer="노출하면 안 되는 내부 실패 답변",
        execution=ExecutionMetadata(
            status="failed",
            failure_stage="retrieval",
            error_code="RETRIEVAL_TIMEOUT",
            retryable=True,
            include_in_llm_history=False,
        ),
    )
    response = _post(_client(RecordingAgent(result)))

    assert response.status_code == 200
    assert response.json()["answer"] is None
    assert response.json()["execution"]["status"] == "failed"


def test_foreign_or_uncited_evidence_is_not_exposed():
    result = _success_result(
        evidence=[_evidence(owner="lee")],
        answer="다른 사용자 일정입니다 [S1]",
    )
    response = _post(_client(RecordingAgent(result)))

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "검증된 근거만으로 답변을 제공할 수 없습니다."
    assert body["references"] == []
    assert body["quality"]["citation_valid"] is False
    assert body["quality"]["limited_answer"] is True


def test_safe_reference_hides_storage_and_acl_identifiers():
    response = _post(_client(RecordingAgent(_success_result())))

    reference = response.json()["references"][0]
    assert reference["evidence_id"] == "S1"
    assert reference["document_id"] != "/private/calendar-1.json"
    assert "acl_decision_id" not in reference
    assert "content_hash" not in reference


def test_bm25_fallback_disclosure_is_preserved_exactly_once():
    result = _limited_result(
        disclosures=[BM25_FALLBACK_DISCLOSURE, BM25_FALLBACK_DISCLOSURE],
    )
    response = _post(_client(RecordingAgent(result)))

    assert response.json()["disclosures"] == [BM25_FALLBACK_DISCLOSURE]


def test_conversation_memory_is_passed_to_the_same_agent_and_saved():
    conversations = InMemoryConversationStore()
    result = _success_result(
        agent_memory=AgentMemoryUpdate(
            current_topic="주간 회의",
            search_history=["search_calendar"],
        )
    )
    agent = RecordingAgent(result)
    client = _client(agent, conversations)

    first = _post(client)
    conversation_id = first.json()["conversation_id"]
    second = _post(
        client,
        message="그 일정 장소는?",
        conversation_id=conversation_id,
    )

    assert second.status_code == 200
    memory = agent.calls[1][2]
    assert memory.messages[-2:] == [
        {"role": "user", "content": "이번 주 일정"},
        {"role": "assistant", "content": "이번 주 일정입니다 [S1]"},
    ]
    assert memory.current_topic == "주간 회의"
    assert memory.search_history == ["search_calendar"]


def test_missing_or_foreign_conversation_never_reaches_the_agent():
    conversations = InMemoryConversationStore()
    agent = RecordingAgent(_success_result())
    client = _client(agent, conversations)

    missing = _post(client, conversation_id="missing")
    first = _post(client)
    foreign = _post(
        client,
        user_id="lee",
        conversation_id=first.json()["conversation_id"],
    )

    assert missing.status_code == 404
    assert foreign.status_code == 404
    assert len(agent.calls) == 1


def test_conversation_store_failure_is_disclosed_without_changing_execution_path():
    class BrokenStore:
        async def load(self, conversation_id, policy):
            raise RuntimeError("unavailable")

        async def save(self, conversation_id, policy, memory):
            raise RuntimeError("unavailable")

    agent = RecordingAgent(_limited_result())
    response = _post(_client(agent, BrokenStore()))

    assert response.status_code == 200
    assert len(agent.calls) == 1
    assert response.json()["disclosures"].count(
        CONTEXT_UNAVAILABLE_DISCLOSURE
    ) == 1

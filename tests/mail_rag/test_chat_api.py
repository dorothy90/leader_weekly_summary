import asyncio

import httpx
import pytest

from app.api.dependencies import ServiceContainer
from app.api.main import create_app
from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ExecutionMetadata,
    FastRAGResult,
    QualityStatus,
    RouteDecision,
)
from app.domain.evidence import Evidence
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.graphs.fast_rag import FastRAGWorkflow
from app.graphs.router import route_request
from app.graphs.deep_research import DeepResearchResult
from app.persistence.conversations import InMemoryConversationStore
from app.observability.node_runs import record_node


class FakeRouter:
    def __init__(self, route="fast", reason_code=None):
        self.route_name = route
        self.reason_code = reason_code or f"model_{route}"
        self.requests = []

    async def route(self, request, conversation=None):
        from app.domain.chat import RouteDecision

        self.requests.append((request, conversation))
        return RouteDecision(
            route=self.route_name,
            reason_code=self.reason_code,
            confidence=1,
            estimated_searches=0 if self.route_name == "clarify" else 1,
        )

    async def contextualize_request(self, request, conversation=None):
        self.requests.append((request, conversation))
        return request.model_copy(update={"message": "대화에서 해석한 독립 질문"})


class FakeFast:
    def __init__(self, result=None, fallback_result=None):
        self.calls = []
        self.result = result or FastRAGResult(
            answer="답변",
            evidence=[],
            quality=QualityStatus(citation_valid=True),
        )
        self.fallback_result = fallback_result or FastRAGResult(
            answer="메일 근거가 없어 일반 안내를 제공합니다.",
            evidence=[],
            quality=QualityStatus(
                citation_valid=None,
                limited_answer=True,
                retrieval_mode="not_used",
            ),
            execution=ExecutionMetadata(status="succeeded"),
        )

    async def invoke(self, request, policy, conversation):
        self.calls.append((request, policy, conversation))
        return self.result

    async def respond_general(self, request, conversation=None):
        self.calls.append((request, None, conversation))
        return self.result

    async def respond_without_evidence(self, request, conversation=None):
        self.calls.append((request, "no_evidence_fallback", conversation))
        return self.fallback_result

    async def contextualize_request(self, request, conversation=None):
        self.calls.append((request, "contextualize", conversation))
        return request.model_copy(update={"message": "대화에서 해석한 독립 질문"})


class FakeDeep:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or DeepResearchResult(
            report="완료된 조사 결과",
            evidence=[],
            completed_sub_questions=1,
            rounds=1,
            citation_valid=True,
        )

    async def invoke(self, question, policy, filters=None):
        self.calls.append((question, policy, filters))
        return self.result


class ASGIClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as session:
                return await session.request(method, path, **kwargs)

        return asyncio.run(send())

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)


def client(router=None, fast=None, deep=None, conversations=None, corpus_info=None):
    return ASGIClient(
        create_app(
            ServiceContainer(
                router=router or FakeRouter(),
                fast=fast or FakeFast(),
                deep=deep,
                conversations=conversations,
                jobs=None,
                corpus_info=corpus_info,
            )
        )
    )


def failed_execution(stage="retrieval", code="RETRIEVAL_TIMEOUT"):
    return ExecutionMetadata(
        status="failed",
        failure_stage=stage,
        error_code=code,
        retryable=True,
        search_count=1,
        evidence_count=0,
        duration_ms=1200,
        include_in_llm_history=False,
    )


def evidence(owner="kim", evidence_id="S1"):
    return Evidence(
        evidence_id=evidence_id,
        source_type="mail",
        document_id="/srv/private/message.json",
        parent_id="parent-secret",
        title="주간 보고",
        excerpt="확인된 사실",
        source_locator="/srv/private/message.json",
        score=1,
        user_id=owner,
        acl_decision_id="acl-secret",
        content_hash="hash-secret",
    )


def test_chat_uses_body_owner_and_returns_trace_without_trusting_headers():
    fast = FakeFast()
    response = client(fast=fast).post(
        "/v1/chat",
        headers={"x-user-id": "lee"},
        json={"user_id": "kim", "message": "질문"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "fast_rag"
    assert len(body["trace_id"]) == 32
    assert fast.calls[0][1].user_id == "kim"


def test_chat_response_includes_request_scoped_router_node_runs():
    class InstrumentedRouter(FakeRouter):
        async def route(self, request, conversation=None):
            async with record_node("router.route"):
                return await super().route(request, conversation)

    response = client(router=InstrumentedRouter("fast")).post(
        "/v1/chat",
        json={"user_id": "kim", "message": "메일 찾아줘"},
    )

    assert response.status_code == 200
    assert response.json()["execution"]["node_runs"][0]["node_name"] == "router.route"


def _no_evidence_result():
    return FastRAGResult(
        answer="검증된 근거만으로 답변을 제공할 수 없습니다.",
        evidence=[],
        quality=QualityStatus(
            citation_valid=None,
            limited_answer=True,
            retrieval_mode="hybrid",
        ),
        execution=ExecutionMetadata(
            status="limited",
            failure_stage="retrieval",
            error_code="NO_EVIDENCE",
            search_count=1,
            evidence_count=0,
            duration_ms=100,
            include_in_llm_history=False,
        ),
    )


def test_auto_fast_no_evidence_uses_labelled_general_fallback():
    conversations = InMemoryConversationStore()
    fast = FakeFast(
        _no_evidence_result(),
        fallback_result=FastRAGResult(
            answer="장례식장을 선택할 때는 위치와 시설을 확인하세요.",
            evidence=[],
            quality=QualityStatus(
                citation_valid=None,
                limited_answer=True,
                retrieval_mode="not_used",
            ),
            execution=ExecutionMetadata(status="succeeded", duration_ms=400),
        ),
    )

    response = client(
        router=FakeRouter("fast"), fast=fast, conversations=conversations
    ).post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "장례식장 정보 알려줘",
            "response_mode": "auto",
        },
    )

    body = response.json()
    assert body["routing"]["route"] == "fast"
    assert body["routing"]["executed_system"] == "fast_rag"
    assert body["execution"]["status"] == "limited"
    assert body["execution"]["error_code"] == "NO_EVIDENCE"
    assert body["execution"]["search_count"] == 1
    assert body["execution"]["evidence_count"] == 0
    assert body["execution"]["duration_ms"] == 500
    assert body["answer"] == "장례식장을 선택할 때는 위치와 시설을 확인하세요."
    assert any("메일" in item and "일반" in item for item in body["disclosures"])
    assert fast.calls[1][1] == "no_evidence_fallback"
    memory = asyncio.run(
        conversations.load(
            body["conversation_id"], PolicyContext.from_user_id("kim")
        )
    )
    assert memory.messages == [
        {"role": "user", "content": "장례식장 정보 알려줘"},
        {
            "role": "assistant",
            "content": "장례식장을 선택할 때는 위치와 시설을 확인하세요.",
        },
    ]
    assert memory.turns[-1].execution.status == "limited"


def test_explicit_fast_no_evidence_remains_grounded_only():
    fast = FakeFast(_no_evidence_result())

    response = client(router=FakeRouter("fast"), fast=fast).post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "장례식장 정보 알려줘",
            "response_mode": "fast",
        },
    )

    assert response.json()["answer"] == "검증된 근거만으로 답변을 제공할 수 없습니다."
    assert len(fast.calls) == 1


def test_failed_fast_execution_never_uses_general_fallback():
    fast = FakeFast(
        FastRAGResult(
            answer="검색 실패",
            evidence=[],
            quality=QualityStatus(
                citation_valid=None,
                limited_answer=True,
                retrieval_mode="not_started",
            ),
            execution=failed_execution(),
        )
    )

    response = client(router=FakeRouter("fast"), fast=fast).post(
        "/v1/chat",
        json={"user_id": "kim", "message": "장례식장 정보 알려줘"},
    )

    assert response.json()["answer"] is None
    assert len(fast.calls) == 1


@pytest.mark.parametrize(
    (
        "requested_mode",
        "route",
        "executed_system",
        "expected_status",
        "estimated_searches",
    ),
    [
        ("auto", "general", "general", 200, 1),
        ("fast", "fast", "fast_rag", 200, 1),
        ("deep", "deep", "deep_research", 200, 1),
        ("auto", "clarify", "clarification", 200, 0),
    ],
)
def test_chat_returns_authoritative_routing_diagnostics(
    requested_mode,
    route,
    executed_system,
    expected_status,
    estimated_searches,
):
    response = client(router=FakeRouter(route), deep=FakeDeep()).post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "hi" if route == "general" else "질문",
            "response_mode": requested_mode,
        },
    )

    assert response.status_code == expected_status
    assert response.json()["routing"] == {
        "requested_mode": requested_mode,
        "route": route,
        "executed_system": executed_system,
        "reason_code": f"model_{route}",
        "confidence": 1.0,
        "estimated_searches": estimated_searches,
        "context_used": False,
        "history_message_count": 0,
        "history_trimmed": False,
    }
    if route in {"general", "clarify"}:
        assert response.json()["quality"]["retrieval_mode"] == "not_used"


def test_unseen_personal_statement_executes_general_without_retrieval():
    class GeneralRouter:
        async def route(self, request, conversation=None):
            return RouteDecision(
                route="general",
                reason_code="model_general",
                confidence=0.99,
                estimated_searches=0,
            )

    fast = FakeFast(
        FastRAGResult(
            answer="이름을 기억할게요.",
            evidence=[],
            quality=QualityStatus(
                citation_valid=True,
                retrieval_mode="not_used",
            ),
        )
    )
    response = client(router=GeneralRouter(), fast=fast).post(
        "/v1/chat",
        json={"user_id": "kim", "message": "내 이름은 대환"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["routing"]["route"] == "general"
    assert body["routing"]["executed_system"] == "general"
    assert body["quality"]["retrieval_mode"] == "not_used"
    assert fast.calls[0][1:] == (None, None)


def test_follow_up_memory_is_shared_with_router_and_general():
    conversations = InMemoryConversationStore()
    router = FakeRouter("general")
    fast = FakeFast(
        FastRAGResult(
            answer="대환이라고 하셨습니다.",
            evidence=[],
            quality=QualityStatus(citation_valid=True, retrieval_mode="not_used"),
        )
    )
    session = client(router=router, fast=fast, conversations=conversations)

    first = session.post(
        "/v1/chat", json={"user_id": "kim", "message": "내 이름은 대환"}
    )
    second = session.post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "내 이름이 뭐라고 했지?",
            "conversation_id": first.json()["conversation_id"],
        },
    )

    assert second.status_code == 200
    router_memory = router.requests[1][1]
    general_memory = fast.calls[1][2]
    assert router_memory.messages[-2:] == [
        {"role": "user", "content": "내 이름은 대환"},
        {"role": "assistant", "content": "대환이라고 하셨습니다."},
    ]
    assert general_memory == router_memory
    assert second.json()["routing"]["context_used"] is True
    assert second.json()["routing"]["history_message_count"] == 2


def test_failed_turn_is_diagnosable_and_preserves_user_context():
    conversations = InMemoryConversationStore()
    router = FakeRouter("fast")
    fast = FakeFast(
        FastRAGResult(
            answer="검색 시간이 초과되었습니다.",
            evidence=[],
            quality=QualityStatus(
                citation_valid=None, limited_answer=True, retrieval_mode="not_started"
            ),
            execution=failed_execution(),
        )
    )
    session = client(router=router, fast=fast, conversations=conversations)

    first = session.post("/v1/chat", json={"user_id": "kim", "message": "검색해줘"})
    assert first.status_code == 200
    assert first.json()["answer"] is None
    assert first.json()["execution"]["status"] == "failed"
    memory = asyncio.run(
        conversations.load(
            first.json()["conversation_id"], PolicyContext.from_user_id("kim")
        )
    )
    assert memory.messages == [{"role": "user", "content": "검색해줘"}]
    assert memory.turns[-1].assistant_content is None
    assert memory.turns[-1].execution.error_code == "RETRIEVAL_TIMEOUT"


def test_diagnostic_route_reports_last_typed_failure_without_model_or_search():
    conversations = InMemoryConversationStore()
    first = client(
        router=FakeRouter("fast"),
        fast=FakeFast(
            FastRAGResult(
                answer="시간 초과",
                quality=QualityStatus(
                    citation_valid=None,
                    limited_answer=True,
                    retrieval_mode="not_started",
                ),
                execution=failed_execution(),
            )
        ),
        conversations=conversations,
    ).post("/v1/chat", json={"user_id": "kim", "message": "검색해줘"})

    response = client(
        router=FakeRouter("diagnostic"), conversations=conversations
    ).post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "왜 답변을 못했어?",
            "conversation_id": first.json()["conversation_id"],
        },
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "diagnostic"
    assert "retrieval" in response.json()["answer"]
    assert "RETRIEVAL_TIMEOUT" in response.json()["answer"]
    assert response.json()["execution"]["status"] == "succeeded"


def test_corpus_info_route_returns_owner_scoped_aggregate_summary():
    class FakeCorpusInfo:
        async def inspect(self, policy):
            assert policy.user_id == "kim"
            from app.retrieval.corpus_info import CorpusInfo

            return CorpusInfo(
                document_count=7,
                teams={"etch": 4},
                first_week="2026-30",
                last_week="2026-31",
                mail_types={"weekly": 7},
                embedding_models=["embedding-v1"],
                recent_titles=["최근 주간 보고"],
            )

    response = client(
        router=FakeRouter("corpus_info"), corpus_info=FakeCorpusInfo()
    ).post("/v1/chat", json={"user_id": "kim", "message": "뭐가 임베딩돼 있어?"})

    assert response.status_code == 200
    assert response.json()["mode"] == "corpus_info"
    assert "7개" in response.json()["answer"]
    assert "2026-30 ~ 2026-31" in response.json()["answer"]


def test_real_router_and_general_workflow_recall_name_across_api_turns():
    class MemoryAwareLLM:
        async def complete_model(self, system, user, schema):
            return RouteDecision(
                route="general",
                reason_code="conversation",
                confidence=1,
                estimated_searches=0,
            )

        async def complete_messages_model(self, system, messages, schema):
            return await self.complete_model(system, messages[-1]["content"], schema)

        async def complete_text(self, system, user):
            return "기억하겠습니다."

        async def complete_messages(self, system, messages):
            if messages[-1]["content"] == "내 이름이 뭐라고 했지?":
                assert any("내 이름은 대환" in item["content"] for item in messages)
                return "대환님이라고 하셨습니다."
            if messages[-1]["content"] == "다시 말해줘":
                assert any(
                    "대환님이라고 하셨습니다" in item["content"]
                    for item in messages
                )
                return "이름은 대환님입니다."
            return "기억하겠습니다."

    llm = MemoryAwareLLM()

    class ActualRouter:
        async def route(self, request, conversation=None):
            return await route_request(request, llm, conversation)

        async def contextualize_request(self, request, conversation=None):
            return request

    session = client(
        router=ActualRouter(),
        fast=FastRAGWorkflow(None, llm),
        conversations=InMemoryConversationStore(),
    )
    first = session.post(
        "/v1/chat", json={"user_id": "kim", "message": "내 이름은 대환"}
    )
    second = session.post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "내 이름이 뭐라고 했지?",
            "conversation_id": first.json()["conversation_id"],
        },
    )
    third = session.post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "다시 말해줘",
            "conversation_id": first.json()["conversation_id"],
        },
    )

    assert second.status_code == 200
    assert second.json()["routing"]["route"] == "general"
    assert second.json()["answer"] == "대환님이라고 하셨습니다."
    assert third.status_code == 200
    assert third.json()["answer"] == "이름은 대환님입니다."
    assert third.json()["routing"]["history_message_count"] == 4


def test_new_conversation_for_same_owner_does_not_receive_other_thread_history():
    conversations = InMemoryConversationStore()
    router = FakeRouter("general")
    session = client(
        router=router,
        fast=FakeFast(),
        conversations=conversations,
    )

    first = session.post(
        "/v1/chat", json={"user_id": "kim", "message": "첫 대화의 비밀"}
    )
    separate = session.post(
        "/v1/chat", json={"user_id": "kim", "message": "새 대화"}
    )

    assert first.json()["conversation_id"] != separate.json()["conversation_id"]
    assert router.requests[1][1] is None
    assert separate.json()["routing"]["context_used"] is False


def test_deep_follow_up_is_contextualized_before_synchronous_execution():
    class SequentialRouter(FakeRouter):
        async def route(self, request, conversation=None):
            self.route_name = "general" if not self.requests else "deep"
            return await super().route(request, conversation)

    conversations = InMemoryConversationStore()
    fast = FakeFast()
    session = client(
        router=SequentialRouter("general"),
        fast=fast,
        deep=FakeDeep(),
        conversations=conversations,
    )
    first = session.post(
        "/v1/chat",
        json={"user_id": "kim", "message": "지난 4주 수율 이슈를 분석해줘"},
    )
    response = session.post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "그걸 보고서로 만들어줘",
            "conversation_id": first.json()["conversation_id"],
        },
    )

    assert response.status_code == 200
    deep = session.app.state.container.deep
    assert deep.calls[0][0] == "대화에서 해석한 독립 질문"
    assert response.json()["answer"] == "완료된 조사 결과"
    assert response.json()["job_id"] is None
    assert len(fast.calls) == 1
    assert fast.calls[0][1:] == (None, None)


@pytest.mark.parametrize(
    "unsafe_reason",
    ["Explain the user's intent in detail", "x" * 500],
)
def test_chat_maps_unsafe_model_reason_to_bounded_server_code(unsafe_reason):
    response = client(router=FakeRouter("fast", unsafe_reason)).post(
        "/v1/chat",
        json={"user_id": "kim", "message": "질문"},
    )

    assert response.status_code == 200
    assert response.json()["routing"]["reason_code"] == "model_fast"


def test_mongo_failure_degrades_to_single_turn_with_context_disclosure():
    class BrokenConversations:
        async def load(self, *_args):
            raise ConnectionError("mongo down")

        async def save(self, *_args):
            raise ConnectionError("mongo down")

    response = client(conversations=BrokenConversations()).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문", "conversation_id": "c1"}
    )
    assert response.status_code == 200
    assert response.json()["disclosures"] == [
        "대화 저장소를 사용할 수 없어 이번 요청은 단일 턴으로 처리했습니다."
    ]


def test_foreign_and_missing_supplied_conversation_ids_are_indistinguishable():
    conversations = InMemoryConversationStore()
    owner_client = client(conversations=conversations)
    created = owner_client.post(
        "/v1/chat", json={"user_id": "kim", "message": "첫 질문"}
    ).json()

    foreign = owner_client.post(
        "/v1/chat",
        json={
            "user_id": "lee",
            "message": "탈취 시도",
            "conversation_id": created["conversation_id"],
        },
    )
    missing = owner_client.post(
        "/v1/chat",
        json={
            "user_id": "lee",
            "message": "존재 확인",
            "conversation_id": "missing-id",
        },
    )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]
    assert created["conversation_id"] not in str(foreign.json())


def test_deep_route_returns_final_answer_in_the_same_chat_request():
    router = FakeRouter("deep")
    fast = FakeFast()
    deep = FakeDeep()

    response = client(router=router, fast=fast, deep=deep).post(
        "/v1/chat", json={"user_id": "kim", "message": "12주 추세"}
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "deep_research"
    assert response.json()["answer"] == "완료된 조사 결과"
    assert response.json()["job_id"] is None
    assert response.json()["status"] is None
    assert response.json()["quality"]["citation_valid"] is None
    assert len(deep.calls) == 1
    assert fast.calls == []


def test_deep_route_without_workflow_returns_safe_dependency_error():
    fast = FakeFast()
    response = client(router=FakeRouter("deep"), fast=fast).post(
        "/v1/chat", json={"user_id": "kim", "message": "보고서"}
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "요청한 서비스를 현재 사용할 수 없습니다.",
        "retryable": True,
    }
    assert fast.calls == []


def test_deep_execution_failure_returns_typed_result_and_preserves_user_context():
    class FailedDeep:
        async def invoke(self, *_args, **_kwargs):
            await asyncio.sleep(0.002)
            raise AppError(
                ErrorCode.INDEX_UNAVAILABLE,
                "검색 인덱스를 사용할 수 없습니다.",
                retryable=True,
            )

    conversations = InMemoryConversationStore()
    response = client(
        router=FakeRouter("deep"),
        deep=FailedDeep(),
        conversations=conversations,
    ).post("/v1/chat", json={"user_id": "kim", "message": "4주 조사"})

    assert response.status_code == 200
    assert response.json()["answer"] is None
    assert response.json()["execution"]["status"] == "failed"
    assert response.json()["execution"]["failure_stage"] == "retrieval"
    assert response.json()["execution"]["error_code"] == "INDEX_UNAVAILABLE"
    assert response.json()["execution"]["duration_ms"] >= 1
    memory = asyncio.run(
        conversations.load(
            response.json()["conversation_id"], PolicyContext.from_user_id("kim")
        )
    )
    assert memory.messages == [{"role": "user", "content": "4주 조사"}]


def test_api_only_returns_cited_same_owner_safe_reference_fields():
    result = FastRAGResult(
        answer="확인된 사실 [S1]",
        evidence=[evidence("kim", "S1"), evidence("lee", "S2")],
        quality=QualityStatus(citation_valid=True),
    )
    response = client(fast=FakeFast(result)).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    assert response.status_code == 200
    references = response.json()["references"]
    assert len(references) == 1
    assert set(references[0]) == {
        "evidence_id",
        "source_type",
        "document_id",
        "title",
        "excerpt",
        "team",
        "week",
    }
    assert "/srv" not in str(references)
    assert "lee" not in str(response.json())


def test_api_canonicalizes_known_model_citation_variant():
    result = FastRAGResult(
        answer="확인된 사실 【s1】",
        evidence=[evidence("kim", "S1")],
        quality=QualityStatus(citation_valid=True),
    )

    response = client(fast=FakeFast(result)).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "확인된 사실 [S1]"
    assert [item["evidence_id"] for item in response.json()["references"]] == ["S1"]
    assert response.json()["quality"]["citation_valid"] is True


def test_api_redacts_analysis_reasoning_and_naked_credential_tokens():
    unsafe = evidence("kim", "S1").model_copy(
        update={
            "excerpt": "<analysis>private reasoning</analysis> fact ghp_abc123",
            "title": "Reasoning: hidden steps",
        }
    )
    result = FastRAGResult(
        answer="<analysis>secret thought</analysis> fact sk-proj-abc [S1]",
        evidence=[unsafe],
        quality=QualityStatus(citation_valid=True),
        disclosures=["Reasoning: hidden ghp_secret"],
    )
    response = client(fast=FakeFast(result)).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    serialized = str(response.json()).casefold()
    assert response.status_code == 200
    for secret in (
        "<analysis>",
        "private reasoning",
        "secret thought",
        "ghp_",
        "sk-proj",
        "hidden steps",
    ):
        assert secret not in serialized


def test_api_rejects_unclosed_analysis_and_multiline_reasoning_fields():
    unsafe = evidence("kim", "S1").model_copy(
        update={"excerpt": "<analysis>unclosed private rationale"}
    )
    result = FastRAGResult(
        answer="Reasoning: private rationale\nsecret step\nFinal [S1]",
        evidence=[unsafe],
        quality=QualityStatus(citation_valid=True),
    )
    response = client(fast=FakeFast(result)).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    serialized = str(response.json()).casefold()
    for secret in ("private rationale", "secret step", "<analysis>"):
        assert secret not in serialized


def test_deep_response_redacts_unsafe_report_content():
    deep = FakeDeep(
        DeepResearchResult(
            report="<analysis>private</analysis> password=hunter2",
            evidence=[],
            completed_sub_questions=1,
            rounds=1,
            citation_valid=True,
        )
    )
    response = client(router=FakeRouter("deep"), deep=deep).post(
        "/v1/chat", json={"user_id": "kim", "message": "보고서"}
    )

    assert response.status_code == 200
    assert "private" not in str(response.json())
    assert "hunter2" not in str(response.json())


def test_invalid_citations_fail_closed_at_the_api_boundary():
    result = FastRAGResult(
        answer="검증되지 않은 답 [S9]",
        evidence=[evidence("kim", "S1")],
        quality=QualityStatus(citation_valid=True),
    )
    response = client(fast=FakeFast(result)).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    assert response.status_code == 200
    assert response.json()["references"] == []
    assert response.json()["quality"]["citation_valid"] is False
    assert "검증되지 않은 답" not in response.json()["answer"]


def test_invalid_citation_persists_no_uncited_owned_evidence():
    conversations = InMemoryConversationStore()
    unsafe = evidence("kim", "S1").model_copy(
        update={
            "document_id": "/srv/private/password.txt",
            "excerpt": "password=hunter2 <analysis>private thought</analysis>",
        }
    )
    result = FastRAGResult(
        answer="검증되지 않은 답 [S9]",
        evidence=[unsafe],
        quality=QualityStatus(citation_valid=True),
    )

    response = client(fast=FakeFast(result), conversations=conversations).post(
        "/v1/chat",
        json={"user_id": "kim", "message": "질문"},
    )
    memory = asyncio.run(
        conversations.load(
            response.json()["conversation_id"],
            PolicyContext.from_user_id("kim"),
        )
    )
    stored = conversations.records[response.json()["conversation_id"]][1]

    assert response.json()["references"] == []
    assert memory.cited_evidence == []
    assert stored.cited_evidence == []


def test_valid_citation_persists_same_safe_evidence_and_sanitized_filters():
    conversations = InMemoryConversationStore()
    unsafe = evidence("kim", "S1").model_copy(
        update={
            "document_id": "/srv/private/password.txt",
            "parent_id": "/srv/private/parent.json",
            "title": "password=hunter2",
            "excerpt": "<analysis>private thought</analysis> 확인된 사실",
            "team": "/srv/private/team",
            "source_locator": "/srv/private/message.json",
            "acl_decision_id": "password=hunter2",
            "content_hash": "/srv/private/hash",
        }
    )
    result = FastRAGResult(
        answer="확인된 사실 [S1]",
        evidence=[unsafe, evidence("kim", "S2")],
        quality=QualityStatus(citation_valid=True),
    )

    response = client(fast=FakeFast(result), conversations=conversations).post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "질문",
            "filters": {
                "teams": [
                    "/srv/private/team",
                    "password=hunter2",
                    "<analysis>private filter</analysis>",
                    "YIELD팀",
                ],
                "weeks": ["2026-08"],
                "mail_type": "weekly_report",
            },
        },
    )
    memory = asyncio.run(
        conversations.load(
            response.json()["conversation_id"],
            PolicyContext.from_user_id("kim"),
        )
    )
    stored = conversations.records[response.json()["conversation_id"]][1]

    assert [item.evidence_id for item in memory.cited_evidence] == ["S1"]
    persisted = memory.cited_evidence[0]
    reference = response.json()["references"][0]
    assert persisted.document_id == reference["document_id"]
    assert persisted.title == reference["title"]
    assert persisted.excerpt == reference["excerpt"]
    assert persisted.team == reference["team"]
    assert memory.filters.teams == ["[REDACTED_PATH]", "[REDACTED]", "YIELD팀"]
    assert memory.filters.weeks == ["2026-08"]
    assert memory.filters.mail_type == "weekly_report"
    assert stored == memory
    serialized = str(stored.model_dump(mode="json")).casefold()
    for secret in ("/srv/private", "hunter2", "private thought", "private filter"):
        assert secret not in serialized


def test_response_and_memory_share_citation_order_capped_at_eight():
    conversations = InMemoryConversationStore()
    all_evidence = [evidence("kim", f"S{index}") for index in range(10, 0, -1)]
    result = FastRAGResult(
        answer=" ".join(f"확인 [S{index}]" for index in range(1, 11)),
        evidence=all_evidence,
        quality=QualityStatus(citation_valid=True),
    )

    response = client(fast=FakeFast(result), conversations=conversations).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )
    memory = asyncio.run(
        conversations.load(
            response.json()["conversation_id"],
            PolicyContext.from_user_id("kim"),
        )
    )

    expected = [f"S{index}" for index in range(1, 9)]
    assert [item["evidence_id"] for item in response.json()["references"]] == expected
    assert [item.evidence_id for item in memory.cited_evidence] == expected


def test_foreign_only_evidence_cannot_support_or_escape_in_an_answer():
    result = FastRAGResult(
        answer="타인 소유의 사실 password=hunter2 [S2]",
        evidence=[evidence("lee", "S2")],
        quality=QualityStatus(citation_valid=True),
    )
    response = client(fast=FakeFast(result)).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    assert response.status_code == 200
    assert response.json()["references"] == []
    assert response.json()["quality"]["citation_valid"] is False
    assert "타인" not in response.json()["answer"]
    assert "hunter2" not in str(response.json())


def test_citation_without_any_evidence_is_not_returned():
    result = FastRAGResult(
        answer="근거 없는 답 [S9]",
        evidence=[],
        quality=QualityStatus(citation_valid=True),
    )
    response = client(fast=FakeFast(result)).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    assert response.status_code == 200
    assert response.json()["quality"]["citation_valid"] is False
    assert "근거 없는 답" not in response.json()["answer"]


def test_invalid_conversation_id_is_rejected_as_a_safe_validation_error():
    response = client(conversations=InMemoryConversationStore()).post(
        "/v1/chat",
        json={
            "user_id": "kim",
            "message": "질문",
            "conversation_id": "../private/path",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "private/path" not in str(response.json())


def test_exact_bm25_fallback_disclosure_is_preserved_once_and_sanitized():
    result = FastRAGResult(
        answer="제한된 답변",
        evidence=[],
        quality=QualityStatus(
            citation_valid=True, limited_answer=True, retrieval_mode="bm25"
        ),
        disclosures=[BM25_FALLBACK_DISCLOSURE, BM25_FALLBACK_DISCLOSURE],
    )
    response = client(fast=FakeFast(result)).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    assert response.json()["disclosures"] == [BM25_FALLBACK_DISCLOSURE]


def test_app_and_validation_errors_have_structured_safe_responses():
    class BrokenRouter:
        async def route(self, request, conversation=None):
            raise AppError(
                ErrorCode.INDEX_UNAVAILABLE,
                "/srv/private index password=hunter2",
                retryable=True,
            )

    invalid = client().post("/v1/chat", json={"user_id": "kim"})
    broken = client(router=BrokenRouter()).post(
        "/v1/chat", json={"user_id": "kim", "message": "질문"}
    )

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert "field required" not in str(invalid.json()).lower()
    assert "input" not in str(invalid.json()).lower()
    assert broken.status_code == 503
    assert broken.json()["error"]["code"] == "INDEX_UNAVAILABLE"
    assert "/srv" not in str(broken.json())
    assert "hunter2" not in str(broken.json())


def test_health_endpoint_is_dependency_free():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

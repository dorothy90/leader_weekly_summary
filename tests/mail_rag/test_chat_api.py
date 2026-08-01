from dataclasses import dataclass
import asyncio

import httpx

from app.api.dependencies import ServiceContainer
from app.api.main import create_app
from app.domain.chat import BM25_FALLBACK_DISCLOSURE, FastRAGResult, QualityStatus
from app.domain.evidence import Evidence
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.domain.research import ResearchStatus
from app.persistence.conversations import InMemoryConversationStore


class FakeRouter:
    def __init__(self, route="fast"):
        self.route_name = route
        self.requests = []

    async def route(self, request):
        from app.domain.chat import RouteDecision

        self.requests.append(request)
        return RouteDecision(
            route=self.route_name,
            reason_code="test",
            confidence=1,
            estimated_searches=1,
        )


class FakeFast:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or FastRAGResult(
            answer="답변",
            evidence=[],
            quality=QualityStatus(citation_valid=True),
        )

    async def invoke(self, request, policy, conversation):
        self.calls.append((request, policy, conversation))
        return self.result

    async def respond_general(self, request):
        self.calls.append((request, None, None))
        return self.result


@dataclass
class FakeJob:
    job_id: str = "research_1"
    status: ResearchStatus = ResearchStatus.QUEUED
    plan_summary: str = "두 팀 비교"


class FakeDeep:
    def __init__(self, job=None):
        self.calls = []
        self.job = job or FakeJob()

    async def enqueue(self, request, policy, trace_id):
        self.calls.append((request, policy, trace_id))
        return self.job


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


def client(router=None, fast=None, deep=None, conversations=None):
    return ASGIClient(
        create_app(
            ServiceContainer(
                router=router or FakeRouter(),
                fast=fast or FakeFast(),
                deep=deep,
                conversations=conversations,
                jobs=None,
            )
        )
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


def test_deep_route_is_an_explicit_async_handoff_and_never_calls_fast():
    router = FakeRouter("deep")
    fast = FakeFast()
    deep = FakeDeep()

    response = client(router=router, fast=fast, deep=deep).post(
        "/v1/chat", json={"user_id": "kim", "message": "12주 추세"}
    )

    assert response.status_code == 202
    assert response.json()["mode"] == "deep_research"
    assert response.json()["job_id"] == "research_1"
    assert len(deep.calls) == 1
    assert fast.calls == []


def test_deep_route_without_coordinator_returns_safe_dependency_error():
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


def test_deep_handoff_rejects_unrecognized_status_without_exposing_it():
    deep = FakeDeep(FakeJob(status="/srv/private password=hunter2"))
    response = client(router=FakeRouter("deep"), deep=deep).post(
        "/v1/chat", json={"user_id": "kim", "message": "보고서"}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "/srv" not in str(response.json())
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
        async def route(self, request):
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

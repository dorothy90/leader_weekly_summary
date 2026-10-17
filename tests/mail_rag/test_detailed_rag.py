import re
import asyncio
import pytest
from app.graphs.detailed_rag import DetailedRAGWorkflow, DetailedPlan, RankingDecision
from app.domain.chat import ChatRequest
from app.domain.quick_rag import QuickConditions
from app.domain.policy import PolicyContext
from app.retrieval.quick_rag import QuickDocument, QuickSearchOutcome


class Search:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.peak = 0

    def capabilities(self):
        return {}

    async def search(self, plan, policy, **kwargs):
        self.calls.append((plan.model_copy(deep=True), policy, kwargs))
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.005)
        self.active -= 1
        return QuickSearchOutcome(
            documents=[
                QuickDocument(
                    source="ews_mail",
                    id=plan.query or "1",
                    original_id=plan.query or "1",
                    title=plan.query,
                    text="검증용 근거",
                )
            ],
            searched_sources=["ews_mail"],
            resolved_plan=plan,
        )


class Model:
    def __init__(self, plan, ranking=None):
        self.plan = plan
        self.ranking = ranking
        self.calls = []

    async def complete_model(self, system, user, schema):
        if schema.__name__ == "GroundedAnswer":
            answer = await self.complete_text(system, user)
            return schema(
                evidence_status="sufficient",
                answer=answer,
                evidence_ids=re.findall(r"\[(S\d+)\]", answer),
            )
        self.calls.append(schema.__name__)
        if schema is DetailedPlan:
            return self.plan
        if isinstance(self.ranking, Exception):
            raise self.ranking
        return self.ranking or RankingDecision(ranked_ids=["D1", "D2", "D3"])

    async def complete_text(self, *args):
        self.calls.append("answer")
        return "근거 설명 [S1]"


@pytest.mark.asyncio
async def test_detailed_parallel_and_at_most_one_supplement_same_constraints():
    search = Search()
    model = Model(
        DetailedPlan(
            query="base",
            subqueries=["A", "B", "C"],
            source_scope="mail",
            conditions=QuickConditions(teams=["team"]),
        ),
        RankingDecision(
            ranked_ids=["D1", "D2", "D3"], needs_more=True, supplemental_query="extra"
        ),
    )
    result = await DetailedRAGWorkflow(search, model, model, model).invoke(
        ChatRequest(user_id="alice", message="비교"),
        PolicyContext.from_user_id("alice"),
    )
    assert len(search.calls) == 4 and search.peak == 3
    assert all(
        p.conditions.teams == ["team"]
        and p.source_scope == "mail"
        and policy.user_id == "alice"
        for p, policy, k in search.calls
    )
    assert model.calls == ["DetailedPlan", "RankingDecision", "answer"]
    assert result.execution.additional_search_count == 1
    assert result.execution.mode == "detailed"
    assert result.quality.citation_valid


@pytest.mark.asyncio
async def test_lists_do_not_expand_or_rerank():
    search = Search()
    model = Model(DetailedPlan(intent="list", query="A", subqueries=["B", "C"]))
    result = await DetailedRAGWorkflow(search, model, model, model).invoke(
        ChatRequest(user_id="alice", message="목록"),
        PolicyContext.from_user_id("alice"),
    )
    assert len(search.calls) == 1 and model.calls == ["DetailedPlan"]
    assert result.execution.additional_search_count == 0


@pytest.mark.asyncio
async def test_invalid_ranking_preserves_retrieval_and_does_not_expand():
    search = Search()
    model = Model(
        DetailedPlan(query="A"),
        RankingDecision(
            ranked_ids=["D99"], needs_more=True, supplemental_query="extra"
        ),
    )
    result = await DetailedRAGWorkflow(search, model, model, model).invoke(
        ChatRequest(user_id="alice", message="설명"),
        PolicyContext.from_user_id("alice"),
    )
    assert len(search.calls) == 1 and result.quality.citation_valid
    assert any("재정렬" in d for d in result.disclosures)


@pytest.mark.asyncio
async def test_subquery_cannot_change_owner_or_reintroduce_filters():
    search = Search()
    model = Model(
        DetailedPlan(
            query="A",
            subqueries=["B", "C"],
            conditions=QuickConditions(teams=["wrong"]),
        )
    )
    request = ChatRequest(
        user_id="alice", message="비교", filters={"teams": ["server"]}
    )
    await DetailedRAGWorkflow(search, model, model, model).invoke(
        request, PolicyContext.from_user_id("alice")
    )
    assert all(p.conditions.teams == ["server"] for p, _, _ in search.calls)


@pytest.mark.asyncio
async def test_within_results_refetch_preserves_ids_on_every_task():
    from app.domain.quick_rag import QuickMemory, QuickPlan, QuickResultRef
    from app.persistence.conversations import ConversationMemory

    state = ConversationMemory(
        quick_memory=QuickMemory(
            user_id="alice",
            plan=QuickPlan(query="prior"),
            result_ids=[QuickResultRef(source="ews_mail", id="allowed")],
        )
    )
    search = Search()
    model = Model(
        DetailedPlan(scope="within_results", query="new", subqueries=["A", "B"]),
        RankingDecision(
            ranked_ids=["D1", "D2"], needs_more=True, supplemental_query="extra"
        ),
    )
    await DetailedRAGWorkflow(search, model, model, model).invoke(
        ChatRequest(user_id="alice", message="그중 비교"),
        PolicyContext.from_user_id("alice"),
        state,
    )
    assert len(search.calls) == 4
    assert all(
        p.scope == "within_results" and kw["prior_ids"] == state.quick_memory.result_ids
        for p, _, kw in search.calls
    )


@pytest.mark.asyncio
async def test_source_failure_does_not_trigger_supplemental_search():
    class Partial(Search):
        async def search(self, *args, **kw):
            result = await super().search(*args, **kw)
            result.failed_sources = ["wiki_summary"]
            result.disclosures = ["위키 검색 실패"]
            return result

    search = Partial()
    model = Model(
        DetailedPlan(query="A"),
        RankingDecision(ranked_ids=["D1"], needs_more=True, supplemental_query="extra"),
    )
    result = await DetailedRAGWorkflow(search, model, model, model).invoke(
        ChatRequest(user_id="alice", message="설명"),
        PolicyContext.from_user_id("alice"),
    )
    assert len(search.calls) == 1 and result.execution.additional_search_count == 0
    assert result.quality.citation_valid


@pytest.mark.asyncio
async def test_ranker_timeout_keeps_successful_evidence():
    class Slow(Model):
        async def complete_model(self, system, user, schema):
            if schema is RankingDecision:
                await asyncio.sleep(1)
            return await super().complete_model(system, user, schema)

    search = Search()
    model = Slow(DetailedPlan(query="A"))
    result = await DetailedRAGWorkflow(
        search, model, model, model, rerank_timeout_seconds=0.01
    ).invoke(
        ChatRequest(user_id="alice", message="설명"),
        PolicyContext.from_user_id("alice"),
    )
    assert (
        result.quality.citation_valid and result.execution.additional_search_count == 0
    )
    assert any("재정렬" in d for d in result.disclosures)


@pytest.mark.asyncio
async def test_detailed_endpoint_routing_and_conversation_ownership():
    import httpx
    from app.api.main import create_app
    from app.api.dependencies import ServiceContainer
    from app.persistence.conversations import InMemoryConversationStore
    from app.domain.chat import FastRAGResult, QualityStatus

    class Workflow:
        def __init__(self, label):
            self.label = label
            self.calls = []

        async def invoke(self, request, policy, memory):
            self.calls.append(policy.user_id)
            return FastRAGResult(
                answer=self.label,
                quality=QualityStatus(citation_valid=None, retrieval_mode="not_used"),
            )

    fast = Workflow("fast")
    detailed = Workflow("detailed")
    app = create_app(
        ServiceContainer(
            agentic=fast,
            detailed=detailed,
            conversations=InMemoryConversationStore(),
            jobs=None,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        a = await client.post("/v1/chat", json={"user_id": "alice", "message": "hi"})
        conversation = a.json()["conversation_id"]
        b = await client.post(
            "/v1/chat/detailed",
            json={
                "user_id": "alice",
                "message": "compare",
                "conversation_id": conversation,
            },
        )
        denied = await client.post(
            "/v1/chat/detailed",
            json={
                "user_id": "bob",
                "message": "compare",
                "conversation_id": conversation,
            },
        )
        page = await client.get("/test-ui")
    assert a.json()["answer"] == "fast" and b.json()["answer"] == "detailed"
    assert denied.status_code == 404 and detailed.calls == ["alice"]
    assert (
        page.status_code == 200
        and "/v1/chat/detailed" in page.text
        and 'value="fast"' in page.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model_answer", ["지어낸 결론 [S99]"])
async def test_invalid_answer_returns_verified_sources_without_unsupported_claim(
    model_answer,
):
    class InvalidModel(Model):
        async def complete_text(self, *args):
            self.calls.append("answer")
            return model_answer

    search = Search()
    model = InvalidModel(DetailedPlan(query="base"), RankingDecision(ranked_ids=["D1"]))
    result = await DetailedRAGWorkflow(search, model, model, model).invoke(
        ChatRequest(user_id="alice", message="설명"),
        PolicyContext.from_user_id("alice"),
    )
    assert result.quality.citation_valid
    assert result.quality.limited_answer
    assert "지어낸 결론" not in result.answer
    assert "[S1]" in result.answer
    assert result.quick_memory.result_ids
    assert model.calls.count("answer") == 1
    from app.api.routes.chat import _safe_agent_result

    answer, references, quality, *_ = _safe_agent_result(
        result, PolicyContext.from_user_id("alice")
    )
    assert references and quality.citation_valid
    assert "검증된 근거만으로 답변을 제공할 수 없습니다" not in answer


@pytest.mark.asyncio
async def test_empty_ranking_preserves_initial_sources_and_base_query():
    search = Search()
    model = Model(
        DetailedPlan(query="base", subqueries=["A", "B", "C"]),
        RankingDecision(ranked_ids=[], needs_more=True),
    )
    result = await DetailedRAGWorkflow(search, model, model, model).invoke(
        ChatRequest(user_id="alice", message="설명"),
        PolicyContext.from_user_id("alice"),
    )
    assert any(p.query == "base" for p, _, _ in search.calls)
    assert len(search.calls) <= 3
    assert result.evidence
    assert result.quality.limited_answer

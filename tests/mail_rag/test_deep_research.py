import asyncio
from functools import wraps

import pytest

from app.domain.chat import BM25_FALLBACK_DISCLOSURE
from app.domain.evidence import Evidence
from app.domain.evidence import RetrievalFilters
from app.domain.policy import PolicyContext
from app.graphs.deep_research import (
    MAX_CONCURRENCY,
    MAX_EVIDENCE,
    MAX_MODEL_INPUT_BYTES,
    MAX_REPORT_REVISIONS,
    MAX_REPORT_BYTES,
    MAX_ROUNDS,
    MAX_SEARCHES,
    MAX_SUBQUESTIONS,
    DeepResearchWorkflow,
    _evidence_context,
)
from app.domain.errors import AppError, ErrorCode
from app.retrieval.service import RetrievalResult


def async_test(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapped


def evidence(owner="kim", document_id="doc-1", excerpt="근거"):
    return Evidence(
        evidence_id="raw",
        source_type="mail",
        document_id=document_id,
        title="주간 보고",
        excerpt=excerpt,
        score=1,
        user_id=owner,
        acl_decision_id="acl",
        content_hash="hash",
    )


class DeepLLM:
    def __init__(self, *, sub_questions=None, gaps=None, reports=None):
        self.sub_questions = sub_questions or ["A팀 원인", "B팀 조치"]
        self.gaps = list(gaps or [{"complete": True, "follow_up_questions": []}])
        self.reports = list(reports or ["종합 결과 [S1]"])
        self.report_calls = 0
        self.text_inputs = []

    async def complete_model(self, system, user, schema):
        if schema.__name__ == "ResearchPlan":
            return schema.model_validate({"sub_questions": self.sub_questions})
        value = (
            self.gaps.pop(0)
            if self.gaps
            else {
                "complete": True,
                "follow_up_questions": [],
            }
        )
        return schema.model_validate(value)

    async def complete_text(self, system, user):
        self.text_inputs.append((system, user))
        value = self.reports[min(self.report_calls, len(self.reports) - 1)]
        self.report_calls += 1
        return value


class DeepRetrieval:
    def __init__(self, *, owner="kim", fallback=False, delay=0):
        self.owner = owner
        self.fallback = fallback
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def search(self, task, policy):
        self.calls.append((task, policy))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            item = evidence(self.owner, f"doc-{len(self.calls)}", task.query)
            return RetrievalResult(
                evidence=[item],
                mode="bm25" if self.fallback else "hybrid",
                embedding_error=("EMBEDDING_UNAVAILABLE" if self.fallback else None),
                disclosures=([BM25_FALLBACK_DISCLOSURE] if self.fallback else []),
            )
        finally:
            self.active -= 1


@async_test
async def test_deep_graph_finishes_bounded_parallel_plan():
    retrieval = DeepRetrieval(delay=0.001)
    result = await DeepResearchWorkflow(retrieval, DeepLLM()).invoke(
        "두 팀 비교", PolicyContext.from_user_id("kim")
    )

    assert result.completed_sub_questions == 2
    assert result.rounds <= MAX_ROUNDS
    assert 1 < retrieval.max_active <= MAX_CONCURRENCY
    assert all(call[1].user_id == "kim" for call in retrieval.calls)


@async_test
async def test_deep_graph_hard_caps_plan_gap_searches_and_evidence():
    retrieval = DeepRetrieval()
    llm = DeepLLM(
        sub_questions=[f"초기 {index}" for index in range(MAX_SUBQUESTIONS + 5)],
        gaps=[
            {
                "complete": False,
                "follow_up_questions": [f"후속 {index}" for index in range(20)],
            },
            {"complete": False, "follow_up_questions": ["무시"]},
        ],
    )

    result = await DeepResearchWorkflow(retrieval, llm).invoke(
        "대규모 조사", PolicyContext.from_user_id("kim")
    )

    assert len(retrieval.calls) == MAX_SEARCHES
    assert result.rounds == MAX_ROUNDS
    assert len(result.evidence) <= MAX_EVIDENCE


@async_test
async def test_deep_graph_revises_once_then_fails_closed_to_safe_empty_result():
    llm = DeepLLM(reports=["근거 없는 결과 [S99]", "여전히 잘못됨 [S98]"])
    result = await DeepResearchWorkflow(DeepRetrieval(), llm).invoke(
        "질문", PolicyContext.from_user_id("kim")
    )

    assert MAX_REPORT_REVISIONS == 1
    assert llm.report_calls == MAX_REPORT_REVISIONS + 1
    assert (
        result.report == "조사 근거의 인용을 검증하지 못해 보고서를 제공할 수 없습니다."
    )
    assert result.evidence == []


@async_test
async def test_deep_graph_returns_only_cited_same_owner_sanitized_evidence():
    retrieval = DeepRetrieval()

    async def unsafe_search(task, policy):
        return RetrievalResult(
            evidence=[
                evidence("kim", "/srv/private/one.json", "확인 [S?] password=hunter2"),
                evidence("kim", "doc-2", "미인용 근거"),
                evidence("lee", "foreign", "외부 근거"),
            ],
            mode="hybrid",
        )

    retrieval.search = unsafe_search
    result = await DeepResearchWorkflow(
        retrieval, DeepLLM(sub_questions=["하나"], reports=["확인된 결과 [S1]"])
    ).invoke("질문", PolicyContext.from_user_id("kim"))

    assert [item.evidence_id for item in result.evidence] == ["S1"]
    serialized = result.model_dump_json()
    assert "lee" not in serialized
    assert "/srv" not in serialized
    assert "hunter2" not in serialized


@async_test
async def test_deep_graph_preserves_exact_embedding_fallback_disclosure_once():
    result = await DeepResearchWorkflow(DeepRetrieval(fallback=True), DeepLLM()).invoke(
        "질문", PolicyContext.from_user_id("kim")
    )

    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]
    assert result.report.count(BM25_FALLBACK_DISCLOSURE) == 1


@async_test
async def test_deep_graph_normalizes_repeated_embedding_disclosure_exactly_once():
    repeated = (
        f"결과 [S1]\n\n{BM25_FALLBACK_DISCLOSURE}\n\n" f"{BM25_FALLBACK_DISCLOSURE}"
    )
    result = await DeepResearchWorkflow(
        DeepRetrieval(fallback=True), DeepLLM(reports=[repeated])
    ).invoke("질문", PolicyContext.from_user_id("kim"))

    assert result.report.count(BM25_FALLBACK_DISCLOSURE) == 1
    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]


@async_test
async def test_deep_graph_passes_persisted_filters_to_every_search():
    retrieval = DeepRetrieval()
    filters = RetrievalFilters(
        teams=["YIELD팀"], weeks=["2026-08"], mail_type="weekly_report"
    )
    await DeepResearchWorkflow(retrieval, DeepLLM()).invoke(
        "질문", PolicyContext.from_user_id("kim"), filters
    )

    assert retrieval.calls
    assert all(task.filters == filters for task, _policy in retrieval.calls)


@async_test
async def test_complete_synthesis_input_and_generated_draft_are_hard_bounded():
    llm = DeepLLM(
        reports=[
            "초안 [S99] " + "다" * 20_000,
            "결과 [S1] " + "가" * 20_000,
        ]
    )

    async def large_search(task, policy):
        return RetrievalResult(
            evidence=[evidence("kim", "doc-large", "나" * 8000)], mode="hybrid"
        )

    retrieval = DeepRetrieval()
    retrieval.search = large_search
    result = await DeepResearchWorkflow(retrieval, llm).invoke(
        "질문" * 2000, PolicyContext.from_user_id("kim")
    )

    assert len(llm.text_inputs) == MAX_REPORT_REVISIONS + 1
    assert all(
        len(system.encode("utf-8")) + len(user.encode("utf-8")) <= MAX_MODEL_INPUT_BYTES
        for system, user in llm.text_inputs
    )
    assert len(result.report.encode("utf-8")) <= MAX_REPORT_BYTES


def test_deep_context_budget_is_a_hard_conservative_token_bound():
    items = [
        evidence("kim", f"doc-{index}", "가" * 8000).model_copy(
            update={"evidence_id": f"S{index + 1}"}
        )
        for index in range(MAX_EVIDENCE)
    ]

    context = _evidence_context(items)

    from app.graphs.deep_research import MAX_CONTEXT_TOKENS

    assert len(context.encode("utf-8")) <= MAX_CONTEXT_TOKENS


@async_test
async def test_deep_graph_enforces_elapsed_time_budget(monkeypatch):
    monkeypatch.setattr("app.graphs.deep_research.MAX_ELAPSED_SECONDS", 0)

    with pytest.raises(AppError) as error:
        await DeepResearchWorkflow(DeepRetrieval(), DeepLLM()).invoke(
            "질문", PolicyContext.from_user_id("kim")
        )

    assert error.value.code == ErrorCode.BUDGET_EXCEEDED

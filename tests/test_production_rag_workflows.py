import asyncio

from app.api.routes.chat import _safe_fast_result
from app.api.routes.research import _response
from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ChatRequest,
    FastRAGResult,
    QualityStatus,
)
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.graphs.deep_research import (
    BranchResult,
    DeepResearchResult,
    DeepResearchWorkflow,
)
from app.graphs.fast_rag import FastRAGWorkflow
from app.llm.answer_format import (
    ensure_rag_answer_structure,
    prepend_summary_notice,
)
from app.persistence.research_jobs import InMemoryResearchJobStore


HEADINGS = ("### 요약", "### 상세설명", "### 핵심결론")


def assert_section_contract(answer: str) -> None:
    h3_headings = tuple(
        line for line in answer.splitlines() if line.startswith("### ")
    )
    assert h3_headings == HEADINGS


def make_evidence(policy: PolicyContext) -> Evidence:
    return Evidence(
        evidence_id="S1",
        source_type="mail",
        document_id="mail-1",
        title="주간 메일",
        excerpt="확인된 사실입니다.",
        score=1.0,
        user_id=policy.user_id,
        acl_decision_id=policy.decision_id,
        content_hash="a" * 64,
    )


class StaticGraph:
    def __init__(self, state):
        self.state = state

    async def ainvoke(self, _input):
        return self.state


class RejectingSupportLLM:
    def __init__(self):
        self.complete_model_calls = 0

    async def complete_model(self, *_args, **_kwargs):
        self.complete_model_calls += 1
        raise AssertionError("claim-support model must not be called")


class DeepAnswerLLM(RejectingSupportLLM):
    async def complete_text(self, *_args, **_kwargs):
        return "확인된 사실입니다 [S1]"


class LongDeepAnswerLLM(RejectingSupportLLM):
    async def complete_text(self, *_args, **_kwargs):
        return "확인된 사실입니다 [S1] " + ("가" * 9_000)


def test_formatter_preserves_valid_three_section_answer():
    answer = (
        "### 요약\n요약\n\n"
        "### 상세설명\n근거 [S1]\n\n"
        "### 핵심결론\n결론 [S1]"
    )

    assert ensure_rag_answer_structure(answer) == answer


def test_formatter_wraps_malformed_answer_without_losing_citation():
    answer = ensure_rag_answer_structure("확인된 사실입니다 [S1]")

    assert_section_contract(answer)
    assert "확인된 사실입니다 [S1]" in answer


def test_formatter_rejects_heading_prefix_that_is_not_an_exact_heading():
    answer = ensure_rag_answer_structure(
        "### 요약문\n요약\n\n"
        "### 상세설명\n근거 [S1]\n\n"
        "### 핵심결론\n결론 [S1]"
    )

    assert_section_contract(answer)


def test_formatter_removes_unexpected_h3_sections():
    answer = ensure_rag_answer_structure(
        "### 요약\n요약\n\n"
        "### 상세설명\n근거 [S1]\n\n"
        "### 핵심결론\n결론 [S1]\n\n"
        "### 참고\n추가 내용"
    )

    assert_section_contract(answer)


def test_summary_notice_stays_inside_summary_section():
    answer = prepend_summary_notice(
        "근거가 부족합니다.",
        "확인 범위가 제한적입니다.",
    )

    assert_section_contract(answer)
    assert answer.index("확인 범위가 제한적입니다.") < answer.index("### 상세설명")


def test_summary_notice_is_not_duplicated_during_revalidation():
    once = prepend_summary_notice(
        "근거가 부족합니다.",
        "확인 범위가 제한적입니다.",
    )

    twice = prepend_summary_notice(once, "확인 범위가 제한적입니다.")

    assert twice == once


def test_summary_notice_uses_stable_prefix_for_deduplication():
    prefix = "확인 범위가 제한적입니다."
    once = prepend_summary_notice(
        "근거가 부족합니다.",
        f"{prefix}\n누락 정보: 초기 범위",
    )

    twice = prepend_summary_notice(once, f"{prefix}\n누락 정보: 변경된 범위")

    assert twice.count(prefix) == 1


def test_fast_rag_returns_citation_valid_answer_without_support_model_call():
    policy = PolicyContext.from_user_id("user-1")
    evidence = make_evidence(policy)
    llm = RejectingSupportLLM()
    workflow = FastRAGWorkflow(retrieval=None, llm=llm)
    workflow.graph = StaticGraph(
        {
            "answer": "확인된 사실입니다 [S1]",
            "evidence": [evidence],
            "sufficient": True,
            "searches": 1,
            "retrieval_mode": "hybrid",
            "disclosures": [],
        }
    )
    request = ChatRequest(user_id=policy.user_id, message="사실을 알려줘")

    result = asyncio.run(workflow._invoke(request, policy, None))

    assert result.execution is not None
    assert result.execution.status == "succeeded"
    assert result.quality.citation_valid is True
    assert llm.complete_model_calls == 0
    assert "[S1]" in result.answer
    assert_section_contract(result.answer)


def test_fast_rag_keeps_citation_validation_as_the_final_gate():
    policy = PolicyContext.from_user_id("user-1")
    llm = RejectingSupportLLM()
    workflow = FastRAGWorkflow(retrieval=None, llm=llm)
    workflow.graph = StaticGraph(
        {
            "answer": "존재하지 않는 근거입니다 [S2]",
            "evidence": [make_evidence(policy)],
            "sufficient": True,
            "searches": 1,
            "retrieval_mode": "hybrid",
            "disclosures": [],
        }
    )
    request = ChatRequest(user_id=policy.user_id, message="사실을 알려줘")

    result = asyncio.run(workflow._invoke(request, policy, None))

    assert result.execution is not None
    assert result.execution.status == "limited"
    assert result.execution.error_code == "CITATION_INVALID"
    assert result.quality.citation_valid is False
    assert llm.complete_model_calls == 0
    assert_section_contract(result.answer)


def test_fast_rag_no_evidence_answer_uses_section_contract():
    policy = PolicyContext.from_user_id("user-1")
    workflow = FastRAGWorkflow(retrieval=None, llm=RejectingSupportLLM())
    workflow.graph = StaticGraph(
        {
            "answer": "확인 가능한 근거가 없습니다.",
            "evidence": [],
            "sufficient": False,
            "missing_information": ["mail evidence"],
            "searches": 1,
            "retrieval_mode": "hybrid",
            "disclosures": [],
        }
    )
    request = ChatRequest(user_id=policy.user_id, message="사실을 알려줘")

    result = asyncio.run(workflow._invoke(request, policy, None))

    assert result.execution is not None
    assert result.execution.error_code == "NO_EVIDENCE"
    assert result.quality.citation_valid is None
    assert_section_contract(result.answer)


def test_fast_api_invalid_citation_fallback_uses_section_contract():
    policy = PolicyContext.from_user_id("user-1")
    result = FastRAGResult(
        answer=ensure_rag_answer_structure("존재하지 않는 근거입니다 [S2]"),
        evidence=[make_evidence(policy)],
        quality=QualityStatus(citation_valid=True),
    )

    answer, references, quality, _disclosures, safe_evidence = _safe_fast_result(
        result, policy
    )

    assert quality.citation_valid is False
    assert references == []
    assert safe_evidence == []
    assert_section_contract(answer)


def test_deep_rag_returns_citation_valid_report_without_support_model_call():
    policy = PolicyContext.from_user_id("user-1")
    evidence = make_evidence(policy)
    llm = DeepAnswerLLM()
    workflow = DeepResearchWorkflow(retrieval=None, llm=llm)

    result = asyncio.run(
        workflow._synthesize(
            {
                "question": "사실을 알려줘",
                "policy": policy,
                "branch_results": [
                    BranchResult(question="사실", evidence=[evidence])
                ],
            }
        )
    )

    assert result["citation_valid"] is True
    assert result["evidence"]
    assert llm.complete_model_calls == 0
    assert "[S1]" in result["report"]
    assert_section_contract(result["report"])


def test_deep_rag_preserves_sections_when_bm25_report_hits_byte_limit():
    policy = PolicyContext.from_user_id("user-1")
    evidence = make_evidence(policy)
    workflow = DeepResearchWorkflow(retrieval=None, llm=LongDeepAnswerLLM())

    result = asyncio.run(
        workflow._synthesize(
            {
                "question": "사실을 알려줘",
                "policy": policy,
                "branch_results": [
                    BranchResult(
                        question="사실",
                        evidence=[evidence],
                        embedding_fallback=True,
                    )
                ],
            }
        )
    )

    assert len(result["report"].encode("utf-8")) <= 8_000
    assert_section_contract(result["report"])


def test_deep_rag_no_evidence_report_uses_section_contract():
    policy = PolicyContext.from_user_id("user-1")
    workflow = DeepResearchWorkflow(retrieval=None, llm=DeepAnswerLLM())

    result = asyncio.run(
        workflow._synthesize(
            {
                "question": "사실을 알려줘",
                "policy": policy,
                "branch_results": [],
            }
        )
    )

    assert result["citation_valid"] is True
    assert result["evidence"] == []
    assert_section_contract(result["report"])


def test_deep_invalid_result_keeps_sections_through_completion_and_status():
    async def complete_invalid_result():
        policy = PolicyContext.from_user_id("user-1")
        store = InMemoryResearchJobStore()
        created = await store.create(policy, "trace-1", "질문", "조사 계획")
        claimed = await store.claim()
        assert claimed is not None
        result = DeepResearchResult(
            report="존재하지 않는 근거입니다 [S2]",
            evidence=[make_evidence(policy)],
            completed_sub_questions=1,
            rounds=1,
            disclosures=[BM25_FALLBACK_DISCLOSURE],
            citation_valid=False,
        )
        completed = await store.complete(
            created.job_id, policy, claimed.lease_token, result
        )
        return policy, completed

    policy, completed = asyncio.run(complete_invalid_result())

    assert completed.result_markdown is not None
    assert len(completed.result_markdown.encode("utf-8")) <= 8_000
    assert_section_contract(completed.result_markdown)
    public = _response(completed, policy)
    assert public.result_markdown is not None
    assert len(public.result_markdown.encode("utf-8")) <= 8_000
    assert_section_contract(public.result_markdown)


def test_deep_queued_status_without_result_is_safe():
    async def create_queued_job():
        policy = PolicyContext.from_user_id("user-1")
        store = InMemoryResearchJobStore()
        job = await store.create(policy, "trace-1", "질문", "조사 계획")
        return policy, job

    policy, job = asyncio.run(create_queued_job())

    public = _response(job, policy)

    assert public.result_markdown is None
    assert public.references == []

import asyncio

from app.domain.chat import ChatRequest
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.graphs.fast_rag import FastRAGWorkflow
from app.llm.answer_format import (
    ensure_rag_answer_structure,
    prepend_summary_notice,
)


HEADINGS = ("### 요약", "### 상세설명", "### 핵심결론")


def assert_section_contract(answer: str) -> None:
    assert answer.startswith(HEADINGS[0])
    assert all(answer.count(heading) == 1 for heading in HEADINGS)
    assert [answer.index(heading) for heading in HEADINGS] == sorted(
        answer.index(heading) for heading in HEADINGS
    )


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


def test_summary_notice_stays_inside_summary_section():
    answer = prepend_summary_notice(
        "근거가 부족합니다.",
        "확인 범위가 제한적입니다.",
    )

    assert_section_contract(answer)
    assert answer.index("확인 범위가 제한적입니다.") < answer.index("### 상세설명")


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

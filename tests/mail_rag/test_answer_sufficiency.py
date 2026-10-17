import re
import pytest
from app.domain.chat import ChatRequest
from app.domain.policy import PolicyContext
from app.domain.quick_rag import QuickPlan
from app.graphs.quick_rag import QuickRAGWorkflow
from app.graphs.detailed_rag import DetailedRAGWorkflow, DetailedPlan, RankingDecision
from app.api.routes.chat import _safe_agent_result
from app.retrieval.quick_rag import QuickSearchOutcome, QuickDocument


class Search:
    def capabilities(self):
        return {}

    async def search(self, plan, policy, **kwargs):
        return QuickSearchOutcome(
            documents=[
                QuickDocument(
                    source="wiki_summary",
                    id="one",
                    original_id="one",
                    title="합성 자료",
                    text="테스트 값은 1입니다.",
                )
            ],
            searched_sources=["wiki_summary"],
            resolved_plan=plan,
        )


class Model:
    def __init__(self, status, answer):
        self.status, self.answer = status, answer

    async def complete_model(self, system, user, schema):
        if schema is DetailedPlan:
            return DetailedPlan(query="test")
        if schema is QuickPlan:
            return QuickPlan(query="test")
        if schema is RankingDecision:
            return RankingDecision(ranked_ids=["D1"])
        return schema(
            evidence_status=self.status,
            answer=self.answer,
            evidence_ids=re.findall(r"\[(S\d+)\]", self.answer),
            missing=(
                "요청한 변경 이유는 검색된 자료에서 확인되지 않습니다."
                if self.status != "sufficient"
                else ""
            ),
        )

    async def complete_text(self, *args):
        raise AssertionError("Answer must be structured")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,answer",
    [
        ("none", ""),
        ("partial", "테스트 값은 1입니다. [S1]"),
        ("sufficient", "테스트 값은 1입니다. [S1]"),
    ],
)
async def test_same_structured_status_and_rendering_in_both_modes(status, answer):
    outputs = []
    for detailed in [False, True]:
        m = Model(status, answer)
        w = (
            DetailedRAGWorkflow(Search(), m, m, m)
            if detailed
            else QuickRAGWorkflow(Search(), m, m)
        )
        result = await w.invoke(
            ChatRequest(user_id="test", message="합성 질문"),
            PolicyContext.from_user_id("test"),
        )
        public, refs, quality, *_ = _safe_agent_result(
            result, PolicyContext.from_user_id("test")
        )
        assert quality.evidence_status == status
        if status == "none":
            assert not refs
            assert "변경 이유" in public
            assert result.execution.error_code is None
            assert quality.citation_valid is None
        else:
            assert refs
            assert quality.citation_valid
            assert answer in public
            if status == "partial":
                assert "변경 이유" in public
        outputs.append(public)
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize(
    "payload",
    [
        {"evidence_status": "none", "answer": "추측한 사실", "missing": "정보 부족"},
        {"evidence_status": "partial", "answer": "확인된 사실 [S1]", "missing": ""},
        {"evidence_status": "sufficient", "answer": "", "missing": ""},
    ],
)
def test_status_contract_rejects_inconsistent_payload(payload):
    from pydantic import ValidationError
    from app.llm.quick_rag import GroundedAnswer

    with pytest.raises(ValidationError):
        GroundedAnswer(**payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("detailed", [False, True])
async def test_partial_does_not_bypass_unknown_citation_validation(detailed):
    m = Model("partial", "잘못된 근거 [S99]")
    w = (
        DetailedRAGWorkflow(Search(), m, m, m)
        if detailed
        else QuickRAGWorkflow(Search(), m, m)
    )
    r = await w.invoke(
        ChatRequest(user_id="test", message="합성 질문"),
        PolicyContext.from_user_id("test"),
    )
    answer, *_ = _safe_agent_result(r, PolicyContext.from_user_id("test"))
    assert "잘못된 근거" not in answer
    assert r.quality.evidence_status == "not_assessed"


@pytest.mark.asyncio
@pytest.mark.parametrize("detailed", [False, True])
async def test_explicit_evidence_ids_render_citations_without_inline_markers(detailed):
    class ExplicitModel(Model):
        async def complete_model(self, system, user, schema):
            if schema.__name__ == "GroundedAnswer":
                return schema(
                    evidence_status="partial",
                    answer="테스트 값은 1입니다.",
                    missing="최초 배포일은 자료에서 확인되지 않습니다.",
                    evidence_ids=["S1"],
                )
            return await super().complete_model(system, user, schema)

    m = ExplicitModel("partial", "")
    w = (
        DetailedRAGWorkflow(Search(), m, m, m)
        if detailed
        else QuickRAGWorkflow(Search(), m, m)
    )
    r = await w.invoke(
        ChatRequest(user_id="test", message="합성 질문"),
        PolicyContext.from_user_id("test"),
    )
    answer, refs, quality, *_ = _safe_agent_result(
        r, PolicyContext.from_user_id("test")
    )
    assert quality.evidence_status == "partial" and quality.citation_valid
    assert "테스트 값은 1입니다." in answer and "배포일" in answer and "[S1]" in answer
    assert refs

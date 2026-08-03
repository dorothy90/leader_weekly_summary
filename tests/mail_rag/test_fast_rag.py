import asyncio
import inspect
from time import perf_counter

import langchain_core.globals as langchain_globals
import pytest

from app.domain.chat import BM25_FALLBACK_DISCLOSURE, ChatRequest
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.domain.errors import AppError, ErrorCode
from app.graphs.fast_rag import (
    INCOMPLETE_ANSWER_PREFIX,
    MAX_EVIDENCE_TOKENS,
    FastRAGWorkflow,
)
from app.retrieval.service import RetrievalResult
from app.security.citations import CitationValidator


def _evidence(
    document_id: str,
    *,
    owner: str = "kim",
    excerpt: str = "수율은 개선되었습니다.",
    title: str = "주간 보고",
    source_locator: str | None = "mail:opaque-id",
) -> Evidence:
    return Evidence(
        evidence_id="S1",
        source_type="mail",
        document_id=document_id,
        title=title,
        excerpt=excerpt,
        score=1,
        user_id=owner,
        acl_decision_id="decision",
        content_hash=f"hash-{document_id}",
        source_locator=source_locator,
    )


class RecordingRetrieval:
    def __init__(self, result_factory):
        self.result_factory = result_factory
        self.calls = []

    async def search(self, task, policy):
        self.calls.append((task, policy))
        return self.result_factory(task, policy)


class ScriptedLLM:
    def __init__(
        self,
        *,
        tasks,
        sufficient=False,
        missing_information=None,
        texts=None,
    ):
        self.tasks = tasks
        self.sufficient = sufficient
        self.missing_information = (
            list(missing_information)
            if missing_information is not None
            else ([] if sufficient else ["추가 근거"])
        )
        self.texts = list(texts or [])
        self.model_calls = []
        self.text_calls = []

    async def complete_model(self, system, user, schema):
        self.model_calls.append((system, user, schema.__name__))
        if schema.__name__ == "RetrievalPlan":
            return schema.model_validate({"tasks": self.tasks})
        if schema.__name__ == "ClaimSupportDecision":
            return schema.model_validate({"supported": True})
        return schema.model_validate(
            {
                "sufficient": self.sufficient,
                "missing_information": self.missing_information,
            }
        )

    async def complete_text(self, system, user):
        self.text_calls.append((system, user))
        if self.texts:
            return self.texts.pop(0)
        return "재작성 검색어"


def test_fast_workflow_construction_does_not_mutate_langchain_core_globals():
    original = (
        langchain_globals._debug,
        langchain_globals._verbose,
        langchain_globals._llm_cache,
    )
    FastRAGWorkflow(
        RecordingRetrieval(
            lambda task, policy: RetrievalResult(evidence=[], mode="hybrid")
        ),
        ScriptedLLM(tasks=[{"query": "수율", "source": "mail"}]),
    )
    assert (
        langchain_globals._debug,
        langchain_globals._verbose,
        langchain_globals._llm_cache,
    ) == original


def test_fast_workflow_invoke_does_not_mutate_langchain_core_globals():
    original = (
        langchain_globals._debug,
        langchain_globals._verbose,
        langchain_globals._llm_cache,
    )
    workflow = FastRAGWorkflow(
        RecordingRetrieval(
            lambda task, policy: RetrievalResult(evidence=[], mode="hybrid")
        ),
        ScriptedLLM(tasks=[{"query": "수율", "source": "mail"}]),
    )

    asyncio.run(
        workflow.invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert (
        langchain_globals._debug,
        langchain_globals._verbose,
        langchain_globals._llm_cache,
    ) == original


def test_general_identity_response_uses_product_identity_without_llm_claims():
    llm = ScriptedLLM(tasks=[])
    workflow = FastRAGWorkflow(None, llm)

    result = asyncio.run(
        workflow.respond_general(ChatRequest(user_id="kim", message="넌누구야"))
    )

    assert result.answer == (
        "저는 Weekly Mail Assistant입니다. 사용자별 메일 근거를 검색하고 "
        "Fast 답변과 Deep Research 보고서를 제공하는 도우미입니다."
    )
    assert result.quality.retrieval_mode == "not_used"
    assert llm.text_calls == []
    assert [run.node_name for run in result.execution.node_runs] == [
        "general.generate"
    ]


def test_general_response_uses_role_preserving_conversation_history():
    class MessageLLM(ScriptedLLM):
        def __init__(self):
            super().__init__(tasks=[])
            self.message_calls = []

        async def complete_messages(self, system, messages):
            self.message_calls.append((system, messages))
            return "파랑입니다."

    llm = MessageLLM()
    workflow = FastRAGWorkflow(None, llm)
    conversation = type(
        "Memory",
        (),
        {
            "messages": [
                {"role": "user", "content": "내가 좋아하는 색은 파랑"},
                {"role": "assistant", "content": "기억하겠습니다."},
            ]
        },
    )()

    result = asyncio.run(
        workflow.respond_general(
            ChatRequest(user_id="kim", message="내가 좋아하는 색이 뭐지?"), conversation
        )
    )

    assert result.answer == "파랑입니다."
    assert llm.message_calls[0][1] == [
        {"role": "user", "content": "내가 좋아하는 색은 파랑"},
        {"role": "assistant", "content": "기억하겠습니다."},
        {"role": "user", "content": "내가 좋아하는 색이 뭐지?"},
    ]


def test_general_name_declaration_and_recall_do_not_depend_on_model_availability():
    llm = ScriptedLLM(tasks=[])
    workflow = FastRAGWorkflow(None, llm)
    declaration = asyncio.run(
        workflow.respond_general(ChatRequest(user_id="kim", message="내이름은대환"))
    )
    conversation = type(
        "Memory",
        (),
        {"messages": [{"role": "user", "content": "내이름은대환"}]},
    )()
    recall = asyncio.run(
        workflow.respond_general(
            ChatRequest(user_id="kim", message="내 이름이 뭐라고?"), conversation
        )
    )

    assert declaration.answer == "반갑습니다, 대환님. 이름을 기억하겠습니다."
    assert recall.answer == "대환님이라고 하셨습니다."
    assert llm.text_calls == []


def test_general_model_timeout_returns_typed_failure():
    class SlowGeneralLLM(ScriptedLLM):
        async def complete_text(self, system, user):
            await asyncio.sleep(0.02)
            return "늦은 답변"

    result = asyncio.run(
        FastRAGWorkflow(
            None,
            SlowGeneralLLM(tasks=[]),
            general_timeout_seconds=0.005,
        ).respond_general(ChatRequest(user_id="kim", message="안녕하세요"))
    )

    assert result.execution.status == "failed"
    assert result.execution.failure_stage == "generation"
    assert result.execution.error_code == "LLM_TIMEOUT"
    assert result.execution.include_in_llm_history is False


def test_no_evidence_general_fallback_uses_safe_prompt_and_conversation_history():
    class MessageLLM(ScriptedLLM):
        def __init__(self):
            super().__init__(tasks=[])
            self.message_calls = []

        async def complete_messages(self, system, messages):
            self.message_calls.append((system, messages))
            return "현재 지역 목록은 확인할 수 없지만 일반 선택 기준은 안내할 수 있습니다."

    llm = MessageLLM()
    workflow = FastRAGWorkflow(None, llm)
    conversation = type(
        "Memory",
        (),
        {
            "messages": [
                {"role": "user", "content": "장례식장 정보 알려줘"},
                {"role": "assistant", "content": "메일에서 찾아보겠습니다."},
            ]
        },
    )()

    result = asyncio.run(
        workflow.respond_without_evidence(
            ChatRequest(user_id="kim", message="서울 지역으로"), conversation
        )
    )

    system, messages = llm.message_calls[0]
    assert "no relevant mail evidence" in system.casefold()
    assert "venue names" in system.casefold()
    assert "current, local, or private" in system.casefold()
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert result.execution.status == "succeeded"
    assert [run.node_name for run in result.execution.node_runs] == [
        "general.no_evidence_fallback"
    ]
    assert result.execution.node_runs[0].output.fallback_used is True


def test_fast_contextualizer_uses_the_same_role_preserving_history_window():
    class MessageLLM(ScriptedLLM):
        def __init__(self):
            super().__init__(tasks=[])
            self.message_calls = []

        async def complete_messages(self, system, messages):
            self.message_calls.append((system, messages))
            return "지난주 생산기술팀 메일 중 수율 이슈를 요약해줘"

    llm = MessageLLM()
    workflow = FastRAGWorkflow(None, llm)
    conversation = type(
        "Memory",
        (),
        {
            "messages": [
                {"role": "user", "content": "지난주 생산기술팀 메일 찾아줘"},
                {"role": "assistant", "content": "검색했습니다."},
            ]
        },
    )()

    result = asyncio.run(
        workflow._contextualize(
            {
                "request": ChatRequest(user_id="kim", message="수율만 요약해줘"),
                "conversation": conversation,
            }
        )
    )

    assert result["standalone_question"] == (
        "지난주 생산기술팀 메일 중 수율 이슈를 요약해줘"
    )
    assert [item["role"] for item in llm.message_calls[0][1]] == [
        "user",
        "assistant",
        "user",
    ]


def test_fast_generation_uses_the_standalone_question():
    llm = ScriptedLLM(tasks=[], texts=["강남역 인근 장례식장입니다. [S1]"])
    workflow = FastRAGWorkflow(None, llm)

    result = asyncio.run(
        workflow._generate(
            {
                "request": ChatRequest(
                    user_id="kim", message="그중 제일 가까운 데는?"
                ),
                "standalone_question": "강남역에서 가장 가까운 장례식장은?",
                "evidence": [_evidence("mail-1")],
                "sufficient": True,
                "missing_information": [],
            }
        )
    )

    prompt = llm.text_calls[-1][1]
    assert "Question: 강남역에서 가장 가까운 장례식장은?" in prompt
    assert "Question: 그중 제일 가까운 데는?" not in prompt
    assert result == {"answer": "강남역 인근 장례식장입니다. [S1]"}


def test_fast_rag_rejects_request_policy_owner_mismatch_before_retrieval():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(evidence=[], mode="hybrid")
    )
    llm = ScriptedLLM(tasks=[{"query": "수율", "source": "mail"}])

    with pytest.raises(ValueError, match="owner"):
        asyncio.run(
            FastRAGWorkflow(retrieval, llm).invoke(
                ChatRequest(user_id="kim", message="수율"),
                PolicyContext.from_user_id("lee"),
                None,
            )
        )

    assert retrieval.calls == []


def test_fast_rag_propagates_exact_embedding_fallback_disclosure():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            mode="bm25",
            embedding_error="EMBEDDING_UNAVAILABLE",
            disclosures=[BM25_FALLBACK_DISCLOSURE],
        )
    )
    llm = ScriptedLLM(tasks=[{"query": "수율", "source": "mail"}])

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="수율"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.answer.endswith(BM25_FALLBACK_DISCLOSURE)
    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]
    assert result.quality.retrieval_mode == "bm25"


def test_fast_rag_does_not_report_embedding_outage_for_normal_bm25_statistics():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            mode="bm25",
            evidence=[_evidence("statistics")],
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "메일 수", "source": "statistics"}],
        sufficient=True,
        texts=["메일 통계입니다 [S1]"],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="메일 수"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.quality.retrieval_mode == "bm25"
    assert result.disclosures == []
    assert BM25_FALLBACK_DISCLOSURE not in result.answer


def test_fast_rag_enforces_six_search_budget_and_request_policy_identity():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence(task.query)],
            mode="hybrid",
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": f"q{i}", "source": "mail"} for i in range(6)],
        sufficient=False,
        texts=["지원되는 답변 [S1]"],
    )
    policy = PolicyContext.from_user_id("kim")

    request = ChatRequest(
        user_id="kim",
        message="질문",
        filters={"teams": ["YIELD팀"], "weeks": ["2026-08"]},
    )

    asyncio.run(FastRAGWorkflow(retrieval, llm).invoke(request, policy, None))

    assert len(retrieval.calls) == 6
    assert all(call_policy is policy for _, call_policy in retrieval.calls)
    assert all(task.filters == request.filters for task, _ in retrieval.calls)


def test_fast_rag_runs_independent_planned_searches_concurrently():
    class SlowRetrieval(RecordingRetrieval):
        async def search(self, task, policy):
            self.calls.append((task, policy))
            await asyncio.sleep(0.04)
            return RetrievalResult(evidence=[_evidence(task.query)], mode="hybrid")

    retrieval = SlowRetrieval(lambda *_: None)
    llm = ScriptedLLM(
        tasks=[{"query": f"q{i}", "source": "mail"} for i in range(3)],
        sufficient=True,
        texts=["답 [S1]"],
    )
    started = perf_counter()
    asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )
    assert perf_counter() - started < 0.1


def test_fast_rag_has_no_workflow_wide_deadline():
    assert "deadline_seconds" not in inspect.signature(FastRAGWorkflow).parameters


def test_fast_rag_planning_timeout_falls_back_to_standalone_search():
    class SlowPlanLLM(ScriptedLLM):
        async def complete_model(self, system, user, schema):
            if schema.__name__ == "RetrievalPlan":
                await asyncio.sleep(0.02)
            return await super().complete_model(system, user, schema)

    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence("doc")], mode="hybrid"
        )
    )
    result = asyncio.run(
        FastRAGWorkflow(
            retrieval,
            SlowPlanLLM(
                tasks=[{"query": "모델 계획", "source": "mail"}],
                sufficient=True,
                texts=["확인 답변 [S1]"],
            ),
            model_step_timeout_seconds=0.005,
        ).invoke(
            ChatRequest(user_id="kim", message="최근 반도체 수율 이슈"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert retrieval.calls[0][0].query == "최근 반도체 수율 이슈"
    assert result.execution.status == "succeeded"
    assert result.execution.search_count == 1
    plan_run = next(
        run for run in result.execution.node_runs if run.node_name == "llm.fast.plan"
    )
    assert plan_run.status == "error"
    assert plan_run.error_class == "TimeoutError"


def test_fast_rag_retrieval_app_error_is_preserved_as_typed_failure():
    async def fail(*_args):
        raise AppError(
            ErrorCode.INDEX_UNAVAILABLE,
            "검색 인덱스를 사용할 수 없습니다.",
            retryable=True,
        )

    retrieval = RecordingRetrieval(lambda *_: RetrievalResult())
    retrieval.search = fail
    result = asyncio.run(
        FastRAGWorkflow(
            retrieval,
            ScriptedLLM(tasks=[{"query": "q", "source": "mail"}]),
        ).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.execution.status == "failed"
    assert result.execution.failure_stage == "retrieval"
    assert result.execution.error_code == "INDEX_UNAVAILABLE"
    assert result.execution.retryable is True
    assert result.quality.citation_valid is None


def test_fast_no_evidence_skips_rewrite_and_preserves_embedding_disclosure():
    llm = ScriptedLLM(tasks=[{"query": "q", "source": "mail"}], sufficient=False)
    retrieval = RecordingRetrieval(
        lambda *_: RetrievalResult(
            evidence=[],
            mode="bm25",
            embedding_error="EMBEDDING_UNAVAILABLE",
            disclosures=[BM25_FALLBACK_DISCLOSURE],
        )
    )
    result = asyncio.run(
        FastRAGWorkflow(
            retrieval,
            llm,
        ).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.execution.status == "limited"
    assert result.execution.error_code == "NO_EVIDENCE"
    assert result.quality.retrieval_mode == "bm25"
    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]
    assert llm.text_calls == []
    assert [run.node_name for run in result.execution.node_runs] == [
        "fast.contextualize",
        "fast.plan",
        "llm.fast.plan",
        "fast.retrieve",
        "fast.grade",
        "fast.generate",
        "fast.validate",
    ]
    retrieve = next(
        run for run in result.execution.node_runs if run.node_name == "fast.retrieve"
    )
    assert retrieve.output.search_count == 1
    assert retrieve.output.evidence_count == 0
    assert retrieve.output.retrieval_mode == "bm25"
    assert retrieve.output.fallback_used is True


def test_fast_rag_no_evidence_is_limited_without_false_citation_failure():
    result = asyncio.run(
        FastRAGWorkflow(
            RecordingRetrieval(lambda *_: RetrievalResult(evidence=[])),
            ScriptedLLM(tasks=[{"query": "q", "source": "mail"}]),
        ).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.execution.status == "limited"
    assert result.execution.error_code == "NO_EVIDENCE"
    assert result.execution.failure_stage == "retrieval"
    assert result.quality.citation_valid is None


def test_fast_rag_unsupported_answer_has_distinct_execution_error():
    class SupportLLM(ScriptedLLM):
        async def complete_model(self, system, user, schema):
            if schema.__name__ == "ClaimSupportDecision":
                return schema.model_validate(
                    {"supported": False, "unsupported_claims": ["임의 주장"]}
                )
            return await super().complete_model(system, user, schema)

    result = asyncio.run(
        FastRAGWorkflow(
            RecordingRetrieval(
                lambda *_: RetrievalResult(evidence=[_evidence("doc")])
            ),
            SupportLLM(
                tasks=[{"query": "q", "source": "mail"}],
                sufficient=True,
                texts=["임의 주장 [S1]"],
            ),
        ).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.execution.status == "limited"
    assert result.execution.error_code == "UNSUPPORTED_ANSWER"
    assert result.execution.failure_stage == "support_validation"


def test_valid_citation_cannot_publish_arbitrary_unsupported_claim():
    class SupportLLM(ScriptedLLM):
        async def complete_model(self, system, user, schema):
            if schema.__name__ == "ClaimSupportDecision":
                return schema.model_validate(
                    {"supported": False, "unsupported_claims": ["임의 주장"]}
                )
            return await super().complete_model(system, user, schema)

    result = asyncio.run(
        FastRAGWorkflow(
            RecordingRetrieval(lambda *_: RetrievalResult(evidence=[_evidence("doc")])),
            SupportLLM(
                tasks=[{"query": "q", "source": "mail"}],
                sufficient=True,
                texts=["임의 주장은 사실입니다 [S1]"],
            ),
        ).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )
    assert "임의 주장은 사실" not in result.answer
    assert result.quality.limited_answer is True


def test_fast_rag_enforces_two_rewrites_and_one_revision():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence(f"doc-{len(retrieval.calls)}")],
            mode="hybrid",
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "initial", "source": "mail"}],
        sufficient=False,
        texts=[
            "rewrite-one",
            "rewrite-two",
            "지원되지 않은 초안 [S99]",
            "근거로 지원되는 답변 [S1]",
        ],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    rewrite_calls = [
        call for call in llm.text_calls if "search query" in call[0].lower()
    ]
    revision_calls = [call for call in llm.text_calls if "revise" in call[0].lower()]
    assert len(rewrite_calls) == 2
    assert len(revision_calls) == 1
    assert len(retrieval.calls) == 3
    assert result.answer.startswith(INCOMPLETE_ANSWER_PREFIX)
    assert result.answer.endswith("근거로 지원되는 답변 [S1]")


def test_rewrite_results_can_replace_stale_evidence_within_eight_item_cap():
    def results_for_round(task, policy):
        if len(retrieval.calls) == 1:
            evidence = [_evidence(f"stale-{index}") for index in range(8)]
        else:
            evidence = [_evidence("rewrite-new")]
        return RetrievalResult(evidence=evidence, mode="hybrid")

    retrieval = RecordingRetrieval(results_for_round)
    llm = ScriptedLLM(
        tasks=[{"query": "initial", "source": "mail"}],
        sufficient=False,
        texts=[
            "rewrite-one",
            "rewrite-two",
            "새 근거로 제한된 답변 [S1]",
        ],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert "rewrite-new" in {item.document_id for item in result.evidence}


def test_incomplete_evidence_is_explicit_in_grounded_generation_contract():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence(f"doc-{len(retrieval.calls)}")], mode="hybrid"
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "initial", "source": "mail"}],
        sufficient=False,
        texts=["rewrite-one", "rewrite-two", "모든 범위 완전 확인 [S1]"],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    generation_system, generation_user = next(
        (system, user)
        for system, user in llm.text_calls
        if "grounded answer" in system.lower()
    )
    assert "incomplete" in generation_system.lower()
    assert "Evidence status: incomplete" in generation_user
    assert "추가 근거" in generation_user
    assert result.quality.limited_answer is True
    assert result.answer.startswith(INCOMPLETE_ANSWER_PREFIX)
    assert "미확인 정보: 추가 근거" in result.answer


def test_missing_information_cannot_inject_unknown_citation_into_final_answer():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence(f"doc-{len(retrieval.calls)}")], mode="hybrid"
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "initial", "source": "mail"}],
        sufficient=False,
        missing_information=["not found [S99]"],
        texts=["rewrite-one", "rewrite-two", "확인 범위 답변 [S1]"],
    )

    policy = PolicyContext.from_user_id("kim")
    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            policy,
            None,
        )
    )

    assert result.quality.citation_valid is True
    assert "[S99]" not in result.answer
    assert result.answer.startswith(INCOMPLETE_ANSWER_PREFIX)
    assert result.answer.endswith("확인 범위 답변 [S1]")
    assert CitationValidator().validate(result.answer, result.evidence, policy).valid


def test_fast_workflow_canonicalizes_known_model_citation_variant():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence("owned")], mode="hybrid"
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "수율", "source": "mail"}],
        sufficient=True,
        texts=["확인된 사실 (s1)"],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.answer == "확인된 사실 [S1]"
    assert result.quality.citation_valid is True
    assert result.execution.status == "succeeded"


def test_incomplete_evidence_limitation_is_preserved_during_revision():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence(f"doc-{len(retrieval.calls)}")], mode="hybrid"
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "initial", "source": "mail"}],
        sufficient=False,
        texts=[
            "rewrite-one",
            "rewrite-two",
            "잘못된 초안 [S99]",
            "확인 범위만 답변 [S1]",
        ],
    )

    asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    revision_system, revision_user = next(
        (system, user) for system, user in llm.text_calls if "revise" in system.lower()
    )
    assert "incomplete" in revision_system.lower()
    assert "Evidence status: incomplete" in revision_user
    assert "추가 근거" in revision_user


def test_fast_rag_filters_cross_owner_evidence_and_caps_evidence_and_context():
    long_excerpt = "가" * 5000
    evidence = [
        _evidence(
            "/tmp",
            excerpt=(
                "password=hunter2 client_secret=oauth-secret "
                "api key: excerpt-space-secret ../secret.txt 수율 근거"
            ),
            title="password=title-secret api key: title-space-secret /tmp",
            source_locator=r"C:\secret",
        ),
        *[_evidence(f"owned-{index}", excerpt=long_excerpt) for index in range(9)],
    ] + [_evidence("foreign-secret", owner="lee", excerpt="CROSS_OWNER_SECRET")]
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(evidence=evidence, mode="hybrid")
    )
    llm = ScriptedLLM(
        tasks=[{"query": "수율", "source": "mail"}],
        sufficient=True,
        texts=["지원되는 답변 [S1]"],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    generation_user = next(
        user for system, user in llm.text_calls if "grounded answer" in system.lower()
    )
    evidence_context = generation_user.partition("Evidence:\n")[2]
    assert len(result.evidence) == 8
    assert all(item.user_id == "kim" for item in result.evidence)
    assert "CROSS_OWNER_SECRET" not in generation_user
    assert "mail:opaque-id" not in generation_user
    assert "hunter2" not in generation_user
    assert "oauth-secret" not in generation_user
    assert "excerpt-space-secret" not in generation_user
    assert "../secret.txt" not in generation_user
    assert "title-secret" not in generation_user
    assert "title-space-secret" not in generation_user
    assert "/tmp" not in generation_user
    assert r"C:\secret" not in generation_user
    assert "hunter2" not in result.model_dump_json()
    assert "oauth-secret" not in result.model_dump_json()
    assert "excerpt-space-secret" not in result.model_dump_json()
    assert "title-secret" not in result.model_dump_json()
    assert "title-space-secret" not in result.model_dump_json()
    assert "/tmp" not in result.model_dump_json()
    assert "secret.txt" not in result.model_dump_json()
    assert "C:\\\\secret" not in result.model_dump_json()
    assert len(evidence_context.encode("utf-8")) <= MAX_EVIDENCE_TOKENS


def test_invalid_revision_returns_limited_answer_without_unsafe_content():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence("owned")], mode="hybrid"
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "수율", "source": "mail"}],
        sufficient=True,
        texts=[
            "secret token=abc /srv/private/mail [S99]",
            "chain-of-thought: hidden reasoning [S98]",
        ],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.quality.citation_valid is False
    assert result.quality.limited_answer is True
    assert "abc" not in result.answer
    assert "/srv/" not in result.answer
    assert "chain-of-thought" not in result.answer.lower()
    assert "S98" not in result.answer


def test_grounded_answer_redacts_url_credentials_and_bearer_tokens():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence("owned")], mode="hybrid"
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "수율", "source": "mail"}],
        sufficient=True,
        texts=[
            "접속 https://kim:pw@example.test Authorization: Bearer bearer-secret "
            "client_secret=answer-secret OPENAI_API_KEY=sk-secret "
            "AWS_SECRET_ACCESS_KEY=aws-secret file:/srv/private/answer.txt "
            "api key: spaced-secret /tmp C:\\secret ../secret.txt "
            "안전 링크 https://example.com/mail/123 공정/품질 공정 / 품질 "
            "/help </div> /foo/bar /credentials.json /secret.txt /app.env "
            "/secrets /run /mnt /dev /Volumes //etc/passwd ///var/log [S1]"
        ],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert result.quality.citation_valid is True
    assert "pw" not in result.answer
    assert "bearer-secret" not in result.answer
    assert "answer-secret" not in result.answer
    assert "sk-secret" not in result.answer
    assert "aws-secret" not in result.answer
    assert "spaced-secret" not in result.answer
    assert "file:/srv" not in result.answer
    assert "/tmp" not in result.answer
    assert r"C:\secret" not in result.answer
    assert "../secret.txt" not in result.answer
    assert "https://example.com/mail/123" in result.answer
    assert "공정/품질" in result.answer
    assert "공정 / 품질" in result.answer
    assert "/help" in result.answer
    assert "</div>" in result.answer
    assert "/foo/bar" not in result.answer
    assert "/credentials.json" not in result.answer
    assert "/secret.txt" not in result.answer
    assert "/app.env" not in result.answer
    assert "/secrets" not in result.answer
    assert "/run" not in result.answer
    assert "/mnt" not in result.answer
    assert "/dev" not in result.answer
    assert "/Volumes" not in result.answer
    assert "//etc/passwd" not in result.answer
    assert "///var/log" not in result.answer
    assert result.answer.endswith("[S1]")


def test_sensitive_request_and_history_are_redacted_from_all_model_prompts():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[_evidence("owned")], mode="hybrid"
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "수율", "source": "mail"}],
        sufficient=True,
        texts=["독립 질문", "근거 답변 [S1]"],
    )
    conversation = type(
        "Memory",
        (),
        {
            "messages": [
                {
                    "role": "user",
                    "content": ("api key: history-secret C:\\secret " "공정/품질 이력"),
                }
            ]
        },
    )()

    asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(
                user_id="kim",
                message="api key: request-secret ../secret.txt 질문",
            ),
            PolicyContext.from_user_id("kim"),
            conversation,
        )
    )

    prompts = [user for _, user in llm.text_calls] + [
        user for _, user, _ in llm.model_calls
    ]
    serialized = "\n".join(prompts)
    assert "history-secret" not in serialized
    assert "request-secret" not in serialized
    assert r"C:\secret" not in serialized
    assert "../secret.txt" not in serialized
    assert "공정/품질" in serialized


def test_unsafe_document_identifiers_remain_distinct_after_redaction():
    retrieval = RecordingRetrieval(
        lambda task, policy: RetrievalResult(
            evidence=[
                _evidence("/tmp"),
                _evidence("../secret.txt"),
            ],
            mode="hybrid",
        )
    )
    llm = ScriptedLLM(
        tasks=[{"query": "수율", "source": "mail"}],
        sufficient=True,
        texts=["두 근거 [S1] [S2]"],
    )

    result = asyncio.run(
        FastRAGWorkflow(retrieval, llm).invoke(
            ChatRequest(user_id="kim", message="질문"),
            PolicyContext.from_user_id("kim"),
            None,
        )
    )

    assert len(result.evidence) == 2
    assert len({item.document_id for item in result.evidence}) == 2
    assert "/tmp" not in result.model_dump_json()
    assert "../secret.txt" not in result.model_dump_json()

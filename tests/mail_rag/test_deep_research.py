import asyncio
from functools import wraps
import inspect

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
        if schema.__name__ == "ClaimSupportDecision":
            return schema.model_validate({"supported": True})
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
    assert result.execution.status == "succeeded"
    assert result.execution.search_count == 2
    assert result.execution.evidence_count == len(result.evidence)
    assert 1 < retrieval.max_active <= MAX_CONCURRENCY
    assert all(call[1].user_id == "kim" for call in retrieval.calls)
    names = [run.node_name for run in result.execution.node_runs]
    assert names[0] == "deep.plan"
    assert "deep.research" in names
    assert "deep.gap" in names
    assert names[-1] == "deep.synthesize"
    research = next(run for run in result.execution.node_runs if run.node_name == "deep.research")
    assert research.output.search_count == 2
    assert research.output.evidence_count == 2


@async_test
async def test_deep_workflow_canonicalizes_known_model_citation_variant():
    result = await DeepResearchWorkflow(
        DeepRetrieval(),
        DeepLLM(sub_questions=["수율"], reports=["종합 결과 【s1】"]),
    ).invoke("수율 조사", PolicyContext.from_user_id("kim"))

    assert result.report == "종합 결과 [S1]"
    assert result.citation_valid is True
    assert [item.evidence_id for item in result.evidence] == ["S1"]


@async_test
async def test_deep_has_no_workflow_wide_deadline():
    assert "deadline_seconds" not in inspect.signature(
        DeepResearchWorkflow
    ).parameters


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
async def test_deep_total_branch_outage_fails_transiently():
    retrieval = DeepRetrieval()

    async def fail(*_args):
        raise AppError(ErrorCode.INDEX_UNAVAILABLE, "down", retryable=True)

    retrieval.search = fail
    with pytest.raises(AppError) as error:
        await DeepResearchWorkflow(retrieval, DeepLLM()).invoke(
            "질문", PolicyContext.from_user_id("kim")
        )
    assert error.value.code == ErrorCode.INDEX_UNAVAILABLE
    assert error.value.retryable is True


@async_test
async def test_deep_support_checker_failure_fails_closed():
    class BrokenChecker(DeepLLM):
        async def complete_model(self, system, user, schema):
            if schema.__name__ == "ClaimSupportDecision":
                raise TimeoutError("checker down")
            return await super().complete_model(system, user, schema)

    result = await DeepResearchWorkflow(DeepRetrieval(), BrokenChecker()).invoke(
        "질문", PolicyContext.from_user_id("kim")
    )
    assert result.citation_valid is False
    assert result.evidence == []
    assert result.execution.status == "limited"
    assert result.execution.include_in_llm_history is False


@async_test
async def test_deep_resume_uses_completed_checkpoint_without_repeating_search():
    retrieval = DeepRetrieval()
    saved = evidence("kim", "saved", "saved evidence").model_dump(mode="json")
    checkpoint = {
        "pending_queries": [],
        "branch_results": [{"question": "done", "evidence": [saved], "failed": False}],
        "searches": 1,
        "rounds": 1,
    }
    result = await DeepResearchWorkflow(
        retrieval, DeepLLM(reports=["resumed [S1]"])
    ).invoke("질문", PolicyContext.from_user_id("kim"), checkpoint=checkpoint)
    assert retrieval.calls == []
    assert result.completed_sub_questions == 1


@async_test
async def test_deep_crash_mid_batch_checkpoints_each_branch_and_does_not_repeat_completed():
    class OrderedRetrieval(DeepRetrieval):
        async def search(self, task, policy):
            self.calls.append((task, policy))
            await asyncio.sleep(0.001 if task.query == "q1" else 0.03)
            return RetrievalResult(
                evidence=[evidence("kim", task.query, task.query)], mode="hybrid"
            )

    retrieval = OrderedRetrieval()
    checkpoints = []

    async def crash_after_first(payload):
        checkpoints.append(payload)
        if payload["checkpoint"]["branch_results"]:
            raise RuntimeError("worker crash")

    with pytest.raises(RuntimeError, match="worker crash"):
        await DeepResearchWorkflow(
            retrieval, DeepLLM(sub_questions=["q1", "q2"])
        ).invoke(
            "질문",
            PolicyContext.from_user_id("kim"),
            progress_callback=crash_after_first,
        )
    saved = checkpoints[-1]["checkpoint"]
    assert [item["question"] for item in saved["branch_results"]] == ["q1"]
    assert saved["pending_queries"] == ["q2"]
    assert saved["rounds"] == 0
    assert saved["round_id"]

    retrieval.calls.clear()
    await DeepResearchWorkflow(retrieval, DeepLLM(reports=["resumed [S1]"])).invoke(
        "질문", PolicyContext.from_user_id("kim"), checkpoint=saved
    )
    assert [task.query for task, _policy in retrieval.calls] == ["q2"]


@async_test
async def test_three_query_round_stays_in_progress_across_branch_crashes_then_allows_gap_round():
    class OrderedRetrieval(DeepRetrieval):
        async def search(self, task, policy):
            self.calls.append((task, policy))
            await asyncio.sleep(
                {"q1": 0.001, "q2": 0.01, "q3": 0.02}.get(task.query, 0)
            )
            return RetrievalResult(
                evidence=[evidence("kim", task.query, task.query)], mode="hybrid"
            )

    retrieval = OrderedRetrieval()
    checkpoint = None
    round_id = None
    expected_pending = [["q2", "q3"], ["q3"], []]

    for completed_count, pending in enumerate(expected_pending, start=1):
        checkpoints = []
        prior_completed = completed_count - 1

        async def crash_after_next_branch(payload):
            checkpoints.append(payload)
            if len(payload["checkpoint"]["branch_results"]) > prior_completed:
                raise RuntimeError("crash")

        with pytest.raises(RuntimeError):
            await DeepResearchWorkflow(
                retrieval, DeepLLM(sub_questions=["q1", "q2", "q3"])
            ).invoke(
                "질문",
                PolicyContext.from_user_id("kim"),
                checkpoint=checkpoint,
                progress_callback=crash_after_next_branch,
            )
        checkpoint = checkpoints[-1]["checkpoint"]
        round_id = round_id or checkpoint["round_id"]
        assert checkpoint["round_id"] == round_id
        assert checkpoint["pending_queries"] == pending
        assert checkpoint["rounds"] == (1 if not pending else 0)

    retrieval.calls.clear()
    resumed_checkpoints = []

    async def save(payload):
        resumed_checkpoints.append(payload)

    llm = DeepLLM(
        gaps=[
            {"complete": False, "follow_up_questions": ["q4"]},
            {"complete": True, "follow_up_questions": []},
        ],
        reports=["done [S1]"],
    )
    await DeepResearchWorkflow(retrieval, llm).invoke(
        "질문",
        PolicyContext.from_user_id("kim"),
        checkpoint=checkpoint,
        progress_callback=save,
    )
    gap_rounds = [item["checkpoint"] for item in resumed_checkpoints]
    assert gap_rounds
    assert all(item["round_id"] != round_id for item in gap_rounds)
    assert gap_rounds[-1]["rounds"] == 2
    assert [task.query for task, _policy in retrieval.calls] == ["q4"]


@async_test
async def test_deep_total_outage_checkpoint_retries_all_original_queries():
    retrieval = DeepRetrieval()
    calls = []

    async def fail(task, policy):
        calls.append(task.query)
        raise AppError(ErrorCode.INDEX_UNAVAILABLE, "down", retryable=True)

    retrieval.search = fail
    checkpoints = []

    async def save(payload):
        checkpoints.append(payload)

    with pytest.raises(AppError):
        await DeepResearchWorkflow(
            retrieval, DeepLLM(sub_questions=["q1", "q2"])
        ).invoke("질문", PolicyContext.from_user_id("kim"), progress_callback=save)
    saved = checkpoints[-1]["checkpoint"]
    assert saved["pending_queries"] == ["q1", "q2"]
    assert saved["branch_results"] == []

    calls.clear()
    with pytest.raises(AppError):
        await DeepResearchWorkflow(retrieval, DeepLLM()).invoke(
            "질문", PolicyContext.from_user_id("kim"), checkpoint=saved
        )
    assert sorted(calls) == ["q1", "q2"]


@async_test
async def test_repeated_total_outage_reclaims_never_exceed_twelve_actual_calls():
    retrieval = DeepRetrieval()
    actual_calls = []

    async def fail(task, policy):
        actual_calls.append(task.query)
        raise AppError(ErrorCode.INDEX_UNAVAILABLE, "down", retryable=True)

    retrieval.search = fail
    checkpoint = None
    for _attempt in range(10):
        saved = []

        async def persist(payload):
            saved.append(payload["checkpoint"])

        with pytest.raises(AppError):
            await DeepResearchWorkflow(
                retrieval, DeepLLM(sub_questions=["q1", "q2", "q3"])
            ).invoke(
                "질문",
                PolicyContext.from_user_id("kim"),
                checkpoint=checkpoint,
                progress_callback=persist,
            )
        if saved:
            checkpoint = saved[-1]
        assert len(actual_calls) <= MAX_SEARCHES
    assert len(actual_calls) == MAX_SEARCHES
    assert checkpoint["searches"] == MAX_SEARCHES

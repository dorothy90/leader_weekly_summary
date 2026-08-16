import asyncio
from datetime import UTC, datetime
import re
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.domain.agentic import (
    JudgeDecision,
    QueryAnalysis,
    SearchDocument,
    SearchResult,
    ToolAction,
)
from app.domain.chat import ChatRequest, ChatResponse
from app.domain.policy import PolicyContext
from app.graphs.fast_rag import FastRAGWorkflow
from app.graphs.multi_source import (
    LIMIT_DISCLOSURE,
    MAX_ITERATIONS,
    MultiSourceAgenticWorkflow,
)
from app.llm.agentic import RuleBasedAgentModel, StructuredAgentModel
from app.persistence.conversations import (
    ConversationMemory,
    apply_agent_memory_update,
)
from app.retrieval.dates import resolve_time_range
from app.retrieval.multi_source import InMemoryMultiSourceSearch
from app.retrieval.source_registry import SourceRegistry


QUESTION = (
    "김OO이 지난주 메일에서 이야기한 NAND 수율 문제가 "
    "어떤 회의에서 논의됐고 어떤 Action을 하기로 했으며 "
    "기술적으로 어떤 의미인지 설명해줘."
)
NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


def build():
    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    return MultiSourceAgenticWorkflow(search, RuleBasedAgentModel(now=NOW)), search


def invoke(workflow, message, memory=None):
    return asyncio.run(
        workflow.invoke(
            ChatRequest(user_id="kim", message=message),
            PolicyContext.from_user_id("kim"),
            memory or ConversationMemory(),
        )
    )


def test_mail_calendar_expand_domain_canonical_flow():
    workflow, search = build()

    result = invoke(workflow, QUESTION)

    tools = [action.tool for action, _owner in search.calls]
    assert tools == [
        "search_mail",
        "search_calendar",
        "expand_calendar_event",
        "search_domain_knowledge",
    ]
    assert "FDC" in result.answer
    assert "Cell Leakage" in result.answer
    assert {item.source_type for item in result.evidence} == {
        "mail",
        "calendar",
        "domain_knowledge",
    }
    assert result.quality.citation_valid is True
    assert result.quality.limited_answer is False
    assert result.agent_trace.iteration_count <= MAX_ITERATIONS == 4


def test_domain_mail_and_calendar_single_source_questions():
    cases = [
        ("Cell Leakage가 뭐야?", ["search_domain_knowledge"]),
        ("NAND 관련 메일 찾아줘", ["search_mail"]),
        ("NAND Yield Review 회의 언제 했어?", ["search_calendar"]),
    ]
    for question, expected in cases:
        workflow, search = build()

        result = invoke(workflow, question)

        assert [action.tool for action, _owner in search.calls] == expected
        assert result.evidence


def test_follow_up_expands_previous_event_before_semantic_search():
    workflow, search = build()
    memory = ConversationMemory.model_validate(
        {
            "previous_event_reference": {
                "event_id": "event-kim-1",
                "subject": "NAND Yield Review",
            }
        }
    )

    result = invoke(workflow, "그 회의에서 Action 뭐였어?", memory)

    assert search.calls[0][0].tool == "expand_calendar_event"
    assert search.calls[0][0].event_id == "event-kim-1"
    assert "FDC" in result.answer


def test_structured_planner_cannot_override_saved_event_id_for_follow_up():
    class FuzzyCalendarLLM:
        def __init__(self):
            self.calls = 0

        async def complete_model(self, system, user, schema):
            self.calls += 1
            return {
                "tool": "search_calendar",
                "query": "NAND Yield Review",
                "reason": "fuzzy search",
            }

    memory = ConversationMemory.model_validate(
        {
            "previous_event_reference": {
                "event_id": "event-kim-1",
                "subject": "NAND Yield Review",
            }
        }
    )
    fallback = RuleBasedAgentModel(now=NOW)
    analysis = asyncio.run(
        fallback.analyze("그 회의에서 Action 뭐였어?", memory, "Asia/Seoul")
    )
    llm = FuzzyCalendarLLM()
    model = StructuredAgentModel(llm, fallback=fallback, now=NOW)

    action = asyncio.run(
        model.plan("그 회의에서 Action 뭐였어?", analysis, [], memory)
    )

    assert action.tool == "expand_calendar_event"
    assert action.event_id == "event-kim-1"
    assert llm.calls == 0


def test_raw_same_event_reference_overrides_misclassified_structured_analysis():
    class FuzzyCalendarLLM:
        def __init__(self):
            self.calls = 0

        async def complete_model(self, system, user, schema):
            self.calls += 1
            return {
                "tool": "search_calendar",
                "query": "NAND Yield Review",
                "reason": "fuzzy search",
            }

    memory = ConversationMemory.model_validate(
        {
            "previous_event_reference": {
                "event_id": "event-kim-1",
                "subject": "NAND Yield Review",
            }
        }
    )
    analysis = QueryAnalysis(
        intent="knowledge_query",
        question_type="calendar_search",
        information_needs=["관련 회의"],
    )
    llm = FuzzyCalendarLLM()
    model = StructuredAgentModel(llm, fallback=RuleBasedAgentModel(now=NOW))

    action = asyncio.run(
        model.plan("해당 회의 Action이 뭐였어?", analysis, [], memory)
    )

    assert action.tool == "expand_calendar_event"
    assert action.event_id == "event-kim-1"
    assert llm.calls == 0


def test_calendar_event_without_requested_action_is_not_judged_sufficient():
    class EventOnlySearch:
        def __init__(self):
            self.calls = []

        async def execute(
            self, action, policy, analysis, request_filters=None
        ):
            self.calls.append(action)
            return SearchResult(
                tool=action.tool,
                query=action.query,
                documents=[
                    SearchDocument(
                        source_type="calendar",
                        document_id="event-kim-no-action",
                        source_id="event-kim-no-action",
                        parent_event_id="event-kim-no-action",
                        content_kind="event",
                        title="NAND Review",
                        text="NAND 수율 검토 회의",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="deterministic",
            )

    search = EventOnlySearch()
    workflow = MultiSourceAgenticWorkflow(search, RuleBasedAgentModel())

    result = invoke(workflow, "NAND 회의에서 Action 뭐였어?")

    assert [action.tool for action in search.calls] == [
        "search_calendar",
        "expand_calendar_event",
    ]
    assert result.quality.limited_answer is True
    assert "확인하지 못한 항목" in result.answer


def test_model_sufficiency_cannot_override_a_missing_required_source():
    class EmptySearch:
        def __init__(self):
            self.calls = []

        async def execute(
            self, action, policy, analysis, request_filters=None
        ):
            self.calls.append(action)
            return SearchResult(
                tool=action.tool,
                query=action.query,
                retrieval_mode="deterministic",
            )

    class SelfApprovingJudge(RuleBasedAgentModel):
        async def judge(self, state):
            return JudgeDecision(
                sufficient=True,
                reason="model says complete",
                missing_information=[],
                recommended_action=None,
            )

    search = EmptySearch()
    workflow = MultiSourceAgenticWorkflow(search, SelfApprovingJudge())

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert [action.tool for action in search.calls] == ["search_mail"]
    assert "duplicate_search_blocked" in result.agent_trace.judge_decisions
    assert result.agent_memory.unresolved_information == ["관련 메일"]
    assert result.quality.limited_answer is True


def test_question_type_requires_its_source_when_model_omits_information_needs():
    analysis = QueryAnalysis(
        intent="knowledge_query",
        question_type="mail_search",
        information_needs=[],
    )

    missing = RuleBasedAgentModel.deterministic_missing(analysis, [])

    assert missing == ["관련 메일"]


def test_model_sufficiency_cannot_override_missing_calendar_action_detail():
    class EventOnlySearch:
        async def execute(
            self, action, policy, analysis, request_filters=None
        ):
            return SearchResult(
                tool=action.tool,
                query=action.query,
                documents=[
                    SearchDocument(
                        source_type="calendar",
                        document_id="event-kim-no-action",
                        source_id="event-kim-no-action",
                        parent_event_id="event-kim-no-action",
                        content_kind="event",
                        title="NAND Review",
                        text="NAND 수율 검토 회의",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="deterministic",
            )

    class SelfApprovingJudge(RuleBasedAgentModel):
        async def judge(self, state):
            return JudgeDecision(
                sufficient=True,
                reason="model says complete",
                missing_information=[],
                recommended_action=None,
            )

    workflow = MultiSourceAgenticWorkflow(
        EventOnlySearch(), SelfApprovingJudge()
    )

    result = invoke(workflow, "NAND 회의에서 Action 뭐였어?")

    assert result.agent_memory.unresolved_information == ["관련 회의와 Action"]
    assert result.quality.limited_answer is True
    assert result.execution.status == "limited"


def test_duplicate_search_is_blocked_without_a_duplicate_backend_call():
    class RepeatingModel(RuleBasedAgentModel):
        async def judge(self, state):
            action = state["current_action"]
            return {
                "sufficient": False,
                "reason": "repeat",
                "missing_information": ["never complete"],
                "recommended_action": action.model_dump(),
            }

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(search, RepeatingModel())

    result = invoke(workflow, "NAND 메일 찾아줘")

    fingerprints = [
        (
            action.tool,
            " ".join(action.query.casefold().split()),
            action.event_id,
            tuple(sorted(action.content_kinds)),
        )
        for action, _owner in search.calls
    ]
    assert len(fingerprints) == len(set(fingerprints)) == 1
    assert result.agent_trace.iteration_count == 1
    assert "duplicate_search_blocked" in result.agent_trace.judge_decisions
    assert result.quality.limited_answer is True
    assert result.execution.error_code == "INSUFFICIENT_EVIDENCE"


def test_semantic_iteration_limit_is_exactly_four_and_disclosed():
    class ExhaustingModel(RuleBasedAgentModel):
        async def plan(self, question, analysis, observations, memory):
            return ToolAction(
                tool="search_mail",
                query="missing-0",
                reason="bounded search",
            )

        async def judge(self, state):
            next_index = state["iteration_count"]
            return JudgeDecision(
                sufficient=False,
                reason="still missing",
                missing_information=["never complete"],
                recommended_action=ToolAction(
                    tool="search_mail",
                    query=f"missing-{next_index}",
                    reason="bounded search",
                ),
            )

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(search, ExhaustingModel())

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert len(search.calls) == MAX_ITERATIONS == 4
    assert result.agent_trace.iteration_count == MAX_ITERATIONS
    assert result.quality.limited_answer is True
    assert LIMIT_DISCLOSURE in result.answer


def test_unique_event_expansions_cannot_bypass_the_total_action_bound():
    class ExpandingModel(RuleBasedAgentModel):
        async def plan(self, question, analysis, observations, memory):
            return ToolAction(
                tool="expand_calendar_event",
                event_id="missing-event-0",
                reason="bounded expansion",
            )

        async def judge(self, state):
            next_index = len(state["observations"])
            return JudgeDecision(
                sufficient=False,
                reason="still missing",
                missing_information=["never complete"],
                recommended_action=ToolAction(
                    tool="expand_calendar_event",
                    event_id=f"missing-event-{next_index}",
                    reason="bounded expansion",
                ),
            )

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(search, ExpandingModel())

    result = invoke(workflow, "그 회의 Action 알려줘")

    assert len(search.calls) == MAX_ITERATIONS == 4
    assert result.quality.limited_answer is True
    assert result.agent_trace.iteration_count == 1
    assert "search_calendar" in result.agent_trace.tool_calls


def test_invalid_structured_output_retries_once_then_falls_back():
    class BrokenStructuredLLM:
        def __init__(self):
            self.calls = 0

        async def complete_model(self, system, user, schema):
            self.calls += 1
            raise ValueError("invalid structured output")

        async def complete_text(self, system, user):
            return ""

    llm = BrokenStructuredLLM()
    model = StructuredAgentModel(llm, fallback=RuleBasedAgentModel())

    analysis = asyncio.run(
        model.analyze(
            "Cell Leakage가 뭐야?",
            ConversationMemory(),
            "Asia/Seoul",
        )
    )

    assert llm.calls == 2
    assert analysis.question_type == "domain_knowledge"


def test_structured_answer_falls_back_when_cited_claim_is_unsupported():
    class UnsupportedAnswerLLM:
        async def complete_text(self, system, user):
            return "근거에 없는 임의 사실입니다. [S1]"

        async def complete_model(self, system, user, schema):
            return {"supported": False}

    fallback = RuleBasedAgentModel()
    analysis = asyncio.run(
        fallback.analyze(
            "NAND Action이 뭐였어?", ConversationMemory(), "Asia/Seoul"
        )
    )
    documents = [
        SearchDocument(
            source_type="calendar",
            document_id="event-kim-1-action",
            content_kind="attachment",
            title="NAND Action",
            text="회의 Action은 장비 A의 FDC 로그를 점검하는 것이다.",
            score=1.0,
        )
    ]
    model = StructuredAgentModel(UnsupportedAnswerLLM(), fallback=fallback)

    answer = asyncio.run(
        model.answer("NAND Action이 뭐였어?", analysis, documents, [])
    )

    assert "근거에 없는 임의 사실" not in answer
    assert "FDC 로그" in answer
    assert "[S1]" in answer


def test_structured_answer_cannot_self_approve_a_fabricated_cited_claim():
    class SelfApprovingLLM:
        def __init__(self):
            self.support_calls = 0

        async def complete_model(self, system, user, schema):
            if schema.__name__ == "QueryAnalysis":
                return {
                    "intent": "knowledge_query",
                    "question_type": "mail_search",
                    "entities": {"product": "NAND"},
                    "information_needs": ["관련 메일"],
                }
            if schema.__name__ == "ToolAction":
                return {
                    "tool": "search_mail",
                    "query": "NAND",
                    "reason": "mail evidence",
                }
            if schema.__name__ == "JudgeDecision":
                return {
                    "sufficient": True,
                    "reason": "self approved",
                    "missing_information": [],
                    "recommended_action": None,
                }
            self.support_calls += 1
            return {"supported": True}

        async def complete_text(self, system, user):
            return "NAND 수율은 99.9%로 확정됐습니다. [S1]"

    llm = SelfApprovingLLM()
    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        StructuredAgentModel(llm, fallback=RuleBasedAgentModel(now=NOW), now=NOW),
    )

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert "99.9%" not in result.answer
    assert "Cell Leakage" in result.answer
    assert result.quality.citation_valid is True
    assert result.execution.status == "succeeded"
    assert llm.support_calls == 0


def test_model_authored_utc_is_replaced_by_deterministic_resolution():
    class MisleadingStructuredLLM:
        async def complete_model(self, system, user, schema):
            return {
                "intent": "knowledge_query",
                "question_type": "mail_search",
                "entities": {"product": "NAND"},
                "time_expression": "어제",
                "start_at_utc": "2000-01-01T00:00:00Z",
                "end_at_utc": "2000-01-02T00:00:00Z",
                "information_needs": ["관련 메일"],
            }

    model = StructuredAgentModel(MisleadingStructuredLLM(), now=NOW)

    analysis = asyncio.run(
        model.analyze(
            "지난주 NAND 메일 찾아줘", ConversationMemory(), "Asia/Seoul"
        )
    )

    expected = resolve_time_range(
        "지난주", now=NOW, timezone_name="Asia/Seoul"
    )
    assert analysis.time_expression == "지난주"
    assert analysis.start_at_utc == expected.start_at_utc
    assert analysis.end_at_utc == expected.end_at_utc


@pytest.mark.parametrize(
    "hostile_analysis",
    [
        {
            "intent": "knowledge_query",
            "question_type": "general_chat",
            "entities": {},
            "information_needs": [],
        },
        {
            "intent": "knowledge_query",
            "question_type": "multi_source",
            "entities": {"product": "NAND"},
            "information_needs": ["기술적 의미"],
        },
    ],
    ids=["general-chat-empty", "multi-source-wrong-needs"],
)
def test_structured_analysis_cannot_remove_raw_multi_source_requirements(
    hostile_analysis,
):
    class HostileStructuredLLM:
        async def complete_model(self, system, user, schema):
            if schema.__name__ == "QueryAnalysis":
                return hostile_analysis
            if schema.__name__ == "ToolAction":
                return {
                    "tool": "search_mail",
                    "query": "NAND",
                    "reason": "stop after one source",
                }
            return {
                "sufficient": True,
                "reason": "one source is enough",
                "missing_information": [],
                "recommended_action": None,
            }

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        StructuredAgentModel(HostileStructuredLLM(), now=NOW),
    )

    result = invoke(workflow, QUESTION)

    assert [action.tool for action, _owner in search.calls] == [
        "search_mail",
        "search_calendar",
        "expand_calendar_event",
        "search_domain_knowledge",
    ]
    assert {item.source_type for item in result.evidence} == {
        "mail",
        "calendar",
        "domain_knowledge",
    }
    assert result.execution.status == "succeeded"


def test_structured_analysis_may_add_need_without_removing_baseline_needs():
    class AdditiveStructuredLLM:
        async def complete_model(self, system, user, schema):
            return {
                "intent": "knowledge_query",
                "question_type": "domain_knowledge",
                "entities": {"product": "NAND"},
                "information_needs": ["기술적 의미"],
            }

    model = StructuredAgentModel(AdditiveStructuredLLM(), now=NOW)

    analysis = asyncio.run(
        model.analyze(
            "NAND 관련 메일 찾아줘",
            ConversationMemory(),
            "Asia/Seoul",
        )
    )

    assert analysis.information_needs == ["관련 메일", "기술적 의미"]
    assert analysis.question_type == "multi_source"


def test_answer_citations_and_evidence_are_grounded_and_normalized():
    workflow, _search = build()

    result = invoke(workflow, QUESTION)

    evidence_ids = [item.evidence_id for item in result.evidence]
    cited_ids = re.findall(r"\[(S\d+)\]", result.answer)
    assert evidence_ids == [f"S{index}" for index in range(1, len(evidence_ids) + 1)]
    assert cited_ids
    assert set(cited_ids) <= set(evidence_ids)
    assert all(item.user_id == "kim" for item in result.evidence)
    assert all(len(item.content_hash) == 64 for item in result.evidence)
    assert all(
        item.acl_decision_id == PolicyContext.from_user_id("kim").decision_id
        for item in result.evidence
    )
    assert all(
        item.source_locator is None
        or item.source_locator.startswith(("mail:", "calendar:", "domain:"))
        for item in result.evidence
    )
    assert "analysis" not in result.model_dump()
    assert "question_type" not in result.model_dump_json()
    assert "agent_memory" not in ChatResponse.model_fields
    assert "agent_trace" not in ChatResponse.model_fields


def test_graph_dedup_preserves_cross_source_storage_id_collisions():
    documents = [
        SearchDocument(
            source_type="mail",
            document_id="shared-storage-id",
            content_kind="body",
            text="mail evidence",
            score=1.0,
        ),
        SearchDocument(
            source_type="calendar",
            document_id="shared-storage-id",
            source_id="event-42",
            parent_event_id="event-42",
            content_kind="event",
            text="calendar evidence",
            score=0.9,
        ),
        SearchDocument(
            source_type="domain_knowledge",
            document_id="shared-storage-id",
            text="domain evidence",
            score=0.8,
        ),
    ]

    deduplicated = MultiSourceAgenticWorkflow._deduplicate_documents(documents)

    assert len(deduplicated) == 3
    assert {item.source_type for item in deduplicated} == {
        "mail",
        "calendar",
        "domain_knowledge",
    }


def test_graph_dedup_preserves_calendar_event_attachment_storage_id_collision():
    event = SearchDocument(
        source_type="calendar",
        document_id="shared-storage-id",
        source_id="event-42",
        parent_event_id="event-42",
        content_kind="event",
        text="meeting",
        score=1.0,
    )
    attachment = SearchDocument(
        source_type="calendar",
        document_id="shared-storage-id",
        source_id="attachment-42",
        parent_event_id="event-42",
        content_kind="attachment",
        text="Action: inspect FDC",
        score=0.9,
    )

    deduplicated = MultiSourceAgenticWorkflow._deduplicate_documents(
        [event, event.model_copy(update={"score": 0.5}), attachment]
    )

    assert len(deduplicated) == 2
    assert {item.content_kind for item in deduplicated} == {
        "event",
        "attachment",
    }
    assert next(item for item in deduplicated if item.content_kind == "event").score == 1


def test_event_memory_uses_stable_event_source_id_not_storage_id():
    reference = MultiSourceAgenticWorkflow._event_reference(
        [
            SearchDocument(
                source_type="calendar",
                document_id="doc-42",
                source_id="event-42",
                content_kind="event",
                title="NAND Yield Review",
                text="meeting",
                score=1.0,
            )
        ]
    )

    assert reference.event_id == "event-42"


def test_event_memory_never_falls_back_to_calendar_storage_id():
    reference = MultiSourceAgenticWorkflow._event_reference(
        [
            SearchDocument(
                source_type="calendar",
                document_id="doc-42",
                content_kind="event",
                title="NAND Yield Review",
                text="meeting",
                score=1.0,
            )
        ]
    )

    assert reference is None


def test_event_memory_can_use_stable_attachment_parent_relation():
    reference = MultiSourceAgenticWorkflow._event_reference(
        [
            SearchDocument(
                source_type="calendar",
                document_id="doc-attachment-42",
                source_id="attachment-42",
                parent_event_id="event-42",
                content_kind="attachment",
                title="NAND action.pdf",
                text="Action: inspect FDC",
                score=1.0,
            )
        ]
    )

    assert reference.event_id == "event-42"


def test_agentic_result_preserves_bm25_retrieval_mode():
    class BM25Search:
        async def execute(
            self, action, policy, analysis, request_filters=None
        ):
            return SearchResult(
                tool=action.tool,
                query=action.query,
                documents=[
                    SearchDocument(
                        source_type="mail",
                        document_id="mail-kim-bm25",
                        content_kind="body",
                        title="NAND 수율",
                        text="NAND 수율 검토 메일이다.",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="bm25",
                disclosures=["embedding unavailable"],
            )

    workflow = MultiSourceAgenticWorkflow(BM25Search(), RuleBasedAgentModel())

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert result.quality.retrieval_mode == "bm25"
    assert result.disclosures == ["embedding unavailable"]


def test_agentic_result_preserves_deterministic_retrieval_mode():
    workflow, _search = build()

    result = invoke(workflow, "NAND 관련 메일 찾아줘")

    assert result.quality.retrieval_mode == "deterministic"


def test_limited_answer_reserves_a_disclosure_slot_for_the_limit_message():
    class FourDisclosureSearch:
        async def execute(
            self, action, policy, analysis, request_filters=None
        ):
            return SearchResult(
                tool=action.tool,
                query=action.query,
                retrieval_mode="deterministic",
                disclosures=["one", "two", "three", "four"],
            )

    workflow = MultiSourceAgenticWorkflow(
        FourDisclosureSearch(), RuleBasedAgentModel()
    )

    result = invoke(workflow, "NAND 관련 메일 찾아줘")

    assert result.quality.limited_answer is True
    assert len(result.disclosures) == 4
    assert LIMIT_DISCLOSURE in result.disclosures


def test_hostile_judge_output_is_sanitized_from_answer_trace_and_memory():
    class HostileJudgeModel(RuleBasedAgentModel):
        async def judge(self, state):
            return JudgeDecision(
                sufficient=False,
                reason="<analysis>trace secret</analysis>",
                missing_information=[
                    "<analysis>answer secret</analysis>, /srv/private/password.txt"
                ],
                recommended_action=None,
            )

        async def answer(self, question, analysis, documents, missing):
            return "확인된 근거입니다. [S1]"

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(search, HostileJudgeModel())

    result = invoke(workflow, "NAND 메일 찾아줘")
    serialized = result.model_dump_json()

    assert "trace secret" not in serialized
    assert "answer secret" not in serialized
    assert "/srv/private/password.txt" not in serialized
    assert "[REDACTED_PATH]" not in result.answer
    assert result.agent_memory.unresolved_information == []


def test_no_evidence_cannot_persist_model_authored_memory_fields():
    class EmptySearch:
        async def execute(
            self, action, policy, analysis, request_filters=None
        ):
            return SearchResult(
                tool=action.tool,
                query=action.query,
                retrieval_mode="deterministic",
            )

    class HostileMemoryModel(RuleBasedAgentModel):
        async def analyze(self, question, memory, timezone_name):
            return QueryAnalysis(
                intent="knowledge_query",
                question_type="mail_search",
                entities={
                    "product": "TOP_SECRET",
                    "issue": "MODEL_INVENTED",
                },
                information_needs=["관련 메일"],
            )

        async def judge(self, state):
            return JudgeDecision(
                sufficient=True,
                reason="hostile complete",
                missing_information=["HOSTILE_MISSING"],
                recommended_action=None,
            )

    trusted = ConversationMemory(
        entities={"product": "TRUSTED_NAND"},
        current_topic="Trusted topic",
        unresolved_information=["Trusted gap"],
    )
    policy = PolicyContext.from_user_id("kim")
    workflow = MultiSourceAgenticWorkflow(EmptySearch(), HostileMemoryModel())

    result = asyncio.run(
        workflow.invoke(
            ChatRequest(user_id="kim", message="NAND 메일 찾아줘"),
            policy,
            trusted,
        )
    )
    persisted = apply_agent_memory_update(trusted, result.agent_memory, policy)

    assert result.agent_memory.entities == {}
    assert result.agent_memory.current_topic is None
    assert result.agent_memory.unresolved_information == ["관련 메일"]
    assert "HOSTILE_MISSING" not in result.model_dump_json()
    assert persisted.entities == {"product": "TRUSTED_NAND"}
    assert persisted.current_topic == "Trusted topic"


def test_request_mail_facets_are_forwarded_to_source_search():
    class FilterRecordingSearch:
        def __init__(self):
            self.request_filters = []

        async def execute(
            self,
            action,
            policy,
            analysis,
            request_filters=None,
        ):
            self.request_filters.append(request_filters)
            return SearchResult(
                tool=action.tool,
                query=action.query,
                documents=[
                    SearchDocument(
                        source_type="mail",
                        document_id="mail-filtered",
                        content_kind="body",
                        title="NAND weekly report",
                        text="NAND 수율 검토 메일이다.",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="deterministic",
            )

    search = FilterRecordingSearch()
    workflow = MultiSourceAgenticWorkflow(search, RuleBasedAgentModel())

    result = asyncio.run(
        workflow.invoke(
            ChatRequest(
                user_id="kim",
                message="NAND 메일 찾아줘",
                filters={
                    "teams": ["YIELD팀"],
                    "weeks": ["2026-08"],
                    "mail_type": "weekly_report",
                },
            ),
            PolicyContext.from_user_id("kim"),
            ConversationMemory(),
        )
    )

    assert result.evidence
    assert search.request_filters == [
        ChatRequest(
            user_id="kim",
            message="NAND 메일 찾아줘",
            filters={
                "teams": ["YIELD팀"],
                "weeks": ["2026-08"],
                "mail_type": "weekly_report",
            },
        ).filters
    ]


def test_source_failure_returns_partial_grounded_result_instead_of_raising():
    class PartiallyFailingSearch:
        def __init__(self):
            self.calls = []

        async def execute(
            self,
            action,
            policy,
            analysis,
            request_filters=None,
        ):
            self.calls.append(action.tool)
            if action.tool == "search_mail":
                return SearchResult(
                    tool=action.tool,
                    query=action.query,
                    documents=[
                        SearchDocument(
                            source_type="mail",
                            document_id="mail-partial",
                            content_kind="body",
                            title="NAND yield mail",
                            text="NAND 수율 이슈가 보고됐다.",
                            score=1.0,
                        )
                    ],
                    total_hits=1,
                    retrieval_mode="hybrid",
                )
            raise RuntimeError("calendar backend secret failure")

    search = PartiallyFailingSearch()
    workflow = MultiSourceAgenticWorkflow(search, RuleBasedAgentModel())

    result = invoke(workflow, "NAND 메일과 회의 찾아줘")

    assert [item.source_type for item in result.evidence] == ["mail"]
    assert result.quality.limited_answer is True
    assert result.execution.status == "limited"
    assert "calendar backend secret failure" not in result.model_dump_json()


def test_calendar_expansion_failure_is_reported_as_typed_partial_result():
    class ExpansionFailingSearch:
        async def execute(
            self,
            action,
            policy,
            analysis,
            request_filters=None,
        ):
            if action.tool == "expand_calendar_event":
                raise TimeoutError("calendar attachment timeout secret")
            return SearchResult(
                tool=action.tool,
                query=action.query,
                documents=[
                    SearchDocument(
                        source_type="calendar",
                        document_id="event-storage-1",
                        source_id="event-1",
                        parent_event_id="event-1",
                        content_kind="event",
                        title="NAND Yield Review",
                        text="NAND 수율 검토 회의",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="hybrid",
            )

    workflow = MultiSourceAgenticWorkflow(
        ExpansionFailingSearch(),
        RuleBasedAgentModel(),
    )

    result = invoke(workflow, "NAND 회의에서 Action 뭐였어?")

    assert [item.source_type for item in result.evidence] == ["calendar"]
    assert result.execution.status == "limited"
    assert result.execution.error_code == "SOURCE_UNAVAILABLE"
    assert "calendar attachment timeout secret" not in result.model_dump_json()


def test_deterministic_required_source_precedes_irrelevant_model_plan():
    class IrrelevantPlanner(RuleBasedAgentModel):
        async def plan(self, question, analysis, observations, memory):
            return ToolAction(
                tool="search_domain_knowledge",
                query="NAND",
                reason="irrelevant model preference",
            )

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(search, IrrelevantPlanner(now=NOW))

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert search.calls[0][0].tool == "search_mail"
    assert {item.source_type for item in result.evidence} == {"mail"}


def test_deterministic_required_source_also_controls_initial_query():
    class EmptyMailQueryPlanner(RuleBasedAgentModel):
        async def plan(self, question, analysis, observations, memory):
            return ToolAction(
                tool="search_mail",
                query="definitely-no-such-mail-token",
                reason="same source but unusable model query",
            )

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        EmptyMailQueryPlanner(now=NOW),
    )

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert search.calls[0][0].query == "NAND"
    assert len(search.calls) == 1
    assert {item.source_type for item in result.evidence} == {"mail"}


def test_fast_rag_facade_delegates_without_entering_legacy_graph():
    expected, _search = build()
    expected_result = invoke(expected, "Cell Leakage가 뭐야?")

    class AgenticSpy:
        def __init__(self):
            self.calls = []

        async def invoke(self, request, policy, conversation):
            self.calls.append((request, policy, conversation))
            return expected_result

    class FailIfCalled:
        def __getattr__(self, name):
            raise AssertionError(f"legacy dependency called: {name}")

    spy = AgenticSpy()
    workflow = FastRAGWorkflow(FailIfCalled(), FailIfCalled(), agentic=spy)
    memory = ConversationMemory()
    request = ChatRequest(user_id="kim", message="Cell Leakage가 뭐야?")
    policy = PolicyContext.from_user_id("kim")

    result = asyncio.run(workflow.invoke(request, policy, memory))

    assert result == expected_result
    assert spy.calls == [(request, policy, memory)]

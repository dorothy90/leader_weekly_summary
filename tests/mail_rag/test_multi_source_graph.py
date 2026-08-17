import asyncio
from datetime import UTC, datetime
import json
import re
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.domain.agentic import (
    IntentDecision,
    JudgeDecision,
    QueryAnalysis,
    SearchDocument,
    SearchResult,
    SourceRequest,
    ToolAction,
)
from app.domain.agentic_policy import TypedAgentPolicy
from app.domain.chat import ChatRequest, ChatResponse
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.graphs.multi_source import (
    LIMIT_DISCLOSURE,
    MAX_ITERATIONS,
    MultiSourceAgenticWorkflow,
)
from app.llm.agentic import StructuredAgentModel
from app.persistence.conversations import (
    ConversationMemory,
    apply_agent_memory_update,
)
from app.retrieval.multi_source import InMemoryMultiSourceSearch
from app.retrieval.source_registry import SourceRegistry


QUESTION = (
    "김OO이 지난주 메일에서 이야기한 NAND 수율 문제가 "
    "어떤 회의에서 논의됐고 어떤 Action을 하기로 했으며 "
    "기술적으로 어떤 의미인지 설명해줘."
)
NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


class StaticIntentAnalyzer:
    def __init__(self, decision, *, now=NOW):
        self.decision = decision
        self.now = now
        self.policy = TypedAgentPolicy()

    async def analyze(self, question, memory, timezone_name):
        return QueryAnalysis.from_intent(
            self.decision, now=self.now, timezone_name=timezone_name
        )

    async def plan(self, question, analysis, observations, memory):
        del question
        return self.policy.next_action(analysis, observations, memory)

    async def judge(
        self,
        question,
        analysis,
        observations,
        documents,
        memory,
        iteration_count,
    ):
        del question
        decision = self.policy.judge(
            analysis,
            observations,
            documents,
            memory,
            iteration_count,
        )
        if (
            not decision.sufficient
            and decision.recommended_action is None
            and analysis.calendar_detail_required
        ):
            event = next(
                (
                    item
                    for item in documents
                    if item.source_type == "calendar"
                    and item.content_kind == "event"
                    and item.source_id
                ),
                None,
            )
            if event is not None:
                return decision.model_copy(
                    update={
                        "recommended_action": ToolAction(
                            tool="expand_calendar_event",
                            event_id=event.source_id,
                            reason="scripted test expansion",
                        )
                    }
                )
        return decision

    async def answer(self, question, analysis, documents, missing, memory):
        del question, analysis, memory
        return self.policy.answer(documents, missing)


def analyzer(*requests, **decision_fields):
    return StaticIntentAnalyzer(
        IntentDecision(
            source_requests=list(requests),
            intent="test",
            **decision_fields,
        )
    )


class FixedIntentLLM:
    def __init__(self, decision):
        self.decision = decision
        self.schemas = []
        self.users = []

    async def complete_model(self, system, user, schema):
        self.schemas.append(schema)
        self.users.append(user)
        return self.decision


class RaisingIntentLLM:
    def __init__(self, failure):
        self.failure = failure
        self.calls = 0

    async def complete_model(self, system, user, schema):
        self.calls += 1
        raise self.failure


class StageLLM:
    def __init__(self):
        self.schemas = []

    async def complete_model(self, system, user, schema):
        self.schemas.append(schema.__name__)
        if schema.__name__ == "PlanningDecision":
            return {
                "action": {
                    "tool": "search_calendar",
                    "query": "이번 주 일정",
                    "reason": "일정 근거 검색",
                },
                "reason": "calendar evidence required",
            }
        if schema.__name__ == "JudgeDecision":
            return {
                "sufficient": True,
                "reason": "calendar evidence found",
                "missing_information": [],
                "recommended_action": None,
            }
        if schema.__name__ == "AnswerDecision":
            return {"answer": "이번 주 일정입니다. [S1]"}
        raise AssertionError(schema)


class UnavailableAnalyzer:
    async def analyze(self, question, memory, timezone_name):
        return QueryAnalysis.unavailable()


class AsyncPolicyAdapter:
    def __init__(self, intent_analyzer, policy):
        self.intent_analyzer = intent_analyzer
        self.policy = policy

    async def analyze(self, question, memory, timezone_name):
        return await self.intent_analyzer.analyze(
            question, memory, timezone_name
        )

    async def plan(self, question, analysis, observations, memory):
        del question
        return self.policy.next_action(analysis, observations, memory)

    async def judge(
        self,
        question,
        analysis,
        observations,
        documents,
        memory,
        iteration_count,
    ):
        del question
        return self.policy.judge(
            analysis,
            observations,
            documents,
            memory,
            iteration_count,
        )

    async def answer(self, question, analysis, documents, missing, memory):
        del question, analysis, memory
        return self.policy.answer(documents, missing)


class RecordingSearch:
    def __init__(self):
        self.calls = []

    async def execute(
        self, action, policy, analysis, request_filters=None
    ):
        self.calls.append((action, policy.user_id))
        return SearchResult(
            tool=action.tool,
            query=action.query,
            retrieval_mode="deterministic",
        )


def test_structured_analyzer_requests_intent_decision_and_does_not_add_sources():
    llm = FixedIntentLLM(
        IntentDecision(
            intent="weekly_schedule",
            source_requests=[
                SourceRequest(source="calendar", query="팀 일정")
            ],
            time_scope="current_week",
        )
    )
    model = StructuredAgentModel(llm, now=NOW)

    analysis = asyncio.run(
        model.analyze(
            "메일 기술 원인 뭐야 같은 단어가 있어도 모델 결정만 사용",
            ConversationMemory(),
            "Asia/Seoul",
        )
    )

    assert llm.schemas == [IntentDecision]
    assert [item.source for item in analysis.source_requests] == ["calendar"]
    assert analysis.time_scope == "current_week"


@pytest.mark.parametrize("failure", [ValueError("bad json"), TimeoutError()])
def test_structured_analyzer_failure_is_unavailable_without_keyword_fallback(
    failure,
):
    llm = RaisingIntentLLM(failure)
    model = StructuredAgentModel(llm, timeout_seconds=0.01)

    analysis = asyncio.run(
        model.analyze(
            "이번주 일정알려줘", ConversationMemory(), "Asia/Seoul"
        )
    )

    assert llm.calls == 2
    assert analysis.analysis_status == "unavailable"
    assert analysis.source_requests == []


def test_structured_analyzer_one_attempt_calls_failing_gateway_once():
    llm = RaisingIntentLLM(ValueError("bad json"))
    model = StructuredAgentModel(
        llm,
        timeout_seconds=0.01,
        attempts=1,
    )

    analysis = asyncio.run(
        model.analyze(
            "이번주 일정알려줘",
            ConversationMemory(),
            "Asia/Seoul",
        )
    )

    assert llm.calls == 1
    assert analysis == QueryAnalysis.unavailable()


def test_structured_analyzer_serializes_only_bounded_safe_memory():
    llm = FixedIntentLLM(IntentDecision(intent="general"))
    model = StructuredAgentModel(llm)
    memory = ConversationMemory(
        entities={f"key-{index}": f"value-{index}" for index in range(20)},
        current_topic="trusted topic",
        search_history=["search_mail"],
        retrieved_source_refs=["private-doc"],
        unresolved_information=["secret gap"],
    )

    asyncio.run(model.analyze("질문", memory, "Asia/Seoul"))

    serialized = json.loads(llm.users[0])["memory"]
    assert serialized == {
        "entities": {
            f"key-{index}": f"value-{index}" for index in range(4, 20)
        },
        "current_topic": "trusted topic",
    }


def test_structured_agent_uses_llm_for_plan_judge_and_answer():
    llm = StageLLM()
    model = StructuredAgentModel(llm, attempts=1)
    query_analysis = QueryAnalysis.from_intent(
        IntentDecision(
            intent="weekly_schedule",
            source_requests=[
                SourceRequest(source="calendar", query="이번 주 일정")
            ],
            time_scope="current_week",
        ),
        now=NOW,
        timezone_name="Asia/Seoul",
    )
    document = SearchDocument(
        source_type="calendar",
        document_id="event-kim-20260817",
        source_id="event-kim-20260817",
        parent_event_id="event-kim-20260817",
        content_kind="event",
        title="NAND 수율 점검 일정",
        text="NAND 수율 점검 회의",
        score=1.0,
    )

    action = asyncio.run(
        model.plan("이번 주 일정 뭐야?", query_analysis, [], ConversationMemory())
    )
    decision = asyncio.run(
        model.judge(
            "이번 주 일정 뭐야?",
            query_analysis,
            [],
            [document],
            ConversationMemory(),
            1,
        )
    )
    answer = asyncio.run(
        model.answer(
            "이번 주 일정 뭐야?",
            query_analysis,
            [document],
            [],
            ConversationMemory(),
        )
    )

    assert action.tool == "search_calendar"
    assert decision.sufficient is True
    assert answer == "이번 주 일정입니다. [S1]"
    assert llm.schemas == ["PlanningDecision", "JudgeDecision", "AnswerDecision"]


def test_graph_uses_agent_for_every_semantic_stage():
    class RecordingAgent:
        def __init__(self):
            self.calls = []

        async def analyze(self, question, memory, timezone_name):
            self.calls.append("routing")
            return QueryAnalysis.from_intent(
                IntentDecision(
                    intent="weekly_schedule",
                    source_requests=[
                        SourceRequest(source="calendar", query="이번 주 일정")
                    ],
                    time_scope="current_week",
                ),
                now=NOW,
                timezone_name=timezone_name,
            )

        async def plan(self, question, analysis, observations, memory):
            self.calls.append("planner")
            return ToolAction(
                tool="search_calendar",
                query="이번 주 일정",
                reason="LLM selected calendar",
            )

        async def judge(
            self,
            question,
            analysis,
            observations,
            documents,
            memory,
            iteration_count,
        ):
            self.calls.append("judge")
            return JudgeDecision(
                sufficient=True,
                reason="calendar evidence is sufficient",
            )

        async def answer(self, question, analysis, documents, missing, memory):
            self.calls.append("answer")
            return "이번 주 일정입니다. [S1]"

    class CalendarSearch:
        async def execute(self, action, policy, analysis, request_filters=None):
            return SearchResult(
                tool=action.tool,
                query=action.query,
                documents=[
                    SearchDocument(
                        source_type="calendar",
                        document_id="event-kim-20260817",
                        source_id="event-kim-20260817",
                        parent_event_id="event-kim-20260817",
                        content_kind="event",
                        title="NAND 수율 점검 일정",
                        text="NAND 수율 점검 회의",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="deterministic",
            )

    agent = RecordingAgent()
    workflow = MultiSourceAgenticWorkflow(CalendarSearch(), agent)

    result = invoke(workflow, "이번 주 일정 뭐야?")

    assert agent.calls == ["routing", "planner", "judge", "answer"]
    assert result.answer == "이번 주 일정입니다. [S1]"


@pytest.mark.parametrize("server_owned_field", ["owner", "index", "tool"])
def test_structured_analyzer_rejects_server_owned_output_after_two_attempts(
    server_owned_field,
):
    llm = FixedIntentLLM(
        {
            "intent": "hostile",
            "source_requests": [],
            server_owned_field: "attacker-controlled",
        }
    )
    model = StructuredAgentModel(llm)

    analysis = asyncio.run(
        model.analyze("질문", ConversationMemory(), "Asia/Seoul")
    )

    assert llm.schemas == [IntentDecision, IntentDecision]
    assert analysis == QueryAnalysis.unavailable()


def test_unavailable_analysis_performs_zero_searches_and_returns_limited_result():
    search = RecordingSearch()
    workflow = MultiSourceAgenticWorkflow(search, UnavailableAnalyzer())

    result = invoke(workflow, "이번주 일정 뭐야?")

    assert search.calls == []
    assert result.evidence == []
    assert result.quality.limited_answer is True
    assert result.execution.status == "limited"
    assert result.execution.failure_stage == "planning"
    assert result.execution.error_code == "ANALYSIS_UNAVAILABLE"
    assert "질문을 안전하게 구조화하지 못했습니다" in result.answer


def test_calendar_only_typed_decision_never_adds_domain_search():
    class CalendarSearch(RecordingSearch):
        async def execute(
            self, action, policy, analysis, request_filters=None
        ):
            self.calls.append((action, policy.user_id))
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
                        title="팀 일정",
                        text="이번 주 팀 일정입니다.",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="deterministic",
            )

    search = CalendarSearch()
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(
            SourceRequest(source="calendar", query="팀 일정"),
            time_scope="current_week",
            information_needs=["뭐야 기술 원인 메일"],
        ),
    )

    result = invoke(workflow, "문구는 정책 입력이 아니다")

    assert [action.tool for action, _owner in search.calls] == [
        "search_calendar"
    ]
    assert result.quality.limited_answer is False


def build(intent_analyzer=None):
    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    intent_analyzer = intent_analyzer or analyzer(
        SourceRequest(source="mail", query="NAND Cell Leakage"),
        SourceRequest(source="calendar", query="NAND Yield Review"),
        SourceRequest(source="domain_knowledge", query="Cell Leakage"),
        time_scope="previous_week",
        calendar_detail_required=True,
    )
    return MultiSourceAgenticWorkflow(search, intent_analyzer), search


def invoke(workflow, message, memory=None):
    return asyncio.run(
        workflow.invoke(
            ChatRequest(user_id="kim", message=message),
            PolicyContext.from_user_id("kim"),
            memory or ConversationMemory(),
        )
    )


def test_mail_calendar_domain_canonical_flow_avoids_redundant_expansion():
    workflow, search = build()

    result = invoke(workflow, QUESTION)

    tools = [action.tool for action, _owner in search.calls]
    assert tools == [
        "search_mail",
        "search_calendar",
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
        (
            "Cell Leakage가 뭐야?",
            "domain_knowledge",
            "Cell Leakage",
            ["search_domain_knowledge"],
        ),
        ("NAND 관련 메일 찾아줘", "mail", "NAND", ["search_mail"]),
        (
            "NAND Yield Review 회의 언제 했어?",
            "calendar",
            "NAND Yield Review",
            ["search_calendar"],
        ),
    ]
    for question, source, query, expected in cases:
        workflow, search = build(
            analyzer(SourceRequest(source=source, query=query))
        )

        result = invoke(workflow, question)

        assert [action.tool for action, _owner in search.calls] == expected
        assert result.evidence


def test_follow_up_expands_previous_event_before_semantic_search():
    workflow, search = build(
        analyzer(
            SourceRequest(source="calendar", query="NAND Yield Review"),
            event_reference="previous_event",
            calendar_detail_required=True,
        )
    )
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


def test_stale_saved_event_falls_back_to_calendar_search_within_bound():
    class StaleEventSearch:
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
            if action.tool == "expand_calendar_event":
                return SearchResult(
                    tool=action.tool,
                    query=action.query,
                    retrieval_mode="deterministic",
                )
            return SearchResult(
                tool=action.tool,
                query=action.query,
                documents=[
                    SearchDocument(
                        source_type="calendar",
                        document_id="event-current-storage",
                        source_id="event-current",
                        parent_event_id="event-current",
                        content_kind="event",
                        title="Current NAND Yield Review",
                        text="현재 NAND 수율 검토 회의",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="deterministic",
            )

    memory = ConversationMemory.model_validate(
        {
            "previous_event_reference": {
                "event_id": "event-stale",
                "subject": "Stale NAND Yield Review",
            }
        }
    )
    search = StaleEventSearch()
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(
            SourceRequest(source="calendar", query="NAND Yield Review"),
            event_reference="previous_event",
        ),
    )

    result = invoke(workflow, "그 회의 언제였어?", memory)

    assert search.calls[:2] == ["expand_calendar_event", "search_calendar"]
    assert len(result.agent_trace.tool_calls) <= MAX_ITERATIONS
    assert [item.document_id for item in result.evidence] == [
        "event-current-storage"
    ]


def test_typed_policy_authorizes_only_the_saved_event_id_for_follow_up():
    memory = ConversationMemory.model_validate(
        {
            "previous_event_reference": {
                "event_id": "event-kim-1",
                "subject": "NAND Yield Review",
            }
        }
    )
    analysis = QueryAnalysis.from_intent(
        IntentDecision(
            intent="follow_up",
            source_requests=[
                SourceRequest(source="calendar", query="NAND Yield Review")
            ],
            event_reference="previous_event",
            calendar_detail_required=True,
        ),
        now=NOW,
        timezone_name="Asia/Seoul",
    )

    action = TypedAgentPolicy().next_action(analysis, [], memory)

    assert action.tool == "expand_calendar_event"
    assert action.event_id == "event-kim-1"


def test_raw_same_event_words_do_not_override_typed_event_reference():
    memory = ConversationMemory.model_validate(
        {
            "previous_event_reference": {
                "event_id": "event-kim-1",
                "subject": "NAND Yield Review",
            }
        }
    )
    analysis = QueryAnalysis.from_intent(
        IntentDecision(
            intent="calendar",
            source_requests=[
                SourceRequest(source="calendar", query="NAND Yield Review")
            ],
            event_reference="none",
        ),
        now=NOW,
        timezone_name="Asia/Seoul",
    )

    action = TypedAgentPolicy().next_action(analysis, [], memory)

    assert action.tool == "search_calendar"
    assert action.event_id is None


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
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(
            SourceRequest(source="calendar", query="NAND"),
            calendar_detail_required=True,
        ),
    )

    result = invoke(workflow, "NAND 회의에서 Action 뭐였어?")

    assert [action.tool for action in search.calls] == [
        "search_calendar",
        "expand_calendar_event",
    ]
    assert result.quality.limited_answer is True
    assert "확인하지 못한 항목" in result.answer


def test_typed_policy_keeps_a_missing_required_source_unresolved():
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

    search = EmptySearch()
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(SourceRequest(source="mail", query="NAND")),
    )

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert [action.tool for action in search.calls] == ["search_mail"]
    assert "duplicate_search_blocked" not in result.agent_trace.judge_decisions
    assert result.agent_memory.unresolved_information == ["관련 메일"]
    assert result.quality.limited_answer is True


def test_information_needs_do_not_change_typed_required_sources():
    analysis = QueryAnalysis.from_intent(
        IntentDecision(
            intent="mail",
            source_requests=[SourceRequest(source="mail", query="NAND")],
            information_needs=["기술 원인 일정"],
        ),
        now=NOW,
        timezone_name="Asia/Seoul",
    )

    policy = TypedAgentPolicy()

    assert policy.required_sources(analysis) == ["mail"]
    assert policy.missing_information(analysis, []) == ["관련 메일"]


def test_typed_policy_does_not_accept_event_as_calendar_detail():
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

    workflow = MultiSourceAgenticWorkflow(
        EventOnlySearch(),
        analyzer(
            SourceRequest(source="calendar", query="NAND"),
            calendar_detail_required=True,
        ),
    )

    result = invoke(workflow, "NAND 회의에서 Action 뭐였어?")

    assert result.agent_memory.unresolved_information == ["관련 회의 상세 내용"]
    assert result.quality.limited_answer is True
    assert result.execution.status == "limited"


def test_duplicate_search_is_blocked_without_a_duplicate_backend_call():
    class RepeatingPolicy(TypedAgentPolicy):
        @staticmethod
        def next_action(analysis, observations, memory):
            return ToolAction(
                tool="search_mail",
                query="NAND",
                reason="repeat",
            )

        def judge(
            self,
            analysis,
            observations,
            documents,
            memory,
            iteration_count,
        ):
            return JudgeDecision(
                sufficient=False,
                reason="repeat",
                missing_information=["never complete"],
                recommended_action=self.next_action(
                    analysis, observations, memory
                ),
            )

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        AsyncPolicyAdapter(
            analyzer(SourceRequest(source="mail", query="NAND")),
            RepeatingPolicy(),
        ),
    )

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
    class ExhaustingPolicy(TypedAgentPolicy):
        @staticmethod
        def next_action(analysis, observations, memory):
            return ToolAction(
                tool="search_mail",
                query=f"missing-{len(observations)}",
                reason="bounded search",
            )

        def judge(
            self,
            analysis,
            observations,
            documents,
            memory,
            iteration_count,
        ):
            return JudgeDecision(
                sufficient=False,
                reason="still missing",
                missing_information=["never complete"],
                recommended_action=self.next_action(
                    analysis, observations, memory
                ),
            )

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        AsyncPolicyAdapter(
            analyzer(SourceRequest(source="mail", query="missing")),
            ExhaustingPolicy(),
        ),
    )

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert len(search.calls) == MAX_ITERATIONS == 4
    assert result.agent_trace.iteration_count == MAX_ITERATIONS
    assert result.quality.limited_answer is True
    assert LIMIT_DISCLOSURE in result.answer


def test_unique_event_expansions_cannot_bypass_the_total_action_bound():
    class ExpandingPolicy(TypedAgentPolicy):
        @staticmethod
        def next_action(analysis, observations, memory):
            if not observations:
                return ToolAction(
                    tool="search_calendar",
                    query="missing-calendar",
                    reason="bounded initial search",
                )
            return ToolAction(
                tool="expand_calendar_event",
                event_id=f"missing-event-{len(observations)}",
                reason="bounded expansion",
            )

        def judge(
            self,
            analysis,
            observations,
            documents,
            memory,
            iteration_count,
        ):
            return JudgeDecision(
                sufficient=False,
                reason="still missing",
                missing_information=["never complete"],
                recommended_action=self.next_action(
                    analysis, observations, memory
                ),
            )

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        AsyncPolicyAdapter(
            analyzer(
                SourceRequest(source="calendar", query="missing-calendar")
            ),
            ExpandingPolicy(),
        ),
    )

    result = invoke(workflow, "그 회의 Action 알려줘")

    assert len(search.calls) == MAX_ITERATIONS == 4
    assert result.quality.limited_answer is True
    assert result.agent_trace.iteration_count == 1
    assert "search_calendar" in result.agent_trace.tool_calls


def test_model_time_scope_is_resolved_by_the_server():
    llm = FixedIntentLLM(
        IntentDecision(
            intent="mail",
            source_requests=[SourceRequest(source="mail", query="NAND")],
            time_scope="previous_week",
        )
    )
    model = StructuredAgentModel(llm, now=NOW)

    analysis = asyncio.run(
        model.analyze("임의 문구", ConversationMemory(), "Asia/Seoul")
    )

    assert analysis.time_scope == "previous_week"
    assert analysis.start_at_utc == datetime(2026, 8, 2, 15, tzinfo=UTC)
    assert analysis.end_at_utc == datetime(2026, 8, 9, 15, tzinfo=UTC)


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
    assert "agent_trace" in ChatResponse.model_fields


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

    workflow = MultiSourceAgenticWorkflow(
        BM25Search(),
        analyzer(SourceRequest(source="mail", query="NAND")),
    )

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
        FourDisclosureSearch(),
        analyzer(SourceRequest(source="mail", query="NAND")),
    )

    result = invoke(workflow, "NAND 관련 메일 찾아줘")

    assert result.quality.limited_answer is True
    assert len(result.disclosures) == 4
    assert LIMIT_DISCLOSURE in result.disclosures


def test_hostile_policy_output_is_sanitized_from_answer_trace_and_memory():
    class HostileJudgePolicy(TypedAgentPolicy):
        def judge(
            self,
            analysis,
            observations,
            documents,
            memory,
            iteration_count,
        ):
            return JudgeDecision(
                sufficient=False,
                reason="<analysis>trace secret</analysis>",
                missing_information=[
                    "<analysis>answer secret</analysis>, /srv/private/password.txt"
                ],
                recommended_action=None,
            )

        @staticmethod
        def answer(documents, missing):
            return "확인된 근거입니다. [S1]"

    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        AsyncPolicyAdapter(
            analyzer(SourceRequest(source="mail", query="NAND")),
            HostileJudgePolicy(),
        ),
    )

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

    hostile_analyzer = analyzer(
        SourceRequest(source="mail", query="NAND"),
        entities={
            "product": "TOP_SECRET",
            "issue": "MODEL_INVENTED",
        },
    )

    trusted = ConversationMemory(
        entities={"product": "TRUSTED_NAND"},
        current_topic="Trusted topic",
        unresolved_information=["Trusted gap"],
    )
    policy = PolicyContext.from_user_id("kim")
    workflow = MultiSourceAgenticWorkflow(EmptySearch(), hostile_analyzer)

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
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(SourceRequest(source="mail", query="NAND")),
    )

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
            raise AppError(
                ErrorCode.RETRIEVAL_TIMEOUT,
                "calendar backend secret failure",
                retryable=True,
            )

    search = PartiallyFailingSearch()
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(
            SourceRequest(source="mail", query="NAND"),
            SourceRequest(source="calendar", query="NAND"),
        ),
    )

    result = invoke(workflow, "NAND 메일과 회의 찾아줘")

    assert [item.source_type for item in result.evidence] == ["mail"]
    assert result.quality.limited_answer is True
    assert result.execution.status == "limited"
    assert result.execution.error_code == "RETRIEVAL_TIMEOUT"
    assert result.execution.retryable is True
    assert "calendar backend secret failure" not in result.model_dump_json()


@pytest.mark.parametrize("mail_outcome", ["zero_hits", "source_failure"])
def test_required_source_sweep_continues_after_empty_or_failed_mail(
    mail_outcome,
):
    class SweepSearch:
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
                if mail_outcome == "source_failure":
                    raise AppError(
                        ErrorCode.INDEX_UNAVAILABLE,
                        "mail backend unavailable secret",
                        retryable=True,
                    )
                return SearchResult(
                    tool=action.tool,
                    query=action.query,
                    retrieval_mode="hybrid",
                )
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

    search = SweepSearch()
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(
            SourceRequest(source="mail", query="NAND"),
            SourceRequest(source="calendar", query="NAND"),
        ),
    )

    result = invoke(workflow, "NAND 메일과 회의 찾아줘")

    assert search.calls == ["search_mail", "search_calendar"]
    assert [item.source_type for item in result.evidence] == ["calendar"]
    assert result.quality.limited_answer is True
    assert "mail backend unavailable secret" not in result.model_dump_json()


def test_policy_and_programming_errors_are_not_downgraded_to_source_failure():
    class RaisingSearch:
        def __init__(self, error):
            self.error = error

        async def execute(
            self,
            action,
            policy,
            analysis,
            request_filters=None,
        ):
            raise self.error

    policy_error = AppError(
        ErrorCode.UNAUTHORIZED_RESOURCE,
        "policy denied",
    )
    policy_workflow = MultiSourceAgenticWorkflow(
        RaisingSearch(policy_error),
        analyzer(SourceRequest(source="mail", query="NAND")),
    )
    programming_workflow = MultiSourceAgenticWorkflow(
        RaisingSearch(AssertionError("programming contract failed")),
        analyzer(SourceRequest(source="mail", query="NAND")),
    )

    with pytest.raises(AppError) as policy_failure:
        invoke(policy_workflow, "NAND 메일 찾아줘")
    assert policy_failure.value.code == ErrorCode.UNAUTHORIZED_RESOURCE
    with pytest.raises(AssertionError, match="programming contract failed"):
        invoke(programming_workflow, "NAND 메일 찾아줘")


def test_additional_typed_source_failure_keeps_quality_and_execution_limited():
    class OptionalFailureSearch:
        async def execute(
            self,
            action,
            policy,
            analysis,
            request_filters=None,
        ):
            if action.tool == "search_domain_knowledge":
                raise AppError(
                    ErrorCode.INDEX_UNAVAILABLE,
                    "optional source unavailable",
                    retryable=True,
                )
            return SearchResult(
                tool=action.tool,
                query=action.query,
                documents=[
                    SearchDocument(
                        source_type="mail",
                        document_id="mail-grounded",
                        content_kind="body",
                        title="NAND yield mail",
                        text="NAND 수율 이슈가 보고됐다.",
                        score=1.0,
                    )
                ],
                total_hits=1,
                retrieval_mode="hybrid",
            )

    workflow = MultiSourceAgenticWorkflow(
        OptionalFailureSearch(),
        analyzer(
            SourceRequest(source="mail", query="NAND"),
            SourceRequest(source="domain_knowledge", query="NAND"),
        ),
    )

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert [item.source_type for item in result.evidence] == ["mail"]
    assert result.quality.limited_answer is True
    assert result.execution.status == "limited"
    assert result.execution.error_code == "INDEX_UNAVAILABLE"
    assert LIMIT_DISCLOSURE in result.answer


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
                raise AppError(
                    ErrorCode.RETRIEVAL_TIMEOUT,
                    "calendar attachment timeout secret",
                    retryable=True,
                )
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
        analyzer(
            SourceRequest(source="calendar", query="NAND"),
            calendar_detail_required=True,
        ),
    )

    result = invoke(workflow, "NAND 회의에서 Action 뭐였어?")

    assert [item.source_type for item in result.evidence] == ["calendar"]
    assert result.execution.status == "limited"
    assert result.execution.error_code == "RETRIEVAL_TIMEOUT"
    assert result.execution.retryable is True
    assert "calendar attachment timeout secret" not in result.model_dump_json()


def test_typed_source_request_controls_initial_source():
    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(
            SourceRequest(source="mail", query="NAND"),
            information_needs=["기술 원인 일정"],
        ),
    )

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert search.calls[0][0].tool == "search_mail"
    assert {item.source_type for item in result.evidence} == {"mail"}


def test_typed_source_request_controls_initial_query():
    search = InMemoryMultiSourceSearch.from_path(
        Path("fixtures/multi_source_demo/corpus.json"),
        SourceRegistry.from_settings(Settings()),
    )
    workflow = MultiSourceAgenticWorkflow(
        search,
        analyzer(SourceRequest(source="mail", query="NAND")),
    )

    result = invoke(workflow, "NAND 메일 찾아줘")

    assert search.calls[0][0].query == "NAND"
    assert len(search.calls) == 1
    assert {item.source_type for item in result.evidence} == {"mail"}

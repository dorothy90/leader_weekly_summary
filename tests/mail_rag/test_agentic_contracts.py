from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.domain.agentic import (
    AgentMemoryUpdate,
    AgentTrace,
    JudgeDecision,
    Observation,
    QueryAnalysis,
    ResolvedTimeRange,
    SearchDocument,
    SearchResult,
    ToolAction,
)
from app.retrieval.source_registry import SourceRegistry


def test_source_registry_uses_configured_aliases_and_no_physical_names():
    settings = Settings()
    registry = SourceRegistry.from_settings(settings)
    assert registry.index_for("search_domain_knowledge") == "syld_gpt"
    assert registry.index_for("search_mail") == "ews-mail-active"
    assert registry.index_for("search_calendar") == "ews-calendar-active"
    assert "ews-mail-v1" not in repr(registry)
    assert "ews-calendar-v1" not in repr(registry)


@pytest.mark.parametrize("tool", ["shell", "search_index", "ews-mail-v1"])
def test_tool_action_rejects_unapproved_tools(tool):
    with pytest.raises(ValidationError):
        ToolAction(tool=tool, query="NAND", reason="test")


@pytest.mark.parametrize("field", ["employee_id", "user_id", "index", "dsl"])
def test_tool_action_has_no_owner_index_or_dsl_fields(field):
    with pytest.raises(ValidationError):
        ToolAction(
            tool="search_mail",
            query="NAND",
            reason="mail",
            **{field: "lee"},
        )


def test_search_result_is_normalized_and_bounded():
    result = SearchResult(
        tool="search_calendar",
        query="NAND review",
        documents=[
            SearchDocument(
                source_type="calendar",
                document_id="event-1",
                source_id="event-1",
                parent_event_id="event-1",
                content_kind="event",
                title="NAND Yield Review",
                text="FDC 확인",
                score=1.0,
                metadata={"start_at_utc": "2026-08-12T01:00:00Z"},
            )
        ],
        total_hits=1,
    )
    assert result.documents[0].text == "FDC 확인"
    with pytest.raises(ValidationError):
        SearchDocument(
            source_type="mail",
            document_id="mail-1",
            text="x" * 8001,
            score=1,
        )


def test_query_analysis_accepts_backend_resolved_utc_range():
    analysis = QueryAnalysis(
        intent="knowledge_query",
        question_type="multi_source",
        entities={"product": "NAND"},
        time_expression="지난주",
        start_at_utc=datetime(2026, 8, 2, 15, tzinfo=UTC),
        end_at_utc=datetime(2026, 8, 9, 15, tzinfo=UTC),
        information_needs=["관련 메일", "관련 회의"],
    )
    assert analysis.start_at_utc < analysis.end_at_utc


@pytest.mark.parametrize("contract", [ResolvedTimeRange, QueryAnalysis])
def test_utc_range_contracts_reject_naive_datetimes(contract):
    fields = {
        "start_at_utc": datetime(2026, 8, 2, 15),
        "end_at_utc": datetime(2026, 8, 9, 15),
    }
    if contract is ResolvedTimeRange:
        fields["expression"] = "지난주"
    else:
        fields.update(intent="knowledge_query", question_type="multi_source")

    with pytest.raises(ValidationError):
        contract(**fields)


def test_query_analysis_rejects_non_utc_range():
    seoul = timezone(timedelta(hours=9))

    with pytest.raises(ValidationError):
        QueryAnalysis(
            intent="knowledge_query",
            question_type="multi_source",
            start_at_utc=datetime(2026, 8, 3, tzinfo=seoul),
            end_at_utc=datetime(2026, 8, 10, tzinfo=seoul),
        )


def _contract_action():
    return ToolAction(tool="search_mail", query="NAND", reason="mail evidence")


def _contract_document(index: int = 0):
    return SearchDocument(
        source_type="mail",
        document_id=f"mail-{index}",
        text="NAND evidence",
        score=1.0,
    )


def _contract_result(document_count: int = 1):
    return SearchResult(
        tool="search_mail",
        query="NAND",
        documents=[_contract_document(index) for index in range(document_count)],
        total_hits=document_count,
    )


def test_agent_output_contracts_accept_representative_values_at_bounds():
    observation = Observation(
        action=_contract_action(),
        result=_contract_result(20),
        extracted_entities={"product": "NAND"},
    )
    decision = JudgeDecision(
        sufficient=False,
        reason="additional evidence is required",
        missing_information=[f"need-{index}" for index in range(8)],
        recommended_action=_contract_action(),
    )
    memory = AgentMemoryUpdate(
        entities={"product": "NAND"},
        current_topic="NAND",
        search_history=[f"search-{index}" for index in range(16)],
        retrieved_source_refs=[f"mail-{index}" for index in range(16)],
        unresolved_information=[f"need-{index}" for index in range(8)],
    )
    trace = AgentTrace(
        tool_calls=[f"tool-{index}" for index in range(8)],
        judge_decisions=[f"decision-{index}" for index in range(8)],
        iteration_count=4,
    )

    assert len(observation.result.documents) == 20
    assert len(decision.missing_information) == 8
    assert len(memory.search_history) == 16
    assert trace.iteration_count == 4


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Observation(
            action=_contract_action(),
            result=_contract_result(),
            unexpected="private",
        ),
        lambda: JudgeDecision(
            sufficient=True,
            reason="complete",
            unexpected="private",
        ),
        lambda: AgentMemoryUpdate(unexpected="private"),
        lambda: AgentTrace(unexpected="private"),
    ],
    ids=["observation", "judge-decision", "agent-memory", "agent-trace"],
)
def test_agent_output_contracts_reject_extra_fields(factory):
    with pytest.raises(ValidationError):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Observation(
            action=_contract_action(),
            result=_contract_result(21),
        ),
        lambda: JudgeDecision(
            sufficient=False,
            reason="missing",
            missing_information=[f"need-{index}" for index in range(9)],
        ),
        lambda: AgentMemoryUpdate(
            search_history=[f"search-{index}" for index in range(17)]
        ),
        lambda: AgentTrace(iteration_count=5),
    ],
    ids=["observation", "judge-decision", "agent-memory", "agent-trace"],
)
def test_agent_output_contracts_reject_values_beyond_bounds(factory):
    with pytest.raises(ValidationError):
        factory()

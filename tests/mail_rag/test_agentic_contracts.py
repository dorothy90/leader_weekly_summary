from datetime import UTC, date, datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.domain.agentic import (
    AgentMemoryUpdate,
    AgentTrace,
    EventReference,
    IntentDecision,
    JudgeDecision,
    Observation,
    QueryAnalysis,
    ResolvedTimeRange,
    SearchDocument,
    SearchResult,
    SourceRequest,
    ToolAction,
)
from app.retrieval.source_registry import SourceRegistry


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def test_intent_decision_accepts_unique_bounded_logical_sources():
    decision = IntentDecision(
        intent="weekly_schedule",
        source_requests=[SourceRequest(source="calendar", query="팀 일정")],
        time_scope="current_week",
        calendar_detail_required=False,
    )

    analysis = QueryAnalysis.from_intent(
        decision, now=NOW, timezone_name="Asia/Seoul"
    )

    assert analysis.analysis_status == "ready"
    assert analysis.question_type == "calendar_search"
    assert [item.source for item in analysis.source_requests] == ["calendar"]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "intent": "duplicate",
            "source_requests": [
                {"source": "calendar", "query": "일정"},
                {"source": "calendar", "query": "회의"},
            ],
        },
        {
            "intent": "bad_exact_date",
            "source_requests": [{"source": "calendar", "query": "일정"}],
            "time_scope": "exact_date",
        },
        {
            "intent": "bad_detail",
            "source_requests": [{"source": "mail", "query": "NAND"}],
            "calendar_detail_required": True,
        },
        {
            "intent": "owner_injection",
            "source_requests": [{"source": "mail", "query": "NAND"}],
            "entities": {"employee_id": "lee"},
        },
        {
            "intent": "index_injection",
            "source_requests": [{"source": "mail", "query": "NAND"}],
            "entities": {"index_name": "ews-mail-v1"},
        },
        {
            "intent": "oversized_query",
            "source_requests": [{"source": "mail", "query": "x" * 1001}],
        },
        {
            "intent": "oversized_needs",
            "information_needs": [f"need-{index}" for index in range(9)],
        },
        {
            "intent": "coerced_boolean",
            "source_requests": [{"source": "calendar", "query": "일정"}],
            "calendar_detail_required": "false",
        },
    ],
)
def test_intent_decision_rejects_inconsistent_or_server_owned_fields(payload):
    with pytest.raises(ValidationError):
        IntentDecision.model_validate(payload)


def test_intent_decision_forbids_tool_and_owner_fields_at_every_level():
    with pytest.raises(ValidationError):
        IntentDecision.model_validate(
            {
                "intent": "hostile",
                "source_requests": [
                    {
                        "source": "mail",
                        "query": "NAND",
                        "tool": "search_mail",
                        "owner": "lee",
                    }
                ],
                "employee_id": "lee",
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"intent": "hostile"},
        {"question_type": "mail_search"},
        {"entities": {"product": "NAND"}},
        {"source_requests": [{"source": "mail", "query": "NAND"}]},
        {"time_scope": "current_week"},
        {"exact_date": date(2026, 8, 17)},
        {"event_reference": "previous_event"},
        {"calendar_detail_required": True},
        {
            "start_at_utc": datetime(2026, 8, 16, 15, tzinfo=UTC),
            "end_at_utc": datetime(2026, 8, 23, 15, tzinfo=UTC),
        },
        {"information_needs": ["mail evidence"]},
    ],
    ids=[
        "intent",
        "question-type",
        "entities",
        "source-requests",
        "time-scope",
        "exact-date",
        "event-reference",
        "calendar-detail",
        "utc-range",
        "information-needs",
    ],
)
def test_unavailable_query_analysis_rejects_noncanonical_state(overrides):
    payload = {
        "analysis_status": "unavailable",
        "intent": "analysis_unavailable",
        "question_type": "general_chat",
        **overrides,
    }

    with pytest.raises(ValidationError):
        QueryAnalysis.model_validate(payload)


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
    analysis = QueryAnalysis.from_intent(
        IntentDecision(
            intent="knowledge_query",
            source_requests=[
                SourceRequest(source="mail", query="NAND"),
                SourceRequest(source="calendar", query="NAND"),
            ],
            entities={"product": "NAND"},
            time_scope="previous_week",
            information_needs=["관련 메일", "관련 회의"],
        ),
        now=NOW,
        timezone_name="Asia/Seoul",
    )
    assert analysis.start_at_utc < analysis.end_at_utc


@pytest.mark.parametrize("contract", [ResolvedTimeRange, QueryAnalysis])
def test_utc_range_contracts_reject_naive_datetimes(contract):
    fields = {
        "start_at_utc": datetime(2026, 8, 2, 15),
        "end_at_utc": datetime(2026, 8, 9, 15),
    }
    if contract is ResolvedTimeRange:
        fields["scope"] = "previous_week"
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


def _contract_document(index: int = 0, metadata=None):
    return SearchDocument(
        source_type="mail",
        document_id=f"mail-{index}",
        text="NAND evidence",
        score=1.0,
        metadata=metadata or {},
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


@pytest.mark.parametrize(
    "metadata",
    [
        {f"key-{index}": "value" for index in range(12)},
        {"k" * 64: "value"},
        {"value": "v" * 512},
        {"items": ["item" for _index in range(20)]},
        {"items": ["i" * 320]},
        {"number": 2**63 - 1},
        {"number": -(2**63 - 1)},
        {"number": 1.5},
    ],
    ids=[
        "key-count",
        "key-length",
        "scalar-string",
        "list-length",
        "list-item-length",
        "positive-number",
        "negative-number",
        "finite-float",
    ],
)
def test_search_document_metadata_accepts_each_exact_bound(metadata):
    assert _contract_document(metadata=metadata).metadata == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {f"key-{index}": "value" for index in range(13)},
        {"k" * 65: "value"},
        {"value": "v" * 513},
        {"items": ["item" for _index in range(21)]},
        {"items": ["i" * 321]},
        {"nested": {"unsafe": True}},
        {"nested": [["unsafe"]]},
        {"number": float("nan")},
        {"number": float("inf")},
        {"number": -(2**63)},
        {"object": object()},
    ],
    ids=[
        "key-count",
        "key-length",
        "scalar-string",
        "list-length",
        "list-item-length",
        "nested-dict",
        "nested-list",
        "nan",
        "infinity",
        "number-magnitude",
        "arbitrary-object",
    ],
)
def test_search_document_metadata_rejects_values_beyond_shared_bounds(metadata):
    with pytest.raises(ValidationError):
        _contract_document(metadata=metadata)


def _metadata_with_aggregate_size(last_value_length: int):
    metadata = {f"k{index}": "v" * 512 for index in range(7)}
    metadata["k7"] = "v" * last_value_length
    return metadata


def test_search_document_metadata_accepts_exact_serialized_utf8_bound():
    metadata = _metadata_with_aggregate_size(447)

    assert len(
        json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) == 4096
    assert _contract_document(metadata=metadata).metadata == metadata


def test_search_document_metadata_rejects_beyond_serialized_utf8_bound():
    metadata = _metadata_with_aggregate_size(448)

    assert len(
        json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) == 4097
    with pytest.raises(ValidationError):
        _contract_document(metadata=metadata)


def test_observation_serializes_only_bounded_document_metadata_for_context():
    metadata = {
        "subject": "NAND Yield Review",
        "attendee_emails": ["kim.oo@example.com"],
        "chunk_index": 0,
        "active": True,
        "optional": None,
    }
    observation = Observation(
        action=_contract_action(),
        result=SearchResult(
            tool="search_mail",
            query="NAND",
            documents=[_contract_document(metadata=metadata)],
            total_hits=1,
        ),
    )

    context = observation.model_dump(mode="json")

    assert context["result"]["documents"][0]["metadata"] == metadata
    assert json.loads(observation.model_dump_json())["result"]["documents"][0][
        "metadata"
    ] == metadata


def _bounded_entities():
    return {
        f"entity-{index:02d}-" + ("k" * 90): "v" * 500
        for index in range(16)
    }


def test_model_facing_entity_and_list_items_accept_exact_bounds():
    entities = _bounded_entities()
    needs = [f"need-{index}-" + ("n" * 493) for index in range(8)]
    analysis = QueryAnalysis(
        intent="knowledge_query",
        question_type="multi_source",
        entities=entities,
        information_needs=needs,
    )
    observation = Observation(
        action=_contract_action(),
        result=_contract_result(),
        extracted_entities=entities,
    )
    decision = JudgeDecision(
        sufficient=False,
        reason="bounded",
        missing_information=needs,
    )
    memory = AgentMemoryUpdate(
        entities=entities,
        search_history=["s" * 500 for _index in range(16)],
        retrieved_source_refs=["r" * 256 for _index in range(16)],
        unresolved_information=needs,
    )
    trace = AgentTrace(
        tool_calls=["search_mail" for _index in range(8)],
        judge_decisions=["j" * 500 for _index in range(8)],
        iteration_count=4,
    )
    result = SearchResult(
        tool="search_mail",
        query="NAND",
        disclosures=["d" * 500 for _index in range(4)],
    )

    assert len(analysis.entities) == len(observation.extracted_entities) == 16
    assert len(decision.missing_information[0]) == 500
    assert len(memory.unresolved_information[0]) == 500
    assert len(trace.judge_decisions[0]) == 500
    assert len(result.disclosures[0]) == 500


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entities", {f"key-{index}": "value" for index in range(17)}),
        ("entities", {"k" * 101: "value"}),
        ("entities", {"key": "v" * 501}),
        ("entities", {"": "value"}),
        ("entities", {"key": 42}),
        ("information_needs", [f"need-{index}" for index in range(9)]),
        ("information_needs", ["n" * 501]),
        ("information_needs", [""]),
        ("information_needs", [42]),
    ],
    ids=[
        "entity-count",
        "entity-key-length",
        "entity-value-length",
        "empty-entity-key",
        "entity-value-type",
        "need-count",
        "need-length",
        "empty-need",
        "need-type",
    ],
)
def test_query_analysis_rejects_unbounded_nested_model_values(field, value):
    with pytest.raises(ValidationError):
        QueryAnalysis(
            intent="knowledge_query",
            question_type="multi_source",
            **{field: value},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entities", {f"key-{index}": "value" for index in range(17)}),
        ("entities", {"k" * 101: "value"}),
        ("entities", {"key": "v" * 501}),
        ("unresolved_information", [f"need-{index}" for index in range(9)]),
        ("unresolved_information", ["n" * 501]),
        ("unresolved_information", [""]),
        ("unresolved_information", [42]),
        ("search_history", ["s" * 501]),
        ("retrieved_source_refs", ["r" * 257]),
    ],
    ids=[
        "entity-count",
        "entity-key-length",
        "entity-value-length",
        "unresolved-count",
        "unresolved-length",
        "empty-unresolved",
        "unresolved-type",
        "search-history-item",
        "source-reference-item",
    ],
)
def test_agent_memory_update_rejects_unbounded_nested_values(field, value):
    with pytest.raises(ValidationError):
        AgentMemoryUpdate(**{field: value})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Observation(
            action=_contract_action(),
            result=_contract_result(),
            extracted_entities={"key": "v" * 501},
        ),
        lambda: JudgeDecision(
            sufficient=False,
            reason="missing",
            missing_information=["n" * 501],
        ),
        lambda: SearchResult(
            tool="search_mail",
            query="NAND",
            disclosures=["d" * 501],
        ),
        lambda: AgentTrace(judge_decisions=["j" * 501]),
        lambda: ToolAction(
            tool="search_calendar",
            query="NAND",
            reason="attendees",
            attendee_emails=["a" * 321],
        ),
    ],
    ids=[
        "observation-entity",
        "judge-missing-item",
        "result-disclosure-item",
        "trace-decision-item",
        "attendee-email-item",
    ],
)
def test_analogous_model_facing_items_reject_oversized_strings(factory):
    with pytest.raises(ValidationError):
        factory()


VALID_EDGE_EVENT_ID = ("A" * 250) + "_.:@+-"


def test_event_reference_and_expansion_accept_shared_valid_edge_id():
    reference = EventReference(event_id=VALID_EDGE_EVENT_ID)
    action = ToolAction(
        tool="expand_calendar_event",
        event_id=VALID_EDGE_EVENT_ID,
        reason="edge",
    )

    assert len(reference.event_id) == len(action.event_id) == 256


@pytest.mark.parametrize(
    "event_id",
    [
        "",
        " event-42",
        "event-42 ",
        "event 42",
        "event/42",
        "event=42",
        "event#42",
        "x" * 257,
        42,
    ],
    ids=[
        "empty",
        "leading-space",
        "trailing-space",
        "internal-space",
        "slash",
        "equals",
        "symbol",
        "overlong",
        "non-string",
    ],
)
def test_event_reference_and_expansion_reject_same_unsafe_ids(event_id):
    with pytest.raises(ValidationError):
        EventReference(event_id=event_id)
    with pytest.raises(ValidationError):
        ToolAction(
            tool="expand_calendar_event",
            event_id=event_id,
            reason="unsafe",
        )

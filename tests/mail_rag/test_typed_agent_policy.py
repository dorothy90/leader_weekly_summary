from datetime import UTC, datetime

from app.domain.agentic import (
    EventReference,
    IntentDecision,
    Observation,
    QueryAnalysis,
    SearchDocument,
    SearchResult,
    SourceRequest,
    ToolAction,
)
from app.domain.agentic_policy import TypedAgentPolicy
from app.persistence.conversations import ConversationMemory


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def typed_analysis(*requests, detail=False, event_reference="none"):
    return QueryAnalysis.from_intent(
        IntentDecision(
            intent="test",
            source_requests=list(requests),
            event_reference=event_reference,
            calendar_detail_required=detail,
            information_needs=["메일 일정 기술 의미 Action 뭐야"],
        ),
        now=NOW,
        timezone_name="Asia/Seoul",
    )


def observation(tool, query, documents=()):
    action = ToolAction(tool=tool, query=query, reason="already attempted")
    return Observation(
        action=action,
        result=SearchResult(
            tool=tool,
            query=query,
            documents=list(documents),
            total_hits=len(documents),
        ),
    )


def document(source, document_id, text, **overrides):
    return SearchDocument(
        document_id=document_id,
        source_type=source,
        text=text,
        score=1,
        **overrides,
    )


def test_required_sources_use_only_source_requests():
    analysis = typed_analysis(SourceRequest(source="calendar", query="팀 일정"))

    assert TypedAgentPolicy().required_sources(analysis) == ["calendar"]


def test_required_sources_preserve_declared_source_order():
    analysis = typed_analysis(
        SourceRequest(source="domain_knowledge", query="Cell Leakage 의미"),
        SourceRequest(source="mail", query="NAND 수율"),
        SourceRequest(source="calendar", query="NAND Yield Review"),
    )

    assert TypedAgentPolicy().required_sources(analysis) == [
        "domain_knowledge",
        "mail",
        "calendar",
    ]


def test_next_action_maps_logical_source_to_allowlisted_tool_and_query():
    analysis = typed_analysis(
        SourceRequest(source="mail", query="NAND 수율"),
        SourceRequest(source="calendar", query="NAND Yield Review"),
    )

    action = TypedAgentPolicy().next_action(
        analysis, [], ConversationMemory()
    )

    assert action.tool == "search_mail"
    assert action.query == "NAND 수율"


def test_next_action_skips_attempted_source_and_keeps_request_query():
    analysis = typed_analysis(
        SourceRequest(source="mail", query="NAND 수율"),
        SourceRequest(source="calendar", query="NAND Yield Review"),
    )
    observations = [observation("search_mail", "different display text")]

    action = TypedAgentPolicy().next_action(
        analysis, observations, ConversationMemory()
    )

    assert action.tool == "search_calendar"
    assert action.query == "NAND Yield Review"


def test_previous_event_expands_only_valid_saved_reference():
    analysis = typed_analysis(
        SourceRequest(source="calendar", query="이전 회의"),
        event_reference="previous_event",
    )
    memory = ConversationMemory(
        previous_event_reference=EventReference(
            event_id="event-kim-1", subject="NAND Yield Review"
        )
    )

    action = TypedAgentPolicy().next_action(analysis, [], memory)

    assert action.tool == "expand_calendar_event"
    assert action.event_id == "event-kim-1"


def test_previous_event_without_saved_reference_does_not_guess():
    analysis = typed_analysis(
        SourceRequest(source="calendar", query="이전 회의"),
        event_reference="previous_event",
    )

    assert TypedAgentPolicy().next_action(
        analysis, [], ConversationMemory()
    ) is None


def test_missing_information_uses_requested_source_and_detail_flag_only():
    analysis = typed_analysis(
        SourceRequest(source="calendar", query="팀 일정"), detail=True
    )
    event = document(
        "calendar",
        "event-1",
        "평범한 본문",
        title="평범한 제목",
        content_kind="event",
        source_id="event-1",
    )

    assert TypedAgentPolicy().missing_information(analysis, [event]) == [
        "관련 회의 상세 내용"
    ]


def test_calendar_attachment_relation_is_detail_evidence_without_text_inference():
    analysis = typed_analysis(
        SourceRequest(source="calendar", query="팀 일정"), detail=True
    )
    attachment = document(
        "calendar",
        "attachment-1",
        "평범한 본문",
        title="평범한 제목",
        content_kind="attachment",
        source_id="attachment-1",
        parent_event_id="event-1",
    )

    assert TypedAgentPolicy().missing_information(analysis, [attachment]) == []


def test_non_normalized_calendar_attachment_is_not_detail_evidence():
    analysis = typed_analysis(
        SourceRequest(source="calendar", query="팀 일정"), detail=True
    )
    attachment = document(
        "calendar",
        "attachment-1",
        "Action이라는 표시 텍스트",
        content_kind="attachment",
        parent_event_id="",
    )

    assert TypedAgentPolicy().missing_information(analysis, [attachment]) == [
        "관련 회의 상세 내용"
    ]


def test_missing_information_labels_are_ordered_and_unique():
    analysis = typed_analysis(
        SourceRequest(source="mail", query="NAND 수율"),
        SourceRequest(source="calendar", query="팀 일정"),
        SourceRequest(source="domain_knowledge", query="Cell Leakage 의미"),
        detail=True,
    )

    missing = TypedAgentPolicy().missing_information(analysis, [])

    assert missing == [
        "관련 메일",
        "관련 회의",
        "기술적 의미",
        "관련 회의 상세 내용",
    ]
    assert len(missing) == len(set(missing))


def test_unavailable_analysis_produces_no_requirements_or_action():
    analysis = QueryAnalysis.unavailable()
    policy = TypedAgentPolicy()

    assert policy.required_sources(analysis) == []
    assert policy.missing_information(analysis, []) == []
    assert policy.next_action(analysis, [], ConversationMemory()) is None


def test_judge_recommends_only_the_next_typed_action():
    mail = document("mail", "mail-1", "NAND 수율 근거")
    analysis = typed_analysis(
        SourceRequest(source="mail", query="NAND 수율"),
        SourceRequest(source="calendar", query="NAND Yield Review"),
    )
    observations = [observation("search_mail", "NAND 수율", [mail])]

    decision = TypedAgentPolicy().judge(
        analysis,
        observations,
        [mail],
        ConversationMemory(),
        iteration_count=1,
    )

    assert decision.sufficient is False
    assert decision.missing_information == ["관련 회의"]
    assert decision.recommended_action.tool == "search_calendar"
    assert decision.recommended_action.query == "NAND Yield Review"


def test_answer_with_no_documents_is_explicitly_limited():
    assert TypedAgentPolicy().answer([], ["관련 메일"]) == (
        "확인 가능한 검색 근거가 없어 답변할 수 없습니다."
    )


def test_answer_is_sanitized_extractive_and_citation_numbered():
    documents = [
        document("mail", "mail-1", "NAND 수율은 개선되었다. secret=hidden"),
        document("calendar", "event-1", "회의 결론은 재검토이다."),
    ]

    answer = TypedAgentPolicy().answer(documents, ["기술적 의미 token=hidden"])

    assert "NAND 수율은 개선되었다. [REDACTED] [S1]" in answer
    assert "회의 결론은 재검토이다. [S2]" in answer
    assert "확인하지 못한 항목: 기술적 의미 [REDACTED]" in answer
    assert "hidden" not in answer

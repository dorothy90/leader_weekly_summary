import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.domain.agentic import (
    IntentDecision,
    QueryAnalysis,
    SearchDocument,
    SourceRequest,
    ToolAction,
)
from app.domain.policy import PolicyContext
from app.retrieval.multi_source import InMemoryMultiSourceSearch, StoredDocument
from app.retrieval.source_registry import SourceRegistry


FIXTURE = Path("fixtures/multi_source_demo/corpus.json")

AUGUST_2026_BUSINESS_DAYS = {
    date(2026, 8, day)
    for day in (
        3, 4, 5, 6, 7,
        10, 11, 12, 13, 14,
        17, 18, 19, 20, 21,
        24, 25, 26, 27, 28,
        31,
    )
}
LEGACY_DOCUMENT_IDS = {
    "domain-cell-leakage",
    "mail-kim-body-0",
    "mail-kim-attachment-0",
    "event-kim-1",
    "event-kim-1-action",
    "mail-lee-decoy",
    "mail-kim-inactive",
    "event-kim-cancelled",
}


def _daily_documents():
    return [
        item
        for item in service().documents
        if item.document_id.startswith(
            ("mail-kim-202608", "event-kim-202608")
        )
    ]


def _document_day(document_id: str) -> date:
    return datetime.strptime(document_id[-8:], "%Y%m%d").date()


def test_august_corpus_has_one_mail_and_calendar_record_per_business_day():
    documents = service().documents
    daily = _daily_documents()
    mail = [item for item in daily if item.source_type == "mail"]
    calendar = [item for item in daily if item.source_type == "calendar"]

    assert len(documents) == 50
    assert len({item.document_id for item in documents}) == 50
    assert len(mail) == 21
    assert len(calendar) == 21
    assert {_document_day(item.document_id) for item in mail} == (
        AUGUST_2026_BUSINESS_DAYS
    )
    assert {_document_day(item.document_id) for item in calendar} == (
        AUGUST_2026_BUSINESS_DAYS
    )


def test_august_daily_records_have_local_business_hours_and_metadata():
    seoul = ZoneInfo("Asia/Seoul")
    daily = _daily_documents()

    assert len(daily) == 42

    for item in daily:
        business_day = _document_day(item.document_id)
        occurred = datetime.fromisoformat(
            item.occurred_at.replace("Z", "+00:00")
        )
        local = occurred.astimezone(seoul)

        assert local.date() == business_day
        assert item.employee_id == "kim"
        assert item.is_active is True
        assert item.is_cancelled is False

        if item.source_type == "mail":
            iso_year, iso_week, _ = business_day.isocalendar()
            assert item.index == "ews-mail-active"
            assert item.content_kind == "body"
            assert local.hour == 9
            assert item.team in {"YIELD팀", "PROCESS팀", "EQUIPMENT팀"}
            assert item.week == f"{iso_year}-{iso_week:02d}"
            assert item.mail_type == "daily_report"
            assert item.metadata == {
                "chunk_index": 0,
                "sender_email": "kim.oo@example.com",
            }
        else:
            start = datetime.fromisoformat(
                item.metadata["start_at_utc"].replace("Z", "+00:00")
            )
            end = datetime.fromisoformat(
                item.metadata["end_at_utc"].replace("Z", "+00:00")
            )
            assert item.index == "ews-calendar-active"
            assert item.content_kind == "event"
            assert local.hour == 10
            assert end - start == timedelta(hours=1)
            assert item.source_id == item.document_id
            assert item.parent_event_id == item.document_id
            assert item.metadata["calendar_item_id"] == item.document_id
            assert item.metadata["timezone"] == "Asia/Seoul"
            assert "일정" in f"{item.title} {item.text}"


def test_august_extension_preserves_security_decoys():
    by_id = {item.document_id: item for item in service().documents}

    assert LEGACY_DOCUMENT_IDS <= by_id.keys()
    assert by_id["mail-lee-decoy"].employee_id == "lee"
    assert by_id["mail-kim-inactive"].is_active is False
    assert by_id["event-kim-cancelled"].is_cancelled is True


def service():
    return InMemoryMultiSourceSearch.from_path(
        FIXTURE, SourceRegistry.from_settings(Settings())
    )


def analysis(**updates):
    question_type = updates.pop("question_type", "multi_source")
    sources = {
        "domain_knowledge": ["domain_knowledge"],
        "mail_search": ["mail"],
        "calendar_search": ["calendar"],
        "multi_source": ["mail", "calendar", "domain_knowledge"],
    }.get(question_type, [])
    base = QueryAnalysis.from_intent(
        IntentDecision(
            intent="test",
            source_requests=[
                SourceRequest(source=source, query="test")
                for source in sources
            ],
        )
    )
    return QueryAnalysis.model_validate(
        {**base.model_dump(), **updates}
    )


def run(action, owner="kim", query_analysis=None):
    return asyncio.run(
        service().execute(
            action,
            PolicyContext.from_user_id(owner),
            query_analysis or analysis(),
        )
    )


def test_domain_search_is_shared_active_and_normalized():
    search = service()
    search.documents.extend(
        [
            StoredDocument(
                index="syld_gpt",
                document_id="domain-inactive",
                source_type="domain_knowledge",
                source_id="domain-inactive",
                is_active=False,
                title="Cell Leakage inactive",
                text="Cell Leakage inactive knowledge",
            ),
            StoredDocument(
                index="wrong-domain-index",
                document_id="domain-wrong-index",
                source_type="domain_knowledge",
                source_id="domain-wrong-index",
                title="Cell Leakage wrong index",
                text="Cell Leakage wrong index knowledge",
            ),
        ]
    )

    result = asyncio.run(
        search.execute(
            ToolAction(
                tool="search_domain_knowledge",
                query="Cell Leakage",
                reason="기술 의미",
            ),
            PolicyContext.from_user_id("lee"),
            analysis(question_type="domain_knowledge"),
        )
    )

    assert [item.document_id for item in result.documents] == [
        "domain-cell-leakage"
    ]
    assert result.retrieval_mode == "deterministic"
    assert result.total_hits == 1
    assert result.documents[0].source_type == "domain_knowledge"


def test_domain_search_applies_content_kind_filter():
    result = run(
        ToolAction(
            tool="search_domain_knowledge",
            query="Cell Leakage",
            reason="domain kind",
            content_kinds=["attachment"],
        )
    )

    assert result.documents == []
    assert result.total_hits == 0


def test_domain_search_applies_attachment_name_filter():
    result = run(
        ToolAction(
            tool="search_domain_knowledge",
            query="Cell Leakage",
            reason="domain attachment",
            attachment_name="secret.pdf",
        )
    )

    assert result.documents == []
    assert result.total_hits == 0


def test_mail_search_enforces_index_owner_active_and_content_kind():
    search = service()
    search.documents.append(
        StoredDocument(
            index="wrong-mail-index",
            document_id="mail-kim-wrong-index",
            source_type="mail",
            content_kind="body",
            source_id="mail-kim-wrong-index",
            employee_id="kim",
            title="NAND Cell Leakage wrong index",
            text="NAND Cell Leakage wrong index body",
            occurred_at="2026-08-05T01:00:00Z",
            metadata={"chunk_index": 0},
        )
    )
    search.documents.append(
        StoredDocument(
            index="ews-mail-active",
            document_id="mail-masquerading-as-shared-domain",
            source_type="domain_knowledge",
            content_kind="body",
            source_id="mail-masquerading-as-shared-domain",
            title="NAND Cell Leakage shared bypass",
            text="NAND Cell Leakage must not bypass mail ownership",
            occurred_at="2026-08-05T01:00:00Z",
        )
    )

    result = asyncio.run(
        search.execute(
            ToolAction(
                tool="search_mail",
                query="NAND Cell Leakage",
                reason="메일",
                content_kinds=["body"],
            ),
            PolicyContext.from_user_id("kim"),
            analysis(question_type="mail_search"),
        )
    )

    ids = {item.document_id for item in result.documents}
    assert "mail-kim-body-0" in ids
    assert "mail-lee-decoy" not in ids
    assert "mail-kim-inactive" not in ids
    assert "mail-kim-wrong-index" not in ids
    assert "mail-masquerading-as-shared-domain" not in ids
    assert all(item.content_kind == "body" for item in result.documents)


def test_calendar_search_filters_cancelled_and_expands_event_bundle():
    found = run(
        ToolAction(
            tool="search_calendar", query="NAND Yield Review", reason="회의"
        )
    )
    assert "event-kim-cancelled" not in {
        item.document_id for item in found.documents
    }

    expanded = run(
        ToolAction(
            tool="expand_calendar_event",
            event_id="event-kim-1",
            reason="회의 내용과 Action",
        )
    )
    assert [item.document_id for item in expanded.documents] == [
        "event-kim-1",
        "event-kim-1-action",
    ]


def test_calendar_search_post_filters_organizer_and_attendees():
    wrong_organizer = run(
        ToolAction(
            tool="search_calendar",
            query="NAND Yield Review",
            reason="organizer",
            organizer_email="other@example.com",
        )
    )
    wrong_attendee = run(
        ToolAction(
            tool="search_calendar",
            query="NAND Yield Review",
            reason="attendee",
            attendee_emails=["other@example.com"],
        )
    )

    assert wrong_organizer.documents == []
    assert wrong_attendee.documents == []


def test_event_expansion_applies_content_kind_after_parent_visibility():
    result = run(
        ToolAction(
            tool="expand_calendar_event",
            event_id="event-kim-1",
            reason="attachment only",
            content_kinds=["attachment"],
        )
    )

    assert [item.document_id for item in result.documents] == [
        "event-kim-1-action"
    ]


def test_event_expansion_applies_attachment_name_after_parent_visibility():
    result = run(
        ToolAction(
            tool="expand_calendar_event",
            event_id="event-kim-1",
            reason="named attachment",
            attachment_name="ACTION.PDF",
        )
    )

    assert [item.document_id for item in result.documents] == [
        "event-kim-1-action"
    ]


def test_cancelled_event_expansion_does_not_reveal_existence():
    result = run(
        ToolAction(
            tool="expand_calendar_event",
            event_id="event-kim-cancelled",
            reason="cancelled",
        )
    )
    assert result.documents == []
    assert result.total_hits == 0


def test_foreign_event_expansion_does_not_reveal_existence():
    result = run(
        ToolAction(
            tool="expand_calendar_event",
            event_id="event-kim-1",
            reason="foreign",
        ),
        owner="lee",
    )
    assert result.documents == []
    assert result.total_hits == 0


def test_event_expansion_requires_an_owner_visible_parent():
    search = service()
    search.documents.append(
        StoredDocument(
            index="ews-calendar-active",
            document_id="event-kim-1-lee-child",
            source_type="calendar",
            content_kind="attachment",
            source_id="event-kim-1-lee-child",
            parent_event_id="event-kim-1",
            employee_id="lee",
            title="foreign-parent child",
            text="lee can own this child but not the parent",
            occurred_at="2026-08-07T01:00:00Z",
        )
    )

    result = asyncio.run(
        search.execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-kim-1",
                reason="foreign parent",
                content_kinds=["attachment"],
            ),
            PolicyContext.from_user_id("lee"),
            analysis(question_type="calendar_search"),
        )
    )

    assert result.documents == []
    assert result.total_hits == 0


def test_resolved_date_range_is_half_open_for_dummy_mail_results():
    inside = run(
        ToolAction(tool="search_mail", query="NAND", reason="날짜 필터"),
        query_analysis=analysis(
            question_type="mail_search",
            start_at_utc=datetime(2026, 8, 5, 1, tzinfo=UTC),
            end_at_utc=datetime(2026, 8, 5, 2, tzinfo=UTC),
        ),
    )
    outside = run(
        ToolAction(tool="search_mail", query="NAND", reason="날짜 필터"),
        query_analysis=analysis(
            question_type="mail_search",
            start_at_utc=datetime(2026, 8, 5, 0, tzinfo=UTC),
            end_at_utc=datetime(2026, 8, 5, 1, tzinfo=UTC),
        ),
    )

    assert [item.document_id for item in inside.documents] == ["mail-kim-body-0"]
    assert outside.documents == []


def test_mail_attachment_name_filter_is_case_insensitive():
    result = run(
        ToolAction(
            tool="search_mail",
            query="Cell Leakage",
            reason="첨부",
            content_kinds=["attachment"],
            attachment_name="MEASUREMENTS.XLSX",
        )
    )

    assert [item.document_id for item in result.documents] == [
        "mail-kim-attachment-0"
    ]


def test_mail_chunks_are_sorted_deduplicated_and_reconstructed():
    documents = [
        SearchDocument(
            source_type="mail",
            document_id="chunk-2",
            source_id="mail-1",
            content_kind="body",
            text="두 번째",
            score=0.8,
            metadata={"chunk_index": 2},
        ),
        SearchDocument(
            source_type="mail",
            document_id="chunk-1",
            source_id="mail-1",
            content_kind="body",
            text="첫 번째",
            score=1.0,
            metadata={"chunk_index": 1},
        ),
        SearchDocument(
            source_type="mail",
            document_id="chunk-1-copy",
            source_id="mail-1",
            content_kind="body",
            text="첫 번째",
            score=0.5,
            metadata={"chunk_index": 1},
        ),
    ]

    result = InMemoryMultiSourceSearch._reconstruct_mail(documents, 10)

    assert len(result) == 1
    assert result[0].document_id == "chunk-1"
    assert result[0].text == "첫 번째\n\n두 번째"


def test_tied_scores_have_deterministic_document_id_order():
    first = run(
        ToolAction(tool="search_mail", query="Cell Leakage", reason="order")
    )
    second = run(
        ToolAction(tool="search_mail", query="Cell Leakage", reason="order")
    )

    expected = ["mail-kim-attachment-0", "mail-kim-body-0"]
    assert [item.document_id for item in first.documents] == expected
    assert [item.document_id for item in second.documents] == expected

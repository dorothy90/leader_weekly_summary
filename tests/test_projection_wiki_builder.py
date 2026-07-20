from datetime import UTC, datetime

import pytest

from knowledge_models import (
    CategoryPath,
    ClassificationDecision,
    ClassificationItem,
    ProjectionSection,
    SupportedClaim,
    WikiProjectionDocument,
    WikiProjectionSpec,
)
from projection_wiki_builder import (
    ProjectionAnalysis,
    ProjectionDraft,
    build_projection_revision,
    validate_projection_draft,
)


def evidence(agenda_id: str, week: str, summary: str) -> ClassificationItem:
    return ClassificationItem(
        agenda_id=agenda_id,
        mail_id=f"M-{agenda_id}",
        summary=summary,
        source_quote=summary,
        classification_context=summary,
        item_kind="lotcd_specific",
        decision=ClassificationDecision(
            status="confirmed",
            target_path=CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA"),
            confidence=1,
        ),
        revision_count=0,
        team="Yield",
        received_at=datetime.fromisocalendar(int(week[:4]), int(week[-2:]), 1).replace(tzinfo=UTC),
    )


def spec() -> WikiProjectionSpec:
    return WikiProjectionSpec(
        projection_id="lotcd:DRAM/Spica/4SA",
        kind="lotcd",
        key="DRAM/Spica/4SA",
        title="4SA Wiki",
        breadcrumb=["DRAM", "Spica", "4SA"],
        direct_topic_ids=["T-001"],
        direct_agenda_ids=["A-001", "A-002"],
    )


def draft(context, analysis):
    assert context["previous_document"]["as_of_week"] == "2026-W29"
    assert [item["agenda_id"] for item in context["new_evidence"]] == ["A-002"]
    return ProjectionDraft(
        summary="조건 원복 뒤 수율을 관찰 중이다.",
        sections=[ProjectionSection(
            key="current_state",
            title="현재 상태와 주요 변화",
            body="조건 원복 뒤 수율을 관찰 중이다. [agenda:A-002]",
        )],
        claims=[SupportedClaim(text="조건 원복 뒤 수율을 관찰 중이다.", agenda_ids=["A-002"])],
        weekly_update="조건을 원복하고 모니터링을 시작했다. [agenda:A-002]",
    )


def test_projection_revision_rewrites_current_narrative_and_preserves_history():
    previous = WikiProjectionDocument(
        projection_id="lotcd:DRAM/Spica/4SA",
        kind="lotcd",
        key="DRAM/Spica/4SA",
        title="4SA Wiki",
        breadcrumb=["DRAM", "Spica", "4SA"],
        summary="수율 하락 원인을 분석 중이다.",
        sections=[ProjectionSection(
            key="current_state", title="현재 상태와 주요 변화",
            body="수율 하락 원인을 분석 중이다. [agenda:A-001]",
        )],
        claims=[SupportedClaim(text="수율 하락 원인을 분석 중이다.", agenda_ids=["A-001"])],
        source_agenda_ids=["A-001"],
        direct_agenda_ids=["A-001"],
        as_of_week="2026-W29",
        revision_id="PREV-REV",
        body_markdown="old body",
        weekly_history=[],
        build_run_id="RUN-29",
        model="test-model",
        published_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    items = [
        evidence("A-001", "2026-W29", "수율이 하락했다."),
        evidence("A-002", "2026-W30", "조건을 원복했다."),
    ]

    document = build_projection_revision(
        spec(), "2026-W30", items, [], previous,
        lambda _context: ProjectionAnalysis(
            summary="조건 원복 뒤 수율을 관찰 중이다.",
            section_titles=["현재 상태와 주요 변화"],
        ),
        draft,
        model="test-model",
        build_run_id="RUN-30",
    )

    assert document.revision_id != previous.revision_id
    assert document.previous_revision_id == "PREV-REV"
    assert document.sections[0].body == "조건 원복 뒤 수율을 관찰 중이다. [agenda:A-002]"
    assert [entry.week for entry in document.weekly_history] == ["2026-W30"]
    assert document.weekly_history[0].body == "조건을 원복하고 모니터링을 시작했다. [agenda:A-002]"
    assert "## 주차별 업데이트 이력" in document.body_markdown


def test_projection_draft_rejects_uncited_factual_sentences():
    with pytest.raises(ValueError, match="uncited factual claim"):
        validate_projection_draft(
            ProjectionDraft(
                summary="요약",
                sections=[ProjectionSection(
                    key="current_state", title="현재 상태", body="수율이 하락했다.",
                )],
                claims=[],
                weekly_update="변화 없음",
            ),
            {"A-001": evidence("A-001", "2026-W30", "수율이 하락했다.")},
        )

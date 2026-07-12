from __future__ import annotations

import pytest
from pydantic import ValidationError

from category_wiki_builder import CategoryNode, load_taxonomy
from integrated_wiki_builder import (
    BuildResult,
    IssueDecision,
    NarrativeDraft,
    NarrativeValidationError,
    PageAnalysis,
    SupportedClaim,
    WeeklyHistoryEntry,
    assemble_body,
    build_child_digest,
    build_integrated_pages,
    canonical_path,
    direct_agendas_for_node,
    integrated_page_index_definition,
    invoke_structured,
    merge_weekly_history,
    render_current_body,
    validate_draft,
    validate_issue_decisions,
)


@pytest.fixture
def taxonomy():
    return load_taxonomy()


def empty_draft() -> NarrativeDraft:
    return NarrativeDraft(
        overview="",
        current_status="",
        cause_and_impact="",
        actions_and_effects="",
        pending_and_decisions="",
        accumulated_knowledge="",
        weekly_update="",
        confidence="low",
    )


def test_supported_claim_requires_mail_and_agenda_evidence():
    with pytest.raises(ValidationError):
        SupportedClaim(text="4SA 수율이 하락했다", mail_ids=[], agenda_ids=[])


def test_narrative_draft_has_the_approved_sections():
    draft = NarrativeDraft(
        overview="개요 [mail:mail-1]",
        current_status="현재 상태 [mail:mail-1]",
        cause_and_impact="원인 분석 [mail:mail-1]",
        actions_and_effects="조치 결과 [mail:mail-1]",
        pending_and_decisions="후속 확인 [mail:mail-1]",
        accumulated_knowledge="누적 패턴 [mail:mail-1]",
        weekly_update="이번 주 변경 [mail:mail-1]",
        confidence="high",
    )
    assert draft.weekly_update.startswith("이번 주")


def test_integrated_mapping_adds_structured_fields_without_vectors():
    properties = integrated_page_index_definition()["mappings"]["properties"]
    assert properties["doc_type"]["type"] == "keyword"
    assert properties["weekly_history"]["type"] == "nested"
    assert properties["citation_map"]["type"] == "nested"
    assert "embedding" not in properties


def test_canonical_path_uses_lowercase_category_segments():
    node = CategoryNode("lotcd:dram:spica:4sa", "lotcd", "DRAM", "Spica", "4SA", "4SA")

    assert canonical_path(node) == "dram/spica/4sa"


def test_tech_direct_evidence_excludes_lotcd_agenda():
    node = CategoryNode("tech:dram:spica", "tech", "DRAM", "Spica", None, "Spica")
    agendas = [
        {
            "agenda_id": "tech",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": None}],
        },
        {
            "agenda_id": "lot",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        },
    ]

    assert [item["agenda_id"] for item in direct_agendas_for_node(node, agendas)] == [
        "tech"
    ]


def test_domain_direct_evidence_requires_domain_only_target():
    node = CategoryNode("domain:dram", "domain", "DRAM", None, None, "DRAM")
    agendas = [
        {
            "agenda_id": "domain",
            "target_paths": [{"domain": "DRAM", "tech": None, "lotcd": None}],
        },
        {
            "agenda_id": "tech",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": None}],
        },
    ]

    assert [item["agenda_id"] for item in direct_agendas_for_node(node, agendas)] == [
        "domain"
    ]


def test_lotcd_direct_evidence_requires_exact_path_and_confirmed_review():
    node = CategoryNode("lotcd:dram:spica:4sa", "lotcd", "DRAM", "Spica", "4SA", "4SA")
    agendas = [
        {
            "agenda_id": "confirmed",
            "review_status": "confirmed",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        },
        {
            "agenda_id": "pending",
            "review_status": "pending",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        },
        {
            "agenda_id": "other-lot",
            "review_status": "confirmed",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SB"}],
        },
    ]

    assert [item["agenda_id"] for item in direct_agendas_for_node(node, agendas)] == [
        "confirmed"
    ]


def test_history_replaces_same_week_and_preserves_older_entries():
    old = [
        WeeklyHistoryEntry(
            week="2026-W27", body_markdown="W27", source_mail_ids=["m27"]
        ),
        WeeklyHistoryEntry(
            week="2026-W26", body_markdown="W26", source_mail_ids=["m26"]
        ),
    ]
    current = WeeklyHistoryEntry(
        week="2026-W27", body_markdown="W27 fixed", source_mail_ids=["m27b"]
    )

    merged = merge_weekly_history(old, current)

    assert [item.week for item in merged] == ["2026-W27", "2026-W26"]
    assert merged[0].body_markdown == "W27 fixed"
    assert "W26" in assemble_body("## 개요\n현재", merged)


def test_render_current_body_uses_only_current_narrative_sections():
    draft = NarrativeDraft(
        overview=" overview ",
        current_status="status",
        cause_and_impact="cause",
        actions_and_effects="actions",
        pending_and_decisions="pending",
        accumulated_knowledge="knowledge",
        weekly_update="separate history entry",
        confidence="high",
    )

    rendered = render_current_body(draft)

    assert rendered.startswith("## 개요\n\noverview")
    assert "## 누적 지식\n\nknowledge" in rendered
    assert "separate history entry" not in rendered


def test_structured_invocation_retries_once_after_validation_error():
    valid = PageAnalysis(outline=["개요"])

    class FakeRunnable:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise ValidationError.from_exception_data("PageAnalysis", [])
            return valid

    runnable = FakeRunnable()

    assert (
        invoke_structured(runnable, [{"role": "user", "content": "evidence"}]) == valid
    )
    assert runnable.calls == 2


def test_invalid_mail_citation_is_rejected():
    draft = NarrativeDraft(
        overview="근거 없는 주장 [mail:missing]",
        current_status="",
        cause_and_impact="",
        actions_and_effects="",
        pending_and_decisions="",
        accumulated_knowledge="",
        weekly_update="",
        confidence="low",
    )

    with pytest.raises(NarrativeValidationError, match="missing"):
        validate_draft(
            draft,
            allowed_agendas=[{"mail_id": "mail-1", "agenda_id": "a1", "state": "open"}],
        )


def test_resolved_decision_requires_terminal_agenda():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="resolved",
                summary="해결",
                mail_ids=["mail-1"],
                agenda_ids=["a1"],
            )
        ],
    )

    with pytest.raises(NarrativeValidationError, match="terminal"):
        validate_issue_decisions(
            analysis,
            [{"mail_id": "mail-1", "agenda_id": "a1", "state": "open"}],
        )


def test_generation_order_is_lotcd_then_tech_then_domain(taxonomy):
    calls = []

    def analyze(context):
        calls.append(context["node"]["level"])
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        [],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert isinstance(result, BuildResult)
    assert calls == ["lotcd"] * 14 + ["tech"] * 7 + ["domain"] * 2
    assert len(result.pages) == 23


def test_failed_lotcd_blocks_its_tech_and_domain(taxonomy):
    def analyze(context):
        if context["node"].get("lotcd") == "4SA":
            raise RuntimeError("generation failed")
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        [],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert "lotcd:4sa" in result.failures
    assert "tech:dram:spica" in result.failures
    assert "domain:dram" in result.failures


def test_child_digest_contains_only_traceable_analysis_and_draft_summary():
    claim = SupportedClaim(
        text="4SA 상태",
        mail_ids=["mail-1"],
        agenda_ids=["agenda-1"],
    )
    decision = IssueDecision(
        issue_id="issue-1",
        status="ongoing",
        summary="확인 중",
        mail_ids=["mail-1"],
        agenda_ids=["agenda-1"],
    )
    analysis = PageAnalysis(
        new_claims=[claim],
        issue_decisions=[decision],
        contradictions=["상태 불일치"],
        review_items=["담당자 확인"],
        outline=["개요"],
    )
    draft = NarrativeDraft(
        overview="요약 [mail:mail-1]",
        current_status="상세 [mail:mail-1]",
        cause_and_impact="",
        actions_and_effects="",
        pending_and_decisions="",
        accumulated_knowledge="",
        weekly_update="",
        confidence="medium",
    )

    digest = build_child_digest(
        {
            "canonical_id": "dram/spica/4sa",
            "as_of_week": "2026-W28",
            "confidence": "medium",
        },
        analysis,
        draft,
    )

    assert digest.model_dump() == {
        "canonical_id": "dram/spica/4sa",
        "as_of_week": "2026-W28",
        "summary": "요약 [mail:mail-1]",
        "claims": [claim.model_dump()],
        "issues": [decision.model_dump()],
        "contradictions": ["상태 불일치"],
        "confidence": "medium",
    }


def test_build_preserves_history_and_resolves_child_evidence(taxonomy):
    contexts = {}
    claim = SupportedClaim(
        text="4SA 상태",
        mail_ids=["mail-child"],
        agenda_ids=["agenda-child"],
    )
    agendas = [
        {
            "agenda_id": "agenda-child",
            "mail_id": "mail-child",
            "week": "2026-W28",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "4SA 상태",
            "subject": "4SA weekly",
            "topic": "yield",
            "source_doc_ids": ["chunk-child"],
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
        },
        {
            "agenda_id": "agenda-old",
            "mail_id": "mail-old",
            "week": "2026-W27",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-old",
            "summary": "이전 상태",
            "subject": "4SA previous",
            "topic": "yield",
            "source_doc_ids": ["chunk-old"],
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
        },
        {
            "agenda_id": "agenda-review",
            "mail_id": "mail-review",
            "week": "2026-W28",
            "state": "open",
            "review_status": "pending",
            "issue_id": "issue-review",
            "summary": "분류 검토",
            "subject": "review",
            "topic": "yield",
            "source_doc_ids": ["chunk-review"],
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
        },
    ]
    previous = {
        "lotcd:4sa": {
            "current_body_markdown": "이전 본문",
            "weekly_history": [
                {
                    "week": "2026-W27",
                    "body_markdown": "W27",
                    "source_mail_ids": ["mail-old"],
                },
                {
                    "week": "2026-W26",
                    "body_markdown": "W26",
                    "source_mail_ids": [],
                },
                {
                    "week": "2026-W25",
                    "body_markdown": "W25",
                    "source_mail_ids": [],
                },
            ],
            "citation_map": [
                {
                    "mail_id": "mail-old",
                    "agenda_ids": ["agenda-old"],
                    "used_in_sections": ["개요"],
                    "category_paths": ["dram/spica/4sa"],
                }
            ],
        }
    }

    def analyze(context):
        node_id = context["node"]["id"]
        contexts[node_id] = context
        if node_id in {"lotcd:4sa", "tech:dram:spica"}:
            return PageAnalysis(
                retained_claims=[claim],
                contradictions=["상태 불일치"],
                review_items=["추가 검토"],
                outline=["개요"],
            )
        return PageAnalysis(outline=["개요"])

    def draft(context, analysis):
        node_id = context["node"]["id"]
        if node_id == "lotcd:4sa":
            return NarrativeDraft(
                overview="LOT 상태 [mail:mail-child]",
                current_status="",
                cause_and_impact="",
                actions_and_effects="",
                pending_and_decisions="",
                accumulated_knowledge="",
                weekly_update="이번 주 [mail:mail-child]",
                confidence="high",
            )
        if node_id == "tech:dram:spica":
            return NarrativeDraft(
                overview="Tech 상태 [mail:mail-child]",
                current_status="",
                cause_and_impact="",
                actions_and_effects="",
                pending_and_decisions="",
                accumulated_knowledge="",
                weekly_update="",
                confidence="medium",
            )
        return empty_draft()

    result = build_integrated_pages(
        taxonomy,
        agendas,
        previous,
        as_of_week="2026-W28",
        analyze=analyze,
        draft=draft,
    )
    pages = {page["category_id"]: page for page in result.pages}

    assert not result.failures
    assert [item["agenda_id"] for item in contexts["lotcd:4sa"]["allowed_agendas"]] == [
        "agenda-child",
        "agenda-old",
    ]
    assert contexts["lotcd:4sa"]["previous_current_body_markdown"] == "이전 본문"
    assert [item["week"] for item in contexts["lotcd:4sa"]["recent_history"]] == [
        "2026-W27",
        "2026-W26",
    ]
    assert contexts["tech:dram:spica"]["child_digests"][0]["claims"][0][
        "agenda_ids"
    ] == ["agenda-child"]
    assert [
        item["agenda_id"] for item in contexts["tech:dram:spica"]["allowed_agendas"]
    ] == ["agenda-child"]

    page = pages["lotcd:4sa"]
    assert [item["week"] for item in page["weekly_history"]] == [
        "2026-W28",
        "2026-W27",
        "2026-W26",
        "2026-W25",
    ]
    assert page["aliases"] == ["SP LPDDR5 24G Fab4"]
    assert page["contradictions"] == ["상태 불일치"]
    assert page["generation_review_items"] == ["추가 검토"]
    assert page["review_agenda_ids"] == ["agenda-review"]
    assert page["citation_map"][0]["category_paths"] == ["dram/spica/4sa"]


def test_missing_child_digest_agenda_blocks_parent(taxonomy):
    missing_claim = SupportedClaim(
        text="근거 없음",
        mail_ids=["mail-missing"],
        agenda_ids=["agenda-missing"],
    )

    def analyze(context):
        if context["node"]["id"] == "lotcd:4sa":
            return PageAnalysis(retained_claims=[missing_claim], outline=["개요"])
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        [],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert "agenda-missing" in result.failures["tech:dram:spica"]
    assert result.failures["domain:dram"] == "required child failed"

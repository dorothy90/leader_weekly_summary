from __future__ import annotations

import pytest
from pydantic import ValidationError

from category_wiki_builder import CategoryNode
from integrated_wiki_builder import (
    NarrativeDraft,
    SupportedClaim,
    WeeklyHistoryEntry,
    assemble_body,
    canonical_path,
    direct_agendas_for_node,
    integrated_page_index_definition,
    merge_weekly_history,
    render_current_body,
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
        WeeklyHistoryEntry(week="2026-W27", body_markdown="W27", source_mail_ids=["m27"]),
        WeeklyHistoryEntry(week="2026-W26", body_markdown="W26", source_mail_ids=["m26"]),
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

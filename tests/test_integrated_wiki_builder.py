from __future__ import annotations

import pytest
from pydantic import ValidationError

from integrated_wiki_builder import (
    NarrativeDraft,
    SupportedClaim,
    integrated_page_index_definition,
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

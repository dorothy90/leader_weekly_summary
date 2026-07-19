import pytest
from pydantic import ValidationError

from knowledge_models import CategoryPath, SupportedClaim, WikiTopic


def test_supported_claim_requires_agenda_evidence():
    with pytest.raises(ValidationError):
        SupportedClaim(text="4SA 불량이 감소했다", agenda_ids=[])


def test_topic_keeps_one_canonical_revision_pointer():
    topic = WikiTopic(
        topic_id="T-001",
        title="4SA D1 불량",
        topic_kind="issue",
        primary_area="yield_defect",
        state="monitoring",
        importance="high",
        first_seen_week="2026-W29",
        last_updated_week="2026-W30",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        teams=["Yield"],
        source_agenda_ids=["A-001"],
        current_revision_id="REV-002",
    )
    assert topic.current_revision_id == "REV-002"

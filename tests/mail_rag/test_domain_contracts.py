from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.chat import ChatRequest
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.domain.research import ResearchJob, ResearchStatus


def test_policy_context_normalizes_bounded_request_owner():
    policy = PolicyContext.from_user_id("  kim.dh  ")
    assert policy.user_id == "kim.dh"
    assert len(policy.decision_id) == 32


@pytest.mark.parametrize("value", ["", "   ", "a" * 129, "kim/dh", "kim dh"])
def test_policy_context_rejects_invalid_owner(value):
    with pytest.raises(ValidationError):
        PolicyContext.from_user_id(value)


def test_chat_request_keeps_user_id_in_body_and_normalizes_weeks():
    request = ChatRequest(user_id="kim", message="최근 이슈", filters={"weeks": ["2026-8"]})
    assert request.user_id == "kim"
    assert request.filters.weeks == ["2026-08"]


def test_evidence_requires_owner_and_bounded_excerpt():
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="S1", source_type="mail", document_id="doc-1",
            title="title", excerpt="x" * 8001, score=1.0,
            user_id="kim", acl_decision_id="d1", content_hash="h1",
        )


def test_research_job_serializes_utc_state():
    job = ResearchJob(
        job_id="research-1", user_id="kim", trace_id="trace-1",
        question="12주 추세", status=ResearchStatus.QUEUED,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    assert job.model_dump(mode="json")["status"] == "queued"

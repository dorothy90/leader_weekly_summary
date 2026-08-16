from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.chat import (
    ChatRequest,
    ExecutionMetadata,
    QualityStatus,
    RoutingDiagnostics,
)
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.domain.research import ResearchJob, ResearchStatus


def test_runtime_requirements_pin_pydantic_settings():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "pydantic-settings==2.11.0" in requirements


def test_policy_context_normalizes_bounded_request_owner():
    policy = PolicyContext.from_user_id("  kim.dh  ")
    assert policy.user_id == "kim.dh"
    assert len(policy.decision_id) == 32


@pytest.mark.parametrize("value", ["", "   ", "a" * 129, "kim/dh", "kim dh"])
def test_policy_context_rejects_invalid_owner(value):
    with pytest.raises(ValidationError):
        PolicyContext.from_user_id(value)


def test_chat_request_keeps_user_id_in_body_and_normalizes_weeks():
    request = ChatRequest(
        user_id="kim", message="최근 이슈", filters={"weeks": ["2026-8"]}
    )
    assert request.user_id == "kim"
    assert request.filters.weeks == ["2026-08"]


def test_routing_diagnostics_and_not_used_retrieval_are_bounded():
    routing = RoutingDiagnostics(
        requested_mode="auto",
        route="general",
        executed_system="general",
        reason_code="deterministic_general",
        confidence=1,
        estimated_searches=0,
    )
    quality = QualityStatus(citation_valid=True, retrieval_mode="not_used")

    assert routing.route == "general"
    assert quality.retrieval_mode == "not_used"


def test_quality_status_accepts_deterministic_retrieval_mode():
    quality = QualityStatus(
        citation_valid=True,
        retrieval_mode="deterministic",
    )

    assert quality.retrieval_mode == "deterministic"


def test_execution_metadata_distinguishes_unstarted_timeout_from_citation_failure():
    execution = ExecutionMetadata(
        status="failed",
        failure_stage="planning",
        error_code="LLM_TIMEOUT",
        retryable=True,
        search_count=0,
        evidence_count=0,
        duration_ms=20_003,
        include_in_llm_history=False,
    )
    quality = QualityStatus(
        citation_valid=None,
        limited_answer=False,
        retrieval_mode="not_started",
    )

    assert execution.error_code == "LLM_TIMEOUT"
    assert execution.failure_stage == "planning"
    assert quality.citation_valid is None
    assert quality.retrieval_mode == "not_started"


@pytest.mark.parametrize("week", ["26-8", "20260-8", "2026-008"])
def test_chat_request_rejects_noncanonical_week_widths(week):
    with pytest.raises(ValidationError):
        ChatRequest(user_id="kim", message="최근 이슈", filters={"weeks": [week]})


def test_evidence_requires_owner_and_bounded_excerpt():
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="S1",
            source_type="mail",
            document_id="doc-1",
            title="title",
            excerpt="x" * 8001,
            score=1.0,
            user_id="kim",
            acl_decision_id="d1",
            content_hash="h1",
        )


def test_research_job_serializes_utc_state():
    job = ResearchJob(
        job_id="research-1",
        user_id="kim",
        trace_id="trace-1",
        question="12주 추세",
        status=ResearchStatus.QUEUED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    serialized = job.model_dump(mode="json")
    assert serialized["status"] == "queued"
    assert serialized["created_at"].endswith(("Z", "+00:00"))
    assert serialized["updated_at"].endswith(("Z", "+00:00"))

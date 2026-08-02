import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.chat import ChatReference
from app.domain.chat import normalize_bm25_fallback
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.domain.research import (
    MAX_RESEARCH_REPORT_BYTES,
    RESEARCH_ABSTENTION,
    ResearchStatus,
)
from app.security.citations import CitationValidator
from app.security.redaction import opaque_identifier, sanitize_text

router = APIRouter(prefix="/v1/research")


class OwnerBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_id: str = Field(min_length=1, max_length=128)


class ResearchJobResponse(BaseModel):
    job_id: str
    status: ResearchStatus
    progress: int = Field(ge=0, le=100)
    plan_summary: str
    result_markdown: str | None = None
    references: list[ChatReference] = Field(default_factory=list, max_length=32)
    disclosures: list[str] = Field(default_factory=list, max_length=4)
    error_code: str | None = None


def _policy(payload: OwnerBody) -> PolicyContext:
    try:
        return PolicyContext.from_user_id(payload.user_id)
    except ValidationError:
        raise AppError(
            ErrorCode.INVALID_USER_ID, "유효하지 않은 사용자 식별자입니다."
        ) from None


def _response(job, policy: PolicyContext) -> ResearchJobResponse:
    if job.user_id != policy.user_id:
        raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, "조사 작업을 찾을 수 없습니다.")
    report = sanitize_text(job.result_markdown) if job.result_markdown else None
    disclosures = [safe for item in job.disclosures if (safe := sanitize_text(item))]
    report, disclosures = normalize_bm25_fallback(
        report or "", disclosures, max_bytes=MAX_RESEARCH_REPORT_BYTES
    )
    validation = CitationValidator().validate(report, job.result_evidence, policy)
    safe_result = bool(job.result_evidence) and validation.valid
    if job.result_markdown is not None and not safe_result:
        report, disclosures = normalize_bm25_fallback(
            RESEARCH_ABSTENTION,
            disclosures,
            max_bytes=MAX_RESEARCH_REPORT_BYTES,
        )
    cited_ids = set(validation.cited_ids) if safe_result else set()
    references = []
    for item in job.result_evidence:
        if item.user_id != policy.user_id or item.evidence_id not in cited_ids:
            continue
        references.append(
            ChatReference(
                evidence_id=opaque_identifier(item.evidence_id),
                source_type=item.source_type,
                document_id=opaque_identifier(item.document_id),
                title=sanitize_text(item.title),
                excerpt=sanitize_text(item.excerpt) or "[REDACTED]",
                team=sanitize_text(item.team) if item.team else None,
                week=item.week,
            )
        )
    return ResearchJobResponse(
        job_id=opaque_identifier(job.job_id),
        status=job.status,
        progress=job.progress,
        plan_summary=sanitize_text(job.plan_summary),
        result_markdown=(report if job.result_markdown is not None else None),
        references=references,
        disclosures=disclosures,
        error_code=(sanitize_text(job.error_code) if job.error_code else None),
    )


@router.post("/{job_id}/status", response_model=ResearchJobResponse)
async def status(job_id: str, payload: OwnerBody, request: Request):
    policy = _policy(payload)
    job = await request.app.state.container.jobs.get(job_id, policy)
    return _response(job, policy)


@router.post("/{job_id}/cancel", response_model=ResearchJobResponse)
async def cancel(job_id: str, payload: OwnerBody, request: Request):
    policy = _policy(payload)
    job = await request.app.state.container.jobs.request_cancel(job_id, policy)
    return _response(job, policy)


@router.post("/{job_id}/retry", response_model=ResearchJobResponse)
async def retry(job_id: str, payload: OwnerBody, request: Request):
    policy = _policy(payload)
    job = await request.app.state.container.jobs.retry(job_id, policy)
    return _response(job, policy)


@router.post("/{job_id}/events")
async def events(job_id: str, payload: OwnerBody, request: Request):
    policy = _policy(payload)
    # Authorize before returning a streaming 200 response.
    await request.app.state.container.jobs.get(job_id, policy)

    async def stream():
        while True:
            job = await request.app.state.container.jobs.get(job_id, policy)
            safe = _response(job, policy)
            event = {
                "job_id": safe.job_id,
                "status": safe.status.value,
                "progress": safe.progress,
            }
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if job.status in {
                ResearchStatus.COMPLETED,
                ResearchStatus.FAILED,
                ResearchStatus.CANCELLED,
            }:
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")

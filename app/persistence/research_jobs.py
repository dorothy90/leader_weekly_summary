import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.domain.chat import normalize_bm25_fallback
from app.domain.evidence import RetrievalFilters
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.domain.research import (
    MAX_RESEARCH_REPORT_BYTES,
    RESEARCH_ABSTENTION,
    ResearchJob,
    ResearchStatus,
)
from app.persistence.conversations import (
    sanitize_evidence_for_memory,
    sanitize_filters_for_memory,
)
from app.security.citations import CitationValidator
from app.security.redaction import opaque_identifier, sanitize_text

_NOT_FOUND = "조사 작업을 찾을 수 없습니다."
_STALE_LEASE = "조사 작업 임대가 만료되었습니다."
_TERMINAL = {
    ResearchStatus.COMPLETED,
    ResearchStatus.FAILED,
    ResearchStatus.CANCELLED,
}


def _owner_error() -> AppError:
    return AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _NOT_FOUND)


def _safe_code(value: str | ErrorCode) -> str:
    raw = value.value if isinstance(value, ErrorCode) else str(value)
    safe = sanitize_text(raw)
    return safe[:64] if safe else "RESEARCH_FAILED"


def _truncate_utf8(value: str, byte_limit: int) -> str:
    return value.encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")


def _new_job(
    policy: PolicyContext,
    trace_id: str,
    question: str,
    plan_summary: str,
    filters: RetrievalFilters | None = None,
) -> ResearchJob:
    if not isinstance(policy, PolicyContext):
        raise _owner_error()
    now = datetime.now(UTC)
    return ResearchJob(
        job_id=f"research_{uuid.uuid4().hex}",
        user_id=policy.user_id,
        trace_id=_truncate_utf8(opaque_identifier(trace_id), 128),
        question=_truncate_utf8(sanitize_text(question), 4000) or "메일 조사",
        status=ResearchStatus.QUEUED,
        created_at=now,
        updated_at=now,
        plan_summary=(
            _truncate_utf8(sanitize_text(plan_summary), 500) or "질문 범위 조사"
        ),
        filters=sanitize_filters_for_memory(filters or RetrievalFilters()),
    )


def _safe_result(result, policy: PolicyContext):
    report = sanitize_text(result.report) or "[REDACTED]"
    disclosures = []
    for value in result.disclosures:
        safe = sanitize_text(value)
        if safe and safe not in disclosures:
            disclosures.append(safe)
    report, disclosures = normalize_bm25_fallback(
        report, disclosures, max_bytes=MAX_RESEARCH_REPORT_BYTES
    )
    supplied = list(result.evidence)
    if (
        not getattr(result, "citation_valid", False)
        or not supplied
        or any(item.user_id != policy.user_id for item in supplied)
    ):
        report, disclosures = normalize_bm25_fallback(
            RESEARCH_ABSTENTION,
            disclosures,
            max_bytes=MAX_RESEARCH_REPORT_BYTES,
        )
        return report, [], disclosures
    safe_evidence = [
        sanitize_evidence_for_memory(item, policy) for item in supplied[:32]
    ]
    validation = CitationValidator().validate(report, safe_evidence, policy)
    if not validation.valid:
        report, disclosures = normalize_bm25_fallback(
            RESEARCH_ABSTENTION,
            disclosures,
            max_bytes=MAX_RESEARCH_REPORT_BYTES,
        )
        return report, [], disclosures
    by_id = {item.evidence_id: item for item in safe_evidence}
    return report, [by_id[item] for item in validation.cited_ids], disclosures


class ResearchJobStore(Protocol):
    async def create(self, policy, trace_id, question, plan_summary, filters=None): ...
    async def get(self, job_id, policy): ...
    async def request_cancel(self, job_id, policy): ...
    async def claim(self, lease_seconds=60): ...
    async def complete(self, job_id, policy, lease_token, result): ...
    async def fail(self, job_id, policy, lease_token, error_code): ...


class InMemoryResearchJobStore:
    def __init__(self):
        self.jobs: dict[str, ResearchJob] = {}

    async def create(self, policy, trace_id, question, plan_summary, filters=None):
        job = _new_job(policy, trace_id, question, plan_summary, filters)
        self.jobs[job.job_id] = job
        return job.model_copy(deep=True)

    async def get(self, job_id, policy):
        if not isinstance(policy, PolicyContext):
            raise _owner_error()
        job = self.jobs.get(job_id)
        if job is None or job.user_id != policy.user_id:
            raise _owner_error()
        return job.model_copy(deep=True)

    async def request_cancel(self, job_id, policy):
        if not isinstance(policy, PolicyContext):
            raise _owner_error()
        job = self.jobs.get(job_id)
        if job is None or job.user_id != policy.user_id:
            raise _owner_error()
        if job.status in _TERMINAL:
            return job
        status = (
            ResearchStatus.CANCELLED
            if job.status == ResearchStatus.QUEUED
            else ResearchStatus.CANCELLING
        )
        updated = job.model_copy(
            update={
                "status": status,
                "lease_until": (
                    None if status == ResearchStatus.CANCELLED else job.lease_until
                ),
                "updated_at": datetime.now(UTC),
            }
        )
        self.jobs[job_id] = updated
        return updated.model_copy(deep=True)

    async def claim(self, lease_seconds=60):
        now = datetime.now(UTC)
        candidates = sorted(self.jobs.values(), key=lambda item: item.created_at)
        for job in candidates:
            cancelling = job.status == ResearchStatus.CANCELLING and (
                job.lease_until is None or job.lease_until <= now
            )
            claimable = (
                cancelling
                or job.status == ResearchStatus.QUEUED
                or (
                    job.status == ResearchStatus.RUNNING
                    and job.lease_until is not None
                    and job.lease_until <= now
                )
            )
            if not claimable:
                continue
            claimed = job.model_copy(
                update={
                    "status": (
                        ResearchStatus.CANCELLING
                        if cancelling
                        else ResearchStatus.RUNNING
                    ),
                    "attempts": job.attempts + 1,
                    "lease_until": now + timedelta(seconds=max(1, lease_seconds)),
                    "lease_token": uuid.uuid4().hex,
                    "updated_at": now,
                }
            )
            self.jobs[job.job_id] = claimed
            return claimed.model_copy(deep=True)
        return None

    async def _leased(self, job_id, policy, lease_token):
        job = await self.get(job_id, policy)
        if job.status == ResearchStatus.CANCELLING:
            raise AppError(ErrorCode.JOB_CANCELLED, "조사 작업이 취소 중입니다.")
        if (
            job.status != ResearchStatus.RUNNING
            or job.lease_token != lease_token
            or job.lease_until is None
            or job.lease_until <= datetime.now(UTC)
        ):
            raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _STALE_LEASE)
        return job

    async def complete(self, job_id, policy, lease_token, result):
        current = await self.get(job_id, policy)
        if current.status == ResearchStatus.COMPLETED:
            if current.lease_token != lease_token:
                raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _STALE_LEASE)
            return current
        job = await self._leased(job_id, policy, lease_token)
        report, safe_evidence, disclosures = _safe_result(result, policy)
        completed = job.model_copy(
            update={
                "status": ResearchStatus.COMPLETED,
                "progress": 100,
                "result_markdown": report,
                "result_evidence": safe_evidence,
                "disclosures": disclosures,
                "lease_until": None,
                "updated_at": datetime.now(UTC),
            }
        )
        self.jobs[job_id] = completed
        return completed.model_copy(deep=True)

    async def mark_cancelled(self, job_id, policy, lease_token):
        job = await self.get(job_id, policy)
        if job.status == ResearchStatus.CANCELLED:
            return job
        if job.status not in {ResearchStatus.RUNNING, ResearchStatus.CANCELLING}:
            raise AppError(ErrorCode.JOB_CANCELLED, "조사 작업을 취소할 수 없습니다.")
        if job.lease_token != lease_token:
            raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _STALE_LEASE)
        if job.lease_until is None or job.lease_until <= datetime.now(UTC):
            raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _STALE_LEASE)
        cancelled = job.model_copy(
            update={
                "status": ResearchStatus.CANCELLED,
                "lease_until": None,
                "updated_at": datetime.now(UTC),
            }
        )
        self.jobs[job_id] = cancelled
        return cancelled.model_copy(deep=True)

    async def fail(self, job_id, policy, lease_token, error_code):
        current = await self.get(job_id, policy)
        if current.status == ResearchStatus.FAILED:
            if current.lease_token != lease_token:
                raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _STALE_LEASE)
            return current
        job = await self._leased(job_id, policy, lease_token)
        failed = job.model_copy(
            update={
                "status": ResearchStatus.FAILED,
                "error_code": _safe_code(error_code),
                "lease_until": None,
                "updated_at": datetime.now(UTC),
            }
        )
        self.jobs[job_id] = failed
        return failed.model_copy(deep=True)

    async def retry(self, job_id, policy):
        job = await self.get(job_id, policy)
        if job.status not in {ResearchStatus.FAILED, ResearchStatus.CANCELLED}:
            return job
        queued = job.model_copy(
            update={
                "status": ResearchStatus.QUEUED,
                "progress": 0,
                "result_markdown": None,
                "result_evidence": [],
                "disclosures": [],
                "error_code": None,
                "lease_until": None,
                "lease_token": None,
                "updated_at": datetime.now(UTC),
            }
        )
        self.jobs[job_id] = queued
        return queued.model_copy(deep=True)


class MongoResearchJobStore:
    def __init__(self, collection):
        self.collection = collection

    async def create(self, policy, trace_id, question, plan_summary, filters=None):
        job = _new_job(policy, trace_id, question, plan_summary, filters)
        document = job.model_dump(mode="python")
        document["_id"] = job.job_id
        await self.collection.insert_one(document)
        return job

    async def get(self, job_id, policy):
        if not isinstance(policy, PolicyContext):
            raise _owner_error()
        document = await self.collection.find_one(
            {"_id": job_id, "user_id": policy.user_id}
        )
        if document is None:
            raise _owner_error()
        return ResearchJob.model_validate(document)

    async def request_cancel(self, job_id, policy):
        from pymongo import ReturnDocument

        if not isinstance(policy, PolicyContext):
            raise _owner_error()
        now = datetime.now(UTC)
        document = await self.collection.find_one_and_update(
            {
                "_id": job_id,
                "user_id": policy.user_id,
                "status": ResearchStatus.QUEUED,
            },
            {
                "$set": {
                    "status": ResearchStatus.CANCELLED,
                    "lease_until": None,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is not None:
            return ResearchJob.model_validate(document)
        document = await self.collection.find_one_and_update(
            {
                "_id": job_id,
                "user_id": policy.user_id,
                "status": {"$in": [ResearchStatus.RUNNING, ResearchStatus.CANCELLING]},
            },
            {
                "$set": {
                    "status": ResearchStatus.CANCELLING,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is not None:
            return ResearchJob.model_validate(document)
        return await self.get(job_id, policy)

    async def claim(self, lease_seconds=60):
        from pymongo import ReturnDocument

        now = datetime.now(UTC)
        lease_token = uuid.uuid4().hex
        document = await self.collection.find_one_and_update(
            {
                "status": ResearchStatus.CANCELLING,
                "$or": [
                    {"lease_until": {"$lte": now}},
                    {"lease_until": None},
                ],
            },
            {
                "$set": {
                    "lease_until": now + timedelta(seconds=max(1, lease_seconds)),
                    "lease_token": lease_token,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if document is not None:
            return ResearchJob.model_validate(document)
        document = await self.collection.find_one_and_update(
            {
                "$or": [
                    {"status": ResearchStatus.QUEUED},
                    {
                        "status": ResearchStatus.RUNNING,
                        "lease_until": {"$lte": now},
                    },
                ]
            },
            {
                "$set": {
                    "status": ResearchStatus.RUNNING,
                    "lease_until": now + timedelta(seconds=max(1, lease_seconds)),
                    "lease_token": lease_token,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return ResearchJob.model_validate(document) if document else None

    async def _leased_update(self, job_id, policy, lease_token, values):
        from pymongo import ReturnDocument

        document = await self.collection.find_one_and_update(
            {
                "_id": job_id,
                "user_id": policy.user_id,
                "status": ResearchStatus.RUNNING,
                "lease_token": lease_token,
                "lease_until": {"$gt": datetime.now(UTC)},
            },
            {"$set": {**values, "updated_at": datetime.now(UTC)}},
            return_document=ReturnDocument.AFTER,
        )
        if document is not None:
            return ResearchJob.model_validate(document)
        current = await self.get(job_id, policy)
        if (
            current.status == values.get("status")
            and current.lease_token == lease_token
        ):
            return current
        if current.status == ResearchStatus.CANCELLING:
            raise AppError(ErrorCode.JOB_CANCELLED, "조사 작업이 취소 중입니다.")
        raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _STALE_LEASE)

    async def complete(self, job_id, policy, lease_token, result):
        report, safe_evidence, disclosures = _safe_result(result, policy)
        return await self._leased_update(
            job_id,
            policy,
            lease_token,
            {
                "status": ResearchStatus.COMPLETED,
                "progress": 100,
                "result_markdown": report,
                "result_evidence": [
                    item.model_dump(mode="python") for item in safe_evidence
                ],
                "disclosures": disclosures,
                "lease_until": None,
            },
        )

    async def mark_cancelled(self, job_id, policy, lease_token):
        from pymongo import ReturnDocument

        document = await self.collection.find_one_and_update(
            {
                "_id": job_id,
                "user_id": policy.user_id,
                "status": {"$in": [ResearchStatus.RUNNING, ResearchStatus.CANCELLING]},
                "lease_token": lease_token,
                "lease_until": {"$gt": datetime.now(UTC)},
            },
            {
                "$set": {
                    "status": ResearchStatus.CANCELLED,
                    "lease_until": None,
                    "updated_at": datetime.now(UTC),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document:
            return ResearchJob.model_validate(document)
        current = await self.get(job_id, policy)
        if (
            current.status == ResearchStatus.CANCELLED
            and current.lease_token == lease_token
        ):
            return current
        raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _STALE_LEASE)

    async def fail(self, job_id, policy, lease_token, error_code):
        return await self._leased_update(
            job_id,
            policy,
            lease_token,
            {
                "status": ResearchStatus.FAILED,
                "error_code": _safe_code(error_code),
                "lease_until": None,
            },
        )

    async def retry(self, job_id, policy):
        from pymongo import ReturnDocument

        current = await self.get(job_id, policy)
        if current.status not in {ResearchStatus.FAILED, ResearchStatus.CANCELLED}:
            return current
        document = await self.collection.find_one_and_update(
            {
                "_id": job_id,
                "user_id": policy.user_id,
                "status": current.status,
            },
            {
                "$set": {
                    "status": ResearchStatus.QUEUED,
                    "progress": 0,
                    "result_markdown": None,
                    "result_evidence": [],
                    "disclosures": [],
                    "error_code": None,
                    "lease_until": None,
                    "lease_token": None,
                    "updated_at": datetime.now(UTC),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return (
            ResearchJob.model_validate(document)
            if document
            else await self.get(job_id, policy)
        )

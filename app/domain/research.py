from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.evidence import Evidence, RetrievalFilters

RESEARCH_ABSTENTION = "확인 가능한 조사 근거가 없어 보고서를 제공할 수 없습니다."
MAX_RESEARCH_REPORT_BYTES = 8_000


class ResearchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLING = "cancelling"


class ResearchJob(BaseModel):
    job_id: str
    user_id: str
    trace_id: str
    question: str
    status: ResearchStatus
    created_at: datetime
    updated_at: datetime
    plan_summary: str = ""
    progress: int = Field(default=0, ge=0, le=100)
    attempts: int = Field(default=0, ge=0)
    result_markdown: str | None = None
    error_code: str | None = None
    lease_until: datetime | None = None
    lease_token: str | None = None
    result_evidence: list[Evidence] = Field(default_factory=list, max_length=32)
    disclosures: list[str] = Field(default_factory=list, max_length=4)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)

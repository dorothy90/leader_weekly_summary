from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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

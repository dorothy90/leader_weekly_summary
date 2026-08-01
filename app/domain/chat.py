from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.evidence import Evidence, RetrievalFilters
from app.domain.research import ResearchStatus

BM25_FALLBACK_DISCLOSURE = (
    "임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. "
    "의미 기반 검색 결과가 일부 누락될 수 있습니다."
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    response_mode: Literal["auto", "fast", "deep"] = "auto"


class RouteDecision(BaseModel):
    route: Literal["fast", "deep", "clarify", "general"]
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    estimated_searches: int = Field(ge=0, le=24)
    clarification_question: str | None = None
    requested_output: Literal["answer", "table", "report", "presentation"] = "answer"


class QualityStatus(BaseModel):
    citation_valid: bool
    limited_answer: bool = False
    retrieval_mode: Literal["hybrid", "bm25"] = "hybrid"


class FastRAGResult(BaseModel):
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    quality: QualityStatus
    disclosures: list[str] = Field(default_factory=list)


class ChatReference(BaseModel):
    """Public evidence metadata; ownership and storage fields stay internal."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1, max_length=32)
    source_type: Literal["mail", "wiki", "statistic"]
    document_id: str = Field(min_length=1, max_length=256)
    title: str = Field(default="", max_length=500)
    excerpt: str = Field(min_length=1, max_length=8000)
    team: str | None = Field(default=None, max_length=100)
    week: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class ChatResponse(BaseModel):
    conversation_id: str
    mode: Literal["fast_rag", "deep_research"]
    answer: str | None = None
    references: list[ChatReference] = Field(default_factory=list)
    quality: QualityStatus | None = None
    disclosures: list[str] = Field(default_factory=list)
    trace_id: str
    job_id: str | None = None
    status: ResearchStatus | None = None
    plan_summary: str | None = None

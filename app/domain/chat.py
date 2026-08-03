from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.evidence import Evidence, RetrievalFilters
from app.domain.research import ResearchStatus
from app.observability.node_runs import NodeRun

BM25_FALLBACK_DISCLOSURE = (
    "임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. "
    "의미 기반 검색 결과가 일부 누락될 수 있습니다."
)


def normalize_bm25_fallback(
    text: str,
    disclosures: list[str],
    *,
    max_bytes: int | None = None,
) -> tuple[str, list[str]]:
    """Return at most one exact fallback disclosure in text and metadata."""
    fallback = BM25_FALLBACK_DISCLOSURE in text or any(
        BM25_FALLBACK_DISCLOSURE in item for item in disclosures
    )
    base = text.replace(BM25_FALLBACK_DISCLOSURE, "").strip()
    suffix = f"\n\n{BM25_FALLBACK_DISCLOSURE}" if fallback else ""
    if max_bytes is not None:
        remaining = max(0, max_bytes - len(suffix.encode("utf-8")))
        base = base.encode("utf-8")[:remaining].decode("utf-8", errors="ignore").strip()
    normalized_text = f"{base}{suffix}".strip()
    others = []
    for item in disclosures:
        without_fallback = item.replace(BM25_FALLBACK_DISCLOSURE, "").strip()
        if without_fallback and without_fallback not in others:
            others.append(without_fallback)
    normalized_disclosures = (
        [BM25_FALLBACK_DISCLOSURE, *others][:4] if fallback else others[:4]
    )
    return normalized_text, normalized_disclosures


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
    route: Literal["fast", "deep", "clarify", "general", "diagnostic", "corpus_info"]
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    estimated_searches: int = Field(ge=0, le=24)
    clarification_question: str | None = None
    requested_output: Literal["answer", "table", "report", "presentation"] = "answer"


class RoutingDiagnostics(BaseModel):
    requested_mode: Literal["auto", "fast", "deep"]
    route: Literal["fast", "deep", "clarify", "general", "diagnostic", "corpus_info"]
    executed_system: Literal[
        "general", "fast_rag", "deep_research", "clarification", "diagnostic", "corpus_info"
    ]
    reason_code: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0, le=1)
    estimated_searches: int = Field(ge=0, le=24)
    context_used: bool = False
    history_message_count: int = Field(default=0, ge=0, le=20)
    history_trimmed: bool = False


class ExecutionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["succeeded", "limited", "failed"]
    failure_stage: Literal[
        "contextualization",
        "planning",
        "embedding",
        "retrieval",
        "grading",
        "generation",
        "citation_validation",
        "support_validation",
    ] | None = None
    error_code: str | None = Field(default=None, max_length=128)
    retryable: bool = False
    search_count: int = Field(default=0, ge=0, le=24)
    evidence_count: int = Field(default=0, ge=0, le=32)
    duration_ms: int = Field(default=0, ge=0)
    include_in_llm_history: bool = True
    node_runs: list[NodeRun] = Field(default_factory=list, max_length=64)


class QualityStatus(BaseModel):
    citation_valid: bool | None
    limited_answer: bool = False
    retrieval_mode: Literal[
        "hybrid", "bm25", "not_used", "not_started"
    ] = "hybrid"


class FastRAGResult(BaseModel):
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    quality: QualityStatus
    disclosures: list[str] = Field(default_factory=list)
    execution: ExecutionMetadata | None = None


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
    mode: Literal["fast_rag", "deep_research", "diagnostic", "corpus_info"]
    answer: str | None = None
    references: list[ChatReference] = Field(default_factory=list)
    quality: QualityStatus | None = None
    disclosures: list[str] = Field(default_factory=list)
    trace_id: str
    routing: RoutingDiagnostics
    job_id: str | None = None
    status: ResearchStatus | None = None
    plan_summary: str | None = None
    execution: ExecutionMetadata | None = None

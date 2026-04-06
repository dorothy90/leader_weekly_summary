"""
Deep Mining Pydantic 스키마
- Request/Response
- Query Analysis
- Evidence Extraction (Map)
- Synthesis (Reduce)
- Presentation Planning
"""

from typing import List, Dict, Optional
from pydantic import BaseModel, Field


# ========== Request / Response ==========

class DeepMineRequest(BaseModel):
    """Deep Mining 작업 요청"""
    user_id: str
    query: str
    team: Optional[str] = None
    week_from: Optional[str] = None
    week_to: Optional[str] = None
    mail_type: Optional[str] = None
    num_slides: int = Field(default=4, ge=1, le=20)


class DeepMineResponse(BaseModel):
    """Deep Mining 작업 상태/결과"""
    job_id: str
    status: str  # "processing" | "completed" | "failed"
    text_summary: Optional[str] = None
    pptx_url: Optional[str] = None
    document_count: Optional[int] = None
    progress: Optional[float] = None  # 0.0 ~ 1.0
    error: Optional[str] = None


# ========== Query Analysis ==========

class QueryFilters(BaseModel):
    """analyze_query 노드 출력 — LLM이 자연어 질의를 파싱한 결과"""
    teams: List[str] = []
    week_from: Optional[str] = None
    week_to: Optional[str] = None
    mail_type: Optional[str] = None
    key_topics: List[str] = []


# ========== Evidence Extraction (Map) ==========

class Fact(BaseModel):
    """단일 사실 — Map 단계에서 추출"""
    claim: str
    team: str
    week: str
    evidence_ref: str  # mail_id
    confidence: float = 1.0


class MetricItem(BaseModel):
    """핵심 수치"""
    name: str
    display_value: str  # "98.5%", "+3.2pp" 등 슬라이드 표시용
    value: Optional[float] = None
    unit: Optional[str] = None
    team: str
    week: str
    evidence_ref: str


class RiskItem(BaseModel):
    """리스크 항목"""
    description: str
    severity: str  # "high" | "medium" | "low"
    team: str
    evidence_ref: str


class TrendSignal(BaseModel):
    """트렌드 신호"""
    description: str
    direction: str  # "improving" | "declining" | "stable"
    teams: List[str]
    evidence_refs: List[str]


class BatchExtraction(BaseModel):
    """Map 노드 1배치 출력"""
    facts: List[Fact] = []
    metrics: List[MetricItem] = []
    risks: List[RiskItem] = []
    trends: List[TrendSignal] = []


# ========== Synthesis (Reduce) ==========

class CoverageMetadata(BaseModel):
    """정보 손실 추적용 — 어떤 데이터가 포함/제외됐는지"""
    total_documents: int
    documents_by_team: Dict[str, int] = {}
    week_range: str
    batch_count: int
    excluded_count: int = 0
    excluded_reason: Optional[str] = None


class TeamFindings(BaseModel):
    """팀별 분석 결과"""
    team: str
    headline: str  # 15자 이내, 슬라이드 타이틀용
    key_bullets: List[str]  # 최대 5개, 각 30자 이내
    details: str
    metrics: List[MetricItem] = []
    risks: List[RiskItem] = []
    evidence_refs: List[str] = []


class TopicFindings(BaseModel):
    """주제별 분석 결과"""
    topic: str
    headline: str
    key_bullets: List[str]
    evidence_refs: List[str] = []


class DeepMiningAnalysis(BaseModel):
    """Reduce 노드 최종 출력"""
    executive_summary: str
    findings_by_team: List[TeamFindings]
    findings_by_topic: List[TopicFindings]
    trends: List[TrendSignal] = []
    key_metrics: List[MetricItem] = []
    recommendations: List[str] = []
    coverage: CoverageMetadata
    document_count: int
    week_range: str


# ========== Presentation Planning ==========

class SlideOutline(BaseModel):
    """LLM이 생성하는 슬라이드 개요 — 좌표/레이아웃은 포함하지 않음"""
    title: str
    objective: str
    key_messages: List[str]
    evidence_refs: List[str] = []
    preferred_visual: str = "bullet"  # "bullet" | "table" | "2-column" | "metric-strip"


class PresentationOutline(BaseModel):
    """Reduce 노드에서 함께 생성되는 프레젠테이션 개요"""
    slides: List[SlideOutline]
    appendix_needed: bool = False
    appendix_content: Optional[str] = None

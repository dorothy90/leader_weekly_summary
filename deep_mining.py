"""
Deep Mining LangGraph — 6단 아키텍처 중 1~4단
1. Query Analysis → 2. Exhaustive Retrieve → 3. Evidence Extraction (Map) → 4. Synthesis (Reduce)

rag_api_opensearch_v3.py의 OpenSearchClient, get_llm(), count_tokens(), TEAMS 재사용
"""

import os
import json
import asyncio
import logging
from typing import List, Dict, Optional, Any, TypedDict, Annotated
from pathlib import Path
from collections import defaultdict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from deep_mining_schemas import (
    QueryFilters,
    BatchExtraction,
    Fact,
    MetricItem,
    RiskItem,
    TrendSignal,
    TeamFindings,
    TopicFindings,
    CoverageMetadata,
    DeepMiningAnalysis,
    SlideOutline,
    PresentationOutline,
)
from deep_mining_ppt import build_deep_mining_pptx

logger = logging.getLogger(__name__)


# ========== 설정 ==========
DEEP_MINING_BATCH_SIZE = int(os.getenv("DEEP_MINING_BATCH_SIZE", "20"))
DEEP_MINING_OUTPUT_DIR = Path(os.getenv("DEEP_MINING_OUTPUT_DIR", "exports/deep_mining"))
DEEP_MINING_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _get_deep_mining_llm():
    """Deep Mining용 LLM (structured output 지원 필요)"""
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("OPENROUTER_BASE_URL", ""),
        api_key=os.getenv("OPENROUTER_API_KEY", ""),
        temperature=0,
    )


# ========== Graph State ==========

class DeepMiningState(TypedDict):
    query: str
    filters: Optional[Dict]
    documents: List[Dict]
    batches: List[List[Dict]]
    extractions: List[Dict]
    analysis: Optional[Dict]
    outline: Optional[Dict]
    pptx_path: Optional[str]
    text_summary: Optional[str]
    document_count: int
    num_slides: int
    job_id: str
    progress_callback: Optional[Any]
    # OpenSearch client reference
    os_client: Optional[Any]


# ========== Node 1: Query Analysis ==========

QUERY_ANALYSIS_PROMPT = """사용자의 deep mining 질의를 분석하여 검색 필터를 추출하세요.

사용 가능한 팀: {teams}

질의: {query}

추가 필터 정보:
- 사용자 지정 팀: {user_team}
- 사용자 지정 시작 주차: {user_week_from}
- 사용자 지정 끝 주차: {user_week_to}
- 사용자 지정 메일 유형: {user_mail_type}

JSON으로 응답하세요:
{{
    "teams": ["팀명1", "팀명2"],  // 빈 리스트면 전체 팀
    "week_from": "2025-01" 또는 null,
    "week_to": "2025-20" 또는 null,
    "mail_type": "weekly_report" 또는 null,
    "key_topics": ["관심 주제1", "주제2"]
}}"""


def analyze_query(state: DeepMiningState) -> Dict:
    """질의 파싱 → QueryFilters"""
    from rag_api_opensearch_v3 import TEAMS

    filters = state.get("filters") or {}
    user_team = filters.get("team")
    user_week_from = filters.get("week_from")
    user_week_to = filters.get("week_to")
    user_mail_type = filters.get("mail_type")

    llm = _get_deep_mining_llm()
    prompt = QUERY_ANALYSIS_PROMPT.format(
        teams=", ".join(TEAMS),
        query=state["query"],
        user_team=user_team or "없음",
        user_week_from=user_week_from or "없음",
        user_week_to=user_week_to or "없음",
        user_mail_type=user_mail_type or "없음",
    )

    response = llm.invoke([{"role": "user", "content": prompt}])

    try:
        content = response.content
        # JSON 블록 추출
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        parsed = json.loads(content.strip())
    except (json.JSONDecodeError, IndexError):
        parsed = {}

    # 사용자 명시 필터 우선
    result_filters = {
        "teams": [user_team] if user_team else parsed.get("teams", []),
        "week_from": user_week_from or parsed.get("week_from"),
        "week_to": user_week_to or parsed.get("week_to"),
        "mail_type": user_mail_type or parsed.get("mail_type"),
        "key_topics": parsed.get("key_topics", []),
    }

    logger.info(f"[Query Analysis] filters={result_filters}")

    if state.get("progress_callback"):
        state["progress_callback"](0.15)

    return {"filters": result_filters}


# ========== Node 2: Exhaustive Retrieve ==========

def exhaustive_retrieve(state: DeepMiningState) -> Dict:
    """scroll API로 전체 문서 수집 + 배치 분할"""
    os_client = state["os_client"]
    filters = state.get("filters") or {}

    all_docs = os_client.scroll_search(
        teams=filters.get("teams") or None,
        week_from=filters.get("week_from"),
        week_to=filters.get("week_to"),
        mail_type=filters.get("mail_type"),
    )

    logger.info(f"[Exhaustive Retrieve] {len(all_docs)} documents retrieved")

    # mail_id 기준 그룹핑하여 원본 재구성
    mail_groups = defaultdict(list)
    for doc in all_docs:
        mail_groups[doc["mail_id"]].append(doc)

    # 그룹 내 part_index 순 정렬 후 텍스트 합침
    reconstructed = []
    for mail_id, parts in mail_groups.items():
        parts.sort(key=lambda x: x.get("part_index", 0))
        combined_text = "\n".join(p["text"] for p in parts)
        meta = parts[0]  # 첫 파트에서 메타데이터 가져옴
        reconstructed.append({
            "mail_id": mail_id,
            "text": combined_text,
            "team": meta["team"],
            "week": meta["week"],
            "mail_type": meta.get("mail_type", ""),
        })

    # 배치 분할
    batch_size = DEEP_MINING_BATCH_SIZE
    batches = [
        reconstructed[i:i + batch_size]
        for i in range(0, len(reconstructed), batch_size)
    ]

    logger.info(f"[Exhaustive Retrieve] {len(reconstructed)} mails → {len(batches)} batches")

    if state.get("progress_callback"):
        state["progress_callback"](0.25)

    return {
        "documents": reconstructed,
        "batches": batches,
        "document_count": len(reconstructed),
    }


# ========== Node 3: Evidence Extraction (Map) ==========

EXTRACTION_PROMPT = """다음 문서 배치에서 구조화된 정보를 추출하세요.

## 문서 배치
{documents}

## 추출 항목
각 문서에서 다음을 추출하세요:
1. **facts**: 핵심 사실/주장 (claim, team, week, evidence_ref=mail_id)
2. **metrics**: 수치 데이터 (name, display_value, value, unit, team, week, evidence_ref)
3. **risks**: 리스크/이슈 (description, severity=high/medium/low, team, evidence_ref)
4. **trends**: 트렌드 신호 (description, direction=improving/declining/stable, teams, evidence_refs)

## 주의사항
- 원문에 없는 사실을 만들지 마세요
- 수치는 원문 그대로 추출하세요
- 각 항목에 반드시 evidence_ref(mail_id)를 포함하세요

JSON으로 응답하세요:
{{
    "facts": [...],
    "metrics": [...],
    "risks": [...],
    "trends": [...]
}}"""


def extract_evidence(state: DeepMiningState) -> Dict:
    """배치별 구조화 추출 (Map 단계)"""
    batches = state["batches"]
    llm = _get_deep_mining_llm()
    all_extractions = []
    total_batches = len(batches)

    for i, batch in enumerate(batches):
        # 배치 문서를 텍스트로 포맷
        doc_texts = []
        for doc in batch:
            doc_texts.append(
                f"[mail_id: {doc['mail_id']}] [팀: {doc['team']}] [주차: {doc['week']}]\n{doc['text'][:2000]}"
            )
        documents_text = "\n\n---\n\n".join(doc_texts)

        prompt = EXTRACTION_PROMPT.format(documents=documents_text)

        try:
            response = llm.invoke([{"role": "user", "content": prompt}])
            content = response.content

            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            parsed = json.loads(content.strip())
            all_extractions.append(parsed)
        except Exception as e:
            logger.warning(f"[Extract Evidence] Batch {i+1} failed: {e}")
            all_extractions.append({"facts": [], "metrics": [], "risks": [], "trends": []})

        # 진행률 업데이트
        if state.get("progress_callback"):
            progress = 0.25 + (0.4 * (i + 1) / total_batches)
            state["progress_callback"](progress)

        logger.info(f"[Extract Evidence] Batch {i+1}/{total_batches} done")

    return {"extractions": all_extractions}


# ========== Node 4: Synthesis (Reduce) ==========

SYNTHESIS_PROMPT = """다음은 여러 배치에서 추출된 구조화 데이터를 합산한 결과입니다.

## 전체 Facts ({fact_count}건)
{facts_summary}

## 전체 Metrics ({metric_count}건)
{metrics_summary}

## 전체 Risks ({risk_count}건)
{risks_summary}

## 전체 Trends ({trend_count}건)
{trends_summary}

## 분석 대상 팀: {teams}
## 분석 기간: {week_range}
## 총 문서 수: {document_count}

## 요청사항
위 데이터를 종합하여 다음을 생성하세요:

1. **executive_summary**: 전체를 2~3문장으로 요약
2. **findings_by_team**: 팀별 분석 (각 팀의 headline(15자 이내), key_bullets(최대 5개, 30자 이내), details)
3. **findings_by_topic**: 주제별 분석 (핵심 주제 3~5개)
4. **trends**: 주요 트렌드 (최대 5개)
5. **key_metrics**: 핵심 수치 (최대 10개, display_value 포함)
6. **recommendations**: 권고사항 (최대 5개)
7. **presentation_outline**: {num_slides}장 슬라이드 개요
   - 각 슬라이드: title, objective, key_messages, evidence_refs, preferred_visual(bullet/table/2-column/metric-strip)

## 주의사항
- 추출 데이터에 없는 새로운 사실을 만들지 마세요 (merge only)
- headline은 슬라이드 타이틀용이므로 간결하게
- key_bullets은 슬라이드 불릿용이므로 30자 이내로
- 빈도가 낮지만 severity가 높은 리스크도 포함하세요
- 데이터가 부족한 팀은 "자료 부족" 명시

JSON으로 응답하세요."""


def synthesize(state: DeepMiningState) -> Dict:
    """모든 추출 결과를 종합 → DeepMiningAnalysis + PresentationOutline"""
    extractions = state["extractions"]
    documents = state["documents"]
    num_slides = state["num_slides"]

    # 모든 배치 결과 병합
    all_facts = []
    all_metrics = []
    all_risks = []
    all_trends = []

    for ext in extractions:
        all_facts.extend(ext.get("facts", []))
        all_metrics.extend(ext.get("metrics", []))
        all_risks.extend(ext.get("risks", []))
        all_trends.extend(ext.get("trends", []))

    # 팀별 문서 수 집계
    docs_by_team = defaultdict(int)
    weeks = set()
    for doc in documents:
        docs_by_team[doc["team"]] += 1
        weeks.add(doc["week"])

    sorted_weeks = sorted(weeks) if weeks else []
    week_range = f"{sorted_weeks[0]} ~ {sorted_weeks[-1]}" if sorted_weeks else "N/A"
    teams_in_data = list(docs_by_team.keys())

    # 요약 텍스트 생성 (토큰 절약을 위해 각 카테고리 상위 항목만)
    def _summarize_items(items, max_items=50):
        return json.dumps(items[:max_items], ensure_ascii=False, indent=1)

    llm = _get_deep_mining_llm()
    prompt = SYNTHESIS_PROMPT.format(
        fact_count=len(all_facts),
        facts_summary=_summarize_items(all_facts),
        metric_count=len(all_metrics),
        metrics_summary=_summarize_items(all_metrics),
        risk_count=len(all_risks),
        risks_summary=_summarize_items(all_risks),
        trend_count=len(all_trends),
        trends_summary=_summarize_items(all_trends),
        teams=", ".join(teams_in_data),
        week_range=week_range,
        document_count=len(documents),
        num_slides=num_slides,
    )

    if state.get("progress_callback"):
        state["progress_callback"](0.7)

    response = llm.invoke([{"role": "user", "content": prompt}])
    content = response.content

    try:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        parsed = json.loads(content.strip())
    except (json.JSONDecodeError, IndexError) as e:
        logger.error(f"[Synthesis] JSON parse failed: {e}")
        parsed = {}

    # DeepMiningAnalysis 구성
    coverage = CoverageMetadata(
        total_documents=len(documents),
        documents_by_team=dict(docs_by_team),
        week_range=week_range,
        batch_count=len(extractions),
    )

    # 팀별 findings 파싱
    findings_by_team = []
    for tf_data in parsed.get("findings_by_team", []):
        try:
            metrics = [MetricItem(**m) for m in tf_data.get("metrics", [])] if "metrics" in tf_data else []
            risks = [RiskItem(**r) for r in tf_data.get("risks", [])] if "risks" in tf_data else []
            findings_by_team.append(TeamFindings(
                team=tf_data.get("team", ""),
                headline=tf_data.get("headline", "")[:15],
                key_bullets=[b[:30] for b in tf_data.get("key_bullets", [])[:5]],
                details=tf_data.get("details", ""),
                metrics=metrics,
                risks=risks,
                evidence_refs=tf_data.get("evidence_refs", []),
            ))
        except Exception as e:
            logger.warning(f"[Synthesis] TeamFindings parse error: {e}")

    # 주제별 findings 파싱
    findings_by_topic = []
    for tp_data in parsed.get("findings_by_topic", []):
        try:
            findings_by_topic.append(TopicFindings(
                topic=tp_data.get("topic", ""),
                headline=tp_data.get("headline", ""),
                key_bullets=tp_data.get("key_bullets", [])[:5],
                evidence_refs=tp_data.get("evidence_refs", []),
            ))
        except Exception as e:
            logger.warning(f"[Synthesis] TopicFindings parse error: {e}")

    # 트렌드 파싱
    trends = []
    for t_data in parsed.get("trends", []):
        try:
            trends.append(TrendSignal(
                description=t_data.get("description", ""),
                direction=t_data.get("direction", "stable"),
                teams=t_data.get("teams", []),
                evidence_refs=t_data.get("evidence_refs", []),
            ))
        except Exception as e:
            logger.warning(f"[Synthesis] TrendSignal parse error: {e}")

    # 수치 파싱
    key_metrics = []
    for m_data in parsed.get("key_metrics", []):
        try:
            key_metrics.append(MetricItem(
                name=m_data.get("name", ""),
                display_value=m_data.get("display_value", ""),
                value=m_data.get("value"),
                unit=m_data.get("unit"),
                team=m_data.get("team", ""),
                week=m_data.get("week", ""),
                evidence_ref=m_data.get("evidence_ref", ""),
            ))
        except Exception as e:
            logger.warning(f"[Synthesis] MetricItem parse error: {e}")

    analysis = DeepMiningAnalysis(
        executive_summary=parsed.get("executive_summary", "분석 결과 요약을 생성하지 못했습니다."),
        findings_by_team=findings_by_team,
        findings_by_topic=findings_by_topic,
        trends=trends,
        key_metrics=key_metrics,
        recommendations=parsed.get("recommendations", []),
        coverage=coverage,
        document_count=len(documents),
        week_range=week_range,
    )

    # PresentationOutline 파싱
    outline = None
    outline_data = parsed.get("presentation_outline", [])
    if outline_data:
        try:
            slides = []
            for s in outline_data:
                slides.append(SlideOutline(
                    title=s.get("title", ""),
                    objective=s.get("objective", ""),
                    key_messages=s.get("key_messages", []),
                    evidence_refs=s.get("evidence_refs", []),
                    preferred_visual=s.get("preferred_visual", "bullet"),
                ))
            outline = PresentationOutline(
                slides=slides,
                appendix_needed=len(documents) > 100,
            )
        except Exception as e:
            logger.warning(f"[Synthesis] PresentationOutline parse error: {e}")

    if state.get("progress_callback"):
        state["progress_callback"](0.8)

    return {
        "analysis": analysis.model_dump(),
        "outline": outline.model_dump() if outline else None,
        "text_summary": analysis.executive_summary,
    }


# ========== Node 5: Build PPTX ==========

def build_pptx(state: DeepMiningState) -> Dict:
    """분석 결과 → PPTX 파일 생성"""
    analysis_data = state["analysis"]
    outline_data = state.get("outline")
    num_slides = state["num_slides"]
    job_id = state["job_id"]

    analysis = DeepMiningAnalysis(**analysis_data)
    outline = PresentationOutline(**outline_data) if outline_data else None

    output_path = str(DEEP_MINING_OUTPUT_DIR / f"{job_id}.pptx")

    pptx_path = build_deep_mining_pptx(
        analysis=analysis,
        outline=outline,
        num_slides=num_slides,
        output_path=output_path,
    )

    if state.get("progress_callback"):
        state["progress_callback"](0.95)

    logger.info(f"[Build PPTX] Created: {pptx_path}")
    return {"pptx_path": pptx_path}


# ========== Graph 조립 ==========

def get_deep_mining_graph():
    """Deep Mining StateGraph 생성"""
    graph = StateGraph(DeepMiningState)

    graph.add_node("analyze_query", analyze_query)
    graph.add_node("exhaustive_retrieve", exhaustive_retrieve)
    graph.add_node("extract_evidence", extract_evidence)
    graph.add_node("synthesize", synthesize)
    graph.add_node("build_pptx", build_pptx)

    graph.add_edge(START, "analyze_query")
    graph.add_edge("analyze_query", "exhaustive_retrieve")
    graph.add_edge("exhaustive_retrieve", "extract_evidence")
    graph.add_edge("extract_evidence", "synthesize")
    graph.add_edge("synthesize", "build_pptx")
    graph.add_edge("build_pptx", END)

    return graph.compile()


# ========== 실행 인터페이스 ==========

async def run_deep_mining_pipeline(
    query: str,
    os_client,
    teams: Optional[List[str]] = None,
    week_from: Optional[str] = None,
    week_to: Optional[str] = None,
    mail_type: Optional[str] = None,
    num_slides: int = 4,
    job_id: str = "",
    progress_callback=None,
) -> Dict:
    """Deep Mining 파이프라인 실행 (async wrapper)

    Returns:
        {"text_summary": str, "pptx_path": str, "document_count": int}
    """
    graph = get_deep_mining_graph()

    initial_state = {
        "query": query,
        "filters": {
            "team": teams[0] if teams and len(teams) == 1 else None,
            "teams": teams,
            "week_from": week_from,
            "week_to": week_to,
            "mail_type": mail_type,
        },
        "documents": [],
        "batches": [],
        "extractions": [],
        "analysis": None,
        "outline": None,
        "pptx_path": None,
        "text_summary": None,
        "document_count": 0,
        "num_slides": num_slides,
        "job_id": job_id,
        "progress_callback": progress_callback,
        "os_client": os_client,
    }

    # LangGraph는 동기 실행이므로 executor에서 실행
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: graph.invoke(initial_state))

    return {
        "text_summary": result.get("text_summary", ""),
        "pptx_path": result.get("pptx_path", ""),
        "document_count": result.get("document_count", 0),
    }

"""
RAG Chatbot API Server (OpenSearch 버전) - v3 Hybrid Agentic RAG
- FastAPI 기반 REST API 서버
- OpenSearch 하이브리드 검색 (벡터 + 키워드)
- LangGraph Hybrid Agentic RAG 구조
  - LLM 기반 질문 유형 분류 (search/statistics/general)
  - 검색: OpenSearch 직접 호출
  - 통계: LLM + Tool Binding
  - 일반: 직접 LLM 응답
- MongoDB 기반 멀티턴 대화 히스토리 관리
"""

import os
import re
import json
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Optional, Any, Annotated, Sequence, Literal, TypedDict
from datetime import datetime, timedelta, UTC
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field, field_validator
from opensearchpy import OpenSearch
from openai import OpenAI
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    AIMessage,
    ToolMessage,
)
from langchain_core.prompts import PromptTemplate

# ToolNode는 더 이상 사용하지 않음 (Naive RAG 구조)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig
from motor.motor_asyncio import AsyncIOMotorClient

from dotenv import load_dotenv
import tiktoken

from knowledge_api import router as knowledge_router
from knowledge_web import mount_knowledge_web
from hybrid_rag import (
    MAX_ANSWER_REVISIONS,
    MAX_QUERY_REWRITES,
    MAX_SEARCH_ATTEMPTS,
    MAX_PARALLEL_SEARCH_TASKS,
    AnswerEvaluation,
    ContextualizedTurn,
    ConversationMemory,
    RetrievalGrade,
    RetrievalPlan,
    SearchTask,
    append_trace,
    build_context,
    build_rewritten_query,
    build_search_tasks,
    can_retry_search,
    deduplicate_documents,
    fallback_evaluate_answer,
    fallback_grade_retrieval,
    fallback_retrieval_plan,
    fallback_route,
    make_search_key,
    normalize_result,
    normalize_weeks,
    rerank_documents,
    validate_citations,
    wiki_save_eligible,
)

# OpenSearch 집계 함수 import
from opensearch import (
    count_weekly_reports_by_team as _count_weekly_reports_by_team,
    count_daily_reports_by_team as _count_daily_reports_by_team,
    count_other_mails_by_team as _count_other_mails_by_team,
    get_missing_teams as _get_missing_teams,
    get_mail_type_summary as _get_mail_type_summary,
    get_unique_weeks as _get_unique_weeks,
)

load_dotenv()

# ========== 설정 ==========
# OpenSearch 설정
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "rlaeorka1!K")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true"
INDEX_NAME = os.getenv("OPENSEARCH_INDEX", "weekly_mail")
SECONDARY_INDEX_NAME = os.getenv("OPENSEARCH_SECONDARY_INDEX", "syldgpt")

# 임베딩 설정
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "")
EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"

# LLM 설정
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss-120b")
# LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# MongoDB 설정
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "weekly_mail_agent")
HISTORY_TTL_SECONDS = int(os.getenv("HISTORY_TTL_SECONDS", "1800"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))
MAX_ANSWER_LENGTH = int(os.getenv("MAX_ANSWER_LENGTH", "1000"))

# LLM 히스토리 설정
MAX_LLM_HISTORY_TURNS = int(os.getenv("MAX_LLM_HISTORY_TURNS", "5"))
MULTITURN_V2_ENABLED = os.getenv("MULTITURN_V2_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# 토큰/검색 설정
MAX_TOOL_RESULT_TOKENS = int(os.getenv("MAX_TOOL_RESULT_TOKENS", "150000"))
SEARCH_RESULT_LIMIT = int(os.getenv("SEARCH_RESULT_LIMIT", "50"))
MAX_TEXT_PER_DOC = int(os.getenv("MAX_TEXT_PER_DOC", "2000"))
ENCODING_NAME = "cl100k_base"

RECENCY_BOOST_WEEKS = 4
RECENCY_BOOSTS = [1.5, 1.0, 0.6, 0.3]  # W-0, W-1, W-2, W-3

# Deep Mining 설정
DEEP_MINING_BATCH_SIZE = int(os.getenv("DEEP_MINING_BATCH_SIZE", "20"))
DEEP_MINING_SCROLL_SIZE = int(os.getenv("DEEP_MINING_SCROLL_SIZE", "200"))
DEEP_MINING_OUTPUT_DIR = Path(os.getenv("DEEP_MINING_OUTPUT_DIR", "exports/deep_mining"))

def _get_recency_boost_clauses() -> list:
    """최근 N주에 대한 term boost should 절 생성."""
    today = datetime.now()
    clauses = []
    for i in range(RECENCY_BOOST_WEEKS):
        dt = today - timedelta(weeks=i)
        iso_year, iso_week, _ = dt.isocalendar()
        week_str = f"{iso_year}-{iso_week:02d}"
        clauses.append({"term": {"week": {"value": week_str, "boost": RECENCY_BOOSTS[i]}}})
    return clauses

# LangGraph 설정
GRAPH_RECURSION_LIMIT = int(os.getenv("GRAPH_RECURSION_LIMIT", "15"))

# 서버 설정
HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "8002"))
API_BASE_URL = os.getenv("API_BASE_URL", f"http://localhost:{PORT}")

# 디렉토리
MAIL_DIR = Path("mail")

# 팀 목록
TEAMS = [
    "CS팀",
    "DT팀",
    "EQUIP팀",
    "FA팀",
    "PE팀",
    "PI팀",
    "PROCESS팀",
    "QA팀",
    "TEST팀",
    "YIELD팀",
]


# ========== Pydantic 스키마 ==========
class Reference(BaseModel):
    """참조 출처"""

    team: str
    week: str
    mail_id: str
    url: str
    score: float
    part_index: Optional[int] = None
    total_parts: Optional[int] = None


class ChatV2Request(BaseModel):
    """채팅 요청"""

    user_id: str
    message: str
    conversation_id: Optional[str] = Field(default=None, description="대화 세션 ID")
    team: Optional[str] = Field(default=None, description="팀 필터")
    week: Optional[str] = Field(default=None, description="주차 필터 (예: 2025-48)")

    @field_validator("week")
    @classmethod
    def validate_week(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        match = re.fullmatch(r"\s*(\d{4})-(\d{1,2})\s*", value)
        if not match or not 1 <= int(match.group(2)) <= 53:
            raise ValueError("week는 YYYY-WW 형식의 1~53주차여야 합니다")
        return f"{match.group(1)}-{int(match.group(2)):02d}"


class ToolCallInfo(BaseModel):
    """Tool 호출 정보"""

    name: str
    arguments: Dict[str, Any]


class ToolResultInfo(BaseModel):
    """Tool 실행 결과"""

    name: str
    result: Any


class ChatV2Response(BaseModel):
    """채팅 응답"""

    conversation_id: str
    answer: str
    tool_calls: List[ToolCallInfo]
    tool_results: List[ToolResultInfo]
    references: List[Reference] = Field(default=[], description="참조 출처 목록")


# ========== 유틸리티 ==========
def count_tokens(text: str) -> int:
    """텍스트의 토큰 수 계산"""
    try:
        encoding = tiktoken.get_encoding(ENCODING_NAME)
        return len(encoding.encode(text))
    except Exception:
        return len(text) // 4


# ========== OpenSearch 클라이언트 ==========
class OpenSearchClient:
    """OpenSearch 클라이언트"""

    def __init__(self):
        self.client = OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
            use_ssl=OPENSEARCH_USE_SSL,
            verify_certs=False,
            ssl_show_warn=False,
        )
        self.embedding_client = OpenAI(
            api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL
        )
        self.index_name = INDEX_NAME
        self.secondary_index_name = SECONDARY_INDEX_NAME
        self.wiki_index_name = "wiki_summaries"

    def _get_embedding(self, text: str) -> List[float]:
        """텍스트를 임베딩 벡터로 변환"""
        text = text[:8000] if len(text) > 8000 else text
        response = self.embedding_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
        )
        return response.data[0].embedding

    def search(
        self,
        query: str,
        team: Optional[str] = None,
        week=None,
        mail_type: Optional[str] = None,
        limit: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict]:
        """하이브리드 검색

        Args:
            week: 주차 필터 - str("2025-30"), List[str](["2025-5","2025-6"]), 또는 None(전체)
            mail_type: 메일 유형 필터 ("weekly_report" / "daily_report" / None=전체)
        """
        # 가중치 정규화
        total = vector_weight + keyword_weight
        if total > 0:
            vector_weight, keyword_weight = (
                vector_weight / total,
                keyword_weight / total,
            )
        else:
            vector_weight, keyword_weight = 0.7, 0.3

        query_embedding = self._get_embedding(query)

        filters = []
        if team:
            filters.append({"term": {"team": team}})
        if week:
            if isinstance(week, list):
                filters.append({"terms": {"week": week}})  # 복수 주차 필터
            else:
                filters.append({"term": {"week": week}})   # 단일 주차 필터 (하위호환)
        if mail_type:
            filters.append({"term": {"mail_type": mail_type}})

        knn_boost = vector_weight * 10
        bm25_boost = keyword_weight

        should_clauses = [
            {
                "knn": {
                    "embedding": {
                        "vector": query_embedding,
                        "k": limit,
                        "boost": knn_boost,
                    }
                }
            },
            {
                "match": {
                    "text": {
                        "query": query,
                        "analyzer": "korean",
                        "boost": bm25_boost,
                    }
                }
            },
        ]

        # 주차 필터 없을 때만 recency boost (최근 4주)
        if not week:
            should_clauses.extend(_get_recency_boost_clauses())

        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "should": should_clauses,
                    "filter": filters if filters else [],
                    "minimum_should_match": 1,
                }
            },
        }

        response = self.client.search(index=self.index_name, body=search_body)

        results = []
        for hit in response["hits"]["hits"]:
            source = hit["_source"]
            results.append(
                {
                    "score": hit["_score"],
                    "text": source.get("text", ""),
                    "team": source.get("team", "unknown"),
                    "week": source.get("week", "unknown"),
                    "mail_id": source.get("mail_id", "unknown"),
                    "html_path": source.get("html_path", ""),
                    "part_index": source.get("part_index"),
                    "total_parts": source.get("total_parts"),
                }
            )
        return results

    def search_secondary(
        self,
        query: str,
        limit: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict]:
        """syldgpt 인덱스 하이브리드 검색 (배경지식용)"""
        total = vector_weight + keyword_weight
        if total > 0:
            vector_weight, keyword_weight = vector_weight / total, keyword_weight / total
        else:
            vector_weight, keyword_weight = 0.7, 0.3

        query_embedding = self._get_embedding(query)

        knn_boost = vector_weight * 10
        bm25_boost = keyword_weight

        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "should": [
                        {
                            "knn": {
                                "embedding": {
                                    "vector": query_embedding,
                                    "k": limit,
                                    "boost": knn_boost,
                                }
                            }
                        },
                        {
                            "match": {
                                "page_content": {
                                    "query": query,
                                    "boost": bm25_boost,
                                }
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
        }

        response = self.client.search(index=self.secondary_index_name, body=search_body)

        results = []
        for hit in response["hits"]["hits"]:
            source = hit["_source"]
            results.append(
                {
                    "score": hit["_score"],
                    "text": source.get("page_content", ""),
                }
            )
        return results

    def search_wiki(
        self,
        query: str,
        team: Optional[str] = None,
        week=None,
        summary_type: Optional[str] = None,
        limit: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict]:
        """wiki_summaries 인덱스 하이브리드 검색"""
        # 인덱스 존재 확인
        if not self.client.indices.exists(index=self.wiki_index_name):
            return []

        total = vector_weight + keyword_weight
        if total > 0:
            vector_weight, keyword_weight = vector_weight / total, keyword_weight / total
        else:
            vector_weight, keyword_weight = 0.7, 0.3

        query_embedding = self._get_embedding(query)

        filters = []
        if team:
            filters.append({"term": {"team": team}})
        if week:
            if isinstance(week, list):
                filters.append({"terms": {"week": week}})
            else:
                filters.append({"term": {"week": week}})
        if summary_type:
            filters.append({"term": {"summary_type": summary_type}})

        knn_boost = vector_weight * 10
        bm25_boost = keyword_weight

        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "should": [
                        {
                            "knn": {
                                "embedding": {
                                    "vector": query_embedding,
                                    "k": limit,
                                    "boost": knn_boost,
                                }
                            }
                        },
                        {
                            "match": {
                                "text": {
                                    "query": query,
                                    "analyzer": "korean",
                                    "boost": bm25_boost,
                                }
                            }
                        },
                    ],
                    "filter": filters if filters else [],
                    "minimum_should_match": 1,
                }
            },
        }

        response = self.client.search(index=self.wiki_index_name, body=search_body)

        results = []
        for hit in response["hits"]["hits"]:
            source = hit["_source"]
            results.append({
                "score": hit["_score"],
                "text": source.get("text", ""),
                "title": source.get("title", ""),
                "summary_type": source.get("summary_type", ""),
                "team": source.get("team"),
                "week": source.get("week"),
                "topic": source.get("topic"),
            })
        return results

    def get_stats(self) -> Dict:
        try:
            count = self.client.count(index=self.index_name)
            return {"total_documents": count["count"], "index": self.index_name}
        except Exception as e:
            return {"error": str(e)}

    def health_check(self) -> Dict:
        try:
            info = self.client.info()
            return {
                "status": "ok",
                "distribution": info["version"]["distribution"],
                "version": info["version"]["number"],
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def scroll_search(
        self,
        teams: Optional[List[str]] = None,
        week_from: Optional[str] = None,
        week_to: Optional[str] = None,
        mail_type: Optional[str] = None,
        scroll_size: int = DEEP_MINING_SCROLL_SIZE,
    ) -> List[Dict]:
        """OpenSearch scroll API로 전체 문서 순회 (Deep Mining용)

        Args:
            teams: 팀 필터 리스트
            week_from: 주차 범위 시작 (e.g. "2025-01")
            week_to: 주차 범위 끝 (e.g. "2025-20")
            mail_type: 메일 유형 필터
            scroll_size: 한 번에 가져올 문서 수

        Returns:
            전체 문서 리스트 (embedding 제외)
        """
        filters = []
        if teams:
            filters.append({"terms": {"team": teams}})
        if week_from or week_to:
            range_filter = {}
            if week_from:
                range_filter["gte"] = week_from
            if week_to:
                range_filter["lte"] = week_to
            filters.append({"range": {"week": range_filter}})
        if mail_type:
            filters.append({"term": {"mail_type": mail_type}})

        search_body = {
            "size": scroll_size,
            "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
            "sort": [{"mail_id": "asc"}, {"part_index": "asc"}],
            "_source": {"excludes": ["embedding"]},
        }

        all_docs = []
        response = self.client.search(
            index=self.index_name, body=search_body, scroll="2m"
        )
        scroll_id = response.get("_scroll_id")
        hits = response["hits"]["hits"]

        while hits:
            for hit in hits:
                source = hit["_source"]
                all_docs.append({
                    "text": source.get("text", ""),
                    "team": source.get("team", "unknown"),
                    "week": source.get("week", "unknown"),
                    "mail_id": source.get("mail_id", "unknown"),
                    "mail_type": source.get("mail_type", ""),
                    "part_index": source.get("part_index", 0),
                    "total_parts": source.get("total_parts", 1),
                    "html_path": source.get("html_path", ""),
                })

            response = self.client.scroll(scroll_id=scroll_id, scroll="2m")
            scroll_id = response.get("_scroll_id")
            hits = response["hits"]["hits"]

        # scroll 컨텍스트 정리
        if scroll_id:
            try:
                self.client.clear_scroll(scroll_id=scroll_id)
            except Exception:
                pass

        return all_docs


# ========== LLM 클라이언트 ==========
def get_llm():
    """LLM 클라이언트"""
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        temperature=0,
        default_headers={
            "HTTP-Referer": "https://weekly-mail-agent.local",
            "X-Title": "Weekly Mail RAG Chatbot",
        },
    )


# ========== LangGraph Tools ==========
@tool
def count_weekly_reports_by_team(week: Optional[str] = None) -> str:
    """주간보고 메일의 팀별 count를 조회합니다.

    Args:
        week: 주차 필터 (예: 2025-48). 미지정시 전체 기간 조회
    """
    try:
        result = _count_weekly_reports_by_team(week=week)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def count_daily_reports_by_team(week: Optional[str] = None) -> str:
    """일일보고 메일의 팀별 count를 조회합니다.

    Args:
        week: 주차 필터 (예: 2025-48). 미지정시 전체 기간 조회
    """
    try:
        result = _count_daily_reports_by_team(week=week)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def count_other_mails_by_team(
    week: Optional[str] = None, team: Optional[str] = None
) -> str:
    """주간보고 외 메일의 팀별 count를 조회합니다.

    Args:
        week: 주차 필터 (예: 2025-48)
        team: 특정 팀 필터 (예: YIELD팀)
    """
    try:
        result = _count_other_mails_by_team(week=week, team=team)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_missing_teams(week: str) -> str:
    """주간보고 미제출 팀 목록을 조회합니다.

    Args:
        week: 주차 (필수, 예: 2025-48)
    """
    try:
        result = _get_missing_teams(week=week)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_mail_type_summary(week: Optional[str] = None) -> str:
    """주간보고와 일반 메일의 전체 요약 통계를 조회합니다.

    Args:
        week: 주차 필터. 미지정시 전체 기간 조회
    """
    try:
        result = _get_mail_type_summary(week=week)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_available_weeks() -> str:
    """데이터가 있는 주차 목록을 조회합니다."""
    try:
        result = _get_unique_weeks()
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ========== 통계 Tool 리스트 (statistics 노드용) ==========
STATISTICS_TOOLS = [
    count_weekly_reports_by_team,
    count_daily_reports_by_team,
    count_other_mails_by_team,
    get_missing_teams,
    get_mail_type_summary,
    get_available_weeks,
]


# ========== Naive RAG + LLM Router State & Graph ==========
class GraphState(TypedDict, total=False):
    """Hybrid Agentic RAG 상태"""

    question: str  # 사용자 질문
    standalone_question: str
    search_query: str  # 컨텍스트 반영된 독립 검색 쿼리 (Router가 생성)
    conversation_memory: Dict[str, Any]
    contextualized_turn: Dict[str, Any]
    contextualized_teams: List[str]
    follow_up_type: str
    context: str  # 검색/통계 결과 (컨텍스트)
    answer: str  # 최종 답변
    draft_answer: str
    final_answer: str
    messages: Annotated[list, add_messages]  # 대화 히스토리
    route: str  # 라우팅 결과 (search/statistics/general)
    team: Optional[str]  # API에서 전달된 명시적 팀 필터
    mail_type: Optional[str]  # 메일 유형 필터 (weekly_report/daily_report/None)
    week: Optional[List[str]]  # 주차 필터 (단일 ["2025-30"] 또는 복수 ["2025-5","2025-6","2025-7"])
    start_week: Optional[str]
    end_week: Optional[str]
    retrieval_error: Optional[str]
    retrieval_results: List[Dict[str, Any]]
    reranked_results: List[Dict[str, Any]]
    retrieval_grade: str
    retrieval_score: float
    missing_information: List[str]
    search_attempts: int
    rewrite_count: int
    rewritten_query: Optional[str]
    sub_questions: List[str]
    selected_sources: List[str]
    retrieval_plan: Dict[str, Any]
    executed_search_keys: List[str]
    limited_answer: bool
    wiki_duplicate: Optional[bool]
    groundedness_score: float
    completeness_score: float
    citation_valid: bool
    unsupported_claims: List[str]
    missing_answers: List[str]
    answer_evaluation: Dict[str, Any]
    answer_revision_count: int
    trace: List[Dict[str, Any]]


class RouteDecision(BaseModel):
    """라우터 분류 결과"""

    route: Literal["search", "statistics", "general"] = Field(
        description="질문 유형: search(내용 검색), statistics(통계 조회), general(일반 대화)"
    )
    reason: str = Field(description="분류 이유 (디버깅용)")


# 라우터 프롬프트
ROUTER_PROMPT = """사용자 질문을 분류하세요.

## 분류 기준
- **search**: 메일 내용 검색이 필요한 질문
  - 예: 수율 이슈, 문제점, 진행상황, 개선사항, 분석 결과, 특정 팀의 업무 내용
  - 키워드: 이슈, 문제, 분석, 개선, 진행, 내용, 알려줘, 뭐야, 어때

- **statistics**: 통계/수치 조회가 필요한 질문
  - 예: 주간보고 제출 수, 미제출 팀, 팀별 보고서 수, 전체 현황
  - 키워드: 몇 개, 몇 건, 제출, 미제출, 통계, 현황, 수

- **general**: 일반 대화 (검색이나 통계가 필요 없음)
  - 예: 인사, 감사, 도움말 요청, 시스템 설명
  - 키워드: 안녕, 고마워, 뭐해, 도움말, 사용법

질문: {question}

JSON 형식으로 답변하세요."""


# 답변 생성 프롬프트
ANSWER_SYSTEM_PROMPT = """당신은 반도체 주간 업무 보고서 시스템의 AI 어시스턴트입니다.

## 핵심 원칙 (반드시 준수)
1. **오직** 제공된 컨텍스트(context)의 내용만 사용하여 답변하세요.
2. 컨텍스트에 없는 내용은 **절대** 추측하거나 생성하지 마세요.
3. 질문에 대한 답이 컨텍스트에 없으면 "제공된 문서에서 해당 정보를 찾을 수 없습니다"라고 명확히 답변하세요.
4. 숫자, 팀명, 날짜 등은 컨텍스트에 있는 그대로 정확히 인용하세요.

## 컨텍스트 구성
- 각 근거는 출처 종류와 관계없이 [S1], [S2]처럼 안정적인 ID로 표기됩니다.
- Wiki는 전체 맥락, 메일은 상세 사실과 수치, 기술 문서는 기술적 배경 설명에 활용하세요.

## 답변 작성 방법
- Wiki 요약으로 전체 맥락을 먼저 파악하고, 메일 원문으로 상세 내용을 보충하세요.
- 기술 문서는 메일 내용의 기술적 맥락을 보충하거나 용어와 공정을 설명할 때 활용하세요.
- 세 출처를 자연스럽게 결합하여 답변하되, 어느 출처에서 왔는지 구분 가능하도록 표기하세요.

## 금지 사항
- 컨텍스트에 명시되지 않은 정보를 답변에 포함하지 마세요.
- 일반 지식, 추측, 유추를 섞지 마세요.
- 불확실한 내용을 확정적으로 말하지 마세요.
- 답을 모르면 솔직히 "해당 정보가 문서에 없습니다"라고 하세요.

## 출처 표시 규칙
- 모든 인용은 제공된 근거 ID와 일치하는 [S#] 형식으로 표시하세요.
- 출처 태그는 **실제로 해당 문서에서 직접 가져온 내용에만** 붙이세요.
- 출처가 불명확하거나 여러 문서를 종합한 내용은 출처를 붙이지 마세요.
- 컨텍스트에 없는 ID를 만들지 마세요.

## 답변 형식 (필수 준수)
- 간결하고 명확하게 작성하세요.
- 핵심 정보를 먼저 제시하고, 세부 내용을 이어서 설명하세요.

## 서식 규칙 (반드시 지켜야 함)
- 마크다운 문법을 사용하지 마세요: #헤딩, ```코드블록```, |표|, [링크](url), *기울임* 금지
- HTML 태그도 사용하지 마세요.
- 아래 서식만 허용됩니다:

### 사용 가능한 서식
- 굵게: **텍스트** (핵심 키워드, 팀명 강조에 사용)
- 섹션 제목: (( 텍스트 )) (파란색 섹션 제목에 사용, 반드시 (( 와 텍스트 사이에 띄어쓰기)
- 목록: • 또는 1. 2. 3.
- 하위 항목: 들여쓰기 + -

### 제목/소제목 형식
- 섹션 제목은 반드시 (( 텍스트 )) 형태로 별도의 줄에 작성하세요.
- 제목 앞에 •(불릿)을 붙이지 마세요.
- 제목 위에는 빈 줄을 하나 넣어 구분하세요.

### 기타
- 표(table)는 사용하지 말고 반드시 개조식(•)으로 작성하세요.
- 코드나 기술 용어는 따옴표로 감싸세요: "defect_count"
- 구분선(────)은 남용하지 마세요.

## 대화 스타일
- 자연스럽고 친근한 어조로 답변하세요.
- 이전 대화 내용이 있으면 자연스럽게 연결하세요.
  예: "앞서 말씀하신 XX 이슈와 관련해서..."
  예: "이전에 확인한 YY팀 현황에 이어서..."
- 답변 마지막에 관련된 후속 탐색을 1~2개 제안하세요.
  예: "다른 팀의 유사한 이슈도 확인해볼까요?"
  예: "최근 몇 주간의 추이도 살펴볼 수 있습니다."
- 단, 후속 제안은 검색 결과가 있을 때만 하세요. 일반 대화에서는 불필요합니다."""


# ===== 유틸리티: 주차 정보 =====
def _get_week_info() -> str:
    """현재 날짜 기반 주차 정보 문자열 생성 (Router 프롬프트에 주입)"""
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    current_week = f"{iso_year}-{iso_week:02d}"
    if iso_week > 1:
        prev_week = f"{iso_year}-{(iso_week - 1):02d}"
    else:
        prev_week = f"{iso_year - 1}-52"
    next_week = f"{iso_year}-{(iso_week + 1):02d}"

    # 최근 N주 리스트 계산 (현재 주 포함, 과거 방향)
    recent_weeks = []
    for i in range(4):
        w = iso_week - i
        y = iso_year
        if w < 1:
            w += 52
            y -= 1
        recent_weeks.append(f"{y}-{w:02d}")

    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    return (
        f"- 오늘 날짜: {now.strftime('%Y-%m-%d')} ({weekday_names[now.weekday()]}요일)\n"
        f"- 이번주 = {current_week}\n"
        f"- 저번주/지난주 = {prev_week}\n"
        f"- 다음주 = {next_week}\n"
        f"- 최근 2주 = {','.join(recent_weeks[:2])}\n"
        f"- 최근 3주 = {','.join(recent_weeks[:3])}\n"
        f"- 최근 4주 = {','.join(recent_weeks[:4])}"
    )


# ===== 노드 함수들 =====
def _explicit_turn_teams(text: str) -> list[str]:
    return [team for team in TEAMS if team in text]


def _explicit_turn_weeks(text: str) -> list[str] | None:
    return normalize_weeks(re.findall(r"\d{4}-\d{1,2}", text))


def _explicit_turn_mail_type(text: str) -> Optional[str]:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("데일리", "일일", "daily")):
        return "daily_report"
    if any(keyword in lowered for keyword in ("주간", "주보", "weekly")):
        return "weekly_report"
    return None


def _is_meaningful_search_context(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False
    if _explicit_turn_teams(normalized) or _explicit_turn_weeks(normalized):
        return True
    technical_keywords = (
        "수율",
        "이슈",
        "defect",
        "particle",
        "공정",
        "장비",
        "ald",
        "cvd",
        "etch",
        "cleaning",
        "pulsed",
        "misalign",
    )
    return any(keyword in normalized for keyword in technical_keywords)


def _build_comparison_question(prior_question: str, teams: list[str]) -> str:
    topic = prior_question
    for team in _explicit_turn_teams(prior_question):
        topic = topic.replace(team, " ")
    topic = re.sub(r"(비교해줘|비교|알려줘|설명해줘)", " ", topic)
    topic = " ".join(topic.split()) or "이슈"
    return f"{' '.join(teams)} {topic} 비교"


def fallback_contextualized_turn(
    question: str,
    memory: ConversationMemory,
    messages: list[BaseMessage],
) -> ContextualizedTurn:
    """Resolve common follow-ups without relying on an LLM."""
    normalized = " ".join(question.strip().split())
    explicit_teams = _explicit_turn_teams(normalized)
    explicit_weeks = _explicit_turn_weeks(normalized) or []
    explicit_mail_type = _explicit_turn_mail_type(normalized)

    last_human = next(
        (
            str(message.content)
            for message in reversed(messages)
            if isinstance(message, HumanMessage)
            and _is_meaningful_search_context(str(message.content))
        ),
        "",
    )
    history_teams = _explicit_turn_teams(last_human)
    history_weeks = _explicit_turn_weeks(last_human) or []
    prior_question = (
        memory.standalone_question
        or memory.search_query
        or memory.active_topic
        or last_human
    )
    has_prior_context = bool(prior_question)

    if re.search(r"(근거|출처|인용|citation)", normalized, re.IGNORECASE):
        follow_up_type = "evidence"
    elif re.search(r"(비교|차이|반면)", normalized):
        follow_up_type = "compare" if has_prior_context else "new_topic"
    elif re.search(r"(더 자세|좀 더|다시|구체적)", normalized):
        follow_up_type = "refine"
    elif re.fullmatch(r"(계속|이어서|계속해줘)[.!? ]*", normalized):
        follow_up_type = "continue"
    elif re.search(r"(다른 팀|추가로|그 외)", normalized):
        follow_up_type = "expand"
    elif re.search(r"(그|해당|방금|앞서|첫 번째|두 번째)", normalized) and len(normalized) <= 40:
        follow_up_type = "continue"
    else:
        follow_up_type = "new_topic"

    referential = follow_up_type != "new_topic"
    if referential and not has_prior_context and not explicit_teams and not explicit_weeks:
        return ContextualizedTurn(
            active_topic=None,
            standalone_question=normalized,
            search_query=normalized,
            route="clarify",
            follow_up_type="clarify",
            teams=[],
            weeks=[],
            clarification_answer="어느 팀이나 이전 이슈를 말씀하시는지 구체적으로 알려주세요.",
        )

    prior_teams = memory.teams or history_teams
    if follow_up_type == "compare" and explicit_teams:
        teams = list(dict.fromkeys([*prior_teams, *explicit_teams]))
    else:
        teams = explicit_teams or (prior_teams if referential else [])

    if follow_up_type == "compare" and prior_question and len(teams) >= 2:
        standalone_question = _build_comparison_question(prior_question, teams)
        search_query = standalone_question
    elif referential and prior_question:
        standalone_question = prior_question
        search_query = memory.search_query or prior_question
    else:
        standalone_question = normalized
        search_query = normalized

    weeks = explicit_weeks or (
        memory.weeks or history_weeks if referential else []
    )
    mail_type = explicit_mail_type or (memory.mail_type if referential else None)
    route = memory.route if referential and memory.route else fallback_route(standalone_question)

    return ContextualizedTurn(
        active_topic=(memory.active_topic if referential else normalized),
        standalone_question=standalone_question,
        search_query=search_query,
        route=route,
        follow_up_type=follow_up_type,
        teams=teams,
        weeks=weeks,
        mail_type=mail_type,
        rolling_summary=memory.rolling_summary,
        cited_evidence=memory.cited_evidence,
    )


def contextualize_turn_node(state: GraphState) -> Dict[str, Any]:
    """Turn a raw follow-up into a validated, independently searchable turn."""
    import time as _time

    started = _time.time()
    question = str(state.get("question", "")).strip()
    try:
        memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
    except Exception:
        memory = ConversationMemory()
    messages = [
        message
        for message in state.get("messages", [])
        if isinstance(message, (HumanMessage, AIMessage))
    ][-(MAX_LLM_HISTORY_TURNS * 2) :]
    fallback = fallback_contextualized_turn(question, memory, messages)
    turn = fallback
    fallback_reason = None

    history_text = "\n".join(
        f"{'user' if isinstance(message, HumanMessage) else 'assistant'}: {str(message.content)[:300]}"
        for message in messages
    )
    prompt = f"""Contextualize this conversation turn. Return JSON only matching this schema:
{{
  "schema_version": 1,
  "active_topic": "string or null",
  "standalone_question": "independent user question",
  "search_query": "bounded search query",
  "route": "general|statistics|search|clarify",
  "follow_up_type": "new_topic|refine|compare|expand|evidence|continue|clarify",
  "teams": ["team"],
  "weeks": ["YYYY-WW"],
  "mail_type": "daily_report|weekly_report|null",
  "rolling_summary": "short summary",
  "cited_evidence": [],
  "clarification_answer": "string or null"
}}
Do not guess unresolved pronouns. A genuinely new topic must not inherit old filters.
Memory: {memory.model_dump_json()}
Recent history:
{history_text or '(none)'}
Current raw question: {question}
"""
    try:
        response = get_llm().invoke([HumanMessage(content=prompt)])
        content = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", str(response.content).strip()
        )
        turn = ContextualizedTurn.model_validate_json(content)
    except Exception as error:
        fallback_reason = f"{type(error).__name__}: {error}"

    utterance_teams = _explicit_turn_teams(question)
    utterance_weeks = _explicit_turn_weeks(question)
    utterance_mail_type = _explicit_turn_mail_type(question)
    has_prior_context = bool(
        memory.teams
        or memory.weeks
        or _is_meaningful_search_context(memory.active_topic or "")
        or _is_meaningful_search_context(memory.standalone_question or "")
        or _is_meaningful_search_context(memory.search_query or "")
        or any(
            isinstance(message, HumanMessage)
            and _is_meaningful_search_context(str(message.content))
            for message in messages
        )
    )
    unresolved_reference = bool(
        re.search(r"(그|해당|방금|앞서|첫 번째|두 번째)", question)
        and len(question.strip()) <= 40
        and not has_prior_context
        and not utterance_teams
        and not utterance_weeks
    )
    if unresolved_reference:
        turn = fallback

    inherits_memory = turn.follow_up_type not in {"new_topic", "clarify"}
    if state.get("team"):
        resolved_teams = [state["team"]]
    elif turn.follow_up_type == "compare" and utterance_teams:
        resolved_teams = list(
            dict.fromkeys([*memory.teams, *turn.teams, *utterance_teams])
        )
    else:
        resolved_teams = utterance_teams or (memory.teams if inherits_memory else [])
    resolved_weeks = (
        normalize_weeks(state.get("week"))
        or utterance_weeks
        or (memory.weeks if inherits_memory else [])
    )
    resolved_mail_type = (
        state.get("mail_type")
        or utterance_mail_type
        or (memory.mail_type if inherits_memory else None)
    )
    turn_updates = {
        **turn.model_dump(),
            "teams": resolved_teams,
            "weeks": resolved_weeks or [],
            "mail_type": resolved_mail_type,
    }
    if turn.follow_up_type == "compare" and len(resolved_teams) >= 2:
        prior_question = (
            memory.standalone_question
            or memory.search_query
            or memory.active_topic
            or turn.standalone_question
        )
        comparison_question = _build_comparison_question(
            prior_question, resolved_teams
        )
        turn_updates["standalone_question"] = comparison_question
        turn_updates["search_query"] = comparison_question
    turn = ContextualizedTurn.model_validate(turn_updates)
    team_filter = resolved_teams[0] if len(resolved_teams) == 1 else None
    trace = append_trace(
        state.get("trace", []),
        "turn_contextualized",
        follow_up_type=turn.follow_up_type,
        route=turn.route,
        standalone_question=turn.standalone_question,
        teams=resolved_teams,
        weeks=resolved_weeks or [],
        mail_type=resolved_mail_type,
        fallback_reason=fallback_reason,
        elapsed_ms=round((_time.time() - started) * 1000, 1),
    )
    updates: Dict[str, Any] = {
        "standalone_question": turn.standalone_question,
        "search_query": turn.search_query,
        "route": turn.route,
        "follow_up_type": turn.follow_up_type,
        "contextualized_teams": resolved_teams,
        "contextualized_turn": turn.model_dump(),
        "team": team_filter,
        "week": resolved_weeks or None,
        "mail_type": resolved_mail_type,
        "trace": trace,
    }
    if turn.follow_up_type == "clarify":
        updates["answer"] = turn.clarification_answer
        updates["draft_answer"] = turn.clarification_answer
    return updates


def route_after_contextualization(
    state: GraphState,
) -> Literal["router", "restore_prior_evidence", "finalize"]:
    if state.get("follow_up_type") == "clarify":
        return "finalize"
    if state.get("follow_up_type") == "evidence":
        try:
            memory = ConversationMemory.model_validate(
                state.get("conversation_memory") or {}
            )
        except Exception:
            memory = ConversationMemory()
        if memory.cited_evidence:
            return "restore_prior_evidence"
    return "router"


def restore_prior_evidence_node(state: GraphState) -> Dict[str, Any]:
    """Restore bounded, previously cited evidence with fresh citation IDs."""
    try:
        memory = ConversationMemory.model_validate(
            state.get("conversation_memory") or {}
        )
    except Exception:
        memory = ConversationMemory()

    restored_documents = []
    for item in memory.cited_evidence:
        source_metadata = {
            key: value
            for key, value in {
                "mail_id": item.mail_id,
                "url": item.url,
                "html_path": item.html_path,
                "part_index": item.part_index,
                "total_parts": item.total_parts,
            }.items()
            if value is not None
        }
        restored_documents.append(
            {
            "document_id": item.document_id,
            "source_type": item.source_type,
            "title": item.title,
            "content": item.snippet,
            "team": item.team,
            "week": item.week,
            "original_score": 0.0,
            "rerank_score": 0.0,
            "metadata": {
                "parent_id": item.document_id,
                "restored_from_conversation_memory": True,
                **source_metadata,
            },
            }
        )
    context, selected = build_context(restored_documents)
    trace = append_trace(
        state.get("trace", []),
        "prior_evidence_restored",
        document_ids=[document["document_id"] for document in selected],
        result_count=len(selected),
    )
    return {
        "route": "search",
        "context": context,
        "retrieval_results": selected,
        "reranked_results": selected,
        "selected_sources": list(
            dict.fromkeys(document["source_type"] for document in selected)
        ),
        "retrieval_grade": "sufficient",
        "retrieval_score": 1.0,
        "retrieval_error": None,
        "trace": trace,
    }


def router_node(state: GraphState) -> Dict[str, Any]:
    """Router 노드: LLM이 질문 유형(route) + 메일 유형(mail_type) 동시 판단"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    routing_question = state.get("standalone_question") or question
    history_messages = state.get("messages", [])
    explicit_weeks = normalize_weeks(state.get("week"))
    trace = state.get("trace", [])
    print(f"🔀 [Router] 질문 분류 시작: {question[:50]}...")

    # 최근 히스토리를 문자열로 변환 (최근 2~3턴만)
    history_text = ""
    conversation_only = [
        msg for msg in history_messages if isinstance(msg, (HumanMessage, AIMessage))
    ]
    recent_history = conversation_only[-(MAX_LLM_HISTORY_TURNS * 2) :]

    if recent_history:
        history_lines = []
        for msg in recent_history:
            if isinstance(msg, HumanMessage):
                history_lines.append(f"사용자: {msg.content}")
            elif isinstance(msg, AIMessage):
                # 답변은 길 수 있으므로 앞부분만
                content = (
                    msg.content[:200] + "..." if len(msg.content) > 200 else msg.content
                )
                history_lines.append(f"어시스턴트: {content}")
        history_text = "\n".join(history_lines)
        print(f"🔀 [Router] 히스토리 {len(recent_history)}개 메시지 포함")

    # 히스토리 섹션 구성
    history_section = (
        f"\n## 이전 대화 히스토리\n{history_text}\n" if history_text else ""
    )

    # 현재 날짜/주차 정보 생성
    week_info = _get_week_info()

    # LLM이 route, mail_type, week, search_query를 함께 판단
    simple_prompt = f"""사용자 질문을 분석하세요.

## 현재 날짜 정보
{week_info}

## 출력 형식 (반드시 이 형식으로 네 줄 출력)
route: [search/statistics/general]
mail_type: [daily/weekly/all]
week: [YYYY-WW 또는 YYYY-WW,YYYY-WW,... 또는 none]
search_query: [검색엔진에 보낼 독립적 검색 쿼리 / none]

## route 분류 기준
- statistics: 제출/미제출 현황, 팀 수, 개수 등 **수치/통계** 질문
- search: 메일 **내용** 검색이 필요한 질문
  - 이슈, 분석, 개선 사항, 진행 상황 등
  - **반도체 공정/장비/기술 용어** (예: pulsed, dep, ALD, CVD, etch, cleaning, 수율, defect, particle 등)
  - 특정 팀, 프로젝트, 업무 관련 질문
  - 잘 모르는 전문 용어가 포함된 질문 → search로 분류
- general: 인사, 감사, 도움말, 시스템 사용법 등 **명확한 일반 대화만**

## 중요: 판단이 애매하면 search로 분류하세요!
- 반도체/공정/기술 관련 단어가 보이면 무조건 search
- 영어 약어나 전문 용어가 있으면 search
- 확실히 일반 대화가 아니면 search

## mail_type 분류 기준
- daily: 일일보고/데일리 메일만 검색할 때
- weekly: 주간보고/주보만 검색할 때
- all: 둘 다 검색하거나 구분이 불명확할 때

## week 분류 기준
- 단일 주차: "YYYY-WW" 형식으로 출력
  예) "30주차 보고" → week: 2025-30
  예) "48주차 DT팀" → week: 2025-48
- 복수 주차: 콤마(,)로 구분하여 나열
  예) "최근 3주" → week: (위 '최근 3주' 값을 그대로 사용)
  예) "최근 2주" → week: (위 '최근 2주' 값을 그대로 사용)
  예) "47~49주차" → week: 2025-47,2025-48,2025-49
- 상대적 시간 표현은 위 날짜 정보를 사용하여 **반드시 실제 주차로 변환**:
  예) "이번주" → week: (위 '이번주' 값 사용)
  예) "저번주"/"지난주" → week: (위 '저번주/지난주' 값 사용)
  예) "다음주" → week: (위 '다음주' 값 사용)
- 주차 언급이 없으면 → week: none
- **중요**: "최근 N주", "이번주", "저번주" 등을 절대 그대로 출력하지 말고, 반드시 위 날짜 정보의 실제 값으로 변환하세요

## search_query 작성 규칙 (route가 search일 때만)
- 검색엔진(OpenSearch)에 보낼 **독립적인 검색 쿼리**를 작성하세요.
- 핵심 원칙:
  1. 이전 대화를 참조하는 후속 질문이면, 이전 대화의 **핵심 키워드/주제**를 반드시 포함
  2. "정리해줘", "알려줘", "조사해줘", "다시" 같은 **지시어는 제거**하고 검색 키워드만 추출
  3. **시간/주차 표현은 search_query에 절대 포함하지 마세요** (시간 필터는 week 필드가 담당)
     - "이번주", "저번주", "최근 3주", "48주차" 같은 표현은 모두 week에만 반영
  4. 팀명, 기술 용어, 프로젝트명 등 **검색에 필요한 키워드**만 포함
- route가 search가 아니면 → search_query: none

## search_query 예시
- 첫 질문 "dsm misalign 조사해줘" → search_query: dsm misalign
- 후속 질문 "오래된 순에서 최신순으로 정리해줘" → search_query: dsm misalign (이전 대화 주제 유지)
- 후속 질문 "다른 팀은?" → search_query: dsm misalign (이전 주제 + 팀 범위 확장)
- 첫 질문 "이번주 PROCESS팀 수율 이슈" → search_query: PROCESS팀 수율 이슈 (시간은 week에)
- 첫 질문 "저번주 DT팀 보고 내용" → search_query: DT팀 보고 내용 (시간은 week에)
- 후속 질문 "최근 3주차로 다시 조사해줘" → search_query: PROCESS팀 업무 (시간은 week에, 주제는 이전 대화에서)
- 후속 질문 "더 자세히 알려줘" → search_query: 수율 이슈 (이전 대화의 검색 주제를 그대로 사용)
- 후속 질문 "좀 더 설명해줘" → search_query: (이전 대화의 검색 주제를 그대로 사용)
- 후속 질문 "계속" → search_query: (이전 대화의 검색 주제를 그대로 사용)

## 중요: 대화 맥락 고려
- 이전 대화가 있으면 현재 질문이 후속 질문인지 확인하세요.
- "3주차는?", "그럼 다음주는?", "다른 팀은?" 같은 짧은 질문은 이전 대화의 맥락을 이어받습니다.
- "더 자세히", "좀 더 알려줘", "계속" 같은 모호한 후속 질문은 이전 대화의 검색 주제를 그대로 search_query에 사용하세요. "자세히" 같은 모호한 단어만으로 search_query를 만들면 안 됩니다.
- 이전에 통계(statistics)를 물어봤다면 후속 질문도 statistics입니다.
- 이전에 내용 검색(search)을 했다면 후속 질문도 search입니다.

## 분류 예시
- "47주차 주보 미제출 팀 알려줘" → route: statistics, mail_type: weekly, week: 2025-47, search_query: none
- "30주차 DT팀 보고 내용" → route: search, mail_type: weekly, week: 2025-30, search_query: DT팀 보고 내용
- "pulsed w dep 관련 내용" → route: search, mail_type: all, week: none, search_query: pulsed w dep
- "ALD 공정 이슈" → route: search, mail_type: all, week: none, search_query: ALD 공정 이슈
- "데일리 메일 이슈 알려줘" → route: search, mail_type: daily, week: none, search_query: 이슈
- "PROCESS팀 수율 이슈 알려줘" → route: search, mail_type: all, week: none, search_query: PROCESS팀 수율 이슈
- "최근 3주 YIELD팀 현황" → route: search, mail_type: all, week: (최근 3주 값), search_query: YIELD팀 현황
- "47~49주차 FA팀 이슈" → route: search, mail_type: all, week: 2025-47,2025-48,2025-49, search_query: FA팀 이슈
- "wads가 뭐니" → route: search, mail_type: all, week: none, search_query: wads
- "안녕" → route: general, mail_type: all, week: none, search_query: none
- "고마워" → route: general, mail_type: all, week: none, search_query: none
{history_section}
현재 질문: {routing_question}

출력:"""

    route = fallback_route(routing_question)
    mail_type = state.get("mail_type")
    inferred_week = None  # API 필터가 없을 때만 사용하는 라우터 추론값
    search_query = state.get("search_query") or routing_question
    fallback_reason = None

    try:
        llm = get_llm()
        response = llm.invoke([HumanMessage(content=simple_prompt)])
        answer = response.content.strip().lower()
        route_parsed = False

        # route 파싱
        if "route:" in answer:
            route_line = [l for l in answer.split("\n") if "route:" in l]
            if route_line:
                route_part = route_line[0].split("route:")[-1].strip()
                if "search" in route_part:
                    route = "search"
                    route_parsed = True
                elif "statistic" in route_part:
                    route = "statistics"
                    route_parsed = True
                elif "general" in route_part:
                    route = "general"
                    route_parsed = True
        else:
            # fallback: 전체 응답에서 키워드 찾기
            if "search" in answer:
                route = "search"
                route_parsed = True
            elif "statistic" in answer:
                route = "statistics"
                route_parsed = True

        if not route_parsed:
            route = fallback_route(question)
            fallback_reason = "invalid_router_output"

        # mail_type 파싱
        if not mail_type and "mail_type:" in answer:
            mail_line = [l for l in answer.split("\n") if "mail_type:" in l]
            if mail_line:
                mail_part = mail_line[0].split("mail_type:")[-1].strip()
                if "daily" in mail_part:
                    mail_type = "daily_report"
                elif "weekly" in mail_part:
                    mail_type = "weekly_report"
                # "all"이면 None 유지 (전체 검색)

        # week 파싱 (단일 또는 콤마 구분 복수 주차)
        if "week:" in answer:
            week_line = [l for l in answer.split("\n") if "week:" in l]
            if week_line:
                week_part = week_line[0].split("week:")[-1].strip()
                # 콤마 구분 복수 주차 또는 단일 주차 매칭
                week_matches = re.findall(r'(\d{4}-\d{1,2})', week_part)
                if week_matches:
                    inferred_week = week_matches  # List[str]
                # "none"이면 None 유지

        # search_query 파싱
        if "search_query:" in answer:
            sq_line = [l for l in answer.split("\n") if "search_query:" in l]
            if sq_line:
                sq_part = sq_line[0].split("search_query:", 1)[-1].strip()
                if sq_part and sq_part.lower() != "none":
                    search_query = sq_part

        rule_route = fallback_route(routing_question)
        guarded_route = route
        if rule_route in {"general", "statistics"}:
            guarded_route = rule_route
        elif route == "general":
            guarded_route = "search"
        if guarded_route != route:
            route = guarded_route
            fallback_reason = "rule_route_guard"

        _elapsed = (_time.time() - _t_start) * 1000
        week = explicit_weeks or normalize_weeks(inferred_week)
        week_display = ",".join(week) if week else "none"
        print(f"🔀 [Router] 분류 결과: route={route}, mail_type={mail_type}, week={week_display}")
        print(f"🔀 [Router] search_query: {search_query[:80]}")
        print(f"   LLM 응답: {answer[:120]}...")
        print(f"   ⏱️ {_elapsed:.0f}ms")

    except Exception as e:
        print(f"⚠️ [Router] 분류 실패, 기본값 사용: {e}")
        route = fallback_route(routing_question)
        week = explicit_weeks
        search_query = state.get("search_query") or routing_question
        fallback_reason = str(e)

    trace = append_trace(
        trace,
        "router_completed",
        route=route,
        team=state.get("team"),
        weeks=week,
        fallback_reason=fallback_reason,
    )
    print(
        json.dumps(
            trace[-1],
            ensure_ascii=False,
            default=str,
        )
    )
    return {
        "route": route,
        "mail_type": mail_type,
        "week": week,
        "search_query": search_query,
        "trace": trace,
    }


def route_question(
    state: GraphState,
) -> Literal["plan_retrieval", "statistics", "llm_answer"]:
    """라우팅 함수: route 값에 따라 다음 노드 결정"""
    route = state.get("route", "general")
    if route == "search":
        return "plan_retrieval"
    elif route == "statistics":
        return "statistics"
    else:
        return "llm_answer"


def plan_retrieval_node(state: GraphState) -> Dict[str, Any]:
    """Select sources, sub-questions, filters, and search weights."""
    import time as _time

    started = _time.time()
    planning_question = (
        state.get("standalone_question")
        or state.get("search_query")
        or state["question"]
    )
    question_teams = state.get("contextualized_teams") or sorted(
        set(re.findall(r"[A-Za-z가-힣]+팀", planning_question))
    )
    filters = {
        "team": state.get("team"),
        "weeks": state.get("week"),
        "allowed_teams": (
            [state["team"]] if state.get("team") else question_teams or None
        ),
    }
    fallback_plan = fallback_retrieval_plan(planning_question, filters)
    plan = fallback_plan
    planner_mode = "rule_fallback"
    fallback_reason = None
    prompt = f"""Create a retrieval plan as JSON only.

Schema:
{{
  "intent": "general|single_search|comparison|trend|statistics",
  "sources": ["mail|wiki|technical_document|statistics"],
  "sub_questions": ["bounded independent search question"],
  "filters": {json.dumps(filters, ensure_ascii=False)},
  "search_strategy": "semantic|keyword|hybrid",
  "vector_weight": 0.0,
  "keyword_weight": 0.0
}}

Use only required sources. Split team comparisons by team and trends by week.
Question: {planning_question}
"""
    try:
        response = get_llm().invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        parsed = RetrievalPlan.model_validate_json(content)
        plan = parsed.model_copy(update={"filters": filters})
        planner_mode = "llm_structured"
    except Exception as error:
        fallback_reason = f"{type(error).__name__}: {error}"

    trace = append_trace(
        state.get("trace", []),
        "retrieval_planned",
        intent=plan.intent,
        selected_sources=plan.sources,
        sub_questions=plan.sub_questions,
        filters=plan.filters,
        search_strategy=plan.search_strategy,
        vector_weight=plan.vector_weight,
        keyword_weight=plan.keyword_weight,
        planner_mode=planner_mode,
        fallback_reason=fallback_reason,
        elapsed_ms=round((_time.time() - started) * 1000, 1),
    )
    print(json.dumps(trace[-1], ensure_ascii=False, default=str))
    return {
        "retrieval_plan": plan.model_dump(),
        "selected_sources": plan.sources,
        "sub_questions": plan.sub_questions,
        "trace": trace,
    }


def retrieve_document(state: GraphState) -> Dict[str, Any]:
    """Retrieve 노드: OpenSearch 검색"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    search_query = state.get("search_query") or question  # fallback: 원문
    team = state.get("team")  # API에서 전달된 명시적 팀 필터
    mail_type = state.get("mail_type")  # 메일 유형 필터
    week = state.get("week")  # 주차 필터 (Router가 추출, List[str] 또는 None)
    trace = state.get("trace", [])
    filters = {"team": team, "weeks": week}
    search_attempts = int(state.get("search_attempts", 0)) + 1
    search_key = make_search_key(search_query, "all", filters)
    executed_search_keys = [*state.get("executed_search_keys", [])]
    if search_key not in executed_search_keys:
        executed_search_keys.append(search_key)
    print(f"🔍 [Retrieve] 원문 질문: {question[:50]}...")
    print(f"🔍 [Retrieve] 검색 쿼리: {search_query[:50]}...")
    if mail_type:
        print(f"📧 [Retrieve] 메일 유형 필터: {mail_type}")
    if week:
        week_display = ",".join(week) if isinstance(week, list) else week
        print(f"📅 [Retrieve] 주차 필터: {week_display} ({len(week) if isinstance(week, list) else 1}개 주차)")

    if not os_client:
        print("⚠️ [Retrieve] OpenSearch 클라이언트 없음")
        trace = append_trace(
            trace,
            "retrieval_completed",
            query=search_query,
            filters=filters,
            result_counts={},
            technical_filter_applicable=False,
            elapsed_ms=round((_time.time() - _t_start) * 1000, 1),
            error="search_unavailable",
        )
        print(json.dumps(trace[-1], ensure_ascii=False, default=str))
        return {
            "context": "",
            "retrieval_error": "search_unavailable",
            "search_attempts": search_attempts,
            "executed_search_keys": executed_search_keys,
            "trace": trace,
        }

    try:
        # ===== Tier 1: Wiki 요약 검색 =====
        wiki_results = []
        try:
            _t_wiki_start = _time.time()
            wiki_results = os_client.search_wiki(
                search_query,
                team=team,
                week=week,
                limit=5,
            )
            _wiki_elapsed = (_time.time() - _t_wiki_start) * 1000
            if wiki_results:
                print(f"📖 [Retrieve] Wiki 요약 검색 완료: {len(wiki_results)}개 문서 ({_wiki_elapsed:.0f}ms)")
            else:
                print(f"📖 [Retrieve] Wiki 요약 없음 ({_wiki_elapsed:.0f}ms)")
        except Exception as e:
            print(f"⚠️ [Retrieve] Wiki 검색 실패 (무시): {e}")

        # ===== Tier 2: Raw chunk 검색 =====
        _t_search_start = _time.time()
        results = os_client.search(
            search_query,
            team=team,
            week=week,
            mail_type=mail_type,
            limit=SEARCH_RESULT_LIMIT,
        )
        _search_elapsed = (_time.time() - _t_search_start) * 1000
        print(f"⏱️ [Retrieve] OpenSearch 검색: {_search_elapsed:.0f}ms")

        # 검색 결과 포맷팅 (토큰 카운팅 제거 - 속도 개선)
        formatted = [
            {
                "team": r["team"],
                "week": r["week"],
                "mail_id": r["mail_id"],
                "text": (
                    r["text"][:MAX_TEXT_PER_DOC]
                    if len(r["text"]) > MAX_TEXT_PER_DOC
                    else r["text"]
                ),
                "score": round(r["score"], 4),
                "html_path": r.get("html_path", ""),
                "part_index": r.get("part_index"),
                "total_parts": r.get("total_parts"),
            }
            for r in results
        ]

        print(f"📊 [Retrieve] 메일 검색 완료: {len(formatted)}개 문서")

        # ===== Tier 3: 배경지식 검색 (syldgpt) =====
        secondary_results = []
        try:
            _t_sec_start = _time.time()
            secondary_results = os_client.search_secondary(
                search_query, limit=SEARCH_RESULT_LIMIT
            )
            _sec_elapsed = (_time.time() - _t_sec_start) * 1000
            print(f"📚 [Retrieve] 배경지식 검색 완료: {len(secondary_results)}개 문서 ({_sec_elapsed:.0f}ms)")
        except Exception as e:
            print(f"⚠️ [Retrieve] 배경지식 검색 실패 (무시): {e}")

        # 컨텍스트 문자열 생성 (3-tier 구조)
        if not wiki_results and not formatted and not secondary_results:
            _total_elapsed = (_time.time() - _t_start) * 1000
            print(f"⏱️ [Retrieve] 총 소요시간: {_total_elapsed:.0f}ms")
            trace = append_trace(
                trace,
                "retrieval_completed",
                query=search_query,
                filters=filters,
                result_counts={"wiki": 0, "mail": 0, "technical_document": 0},
                technical_filter_applicable=False,
                elapsed_ms=round(_total_elapsed, 1),
                error="no_relevant_documents",
            )
            print(json.dumps(trace[-1], ensure_ascii=False, default=str))
            return {
                "context": "",
                "retrieval_error": "no_relevant_documents",
                "search_attempts": search_attempts,
                "executed_search_keys": executed_search_keys,
                "trace": trace,
            }

        task_metadata = {"query": search_query, "sub_question": question}
        new_candidates = [
            *[
                normalize_result("wiki", result, task_metadata)
                for result in wiki_results
            ],
            *[
                normalize_result("mail", result, task_metadata)
                for result in formatted
            ],
            *[
                normalize_result(
                    "technical_document",
                    {
                        **result,
                        "text": result.get("text", "")[:MAX_TEXT_PER_DOC],
                    },
                    task_metadata,
                )
                for result in secondary_results
            ],
        ]
        normalized_candidates = [
            *state.get("retrieval_results", []),
            *new_candidates,
        ]
        retrieval_results, duplicates_removed = deduplicate_documents(
            normalized_candidates
        )
        ranked_results = rerank_documents(
            question,
            retrieval_results,
            filters,
        )
        context_text, reranked_results = build_context(ranked_results)

        # 검색 결과 JSON도 저장 (출처 추출용)
        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [Retrieve] 총 소요시간: {_total_elapsed:.0f}ms")
        trace = append_trace(
            trace,
            "retrieval_completed",
            query=search_query,
            filters=filters,
            result_counts={
                "wiki": len(wiki_results),
                "mail": len(formatted),
                "technical_document": len(secondary_results),
            },
            technical_filter_applicable=False,
            duplicates_removed=duplicates_removed,
            ranking_before=[
                {
                    "document_id": document["document_id"],
                    "original_score": document.get("original_score"),
                }
                for document in retrieval_results
            ],
            ranking_after=[
                {
                    "document_id": document["document_id"],
                    "rerank_score": document.get("rerank_score"),
                }
                for document in reranked_results
            ],
            selected_document_ids=[
                document["document_id"] for document in reranked_results
            ],
            elapsed_ms=round(_total_elapsed, 1),
            error=None,
        )
        print(json.dumps(trace[-1], ensure_ascii=False, default=str))
        return {
            "context": context_text,
            "retrieval_error": None,
            "retrieval_results": retrieval_results,
            "reranked_results": reranked_results,
            "search_attempts": search_attempts,
            "executed_search_keys": executed_search_keys,
            "trace": trace,
            "messages": [
                ToolMessage(
                    content=json.dumps(formatted, ensure_ascii=False),
                    name="retrieve",
                    tool_call_id="retrieve",
                )
            ],
        }

    except Exception as e:
        print(f"❌ [Retrieve] 검색 실패: {e}")
        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [Retrieve] 총 소요시간: {_total_elapsed:.0f}ms")
        trace = append_trace(
            trace,
            "retrieval_completed",
            query=search_query,
            filters=filters,
            result_counts={},
            technical_filter_applicable=False,
            elapsed_ms=round(_total_elapsed, 1),
            error="search_failed",
            error_type=type(e).__name__,
        )
        print(json.dumps(trace[-1], ensure_ascii=False, default=str))
        return {
            "context": "",
            "retrieval_error": "search_failed",
            "search_attempts": search_attempts,
            "executed_search_keys": executed_search_keys,
            "trace": trace,
        }


def _execute_search_task(
    task: SearchTask,
    mail_type: Optional[str],
) -> tuple[SearchTask, List[Dict[str, Any]]]:
    if not os_client:
        raise RuntimeError("OpenSearch client is unavailable")
    if task.source == "mail":
        results = os_client.search(
            task.query,
            team=task.team,
            week=task.weeks,
            mail_type=mail_type,
            limit=SEARCH_RESULT_LIMIT,
            vector_weight=task.vector_weight,
            keyword_weight=task.keyword_weight,
        )
    elif task.source == "wiki":
        results = os_client.search_wiki(
            task.query,
            team=task.team,
            week=task.weeks,
            limit=min(50, max(30, SEARCH_RESULT_LIMIT)),
            vector_weight=task.vector_weight,
            keyword_weight=task.keyword_weight,
        )
    elif task.source == "technical_document":
        results = os_client.search_secondary(
            task.query,
            limit=SEARCH_RESULT_LIMIT,
            vector_weight=task.vector_weight,
            keyword_weight=task.keyword_weight,
        )
    else:
        results = []
    if task.source in {"mail", "wiki"}:
        requested_weeks = set(task.weeks or [])
        results = [
            result
            for result in results
            if (not task.team or result.get("team") == task.team)
            and (not requested_weeks or result.get("week") in requested_weeks)
        ]
    return task, results


def execute_searches_node(state: GraphState) -> Dict[str, Any]:
    """Execute planned independent searches concurrently and merge evidence."""
    import time as _time

    started = _time.time()
    trace = state.get("trace", [])
    if not os_client:
        trace = append_trace(
            trace,
            "searches_executed",
            result_counts={},
            search_errors=[{"source": "all", "error": "search_unavailable"}],
            elapsed_ms=round((_time.time() - started) * 1000, 1),
        )
        print(json.dumps(trace[-1], ensure_ascii=False, default=str))
        return {
            "context": "",
            "retrieval_error": "search_unavailable",
            "search_attempts": state.get("search_attempts", 0) + 1,
            "trace": trace,
        }

    try:
        plan = RetrievalPlan.model_validate(state.get("retrieval_plan", {}))
    except Exception:
        plan = fallback_retrieval_plan(
            state["question"],
            {"team": state.get("team"), "weeks": state.get("week")},
        )

    if state.get("rewritten_query"):
        plan = plan.model_copy(update={"sub_questions": [state["search_query"]]})
    tasks = build_search_tasks(plan)
    executed_keys = set(state.get("executed_search_keys", []))
    pending_tasks = [task for task in tasks if task.search_key not in executed_keys]
    search_attempts = state.get("search_attempts", 0) + 1
    raw_results: list[tuple[SearchTask, list[dict[str, Any]]]] = []
    search_errors: list[dict[str, Any]] = []

    if pending_tasks:
        worker_count = min(4, MAX_PARALLEL_SEARCH_TASKS, len(pending_tasks))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_execute_search_task, task, state.get("mail_type")): task
                for task in pending_tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    raw_results.append(future.result())
                except Exception as error:
                    search_errors.append(
                        {
                            "source": task.source,
                            "sub_question": task.sub_question,
                            "error_type": type(error).__name__,
                            "error": str(error),
                        }
                    )

    raw_results.sort(key=lambda item: item[0].search_key)
    search_errors.sort(key=lambda item: (item["source"], item["sub_question"]))
    new_documents: list[dict[str, Any]] = []
    mail_tool_results: list[dict[str, Any]] = []
    result_counts: dict[str, int] = {source: 0 for source in plan.sources}
    for task, results in raw_results:
        result_counts[task.source] = result_counts.get(task.source, 0) + len(results)
        task_metadata = task.model_dump()
        for result in results:
            if task.source == "mail":
                trimmed = {
                    **result,
                    "text": result.get("text", "")[:MAX_TEXT_PER_DOC],
                }
                mail_tool_results.append(trimmed)
                new_documents.append(normalize_result("mail", trimmed, task_metadata))
            elif task.source == "wiki":
                new_documents.append(normalize_result("wiki", result, task_metadata))
            elif task.source == "technical_document":
                trimmed = {
                    **result,
                    "text": result.get("text", "")[:MAX_TEXT_PER_DOC],
                }
                new_documents.append(
                    normalize_result("technical_document", trimmed, task_metadata)
                )

    merged_candidates = [*state.get("retrieval_results", []), *new_documents]
    retrieval_results, duplicates_removed = deduplicate_documents(merged_candidates)
    ranked = rerank_documents(
        state["question"],
        retrieval_results,
        {"team": state.get("team"), "weeks": state.get("week")},
    )
    context, reranked_results = build_context(ranked)
    executed_search_keys = [
        *state.get("executed_search_keys", []),
        *[task.search_key for task in pending_tasks],
    ]
    retrieval_error = None
    if not retrieval_results:
        retrieval_error = "search_failed" if search_errors else "no_relevant_documents"

    trace = append_trace(
        trace,
        "searches_executed",
        selected_sources=plan.sources,
        filters=plan.filters,
        task_count=len(pending_tasks),
        result_counts=result_counts,
        search_errors=search_errors,
        technical_filter_applicable=False,
        duplicates_removed=duplicates_removed,
        ranking_before=[
            {
                "document_id": document["document_id"],
                "original_score": document.get("original_score"),
            }
            for document in retrieval_results
        ],
        ranking_after=[
            {
                "document_id": document["document_id"],
                "rerank_score": document.get("rerank_score"),
            }
            for document in reranked_results
        ],
        selected_document_ids=[doc["document_id"] for doc in reranked_results],
        search_attempts=search_attempts,
        elapsed_ms=round((_time.time() - started) * 1000, 1),
    )
    print(json.dumps(trace[-1], ensure_ascii=False, default=str))
    messages = []
    if mail_tool_results:
        messages.append(
            ToolMessage(
                content=json.dumps(mail_tool_results, ensure_ascii=False),
                name="retrieve",
                tool_call_id=f"retrieve_{search_attempts}",
            )
        )
    return {
        "context": context,
        "retrieval_error": retrieval_error,
        "retrieval_results": retrieval_results,
        "reranked_results": reranked_results,
        "search_attempts": search_attempts,
        "executed_search_keys": executed_search_keys,
        "messages": messages,
        "trace": trace,
    }


def grade_retrieval_node(state: GraphState) -> Dict[str, Any]:
    """Evaluate whether the accumulated evidence can answer the question."""
    import time as _time

    started = _time.time()
    documents = state.get("reranked_results", [])
    filters = {"team": state.get("team"), "weeks": state.get("week")}
    deterministic_grade = fallback_grade_retrieval(
        state["question"],
        documents,
        filters,
        state.get("sub_questions", []),
    )
    grade = deterministic_grade
    grader_mode = "rule_fallback"
    fallback_reason = None

    if documents:
        prompt = f"""Assess this retrieval grade for a RAG system.

Return JSON only with these fields:
relevant: boolean
sufficient: boolean
score: number from 0 to 1
missing_information: array of strings
reason: string

Question: {state['question']}
Required filters: {json.dumps(filters, ensure_ascii=False)}
Sub questions: {json.dumps(state.get('sub_questions', []), ensure_ascii=False)}
Retrieved context:
{state.get('context', '')}
"""
        try:
            response = get_llm().invoke([HumanMessage(content=prompt)])
            content = response.content.strip()
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
            llm_grade = RetrievalGrade.model_validate_json(content)
            hard_missing = list(deterministic_grade.missing_information)
            combined_missing = list(
                dict.fromkeys([*hard_missing, *llm_grade.missing_information])
            )
            grade = RetrievalGrade(
                relevant=deterministic_grade.relevant and llm_grade.relevant,
                sufficient=(
                    deterministic_grade.relevant
                    and llm_grade.relevant
                    and llm_grade.sufficient
                    and not combined_missing
                ),
                score=min(deterministic_grade.score, llm_grade.score),
                missing_information=combined_missing,
                reason=llm_grade.reason,
            )
            grader_mode = "llm_with_rule_constraints"
        except Exception as error:
            fallback_reason = f"{type(error).__name__}: {error}"

    grade_label = (
        "sufficient"
        if grade.sufficient
        else "insufficient"
        if grade.relevant
        else "irrelevant"
    )
    trace = append_trace(
        state.get("trace", []),
        "retrieval_graded",
        grade=grade_label,
        score=grade.score,
        missing_information=grade.missing_information,
        grader_mode=grader_mode,
        fallback_reason=fallback_reason,
        search_attempts=state.get("search_attempts", 0),
        elapsed_ms=round((_time.time() - started) * 1000, 1),
    )
    print(json.dumps(trace[-1], ensure_ascii=False, default=str))
    return {
        "retrieval_grade": grade_label,
        "retrieval_score": grade.score,
        "missing_information": grade.missing_information,
        "trace": trace,
    }


def route_after_retrieval(
    state: GraphState,
) -> Literal["llm_answer", "rewrite_query", "generate_limited_answer"]:
    if state.get("retrieval_grade") == "sufficient":
        return "llm_answer"
    if (
        state.get("search_attempts", 0) < MAX_SEARCH_ATTEMPTS
        and state.get("rewrite_count", 0) < MAX_QUERY_REWRITES
    ):
        return "rewrite_query"
    return "generate_limited_answer"


def rewrite_query_node(state: GraphState) -> Dict[str, Any]:
    """Rewrite toward missing evidence and suppress identical searches."""
    import time as _time

    started = _time.time()
    current_query = state.get("search_query") or state["question"]
    rewritten_query = build_rewritten_query(
        state["question"],
        current_query,
        state.get("missing_information", []),
    )
    updated_plan = dict(state.get("retrieval_plan", {}))
    if not rewritten_query and state.get("retrieval_grade") in {
        "irrelevant",
        "insufficient",
    }:
        numeric_gap = any(
            "숫자" in missing or "수치" in missing
            for missing in state.get("missing_information", [])
        )
        if numeric_gap:
            rewritten_query = f"{current_query} 건수 수치"
        elif state.get("retrieval_grade") == "irrelevant":
            rewritten_query = f"{current_query} 관련 공정 원인 유사 사례"
        else:
            rewritten_query = f"{current_query} 상세 근거"
        exact_identifier = bool(
            re.search(r"\b[A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*\b", current_query)
        )
        keyword_focused = exact_identifier or numeric_gap
        updated_plan["search_strategy"] = (
            "keyword" if keyword_focused else "semantic"
        )
        updated_plan["vector_weight"] = 0.2 if keyword_focused else 0.8
        updated_plan["keyword_weight"] = 0.8 if keyword_focused else 0.2
        if not state.get("team"):
            sources = list(updated_plan.get("sources", []))
            if "technical_document" not in sources:
                sources.append("technical_document")
            updated_plan["sources"] = sources
    filters = {"team": state.get("team"), "weeks": state.get("week")}
    search_key = (
        make_search_key(rewritten_query, "all", filters) if rewritten_query else ""
    )
    allowed = bool(rewritten_query) and can_retry_search(
        state.get("search_attempts", 0),
        state.get("rewrite_count", 0),
        search_key,
        state.get("executed_search_keys", []),
    )
    trace = append_trace(
        state.get("trace", []),
        "query_rewritten" if allowed else "query_rewrite_skipped",
        previous_query=current_query,
        rewritten_query=rewritten_query,
        missing_information=state.get("missing_information", []),
        reason=None if allowed else "duplicate_or_no_new_search_terms",
        elapsed_ms=round((_time.time() - started) * 1000, 1),
    )
    print(json.dumps(trace[-1], ensure_ascii=False, default=str))
    if not allowed:
        return {
            "rewritten_query": None,
            "retrieval_error": "retry_exhausted",
            "trace": trace,
        }
    return {
        "search_query": rewritten_query,
        "rewritten_query": rewritten_query,
        "rewrite_count": state.get("rewrite_count", 0) + 1,
        "retrieval_plan": updated_plan,
        "trace": trace,
    }


def route_after_rewrite(
    state: GraphState,
) -> Literal["execute_searches", "generate_limited_answer"]:
    if state.get("rewritten_query"):
        return "execute_searches"
    return "generate_limited_answer"


STATISTICS_SYSTEM_PROMPT = """당신은 통계 조회 도우미입니다. 사용자 질문에 맞는 함수를 반드시 호출하세요.

## 사용 가능한 함수
- get_missing_teams: 주간보고 **미제출 팀** 조회 (week 파라미터 필수!)
- count_weekly_reports_by_team: 주간보고 제출 수 조회 (팀별)
- count_daily_reports_by_team: 일일보고 제출 수 조회 (팀별)
- count_other_mails_by_team: 기타 메일 수 조회
- get_mail_type_summary: 전체 메일 현황 요약
- get_available_weeks: 데이터가 있는 주차 목록

## 예시 (반드시 따라하세요)
- "47주차 주보 미제출 팀 알려줘" → get_missing_teams(week="2025-47")
- "48주차 주보 미제출팀 알려줘" → get_missing_teams(week="2025-48")
- "48주차 주보 안보낸 팀알려줘" → get_missing_teams(week="2025-48")
- "48주차 주보 안 보낸 팀" → get_missing_teams(week="2025-48")
- "49주차 미제출 팀" → get_missing_teams(week="2025-49")
- "제출 현황 알려줘" → count_weekly_reports_by_team()
- "몇 개 팀이 제출했어?" → count_weekly_reports_by_team()

## 규칙
1. "미제출", "미제출팀", "안 낸", "안낸", "안보낸", "안 보낸" 키워드가 있으면 **반드시** get_missing_teams를 호출하세요.
2. week 파라미터는 "2025-48" 형식으로 지정하세요 (48주차 → 2025-48).
3. 주차가 명시되어 있으면 해당 주차를 week 파라미터로 전달하세요.
"""


def _filter_statistics_result_for_team(
    tool_name: str,
    result: Any,
    team: Optional[str],
) -> Any:
    """Restrict team-oriented statistics output when the API selected a team."""
    if not team or tool_name == "get_available_weeks":
        return result
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
    except (json.JSONDecodeError, TypeError):
        return result

    if tool_name in {
        "count_weekly_reports_by_team",
        "count_daily_reports_by_team",
        "count_other_mails_by_team",
    } and isinstance(parsed, dict):
        parsed = {team: parsed.get(team, 0)}
    elif tool_name == "get_missing_teams" and isinstance(parsed, list):
        parsed = [team] if team in parsed else []
    elif tool_name == "get_mail_type_summary" and isinstance(parsed, dict):
        for section in ("weekly_report", "daily_report", "other"):
            section_data = parsed.get(section)
            if not isinstance(section_data, dict):
                continue
            by_team = section_data.get("by_team", {})
            count = by_team.get(team, 0) if isinstance(by_team, dict) else 0
            section_data["by_team"] = {team: count}
            section_data["total"] = count

    return json.dumps(parsed, ensure_ascii=False, default=str)


def statistics_node(state: GraphState) -> Dict[str, Any]:
    """Statistics 노드: LLM + Tool Binding으로 통계 함수 호출"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    history_messages = state.get("messages", [])
    explicit_weeks = normalize_weeks(state.get("week"))
    explicit_week = explicit_weeks[0] if explicit_weeks else None
    print(f"📊 [Statistics] 통계 조회 시작: {question[:50]}...")

    tool_results = []

    # 히스토리 추출 (최근 대화만)
    history_text = ""
    conversation_only = [
        msg for msg in history_messages if isinstance(msg, (HumanMessage, AIMessage))
    ]
    recent_history = conversation_only[-(MAX_LLM_HISTORY_TURNS * 2) :]

    if recent_history:
        history_lines = []
        for msg in recent_history:
            if isinstance(msg, HumanMessage):
                history_lines.append(f"사용자: {msg.content}")
            elif isinstance(msg, AIMessage):
                content = (
                    msg.content[:200] + "..." if len(msg.content) > 200 else msg.content
                )
                history_lines.append(f"어시스턴트: {content}")
        history_text = "\n".join(history_lines)
        print(f"📊 [Statistics] 히스토리 {len(recent_history)}개 메시지 포함")

    # 히스토리 포함한 프롬프트 구성
    if history_text:
        user_content = f"""## 이전 대화
{history_text}

## 현재 질문
{question}

이전 대화의 맥락을 고려하여 적절한 함수를 호출하세요."""
    else:
        user_content = question

    try:
        # LLM에 통계 tool 바인딩
        _t_llm_start = _time.time()
        llm = get_llm().bind_tools(STATISTICS_TOOLS)
        response = llm.invoke(
            [
                SystemMessage(content=STATISTICS_SYSTEM_PROMPT),
                HumanMessage(content=user_content),
            ]
        )
        _llm_elapsed = (_time.time() - _t_llm_start) * 1000
        print(f"⏱️ [Statistics] LLM 호출: {_llm_elapsed:.0f}ms")

        # Tool 호출이 있는 경우 실행
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = dict(tc.get("args", {}))
                if (
                    explicit_week
                    and tool_name != "get_available_weeks"
                ):
                    tool_args["week"] = explicit_week
                if (
                    state.get("team")
                    and tool_name == "count_other_mails_by_team"
                ):
                    tool_args["team"] = state["team"]
                print(f"📊 [Statistics] Tool 호출: {tool_name}({tool_args})")

                # Tool 실행
                tool_func = next(
                    (t for t in STATISTICS_TOOLS if t.name == tool_name), None
                )
                if tool_func:
                    try:
                        result = tool_func.invoke(tool_args)
                        tool_results.append({"name": tool_name, "result": result})
                        print(f"📊 [Statistics] Tool 결과: {result[:100]}...")
                    except Exception as e:
                        print(f"⚠️ [Statistics] Tool 실행 실패: {tool_name} - {e}")
        else:
            # Tool 호출이 없으면 키워드 기반 fallback
            print(
                "⚠️ [Statistics] LLM이 Tool을 호출하지 않음, 키워드 기반 fallback 사용"
            )

            # 미제출 관련 키워드 확인
            missing_keywords = ["미제출", "안 낸", "안낸", "안보낸", "안 보낸"]
            if any(kw in question for kw in missing_keywords):
                # 주차 추출 (예: "48주차" → "2025-48")
                import re

                week_match = re.search(r"(\d{1,2})주차", question)
                if week_match:
                    week_num = int(week_match.group(1))
                    week = f"2026-{week_num}"
                    print(f"📊 [Statistics] Fallback: get_missing_teams(week={week})")
                    result = get_missing_teams.invoke({"week": week})
                    tool_results.append({"name": "get_missing_teams", "result": result})
                else:
                    result = get_mail_type_summary.invoke(
                        {"week": explicit_week} if explicit_week else {}
                    )
                    tool_results.append(
                        {"name": "get_mail_type_summary", "result": result}
                    )
            else:
                result = get_mail_type_summary.invoke(
                    {"week": explicit_week} if explicit_week else {}
                )
                tool_results.append({"name": "get_mail_type_summary", "result": result})

        if state.get("team"):
            for item in tool_results:
                item["result"] = _filter_statistics_result_for_team(
                    item["name"], item["result"], state["team"]
                )

        if tool_results:
            # 결과를 context로 변환
            context = "\n\n".join(
                [f"[{tr['name']}]\n{tr['result']}" for tr in tool_results]
            )

            _total_elapsed = (_time.time() - _t_start) * 1000
            print(f"⏱️ [Statistics] 총 소요시간: {_total_elapsed:.0f}ms")
            trace = append_trace(
                state.get("trace", []),
                "statistics_completed",
                filters={"team": state.get("team"), "weeks": explicit_weeks},
                tool_names=[item["name"] for item in tool_results],
                result_count=len(tool_results),
                elapsed_ms=round(_total_elapsed, 1),
            )
            print(json.dumps(trace[-1], ensure_ascii=False, default=str))
            return {
                "context": context,
                "messages": [
                    *[
                        ToolMessage(
                            content=tr["result"],
                            name=tr["name"],
                            tool_call_id=f"stats_{i}",
                        )
                        for i, tr in enumerate(tool_results)
                    ],
                    ],
                "trace": trace,
            }
        else:
            print("⚠️ [Statistics] Tool 결과 없음")
            _total_elapsed = (_time.time() - _t_start) * 1000
            print(f"⏱️ [Statistics] 총 소요시간: {_total_elapsed:.0f}ms")
            trace = append_trace(
                state.get("trace", []),
                "statistics_completed",
                filters={"team": state.get("team"), "weeks": explicit_weeks},
                tool_names=[],
                result_count=0,
                elapsed_ms=round(_total_elapsed, 1),
                fallback_reason="no_tool_result",
            )
            print(json.dumps(trace[-1], ensure_ascii=False, default=str))
            return {"context": "", "trace": trace}

    except Exception as e:
        print(f"❌ [Statistics] 통계 조회 실패: {e}")
        import traceback

        traceback.print_exc()
        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [Statistics] 총 소요시간: {_total_elapsed:.0f}ms")
        trace = append_trace(
            state.get("trace", []),
            "statistics_failed",
            filters={"team": state.get("team"), "weeks": explicit_weeks},
            error_type=type(e).__name__,
            fallback_reason=str(e),
            elapsed_ms=round(_total_elapsed, 1),
        )
        print(json.dumps(trace[-1], ensure_ascii=False, default=str))
        return {"context": "", "trace": trace}


def llm_answer_node(state: GraphState) -> Dict[str, Any]:
    """LLM Answer 노드: 최종 답변 생성"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    context = state.get("context", "")
    history_messages = state.get("messages", [])
    trace = state.get("trace", [])

    print(f"💬 [LLM Answer] 답변 생성 시작...")
    print(
        f"💬 [LLM Answer] Context 길이: {len(context)}, History: {len(history_messages)}"
    )

    # 현재 날짜/시간 정보
    now = datetime.now()
    date_info = (
        f"\n\n## 현재 날짜/시간 정보\n"
        f"- 오늘 날짜: {now.strftime('%Y년 %m월 %d일')}\n"
        f"- 요일: {['월','화','수','목','금','토','일'][now.weekday()]}요일\n"
        f"- 현재 시각: {now.strftime('%H시 %M분')}\n"
        f"- ISO 주차: {now.strftime('%G-%V')}"
    )

    # 프롬프트 구성
    route = state.get("route", "general")

    if route == "search" and not context:
        retrieval_error = state.get("retrieval_error")
        if retrieval_error == "search_failed":
            answer = (
                "검색 처리 중 오류가 발생해 답변 근거를 확보하지 못했습니다. "
                "현재 근거 없이 답변할 수 없습니다. 잠시 후 다시 시도해주세요."
            )
        else:
            answer = (
                "검색 결과에서 질문에 답할 수 있는 근거를 찾지 못했습니다. "
                "검색 범위나 표현을 바꿔 다시 질문해주세요."
            )
        trace = append_trace(
            trace,
            "answer_limited",
            reason=retrieval_error or "empty_context",
        )
        print(json.dumps(trace[-1], ensure_ascii=False, default=str))
        return {
            "answer": answer,
            "draft_answer": answer,
            "messages": [HumanMessage(content=question), AIMessage(content=answer)],
            "trace": trace,
        }

    if route == "search" and context:
        limitation = ""
        if state.get("limited_answer"):
            missing = ", ".join(state.get("missing_information", [])) or "추가 근거"
            limitation = (
                "\n검색 재시도 한도까지 충분한 근거를 확보하지 못했습니다. "
                f"확인된 내용만 답하고 다음 부족 정보를 명시하세요: {missing}\n"
            )
        user_prompt = f"""질문: {question}

참고 정보:
{context}
{limitation}

위 정보를 바탕으로 아래 구조에 맞춰 답변해주세요.

## 답변 구조 (반드시 이 순서와 형식을 따르세요)

(( 요약 ))
질문에 대한 핵심 답변을 3~5줄로 요약하세요.

(( 상세 설명 ))
요약에서 언급한 내용을 구체적으로 설명하세요.
• 개조식(•)으로 항목별 정리
• 팀명, 수치 등 핵심 키워드는 **굵게** 표시
• 각 항목에 출처 [S1], [S2] 형식으로 표시

(( 핵심 결론 ))
전체 내용을 1~2줄로 마무리하세요.

## 주의
- 섹션 제목은 반드시 (( 요약 )), (( 상세 설명 )), (( 핵심 결론 )) 형태로 작성하세요.
- 핵심 주장, 숫자, 팀명, 주차 뒤에는 참고 정보의 [S#] 출처를 표시하세요.
- (( 와 텍스트 사이에 띄어쓰기를 반드시 넣으세요.
- 섹션 제목 앞에 •(불릿)을 붙이지 마세요."""
    elif context:
        user_prompt = f"""질문: {question}

참고 정보:
{context}

위 정보를 바탕으로 질문에 답변해주세요.
섹션 제목은 (( 제목 )) 형태로 작성하세요. (( 와 텍스트 사이에 띄어쓰기를 넣으세요."""
    else:
        user_prompt = f"""질문: {question}

일반적인 대화로 응답해주세요."""

    try:
        llm = get_llm()

        # 메시지 구성: 시스템 프롬프트 + 날짜 정보 + 히스토리 + 현재 질문
        system_prompt_with_date = ANSWER_SYSTEM_PROMPT + date_info
        llm_messages = [{"role": "system", "content": system_prompt_with_date}]

        # 히스토리 추가 (최근 N턴만)
        conversation_only = [
            msg
            for msg in history_messages
            if isinstance(msg, (HumanMessage, AIMessage))
        ]
        recent_messages = conversation_only[-(MAX_LLM_HISTORY_TURNS * 2) :]

        for msg in recent_messages:
            if isinstance(msg, HumanMessage):
                llm_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                llm_messages.append({"role": "assistant", "content": msg.content})

        # 현재 질문 추가
        llm_messages.append({"role": "user", "content": user_prompt})

        print(f"💬 [LLM Answer] LLM 메시지 수: {len(llm_messages)}")

        _t_llm_start = _time.time()
        response = llm.invoke(llm_messages, timeout=60)
        _llm_elapsed = (_time.time() - _t_llm_start) * 1000
        print(f"⏱️ [LLM Answer] LLM 호출: {_llm_elapsed:.0f}ms")

        answer = response.content
        print(f"💬 [LLM Answer] 답변 생성 완료: {len(answer)}자")

        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [LLM Answer] 총 소요시간: {_total_elapsed:.0f}ms")
        return {
            "answer": answer,
            "draft_answer": answer,
            "messages": [
                HumanMessage(content=question),
                AIMessage(content=answer),
            ],
            "trace": append_trace(
                trace,
                "answer_generated",
                route=route,
                elapsed_ms=round(_total_elapsed, 1),
            ),
        }

    except Exception as e:
        print(f"❌ [LLM Answer] 답변 생성 실패: {e}")
        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [LLM Answer] 총 소요시간: {_total_elapsed:.0f}ms")
        return {
            "answer": "답변 생성 중 오류가 발생했습니다. 다시 시도해주세요.",
            "draft_answer": "답변 생성 중 오류가 발생했습니다. 다시 시도해주세요.",
            "messages": [],
            "trace": append_trace(
                trace,
                "answer_generation_failed",
                error_type=type(e).__name__,
                elapsed_ms=round(_total_elapsed, 1),
            ),
        }


def generate_limited_answer_node(state: GraphState) -> Dict[str, Any]:
    """Generate an explicitly limited answer from the evidence available."""
    result = llm_answer_node({**state, "limited_answer": True})
    trace = append_trace(
        result.get("trace", state.get("trace", [])),
        "limited_answer_generated",
        missing_information=state.get("missing_information", []),
        search_attempts=state.get("search_attempts", 0),
    )
    print(json.dumps(trace[-1], ensure_ascii=False, default=str))
    return {**result, "limited_answer": True, "trace": trace}


def evaluate_answer_node(state: GraphState) -> Dict[str, Any]:
    """Validate grounding, coverage, and citation IDs before finalization."""
    import time as _time

    started = _time.time()
    route = state.get("route", "general")
    answer = state.get("answer", "")
    documents = state.get("reranked_results", [])
    evaluator_mode = "not_applicable"
    fallback_reason = None

    if route != "search":
        evaluation = AnswerEvaluation(
            groundedness_score=1.0,
            completeness_score=1.0,
            citation_valid=True,
            unsupported_claims=[],
            missing_answers=[],
            passed=True,
            reason="검색 근거 검증이 필요하지 않은 경로입니다.",
        )
    else:
        deterministic = fallback_evaluate_answer(
            state["question"],
            answer,
            documents,
            {"team": state.get("team"), "weeks": state.get("week")},
        )
        evaluation = deterministic
        evaluator_mode = "rule_fallback"
        prompt = f"""Evaluate this RAG answer. Return JSON only.

Fields: groundedness_score, completeness_score, citation_valid,
unsupported_claims, missing_answers, passed, reason.
Only citations present in the evidence are valid. Do not overlook unsupported
numbers, teams, weeks, or unanswered comparison conditions.

Question: {state['question']}
Answer: {answer}
Evidence: {state.get('context', '')}
"""
        try:
            response = get_llm().invoke([HumanMessage(content=prompt)])
            content = re.sub(
                r"^```(?:json)?\s*|\s*```$", "", response.content.strip()
            )
            llm_evaluation = AnswerEvaluation.model_validate_json(content)
            unsupported = list(
                dict.fromkeys(
                    [
                        *deterministic.unsupported_claims,
                        *llm_evaluation.unsupported_claims,
                    ]
                )
            )
            missing = list(
                dict.fromkeys(
                    [*deterministic.missing_answers, *llm_evaluation.missing_answers]
                )
            )
            citation_valid, _ = validate_citations(answer, documents)
            evaluation = AnswerEvaluation(
                groundedness_score=min(
                    deterministic.groundedness_score,
                    llm_evaluation.groundedness_score,
                ),
                completeness_score=min(
                    deterministic.completeness_score,
                    llm_evaluation.completeness_score,
                ),
                citation_valid=citation_valid and llm_evaluation.citation_valid,
                unsupported_claims=unsupported,
                missing_answers=missing,
                passed=(
                    deterministic.passed
                    and llm_evaluation.passed
                    and citation_valid
                    and not unsupported
                    and not missing
                ),
                reason=llm_evaluation.reason,
            )
            evaluator_mode = "llm_with_rule_constraints"
        except Exception as error:
            fallback_reason = f"{type(error).__name__}: {error}"

    evaluation_data = evaluation.model_dump()
    trace = append_trace(
        state.get("trace", []),
        "answer_evaluated",
        **evaluation_data,
        evaluator_mode=evaluator_mode,
        fallback_reason=fallback_reason,
        answer_revision_count=state.get("answer_revision_count", 0),
        elapsed_ms=round((_time.time() - started) * 1000, 1),
    )
    print(json.dumps(trace[-1], ensure_ascii=False, default=str))
    return {
        "groundedness_score": evaluation.groundedness_score,
        "completeness_score": evaluation.completeness_score,
        "citation_valid": evaluation.citation_valid,
        "unsupported_claims": evaluation.unsupported_claims,
        "missing_answers": evaluation.missing_answers,
        "answer_evaluation": evaluation_data,
        "trace": trace,
    }


def route_after_answer_evaluation(
    state: GraphState,
) -> Literal["revise_answer", "rewrite_query", "finalize"]:
    evaluation = state.get("answer_evaluation", {})
    if state.get("route") != "search" or evaluation.get("passed"):
        return "finalize"
    if (
        state.get("reranked_results")
        and state.get("answer_revision_count", 0) < MAX_ANSWER_REVISIONS
    ):
        return "revise_answer"
    if (
        not state.get("reranked_results")
        and state.get("retrieval_error") != "retry_exhausted"
        and state.get("search_attempts", 0) < MAX_SEARCH_ATTEMPTS
        and state.get("rewrite_count", 0) < MAX_QUERY_REWRITES
    ):
        return "rewrite_query"
    return "finalize"


def revise_answer_node(state: GraphState) -> Dict[str, Any]:
    """Revise an unsupported draft once, using only the selected evidence."""
    import time as _time

    started = _time.time()
    current_answer = state.get("answer", "")
    issues = {
        "unsupported_claims": state.get("unsupported_claims", []),
        "missing_answers": state.get("missing_answers", []),
        "citation_valid": state.get("citation_valid", False),
    }
    prompt = f"""Revise this RAG answer using only the evidence below.
Remove unsupported claims, answer the requested conditions when evidence exists,
and cite only the supplied [S#] IDs. Return only the revised answer.

Question: {state['question']}
Current answer: {current_answer}
Validation issues: {json.dumps(issues, ensure_ascii=False)}
Evidence: {state.get('context', '')}
"""
    error_type = None
    try:
        revised = get_llm().invoke([HumanMessage(content=prompt)]).content.strip()
        if not revised:
            revised = current_answer
    except Exception as error:
        error_type = type(error).__name__
        revised = current_answer

    revision_count = state.get("answer_revision_count", 0) + 1
    trace = append_trace(
        state.get("trace", []),
        "answer_revised",
        answer_revision_count=revision_count,
        error_type=error_type,
        elapsed_ms=round((_time.time() - started) * 1000, 1),
    )
    print(json.dumps(trace[-1], ensure_ascii=False, default=str))
    return {
        "answer": revised,
        "draft_answer": revised,
        "answer_revision_count": revision_count,
        "messages": [AIMessage(content=revised)],
        "trace": trace,
    }


def _safe_evidence_only_answer(state: GraphState) -> str:
    documents = state.get("reranked_results", [])
    if not documents:
        return (
            "질문에 답할 수 있는 검증된 근거를 확보하지 못했습니다. "
            "검색 범위나 표현을 바꿔 다시 질문해주세요."
        )
    lines = [
        "답변 검증을 통과하지 못해 확인된 근거만으로 제한적으로 제공합니다."
    ]
    for document in documents[:3]:
        content = " ".join(str(document.get("content", "")).split())[:300]
        lines.append(
            f"• {document.get('title', '근거')}: {content} "
            f"[{document.get('citation_id')}]"
        )
    return "\n".join(lines)


def finalize_node(state: GraphState) -> Dict[str, Any]:
    """Expose only a validated answer, or a bounded evidence-only fallback."""
    import time as _time

    started = _time.time()
    evaluation = state.get("answer_evaluation", {})
    passed = bool(evaluation.get("passed"))
    final_answer = state.get("answer", "")
    if state.get("route") == "search" and not passed:
        final_answer = _safe_evidence_only_answer(state)
    trace = append_trace(
        state.get("trace", []),
        "answer_finalized",
        validation_passed=passed,
        used_safe_fallback=state.get("route") == "search" and not passed,
        answer_revision_count=state.get("answer_revision_count", 0),
        elapsed_ms=round((_time.time() - started) * 1000, 1),
    )
    print(json.dumps(trace[-1], ensure_ascii=False, default=str))
    return {
        "answer": final_answer,
        "final_answer": final_answer,
        "trace": trace,
    }


# ===== 그래프 빌드 =====
_naive_rag_graph = None


def get_naive_rag_graph():
    """Hybrid Agentic RAG 그래프 (기존 함수명 호환 유지, 싱글톤)."""
    global _naive_rag_graph
    if _naive_rag_graph is not None:
        return _naive_rag_graph

    workflow = StateGraph(GraphState)

    # 노드 추가
    if MULTITURN_V2_ENABLED:
        workflow.add_node("contextualize_turn", contextualize_turn_node)
        workflow.add_node("restore_prior_evidence", restore_prior_evidence_node)
    workflow.add_node("router", router_node)
    workflow.add_node("plan_retrieval", plan_retrieval_node)
    workflow.add_node("execute_searches", execute_searches_node)
    workflow.add_node("grade_retrieval", grade_retrieval_node)
    workflow.add_node("rewrite_query", rewrite_query_node)
    workflow.add_node("generate_limited_answer", generate_limited_answer_node)
    workflow.add_node("statistics", statistics_node)
    workflow.add_node("llm_answer", llm_answer_node)
    workflow.add_node("evaluate_answer", evaluate_answer_node)
    workflow.add_node("revise_answer", revise_answer_node)
    workflow.add_node("finalize", finalize_node)

    # 엣지 연결
    if MULTITURN_V2_ENABLED:
        workflow.add_edge(START, "contextualize_turn")
        workflow.add_conditional_edges(
            "contextualize_turn",
            route_after_contextualization,
            {
                "router": "router",
                "restore_prior_evidence": "restore_prior_evidence",
                "finalize": "finalize",
            },
        )
        workflow.add_edge("restore_prior_evidence", "llm_answer")
    else:
        workflow.add_edge(START, "router")

    # router → 3분기 (search/statistics/general)
    workflow.add_conditional_edges(
        "router",
        route_question,
        {
            "plan_retrieval": "plan_retrieval",
            "statistics": "statistics",
            "llm_answer": "llm_answer",
        },
    )

    # 검색 결과를 평가하고 필요한 경우 제한적으로 재검색
    workflow.add_edge("plan_retrieval", "execute_searches")
    workflow.add_edge("execute_searches", "grade_retrieval")
    workflow.add_conditional_edges(
        "grade_retrieval",
        route_after_retrieval,
        {
            "llm_answer": "llm_answer",
            "rewrite_query": "rewrite_query",
            "generate_limited_answer": "generate_limited_answer",
        },
    )
    workflow.add_conditional_edges(
        "rewrite_query",
        route_after_rewrite,
        {
            "execute_searches": "execute_searches",
            "generate_limited_answer": "generate_limited_answer",
        },
    )
    workflow.add_edge("statistics", "llm_answer")

    # 모든 초안은 검증 후 최대 1회 수정하고 최종 확정
    workflow.add_edge("llm_answer", "evaluate_answer")
    workflow.add_edge("generate_limited_answer", "evaluate_answer")
    workflow.add_conditional_edges(
        "evaluate_answer",
        route_after_answer_evaluation,
        {
            "revise_answer": "revise_answer",
            "rewrite_query": "rewrite_query",
            "finalize": "finalize",
        },
    )
    workflow.add_edge("revise_answer", "evaluate_answer")
    workflow.add_edge("finalize", END)

    _naive_rag_graph = workflow.compile()
    # _naive_rag_graph = workflow.compile(checkpointer=MemorySaver())
    print("✅ Hybrid Agentic RAG 그래프 컴파일 완료")
    return _naive_rag_graph


# ========== Chat 함수 ==========
def _convert_history_to_messages(history: List[Dict]) -> List[BaseMessage]:
    """히스토리를 LangChain 메시지로 변환"""
    converted = []
    for item in history:
        role = item.get("role")
        content = item.get("content", "")
        if role == "user":
            converted.append(HumanMessage(content=content))
        elif role == "assistant":
            converted.append(AIMessage(content=content))
        elif role == "system":
            converted.append(SystemMessage(content=content))
    return converted


async def chat_with_agent(
    user_message: str,
    team: Optional[str] = None,
    week: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Hybrid Agentic RAG 기반 채팅"""
    import time

    t_start = time.time()
    print(f"🚀 [chat_with_agent] 시작: {user_message[:50]}...")

    # 그래프 가져오기
    graph = get_naive_rag_graph()

    # 대화 히스토리와 구조화 메모리를 한 번에 조회
    history = []
    conversation_memory = ConversationMemory()
    memory_available = mongo_db is not None
    if conversation_id:
        history, conversation_memory, memory_available = await load_conversation_state(
            conversation_id
        )
    history_messages = _convert_history_to_messages(history)
    initial_trace = []

    # 그래프 실행
    loop = asyncio.get_event_loop()
    try:
        print("🚀 graph.invoke 시작...")
        config = RunnableConfig(
            recursion_limit=GRAPH_RECURSION_LIMIT,
            configurable={"thread_id": conversation_id or "single"},
        )

        result = await loop.run_in_executor(
            None,
            lambda: graph.invoke(
                {
                    "question": user_message,
                    "standalone_question": "",
                    "search_query": "",  # Router가 채워줌
                    "conversation_memory": conversation_memory.model_dump(),
                    "contextualized_turn": {},
                    "contextualized_teams": [],
                    "follow_up_type": "new_topic",
                    "context": "",
                    "answer": "",
                    "draft_answer": "",
                    "final_answer": "",
                    "messages": history_messages,
                    "route": "",
                    "team": team,
                    "mail_type": None,
                    "week": normalize_weeks(week),
                    "retrieval_error": None,
                    "retrieval_results": [],
                    "reranked_results": [],
                    "retrieval_grade": "",
                    "retrieval_score": 0.0,
                    "missing_information": [],
                    "search_attempts": 0,
                    "rewrite_count": 0,
                    "rewritten_query": None,
                    "sub_questions": [],
                    "selected_sources": [],
                    "retrieval_plan": {},
                    "executed_search_keys": [],
                    "limited_answer": False,
                    "groundedness_score": 0.0,
                    "completeness_score": 0.0,
                    "citation_valid": False,
                    "unsupported_claims": [],
                    "missing_answers": [],
                    "answer_evaluation": {},
                    "answer_revision_count": 0,
                    "trace": initial_trace,
                },
                config=config,
            ),
        )
        print("✅ graph.invoke 완료")

        # 디버깅: Query Rewrite 결과 출력
        _original_q = result.get("question", "")
        _rewritten_q = result.get("search_query", "")
        _route = result.get("route", "")
        _week = result.get("week")
        _week_display = ",".join(_week) if isinstance(_week, list) else (_week or "none")
        _mail_type = result.get("mail_type")
        print("=" * 60)
        print(f"📋 [DEBUG] 원문 질문:    {_original_q}")
        print(f"📋 [DEBUG] 재작성 쿼리:  {_rewritten_q}")
        print(f"📋 [DEBUG] route={_route}, week={_week_display}, mail_type={_mail_type}")
        print("=" * 60)

    except Exception as e:
        print(f"❌ graph.invoke 실패: {e}")
        import traceback

        traceback.print_exc()
        return {
            "answer": "처리 중 오류가 발생했습니다. 다시 시도해주세요.",
            "tool_calls": [],
            "tool_results": [],
        }

    # 결과 파싱
    answer = result.get("answer", "")
    response_messages = result.get("messages", [])
    tool_calls_info = []
    tool_results = []

    # messages에서 tool 정보 추출
    for msg in response_messages:
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls_info.append(
                    {
                        "name": tc.get("name", ""),
                        "arguments": tc.get("args", {}),
                    }
                )
        if isinstance(msg, ToolMessage):
            tool_results.append({"name": msg.name, "result": msg.content})

    if not answer:
        answer = "질문을 이해하지 못했습니다. 다시 말씀해주세요."

    elapsed = (time.time() - t_start) * 1000
    print(
        f"✅ [chat_with_agent] 완료: {elapsed:.0f}ms, route={result.get('route', 'unknown')}"
    )

    # 자동 Wiki 저장은 기본 비활성화한다. 조건 통과 시에도 검토 후보만 기록한다.
    if wiki_save_eligible(result):
        review_candidate = {
            "status": "pending_review",
            "question": user_message,
            "answer": answer,
            "source_ids": [
                document.get("document_id")
                for document in result.get("reranked_results", [])
            ],
        }
        print(
            json.dumps(
                {"event": "wiki_review_candidate_created", **review_candidate},
                ensure_ascii=False,
                default=str,
            )
        )

    completion_trace = append_trace(
        result.get("trace", []),
        "request_completed",
        route=result.get("route", "unknown"),
        search_attempts=result.get("search_attempts", 0),
        answer_revision_count=result.get("answer_revision_count", 0),
        elapsed_ms=round(elapsed, 1),
    )
    print(json.dumps(completion_trace[-1], ensure_ascii=False, default=str))

    return {
        "answer": answer,
        "tool_calls": tool_calls_info,
        "tool_results": tool_results,
        "retrieval_results": result.get("retrieval_results", []),
        "reranked_results": result.get("reranked_results", []),
        "selected_sources": result.get("selected_sources", []),
        "sub_questions": result.get("sub_questions", []),
        "retrieval_grade": result.get("retrieval_grade", ""),
        "search_attempts": result.get("search_attempts", 0),
        "answer_evaluation": result.get("answer_evaluation", {}),
        "groundedness_score": result.get("groundedness_score", 0.0),
        "completeness_score": result.get("completeness_score", 0.0),
        "citation_valid": result.get("citation_valid", False),
        "standalone_question": result.get("standalone_question", ""),
        "search_query": result.get("search_query", ""),
        "route": result.get("route", ""),
        "follow_up_type": result.get("follow_up_type", "new_topic"),
        "contextualized_teams": result.get("contextualized_teams", []),
        "contextualized_turn": result.get("contextualized_turn", {}),
        "week": result.get("week"),
        "mail_type": result.get("mail_type"),
        "conversation_memory": conversation_memory.model_dump(),
        "trace": completion_trace,
    }


# ========== 출처 처리 ==========
def parse_used_references(answer: str, contexts: List[Dict]) -> tuple:
    """LLM 답변에서 실제 참고한 문서만 추출"""
    matches = re.findall(r"\[S(\d+)\]", answer)

    if matches:
        by_citation = {
            str(context.get("citation_id", "")).upper(): context
            for context in contexts
            if context.get("citation_id")
        }
        used_contexts = []
        seen = set()
        for match in matches:
            citation_id = f"S{match}"
            context = by_citation.get(citation_id)
            if context is not None and citation_id not in seen:
                seen.add(citation_id)
                used_contexts.append(context)
        return answer, used_contexts

    # 이전 형식과의 하위 호환
    matches = re.findall(r"\[문서\s*(\d+)\]", answer)

    if not matches:
        return answer, []

    used_indices = set(int(m) - 1 for m in matches if int(m) - 1 < len(contexts))
    used_contexts = [ctx for i, ctx in enumerate(contexts) if i in used_indices]

    clean_answer = re.sub(
        r"\n*참고:\s*(\[(문서|참고)\s*\d+\],?\s*)+\.?$", "", answer, flags=re.MULTILINE
    ).strip()

    return clean_answer, used_contexts


def extract_references(contexts: List[Dict]) -> List[Reference]:
    """검색 결과에서 참조 출처 추출"""
    refs = []
    seen = set()

    for ctx in contexts:
        if ctx.get("source_type") and ctx.get("source_type") != "mail":
            continue
        metadata = ctx.get("metadata", {})
        team = ctx.get("team") or "unknown"
        week = ctx.get("week") or "unknown"
        mail_id = metadata.get("mail_id") or ctx.get("mail_id")
        if not mail_id or mail_id == "unknown":
            continue

        key = f"{team}_{week}_{mail_id}"
        if key in seen:
            continue
        seen.add(key)

        filename = f"{week}_{team}_{mail_id}.html"
        url = (
            metadata.get("url")
            or ctx.get("url")
            or f"{API_BASE_URL}/mail/{quote(filename, safe='')}"
        )
        refs.append(
            Reference(
                team=team,
                week=week,
                mail_id=mail_id,
                url=url,
                score=(
                    ctx.get("rerank_score")
                    or ctx.get("original_score")
                    or ctx.get("score")
                    or 0.0
                ),
                part_index=metadata.get("part_index", ctx.get("part_index")),
                total_parts=metadata.get("total_parts", ctx.get("total_parts")),
            )
        )

    return refs


def convert_table_to_bullet(text: str) -> str:
    """마크다운 표를 개조식으로 변환"""
    lines = text.split("\n")
    result = []
    table_lines = []
    in_table = False

    for line in lines:
        if line.strip().startswith("|") and "|" in line[1:]:
            in_table = True
            table_lines.append(line)
        else:
            if in_table and table_lines:
                result.append(_parse_table_to_bullet(table_lines))
                table_lines = []
                in_table = False
            result.append(line)

    if table_lines:
        result.append(_parse_table_to_bullet(table_lines))

    return "\n".join(result)


def _parse_table_to_bullet(table_lines: list) -> str:
    """표를 개조식으로 변환"""
    rows = []
    headers = []

    for line in table_lines:
        if re.match(r"^\|[\s\-:\|]+\|?$", line.strip()):
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if not headers:
            headers = cells
        else:
            rows.append(cells)

    bullet_lines = []
    for row in rows:
        if len(headers) == len(row) and len(row) >= 2:
            item = f"• {row[0]}"
            details = [f"{headers[j]}: {row[j]}" for j in range(1, len(row)) if row[j]]
            if details:
                item += f" ({', '.join(details)})"
            bullet_lines.append(item)
        elif row:
            bullet_lines.append(f"• {' | '.join(row)}")

    return "\n".join(bullet_lines) if bullet_lines else ""


def clean_html_breaks(text: str) -> str:
    """HTML <br> 태그를 줄바꿈으로 변환"""
    return re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)


def format_for_konan_chatbot(text: str) -> str:
    """마크다운/HTML을 코난 챗봇용 서식으로 변환

    코난 챗봇 지원 서식:
      **텍스트** → 굵은 글씨
      (( 텍스트 )) → 파란 글씨 (섹션 제목용)
    변환 대상: 헤딩, 코드블록, 인라인코드, 기울임, 취소선, 링크, 이미지,
              인용문, 수평선, 표, HTML 태그
    """
    # (( )) 패턴은 다른 regex에 의해 수정되지 않으므로 별도 보호 없이 유지

    # 1) 코드블록 (```...```) → 들여쓰기
    def _codeblock_replace(m):
        code = m.group(1).strip()
        indented = "\n".join("    " + line for line in code.split("\n"))
        return f"\n{indented}\n"

    text = re.sub(r"```\w*\n?([\s\S]*?)```", _codeblock_replace, text)

    # 2) 인라인 코드 (`...`) → 따옴표
    text = re.sub(r"`([^`]+)`", r'"\1"', text)

    # 3) 헤딩 (#{1,6} 제목) → (( 제목 )) 파란색 섹션 제목
    def _heading_replace(m):
        title = m.group(2).strip()
        return f"\n(( {title} ))\n"

    text = re.sub(r"^(#{1,6})\s+(.+)$", _heading_replace, text, flags=re.MULTILINE)

    # 4) __굵게__ → **굵게** 통일 (코난 챗봇 지원 형식)
    text = re.sub(r"__(.+?)__", r"**\1**", text)

    # 5) 기울임 (*text*, _text_) → 그냥 텍스트
    #    주의: **bold** 내부의 *는 건드리지 않음
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", text)

    # 6) 취소선 (~~text~~) → 텍스트
    text = re.sub(r"~~(.+?)~~", r"\1", text)

    # 7) 링크 [텍스트](URL) → 텍스트
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)

    # 8) 이미지 ![alt](url) → 제거
    text = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", "", text)

    # 9) 인용문 (> ...) → 들여쓰기
    text = re.sub(r"^>\s?(.*)$", r"  \1", text, flags=re.MULTILINE)

    # 10) 수평선 (---, ***, ___) → 구분선
    text = re.sub(r"^[-*_]{3,}\s*$", "─" * 20, text, flags=re.MULTILINE)

    # 11) 마크다운 표 → 개조식 (기존 함수 활용)
    text = convert_table_to_bullet(text)

    # 12) HTML 태그 처리
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE)
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)

    # 13) 불릿+볼드 결합 패턴 → 소제목으로 분리
    #     "• **제목**" 또는 "- **제목**" (줄 전체가 불릿+볼드만) → (( 제목 ))
    text = re.sub(
        r"^[•\-\*]\s*\*\*(.+?)\*\*\s*$",
        r"\n(( \1 ))",
        text,
        flags=re.MULTILINE,
    )

    # 14) (( 섹션 )) 앞뒤에 빈 줄 보장 (가독성)
    text = re.sub(r"([^\n])\n\(\(", r"\1\n\n((", text)
    text = re.sub(r"\)\)\n([^\n])", r"))\n\n\1", text)

    # 15) 구분선이 제목과 같은 줄에 붙는 경우 분리
    text = re.sub(r"(─+)\s*\(\(", r"\1\n\n((", text)

    # 16) 연속 빈 줄 정리 (최대 2줄)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ========== MongoDB ==========
mongo_client: Optional[AsyncIOMotorClient] = None
mongo_db = None


async def init_mongodb():
    """MongoDB 초기화"""
    global mongo_client, mongo_db
    mongo_client = None
    mongo_db = None
    try:
        client = AsyncIOMotorClient(MONGO_URI)
        database = client[MONGO_DB]

        await database.conversation_logs.create_index(
            [("conversation_id", 1), ("timestamp", -1)]
        )
        await database.conversation_logs.create_index("user_id")
        await database.conversation_history.create_index(
            "conversation_id", unique=True
        )
        await _ensure_history_ttl_index(database)

        mongo_client = client
        mongo_db = database
        print(f"✅ MongoDB 초기화: {MONGO_URI}/{MONGO_DB}")
    except Exception as error:
        try:
            if "client" in locals():
                client.close()
        except Exception:
            pass
        mongo_client = None
        mongo_db = None
        print(
            json.dumps(
                {
                    "event": "mongodb_degraded_mode",
                    "error": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            )
        )


async def _ensure_history_ttl_index(database) -> None:
    """Create or safely update the existing conversation TTL index."""
    collection = database.conversation_history
    try:
        index_information = await collection.index_information()
    except (AttributeError, TypeError):
        await collection.create_index(
            "updated_at", expireAfterSeconds=HISTORY_TTL_SECONDS
        )
        return

    ttl_index = next(
        (
            (name, info)
            for name, info in index_information.items()
            if list(info.get("key", [])) == [("updated_at", 1)]
        ),
        None,
    )
    if ttl_index is None:
        await collection.create_index(
            "updated_at", expireAfterSeconds=HISTORY_TTL_SECONDS
        )
        return

    index_name, info = ttl_index
    if info.get("expireAfterSeconds") == HISTORY_TTL_SECONDS:
        return
    try:
        await database.command(
            {
                "collMod": "conversation_history",
                "index": {
                    "name": index_name,
                    "expireAfterSeconds": HISTORY_TTL_SECONDS,
                },
            }
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "event": "mongodb_ttl_update_skipped",
                    "error": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            )
        )


async def close_mongodb():
    """MongoDB 종료"""
    global mongo_client, mongo_db
    try:
        if mongo_client:
            mongo_client.close()
            print("🔌 MongoDB 연결 종료")
    except Exception as error:
        print(f"⚠️ MongoDB 종료 실패: {error}")
    finally:
        mongo_client = None
        mongo_db = None


async def save_full_log(
    conversation_id: str,
    user_id: str,
    message: str,
    team: Optional[str],
    week: Optional[str],
    tool_calls: List[Dict],
    tool_results: List[Dict],
    answer: str,
    references: List[Dict],
) -> None:
    """전체 대화 기록 저장"""
    if mongo_db is None:
        return

    existing_count = await mongo_db.conversation_logs.count_documents(
        {"conversation_id": conversation_id}
    )

    log_doc = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "timestamp": datetime.now(UTC),
        "turn": existing_count + 1,
        "request": {"message": message, "team": team, "week": week},
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "response": {"answer": answer, "references": references},
    }

    await mongo_db.conversation_logs.insert_one(log_doc)


async def save_history(
    conversation_id: str, user_message: str, assistant_answer: str
) -> None:
    """히스토리 저장"""
    if mongo_db is None:
        return

    messages_to_add = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_answer},
    ]

    await mongo_db.conversation_history.update_one(
        {"conversation_id": conversation_id},
        {
            "$push": {
                "messages": {
                    "$each": messages_to_add,
                    "$slice": -(MAX_HISTORY_TURNS * 2),
                }
            },
            "$set": {"updated_at": datetime.now(UTC)},
            "$setOnInsert": {"created_at": datetime.now(UTC)},
        },
        upsert=True,
    )


async def load_conversation_state(
    conversation_id: str,
) -> tuple[List[Dict], ConversationMemory, bool]:
    """Load bounded history and structured memory from one conversation document."""
    if mongo_db is None:
        return [], ConversationMemory(), False

    try:
        doc = await mongo_db.conversation_history.find_one(
            {"conversation_id": conversation_id}
        )
    except Exception:
        return [], ConversationMemory(), False
    if not doc:
        return [], ConversationMemory(), True

    try:
        memory = ConversationMemory.model_validate(doc.get("memory") or {})
    except Exception:
        memory = ConversationMemory()
    return doc.get("messages", []), memory, True


def build_next_conversation_memory(
    result: Dict[str, Any], previous: ConversationMemory
) -> ConversationMemory:
    """Build the bounded structured state persisted for the next turn."""
    contextualized = result.get("contextualized_turn") or {}
    follow_up_type = result.get("follow_up_type") or previous.follow_up_type
    if follow_up_type == "clarify":
        return previous.model_copy(update={"follow_up_type": "clarify"})
    is_new_topic = follow_up_type == "new_topic"

    standalone_question = result.get("standalone_question") or (
        None if is_new_topic else previous.standalone_question
    )
    answer = str(result.get("answer") or "")
    current_parts = [
        str(standalone_question or "")[:400],
        answer[:800],
    ]
    current_summary = "\n".join(part for part in current_parts if part)
    if is_new_topic:
        rolling_summary = current_summary[:2000]
    elif current_summary:
        separator = "\n" if previous.rolling_summary else ""
        prior_budget = max(0, 2000 - len(separator) - len(current_summary))
        bounded_previous = previous.rolling_summary[-prior_budget:] if prior_budget else ""
        rolling_summary = f"{bounded_previous}{separator if bounded_previous else ''}{current_summary}"
    else:
        rolling_summary = previous.rolling_summary

    cited_evidence = []
    for document in result.get("used_contexts") or []:
        document_id = document.get("document_id")
        if not document_id:
            continue
        metadata = document.get("metadata") or {}
        normalized_document_weeks = normalize_weeks(document.get("week"))
        cited_evidence.append(
            {
                "document_id": str(document_id),
                "source_type": str(document.get("source_type") or "mail"),
                "title": str(document.get("title") or "")[:300],
                "team": document.get("team"),
                "week": (
                    normalized_document_weeks[0]
                    if normalized_document_weeks
                    else None
                ),
                "snippet": str(
                    document.get("snippet")
                    or document.get("content")
                    or document.get("text")
                    or ""
                )[:500],
                "mail_id": metadata.get("mail_id") or document.get("mail_id"),
                "url": metadata.get("url") or document.get("url"),
                "html_path": metadata.get("html_path")
                or document.get("html_path"),
                "part_index": metadata.get("part_index", document.get("part_index")),
                "total_parts": metadata.get("total_parts", document.get("total_parts")),
            }
        )
        if len(cited_evidence) >= 8:
            break

    route = result.get("route") or (None if is_new_topic else previous.route)
    if route not in {"general", "statistics", "search"}:
        route = None
    return ConversationMemory.model_validate(
        {
            "schema_version": 1,
            "active_topic": contextualized.get("active_topic")
            or standalone_question
            or (None if is_new_topic else previous.active_topic),
            "standalone_question": standalone_question,
            "search_query": result.get("search_query")
            or (None if is_new_topic else previous.search_query),
            "route": route,
            "follow_up_type": follow_up_type,
            "teams": result.get("contextualized_teams")
            or ([] if is_new_topic else previous.teams),
            "weeks": normalize_weeks(result.get("week"))
            or ([] if is_new_topic else previous.weeks),
            "mail_type": result.get("mail_type")
            or (None if is_new_topic else previous.mail_type),
            "rolling_summary": rolling_summary,
            "cited_evidence": cited_evidence
            or ([] if is_new_topic else previous.cited_evidence),
        }
    )


async def save_conversation_turn(
    conversation_id: str,
    user_id: str,
    user_message: str,
    assistant_answer: str,
    memory: ConversationMemory,
) -> None:
    """Atomically append one turn and replace its bounded structured memory."""
    if mongo_db is None:
        return

    now = datetime.now(UTC)
    await mongo_db.conversation_history.update_one(
        {"conversation_id": conversation_id},
        {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": assistant_answer},
                    ],
                    "$slice": -(MAX_HISTORY_TURNS * 2),
                }
            },
            "$set": {
                "user_id": user_id,
                "memory": memory.model_dump(mode="json"),
                "schema_version": 1,
                "updated_at": now,
            },
            "$setOnInsert": {
                "conversation_id": conversation_id,
                "created_at": now,
            },
        },
        upsert=True,
    )


async def get_history(conversation_id: str) -> List[Dict]:
    """대화 히스토리 조회"""
    if mongo_db is None:
        return []

    doc = await mongo_db.conversation_history.find_one(
        {"conversation_id": conversation_id}
    )
    if not doc:
        return []

    return doc.get("messages", [])


# ========== FastAPI ==========
os_client: Optional[OpenSearchClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 초기화"""
    global os_client
    os_client = OpenSearchClient()
    print(f"✅ OpenSearch: {os_client.health_check()}")
    print(f"📊 인덱스: {os_client.get_stats()}")
    await init_mongodb()
    yield
    await close_mongodb()


app = FastAPI(
    title="Weekly Mail RAG Chatbot API (Naive RAG + LLM Router v3)",
    description="""주간 메일 기반 Q&A API - LangGraph Naive RAG + LLM Router

## 주요 기능
- LLM 기반 질문 유형 분류 (search/statistics/general)
- OpenSearch 하이브리드 검색
- 통계 Tool Binding
- 멀티턴 대화 지원

## API
- POST /chat/v2 - 채팅
- GET /stats/* - 통계 조회
- GET /health - 헬스체크
""",
    version="3.1.0",
    lifespan=lifespan,
)
app.include_router(knowledge_router)

MAIL_DIR.mkdir(exist_ok=True)
app.mount("/mail", StaticFiles(directory=str(MAIL_DIR)), name="mail")


@app.get("/health")
async def health():
    """헬스체크"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "opensearch": os_client.health_check() if os_client else None,
        "db_stats": os_client.get_stats() if os_client else None,
    }


@app.post("/chat/v2", response_model=ChatV2Response)
async def chat_v2(request: ChatV2Request):
    """Hybrid Agentic RAG 채팅 API"""
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    # conversation_id 미전송 시 자동 생성 (멀티턴 히스토리 보장)
    conversation_id = request.conversation_id or str(uuid.uuid4())

    # Agent 호출
    result = await chat_with_agent(
        message,
        team=request.team,
        week=request.week,
        conversation_id=conversation_id,
    )

    # 출처 추출
    references = []
    search_contexts = result.get("reranked_results", [])
    if not search_contexts:
        for tr in result["tool_results"]:
            if tr["name"] == "retrieve":
                try:
                    search_results = json.loads(tr["result"])
                    if isinstance(search_results, list):
                        search_contexts = search_results
                except (json.JSONDecodeError, TypeError):
                    pass
    answer = result["answer"]
    used_contexts = []
    if search_contexts:
        clean_answer, used_contexts = parse_used_references(answer, search_contexts)
        references = extract_references(used_contexts)
    else:
        clean_answer = answer

    # 후처리: 마크다운/HTML → 코난 챗봇 plain text (굵게만 사용)
    answer_converted = format_for_konan_chatbot(clean_answer)

    # MongoDB 저장 (conversation_id는 항상 존재)
    try:
        await save_full_log(
            conversation_id=conversation_id,
            user_id=request.user_id,
            message=message,
            team=request.team,
            week=request.week,
            tool_calls=result["tool_calls"],
            tool_results=result["tool_results"],
            answer=answer_converted,
            references=[ref.model_dump() for ref in references],
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "event": "conversation_log_write_failed",
                    "conversation_id": conversation_id,
                    "error": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            )
        )
    try:
        previous_memory = ConversationMemory.model_validate(
            result.get("conversation_memory") or {}
        )
    except Exception as error:
        previous_memory = ConversationMemory()
        print(
            json.dumps(
                {
                    "event": "conversation_memory_validation_failed",
                    "conversation_id": conversation_id,
                    "error": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            )
        )
    try:
        next_memory = build_next_conversation_memory(
            {**result, "answer": answer_converted, "used_contexts": used_contexts},
            previous_memory,
        )
    except Exception as error:
        next_memory = previous_memory
        print(
            json.dumps(
                {
                    "event": "conversation_memory_build_failed",
                    "conversation_id": conversation_id,
                    "error": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            )
        )
    try:
        await save_conversation_turn(
            conversation_id=conversation_id,
            user_id=request.user_id,
            user_message=message,
            assistant_answer=answer_converted,
            memory=next_memory,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "event": "conversation_state_write_failed",
                    "conversation_id": conversation_id,
                    "error": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            )
        )

    return ChatV2Response(
        conversation_id=conversation_id,
        answer=answer_converted,
        tool_calls=[ToolCallInfo(**tc) for tc in result["tool_calls"]],
        tool_results=[ToolResultInfo(**tr) for tr in result["tool_results"]],
        references=references,
    )


# ========== 통계 API ==========
@app.get("/stats/weekly-reports")
async def stats_weekly_reports(week: Optional[str] = None):
    """주간보고 팀별 통계"""
    try:
        counts = _count_weekly_reports_by_team(week=week)
        return {
            "type": "weekly_report",
            "week": week or "all",
            "by_team": counts,
            "total": sum(counts.values()),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats/other-mails")
async def stats_other_mails(week: Optional[str] = None, team: Optional[str] = None):
    """주간보고 외 메일 통계"""
    try:
        counts = _count_other_mails_by_team(week=week, team=team)
        return {
            "type": "other",
            "week": week or "all",
            "team": team or "all",
            "by_team": counts,
            "total": sum(counts.values()),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats/missing-teams")
async def stats_missing_teams(week: str):
    """주간보고 미제출 팀"""
    try:
        missing = _get_missing_teams(week=week)
        return {"week": week, "missing_teams": missing, "count": len(missing)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats/summary")
async def stats_summary(week: Optional[str] = None):
    """전체 메일 현황 요약"""
    try:
        summary = _get_mail_type_summary(week=week)
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/weeks")
async def get_weeks():
    """데이터가 있는 주차 목록"""
    try:
        weeks = _get_unique_weeks()
        return {"weeks": weeks, "count": len(weeks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/teams")
async def get_teams():
    """팀 목록"""
    return {"teams": TEAMS}


# ========== Deep Mining API ==========
from deep_mining_schemas import DeepMineRequest, DeepMineResponse
from fastapi.responses import FileResponse
import uuid

# 작업 상태 저장 (in-memory)
_deep_mine_jobs: Dict[str, DeepMineResponse] = {}
_deep_mine_lock = asyncio.Lock()


async def _run_deep_mining(job_id: str, request: DeepMineRequest):
    """백그라운드에서 deep mining 실행"""
    try:
        async with _deep_mine_lock:
            _deep_mine_jobs[job_id].status = "processing"
            _deep_mine_jobs[job_id].progress = 0.1

        from deep_mining import run_deep_mining_pipeline
        result = await run_deep_mining_pipeline(
            query=request.query,
            os_client=os_client,
            teams=[request.team] if request.team else None,
            week_from=request.week_from,
            week_to=request.week_to,
            mail_type=request.mail_type,
            num_slides=request.num_slides,
            job_id=job_id,
            progress_callback=lambda p: _update_progress(job_id, p),
        )

        async with _deep_mine_lock:
            job = _deep_mine_jobs[job_id]
            job.status = "completed"
            job.progress = 1.0
            job.text_summary = result["text_summary"]
            job.pptx_url = f"/deep-mine/{job_id}/download"
            job.document_count = result["document_count"]

    except Exception as e:
        async with _deep_mine_lock:
            job = _deep_mine_jobs[job_id]
            job.status = "failed"
            job.error = str(e)


def _update_progress(job_id: str, progress: float):
    """진행률 업데이트 (동기 콜백)"""
    if job_id in _deep_mine_jobs:
        _deep_mine_jobs[job_id].progress = progress


@app.post("/deep-mine", response_model=DeepMineResponse)
async def start_deep_mine(request: DeepMineRequest):
    """Deep Mining 작업 시작 — job_id 즉시 반환"""
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    job_id = str(uuid.uuid4())[:8]
    _deep_mine_jobs[job_id] = DeepMineResponse(
        job_id=job_id, status="accepted", progress=0.0
    )

    asyncio.create_task(_run_deep_mining(job_id, request))

    return _deep_mine_jobs[job_id]


@app.get("/deep-mine/{job_id}", response_model=DeepMineResponse)
async def get_deep_mine_status(job_id: str):
    """Deep Mining 작업 상태 조회"""
    if job_id not in _deep_mine_jobs:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    return _deep_mine_jobs[job_id]


@app.get("/deep-mine/{job_id}/download")
async def download_deep_mine(job_id: str):
    """Deep Mining PPTX 파일 다운로드"""
    if job_id not in _deep_mine_jobs:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")

    job = _deep_mine_jobs[job_id]
    if job.status != "completed":
        raise HTTPException(status_code=400, detail=f"작업 상태: {job.status}")

    pptx_path = DEEP_MINING_OUTPUT_DIR / f"{job_id}.pptx"
    if not pptx_path.exists():
        raise HTTPException(status_code=404, detail="PPTX 파일이 없습니다")

    return FileResponse(
        path=str(pptx_path),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"deep_mining_{job_id}.pptx",
    )


mount_knowledge_web(app)


# ========== 실행 ==========
if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("RAG Chatbot API Server (Hybrid Agentic RAG v3)")
    print("=" * 50)
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Docs: http://localhost:{PORT}/docs")
    print(f"  OpenSearch: {OPENSEARCH_HOST}:{OPENSEARCH_PORT}")
    print(f"  Index: {INDEX_NAME}")
    print("  Graph: router -> plan/search/grade loop -> answer/evaluate/finalize")
    print("=" * 50)

    uvicorn.run(app, host=HOST, port=PORT)

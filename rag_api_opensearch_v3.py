"""
RAG Chatbot API Server (OpenSearch 버전) - v3 Naive RAG + LLM Router
- FastAPI 기반 REST API 서버
- OpenSearch 하이브리드 검색 (벡터 + 키워드)
- LangGraph Naive RAG + LLM Router 구조
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
from pathlib import Path
from typing import List, Dict, Optional, Any, Annotated, Sequence, Literal, TypedDict
from datetime import datetime, timedelta, UTC
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
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

# 주제별 타임라인 설정
TOPIC_TIMELINE_OUTPUT_DIR = Path(
    os.getenv("TOPIC_TIMELINE_OUTPUT_DIR", "exports/topic_timeline")
)

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
class GraphState(TypedDict):
    """Naive RAG 상태"""

    question: str  # 사용자 질문
    search_query: str  # 컨텍스트 반영된 독립 검색 쿼리 (Router가 생성)
    context: str  # 검색/통계 결과 (컨텍스트)
    answer: str  # 최종 답변
    messages: Annotated[list, add_messages]  # 대화 히스토리
    route: str  # 라우팅 결과 (search/statistics/general)
    mail_type: Optional[str]  # 메일 유형 필터 (weekly_report/daily_report/None)
    week: Optional[List[str]]  # 주차 필터 (단일 ["2025-30"] 또는 복수 ["2025-5","2025-6","2025-7"])


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
- "=== Wiki 요약 ===" : 사전에 정리된 팀별/주차별 핵심 요약입니다. [요약 N]으로 표기됩니다. 전체적인 맥락과 핵심 내용을 파악하는 데 우선 활용하세요.
- "=== 메일 검색 결과 ===" : 사내 주간업무보고 메일에서 검색된 원문 문서입니다. [문서 N]으로 표기됩니다. 상세한 근거와 수치 확인에 활용하세요.
- "=== 배경지식 ===" : 반도체 공정/장비/기술 관련 사내 기술문서(SYLD GPT) 데이터베이스에서 검색된 참고 자료입니다. [참고 N]으로 표기됩니다.

## 답변 작성 방법
- Wiki 요약([요약 N])으로 전체 맥락을 먼저 파악하고, 메일 원문([문서 N])으로 상세 내용을 보충하세요.
- 배경지식([참고 N])은 메일 내용의 기술적 맥락을 보충하거나, 용어/공정을 설명할 때 활용하세요.
- 세 출처를 자연스럽게 결합하여 답변하되, 어느 출처에서 왔는지 구분 가능하도록 표기하세요.

## 금지 사항
- 컨텍스트에 명시되지 않은 정보를 답변에 포함하지 마세요.
- 일반 지식, 추측, 유추를 섞지 마세요.
- 불확실한 내용을 확정적으로 말하지 마세요.
- 답을 모르면 솔직히 "해당 정보가 문서에 없습니다"라고 하세요.

## 출처 표시 규칙
- Wiki 요약 인용 시: [요약 N] 형식으로 표시하세요.
- 메일 내용 인용 시: [문서 N] 형식으로 표시하세요.
- 배경지식 인용 시: [참고 N] 형식으로 표시하세요.
- 출처 태그는 **실제로 해당 문서에서 직접 가져온 내용에만** 붙이세요.
- 출처가 불명확하거나 여러 문서를 종합한 내용은 출처를 붙이지 마세요.
- 답변 마지막에 "참고: [요약 1], [문서 2], [참고 3]" 형태로 사용한 출처를 명시하세요.

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
def router_node(state: GraphState) -> Dict[str, Any]:
    """Router 노드: LLM이 질문 유형(route) + 메일 유형(mail_type) 동시 판단"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    history_messages = state.get("messages", [])
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
현재 질문: {question}

출력:"""

    route = "general"
    mail_type = None  # None이면 전체 검색
    week = None  # None이면 주차 필터 없음

    try:
        llm = get_llm()
        response = llm.invoke([HumanMessage(content=simple_prompt)])
        answer = response.content.strip().lower()

        # route 파싱
        if "route:" in answer:
            route_line = [l for l in answer.split("\n") if "route:" in l]
            if route_line:
                route_part = route_line[0].split("route:")[-1].strip()
                if "search" in route_part:
                    route = "search"
                elif "statistic" in route_part:
                    route = "statistics"
                else:
                    route = "general"
        else:
            # fallback: 전체 응답에서 키워드 찾기
            if "search" in answer:
                route = "search"
            elif "statistic" in answer:
                route = "statistics"

        # mail_type 파싱
        if "mail_type:" in answer:
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
                    week = week_matches  # List[str]
                # "none"이면 None 유지

        # search_query 파싱
        search_query = question  # 기본값: 원문 그대로
        if "search_query:" in answer:
            sq_line = [l for l in answer.split("\n") if "search_query:" in l]
            if sq_line:
                sq_part = sq_line[0].split("search_query:", 1)[-1].strip()
                if sq_part and sq_part.lower() != "none":
                    search_query = sq_part

        _elapsed = (_time.time() - _t_start) * 1000
        week_display = ",".join(week) if week else "none"
        print(f"🔀 [Router] 분류 결과: route={route}, mail_type={mail_type}, week={week_display}")
        print(f"🔀 [Router] search_query: {search_query[:80]}")
        print(f"   LLM 응답: {answer[:120]}...")
        print(f"   ⏱️ {_elapsed:.0f}ms")

    except Exception as e:
        print(f"⚠️ [Router] 분류 실패, 기본값 사용: {e}")
        route = "general"
        mail_type = None
        week = None
        search_query = question  # fallback: 원문

    return {"route": route, "mail_type": mail_type, "week": week, "search_query": search_query}


def route_question(
    state: GraphState,
) -> Literal["retrieve", "statistics", "llm_answer"]:
    """라우팅 함수: route 값에 따라 다음 노드 결정"""
    route = state.get("route", "general")
    if route == "search":
        return "retrieve"
    elif route == "statistics":
        return "statistics"
    else:
        return "llm_answer"


def retrieve_document(state: GraphState) -> Dict[str, Any]:
    """Retrieve 노드: OpenSearch 검색"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    search_query = state.get("search_query") or question  # fallback: 원문
    mail_type = state.get("mail_type")  # 메일 유형 필터
    week = state.get("week")  # 주차 필터 (Router가 추출, List[str] 또는 None)
    print(f"🔍 [Retrieve] 원문 질문: {question[:50]}...")
    print(f"🔍 [Retrieve] 검색 쿼리: {search_query[:50]}...")
    if mail_type:
        print(f"📧 [Retrieve] 메일 유형 필터: {mail_type}")
    if week:
        week_display = ",".join(week) if isinstance(week, list) else week
        print(f"📅 [Retrieve] 주차 필터: {week_display} ({len(week) if isinstance(week, list) else 1}개 주차)")

    if not os_client:
        print("⚠️ [Retrieve] OpenSearch 클라이언트 없음")
        return {"context": ""}

    try:
        # ===== Tier 1: Wiki 요약 검색 =====
        wiki_results = []
        try:
            _t_wiki_start = _time.time()
            wiki_results = os_client.search_wiki(
                search_query,
                team=None,
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
            team=None,
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
            return {"context": ""}

        context_parts = []

        # Wiki 요약 결과 (Tier 1 - 사전 정리된 고수준 요약)
        if wiki_results:
            context_parts.append("\n=== Wiki 요약 (사전 정리된 핵심 요약) ===")
            for i, ctx in enumerate(wiki_results, 1):
                wiki_meta = []
                if ctx.get("team"):
                    wiki_meta.append(f"팀: {ctx['team']}")
                if ctx.get("week"):
                    wiki_meta.append(f"주차: {ctx['week']}")
                meta_str = ", ".join(wiki_meta) if wiki_meta else ""
                context_parts.append(
                    f"\n[요약 {i}] {ctx.get('title', '')}\n"
                    f"{meta_str}\n"
                    f"내용:\n{ctx.get('text', '')}\n"
                    f"{'-' * 40}"
                )

        # 메일 검색 결과 (Tier 2 - 원문 상세)
        if formatted:
            context_parts.append("\n=== 메일 검색 결과 ===")
            for i, ctx in enumerate(formatted, 1):
                context_parts.append(
                    f"\n[문서 {i}]\n"
                    f"팀: {ctx.get('team', 'unknown')}\n"
                    f"주차: {ctx.get('week', 'unknown')}\n"
                    f"내용:\n{ctx.get('text', '')}\n"
                    f"{'-' * 40}"
                )

        # 배경지식 결과 (Tier 3)
        if secondary_results:
            context_parts.append("\n=== 배경지식 ===")
            for i, ctx in enumerate(secondary_results, 1):
                text = ctx["text"][:MAX_TEXT_PER_DOC] if len(ctx["text"]) > MAX_TEXT_PER_DOC else ctx["text"]
                context_parts.append(
                    f"\n[참고 {i}]\n"
                    f"내용:\n{text}\n"
                    f"{'-' * 40}"
                )

        context_text = "".join(context_parts)

        # 검색 결과 JSON도 저장 (출처 추출용)
        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [Retrieve] 총 소요시간: {_total_elapsed:.0f}ms")
        return {
            "context": context_text,
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
        return {"context": ""}


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


def statistics_node(state: GraphState) -> Dict[str, Any]:
    """Statistics 노드: LLM + Tool Binding으로 통계 함수 호출"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    history_messages = state.get("messages", [])
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
                tool_args = tc.get("args", {})
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
                    result = get_mail_type_summary.invoke({})
                    tool_results.append(
                        {"name": "get_mail_type_summary", "result": result}
                    )
            else:
                result = get_mail_type_summary.invoke({})
                tool_results.append({"name": "get_mail_type_summary", "result": result})

        if tool_results:
            # 결과를 context로 변환
            context = "\n\n".join(
                [f"[{tr['name']}]\n{tr['result']}" for tr in tool_results]
            )

            _total_elapsed = (_time.time() - _t_start) * 1000
            print(f"⏱️ [Statistics] 총 소요시간: {_total_elapsed:.0f}ms")
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
            }
        else:
            print("⚠️ [Statistics] Tool 결과 없음")
            _total_elapsed = (_time.time() - _t_start) * 1000
            print(f"⏱️ [Statistics] 총 소요시간: {_total_elapsed:.0f}ms")
            return {"context": ""}

    except Exception as e:
        print(f"❌ [Statistics] 통계 조회 실패: {e}")
        import traceback

        traceback.print_exc()
        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [Statistics] 총 소요시간: {_total_elapsed:.0f}ms")
        return {"context": ""}


def llm_answer_node(state: GraphState) -> Dict[str, Any]:
    """LLM Answer 노드: 최종 답변 생성"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    context = state.get("context", "")
    history_messages = state.get("messages", [])

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

    if route == "search" and context:
        user_prompt = f"""질문: {question}

참고 정보:
{context}

위 정보를 바탕으로 아래 구조에 맞춰 답변해주세요.

## 답변 구조 (반드시 이 순서와 형식을 따르세요)

(( 요약 ))
질문에 대한 핵심 답변을 3~5줄로 요약하세요.

(( 상세 설명 ))
요약에서 언급한 내용을 구체적으로 설명하세요.
• 개조식(•)으로 항목별 정리
• 팀명, 수치 등 핵심 키워드는 **굵게** 표시
• 각 항목에 출처 [문서 N] 또는 [참고 N] 표시

(( 핵심 결론 ))
전체 내용을 1~2줄로 마무리하세요.

## 주의
- 섹션 제목은 반드시 (( 요약 )), (( 상세 설명 )), (( 핵심 결론 )) 형태로 작성하세요.
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
            "messages": [
                HumanMessage(content=question),
                AIMessage(content=answer),
            ],
        }

    except Exception as e:
        print(f"❌ [LLM Answer] 답변 생성 실패: {e}")
        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [LLM Answer] 총 소요시간: {_total_elapsed:.0f}ms")
        return {
            "answer": "답변 생성 중 오류가 발생했습니다. 다시 시도해주세요.",
            "messages": [],
        }


# ===== 그래프 빌드 =====
_naive_rag_graph = None


def get_naive_rag_graph():
    """Naive RAG + LLM Router 그래프 (싱글톤)"""
    global _naive_rag_graph
    if _naive_rag_graph is not None:
        return _naive_rag_graph

    workflow = StateGraph(GraphState)

    # 노드 추가
    workflow.add_node("router", router_node)
    workflow.add_node("retrieve", retrieve_document)
    workflow.add_node("statistics", statistics_node)
    workflow.add_node("llm_answer", llm_answer_node)

    # 엣지 연결
    workflow.add_edge(START, "router")

    # router → 3분기 (search/statistics/general)
    workflow.add_conditional_edges(
        "router",
        route_question,
        {
            "retrieve": "retrieve",
            "statistics": "statistics",
            "llm_answer": "llm_answer",
        },
    )

    # retrieve, statistics → llm_answer
    workflow.add_edge("retrieve", "llm_answer")
    workflow.add_edge("statistics", "llm_answer")

    # llm_answer → END
    workflow.add_edge("llm_answer", END)

    _naive_rag_graph = workflow.compile()
    # _naive_rag_graph = workflow.compile(checkpointer=MemorySaver())
    print("✅ Naive RAG + LLM Router 그래프 컴파일 완료")
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
    """Naive RAG + LLM Router 기반 채팅"""
    import time

    t_start = time.time()
    print(f"🚀 [chat_with_agent] 시작: {user_message[:50]}...")

    # 그래프 가져오기
    graph = get_naive_rag_graph()

    # 대화 히스토리 조회
    history = []
    if conversation_id:
        history = await get_history(conversation_id)
    history_messages = _convert_history_to_messages(history)

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
                    "search_query": "",  # Router가 채워줌
                    "context": "",
                    "answer": "",
                    "messages": history_messages,
                    "route": "",
                    "mail_type": None,
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

    # Knowledge Accumulation: 검색 기반 양질의 답변을 wiki에 축적
    if (
        result.get("route") == "search"
        and answer
        and len(answer) > 200
        and "[문서" in answer  # 실제 문서를 참조한 답변만
        and os_client
    ):
        try:
            from wiki_builder import accumulate_query_result, get_embedding_client as _get_embed_client

            _embed_client = _get_embed_client()
            _parsed_results = json.loads(tool_results[0]["result"]) if tool_results else []
            _source_chunks_text = "\n\n---\n\n".join(
                r.get("text", "") for r in _parsed_results if r.get("text")
            )
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: accumulate_query_result(
                    os_client=os_client.client,
                    embed_client=_embed_client,
                    question=user_message,
                    answer=answer,
                    source_teams=list(set(
                        r.get("team") for r in _parsed_results
                        if r.get("team")
                    )) or None,
                    source_weeks=list(set(
                        r.get("week") for r in _parsed_results
                        if r.get("week")
                    )) or None,
                    source_chunks=_source_chunks_text or None,
                ),
            )
            print("📖 [Knowledge Accumulation] 비동기 저장 시작")
        except Exception as e:
            print(f"⚠️ [Knowledge Accumulation] 실패 (무시): {e}")

    return {
        "answer": answer,
        "tool_calls": tool_calls_info,
        "tool_results": tool_results,
    }


# ========== 출처 처리 ==========
def parse_used_references(answer: str, contexts: List[Dict]) -> tuple:
    """LLM 답변에서 실제 참고한 문서만 추출"""
    pattern = r"\[문서\s*(\d+)\]"
    matches = re.findall(pattern, answer)

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
        team = ctx.get("team", "unknown")
        week = ctx.get("week", "unknown")
        mail_id = ctx.get("mail_id", "unknown")

        key = f"{team}_{week}_{mail_id}"
        if key in seen:
            continue
        seen.add(key)

        filename = f"{week}_{team}_{mail_id}.html"
        url = f"{API_BASE_URL}/mail/{quote(filename, safe='')}"
        refs.append(
            Reference(
                team=team,
                week=week,
                mail_id=mail_id,
                url=url,
                score=ctx.get("score", 0),
                part_index=ctx.get("part_index"),
                total_parts=ctx.get("total_parts"),
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
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    mongo_db = mongo_client[MONGO_DB]

    await mongo_db.conversation_logs.create_index(
        [("conversation_id", 1), ("timestamp", -1)]
    )
    await mongo_db.conversation_logs.create_index("user_id")
    await mongo_db.conversation_history.create_index("conversation_id", unique=True)
    await mongo_db.conversation_history.create_index(
        "updated_at", expireAfterSeconds=HISTORY_TTL_SECONDS
    )

    print(f"✅ MongoDB 초기화: {MONGO_URI}/{MONGO_DB}")


async def close_mongodb():
    """MongoDB 종료"""
    global mongo_client
    if mongo_client:
        mongo_client.close()
        print("🔌 MongoDB 연결 종료")


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
    """Naive RAG + LLM Router 채팅 API"""
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    # conversation_id 미전송 시 자동 생성 (멀티턴 히스토리 보장)
    conversation_id = request.conversation_id or f"auto_{request.user_id}"

    # Agent 호출
    result = await chat_with_agent(
        message,
        team=request.team,
        week=request.week,
        conversation_id=conversation_id,
    )

    # 출처 추출
    references = []
    search_contexts = []
    for tr in result["tool_results"]:
        if tr["name"] == "retrieve":
            try:
                search_results = json.loads(tr["result"])
                if isinstance(search_results, list):
                    search_contexts = search_results
            except (json.JSONDecodeError, TypeError):
                pass
    answer = result["answer"]
    if search_contexts:
        clean_answer, used_contexts = parse_used_references(answer, search_contexts)
        references = extract_references(used_contexts)
    else:
        clean_answer = answer

    # 후처리: 마크다운/HTML → 코난 챗봇 plain text (굵게만 사용)
    answer_converted = format_for_konan_chatbot(clean_answer)

    # MongoDB 저장 (conversation_id는 항상 존재)
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
    await save_history(
        conversation_id=conversation_id,
        user_message=message,
        assistant_answer=answer_converted,
    )

    return ChatV2Response(
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


# ========== 주제별 타임라인 API ==========
from generate_topic_timeline import (
    TopicTimelineRequest,
    TopicTimelineResponse,
    generate_topic_timeline,
)

# 작업 상태 저장 (in-memory)
_topic_timeline_jobs: Dict[str, TopicTimelineResponse] = {}
_topic_timeline_lock = asyncio.Lock()


def _update_timeline_progress(job_id: str, progress: float):
    """진행률 업데이트 (동기 콜백)"""
    if job_id in _topic_timeline_jobs:
        _topic_timeline_jobs[job_id].progress = progress


async def _run_topic_timeline(job_id: str, request: TopicTimelineRequest):
    """백그라운드에서 주제별 타임라인 생성"""
    try:
        async with _topic_timeline_lock:
            _topic_timeline_jobs[job_id].status = "processing"
            _topic_timeline_jobs[job_id].progress = 0.05

        # 블로킹(검색 + LLM) 작업이므로 스레드로 오프로드
        result = await asyncio.to_thread(
            generate_topic_timeline,
            request.topic,
            os_client,
            llm=get_llm(),
            week_from=request.week_from,
            week_to=request.week_to,
            team=request.team,
            k_per_week=request.k_per_week,
            output_dir=TOPIC_TIMELINE_OUTPUT_DIR,
            job_id=job_id,
            progress_callback=lambda p: _update_timeline_progress(job_id, p),
        )

        # PPTX 생성 (best-effort) — pptdaddy 미설치 등 실패해도 md/html 은 유지
        pptx_ok = False
        try:
            from topic_timeline_ppt import build_topic_timeline_pptx
            pptx_path = str(TOPIC_TIMELINE_OUTPUT_DIR / f"{job_id}.pptx")
            await asyncio.to_thread(build_topic_timeline_pptx, result, pptx_path)
            pptx_ok = True
        except Exception as e:
            print(f"[topic-timeline] PPTX 생성 건너뜀: {type(e).__name__}: {e}")

        async with _topic_timeline_lock:
            job = _topic_timeline_jobs[job_id]
            job.status = "completed"
            job.progress = 1.0
            job.topic = result["topic"]
            job.markdown_url = f"/topic-timeline/{job_id}/download?format=md"
            job.html_url = f"/topic-timeline/{job_id}/download?format=html"
            if pptx_ok:
                job.pptx_url = f"/topic-timeline/{job_id}/download?format=pptx"
            job.overview = result["overview"]
            job.weeks_total = result["weeks_total"]
            job.weeks_covered = result["weeks_covered"]
            job.document_count = result["document_count"]

    except Exception as e:
        async with _topic_timeline_lock:
            job = _topic_timeline_jobs[job_id]
            job.status = "failed"
            job.error = str(e)


@app.post("/topic-timeline", response_model=TopicTimelineResponse)
async def start_topic_timeline(request: TopicTimelineRequest):
    """주제별 타임라인 작업 시작 — job_id 즉시 반환.

    원하는 주제를 모든 주차에서 검색해 시간순 리포트로 정리한다.
    완료 후 /topic-timeline/{job_id}/download 로 md/html 다운로드.
    """
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    job_id = str(uuid.uuid4())[:8]
    _topic_timeline_jobs[job_id] = TopicTimelineResponse(
        job_id=job_id, status="accepted", topic=request.topic, progress=0.0
    )

    asyncio.create_task(_run_topic_timeline(job_id, request))

    return _topic_timeline_jobs[job_id]


@app.get("/topic-timeline/{job_id}", response_model=TopicTimelineResponse)
async def get_topic_timeline_status(job_id: str):
    """주제별 타임라인 작업 상태/결과 조회"""
    if job_id not in _topic_timeline_jobs:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    return _topic_timeline_jobs[job_id]


_TIMELINE_MEDIA_TYPES = {
    "md": "text/markdown",
    "html": "text/html",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@app.get("/topic-timeline/{job_id}/download")
async def download_topic_timeline(job_id: str, format: str = "md"):
    """주제별 타임라인 리포트 다운로드 (format=md | html | pptx)"""
    if job_id not in _topic_timeline_jobs:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")

    job = _topic_timeline_jobs[job_id]
    if job.status != "completed":
        raise HTTPException(status_code=400, detail=f"작업 상태: {job.status}")

    fmt = format.lower()
    if fmt not in _TIMELINE_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="format은 md, html, pptx 중 하나")

    file_path = TOPIC_TIMELINE_OUTPUT_DIR / f"{job_id}.{fmt}"
    if not file_path.exists():
        detail = "PPTX가 생성되지 않았습니다 (pptdaddy 미설치 등)" if fmt == "pptx" else "리포트 파일이 없습니다"
        raise HTTPException(status_code=404, detail=detail)

    return FileResponse(
        path=str(file_path),
        media_type=_TIMELINE_MEDIA_TYPES[fmt],
        filename=f"topic_timeline_{job_id}.{fmt}",
    )


# ========== 실행 ==========
if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("RAG Chatbot API Server (Naive RAG + LLM Router v3)")
    print("=" * 50)
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Docs: http://localhost:{PORT}/docs")
    print(f"  OpenSearch: {OPENSEARCH_HOST}:{OPENSEARCH_PORT}")
    print(f"  Index: {INDEX_NAME}")
    print("  Graph: START -> router -> (retrieve|statistics|llm_answer) -> END")
    print("=" * 50)

    uvicorn.run(app, host=HOST, port=PORT)

"""
RAG Chatbot API Server (OpenSearch 버전)
- 사내 메신저 API 연동을 위한 REST API 서버
- FastAPI 기반
- OpenSearch 하이브리드 검색 (벡터 + 키워드 가중치 조절) + LLM 답변 생성
- LangGraph ReAct Agent 기반 Tool Calling 지원
- MongoDB 기반 멀티턴 대화 히스토리 관리
"""

import os
import re
import json
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Any, Annotated, Sequence, TypedDict, Literal
from datetime import datetime
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
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
from langgraph.prebuilt import tools_condition
from langgraph.prebuilt import ToolNode
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
    get_unique_teams,
    get_unique_weeks as _get_unique_weeks,
)

load_dotenv()

# ========== 설정 ==========
# OpenSearch 설정
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "rlaeorka1!K")
OPENSEARCH_USE_SSL = "true"
INDEX_NAME = os.getenv("OPENSEARCH_INDEX", "weekly_mail")

# 임베딩 설정
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "")
EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
EMBEDDING_DIMENSION = 4096

# LLM 설정
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss-120b")

# MongoDB 설정
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "weekly_mail_agent")
HISTORY_TTL_SECONDS = int(os.getenv("HISTORY_TTL_SECONDS", "1800"))  # 30분
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))  # 최근 10턴
MAX_ANSWER_LENGTH = int(os.getenv("MAX_ANSWER_LENGTH", "1000"))  # 답변 압축 길이

# 대화 요약 설정
SUMMARY_THRESHOLD = int(os.getenv("SUMMARY_THRESHOLD", "3"))  # 3턴 초과 시 요약 트리거
MAX_SUMMARY_TOKENS = int(os.getenv("MAX_SUMMARY_TOKENS", "300"))  # 요약 최대 토큰
RECENT_TURNS_TO_KEEP = int(
    os.getenv("RECENT_TURNS_TO_KEEP", "2")
)  # 요약 없이 유지할 최근 턴 수

# 토큰 설정 (256K 모델 기준)
MAX_TOOL_RESULT_TOKENS = int(
    os.getenv("MAX_TOOL_RESULT_TOKENS", "150000")
)  # tool 결과용 토큰 예산
SEARCH_RESULT_LIMIT = int(os.getenv("SEARCH_RESULT_LIMIT", "50"))  # 검색 결과 개수
MAX_TEXT_PER_DOC = int(os.getenv("MAX_TEXT_PER_DOC", "2000"))  # 문서당 텍스트 길이 제한
ENCODING_NAME = "cl100k_base"  # GPT-4/3.5 호환 인코딩
MAX_SEARCH_CALLS = int(
    os.getenv("MAX_SEARCH_CALLS", "2")
)  # search_mail_content 최대 호출
MAX_REWRITE_COUNT = int(os.getenv("MAX_REWRITE_COUNT", "1"))  # query rewrite 최대 횟수
GRAPH_RECURSION_LIMIT = int(os.getenv("GRAPH_RECURSION_LIMIT", "15"))

# 서버 설정
HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "8002"))
# API 베이스 URL (외부 접근용, 환경변수로 재정의 가능)
API_BASE_URL = os.getenv("API_BASE_URL", f"http://localhost:{PORT}")

# 데이터 디렉토리
DATA_DIR = Path("data")
MAIL_DIR = Path("mail")  # body.html 모아두는 폴더 (정적 파일 서빙용)

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
class ChatRequest(BaseModel):
    """채팅 요청

    가중치 설정 예시:
    - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
    - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
    - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값, 추천)
    """

    user_id: str
    message: str
    team: Optional[str] = Field(default=None, description="팀 필터")
    week: Optional[str] = Field(default=None, description="주차 필터 (예: 2025-48)")
    vector_weight: float = Field(
        default=0.7, ge=0.0, le=1.0, description="벡터 검색 가중치"
    )
    keyword_weight: float = Field(
        default=0.3, ge=0.0, le=1.0, description="키워드 검색 가중치"
    )


class Reference(BaseModel):
    """참조 출처"""

    team: str
    week: str
    mail_id: str
    url: str
    score: float
    part_index: Optional[int] = None
    total_parts: Optional[int] = None


class ChatResponse(BaseModel):
    """채팅 응답"""

    answer: str
    references: List[Reference]
    vector_weight: float
    keyword_weight: float


class SearchRequest(BaseModel):
    """검색 요청 (채팅 없이 검색만)"""

    query: str
    team: Optional[str] = None
    week: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=20)
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    keyword_weight: float = Field(default=0.3, ge=0.0, le=1.0)


class SearchResult(BaseModel):
    """검색 결과"""

    score: float
    text: str
    team: str
    week: str
    mail_id: str
    html_path: str
    part_index: Optional[int] = None
    total_parts: Optional[int] = None
    vector_score: Optional[float] = None
    keyword_score: Optional[float] = None


# ========== 토큰 계산 ==========
def count_tokens(text: str) -> int:
    """텍스트의 토큰 수 계산"""
    try:
        encoding = tiktoken.get_encoding(ENCODING_NAME)
        return len(encoding.encode(text))
    except Exception:
        # fallback: 대략 4자당 1토큰으로 추정
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
        self.embedding_client = self._get_embedding_client()
        self.index_name = INDEX_NAME

    def _get_embedding_client(self) -> OpenAI:
        """OpenAI 임베딩 클라이언트"""
        api_key = OPENROUTER_API_KEY
        base_url = OPENROUTER_BASE_URL
        return OpenAI(api_key=api_key, base_url=base_url)

    def _get_embedding(self, text: str) -> List[float]:
        """텍스트를 임베딩 벡터로 변환"""
        max_chars = 8000
        if len(text) > max_chars:
            text = text[:max_chars]

        response = self.embedding_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
        )
        return response.data[0].embedding

    def _search_vector(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """벡터 유사도 검색 (의미 기반) - 내부 사용"""
        query_embedding = self._get_embedding(query)

        # 필터 구성
        filters = []
        if team:
            filters.append({"term": {"team": team}})
        if week:
            filters.append({"term": {"week": week}})

        # k-NN 검색 쿼리
        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [
                        {"knn": {"embedding": {"vector": query_embedding, "k": limit}}}
                    ],
                    "filter": filters if filters else [],
                }
            },
        }

        response = self.client.search(index=self.index_name, body=search_body)
        return self._parse_results(response)

    def _search_keyword(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """키워드 검색 (한국어 Nori 형태소 분석 기반 BM25) - 내부 사용"""
        # 필터 구성
        filters = []
        if team:
            filters.append({"term": {"team": team}})
        if week:
            filters.append({"term": {"week": week}})

        # BM25 키워드 검색
        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [
                        {"match": {"text": {"query": query, "analyzer": "korean"}}}
                    ],
                    "filter": filters if filters else [],
                }
            },
        }

        response = self.client.search(index=self.index_name, body=search_body)
        return self._parse_results(response)

    def search(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        limit: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict]:
        """네이티브 하이브리드 검색 (단일 쿼리로 벡터 + 키워드)

        OpenSearch에 1번의 요청으로 벡터/키워드 검색을 동시에 수행합니다.

        가중치 설정:
        - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
        - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
        - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값)
        """
        # 가중치 정규화
        total_weight = vector_weight + keyword_weight
        if total_weight > 0:
            vector_weight = vector_weight / total_weight
            keyword_weight = keyword_weight / total_weight
        else:
            vector_weight = 0.7
            keyword_weight = 0.3

        # 순수 키워드 검색 (벡터 가중치가 0인 경우)
        if vector_weight == 0:
            return self._search_keyword(query, team, week, limit)

        # 순수 벡터 검색 (키워드 가중치가 0인 경우)
        if keyword_weight == 0:
            return self._search_vector(query, team, week, limit)

        # 하이브리드 검색: 단일 쿼리로 knn + match 결합
        query_embedding = self._get_embedding(query)

        # 필터 구성
        filters = []
        if team:
            filters.append({"term": {"team": team}})
        if week:
            filters.append({"term": {"week": week}})

        # boost 값 계산 (상대적 가중치 반영)
        # BM25 점수가 보통 5~20 범위, knn 점수가 0~1 범위이므로 스케일 조정
        knn_boost = vector_weight * 10  # knn 점수 스케일업
        bm25_boost = keyword_weight

        # 네이티브 하이브리드 쿼리 (단일 요청)
        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "should": [
                        # 벡터 검색 (k-NN)
                        {
                            "knn": {
                                "embedding": {
                                    "vector": query_embedding,
                                    "k": limit,
                                    "boost": knn_boost,
                                }
                            }
                        },
                        # 키워드 검색 (BM25)
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

        response = self.client.search(index=self.index_name, body=search_body)
        return self._parse_results(response)

    def _parse_results(self, response: Dict) -> List[Dict]:
        """검색 결과 파싱"""
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

    def get_stats(self) -> Dict:
        """인덱스 통계"""
        try:
            count = self.client.count(index=self.index_name)
            return {
                "total_documents": count["count"],
                "index": self.index_name,
            }
        except Exception as e:
            return {"error": str(e)}

    def health_check(self) -> Dict:
        """OpenSearch 헬스체크"""
        try:
            info = self.client.info()
            return {
                "status": "ok",
                "distribution": info["version"]["distribution"],
                "version": info["version"]["number"],
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# ========== LLM 클라이언트 ==========
def get_llm():
    """LLM 클라이언트 (LangChain)"""
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        temperature=0.3,
        default_headers={
            "HTTP-Referer": "https://weekly-mail-agent.local",
            "X-Title": "Weekly Mail RAG Chatbot (OpenSearch)",
        },
    )


# ========== LangGraph Tool 정의 (@tool 데코레이터) ==========


@tool
def count_weekly_reports_by_team(week: Optional[str] = None) -> str:
    """주간보고 메일의 팀별 count를 조회합니다. 주간보고가 몇 개인지, 어떤 팀이 주간보고를 보냈는지 확인할 때 사용합니다.

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
    """일일보고(daily) 메일의 팀별 count를 조회합니다. 일일보고가 몇 개인지, 어떤 팀이 일일보고를 보냈는지 확인할 때 사용합니다.

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
    """주간보고 외(일반) 메일의 팀별 count를 조회합니다. 주간보고가 아닌 다른 메일이 몇 개인지 확인할 때 사용합니다.

    Args:
        week: 주차 필터 (예: 2025-48)
        team: 특정 팀 필터 (예: YIELD팀). 미지정시 전체 팀 조회
    """
    try:
        result = _count_other_mails_by_team(week=week, team=team)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_missing_teams(week: str) -> str:
    """주간보고를 제출하지 않은(미제출) 팀 목록을 조회합니다.

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
    """주간보고와 일반 메일의 전체 요약 통계를 조회합니다. 전반적인 현황을 파악할 때 사용합니다.

    Args:
        week: 주차 필터. 미지정시 전체 기간 조회
    """
    try:
        result = _get_mail_type_summary(week=week)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def search_mail_content(query: str, week: Optional[str] = None) -> str:
    """메일 본문 내용을 검색합니다. 팀명, 제품군(NAND/DRAM), 키워드를 모두 query에 포함시키세요.

    Args:
        query: 검색 질문 또는 키워드 (팀명, 제품군 포함)
        week: 주차 필터
    """
    try:
        if os_client:
            # team 필터 없이 검색 (팀명은 query에 포함)
            results = os_client.search(
                query, team=None, week=week, limit=SEARCH_RESULT_LIMIT
            )

            # 토큰 제한에 맞게 결과 truncate
            formatted = []
            used_tokens = 0

            for r in results:
                # 각 문서 텍스트 길이 제한 (너무 긴 문서는 자름)
                text = r["text"]
                if len(text) > MAX_TEXT_PER_DOC:
                    text = text[:MAX_TEXT_PER_DOC] + "...(truncated)"

                item = {
                    "team": r["team"],
                    "week": r["week"],
                    "mail_id": r["mail_id"],
                    "text": text,
                    "score": round(r["score"], 4),
                }

                # 토큰 수 계산
                item_json = json.dumps(item, ensure_ascii=False)
                item_tokens = count_tokens(item_json)

                # 토큰 예산 초과 시 중단
                if used_tokens + item_tokens > MAX_TOOL_RESULT_TOKENS:
                    print(
                        f"⚠️ 토큰 제한 도달: {len(formatted)}개 문서 사용 ({used_tokens} tokens)"
                    )
                    break

                formatted.append(item)
                used_tokens += item_tokens

            print(
                f"📊 검색 결과: {len(formatted)}/{len(results)}개 문서, {used_tokens} tokens"
            )
            return json.dumps(formatted, ensure_ascii=False, default=str)
        return json.dumps([], ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def get_available_weeks() -> str:
    """데이터가 있는 주차 목록을 조회합니다. 어떤 주차의 데이터가 있는지 확인할 때 사용합니다."""
    try:
        result = _get_unique_weeks()
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# Tool 리스트
AGENT_TOOLS = [
    count_weekly_reports_by_team,
    count_daily_reports_by_team,
    count_other_mails_by_team,
    get_missing_teams,
    get_mail_type_summary,
    search_mail_content,
    get_available_weeks,
]

# 시스템 프롬프트
AGENT_SYSTEM_PROMPT = """당신은 반도체 주간 업무 보고서 시스템의 AI 어시스턴트입니다.

## 역할
- 주간보고 및 메일 관련 통계 질문에 답변합니다.
- 메일 내용 검색 요청에 응답합니다.
- 제공된 도구(tool)를 적절히 활용하세요.

## 검색 규칙
search_mail_content 사용 시:
- 사용자의 질문을 그대로 query로 사용하세요.
- 팀명, 제품군(NAND/DRAM), 키워드가 있으면 query에 포함시키세요.
- **팀명이나 제품군이 명시되지 않아도 키워드만으로 검색을 수행하세요.**
- 축약어도 그대로 사용하세요 (예: CL팀, 256단, HE, M0C 등).
- **정보가 부족하다고 판단하지 말고, 일단 검색을 실행하세요.**

예시:
- "HE에서 발생한 M0C 이슈" → query="HE M0C 이슈"
- "NAND M0C단 이슈" → query="NAND M0C단 이슈"
- "CL팀 수율" → query="CL팀 수율"
- "DRAM 불량 분석" → query="DRAM 불량 분석"
- "수율 개선" → query="수율 개선"

## 참고: 예외 팀-제품군 매핑
다음 팀들은 이름에 NAND/DRAM이 없지만 해당 제품군입니다:
- 256단 수율팀, Colosseum수율팀 → NAND

## 날짜/주차 해석 규칙
- "이번주", "이번 주차", "현재 주차" → 컨텍스트에 제공된 "이번주" 값을 사용
- "지난주", "저번주" → 이번주 - 1주차 계산
- "다음주" → 이번주 + 1주차 계산
- 주차는 "YYYY-WW" 형식 (예: 2025-48)

## 답변 원칙
1. **질문이 들어오면 먼저 검색/조회를 시도하세요. 추가 정보를 요청하지 마세요.**
2. 도구 실행 결과를 바탕으로 명확하게 답변하세요.
3. 숫자와 팀명을 정확히 포함하세요.
4. 표 형식이 적절하면 표로 정리하세요.
5. 결과가 없으면 "검색 결과가 없습니다"라고 알려주세요.

## 도구 사용 가이드
- 주간보고 통계: count_weekly_reports_by_team
- 일일보고 통계: count_daily_reports_by_team
- 일반 메일 통계: count_other_mails_by_team
- 미제출 팀: get_missing_teams
- 전체 요약: get_mail_type_summary
- 내용 검색: search_mail_content
- 주차 목록: get_available_weeks

## 반복 검색 제한
- 내용 검색은 최대 2회(재작성 1회 포함)만 수행하고, 추가 검색을 반복하지 마세요.

## 멀티턴 대화 규칙
- **이전 대화 내용을 참고하여 문맥에 맞게 답변하세요.**
- 후속 질문(예: "그 중에서", "더 자세히", "다른 건?")은 이전 답변과 연결하여 이해하세요.
- [이전 대화 요약]이 제공되면 해당 맥락을 고려하세요.
- 사용자가 새로운 주제로 전환하면 새 검색을 수행하세요.

예시:
- 이전 답변: "Spica에서 발생한 최근 이슈 알려줘"
- 후속 질문: "향후 계획은?" → Spica에서 발생한 최근 이슈에 대한 향후 계획을 검색

"""

# Agentic RAG LangGraph (지연 초기화)
_agentic_graph = None


class AgentState(TypedDict):
    """Agentic RAG 상태"""

    messages: Annotated[Sequence[BaseMessage], add_messages]
    search_count: int
    rewrite_count: int
    last_query: str
    blocked: bool


def _get_last_tool_message(messages: Sequence[BaseMessage]) -> Optional[ToolMessage]:
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            return msg
    return None


def _parse_search_results(tool_msg: Optional[ToolMessage]) -> List[Dict]:
    if tool_msg is None or not tool_msg.content:
        return []
    try:
        data = json.loads(tool_msg.content)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _agent_node(state: AgentState) -> Dict[str, Any]:
    messages = state["messages"]
    model = get_llm().bind_tools(AGENT_TOOLS)
    response = model.invoke(messages)
    return {"messages": [response]}


def _tool_guard(state: AgentState) -> Dict[str, Any]:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", []) or []
    search_calls = [tc for tc in tool_calls if tc.get("name") == "search_mail_content"]

    search_count = state.get("search_count", 0)
    blocked = False

    if search_calls:
        if search_count >= MAX_SEARCH_CALLS:
            blocked = True
        else:
            search_count += 1

    return {"search_count": search_count, "blocked": blocked}


def _route_after_guard(state: AgentState) -> Literal["retrieve", "blocked"]:
    return "blocked" if state.get("blocked") else "retrieve"


def _route_after_tool(state: AgentState) -> Literal["grade", "agent"]:
    last_tool = _get_last_tool_message(state["messages"])
    if last_tool and last_tool.name == "search_mail_content":
        return "grade"
    return "agent"


def _grade_node(state: AgentState) -> Dict[str, Any]:
    return {}


def _grade_documents(state: AgentState) -> Literal["generate", "rewrite"]:
    last_tool = _get_last_tool_message(state["messages"])
    results = _parse_search_results(last_tool)
    if results:
        return "generate"
    if state.get("rewrite_count", 0) >= MAX_REWRITE_COUNT:
        return "generate"
    return "rewrite"


def _rewrite_node(state: AgentState) -> Dict[str, Any]:
    question = state.get("last_query") or state["messages"][0].content
    prompt = f"""사용자 질문을 검색에 적합하도록 핵심 키워드 중심으로 재작성하세요.
불필요한 수식어는 줄이고, 팀명/제품군/키워드를 유지하세요.

질문:
{question}

재작성:"""
    response = get_llm().invoke([HumanMessage(content=prompt)])
    rewritten = response.content.strip()
    return {
        "messages": [HumanMessage(content=rewritten)],
        "rewrite_count": state.get("rewrite_count", 0) + 1,
        "last_query": rewritten,
    }


def _generate_node(state: AgentState) -> Dict[str, Any]:
    last_tool = _get_last_tool_message(state["messages"])
    contexts = _parse_search_results(last_tool)
    question = state.get("last_query") or state["messages"][0].content
    answer = generate_answer(question, contexts, 0.7, 0.3)
    return {"messages": [AIMessage(content=answer)]}


def _blocked_response_node(state: AgentState) -> Dict[str, Any]:
    return {
        "messages": [
            AIMessage(
                content="검색 결과가 충분치 않아 추가 검색을 중단했습니다. 질문을 더 구체적으로 알려주세요."
            )
        ]
    }


def get_agentic_graph():
    """Agentic RAG 그래프 생성 (싱글톤)"""
    global _agentic_graph
    if _agentic_graph is None:
        workflow = StateGraph(AgentState)
        workflow.add_node("agent", _agent_node)
        workflow.add_node("tool_guard", _tool_guard)
        workflow.add_node("retrieve", ToolNode(AGENT_TOOLS))
        workflow.add_node("grade", _grade_node)
        workflow.add_node("rewrite", _rewrite_node)
        workflow.add_node("generate", _generate_node)
        workflow.add_node("blocked", _blocked_response_node)

        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "agent",
            tools_condition,
            {
                "tools": "tool_guard",
                END: END,
            },
        )
        workflow.add_conditional_edges(
            "tool_guard",
            _route_after_guard,
            {
                "retrieve": "retrieve",
                "blocked": "blocked",
            },
        )
        workflow.add_conditional_edges(
            "retrieve",
            _route_after_tool,
            {
                "grade": "grade",
                "agent": "agent",
            },
        )
        workflow.add_conditional_edges("grade", _grade_documents)
        workflow.add_edge("rewrite", "agent")
        workflow.add_edge("generate", END)
        workflow.add_edge("blocked", END)

        _agentic_graph = workflow.compile(checkpointer=MemorySaver())
    return _agentic_graph


def _convert_history_to_messages(history: List[Dict[str, Any]]) -> List[BaseMessage]:
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
        else:
            converted.append(AIMessage(content=content))
    return converted


async def chat_with_tools_async(
    user_message: str,
    team: str = None,
    week: str = None,
    conversation_id: str = None,
) -> Dict[str, Any]:
    """Agentic RAG 그래프 기반 채팅 (멀티턴 지원)"""
    graph = get_agentic_graph()

    # 현재 날짜 및 주차 계산
    now = datetime.now()
    iso_cal = now.isocalendar()
    current_week = f"{iso_cal[0]}-{iso_cal[1]:02d}"

    # 컨텍스트 정보 (시스템 메시지용, 1회만 제공)
    context_info = f"오늘 날짜: {now.strftime('%Y-%m-%d')}, 이번주: {current_week}"
    if team:
        context_info += f", 팀 필터: {team}"
    if week:
        context_info += f", 주차 필터: {week}"

    # 대화 히스토리 조회 (멀티턴)
    history = []
    if conversation_id:
        history = await get_history(conversation_id)

    # 메시지 구성: [시스템(가이드)] + [시스템(컨텍스트)] + [히스토리] + [현재 질문]
    history_messages = _convert_history_to_messages(history)
    messages: List[BaseMessage] = (
        [
            SystemMessage(content=AGENT_SYSTEM_PROMPT),
            SystemMessage(content=f"[컨텍스트] {context_info}"),
        ]
        + history_messages
        + [HumanMessage(content=user_message)]
    )

    # ===== 디버깅: 전체 메시지 토큰 출력 =====
    system_tokens = count_tokens(AGENT_SYSTEM_PROMPT) + count_tokens(
        f"[컨텍스트] {context_info}"
    )
    history_tokens = sum(count_tokens(m.get("content", "")) for m in history)
    user_tokens = count_tokens(user_message)
    total_input_tokens = system_tokens + history_tokens + user_tokens

    print(f"🔍 [DEBUG] chat_with_tools_async - 메시지 구성")
    print(f"   - 시스템 메시지 토큰: {system_tokens}")
    print(f"   - 히스토리 토큰: {history_tokens} ({len(history)}개 메시지)")
    print(f"   - 현재 질문 토큰: {user_tokens}")
    print(f"   - 총 입력 토큰 (tool 제외): {total_input_tokens}")
    print(f"   - 메시지 구조: {[m.type for m in messages]}")
    # =========================================

    # Graph 실행 (동기 함수이므로 run_in_executor 사용)
    loop = asyncio.get_event_loop()
    try:
        print(f"🚀 [DEBUG] graph.invoke 호출 시작...")
        config = RunnableConfig(
            recursion_limit=GRAPH_RECURSION_LIMIT,
            configurable={"thread_id": conversation_id or "single"},
        )
        result = await loop.run_in_executor(
            None,
            lambda: graph.invoke(
                {
                    "messages": messages,
                    "search_count": 0,
                    "rewrite_count": 0,
                    "last_query": user_message,
                    "blocked": False,
                },
                config=config,
            ),
        )
        print(f"✅ [DEBUG] graph.invoke 성공")
    except Exception as e:
        # ===== 디버깅: 에러 상세 출력 =====
        print(f"❌ [DEBUG] graph.invoke 실패!")
        print(f"   - 에러 타입: {type(e).__name__}")
        print(f"   - 에러 메시지: {e}")
        print(f"   - 입력 토큰 (추정): {total_input_tokens}")
        print(f"   - 히스토리 메시지 수: {len(history)}")
        for i, m in enumerate(history):
            content = m.get("content", "")
            content_preview = content[:100] + "..." if len(content) > 100 else content
            print(
                f"   - history[{i}] ({m['role']}): {count_tokens(content)} tokens - {content_preview}"
            )
        # ==================================

        error_msg = str(e).lower()
        # 토큰 초과 관련 에러 처리
        if (
            "context_length" in error_msg
            or "token" in error_msg
            or "too long" in error_msg
            or "maximum" in error_msg
            or "500" in str(e)
        ):
            print(f"⚠️ 토큰/서버 에러로 판단됨")
            return {
                "answer": "대화가 너무 길어 처리할 수 없습니다. 새 대화를 시작해주세요.",
                "tool_calls": [],
                "tool_results": [],
            }
        raise

    # 결과 파싱
    response_messages = result.get("messages", [])
    tool_calls_info = []
    tool_results = []
    answer = ""

    for msg in response_messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls_info.append(
                    {"name": tc.get("name", ""), "arguments": tc.get("args", {})}
                )
        if isinstance(msg, ToolMessage):
            tool_results.append({"name": msg.name, "result": msg.content})
        if isinstance(msg, AIMessage) and msg.content:
            answer = msg.content

    if not answer:
        answer = "질문을 이해하지 못했습니다. 다시 말씀해주세요."

    return {
        "answer": answer,
        "tool_calls": tool_calls_info,
        "tool_results": tool_results,
    }


# ========== RAG 로직 ==========
def generate_answer(
    query: str,
    contexts: List[Dict],
    vector_weight: float,
    keyword_weight: float,
) -> str:
    """검색된 context 기반으로 LLM 답변 생성"""
    if not contexts:
        return "관련 정보를 찾을 수 없습니다."

    # Context 구성
    context_text = ""
    for i, ctx in enumerate(contexts, 1):
        context_text += f"\n[문서 {i}]\n"
        context_text += f"팀: {ctx.get('team', 'unknown')}\n"
        context_text += f"주차: {ctx.get('week', 'unknown')}\n"
        if ctx.get("part_index") is not None:
            context_text += (
                f"파트: {ctx['part_index'] + 1}/{ctx.get('total_parts', 1)}\n"
            )
        context_text += f"내용:\n{ctx['text'][:3000]}\n"
        context_text += "-" * 40

    # 프롬프트
    system_prompt = """당신은 반도체 주간 업무 보고서를 기반으로 질문에 답변하는 AI 어시스턴트입니다.

## 답변 원칙
1. 제공된 문서(context)만 근거로 답변하세요.
2. 문서에 없는 내용은 추측하지 마세요.
3. 관련 정보가 없으면 "관련 정보를 찾을 수 없습니다"라고 답하세요.
4. 답변은 간결하고 명확하게 작성하세요.
5. 수치, 이슈, 액션 등 핵심 정보를 포함하세요.

## 출처 표시 규칙 (중요!)
- 답변에 사용한 정보는 해당 위치에 [문서 1], [문서 2] 형식으로 출처를 표시하세요.
- 참고하지 않은 문서는 절대 표시하지 마세요.
- 답변 마지막 줄에 "참고: [문서 1], [문서 3]" 형태로 사용한 문서를 요약하세요.
"""

    weight_info = f"벡터:{vector_weight:.0%}, 키워드:{keyword_weight:.0%}"
    user_prompt = f"""질문: {query}

참고 문서 (검색 가중치 - {weight_info}):
{context_text}

위 문서를 바탕으로 질문에 답변해주세요."""

    llm = get_llm()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    response = llm.invoke(messages)
    return response.content


def parse_used_references(answer: str, contexts: List[Dict]) -> tuple:
    """LLM 답변에서 실제 참고한 문서만 추출

    Args:
        answer: LLM 답변 (출처 표시 포함)
        contexts: 검색된 문서 리스트

    Returns:
        (정제된 답변, 참고한 contexts 리스트)
    """
    # [문서 N] 패턴 찾기
    pattern = r"\[문서\s*(\d+)\]"
    matches = re.findall(pattern, answer)

    if not matches:
        # 출처 표시가 없으면 빈 리스트 반환
        return answer, []

    # 사용된 문서 인덱스 추출 (0-indexed)
    used_indices = set(int(m) - 1 for m in matches if int(m) - 1 < len(contexts))

    # 참고한 문서만 필터링
    used_contexts = [ctx for i, ctx in enumerate(contexts) if i in used_indices]

    # "참고: [문서 1], [문서 3]" 부분 제거 (출처는 별도로 표시하므로)
    clean_answer = re.sub(
        r"\n*참고:\s*(\[문서\s*\d+\],?\s*)+\.?$", "", answer, flags=re.MULTILINE
    )
    clean_answer = clean_answer.strip()

    return clean_answer, used_contexts


def extract_references(contexts: List[Dict]) -> List[Reference]:
    """검색 결과에서 참조 출처 추출 (같은 메일은 1개만)"""
    refs = []
    seen = set()

    for ctx in contexts:
        team = ctx.get("team", "unknown")
        week = ctx.get("week", "unknown")
        mail_id = ctx.get("mail_id", "unknown")
        part_index = ctx.get("part_index")

        # 중복 제거 (같은 메일은 한 번만 표시, part_index 무시)
        key = f"{team}_{week}_{mail_id}"
        if key in seen:
            continue
        seen.add(key)

        # /mail 경로로 static files 서빙: {week}_{team}_{mail_id}.html 형식
        # URL 인코딩 적용 (팀명 등에 띄어쓰기가 있을 경우 대비)
        filename = f"{week}_{team}_{mail_id}.html"
        url = f"{API_BASE_URL}/mail/{quote(filename, safe='')}"
        refs.append(
            Reference(
                team=team,
                week=week,
                mail_id=mail_id,
                url=url,
                score=ctx.get("score", 0),
                part_index=part_index,
                total_parts=ctx.get("total_parts"),
            )
        )

    return refs


def format_answer_with_references(answer: str, references: List[Reference]) -> str:
    """답변에 출처 정보를 포함하여 반환"""
    if not references:
        return answer

    # 출처 섹션 구성: URL은 대괄호 없이 표시 (클릭 가능하도록)
    ref_lines = ["\n\n━━━━━━━━━━━━━━━━━━━━", "📎 참고 출처:"]
    for ref in references:
        ref_lines.append(f"  • {ref.team} | {ref.week}")
        ref_lines.append(f"    {ref.url}")

    return answer + "\n".join(ref_lines)


def convert_table_to_bullet(text: str) -> str:
    """마크다운 표를 개조식으로 변환 (사내 메신저 richnotification 호환용)"""
    lines = text.split("\n")
    result = []
    table_lines = []
    in_table = False

    for line in lines:
        # 표 시작 감지 (| 로 시작하는 라인)
        if line.strip().startswith("|") and "|" in line[1:]:
            in_table = True
            table_lines.append(line)
        else:
            # 표 끝났으면 변환
            if in_table and table_lines:
                result.append(_parse_table_to_bullet(table_lines))
                table_lines = []
                in_table = False
            result.append(line)

    # 마지막 표 처리
    if table_lines:
        result.append(_parse_table_to_bullet(table_lines))

    return "\n".join(result)


def _parse_table_to_bullet(table_lines: list) -> str:
    """표 라인들을 개조식으로 변환"""
    rows = []
    headers = []

    for i, line in enumerate(table_lines):
        # 구분선 스킵 (|---|---|)
        if re.match(r"^\|[\s\-:\|]+\|?$", line.strip()):
            continue

        # 셀 파싱
        cells = [c.strip() for c in line.split("|")]
        # 앞뒤 빈 셀 제거 (| col1 | col2 | 형식에서 발생)
        cells = [c for c in cells if c]

        if not headers:
            headers = cells
        else:
            rows.append(cells)

    # 개조식으로 변환
    bullet_lines = []
    for row in rows:
        if len(headers) == len(row) and len(row) >= 2:
            # 첫 번째 컬럼을 제목으로, 나머지는 속성으로
            item = f"• {row[0]}"
            details = [f"{headers[j]}: {row[j]}" for j in range(1, len(row)) if row[j]]
            if details:
                item += f" ({', '.join(details)})"
            bullet_lines.append(item)
        elif row:
            # 헤더 없거나 맞지 않으면 그냥 나열
            bullet_lines.append(f"• {' | '.join(row)}")

    return "\n".join(bullet_lines) if bullet_lines else ""


def clean_html_breaks(text: str) -> str:
    """HTML <br> 태그를 줄바꿈으로 변환 (사내 메신저 호환용)

    LLM이 간혹 줄바꿈을 <br> 태그로 출력하는 경우가 있어 후처리합니다.
    """
    # <br>, <br/>, <br /> 등 모든 형태의 br 태그를 줄바꿈으로 변환
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return text


# ========== FastAPI 앱 ==========
app = FastAPI(
    title="Weekly Mail RAG Chatbot API (OpenSearch + LangGraph)",
    description="""주간 메일 히스토리 기반 Q&A API - OpenSearch 하이브리드 검색 + LangGraph ReAct Agent 지원

## 주요 기능

### 1. LangGraph ReAct Agent 채팅 (/chat/v2) ⭐ NEW
LangGraph `create_react_agent` 기반으로 LLM이 자동으로 도구를 선택합니다:
- 통계 질문 → 집계 함수 자동 호출
- 내용 검색 → RAG 검색 자동 호출
- Multi-step 추론 지원 (복잡한 질문도 가능)

예시 질문:
- "2025-48주차 주간보고 팀별 count 알려줘"
- "YIELD팀이 보낸 주간보고 외 메일은 몇 개야?"
- "주간보고 미제출 팀은?"
- "수율 개선 관련 이슈 알려줘"

### 2. 기존 RAG 채팅 (/chat)
- `vector_weight=1.0, keyword_weight=0.0` → 순수 벡터 검색
- `vector_weight=0.0, keyword_weight=1.0` → 순수 키워드 검색
- `vector_weight=0.7, keyword_weight=0.3` → 하이브리드 (기본값)

### 3. 통계 API (/stats/*)
- `/stats/weekly-reports` - 주간보고 팀별 count
- `/stats/other-mails` - 주간보고 외 메일 팀별 count
- `/stats/missing-teams` - 미제출 팀 조회
- `/stats/summary` - 전체 현황 요약
""",
    version="4.0.0",
)

# Static files 서빙 (원본 메일 HTML 조회용)
# /mail/{week}_{team}_{mail_id}.html 경로로 접근 가능
MAIL_DIR.mkdir(exist_ok=True)
app.mount("/mail", StaticFiles(directory=str(MAIL_DIR)), name="mail")

# 전역 클라이언트
os_client: Optional[OpenSearchClient] = None
mongo_client: Optional[AsyncIOMotorClient] = None
mongo_db = None


# ========== MongoDB 대화 히스토리 관리 ==========
async def init_mongodb():
    """MongoDB 초기화 및 인덱스 생성"""
    global mongo_client, mongo_db
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    mongo_db = mongo_client[MONGO_DB]

    # conversation_logs: 전체 기록 (영구 보관)
    # conversation_id + timestamp 복합 인덱스
    await mongo_db.conversation_logs.create_index(
        [("conversation_id", 1), ("timestamp", -1)]
    )
    await mongo_db.conversation_logs.create_index("user_id")
    await mongo_db.conversation_logs.create_index("timestamp")

    # conversation_history: LLM용 경량 히스토리 (TTL 30분)
    await mongo_db.conversation_history.create_index("conversation_id", unique=True)
    await mongo_db.conversation_history.create_index(
        "updated_at", expireAfterSeconds=HISTORY_TTL_SECONDS
    )

    print(f"✅ MongoDB 초기화 완료: {MONGO_URI}/{MONGO_DB}")
    print(f"   - conversation_logs: 전체 기록 (영구)")
    print(f"   - conversation_history: LLM용 (TTL {HISTORY_TTL_SECONDS}초)")


async def close_mongodb():
    """MongoDB 연결 종료"""
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
    """전체 대화 기록 저장 (감사/분석용, 영구 보관)"""
    if mongo_db is None:
        return

    # 현재 턴 번호 계산
    existing_count = await mongo_db.conversation_logs.count_documents(
        {"conversation_id": conversation_id}
    )
    turn = existing_count + 1

    log_doc = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "timestamp": datetime.utcnow(),
        "turn": turn,
        "request": {
            "message": message,
            "team": team,
            "week": week,
        },
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "response": {
            "answer": answer,
            "references": references,
        },
    }

    await mongo_db.conversation_logs.insert_one(log_doc)


async def summarize_conversation(messages: List[Dict]) -> str:
    """오래된 대화를 LLM으로 요약

    Args:
        messages: 요약할 메시지 리스트 [{"role": "user/assistant", "content": "..."}]

    Returns:
        요약된 텍스트 (MAX_SUMMARY_TOKENS 이내)
    """
    if not messages:
        return ""

    conversation_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])

    llm = get_llm()
    prompt = f"""다음 대화를 {MAX_SUMMARY_TOKENS}토큰 이내로 핵심만 요약하세요.
사용자가 물은 질문과 얻은 답변의 핵심 정보만 포함하세요.
불필요한 인사말이나 부연 설명은 제외하세요.

대화:
{conversation_text}

요약:"""

    try:
        response = await asyncio.to_thread(
            lambda: llm.invoke([{"role": "user", "content": prompt}])
        )
        return response.content.strip()
    except Exception as e:
        print(f"⚠️ 대화 요약 실패: {e}")
        # 요약 실패 시 대화 내용을 간단히 축약
        fallback = []
        for m in messages:
            content = (
                m["content"][:200] + "..." if len(m["content"]) > 200 else m["content"]
            )
            fallback.append(f"{m['role']}: {content}")
        return "\n".join(fallback)


async def save_history(
    conversation_id: str,
    user_message: str,
    assistant_answer: str,
) -> None:
    """경량 히스토리 저장 (LLM 멀티턴용, 질문/답변만)"""
    if mongo_db is None:
        return

    # 답변 압축
    compressed_answer = assistant_answer
    if len(assistant_answer) > MAX_ANSWER_LENGTH:
        compressed_answer = assistant_answer[:MAX_ANSWER_LENGTH] + "...(생략)"

    messages_to_add = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": compressed_answer},
    ]

    # upsert로 대화 추가 (최근 N턴만 유지)
    await mongo_db.conversation_history.update_one(
        {"conversation_id": conversation_id},
        {
            "$push": {
                "messages": {
                    "$each": messages_to_add,
                    "$slice": -(MAX_HISTORY_TURNS * 2),  # user+assistant 쌍
                }
            },
            "$set": {"updated_at": datetime.utcnow()},
            "$setOnInsert": {"created_at": datetime.utcnow()},
        },
        upsert=True,
    )


async def get_history(conversation_id: str) -> List[Dict]:
    """LLM용 대화 히스토리 조회 (필요시 요약 생성)

    SUMMARY_THRESHOLD 초과 시 오래된 대화를 LLM으로 요약하고,
    요약 결과를 MongoDB에 캐싱하여 재사용합니다.

    Returns:
        [{"role": "system", "content": "[이전 대화 요약]..."}] + 최근 메시지
        또는 threshold 이하면 원본 메시지 그대로
    """
    if mongo_db is None:
        return []

    doc = await mongo_db.conversation_history.find_one(
        {"conversation_id": conversation_id}
    )
    if not doc:
        return []

    messages = doc.get("messages", [])
    existing_summary = doc.get("summary", "")

    # ===== 디버깅: 히스토리 상태 출력 =====
    total_tokens = sum(count_tokens(m.get("content", "")) for m in messages)
    summary_tokens = count_tokens(existing_summary) if existing_summary else 0
    print(f"🔍 [DEBUG] get_history - conversation_id: {conversation_id}")
    print(f"   - 메시지 수: {len(messages)}")
    print(f"   - 턴 수: {len(messages) // 2}")
    print(f"   - 히스토리 토큰: {total_tokens}")
    print(f"   - 요약 토큰: {summary_tokens}")
    print(f"   - 총 히스토리 토큰: {total_tokens + summary_tokens}")
    # =====================================

    # 턴 수 계산 (user+assistant 쌍 = 1턴)
    turn_count = len(messages) // 2

    # threshold 이하면 그대로 반환
    if turn_count <= SUMMARY_THRESHOLD:
        result = []
        if existing_summary:
            result.append(
                {"role": "system", "content": f"[이전 대화 요약]\n{existing_summary}"}
            )
        return result + messages

    # threshold 초과: 요약 필요
    recent_count = RECENT_TURNS_TO_KEEP * 2  # user+assistant 쌍
    old_messages = messages[:-recent_count] if recent_count > 0 else messages
    recent_messages = messages[-recent_count:] if recent_count > 0 else []

    # 오래된 메시지 요약 생성
    print(f"📝 대화 요약 생성 중... (기존 {len(old_messages)}개 메시지 → 요약)")
    new_summary_part = await summarize_conversation(old_messages)

    # 기존 요약과 병합
    if existing_summary:
        combined_summary = f"{existing_summary}\n\n{new_summary_part}"
    else:
        combined_summary = new_summary_part

    # MongoDB 업데이트 (요약 저장 + 오래된 메시지 제거)
    await mongo_db.conversation_history.update_one(
        {"conversation_id": conversation_id},
        {
            "$set": {
                "summary": combined_summary,
                "messages": recent_messages,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    print(f"✅ 요약 저장 완료 (최근 {len(recent_messages)}개 메시지 유지)")

    # 요약 + 최근 메시지 반환
    return [
        {"role": "system", "content": f"[이전 대화 요약]\n{combined_summary}"}
    ] + recent_messages


@app.on_event("startup")
async def startup():
    """서버 시작 시 OpenSearch + MongoDB 초기화"""
    global os_client
    os_client = OpenSearchClient()
    health = os_client.health_check()
    stats = os_client.get_stats()
    print(f"✅ OpenSearch 초기화 완료: {health}")
    print(f"📊 인덱스 통계: {stats}")

    # MongoDB 초기화
    await init_mongodb()


@app.on_event("shutdown")
async def shutdown():
    """서버 종료 시 정리"""
    await close_mongodb()


@app.get("/health")
async def health():
    """헬스체크"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "opensearch": os_client.health_check() if os_client else None,
        "db_stats": os_client.get_stats() if os_client else None,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """채팅 API - 질문 수신 및 답변 반환

    가중치로 검색 방식을 조절합니다:
    - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
    - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
    - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값)
    """
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    query = request.message.strip()
    if not query:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    # 1. 하이브리드 검색 (가중치로 모드 제어)
    contexts = os_client.search(
        query,
        team=request.team,
        week=request.week,
        limit=5,
        vector_weight=request.vector_weight,
        keyword_weight=request.keyword_weight,
    )

    # 2. LLM 답변 생성
    raw_answer = generate_answer(
        query,
        contexts,
        request.vector_weight,
        request.keyword_weight,
    )

    # 3. 실제 참고한 문서만 추출
    clean_answer, used_contexts = parse_used_references(raw_answer, contexts)

    # 4. 참조 출처 추출 (사용된 문서만)
    references = extract_references(used_contexts)

    # 5. 답변에 출처 포함
    answer_with_refs = format_answer_with_references(clean_answer, references)

    # 6. HTML <br> 태그를 줄바꿈으로 변환
    answer_with_refs = clean_html_breaks(answer_with_refs)

    return ChatResponse(
        answer=answer_with_refs,
        references=references,
        vector_weight=request.vector_weight,
        keyword_weight=request.keyword_weight,
    )


@app.post("/search", response_model=List[SearchResult])
async def search(request: SearchRequest):
    """검색 API (채팅 없이 검색만)

    가중치로 검색 방식을 조절합니다:
    - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
    - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
    - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값)
    """
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="검색어가 비어있습니다")

    # 하이브리드 검색
    results = os_client.search(
        query,
        team=request.team,
        week=request.week,
        limit=request.limit,
        vector_weight=request.vector_weight,
        keyword_weight=request.keyword_weight,
    )

    return [
        SearchResult(
            score=r["score"],
            text=r["text"][:500] + "..." if len(r["text"]) > 500 else r["text"],
            team=r["team"],
            week=r["week"],
            mail_id=r["mail_id"],
            html_path=r["html_path"],
            part_index=r.get("part_index"),
            total_parts=r.get("total_parts"),
            vector_score=r.get("vector_score"),
            keyword_score=r.get("keyword_score"),
        )
        for r in results
    ]


@app.get("/teams")
async def get_teams():
    """팀 목록 조회"""
    return {"teams": TEAMS}


# ========== Tool Calling 기반 API ==========


class ChatV2Request(BaseModel):
    """Tool Calling 채팅 요청"""

    user_id: str
    message: str
    conversation_id: Optional[str] = Field(
        default=None, description="대화 세션 ID (멀티턴용, 미지정시 싱글턴)"
    )
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
    """Tool Calling 채팅 응답"""

    answer: str
    tool_calls: List[ToolCallInfo]
    tool_results: List[ToolResultInfo]
    references: List[Reference] = Field(default=[], description="참조 출처 목록")


@app.post("/chat/v2", response_model=ChatV2Response)
async def chat_v2(request: ChatV2Request):
    """Tool Calling 기반 채팅 API (멀티턴 지원)

    LLM이 질문을 분석하여 적절한 도구를 선택하고 실행합니다.
    conversation_id를 제공하면 이전 대화 히스토리를 기반으로 답변합니다.

    - 통계 질문 → 집계 함수 자동 호출
    - 내용 검색 → RAG 검색 자동 호출
    - 멀티턴 대화 → 이전 대화 컨텍스트 유지

    예시 질문:
    - "2025-48주차 주간보고 팀별 count 알려줘"
    - "YIELD팀이 보낸 주간보고 외 메일은 몇 개야?"
    - "이번주 주간보고 미제출 팀은?"
    - "수율 개선 이슈 뭐야?"
    - (후속) "그 중에서 HE 관련만 알려줘" (conversation_id 필요)
    """
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    # 비동기 Agent 호출 (멀티턴 히스토리 포함)
    result = await chat_with_tools_async(
        message,
        team=request.team,
        week=request.week,
        conversation_id=request.conversation_id,
    )

    # tool_results에서 search_mail_content 결과 추출
    references = []
    search_contexts = []
    for tr in result["tool_results"]:
        if tr["name"] == "search_mail_content":
            try:
                search_results = json.loads(tr["result"])
                if isinstance(search_results, list) and search_results:
                    search_contexts = search_results
            except (json.JSONDecodeError, TypeError):
                pass

    # 실제 참고한 문서만 필터링하여 references 생성
    answer = result["answer"]
    if search_contexts:
        clean_answer, used_contexts = parse_used_references(answer, search_contexts)
        references = extract_references(used_contexts)
    else:
        clean_answer = answer

    # 표를 개조식으로 변환 (사내 메신저 richnotification 호환)
    answer_converted = convert_table_to_bullet(clean_answer)

    # HTML <br> 태그를 줄바꿈으로 변환
    answer_converted = clean_html_breaks(answer_converted)

    # MongoDB에 대화 저장 (conversation_id가 있는 경우)
    if request.conversation_id:
        # 전체 기록 저장 (감사/분석용)
        await save_full_log(
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            message=message,
            team=request.team,
            week=request.week,
            tool_calls=result["tool_calls"],
            tool_results=result["tool_results"],
            answer=answer_converted,
            references=[ref.model_dump() for ref in references],
        )

        # 경량 히스토리 저장 (LLM 멀티턴용, 순수 메시지만 저장)
        await save_history(
            conversation_id=request.conversation_id,
            user_message=message,  # 컨텍스트 제외한 순수 메시지
            assistant_answer=answer_converted,
        )

    # 출처는 references 필드로 분리 (클라이언트에서 별도 처리)
    return ChatV2Response(
        answer=answer_converted,
        tool_calls=[ToolCallInfo(**tc) for tc in result["tool_calls"]],
        tool_results=[ToolResultInfo(**tr) for tr in result["tool_results"]],
        references=references,
    )


# ========== 통계 API ==========


@app.get("/stats/weekly-reports")
async def stats_weekly_reports(week: Optional[str] = None):
    """주간보고 팀별 통계

    Args:
        week: 주차 필터 (예: 2025-48). 미지정시 전체 기간
    """
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
    """주간보고 외 메일 팀별 통계

    Args:
        week: 주차 필터
        team: 팀 필터 (특정 팀만 조회)
    """
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
    """주간보고 미제출 팀 조회

    Args:
        week: 주차 (필수, 예: 2025-48)
    """
    try:
        missing = _get_missing_teams(week=week)
        return {"week": week, "missing_teams": missing, "count": len(missing)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats/summary")
async def stats_summary(week: Optional[str] = None):
    """전체 메일 현황 요약

    Args:
        week: 주차 필터. 미지정시 전체 기간
    """
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


# ========== 실행 ==========
if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("RAG Chatbot API Server (OpenSearch + LangGraph)")
    print("=" * 50)
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Docs: http://localhost:{PORT}/docs")
    print(f"  Mail: http://localhost:{PORT}/mail/")
    print(f"  OpenSearch: {OPENSEARCH_HOST}:{OPENSEARCH_PORT}")
    print(f"  Index: {INDEX_NAME}")
    print(f"  Agent: LangGraph ReAct Agent")
    print("=" * 50)

    uvicorn.run(app, host=HOST, port=PORT)

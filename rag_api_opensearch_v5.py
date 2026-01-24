"""
RAG Chatbot API Server (OpenSearch 버전) - v5 Naive RAG + LangGraph 스트리밍
- FastAPI 기반 REST API 서버
- OpenSearch 하이브리드 검색 (벡터 + 키워드)
- LangGraph Naive RAG + LLM Router 구조
- **신규**: LangGraph astream_events를 사용한 실시간 토큰 스트리밍
- MongoDB 기반 멀티턴 대화 히스토리 관리
"""

import os
import re
import json
import asyncio
from pathlib import Path
from typing import (
    List,
    Dict,
    Optional,
    Any,
    Annotated,
    Sequence,
    Literal,
    TypedDict,
    Callable,
    AsyncGenerator,
)
from datetime import datetime, UTC
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
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
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "true").lower() == "true"
INDEX_NAME = os.getenv("OPENSEARCH_INDEX", "weekly_mail")

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

# 대화 요약 설정
SUMMARY_THRESHOLD = int(os.getenv("SUMMARY_THRESHOLD", "3"))
MAX_SUMMARY_TOKENS = int(os.getenv("MAX_SUMMARY_TOKENS", "300"))
RECENT_TURNS_TO_KEEP = int(os.getenv("RECENT_TURNS_TO_KEEP", "2"))

# 토큰/검색 설정
MAX_TOOL_RESULT_TOKENS = int(os.getenv("MAX_TOOL_RESULT_TOKENS", "150000"))
SEARCH_RESULT_LIMIT = int(os.getenv("SEARCH_RESULT_LIMIT", "5"))
MAX_TEXT_PER_DOC = int(os.getenv("MAX_TEXT_PER_DOC", "2000"))
ENCODING_NAME = "cl100k_base"

# Agentic RAG 설정
MAX_SEARCH_CALLS = int(os.getenv("MAX_SEARCH_CALLS", "2"))
MAX_REWRITE_COUNT = int(os.getenv("MAX_REWRITE_COUNT", "1"))
GRAPH_RECURSION_LIMIT = int(os.getenv("GRAPH_RECURSION_LIMIT", "15"))

# 스트리밍 설정 - 문단 단위로만 끊기
STREAM_BUFFER_ENDINGS = ("\n\n",)  # 문단 끝에서만 플러시

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
        week: Optional[str] = None,
        limit: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict]:
        """하이브리드 검색"""
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
            filters.append({"term": {"week": week}})

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


# ========== LLM 클라이언트 ==========
def get_llm(streaming: bool = False):
    """LLM 클라이언트

    Args:
        streaming: 스트리밍 모드 활성화 여부
    """
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        temperature=0,
        streaming=streaming,
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
def search_mail_content(query: str, week: Optional[str] = None) -> str:
    """메일 본문 내용을 검색합니다. 팀명, 제품군, 키워드를 query에 포함시키세요.

    Args:
        query: 검색 질문 또는 키워드
        week: 주차 필터
    """
    try:
        if os_client:
            results = os_client.search(
                query, team=None, week=week, limit=SEARCH_RESULT_LIMIT
            )

            formatted = []
            used_tokens = 0

            for r in results:
                text = r["text"]

                item = {
                    "team": r["team"],
                    "week": r["week"],
                    "mail_id": r["mail_id"],
                    "text": text,
                    "score": round(r["score"], 4),
                }

                item_json = json.dumps(item, ensure_ascii=False)
                item_tokens = count_tokens(item_json)

                if used_tokens + item_tokens > MAX_TOOL_RESULT_TOKENS:
                    print(
                        f"⚠️ 토큰 제한 도달: {len(formatted)}개 문서 ({used_tokens} tokens)"
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
    context: str  # 검색/통계 결과 (컨텍스트)
    answer: str  # 최종 답변
    messages: Annotated[list, add_messages]  # 대화 히스토리
    route: str  # 라우팅 결과 (search/statistics/general)


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

## 답변 원칙
1. 제공된 컨텍스트(context)를 기반으로 답변하세요.
2. 컨텍스트가 없으면 일반적인 대화로 응답하세요.
3. 답변은 간결하고 명확하게 작성하세요.
4. 숫자와 팀명을 정확히 포함하세요.

## 출처 표시 (검색 결과가 있을 때)
- 답변에 사용한 정보는 [문서 1], [문서 2] 형식으로 출처를 표시하세요.
- 답변 마지막에 "참고: [문서 1], [문서 3]" 형태로 사용한 문서를 요약하세요."""


# ===== 노드 함수들 =====
def router_node(state: GraphState) -> Dict[str, Any]:
    """Router 노드: LLM이 질문 유형 판단"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    print(f"🔀 [Router] 질문 분류 시작: {question[:50]}...")

    # Few-shot 예시가 포함된 프롬프트로 분류 (로컬 모델 일관성 향상)
    simple_prompt = f"""사용자 질문을 분류하세요. 아래 세 가지 중 하나만 답하세요.

## 분류 기준
- statistics: 제출/미제출 현황, 팀 수, 개수 등 **수치/통계** 질문
- search: 주간보고 **내용** 검색 (수율 분석, 이슈 상세, 개선 내용 등)
- general: 인사, 감사, 도움말 등 일반 대화

## 예시
- "47주차 주보 미제출 팀 알려줘" → statistics
- "48주차 주보 미제출팀 알려줘" → statistics
- "몇 개 팀이 제출했어?" → statistics
- "제출 현황 알려줘" → statistics
- "PROCESS팀 수율 이슈 알려줘" → search
- "이번주 개선 사항 뭐야?" → search
- "안녕" → general
- "도움말" → general

질문: {question}

분류 결과 (search, statistics, general 중 하나만):"""

    try:
        llm = get_llm()
        response = llm.invoke([HumanMessage(content=simple_prompt)])
        answer = response.content.strip().lower()

        # 응답에서 분류 키워드 추출
        if "search" in answer:
            route = "search"
        elif "statistics" in answer or "statistic" in answer:
            route = "statistics"
        else:
            route = "general"

        _elapsed = (_time.time() - _t_start) * 1000
        print(
            f"🔀 [Router] 분류 결과: {route} (LLM 응답: {answer[:50]}) ⏱️ {_elapsed:.0f}ms"
        )
    except Exception as e:
        print(f"⚠️ [Router] 분류 실패, 기본값 general 사용: {e}")
        route = "general"

    _total_elapsed = (_time.time() - _t_start) * 1000
    print(f"⏱️ [Router] 총 소요시간: {_total_elapsed:.0f}ms")
    return {"route": route}


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
    print(f"🔍 [Retrieve] 검색 시작: {question[:50]}...")

    if not os_client:
        print("⚠️ [Retrieve] OpenSearch 클라이언트 없음")
        return {"context": ""}

    try:
        _t_search_start = _time.time()
        results = os_client.search(
            question, team=None, week=None, limit=SEARCH_RESULT_LIMIT
        )
        _search_elapsed = (_time.time() - _t_search_start) * 1000
        print(f"⏱️ [Retrieve] OpenSearch 검색: {_search_elapsed:.0f}ms")

        # 검색 결과 포맷팅
        formatted = []
        used_tokens = 0

        for r in results:
            text = r["text"]
            if len(text) > MAX_TEXT_PER_DOC:
                text = text

            item = {
                "team": r["team"],
                "week": r["week"],
                "mail_id": r["mail_id"],
                "text": text,
                "score": round(r["score"], 4),
            }

            item_json = json.dumps(item, ensure_ascii=False)
            item_tokens = count_tokens(item_json)

            if used_tokens + item_tokens > MAX_TOOL_RESULT_TOKENS:
                print(f"⚠️ [Retrieve] 토큰 제한 도달: {len(formatted)}개 문서")
                break

            formatted.append(item)
            used_tokens += item_tokens

        print(
            f"📊 [Retrieve] 검색 완료: {len(formatted)}/{len(results)}개 문서, {used_tokens} tokens"
        )

        # 컨텍스트 문자열 생성
        if not formatted:
            _total_elapsed = (_time.time() - _t_start) * 1000
            print(f"⏱️ [Retrieve] 총 소요시간: {_total_elapsed:.0f}ms")
            return {"context": ""}

        context_text = ""
        for i, ctx in enumerate(formatted[:10], 1):
            context_text += f"\n[문서 {i}]\n"
            context_text += f"팀: {ctx.get('team', 'unknown')}\n"
            context_text += f"주차: {ctx.get('week', 'unknown')}\n"
            context_text += f"내용:\n{ctx.get('text', '')}\n"
            context_text += "-" * 40

        # 검색 결과 JSON도 저장 (출처 추출용)
        _total_elapsed = (_time.time() - _t_start) * 1000
        print(f"⏱️ [Retrieve] 총 소요시간: {_total_elapsed:.0f}ms")
        return {
            "context": context_text,
            "messages": [
                ToolMessage(
                    content=json.dumps(formatted, ensure_ascii=False),
                    name="search_mail_content",
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
    print(f"📊 [Statistics] 통계 조회 시작: {question[:50]}...")

    tool_results = []

    try:
        # LLM에 통계 tool 바인딩
        _t_llm_start = _time.time()
        llm = get_llm().bind_tools(STATISTICS_TOOLS)
        response = llm.invoke(
            [
                SystemMessage(content=STATISTICS_SYSTEM_PROMPT),
                HumanMessage(content=question),
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
                    week = f"2025-{week_num}"
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

    # 프롬프트 구성
    if context:
        user_prompt = f"""질문: {question}

참고 정보:
{context}

위 정보를 바탕으로 질문에 답변해주세요."""
    else:
        user_prompt = f"""질문: {question}

일반적인 대화로 응답해주세요."""

    try:
        llm = get_llm()

        # 메시지 구성: 시스템 프롬프트 + 히스토리 + 현재 질문
        llm_messages = [{"role": "system", "content": ANSWER_SYSTEM_PROMPT}]

        # 히스토리 추가 (멀티턴 대화 지원)
        for msg in history_messages:
            if isinstance(msg, HumanMessage):
                llm_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                llm_messages.append({"role": "assistant", "content": msg.content})
            elif isinstance(msg, SystemMessage):
                llm_messages.append({"role": "system", "content": msg.content})

        # 현재 질문 추가
        llm_messages.append({"role": "user", "content": user_prompt})

        print(f"💬 [LLM Answer] LLM 메시지 수: {len(llm_messages)}")

        _t_llm_start = _time.time()
        response = llm.invoke(llm_messages)
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
_preprocessing_graph = None


def get_naive_rag_graph():
    """Naive RAG + LLM Router 그래프 (싱글톤) - 일반 응답용"""
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

    _naive_rag_graph = workflow.compile(checkpointer=MemorySaver())
    print("✅ Naive RAG + LLM Router 그래프 컴파일 완료")
    return _naive_rag_graph


def _route_for_preprocessing(
    state: GraphState,
) -> Literal["retrieve", "statistics", "end_preprocessing"]:
    """전처리 그래프용 라우팅: llm_answer 대신 end로"""
    route = state.get("route", "general")
    if route == "search":
        return "retrieve"
    elif route == "statistics":
        return "statistics"
    else:
        return "end_preprocessing"


def get_preprocessing_graph():
    """전처리 전용 그래프 (스트리밍용) - router + retrieve/statistics만 실행"""
    global _preprocessing_graph
    if _preprocessing_graph is not None:
        return _preprocessing_graph

    workflow = StateGraph(GraphState)

    # 노드 추가 (llm_answer 제외)
    workflow.add_node("router", router_node)
    workflow.add_node("retrieve", retrieve_document)
    workflow.add_node("statistics", statistics_node)
    workflow.add_node("end_preprocessing", lambda state: state)  # 패스스루 노드

    # 엣지 연결
    workflow.add_edge(START, "router")

    # router → 3분기 (retrieve/statistics/end)
    workflow.add_conditional_edges(
        "router",
        _route_for_preprocessing,
        {
            "retrieve": "retrieve",
            "statistics": "statistics",
            "end_preprocessing": "end_preprocessing",
        },
    )

    # retrieve, statistics → end
    workflow.add_edge("retrieve", "end_preprocessing")
    workflow.add_edge("statistics", "end_preprocessing")

    # end_preprocessing → END
    workflow.add_edge("end_preprocessing", END)

    _preprocessing_graph = workflow.compile(checkpointer=MemorySaver())
    print("✅ 전처리 그래프 컴파일 완료 (스트리밍용)")
    return _preprocessing_graph


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
    """Naive RAG + LLM Router 기반 채팅 (일반 응답)"""
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
                    "context": "",
                    "answer": "",
                    "messages": history_messages,
                    "route": "",
                },
                config=config,
            ),
        )
        print("✅ graph.invoke 완료")

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

    return {
        "answer": answer,
        "tool_calls": tool_calls_info,
        "tool_results": tool_results,
    }


# ========== 스트리밍 Chat 함수 (v5 신규) ==========
async def chat_with_agent_streaming(
    user_message: str,
    team: Optional[str] = None,
    week: Optional[str] = None,
    conversation_id: Optional[str] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """진짜 토큰 스트리밍 채팅

    1단계: 전처리 그래프 실행 (router + retrieve/statistics)
    2단계: LLM 직접 스트리밍 호출 (astream)

    Args:
        user_message: 사용자 메시지
        team: 팀 필터
        week: 주차 필터
        conversation_id: 대화 세션 ID
        on_chunk: 청크 콜백 함수 (선택사항)

    Yields:
        Dict with:
            - type: "preprocessing" | "token" | "done" | "error"
            - data: 해당 타입의 데이터
    """
    import time

    t_start = time.time()
    print(f"🚀 [chat_with_agent_streaming] 시작: {user_message[:50]}...")

    # 대화 히스토리 조회
    history = []
    if conversation_id:
        history = await get_history(conversation_id)
    history_messages = _convert_history_to_messages(history)

    full_answer = ""
    buffer = ""
    tool_results = []
    chunk_index = 0
    context = ""
    route = ""

    try:
        # ===== 1단계: 전처리 그래프 실행 (router + retrieve/statistics) =====
        yield {"type": "preprocessing", "data": {"status": "start", "step": "routing"}}

        preprocessing_graph = get_preprocessing_graph()
        config = RunnableConfig(
            recursion_limit=GRAPH_RECURSION_LIMIT,
            configurable={"thread_id": f"{conversation_id or 'single'}_preprocess"},
        )

        input_state = {
            "question": user_message,
            "context": "",
            "answer": "",
            "messages": history_messages,
            "route": "",
        }

        # 전처리 그래프 실행 (동기적으로 완료)
        loop = asyncio.get_event_loop()
        preprocess_result = await loop.run_in_executor(
            None,
            lambda: preprocessing_graph.invoke(input_state, config=config),
        )

        context = preprocess_result.get("context", "")
        route = preprocess_result.get("route", "general")
        preprocess_messages = preprocess_result.get("messages", [])

        # Tool 결과 추출
        for msg in preprocess_messages:
            if isinstance(msg, ToolMessage):
                tool_results.append({"name": msg.name, "result": msg.content})

        yield {
            "type": "preprocessing",
            "data": {
                "status": "done",
                "route": route,
                "context_length": len(context),
                "tool_results_count": len(tool_results),
            },
        }

        print(
            f"📊 [Preprocessing] route={route}, context={len(context)}자, tools={len(tool_results)}개"
        )

        # ===== 2단계: LLM 직접 스트리밍 =====
        yield {"type": "llm", "data": {"status": "start"}}

        # 프롬프트 구성
        if context:
            user_prompt = f"""질문: {user_message}

참고 정보:
{context}

위 정보를 바탕으로 질문에 답변해주세요."""
        else:
            user_prompt = f"""질문: {user_message}

일반적인 대화로 응답해주세요."""

        # LLM 메시지 구성
        llm_messages = [SystemMessage(content=ANSWER_SYSTEM_PROMPT)]

        # 히스토리 추가
        for msg in history_messages:
            if isinstance(msg, HumanMessage):
                llm_messages.append(HumanMessage(content=msg.content))
            elif isinstance(msg, AIMessage):
                llm_messages.append(AIMessage(content=msg.content))
            elif isinstance(msg, SystemMessage):
                llm_messages.append(SystemMessage(content=msg.content))

        # 현재 질문 추가
        llm_messages.append(HumanMessage(content=user_prompt))

        # 스트리밍 LLM 사용
        streaming_llm = get_llm(streaming=True)

        # LLM 직접 스트리밍 (astream 사용)
        async for chunk in streaming_llm.astream(llm_messages):
            if hasattr(chunk, "content") and chunk.content:
                token = chunk.content
                buffer += token
                full_answer += token

                # 문단 끝에서만 플러시 (\n\n)
                should_flush = any(
                    buffer.endswith(ending) for ending in STREAM_BUFFER_ENDINGS
                )

                if should_flush:
                    yield {
                        "type": "token",
                        "index": chunk_index,
                        "data": buffer,
                    }

                    # 콜백 호출 (외부 chatbot 전송용)
                    if on_chunk:
                        on_chunk(buffer)

                    chunk_index += 1
                    buffer = ""

        # 남은 버퍼 플러시
        if buffer:
            yield {
                "type": "token",
                "index": chunk_index,
                "data": buffer,
            }
            if on_chunk:
                on_chunk(buffer)

        elapsed = (time.time() - t_start) * 1000
        print(
            f"✅ [chat_with_agent_streaming] 완료: {elapsed:.0f}ms, {chunk_index + 1}개 청크"
        )

        # 완료 신호
        yield {
            "type": "done",
            "data": {
                "full_answer": full_answer,
                "tool_calls": [],
                "tool_results": tool_results,
                "route": route,
                "elapsed_ms": elapsed,
            },
        }

    except Exception as e:
        print(f"❌ [chat_with_agent_streaming] 실패: {e}")
        import traceback

        traceback.print_exc()
        yield {
            "type": "error",
            "data": {"message": str(e)},
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
        r"\n*참고:\s*(\[문서\s*\d+\],?\s*)+\.?$", "", answer, flags=re.MULTILINE
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
    """경량 히스토리 저장"""
    if mongo_db is None:
        return

    compressed = (
        assistant_answer[:MAX_ANSWER_LENGTH] + "...(생략)"
        if len(assistant_answer) > MAX_ANSWER_LENGTH
        else assistant_answer
    )

    messages_to_add = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": compressed},
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


async def summarize_conversation(messages: List[Dict]) -> str:
    """대화 요약"""
    if not messages:
        return ""

    conversation_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
    prompt = f"""다음 대화를 {MAX_SUMMARY_TOKENS}토큰 이내로 핵심만 요약하세요.

대화:
{conversation_text}

요약:"""

    try:
        response = await asyncio.to_thread(
            lambda: get_llm().invoke([{"role": "user", "content": prompt}])
        )
        return response.content.strip()
    except Exception as e:
        print(f"⚠️ 요약 실패: {e}")
        return "\n".join([f"{m['role']}: {m['content'][:200]}..." for m in messages])


async def get_history(conversation_id: str) -> List[Dict]:
    """대화 히스토리 조회"""
    if mongo_db is None:
        return []

    doc = await mongo_db.conversation_history.find_one(
        {"conversation_id": conversation_id}
    )
    if not doc:
        return []

    messages = doc.get("messages", [])
    existing_summary = doc.get("summary", "")
    turn_count = len(messages) // 2

    if turn_count <= SUMMARY_THRESHOLD:
        result = []
        if existing_summary:
            result.append(
                {"role": "system", "content": f"[이전 대화 요약]\n{existing_summary}"}
            )
        return result + messages

    # 요약 필요
    recent_count = RECENT_TURNS_TO_KEEP * 2
    old_messages = messages[:-recent_count] if recent_count > 0 else messages
    recent_messages = messages[-recent_count:] if recent_count > 0 else []

    new_summary = await summarize_conversation(old_messages)
    combined_summary = (
        f"{existing_summary}\n\n{new_summary}" if existing_summary else new_summary
    )

    await mongo_db.conversation_history.update_one(
        {"conversation_id": conversation_id},
        {
            "$set": {
                "summary": combined_summary,
                "messages": recent_messages,
                "updated_at": datetime.now(UTC),
            }
        },
    )

    return [
        {"role": "system", "content": f"[이전 대화 요약]\n{combined_summary}"}
    ] + recent_messages


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
    title="Weekly Mail RAG Chatbot API (Naive RAG + 실시간 토큰 스트리밍 v5)",
    description="""주간 메일 기반 Q&A API - LangGraph Naive RAG + LLM Router + 진짜 토큰 스트리밍

## 주요 기능
- LLM 기반 질문 유형 분류 (search/statistics/general)
- OpenSearch 하이브리드 검색
- 통계 Tool Binding
- 멀티턴 대화 지원
- **신규**: LLM 직접 스트리밍 (ChatGPT처럼 실시간 토큰 출력!)

## API
- POST /chat/v2 - 일반 채팅 (전체 응답)
- POST /chat/v2/stream - 실시간 스트리밍 채팅 (SSE, 토큰 단위)
- GET /stats/* - 통계 조회
- GET /health - 헬스체크

## 스트리밍 동작 방식
1. **전처리**: router → retrieve/statistics 실행 (context 수집)
2. **LLM 스트리밍**: 토큰 단위로 실시간 SSE 전송

## 스트리밍 이벤트 타입
- `preprocessing`: 전처리 단계 상태
- `llm`: LLM 스트리밍 시작
- `token`: LLM 토큰 스트리밍 (실시간!)
- `done`: 스트리밍 완료
- `references`: 참조 출처
- `error`: 에러 발생
""",
    version="5.1.0",
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
    """Naive RAG + LLM Router 채팅 API (일반 응답)"""
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    # Agent 호출
    result = await chat_with_agent(
        message,
        team=request.team,
        week=request.week,
        conversation_id=request.conversation_id,
    )

    # 출처 추출
    references = []
    search_contexts = []
    for tr in result["tool_results"]:
        if tr["name"] == "search_mail_content":
            try:
                search_results = json.loads(tr["result"])
                if isinstance(search_results, list):
                    search_contexts = search_results
            except (json.JSONDecodeError, TypeError):
                pass
    print(f"search_contexts: {search_contexts}")
    answer = result["answer"]
    if search_contexts:
        clean_answer, used_contexts = parse_used_references(answer, search_contexts)
        references = extract_references(used_contexts)
    else:
        clean_answer = answer

    # 후처리
    answer_converted = convert_table_to_bullet(clean_answer)
    answer_converted = clean_html_breaks(answer_converted)

    # MongoDB 저장
    if request.conversation_id:
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
        await save_history(
            conversation_id=request.conversation_id,
            user_message=message,
            assistant_answer=answer_converted,
        )

    return ChatV2Response(
        answer=answer_converted,
        tool_calls=[ToolCallInfo(**tc) for tc in result["tool_calls"]],
        tool_results=[ToolResultInfo(**tr) for tr in result["tool_results"]],
        references=references,
    )


@app.post("/chat/v2/stream")
async def chat_v2_stream(request: ChatV2Request):
    """실시간 토큰 스트리밍 채팅 API (SSE)

    2단계 처리:
    1. 전처리: router + retrieve/statistics 실행
    2. LLM 스트리밍: 토큰 단위로 실시간 전송

    이벤트 타입:
    - preprocessing: 전처리 단계 (routing, context 수집)
    - llm: LLM 스트리밍 시작
    - token: LLM 생성 토큰 (버퍼링되어 전송) ← 실시간!
    - done: 스트리밍 완료 (전체 답변 포함)
    - references: 참조 출처 정보
    - error: 에러 발생
    """
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    async def generate() -> AsyncGenerator[str, None]:
        full_answer = ""
        tool_results = []

        try:
            async for event in chat_with_agent_streaming(
                message,
                team=request.team,
                week=request.week,
                conversation_id=request.conversation_id,
            ):
                event_type = event.get("type", "")

                # 토큰 수집
                if event_type == "token":
                    full_answer += event.get("data", "")

                # 완료 시 데이터 수집
                elif event_type == "done":
                    done_data = event.get("data", {})
                    full_answer = done_data.get("full_answer", full_answer)
                    tool_results = done_data.get("tool_results", tool_results)

                # SSE 이벤트 전송 (모든 이벤트 즉시 전송)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # 출처 추출 및 MongoDB 저장
            references = []
            search_contexts = []
            for tr in tool_results:
                if tr.get("name") == "search_mail_content":
                    try:
                        search_results = json.loads(tr.get("result", "[]"))
                        if isinstance(search_results, list):
                            search_contexts = search_results
                    except (json.JSONDecodeError, TypeError):
                        pass

            if search_contexts:
                clean_answer, used_contexts = parse_used_references(
                    full_answer, search_contexts
                )
                references = extract_references(used_contexts)
            else:
                clean_answer = full_answer

            # 후처리
            answer_converted = convert_table_to_bullet(clean_answer)
            answer_converted = clean_html_breaks(answer_converted)

            # 참조 정보 전송
            if references:
                refs_event = {
                    "type": "references",
                    "data": [ref.model_dump() for ref in references],
                }
                yield f"data: {json.dumps(refs_event, ensure_ascii=False)}\n\n"

            # MongoDB 저장
            if request.conversation_id:
                await save_full_log(
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    message=message,
                    team=request.team,
                    week=request.week,
                    tool_calls=[],
                    tool_results=tool_results,
                    answer=answer_converted,
                    references=[ref.model_dump() for ref in references],
                )
                await save_history(
                    conversation_id=request.conversation_id,
                    user_message=message,
                    assistant_answer=answer_converted,
                )

        except Exception as e:
            error_event = {"type": "error", "data": {"message": str(e)}}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ========== 스트리밍 헬퍼 함수 (외부 chatbot용) ==========
async def stream_to_chatbot(
    user_message: str,
    send_func: Callable[[str], None],
    conversation_id: Optional[str] = None,
    team: Optional[str] = None,
    week: Optional[str] = None,
) -> Dict[str, Any]:
    """LangGraph 스트리밍을 사용하여 외부 chatbot에 실시간 전송

    사용 예시:
    ```python
    # 기존 코드 (한 번에 전송)
    # result = await chat_with_agent(message, ...)
    # send(chatbot, result["answer"])

    # v5 변경 후 (실시간 스트리밍)
    result = await stream_to_chatbot(
        message,
        send_func=lambda chunk: send(chatbot, chunk),
        conversation_id=request.conversation_id,
    )
    ```

    Args:
        user_message: 사용자 메시지
        send_func: 챗봇에 메시지를 보내는 함수 (동기 함수)
        conversation_id: 대화 세션 ID
        team: 팀 필터
        week: 주차 필터

    Returns:
        Dict with full_answer, tool_calls, tool_results
    """
    print(f"📤 [stream_to_chatbot] 스트리밍 시작: {user_message[:50]}...")

    full_answer = ""
    tool_calls_info = []
    tool_results = []
    chunk_count = 0

    async for event in chat_with_agent_streaming(
        user_message,
        team=team,
        week=week,
        conversation_id=conversation_id,
        on_chunk=send_func,  # 콜백으로 전달
    ):
        event_type = event.get("type", "")

        if event_type == "token":
            chunk_count += 1
            full_answer += event.get("data", "")

        elif event_type == "done":
            done_data = event.get("data", {})
            full_answer = done_data.get("full_answer", full_answer)
            tool_calls_info = done_data.get("tool_calls", [])
            tool_results = done_data.get("tool_results", [])

    print(f"✅ [stream_to_chatbot] 완료: {chunk_count}개 청크 전송")

    return {
        "answer": full_answer,
        "tool_calls": tool_calls_info,
        "tool_results": tool_results,
    }


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


# ========== 실행 ==========
if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("RAG Chatbot API Server (Naive RAG + LangGraph 스트리밍 v5)")
    print("=" * 50)
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Docs: http://localhost:{PORT}/docs")
    print(f"  OpenSearch: {OPENSEARCH_HOST}:{OPENSEARCH_PORT}")
    print(f"  Index: {INDEX_NAME}")
    print("  Graph: START -> router -> (retrieve|statistics|llm_answer) -> END")
    print("  Streaming: 문단 단위 (\\n\\n)")
    print("=" * 50)

    uvicorn.run(app, host=HOST, port=PORT)

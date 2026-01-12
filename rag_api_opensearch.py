"""
RAG Chatbot API Server (OpenSearch 버전)
- 사내 메신저 API 연동을 위한 REST API 서버
- FastAPI 기반
- OpenSearch 하이브리드 검색 (벡터 + 키워드 가중치 조절) + LLM 답변 생성
- LangGraph ReAct Agent 기반 Tool Calling 지원
"""

import os
import re
import json
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from opensearchpy import OpenSearch
from openai import OpenAI
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from dotenv import load_dotenv

# OpenSearch 집계 함수 import
from opensearch import (
    count_weekly_reports_by_team as _count_weekly_reports_by_team,
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
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "admin")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true"
INDEX_NAME = os.getenv("OPENSEARCH_INDEX", "weekly_mail")

# 임베딩 설정
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536

# LLM 설정
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss-120b")

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
        api_key = OPENAI_API_KEY or OPENROUTER_API_KEY
        return OpenAI(api_key=api_key)

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
        """하이브리드 검색 (벡터 + 키워드)

        최종 점수 = (벡터 유사도 × vector_weight) + (키워드 BM25 × keyword_weight)

        가중치 설정:
        - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
        - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
        - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값)
        """
        # 가중치 정규화 (합이 1이 아닐 경우)
        total_weight = vector_weight + keyword_weight
        if total_weight > 0:
            vector_weight = vector_weight / total_weight
            keyword_weight = keyword_weight / total_weight
        else:
            # 둘 다 0인 경우 기본값
            vector_weight = 0.7
            keyword_weight = 0.3

        score_map = {}

        # 1. 벡터 검색 (가중치가 0보다 클 때만)
        if vector_weight > 0:
            vector_results = self._search_vector(query, team, week, limit=limit * 2)
            if vector_results:
                max_vector = max(r["score"] for r in vector_results)
                for r in vector_results:
                    key = f"{r['team']}_{r['week']}_{r['mail_id']}_{r.get('part_index', 0)}"
                    normalized_score = r["score"] / max_vector if max_vector > 0 else 0
                    score_map[key] = {
                        "vector_score": normalized_score,
                        "keyword_score": 0,
                        "doc": r,
                    }

        # 2. 키워드 검색 (가중치가 0보다 클 때만)
        if keyword_weight > 0:
            keyword_results = self._search_keyword(query, team, week, limit=limit * 2)
            if keyword_results:
                max_keyword = max(r["score"] for r in keyword_results)
                for r in keyword_results:
                    key = f"{r['team']}_{r['week']}_{r['mail_id']}_{r.get('part_index', 0)}"
                    normalized_score = (
                        r["score"] / max_keyword if max_keyword > 0 else 0
                    )
                    if key in score_map:
                        score_map[key]["keyword_score"] = normalized_score
                    else:
                        score_map[key] = {
                            "vector_score": 0,
                            "keyword_score": normalized_score,
                            "doc": r,
                        }

        # 3. 최종 점수 계산 및 정렬
        final_results = []
        for key, data in score_map.items():
            final_score = (
                data["vector_score"] * vector_weight
                + data["keyword_score"] * keyword_weight
            )
            doc = data["doc"]
            doc["score"] = final_score
            doc["vector_score"] = data["vector_score"]
            doc["keyword_score"] = data["keyword_score"]
            final_results.append(doc)

        # 점수 내림차순 정렬
        final_results.sort(key=lambda x: x["score"], reverse=True)

        return final_results[:limit]

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
        base_url="https://openrouter.ai/api/v1",
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
def search_mail_content(
    query: str, team: Optional[str] = None, week: Optional[str] = None
) -> str:
    """메일 본문 내용을 검색합니다. 특정 정보, 이슈, 키워드를 찾을 때 사용합니다. 통계가 아닌 내용 검색에 사용하세요.

    Args:
        query: 검색 질문 또는 키워드
        team: 특정 팀 필터
        week: 주차 필터
    """
    try:
        if os_client:
            results = os_client.search(query, team=team, week=week, limit=5)
            formatted = [
                {
                    "team": r["team"],
                    "week": r["week"],
                    "mail_id": r["mail_id"],
                    "text": (
                        r["text"][:500] + "..." if len(r["text"]) > 500 else r["text"]
                    ),
                    "score": round(r["score"], 4),
                }
                for r in results
            ]
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

## 답변 원칙
1. 도구 실행 결과를 바탕으로 명확하게 답변하세요.
2. 숫자와 팀명을 정확히 포함하세요.
3. 표 형식이 적절하면 표로 정리하세요.
4. 결과가 없으면 솔직히 알려주세요.

## 도구 사용 가이드
- 주간보고 통계: count_weekly_reports_by_team
- 일반 메일 통계: count_other_mails_by_team
- 미제출 팀: get_missing_teams
- 전체 요약: get_mail_type_summary
- 내용 검색: search_mail_content
- 주차 목록: get_available_weeks
"""

# LangGraph ReAct Agent (지연 초기화)
_react_agent = None


def get_react_agent():
    """LangGraph ReAct Agent 가져오기 (싱글톤)"""
    global _react_agent
    if _react_agent is None:
        llm = get_llm()
        _react_agent = create_react_agent(
            model=llm,
            tools=AGENT_TOOLS,
            prompt=AGENT_SYSTEM_PROMPT,
        )
    return _react_agent


def chat_with_tools(
    user_message: str, team: str = None, week: str = None
) -> Dict[str, Any]:
    """LangGraph ReAct Agent를 사용한 채팅

    LLM이 질문을 분석하여 적절한 tool을 선택하고 실행합니다.
    ReAct 패턴으로 multi-step 추론이 가능합니다.

    Args:
        user_message: 사용자 질문
        team: 팀 필터 (optional)
        week: 주차 필터 (optional)

    Returns:
        answer: LLM 답변
        tool_calls: 호출된 tool 정보
        tool_results: tool 실행 결과
    """
    agent = get_react_agent()

    # 컨텍스트 정보 추가
    context_info = ""
    if team:
        context_info += f"\n[컨텍스트] 팀 필터: {team}"
    if week:
        context_info += f"\n[컨텍스트] 주차 필터: {week}"

    full_message = user_message + context_info

    # Agent 실행
    result = agent.invoke({"messages": [{"role": "user", "content": full_message}]})

    # 결과 파싱
    messages = result.get("messages", [])
    tool_calls_info = []
    tool_results = []
    answer = ""

    for msg in messages:
        # AIMessage에서 tool_calls 추출
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls_info.append(
                    {"name": tc.get("name", ""), "arguments": tc.get("args", {})}
                )

        # ToolMessage에서 결과 추출
        if hasattr(msg, "type") and msg.type == "tool":
            tool_results.append(
                {"name": getattr(msg, "name", "unknown"), "result": msg.content}
            )

        # 마지막 AIMessage가 최종 답변
        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
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
    """검색 결과에서 참조 출처 추출"""
    refs = []
    seen = set()

    for ctx in contexts:
        team = ctx.get("team", "unknown")
        week = ctx.get("week", "unknown")
        mail_id = ctx.get("mail_id", "unknown")
        part_index = ctx.get("part_index")

        # 중복 제거 (같은 메일의 다른 파트는 허용)
        key = f"{team}_{week}_{mail_id}_{part_index}"
        if key in seen:
            continue
        seen.add(key)

        # /mail 경로로 static files 서빙: {week}_{team}_{mail_id}.html 형식
        url = f"{API_BASE_URL}/mail/{week}_{team}_{mail_id}.html"
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


@app.on_event("startup")
async def startup():
    """서버 시작 시 OpenSearch 클라이언트 초기화"""
    global os_client
    os_client = OpenSearchClient()
    health = os_client.health_check()
    stats = os_client.get_stats()
    print(f"✅ OpenSearch 초기화 완료: {health}")
    print(f"📊 인덱스 통계: {stats}")


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


@app.post("/chat/v2", response_model=ChatV2Response)
async def chat_v2(request: ChatV2Request):
    """Tool Calling 기반 채팅 API

    LLM이 질문을 분석하여 적절한 도구를 선택하고 실행합니다.

    - 통계 질문 → 집계 함수 자동 호출
    - 내용 검색 → RAG 검색 자동 호출

    예시 질문:
    - "2025-48주차 주간보고 팀별 count 알려줘"
    - "YIELD팀이 보낸 주간보고 외 메일은 몇 개야?"
    - "이번주 주간보고 미제출 팀은?"
    - "수율 개선 이슈 뭐야?"
    """
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    result = chat_with_tools(message, team=request.team, week=request.week)

    return ChatV2Response(
        answer=result["answer"],
        tool_calls=[ToolCallInfo(**tc) for tc in result["tool_calls"]],
        tool_results=[ToolResultInfo(**tr) for tr in result["tool_results"]],
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

"""
graph.py - 비즈니스 로직 계층
- 통계 Tools
- GraphState, 프롬프트
- 노드 함수들 (router, retrieve, statistics, llm_answer)
- 그래프 빌드
- chat_with_agent()
"""

import json
import asyncio
import re
from typing import List, Dict, Optional, Any, Annotated, Literal, TypedDict

from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    AIMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig

# OpenSearch 집계 함수 import
from opensearch import (
    count_weekly_reports_by_team as _count_weekly_reports_by_team,
    count_daily_reports_by_team as _count_daily_reports_by_team,
    count_other_mails_by_team as _count_other_mails_by_team,
    get_missing_teams as _get_missing_teams,
    get_mail_type_summary as _get_mail_type_summary,
    get_unique_weeks as _get_unique_weeks,
)

from .core import (
    get_llm,
    count_tokens,
    get_history,
    MAX_TOOL_RESULT_TOKENS,
    SEARCH_RESULT_LIMIT,
    MAX_TEXT_PER_DOC,
    GRAPH_RECURSION_LIMIT,
    MAX_LLM_HISTORY_TURNS,
)

# OpenSearch 클라이언트 (main.py에서 주입)
os_client = None


def set_os_client(client):
    """OpenSearch 클라이언트 설정 (main.py에서 호출)"""
    global os_client
    os_client = client


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
    context: str  # 검색/통계 결과 (컨텍스트)
    answer: str  # 최종 답변
    messages: Annotated[list, add_messages]  # 대화 히스토리
    route: str  # 라우팅 결과 (search/statistics/general)
    mail_type: Optional[str]  # 메일 유형 필터 (weekly_report/daily_report/None)


class RouteDecision(BaseModel):
    """라우터 분류 결과"""

    route: Literal["search", "statistics", "general"] = Field(
        description="질문 유형: search(내용 검색), statistics(통계 조회), general(일반 대화)"
    )
    reason: str = Field(description="분류 이유 (디버깅용)")


# ===== 프롬프트 =====
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

ANSWER_SYSTEM_PROMPT = """당신은 반도체 주간 업무 보고서 시스템의 AI 어시스턴트입니다.

## 답변 원칙
1. 제공된 컨텍스트(context)를 기반으로 답변하세요.
2. 컨텍스트가 없으면 일반적인 대화로 응답하세요.
3. 답변은 간결하고 명확하게 작성하세요.
4. 숫자와 팀명을 정확히 포함하세요.

## 출처 표시 (검색 결과가 있을 때)
- 답변에 사용한 정보는 [문서 1], [문서 2] 형식으로 출처를 표시하세요.
- 답변 마지막에 "참고: [문서 1], [문서 3]" 형태로 사용한 문서를 요약하세요."""

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


# ===== 노드 함수들 =====
def router_node(state: GraphState) -> Dict[str, Any]:
    """Router 노드: LLM이 질문 유형(route) + 메일 유형(mail_type) 동시 판단"""
    import time as _time

    _t_start = _time.time()

    question = state["question"]
    print(f"🔀 [Router] 질문 분류 시작: {question[:50]}...")

    # LLM이 route와 mail_type을 함께 판단
    simple_prompt = f"""사용자 질문을 분석하세요.

## 출력 형식 (반드시 이 형식으로 두 줄 출력)
route: [search/statistics/general]
mail_type: [daily/weekly/all]

## route 분류 기준
- statistics: 제출/미제출 현황, 팀 수, 개수 등 **수치/통계** 질문
- search: 메일 **내용** 검색 (이슈, 분석, 개선 사항 등)
- general: 인사, 감사, 도움말 등 일반 대화

## mail_type 분류 기준
- daily: 일일보고/데일리 메일만 검색할 때
- weekly: 주간보고/주보만 검색할 때
- all: 둘 다 검색하거나 구분이 불명확할 때

## 예시
- "47주차 주보 미제출 팀 알려줘" → route: statistics, mail_type: weekly
- "데일리 메일 이슈 알려줘" → route: search, mail_type: daily
- "오늘 주보 검색해줘" → route: search, mail_type: weekly (주보=주간보고)
- "일일보고 분석해줘" → route: search, mail_type: daily
- "PROCESS팀 수율 이슈 알려줘" → route: search, mail_type: all
- "이번주 개선 사항 뭐야?" → route: search, mail_type: weekly
- "안녕" → route: general, mail_type: all

질문: {question}

출력:"""

    route = "general"
    mail_type = None  # None이면 전체 검색

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

        _elapsed = (_time.time() - _t_start) * 1000
        print(f"🔀 [Router] 분류 결과: route={route}, mail_type={mail_type}")
        print(f"   LLM 응답: {answer[:80]}...")
        print(f"   ⏱️ {_elapsed:.0f}ms")

    except Exception as e:
        print(f"⚠️ [Router] 분류 실패, 기본값 사용: {e}")
        route = "general"
        mail_type = None

    return {"route": route, "mail_type": mail_type}


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
    mail_type = state.get("mail_type")  # 메일 유형 필터
    print(f"🔍 [Retrieve] 검색 시작: {question[:50]}...")
    if mail_type:
        print(f"📧 [Retrieve] 메일 유형 필터: {mail_type}")

    if not os_client:
        print("⚠️ [Retrieve] OpenSearch 클라이언트 없음")
        return {"context": ""}

    try:
        _t_search_start = _time.time()
        results = os_client.search(
            question,
            team=None,
            week=None,
            mail_type=mail_type,
            limit=SEARCH_RESULT_LIMIT,
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
        for i, ctx in enumerate(formatted, 1):
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

    _naive_rag_graph = workflow.compile(checkpointer=MemorySaver())
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


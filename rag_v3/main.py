"""
main.py - API 계층
- FastAPI 앱 생성
- lifespan (초기화/종료)
- 엔드포인트들
"""

import json
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

# OpenSearch 집계 함수 import
from opensearch import (
    count_weekly_reports_by_team as _count_weekly_reports_by_team,
    count_other_mails_by_team as _count_other_mails_by_team,
    get_missing_teams as _get_missing_teams,
    get_mail_type_summary as _get_mail_type_summary,
    get_unique_weeks as _get_unique_weeks,
)

from .core import (
    # 설정
    HOST,
    PORT,
    MAIL_DIR,
    TEAMS,
    # 클라이언트
    OpenSearchClient,
    # 스키마
    Reference,
    ChatV2Request,
    ChatV2Response,
    ToolCallInfo,
    ToolResultInfo,
    # 유틸리티
    parse_used_references,
    extract_references,
    convert_table_to_bullet,
    clean_html_breaks,
    # MongoDB
    init_mongodb,
    close_mongodb,
    save_full_log,
    save_history,
)

from .graph import (
    chat_with_agent,
    set_os_client,
)


# ========== FastAPI ==========
os_client: Optional[OpenSearchClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 초기화"""
    global os_client
    os_client = OpenSearchClient()
    set_os_client(os_client)  # graph.py에 클라이언트 주입
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

Path(MAIL_DIR).mkdir(exist_ok=True)
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
        if tr["name"] == "retrieve":
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
    print("RAG Chatbot API Server (Naive RAG + LLM Router v3)")
    print("=" * 50)
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Docs: http://localhost:{PORT}/docs")
    print("  Graph: START -> router -> (retrieve|statistics|llm_answer) -> END")
    print("=" * 50)

    uvicorn.run(app, host=HOST, port=PORT)


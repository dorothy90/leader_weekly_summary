"""
Weekly Mail RAG Chatbot - Streamlit UI
- /chat/v2 API 전용 (Naive RAG + LLM Router)
- 멀티턴 대화 지원 (conversation_id)
- 모던 채팅 UI
"""

import streamlit as st
import requests
import json
import uuid
import time
from typing import Optional, Dict, Any, List

# ==================== 페이지 설정 ====================
st.set_page_config(
    page_title="Weekly Mail RAG Chatbot",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== 커스텀 CSS ====================
st.markdown(
    """
<style>
/* 전체 배경 */
.stApp {
    background-color: #f8f9fb;
}

/* 사이드바 */
section[data-testid="stSidebar"] {
    background-color: #ffffff;
    border-right: 1px solid #e8eaed;
}
section[data-testid="stSidebar"] .stMarkdown h1,
section[data-testid="stSidebar"] .stMarkdown h2,
section[data-testid="stSidebar"] .stMarkdown h3 {
    color: #1a1a2e;
}

/* 헤더 영역 */
.main-header {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    padding: 1.5rem 2rem;
    border-radius: 12px;
    margin-bottom: 1.5rem;
    color: white;
}
.main-header h1 {
    margin: 0;
    font-size: 1.6rem;
    font-weight: 700;
    color: white !important;
}
.main-header p {
    margin: 0.3rem 0 0;
    font-size: 0.9rem;
    opacity: 0.85;
}

/* 채팅 컨테이너 */
.chat-container {
    max-width: 900px;
    margin: 0 auto;
}

/* 상태 배지 */
.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 500;
}
.status-connected {
    background-color: #e8f5e9;
    color: #2e7d32;
}
.status-disconnected {
    background-color: #ffebee;
    color: #c62828;
}

/* 참고 출처 카드 */
.ref-card {
    background: #f8f9fa;
    border: 1px solid #e9ecef;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 4px 0;
    transition: all 0.2s ease;
}
.ref-card:hover {
    border-color: #667eea;
    box-shadow: 0 2px 8px rgba(102, 126, 234, 0.15);
}
.ref-team {
    font-weight: 600;
    color: #1a1a2e;
    font-size: 0.88rem;
}
.ref-meta {
    color: #6c757d;
    font-size: 0.78rem;
}

/* Tool 정보 */
.tool-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: #eef2ff;
    border: 1px solid #c7d2fe;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 0.8rem;
    color: #4338ca;
    font-family: 'SF Mono', 'Fira Code', monospace;
    margin: 2px;
}

/* 라우트 배지 */
.route-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.3px;
}
.route-search {
    background: #dbeafe;
    color: #1e40af;
}
.route-statistics {
    background: #fef3c7;
    color: #92400e;
}
.route-general {
    background: #d1fae5;
    color: #065f46;
}

/* 빈 상태 */
.empty-state {
    text-align: center;
    padding: 4rem 2rem;
    color: #9ca3af;
}
.empty-state .icon {
    font-size: 3rem;
    margin-bottom: 1rem;
}
.empty-state h3 {
    color: #6b7280;
    margin-bottom: 0.5rem;
}
.empty-state p {
    font-size: 0.9rem;
}

/* 예시 질문 버튼 */
.example-btn {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 12px 16px;
    text-align: left;
    cursor: pointer;
    transition: all 0.2s ease;
    width: 100%;
    font-size: 0.88rem;
    color: #374151;
}
.example-btn:hover {
    border-color: #667eea;
    background: #f8f9ff;
    box-shadow: 0 2px 8px rgba(102, 126, 234, 0.1);
}

/* 응답 시간 */
.response-time {
    font-size: 0.72rem;
    color: #9ca3af;
    margin-top: 6px;
}

/* 스크롤바 스타일링 */
::-webkit-scrollbar {
    width: 6px;
}
::-webkit-scrollbar-track {
    background: transparent;
}
::-webkit-scrollbar-thumb {
    background: #d1d5db;
    border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
    background: #9ca3af;
}
</style>
""",
    unsafe_allow_html=True,
)


# ==================== 세션 상태 초기화 ====================
def init_session_state():
    defaults = {
        "messages": [],
        "conversation_id": str(uuid.uuid4()),
        "api_url": "http://localhost:8002",
        "server_connected": False,
        "total_messages": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()


# ==================== API 함수 ====================
def check_health(api_url: str) -> Dict[str, Any]:
    """서버 헬스체크"""
    try:
        resp = requests.get(f"{api_url}/health", timeout=5)
        if resp.status_code == 200:
            return {"connected": True, "data": resp.json()}
    except Exception:
        pass
    return {"connected": False}


def get_weeks(api_url: str) -> List[str]:
    """주차 목록 조회"""
    try:
        resp = requests.get(f"{api_url}/weeks", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("weeks", [])
    except Exception:
        pass
    return []


def get_teams(api_url: str) -> List[str]:
    """팀 목록 조회"""
    try:
        resp = requests.get(f"{api_url}/teams", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("teams", [])
    except Exception:
        pass
    return []


def call_chat_v2(
    api_url: str,
    message: str,
    user_id: str = "streamlit_user",
    conversation_id: Optional[str] = None,
    team: Optional[str] = None,
    week: Optional[str] = None,
) -> Dict[str, Any]:
    """chat/v2 API 호출"""
    payload = {
        "user_id": user_id,
        "message": message,
        "conversation_id": conversation_id,
        "team": team,
        "week": week,
    }
    try:
        resp = requests.post(f"{api_url}/chat/v2", json=payload, timeout=180)
        if resp.status_code == 200:
            return {"ok": True, "data": resp.json()}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "요청 시간 초과 (180초)"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "서버에 연결할 수 없습니다. API 서버가 실행 중인지 확인하세요."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==================== 사이드바 ====================
def render_sidebar():
    with st.sidebar:
        # 로고 & 타이틀
        st.markdown("### Weekly Mail RAG")
        st.caption("Naive RAG + LLM Router v3")

        st.divider()

        # 서버 연결
        st.markdown("##### 서버 연결")
        api_url = st.text_input(
            "API URL",
            value=st.session_state.api_url,
            label_visibility="collapsed",
            placeholder="http://localhost:8002",
        )
        st.session_state.api_url = api_url

        health = check_health(api_url)
        st.session_state.server_connected = health["connected"]

        if health["connected"]:
            st.markdown(
                '<div class="status-badge status-connected">● 연결됨</div>',
                unsafe_allow_html=True,
            )
            data = health.get("data", {})
            db_stats = data.get("db_stats", {})
            if db_stats:
                total_docs = db_stats.get("total_documents", "N/A")
                st.caption(f"인덱싱 문서: {total_docs:,}개" if isinstance(total_docs, int) else f"인덱싱 문서: {total_docs}")
        else:
            st.markdown(
                '<div class="status-badge status-disconnected">● 연결 실패</div>',
                unsafe_allow_html=True,
            )

        st.divider()

        # 필터 설정
        st.markdown("##### 필터")

        teams = get_teams(api_url) if health["connected"] else []
        weeks = get_weeks(api_url) if health["connected"] else []

        selected_team = st.selectbox(
            "팀",
            ["전체"] + teams,
            help="특정 팀의 메일만 검색합니다",
        )
        selected_week = st.selectbox(
            "주차",
            ["전체"] + weeks,
            help="특정 주차의 메일만 검색합니다",
        )

        team_filter = None if selected_team == "전체" else selected_team
        week_filter = None if selected_week == "전체" else selected_week

        st.divider()

        # 대화 관리
        st.markdown("##### 대화 관리")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("메시지", len(st.session_state.messages))
        with col2:
            turn_count = len([m for m in st.session_state.messages if m["role"] == "user"])
            st.metric("턴", turn_count)

        conv_id_short = st.session_state.conversation_id[:8] + "..."
        st.caption(f"세션: `{conv_id_short}`")

        if st.button("새 대화 시작", use_container_width=True, type="primary"):
            st.session_state.messages = []
            st.session_state.conversation_id = str(uuid.uuid4())
            st.rerun()

        if st.button("대화 내보내기 (JSON)", use_container_width=True):
            export_data = {
                "conversation_id": st.session_state.conversation_id,
                "messages": st.session_state.messages,
            }
            st.download_button(
                "다운로드",
                data=json.dumps(export_data, ensure_ascii=False, indent=2),
                file_name=f"chat_{st.session_state.conversation_id[:8]}.json",
                mime="application/json",
                use_container_width=True,
            )

        st.divider()
        st.caption("Built with Streamlit + FastAPI")

    return team_filter, week_filter


# ==================== 메시지 렌더링 ====================
def render_assistant_message(msg: Dict):
    """어시스턴트 메시지 렌더링 (답변 + 메타데이터)"""
    st.markdown(msg["content"])

    # 응답 시간
    if msg.get("elapsed_ms"):
        st.markdown(
            f'<div class="response-time">응답 시간: {msg["elapsed_ms"]:.0f}ms</div>',
            unsafe_allow_html=True,
        )

    # Tool 호출 정보
    tool_calls = msg.get("tool_calls", [])
    if tool_calls:
        with st.expander(f"🔧 사용된 도구 ({len(tool_calls)}개)", expanded=False):
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                args = tc.get("arguments", {})
                args_str = json.dumps(args, ensure_ascii=False)
                st.markdown(
                    f'<div class="tool-chip">{name}({args_str})</div>',
                    unsafe_allow_html=True,
                )

    # Tool 결과 (raw)
    tool_results = msg.get("tool_results", [])
    if tool_results:
        with st.expander("📋 도구 실행 결과 (raw)", expanded=False):
            for tr in tool_results:
                name = tr.get("name", "unknown")
                result = tr.get("result", "")
                st.markdown(f"**{name}**")
                # JSON 파싱 시도
                try:
                    parsed = json.loads(result) if isinstance(result, str) else result
                    st.json(parsed)
                except (json.JSONDecodeError, TypeError):
                    st.code(str(result)[:2000], language="text")

    # 참고 출처
    references = msg.get("references", [])
    if references:
        with st.expander(f"📎 참고 출처 ({len(references)}개)", expanded=False):
            for ref in references:
                team = ref.get("team", "")
                week = ref.get("week", "")
                mail_id = ref.get("mail_id", "")
                url = ref.get("url", "")
                score = ref.get("score", 0)
                part = ref.get("part_index")
                total = ref.get("total_parts")

                part_info = f" (파트 {part + 1}/{total})" if part is not None else ""

                st.markdown(
                    f"""<div class="ref-card">
                        <div class="ref-team">{team} &middot; {week}{part_info}</div>
                        <div class="ref-meta">mail_id: {mail_id} &middot; score: {score:.4f}</div>
                        <a href="{url}" target="_blank" style="font-size:0.8rem; color:#667eea;">원본 보기 ↗</a>
                    </div>""",
                    unsafe_allow_html=True,
                )


# ==================== 빈 상태 (예시 질문) ====================
EXAMPLE_QUESTIONS = [
    "이번 주 수율 이슈 알려줘",
    "48주차 주간보고 미제출 팀 알려줘",
    "PROCESS팀 최근 업무 내용 요약해줘",
    "ALD 공정 관련 이슈 알려줘",
    "데일리 메일에서 주요 개선 사항 알려줘",
    "팀별 주간보고 제출 현황 알려줘",
]


def render_empty_state():
    """대화가 없을 때 빈 상태 UI"""
    st.markdown(
        """<div class="empty-state">
            <div class="icon">💬</div>
            <h3>주간 메일 RAG 챗봇</h3>
            <p>반도체 주간 업무 보고서를 검색하고 분석합니다.<br>
            아래 예시를 클릭하거나 직접 질문을 입력하세요.</p>
        </div>""",
        unsafe_allow_html=True,
    )

    # 예시 질문 그리드
    cols = st.columns(2)
    for i, q in enumerate(EXAMPLE_QUESTIONS):
        with cols[i % 2]:
            if st.button(
                q,
                key=f"example_{i}",
                use_container_width=True,
            ):
                st.session_state["_pending_question"] = q
                st.rerun()


# ==================== 메인 ====================
def main():
    team_filter, week_filter = render_sidebar()

    # 헤더
    st.markdown(
        """<div class="main-header">
            <h1>Weekly Mail RAG Chatbot</h1>
            <p>Naive RAG + LLM Router &middot; OpenSearch 하이브리드 검색 &middot; 멀티턴 대화</p>
        </div>""",
        unsafe_allow_html=True,
    )

    # 필터 표시
    filter_parts = []
    if team_filter:
        filter_parts.append(f"팀: **{team_filter}**")
    if week_filter:
        filter_parts.append(f"주차: **{week_filter}**")
    if filter_parts:
        st.info("🔍 활성 필터: " + " | ".join(filter_parts))

    # 빈 상태 또는 대화 히스토리
    if not st.session_state.messages:
        render_empty_state()
    else:
        # 채팅 히스토리 렌더링
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                if msg["role"] == "assistant":
                    render_assistant_message(msg)
                else:
                    st.markdown(msg["content"])

    # 예시 질문에서 선택된 경우 처리
    pending = st.session_state.pop("_pending_question", None)

    # 채팅 입력
    user_input = st.chat_input(
        "질문을 입력하세요...",
        disabled=not st.session_state.server_connected,
    )

    # 입력 처리 (직접 입력 또는 예시 질문)
    prompt = user_input or pending

    if prompt:
        # 사용자 메시지 추가 & 표시
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # API 호출 & 응답
        with st.chat_message("assistant"):
            # 로딩 상태
            status_placeholder = st.empty()
            status_placeholder.markdown("⏳ 답변을 생성하고 있습니다...")

            t_start = time.time()
            result = call_chat_v2(
                api_url=st.session_state.api_url,
                message=prompt,
                conversation_id=st.session_state.conversation_id,
                team=team_filter,
                week=week_filter,
            )
            elapsed_ms = (time.time() - t_start) * 1000

            status_placeholder.empty()

            if result["ok"]:
                data = result["data"]
                answer = data.get("answer", "답변을 생성하지 못했습니다.")
                tool_calls = data.get("tool_calls", [])
                tool_results = data.get("tool_results", [])
                references = data.get("references", [])

                # 어시스턴트 메시지 저장
                assistant_msg = {
                    "role": "assistant",
                    "content": answer,
                    "tool_calls": tool_calls,
                    "tool_results": tool_results,
                    "references": references,
                    "elapsed_ms": elapsed_ms,
                }
                st.session_state.messages.append(assistant_msg)

                # 렌더링
                render_assistant_message(assistant_msg)
            else:
                error_msg = f"오류가 발생했습니다: {result['error']}"
                st.error(error_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"❌ {error_msg}",
                })


if __name__ == "__main__":
    main()

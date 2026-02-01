"""
RAG API v3 Chat 테스트용 Streamlit 앱
- /chat/v2 API 전용
- conversation_id 지원 (멀티턴 대화)
"""

import streamlit as st
import requests
import json
import uuid
from typing import Optional, Dict, Any

# 페이지 설정
st.set_page_config(
    page_title="RAG Chat v3",
    page_icon="💬",
    layout="wide",
)

# CSS 스타일
st.markdown("""
<style>
    .stChatMessage {
        padding: 1rem;
    }
    .tool-info {
        background-color: #f0f2f6;
        padding: 0.5rem;
        border-radius: 0.5rem;
        font-size: 0.85rem;
    }
    .reference-link {
        color: #1f77b4;
        text-decoration: none;
    }
</style>
""", unsafe_allow_html=True)


# 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = str(uuid.uuid4())


def call_chat_v2(
    api_url: str,
    message: str,
    user_id: str,
    conversation_id: str,
    team: Optional[str] = None,
    week: Optional[str] = None,
) -> Dict[str, Any]:
    """채팅 API 호출"""
    payload = {
        "user_id": user_id,
        "message": message,
        "conversation_id": conversation_id,
        "team": team,
        "week": week,
    }
    try:
        response = requests.post(f"{api_url}/chat/v2", json=payload, timeout=120)
        if response.status_code == 200:
            return {"status": "ok", "data": response.json()}
        return {"status": "error", "message": f"HTTP {response.status_code}: {response.text}"}
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "요청 시간 초과 (120초)"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def check_health(api_url: str) -> bool:
    """헬스체크"""
    try:
        response = requests.get(f"{api_url}/health", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def get_weeks(api_url: str) -> list:
    """주차 목록 조회"""
    try:
        response = requests.get(f"{api_url}/weeks", timeout=5)
        if response.status_code == 200:
            return response.json().get("weeks", [])
    except Exception:
        pass
    return []


def get_teams(api_url: str) -> list:
    """팀 목록 조회"""
    try:
        response = requests.get(f"{api_url}/teams", timeout=5)
        if response.status_code == 200:
            return response.json().get("teams", [])
    except Exception:
        pass
    return []


def main():
    # 헤더
    st.title("💬 RAG Chat v3 테스트")
    st.caption("Naive RAG + LLM Router | 멀티턴 대화 지원")

    # 사이드바
    with st.sidebar:
        st.header("⚙️ 설정")

        api_url = st.text_input(
            "API 엔드포인트",
            value="http://localhost:8002",
            help="RAG API 서버 주소",
        )

        # 연결 상태
        if check_health(api_url):
            st.success("✅ 서버 연결됨")
        else:
            st.error("❌ 서버 연결 실패")

        st.divider()

        # 필터
        st.subheader("🔍 필터")
        teams = get_teams(api_url)
        weeks = get_weeks(api_url)

        selected_team = st.selectbox("팀", ["전체"] + teams)
        selected_week = st.selectbox("주차", ["전체"] + weeks)

        team_filter = None if selected_team == "전체" else selected_team
        week_filter = None if selected_week == "전체" else selected_week

        st.divider()

        # 세션 정보
        st.subheader("📋 세션 정보")
        st.text_input(
            "Conversation ID",
            value=st.session_state.conversation_id,
            disabled=True,
            help="현재 대화 세션 ID",
        )
        st.caption(f"메시지 수: {len(st.session_state.messages)}")

        # 새 대화 시작
        if st.button("🔄 새 대화 시작", use_container_width=True):
            st.session_state.messages = []
            st.session_state.conversation_id = str(uuid.uuid4())
            st.rerun()

    # 채팅 히스토리 표시
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

            # 도구 호출 정보
            if msg.get("tool_calls"):
                with st.expander("🔧 사용된 도구", expanded=False):
                    for tc in msg["tool_calls"]:
                        st.code(f"{tc['name']}({json.dumps(tc.get('arguments', {}), ensure_ascii=False)})")

            # 참고 출처
            if msg.get("references"):
                with st.expander("📎 참고 출처", expanded=False):
                    for ref in msg["references"]:
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.markdown(f"**{ref['team']}** | {ref['week']} | {ref['mail_id']}")
                        with col2:
                            st.markdown(f"[열기]({ref['url']})")

    # 입력
    if prompt := st.chat_input("질문을 입력하세요..."):
        # 사용자 메시지 추가
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            st.markdown(prompt)

        # API 호출
        with st.chat_message("assistant"):
            with st.spinner("답변 생성 중..."):
                result = call_chat_v2(
                    api_url=api_url,
                    message=prompt,
                    user_id="streamlit_user",
                    conversation_id=st.session_state.conversation_id,
                    team=team_filter,
                    week=week_filter,
                )

            if result["status"] == "ok":
                data = result["data"]
                answer = data.get("answer", "")
                tool_calls = data.get("tool_calls", [])
                references = data.get("references", [])

                st.markdown(answer)

                # 도구 호출 정보
                if tool_calls:
                    with st.expander("🔧 사용된 도구", expanded=False):
                        for tc in tool_calls:
                            st.code(f"{tc['name']}({json.dumps(tc.get('arguments', {}), ensure_ascii=False)})")

                # 참고 출처
                if references:
                    with st.expander("📎 참고 출처", expanded=False):
                        for ref in references:
                            col1, col2 = st.columns([3, 1])
                            with col1:
                                st.markdown(f"**{ref['team']}** | {ref['week']} | {ref['mail_id']}")
                            with col2:
                                st.markdown(f"[열기]({ref['url']})")

                # 어시스턴트 메시지 저장
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "tool_calls": tool_calls,
                    "references": references,
                })
            else:
                error_msg = f"❌ 오류: {result['message']}"
                st.error(error_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": error_msg,
                })


if __name__ == "__main__":
    main()



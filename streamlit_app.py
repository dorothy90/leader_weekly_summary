"""
RAG API OpenSearch 테스트용 Streamlit 앱
- 채팅, 검색, 통계 조회 기능 제공
"""

import streamlit as st
import requests
import json
from typing import Optional, Dict, Any

# 페이지 설정
st.set_page_config(
    page_title="RAG API 테스트",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 세션 상태 초기화
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_history_v2" not in st.session_state:
    st.session_state.chat_history_v2 = []


# ========== API 클라이언트 ==========
def get_api_base_url() -> str:
    """API 베이스 URL 가져오기"""
    return st.sidebar.text_input(
        "API 엔드포인트",
        value="http://localhost:8002",
        help="RAG API 서버 주소",
    )


def check_health(api_url: str) -> Dict[str, Any]:
    """헬스체크"""
    try:
        response = requests.get(f"{api_url}/health", timeout=5)
        if response.status_code == 200:
            return {"status": "ok", "data": response.json()}
        return {"status": "error", "message": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_teams(api_url: str) -> list:
    """팀 목록 조회"""
    try:
        response = requests.get(f"{api_url}/teams", timeout=5)
        if response.status_code == 200:
            return response.json().get("teams", [])
        return []
    except Exception:
        return []


def get_weeks(api_url: str) -> list:
    """주차 목록 조회"""
    try:
        response = requests.get(f"{api_url}/weeks", timeout=5)
        if response.status_code == 200:
            return response.json().get("weeks", [])
        return []
    except Exception:
        return []


# ========== API 호출 함수 ==========
def call_chat(
    api_url: str,
    message: str,
    user_id: str,
    team: Optional[str],
    week: Optional[str],
    vector_weight: float,
    keyword_weight: float,
) -> Dict[str, Any]:
    """기본 RAG 채팅 API 호출"""
    payload = {
        "user_id": user_id,
        "message": message,
        "team": team,
        "week": week,
        "vector_weight": vector_weight,
        "keyword_weight": keyword_weight,
    }
    response = requests.post(f"{api_url}/chat", json=payload, timeout=60)
    if response.status_code == 200:
        return {"status": "ok", "data": response.json()}
    return {"status": "error", "message": response.text}


def call_chat_v2(
    api_url: str,
    message: str,
    user_id: str,
    team: Optional[str],
    week: Optional[str],
) -> Dict[str, Any]:
    """Tool Calling 채팅 API 호출"""
    payload = {
        "user_id": user_id,
        "message": message,
        "team": team,
        "week": week,
    }
    response = requests.post(f"{api_url}/chat/v2", json=payload, timeout=60)
    if response.status_code == 200:
        return {"status": "ok", "data": response.json()}
    return {"status": "error", "message": response.text}


def call_search(
    api_url: str,
    query: str,
    team: Optional[str],
    week: Optional[str],
    limit: int,
    vector_weight: float,
    keyword_weight: float,
) -> Dict[str, Any]:
    """검색 API 호출"""
    payload = {
        "query": query,
        "team": team,
        "week": week,
        "limit": limit,
        "vector_weight": vector_weight,
        "keyword_weight": keyword_weight,
    }
    response = requests.post(f"{api_url}/search", json=payload, timeout=30)
    if response.status_code == 200:
        return {"status": "ok", "data": response.json()}
    return {"status": "error", "message": response.text}


def call_stats_weekly_reports(api_url: str, week: Optional[str]) -> Dict[str, Any]:
    """주간보고 통계 조회"""
    params = {}
    if week:
        params["week"] = week
    response = requests.get(f"{api_url}/stats/weekly-reports", params=params, timeout=10)
    if response.status_code == 200:
        return {"status": "ok", "data": response.json()}
    return {"status": "error", "message": response.text}


def call_stats_other_mails(
    api_url: str, week: Optional[str], team: Optional[str]
) -> Dict[str, Any]:
    """일반 메일 통계 조회"""
    params = {}
    if week:
        params["week"] = week
    if team:
        params["team"] = team
    response = requests.get(f"{api_url}/stats/other-mails", params=params, timeout=10)
    if response.status_code == 200:
        return {"status": "ok", "data": response.json()}
    return {"status": "error", "message": response.text}


def call_stats_missing_teams(api_url: str, week: str) -> Dict[str, Any]:
    """미제출 팀 조회"""
    params = {"week": week}
    response = requests.get(f"{api_url}/stats/missing-teams", params=params, timeout=10)
    if response.status_code == 200:
        return {"status": "ok", "data": response.json()}
    return {"status": "error", "message": response.text}


def call_stats_summary(api_url: str, week: Optional[str]) -> Dict[str, Any]:
    """전체 요약 조회"""
    params = {}
    if week:
        params["week"] = week
    response = requests.get(f"{api_url}/stats/summary", params=params, timeout=10)
    if response.status_code == 200:
        return {"status": "ok", "data": response.json()}
    return {"status": "error", "message": response.text}


# ========== UI ==========
def main():
    st.title("🤖 RAG API 테스트 대시보드")
    st.markdown("---")

    # 사이드바 설정
    api_url = get_api_base_url()

    # 헬스체크
    with st.sidebar:
        st.subheader("연결 상태")
        if st.button("헬스체크", use_container_width=True):
            health = check_health(api_url)
            if health["status"] == "ok":
                st.success("✅ 연결됨")
                if "opensearch" in health["data"]:
                    st.json(health["data"]["opensearch"])
            else:
                st.error(f"❌ 연결 실패: {health['message']}")

        st.markdown("---")
        st.subheader("필터 설정")
        teams = get_teams(api_url)
        weeks = get_weeks(api_url)

        selected_team = st.selectbox("팀 선택", ["전체"] + teams, index=0)
        selected_week = st.selectbox("주차 선택", ["전체"] + weeks, index=0)

        team_filter = None if selected_team == "전체" else selected_team
        week_filter = None if selected_week == "전체" else selected_week

        st.markdown("---")
        st.subheader("검색 가중치")
        vector_weight = st.slider(
            "벡터 가중치",
            min_value=0.0,
            max_value=1.0,
            value=0.7,
            step=0.1,
            help="벡터 검색 비중",
        )
        keyword_weight = st.slider(
            "키워드 가중치",
            min_value=0.0,
            max_value=1.0,
            value=0.3,
            step=0.1,
            help="키워드 검색 비중",
        )

    # 메인 탭
    tab1, tab2, tab3, tab4 = st.tabs(["💬 채팅 (기본)", "🛠️ 채팅 (Tool Calling)", "🔍 검색", "📊 통계"])

    # 탭 1: 기본 채팅
    with tab1:
        st.subheader("기본 RAG 채팅")
        st.caption("하이브리드 검색 기반 채팅 (벡터 + 키워드)")

        # 채팅 히스토리 표시
        for i, chat in enumerate(st.session_state.chat_history):
            with st.chat_message("user"):
                st.write(chat["question"])
            with st.chat_message("assistant"):
                st.write(chat["answer"])
                if chat.get("references"):
                    with st.expander("📎 참고 출처"):
                        for ref in chat["references"]:
                            st.markdown(f"- **{ref['team']}** | {ref['week']}")
                            st.markdown(f"  {ref['url']}")
            st.markdown("---")

        # 질문 입력
        user_input = st.chat_input("질문을 입력하세요...")
        if user_input:
            with st.chat_message("user"):
                st.write(user_input)

            with st.chat_message("assistant"):
                with st.spinner("답변 생성 중..."):
                    result = call_chat(
                        api_url,
                        user_input,
                        user_id="streamlit_user",
                        team=team_filter,
                        week=week_filter,
                        vector_weight=vector_weight,
                        keyword_weight=keyword_weight,
                    )

                    if result["status"] == "ok":
                        data = result["data"]
                        answer = data.get("answer", "")
                        references = data.get("references", [])

                        st.write(answer)

                        if references:
                            with st.expander("📎 참고 출처"):
                                for ref in references:
                                    st.markdown(f"- **{ref['team']}** | {ref['week']}")
                                    st.markdown(f"  {ref['url']}")

                        # 히스토리에 추가
                        st.session_state.chat_history.append(
                            {
                                "question": user_input,
                                "answer": answer,
                                "references": references,
                            }
                        )
                    else:
                        st.error(f"오류: {result['message']}")

        # 히스토리 초기화 버튼
        if st.button("채팅 히스토리 초기화", use_container_width=True):
            st.session_state.chat_history = []
            st.rerun()

    # 탭 2: Tool Calling 채팅
    with tab2:
        st.subheader("Tool Calling 채팅 (LangGraph ReAct Agent)")
        st.caption("LLM이 자동으로 도구를 선택하여 실행합니다")

        # 채팅 히스토리 표시
        for i, chat in enumerate(st.session_state.chat_history_v2):
            with st.chat_message("user"):
                st.write(chat["question"])
            with st.chat_message("assistant"):
                st.write(chat["answer"])

                # Tool 호출 정보
                if chat.get("tool_calls"):
                    with st.expander("🔧 사용된 도구"):
                        for tc in chat["tool_calls"]:
                            st.code(f"{tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)})")

                # 참고 출처
                if chat.get("references"):
                    with st.expander("📎 참고 출처"):
                        for ref in chat["references"]:
                            st.markdown(f"- **{ref['team']}** | {ref['week']}")
                            st.markdown(f"  {ref['url']}")
            st.markdown("---")

        # 질문 입력
        user_input_v2 = st.chat_input("질문을 입력하세요 (예: 2025-48주차 주간보고 팀별 count 알려줘)...")
        if user_input_v2:
            with st.chat_message("user"):
                st.write(user_input_v2)

            with st.chat_message("assistant"):
                with st.spinner("답변 생성 중..."):
                    result = call_chat_v2(
                        api_url,
                        user_input_v2,
                        user_id="streamlit_user",
                        team=team_filter,
                        week=week_filter,
                    )

                    if result["status"] == "ok":
                        data = result["data"]
                        answer = data.get("answer", "")
                        tool_calls = data.get("tool_calls", [])
                        references = data.get("references", [])

                        st.write(answer)

                        # Tool 호출 정보
                        if tool_calls:
                            with st.expander("🔧 사용된 도구"):
                                for tc in tool_calls:
                                    st.code(
                                        f"{tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)})"
                                    )

                        # 참고 출처
                        if references:
                            with st.expander("📎 참고 출처"):
                                for ref in references:
                                    st.markdown(f"- **{ref['team']}** | {ref['week']}")
                                    st.markdown(f"  {ref['url']}")

                        # 히스토리에 추가
                        st.session_state.chat_history_v2.append(
                            {
                                "question": user_input_v2,
                                "answer": answer,
                                "tool_calls": tool_calls,
                                "references": references,
                            }
                        )
                    else:
                        st.error(f"오류: {result['message']}")

        # 히스토리 초기화 버튼
        if st.button("채팅 히스토리 초기화", key="clear_v2", use_container_width=True):
            st.session_state.chat_history_v2 = []
            st.rerun()

    # 탭 3: 검색
    with tab3:
        st.subheader("메일 내용 검색")
        st.caption("채팅 없이 검색 결과만 조회합니다")

        search_query = st.text_input("검색어 입력")
        search_limit = st.slider("결과 개수", min_value=1, max_value=20, value=5)

        if st.button("검색", use_container_width=True):
            if search_query:
                with st.spinner("검색 중..."):
                    result = call_search(
                        api_url,
                        search_query,
                        team=team_filter,
                        week=week_filter,
                        limit=search_limit,
                        vector_weight=vector_weight,
                        keyword_weight=keyword_weight,
                    )

                    if result["status"] == "ok":
                        results = result["data"]
                        st.success(f"검색 결과: {len(results)}개")

                        for i, r in enumerate(results, 1):
                            with st.expander(
                                f"결과 {i}: {r['team']} | {r['week']} | 점수: {r['score']:.4f}"
                            ):
                                st.markdown(f"**메일 ID:** {r['mail_id']}")
                                if r.get("part_index") is not None:
                                    st.markdown(
                                        f"**파트:** {r['part_index'] + 1}/{r.get('total_parts', 1)}"
                                    )
                                st.markdown("**내용:**")
                                st.text_area("", r["text"], height=150, key=f"text_{i}", disabled=True)
                                if r.get("html_path"):
                                    st.markdown(f"**경로:** {r['html_path']}")
                    else:
                        st.error(f"오류: {result['message']}")
            else:
                st.warning("검색어를 입력하세요")

    # 탭 4: 통계
    with tab4:
        st.subheader("통계 조회")

        stat_tab1, stat_tab2, stat_tab3, stat_tab4 = st.tabs(
            ["주간보고", "일반 메일", "미제출 팀", "전체 요약"]
        )

        with stat_tab1:
            st.caption("주간보고 팀별 통계")
            week_for_stats = st.selectbox("주차 선택", ["전체"] + weeks, key="stats_week1")
            if st.button("조회", key="btn_stats1"):
                result = call_stats_weekly_reports(
                    api_url, None if week_for_stats == "전체" else week_for_stats
                )
                if result["status"] == "ok":
                    data = result["data"]
                    st.json(data)
                    # 표로도 표시
                    if data.get("by_team"):
                        st.dataframe(
                            {
                                "팀": list(data["by_team"].keys()),
                                "개수": list(data["by_team"].values()),
                            },
                            use_container_width=True,
                        )
                else:
                    st.error(f"오류: {result['message']}")

        with stat_tab2:
            st.caption("일반 메일 팀별 통계")
            week_for_stats2 = st.selectbox("주차 선택", ["전체"] + weeks, key="stats_week2")
            team_for_stats = st.selectbox("팀 선택", ["전체"] + teams, key="stats_team2")
            if st.button("조회", key="btn_stats2"):
                result = call_stats_other_mails(
                    api_url,
                    None if week_for_stats2 == "전체" else week_for_stats2,
                    None if team_for_stats == "전체" else team_for_stats,
                )
                if result["status"] == "ok":
                    data = result["data"]
                    st.json(data)
                    if data.get("by_team"):
                        st.dataframe(
                            {
                                "팀": list(data["by_team"].keys()),
                                "개수": list(data["by_team"].values()),
                            },
                            use_container_width=True,
                        )
                else:
                    st.error(f"오류: {result['message']}")

        with stat_tab3:
            st.caption("주간보고 미제출 팀 조회")
            week_for_missing = st.selectbox("주차 선택", weeks, key="stats_week3")
            if st.button("조회", key="btn_stats3"):
                result = call_stats_missing_teams(api_url, week_for_missing)
                if result["status"] == "ok":
                    data = result["data"]
                    st.json(data)
                    if data.get("missing_teams"):
                        st.write(f"**미제출 팀 ({data['count']}개):**")
                        for team in data["missing_teams"]:
                            st.write(f"- {team}")
                    else:
                        st.success("모든 팀이 제출했습니다!")
                else:
                    st.error(f"오류: {result['message']}")

        with stat_tab4:
            st.caption("전체 메일 현황 요약")
            week_for_summary = st.selectbox("주차 선택", ["전체"] + weeks, key="stats_week4")
            if st.button("조회", key="btn_stats4"):
                result = call_stats_summary(
                    api_url, None if week_for_summary == "전체" else week_for_summary
                )
                if result["status"] == "ok":
                    data = result["data"]
                    st.json(data)
                else:
                    st.error(f"오류: {result['message']}")


if __name__ == "__main__":
    main()




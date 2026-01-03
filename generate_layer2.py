"""
Layer2: 팀별 스토리 요약 생성
- 최근 3주 히스토리 기반
- LLM을 이용한 스토리형 요약
- ChromaDB 또는 파일 기반 히스토리 조회
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from dotenv import load_dotenv
import os
from openai import OpenAI

load_dotenv()

# ========== 설정 ==========
DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")
CHROMA_DIR = Path("chroma_db")

# LLM 설정
API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = os.getenv("OPENROUTER_BASE_URL")
MODEL = "gpt-oss-120b"

# 실행 설정
CONFIG = {
    "week": "2025-48",  # 대상 주차
    "teams": None,  # None이면 전체 팀, ["FA팀"] 처럼 지정 가능
    "weeks_back": 3,  # 히스토리 주 수
    "output_format": "both",  # "html", "md", "both"
    "db_source": "chromadb",  # "file" 또는 "chromadb"
}

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


def get_previous_weeks(current_week: str, count: int = 3) -> List[str]:
    """현재 주차 기준 이전 N주 목록 반환 (현재 주 포함)

    Args:
        current_week: "2025-48" 형식
        count: 가져올 주 수 (기본 3주)

    Returns:
        ["2025-46", "2025-47", "2025-48"] (오래된 순)
    """
    year, week = map(int, current_week.split("-"))
    weeks = []

    for i in range(count - 1, -1, -1):
        w = week - i
        y = year

        # 연도 넘어가는 경우 처리
        while w < 1:
            y -= 1
            w += 52  # 간단히 52주로 계산

        weeks.append(f"{y}-{w:02d}")

    return weeks


def load_team_history(team: str, current_week: str, weeks_back: int = 3) -> Dict:
    """팀의 최근 N주 원본 텍스트 및 청크 데이터 로드

    Returns:
        {
            "combined_texts": {
                "2025-46": "원본 텍스트...",
                "2025-47": "원본 텍스트...",
                "2025-48": "원본 텍스트...",
            },
            "chunks": {
                "2025-46": [청크들...],
                ...
            }
        }
    """
    weeks = get_previous_weeks(current_week, weeks_back)
    combined_texts = {}
    chunks = {}

    for week in weeks:
        week_dir = DATA_DIR / week / team
        week_chunks = []
        week_combined = ""

        if week_dir.exists():
            for mail_dir in week_dir.iterdir():
                if mail_dir.is_dir():
                    # combined.txt 로드 (원본 텍스트)
                    combined_file = mail_dir / "combined.txt"
                    if combined_file.exists():
                        with open(combined_file, "r", encoding="utf-8") as f:
                            week_combined += f.read() + "\n\n"

                    # chunks.json 로드 (이번 주 업무 목록용)
                    chunks_file = mail_dir / "chunks.json"
                    if chunks_file.exists():
                        with open(chunks_file, "r", encoding="utf-8") as f:
                            mail_chunks = json.load(f)
                            for chunk in mail_chunks:
                                chunk["week"] = week
                            week_chunks.extend(mail_chunks)

        combined_texts[week] = week_combined.strip()
        chunks[week] = week_chunks

    return {
        "combined_texts": combined_texts,
        "chunks": chunks,
    }


def load_team_history_from_chromadb(
    team: str, current_week: str, weeks_back: int = 3
) -> Dict:
    """ChromaDB에서 팀의 최근 N주 히스토리 로드

    Returns:
        {
            "combined_texts": {"2025-46": "원본 텍스트...", ...},
            "chunks": {"2025-46": [청크들...], ...}
        }
    """
    from embed_chromadb import ChromaDBClient

    client = ChromaDBClient(persist_dir=str(CHROMA_DIR))
    weeks = get_previous_weeks(current_week, weeks_back)

    combined_texts = {}
    chunks = {}

    for week in weeks:
        # original_mail 조회
        original_docs = client.search_by_filter(
            team=team, week=week, doc_type="original_mail"
        )
        if original_docs:
            combined_texts[week] = "\n\n".join([doc["text"] for doc in original_docs])
        else:
            combined_texts[week] = ""

        # chunk 조회
        chunk_docs = client.search_by_filter(team=team, week=week, doc_type="chunk")
        week_chunks = []
        for doc in chunk_docs:
            week_chunks.append(
                {
                    "text": doc["text"],
                    "domain": doc["metadata"].get("domain", ""),
                    "tech": doc["metadata"].get("tech", ""),
                    "product": doc["metadata"].get("product", ""),
                    "week": week,
                }
            )
        chunks[week] = week_chunks

    return {
        "combined_texts": combined_texts,
        "chunks": chunks,
    }


def generate_team_summary(team: str, history: Dict, current_week: str) -> str:
    """팀별 스토리 요약 생성 (LLM 사용)

    Args:
        team: 팀명
        history: {"combined_texts": {...}, "chunks": {...}}
        current_week: 현재 주차 (요약 대상)
    """
    combined_texts = history.get("combined_texts", {})
    weeks = sorted(combined_texts.keys())

    # 원본 텍스트 기반 히스토리 생성 (현재 주차 표시)
    history_text = ""
    for week in weeks:
        text = combined_texts[week]
        if text:
            if week == current_week:
                history_text += (
                    f"\n{'='*50}\n### ⭐ {week} 주차 (현재 - 요약 대상)\n{'='*50}\n"
                )
            else:
                history_text += f"\n{'='*50}\n### {week} 주차 (참조용)\n{'='*50}\n"
            history_text += text + "\n"

    if not history_text.strip():
        return f"[{team}] Weekly Summary\n\n데이터 없음\n"

    # LLM으로 스토리 요약 생성
    return _generate_llm_summary(team, history_text, current_week)


def _generate_llm_summary(team: str, history_text: str, current_week: str) -> str:
    """LLM으로 스토리 요약 생성"""

    system_prompt = """당신은 반도체 공정 팀의 주간 보고서 요약 전문가입니다.

## 입력 데이터 구조:
- **현재 주차 (⭐ 표시)**: 요약 대상 - 이번 주 업무 내용
- **이전 주차들 (참조용)**: 맥락 파악용 - 현재 주차 업무의 배경 이해용

## 핵심 원칙:
1. 요약은 **현재 주차** 업무 기준으로 작성
2. 이전 주차 데이터는 **맥락 파악용**으로만 활용
3. "지난주에 시작한 A가 이번 주 완료됨" 같은 스토리 연결 반영

## 출력 형식 (반드시 준수):
[팀명] Weekly Summary

1. 주요 보고
- 이전 주 맥락을 반영한 이번 주 핵심 스토리 (1-2문장)

2. Key Actions (이번 주 기준)
1) Domain-Tech: 구체적 액션
2) Domain-Tech: 구체적 액션
(최대 5개)

3. Issue / Risk
- Domain-Tech: 이슈 내용 및 영향
(해당 사항 없으면 "특이사항 없음")

4. 향후 계획
- Domain-Tech: 예정 업무
(1-3개)

## 요약 원칙:
- 이전 주에서 시작해 현재 주에 진행/완료된 업무는 진척 상황 반영
- 2주 이상 지속되는 이슈는 Risk로 강조
- 수치가 있으면 포함
- 간결하고 명확하게 작성"""

    user_prompt = f"""[{team}] 주간 요약을 생성해주세요.

**⭐ 현재 주차 (요약 대상)**: {current_week}

---
{history_text}
---

위 데이터에서 {current_week} 주차가 핵심 요약 대상이고,
이전 주차들은 맥락 파악용입니다.
"""

    client = OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
    )
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=1000,
    )
    return response.choices[0].message.content


def get_mail_html_links(team: str, week: str) -> List[Dict[str, str]]:
    """팀의 해당 주차 원본 메일 HTML 링크 목록 반환"""
    team_dir = DATA_DIR / week / team
    links = []

    if team_dir.exists():
        for mail_dir in sorted(team_dir.iterdir()):
            if mail_dir.is_dir() and mail_dir.name.startswith("mail_"):
                html_file = mail_dir / "body.html"
                meta_file = mail_dir / "meta.json"

                if html_file.exists():
                    # 메타데이터에서 제목 가져오기
                    subject = mail_dir.name
                    if meta_file.exists():
                        try:
                            with open(meta_file, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                                subject = meta.get("subject", mail_dir.name)[:50]
                        except Exception:
                            pass

                    # 상대 경로 (output/ 기준)
                    relative_path = f"../data/{week}/{team}/{mail_dir.name}/body.html"
                    links.append({"path": relative_path, "subject": subject})

    return links


def generate_html(summaries: Dict[str, str], week: str, timestamp: str) -> str:
    """HTML 출력 생성"""
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Layer2 팀별 요약 - {week}</title>
</head>
<body style="font-family: 'Malgun Gothic', sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
            <td style="padding: 20px; background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                <h1 style="margin: 0 0 10px 0; color: #333; font-size: 24px;">Layer2 팀별 요약 리포트</h1>
                <p style="margin: 0; color: #666; font-size: 14px;">대상 주차: {week} | 생성 시각: {timestamp}</p>
            </td>
        </tr>
        <tr><td style="height: 20px;"></td></tr>
"""

    for team, summary in summaries.items():
        # 원본 메일 링크 생성
        mail_links = get_mail_html_links(team, week)
        links_html = ""
        if mail_links:
            links_html = '<div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid #eee;">'
            links_html += (
                '<span style="font-size: 11px; color: #888;">📎 원본 메일: </span>'
            )
            for i, link in enumerate(mail_links):
                if i > 0:
                    links_html += '<span style="color: #ccc;"> | </span>'
                links_html += f'<a href="{link["path"]}" style="font-size: 11px; color: #4a90d9; text-decoration: none;" target="_blank">{link["subject"]}</a>'
            links_html += "</div>"

        html += f"""        <tr>
            <td style="padding: 20px; background: white; border-radius: 8px; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                <pre style="font-family: 'Malgun Gothic', sans-serif; white-space: pre-wrap; margin: 0; font-size: 13px; line-height: 1.6; color: #333;">{summary}</pre>
                {links_html}
            </td>
        </tr>
        <tr><td style="height: 15px;"></td></tr>
"""

    html += """    </table>
</body>
</html>
"""
    return html


def generate_markdown(summaries: Dict[str, str], week: str, timestamp: str) -> str:
    """Markdown 출력 생성"""
    md = f"""# Layer2 팀별 요약 리포트

**대상 주차**: {week}
**생성 시각**: {timestamp}

---

"""
    for team, summary in summaries.items():
        md += f"{summary}\n\n---\n\n"

    return md


def generate_layer2(
    week: str,
    teams: Optional[List[str]] = None,
    weeks_back: int = 3,
    output_format: str = "both",
    db_source: str = "file",
) -> Dict:
    """Layer2 팀별 요약 생성

    Args:
        week: 대상 주차 (예: "2025-48")
        teams: 대상 팀 목록 (None이면 전체 팀)
        weeks_back: 히스토리 주 수 (기본 3주)
        output_format: "html", "md", "both"
        db_source: 데이터 소스 ("file" 또는 "chromadb")

    Returns:
        {
            "summaries": {"FA팀": "요약...", ...},
            "html_path": "...",
            "md_path": "...",
        }
    """
    print("=" * 50)
    print("Layer2 팀별 요약 리포트 생성")
    print("=" * 50)

    if teams is None:
        teams = TEAMS

    print(f"📅 대상 주차: {week}")
    print(f"📊 히스토리: 최근 {weeks_back}주")
    print(f"👥 대상 팀: {len(teams)}개")
    print(f"💾 데이터 소스: {db_source}")
    print()

    summaries = {}

    for team in teams:
        print(f"📝 {team} 요약 생성 중...")

        # 히스토리 로드 (데이터 소스에 따라 분기)
        if db_source == "chromadb":
            history = load_team_history_from_chromadb(team, week, weeks_back)
        else:
            history = load_team_history(team, week, weeks_back)

        total_chunks = sum(len(chunks) for chunks in history["chunks"].values())
        total_combined = sum(1 for text in history["combined_texts"].values() if text)
        print(f"   - 원본 텍스트: {total_combined}개, 청크: {total_chunks}개")

        # 요약 생성 (현재 주차 전달)
        summary = generate_team_summary(team, history, week)
        summaries[team] = summary
        print(f"   ✅ 완료")

    print()

    # 출력 파일 생성
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamp_display = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    result = {"summaries": summaries}

    if output_format in ("md", "both"):
        md_content = generate_markdown(summaries, week, timestamp_display)
        md_path = OUTPUT_DIR / f"layer2_{week}_{timestamp}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"📄 Markdown 저장: {md_path}")
        result["md_path"] = str(md_path)

    if output_format in ("html", "both"):
        html_content = generate_html(summaries, week, timestamp_display)
        html_path = OUTPUT_DIR / f"layer2_{week}_{timestamp}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"📄 HTML 저장: {html_path}")
        result["html_path"] = str(html_path)

    print()
    print("=" * 50)
    print("✅ Layer2 생성 완료")
    print("=" * 50)

    return result


# ========== 실행 ==========
if __name__ == "__main__":
    generate_layer2(
        week=CONFIG["week"],
        teams=CONFIG["teams"],
        weeks_back=CONFIG["weeks_back"],
        output_format=CONFIG["output_format"],
        db_source=CONFIG["db_source"],
    )

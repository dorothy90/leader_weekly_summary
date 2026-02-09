"""
Layer3: Executive Dashboard 생성
- 팀별 Executive Summary 추출 및 100자 요약
- 도메인별 그룹핑 (COMMON, DRAM, NAND)
"""

import json
import re
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

# LLM 설정
API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = os.getenv("OPENROUTER_BASE_URL")
MODEL = "gpt-oss-120b"

# 실행 설정
CONFIG = {
    "week": "2025-48",  # 대상 주차
    "teams": None,  # None이면 전체 팀, ["FA팀"] 처럼 지정 가능
    "output_format": "both",  # "html", "md", "both"
    "db_source": "opensearch",  # "file" 또는 "opensearch"
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

# 그룹 순서 및 색상
DOMAIN_ORDER = ["DRAM - PTE", "DRAM - SRT", "NAND - PTE", "NAND - SRT", "직속"]
DOMAIN_COLORS = {
    "DRAM - PTE": "#81c784",  # 연한 초록
    "DRAM - SRT": "#a5d6a7",  # 밝은 초록
    "NAND - PTE": "#ffb74d",  # 연한 주황
    "NAND - SRT": "#ffcc80",  # 밝은 주황
    "직속": "#6fa8dc",  # 연한 파랑
}

# 팀 → 그룹 매핑 (실제 조직 구조에 맞게 수정 필요)
TEAM_GROUP_MAP = {
    "FA팀": "DRAM - PTE",
    "PE팀": "DRAM - PTE",
    "YIELD팀": "DRAM - SRT",
    "PROCESS팀": "DRAM - SRT",
    "TEST팀": "NAND - PTE",
    "EQUIP팀": "NAND - PTE",
    "PI팀": "NAND - SRT",
    "QA팀": "NAND - SRT",
    "CS팀": "직속",
    "DT팀": "직속",
}

# Executive Summary 추출 키워드
EXEC_SUMMARY_KEYWORDS = [
    "Executive Summary",
    "executive summary",
    "EXECUTIVE SUMMARY",
    "Summary",
    "summary",
    "SUMMARY",
    "요약",
    "핵심",
    "핵심 내용",
    "주요 사항",
    "주요사항",
    "Overview",
    "overview",
]


# ========== 데이터 로드 ==========
def load_team_mail(team: str, week: str) -> str:
    """팀의 해당 주차 원본 메일 텍스트 로드 (파일 기반)

    Returns:
        combined.txt 내용 또는 빈 문자열
    """
    week_dir = DATA_DIR / week / team
    combined_text = ""

    if week_dir.exists():
        for mail_dir in week_dir.iterdir():
            if mail_dir.is_dir():
                combined_file = mail_dir / "combined.txt"
                if combined_file.exists():
                    with open(combined_file, "r", encoding="utf-8") as f:
                        combined_text += f.read() + "\n\n"

    return combined_text.strip()


def load_team_mail_from_opensearch(team: str, week: str) -> str:
    """OpenSearch에서 팀의 해당 주차 원본 메일 로드

    Returns:
        원본 메일 텍스트 또는 빈 문자열
    """
    from opensearch import get_by_team_and_week

    docs = get_by_team_and_week(team, week)

    if docs:
        # 텍스트를 결합하여 반환
        texts = [doc.get("text", "") for doc in docs if doc.get("text")]
        return "\n\n".join(texts)
    return ""


# ========== Executive Summary 추출 ==========
def extract_executive_summary(text: str) -> str:
    """메일 텍스트에서 Executive Summary 섹션 추출

    Args:
        text: 원본 메일 텍스트

    Returns:
        Executive Summary 내용 (없으면 메일 상단 첫 문단)
    """
    if not text.strip():
        return ""

    lines = text.split("\n")

    # 1. 키워드로 시작하는 섹션 찾기
    for i, line in enumerate(lines):
        line_stripped = line.strip()

        # 키워드 매칭
        for keyword in EXEC_SUMMARY_KEYWORDS:
            if line_stripped.startswith(keyword) or keyword in line_stripped:
                # 해당 라인부터 다음 섹션 전까지 추출
                summary_lines = []
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()

                    # 다음 섹션 시작 감지 (번호로 시작하거나 새 헤더)
                    if re.match(r"^\d+\.", next_line) or re.match(
                        r"^[#\*]+\s", next_line
                    ):
                        break
                    # 빈 줄이 2개 연속이면 섹션 종료
                    if (
                        not next_line
                        and j + 1 < len(lines)
                        and not lines[j + 1].strip()
                    ):
                        break

                    if next_line:
                        summary_lines.append(next_line)

                if summary_lines:
                    return " ".join(summary_lines)

    # 2. 키워드 없으면 메일 상단 첫 문단 반환 (인사말 제외)
    content_started = False
    first_paragraph = []

    for line in lines:
        line_stripped = line.strip()

        # 제목/인사말 건너뛰기
        if not content_started:
            if line_stripped.startswith("[") or "안녕하세요" in line_stripped:
                continue
            if line_stripped.startswith("금일") or line_stripped.startswith("보고"):
                continue
            if re.match(r"^\d+\.", line_stripped):
                content_started = True

        if content_started:
            if not line_stripped:
                if first_paragraph:
                    break
                continue
            first_paragraph.append(line_stripped)
            if len(first_paragraph) >= 5:  # 최대 5줄
                break

    return " ".join(first_paragraph) if first_paragraph else text[:500]


# ========== LLM 요약 생성 ==========
def generate_one_line_summary(team: str, exec_summary: str, current_week: str) -> str:
    """LLM으로 100자 내외 한 문장 요약 생성

    Args:
        team: 팀명
        exec_summary: Executive Summary 텍스트
        current_week: 현재 주차

    Returns:
        100자 내외 요약 문장
    """
    if not exec_summary.strip():
        return "데이터 없음"

    system_prompt = """당신은 반도체 공정 팀의 주간보고 'Executive Summary'를 짧고 자연스러운 한국어 문장으로 재작성하는 요약 전문가입니다.

## 주차 형식 (중요!):
- 형식: YYYY-WW (예: 2026-02 = 2026년 **2주차**, 월(Month)이 아님!)

## 사실성 원칙 (반드시 준수):
1. 원문에 없는 사실/원인/평가/추정을 추가하지 말 것 (추론/판단 금지)
2. 원문에 수치/지표/기간/조건이 있으면 가능한 한 포함할 것
3. "~한 것으로 보임", "~할 예정으로 판단됨" 같은 추측 표현 금지

## 문장 품질 원칙 (중요!):
1. 원문 표현을 그대로 '붙여쓰기/나열'하지 말고, 같은 의미로 자연스럽게 문장으로 재작성(패러프레이즈)할 것
2. 반드시 올바른 띄어쓰기를 포함할 것 (공백을 제거해서 글자 수를 맞추지 말 것)
3. 명사구/약어/키워드만 나열 금지. 반드시 서술어(했다/진행 중이다/완료했다 등) 포함
4. 가능한 한 한 문장으로. 너무 길어지면 2문장까지 허용하되 출력은 한 줄(줄바꿈 금지)

## 길이:
- 90~120자(공백 포함) 권장. 핵심이 잘리면 80~140자까지 허용.

## 하이라이트 태그 (해당 시에만 문장 앞에 추가):
- [완료]: 주요 업무가 완료되었다고 명시된 경우
- 태그는 최대 1개만 사용, 해당 없으면 태그 없이 작성

## 출력 형식:
- 태그 없는 경우: 설비 점검을 완료했고 수율 분석 리포트 작성이 진행 중이다.
- 태그 있는 경우: [완료] 1a DRAM 공정 안정화를 달성했고 Yield 92.3%를 기록했다.
"""

    user_prompt = f"""[{team}] {current_week} 주간보고 Executive Summary를 위 원칙대로 요약하세요.

---
{exec_summary}
---

체크리스트:
- 띄어쓰기 유지(공백 제거로 글자 수 맞추기 금지)
- 키워드 나열 금지(반드시 서술어 포함)
- 원문에 없는 내용 추가 금지, 수치가 있으면 포함
- 최종 출력은 한 줄 텍스트만(줄바꿈/불릿/따옴표 금지)
"""

    try:
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
            temperature=0.1,
            max_tokens=200,
        )
        summary = response.choices[0].message.content.strip()

        # 따옴표 제거
        summary = summary.strip("\"'")

        return summary
    except Exception as e:
        print(f"   ⚠️ LLM 오류: {e}")
        return exec_summary[:100] + "..." if len(exec_summary) > 100 else exec_summary


# ========== 도메인별 그룹핑 ==========
def get_team_primary_domain(team: str, week: str) -> str:
    """팀의 그룹 반환 (매핑 기반)

    Returns:
        그룹명 ("DRAM - PTE", "DRAM - SRT", "NAND - PTE", "NAND - SRT", "직속")
    """
    return TEAM_GROUP_MAP.get(team, "직속")


def group_teams_by_domain(
    teams_data: Dict[str, Dict], week: str
) -> Dict[str, List[Dict]]:
    """팀 데이터를 도메인별로 그룹핑

    Args:
        teams_data: {팀명: {"summary": ..., "exec_summary": ...}}
        week: 대상 주차

    Returns:
        {도메인: [{"team": ..., "summary": ...}, ...]}
    """
    grouped = {domain: [] for domain in DOMAIN_ORDER}

    for team, data in teams_data.items():
        domain = get_team_primary_domain(team, week)

        # 유효한 도메인인지 확인
        if domain not in DOMAIN_ORDER:
            domain = "직속"

        grouped[domain].append(
            {
                "team": team,
                "summary": data.get("summary", ""),
                "exec_summary": data.get("exec_summary", ""),
            }
        )

    # 각 도메인 내에서 팀명으로 정렬
    for domain in grouped:
        grouped[domain].sort(key=lambda x: x["team"])

    return grouped


# ========== HTML 출력 ==========
def generate_html(
    grouped_data: Dict[str, List[Dict]], week: str, timestamp: str
) -> str:
    """HTML 출력 생성 (Outlook 호환 - 도메인별 테이블)"""

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Layer3 Executive Dashboard - {week}</title>
    <style>
        body {{ margin: 0; padding: 0; }}
        table {{ border-collapse: collapse; }}
        body, table, td, th, p, span, div {{
            font-family: 'Malgun Gothic', '맑은 고딕', 'Apple SD Gothic Neo', 'Segoe UI', Arial, sans-serif !important;
        }}
    </style>
</head>
<body style="margin: 0; padding: 0; background-color: #f5f5f5; font-family: 'Malgun Gothic','맑은 고딕','Apple SD Gothic Neo','Segoe UI',Arial,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f5f5f5;">
        <tr>
            <td align="center" style="padding: 20px 10px;">
                <table width="800" cellpadding="0" cellspacing="0" border="0" style="max-width: 800px;">
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #1a202c; padding: 24px 28px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td style="font-size: 22px; font-weight: bold; color: #ffffff;">
                                        Layer3 Executive Dashboard
                                    </td>
                                </tr>
                                <tr>
                                    <td style="font-size: 13px; color: #a0aec0; padding-top: 6px;">
                                        대상 주차: {week} | 생성: {timestamp}
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr><td style="height: 16px; background-color: #f5f5f5;"></td></tr>
"""

    for domain in DOMAIN_ORDER:
        teams = grouped_data.get(domain, [])
        if not teams:
            continue

        domain_color = DOMAIN_COLORS.get(domain, "#666")

        html += f"""
                    <!-- {domain} Section -->
                    <tr>
                        <td style="background-color: #ffffff;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <!-- Domain Header -->
                                <tr>
                                    <td style="background-color: {domain_color}; padding: 12px 20px; font-size: 15px; font-weight: bold; color: #ffffff;">
                                        {domain}
                                    </td>
                                </tr>
                                <!-- Table Header -->
                                <tr>
                                    <td style="padding: 0;">
                                        <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                            <tr style="background-color: #f7fafc;">
                                                <td style="padding: 10px 16px; font-size: 11px; font-weight: bold; color: #4a5568; width: 80px; border-bottom: 2px solid #e2e8f0;">팀</td>
                                                <td style="padding: 10px 16px; font-size: 11px; font-weight: bold; color: #4a5568; border-bottom: 2px solid #e2e8f0;">Executive Summary</td>
                                            </tr>
"""

        for team_data in teams:
            team = team_data["team"]
            summary = team_data["summary"]

            # 하이라이트 태그 스타일링
            summary_html = summary
            if summary.startswith("[완료]"):
                summary_html = f'<span style="background-color: #bee3f8; color: #2a4365; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: bold;">완료</span> {summary[4:].strip()}'

            html += f"""
                                            <tr>
                                                <td style="padding: 12px 16px; font-size: 13px; color: #2d3748; font-weight: bold; border-bottom: 1px solid #e2e8f0; vertical-align: top;">{team}</td>
                                                <td style="padding: 12px 16px; font-size: 13px; color: #2d3748; line-height: 1.5; border-bottom: 1px solid #e2e8f0;">{summary_html}</td>
                                            </tr>
"""

        html += """
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr><td style="height: 12px; background-color: #f5f5f5;"></td></tr>
"""

    html += """
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
    return html


# ========== Markdown 출력 ==========
def generate_markdown(
    grouped_data: Dict[str, List[Dict]], week: str, timestamp: str
) -> str:
    """Markdown 출력 생성"""

    md = f"""# Layer3 Executive Dashboard

**대상 주차**: {week}
**생성 시각**: {timestamp}

---

"""

    for domain in DOMAIN_ORDER:
        teams = grouped_data.get(domain, [])
        if not teams:
            continue

        md += f"## {domain}\n\n"
        md += "| 팀 | Executive Summary |\n"
        md += "|-----|-------------------|\n"

        for team_data in teams:
            team = team_data["team"]
            summary = team_data["summary"].replace("|", "\\|")

            md += f"| {team} | {summary} |\n"

        md += "\n---\n\n"

    return md


# ========== 메인 함수 ==========
def generate_layer3(
    week: str,
    teams: Optional[List[str]] = None,
    output_format: str = "both",
    db_source: str = "file",
) -> Dict:
    """Layer3 Executive Dashboard 생성

    Args:
        week: 대상 주차 (예: "2025-48")
        teams: 대상 팀 목록 (None이면 전체 팀)
        output_format: "html", "md", "both"
        db_source: 데이터 소스 ("file" 또는 "opensearch")

    Returns:
        {
            "teams_data": {...},
            "html_path": "...",
            "md_path": "...",
        }
    """
    print("=" * 50)
    print("Layer3 Executive Dashboard 생성")
    print("=" * 50)

    if teams is None:
        teams = TEAMS

    print(f"📅 대상 주차: {week}")
    print(f"👥 대상 팀: {len(teams)}개")
    print(f"💾 데이터 소스: {db_source}")
    print()

    teams_data = {}

    for team in teams:
        print(f"📝 {team} 처리 중...")

        # 1. 현재 주 메일 로드
        if db_source == "opensearch":
            current_mail = load_team_mail_from_opensearch(team, week)
        else:
            current_mail = load_team_mail(team, week)

        # 2. Executive Summary 추출
        exec_summary = extract_executive_summary(current_mail)
        print(f"   - Executive Summary: {len(exec_summary)}자")

        # 3. LLM 100자 요약 생성
        summary = generate_one_line_summary(team, exec_summary, week)
        print(f"   - 요약: {summary[:50]}...")

        teams_data[team] = {
            "summary": summary,
            "exec_summary": exec_summary,
        }

        print(f"   ✅ 완료")

    print()

    # 4. 도메인별 그룹핑
    grouped_data = group_teams_by_domain(teams_data, week)

    # 5. 출력 파일 생성
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamp_display = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    result = {"teams_data": teams_data, "grouped_data": grouped_data}

    if output_format in ("md", "both"):
        md_content = generate_markdown(grouped_data, week, timestamp_display)
        md_path = OUTPUT_DIR / f"layer3_{week}_{timestamp}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"📄 Markdown 저장: {md_path}")
        result["md_path"] = str(md_path)

    if output_format in ("html", "both"):
        html_content = generate_html(grouped_data, week, timestamp_display)
        html_path = OUTPUT_DIR / f"layer3_{week}_{timestamp}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"📄 HTML 저장: {html_path}")
        result["html_path"] = str(html_path)

    print()
    print("=" * 50)
    print("✅ Layer3 생성 완료")
    print("=" * 50)

    # 도메인별 통계
    for domain in DOMAIN_ORDER:
        count = len(grouped_data.get(domain, []))
        print(f"   {domain}: {count}개 팀")

    return result


# ========== 실행 ==========
if __name__ == "__main__":
    generate_layer3(
        week=CONFIG["week"],
        teams=CONFIG["teams"],
        output_format=CONFIG["output_format"],
        db_source=CONFIG["db_source"],
    )

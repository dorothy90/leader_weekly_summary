"""
Layer3 v2: Executive Dashboard 생성
- 팀별 Executive Summary 추출 및 100자 요약
- LLM 구조화 인사이트(JSON) 추출
- 도메인별 그룹핑 및 HTML/Markdown 리포트 출력
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
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
    "DRAM - PTE": "#81c784",
    "DRAM - SRT": "#a5d6a7",
    "NAND - PTE": "#ffb74d",
    "NAND - SRT": "#ffcc80",
    "직속": "#6fa8dc",
}

# 팀 -> 그룹 매핑
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

ALLOWED_STATUS = {"on_track", "risk", "blocked", "done", "unknown"}
TAG_STYLE = {
    "[완료]": (
        "background-color: #bee3f8; color: #2a4365;",
        "완료",
    ),
    "[리스크]": (
        "background-color: #fed7d7; color: #742a2a;",
        "리스크",
    ),
    "[의사결정요청]": (
        "background-color: #faf089; color: #744210;",
        "의사결정요청",
    ),
}


def _create_client() -> OpenAI:
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


def _clip(value: str, max_len: int = 7000) -> str:
    if len(value) <= max_len:
        return value
    return value[:max_len] + "\n...(truncated)"


def _safe_list(value: object, fallback: Optional[List[str]] = None) -> List[str]:
    if fallback is None:
        fallback = []
    if not isinstance(value, list):
        return fallback
    result: List[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                result.append(cleaned)
    return result


def _safe_kpi(value: object) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    results: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        results.append(
            {
                "name": name,
                "value": str(item.get("value", "")).strip(),
                "delta": str(item.get("delta", "")).strip(),
            }
        )
    return results


def _default_insight() -> Dict:
    return {
        "status": "unknown",
        "achievement": [],
        "risk": [],
        "next_week_plan": [],
        "dependency": [],
        "action_required": [],
        "kpi": [],
    }


def normalize_insight(data: Dict) -> Dict:
    normalized = _default_insight()
    if not isinstance(data, dict):
        return normalized

    status = str(data.get("status", "unknown")).strip().lower()
    normalized["status"] = status if status in ALLOWED_STATUS else "unknown"
    normalized["achievement"] = _safe_list(data.get("achievement"))
    normalized["risk"] = _safe_list(data.get("risk"))
    normalized["next_week_plan"] = _safe_list(data.get("next_week_plan"))
    normalized["dependency"] = _safe_list(data.get("dependency"))
    normalized["action_required"] = _safe_list(data.get("action_required"))
    normalized["kpi"] = _safe_kpi(data.get("kpi"))
    return normalized


def _extract_json_object(raw_text: str) -> Dict:
    text = raw_text.strip()
    if not text:
        return _default_insight()

    # 코드블록 대응
    codeblock_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if codeblock_match:
        text = codeblock_match.group(1).strip()

    try:
        return normalize_insight(json.loads(text))
    except json.JSONDecodeError:
        pass

    # 가장 바깥 JSON 객체 추출 시도
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
        candidate = text[start_idx : end_idx + 1]
        try:
            return normalize_insight(json.loads(candidate))
        except json.JSONDecodeError:
            return _default_insight()
    return _default_insight()


# ========== 데이터 로드 ==========
def load_team_mail(team: str, week: str) -> str:
    week_dir = DATA_DIR / week / team
    combined_text = ""
    if week_dir.exists():
        for mail_dir in week_dir.iterdir():
            if mail_dir.is_dir():
                combined_file = mail_dir / "combined.txt"
                if combined_file.exists():
                    with open(combined_file, "r", encoding="utf-8") as file_obj:
                        combined_text += file_obj.read() + "\n\n"
    return combined_text.strip()


def load_team_mail_from_opensearch(team: str, week: str) -> str:
    from opensearch import get_by_team_and_week

    docs = get_by_team_and_week(team, week)
    if docs:
        texts = [doc.get("text", "") for doc in docs if doc.get("text")]
        return "\n\n".join(texts)
    return ""


# ========== Executive Summary 추출 ==========
def extract_executive_summary(text: str) -> str:
    if not text.strip():
        return ""

    lines = text.split("\n")

    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        for keyword in EXEC_SUMMARY_KEYWORDS:
            if line_stripped.startswith(keyword) or keyword in line_stripped:
                summary_lines = []
                for jdx in range(idx + 1, len(lines)):
                    next_line = lines[jdx].strip()
                    if re.match(r"^\d+\.", next_line) or re.match(r"^[#\*]+\s", next_line):
                        break
                    if (
                        not next_line
                        and jdx + 1 < len(lines)
                        and not lines[jdx + 1].strip()
                    ):
                        break
                    if next_line:
                        summary_lines.append(next_line)
                if summary_lines:
                    return " ".join(summary_lines)

    content_started = False
    first_paragraph = []
    for line in lines:
        line_stripped = line.strip()
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
            if len(first_paragraph) >= 5:
                break
    return " ".join(first_paragraph) if first_paragraph else text[:500]


# ========== LLM 요약 ==========
def generate_one_line_summary(team: str, exec_summary: str, current_week: str) -> str:
    if not exec_summary.strip():
        return "데이터 없음"

    system_prompt = """당신은 반도체 공정 팀의 주간보고 'Executive Summary'를 짧고 자연스러운 한국어 문장으로 재작성하는 요약 전문가입니다.

## 사실성 원칙:
1. 원문에 없는 사실/원인/평가/추정 추가 금지
2. 수치/지표/기간/조건이 있으면 가능한 한 포함
3. 추측성 표현 금지

## 문장 품질:
1. 키워드 나열 금지, 반드시 서술어 포함
2. 자연스러운 한국어 문장으로 재작성
3. 출력은 한 줄 텍스트

## 길이:
- 90~120자 권장 (80~140자 허용)

## 하이라이트 태그:
- [완료]: 주요 업무 완료 명시
- [리스크]: 일정/품질/설비 등 리스크 명시
- [의사결정요청]: 승인/우선순위 결정 요청이 있는 경우
- 태그는 최대 1개만 사용, 해당 없으면 태그 없이 작성
"""

    user_prompt = f"""[{team}] {current_week} 주간보고 Executive Summary를 요약하세요.

---
{_clip(exec_summary, max_len=5000)}
---

최종 출력은 한 줄 텍스트만 반환하세요.
"""

    try:
        response = _create_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=220,
        )
        summary = response.choices[0].message.content.strip().strip("\"'")
        return summary
    except Exception as exc:
        print(f"   ⚠️ 요약 LLM 오류: {exc}")
        return exec_summary[:100] + "..." if len(exec_summary) > 100 else exec_summary


# ========== LLM 구조화 인사이트 ==========
def generate_structured_insight(team: str, exec_summary: str, current_week: str) -> Dict:
    if not exec_summary.strip():
        return _default_insight()

    system_prompt = """당신은 반도체 공정 주간보고를 구조화하는 분석기입니다.

규칙:
1) 원문에 없는 정보 추가 금지
2) 해석/추측 금지, 원문 사실만 추출
3) 한국어로 작성
4) 반드시 JSON 객체 1개만 출력, 마크다운/설명문 금지

JSON 스키마:
{
  "status": "on_track|risk|blocked|done|unknown",
  "achievement": ["이번주 성과 최대 3개"],
  "risk": ["리스크 최대 3개"],
  "next_week_plan": ["다음주 계획 최대 3개"],
  "dependency": ["타팀/타조직 의존사항 최대 2개"],
  "action_required": ["의사결정/지원 요청 최대 2개"],
  "kpi": [
    {"name":"지표명", "value":"현재값", "delta":"증감/변화"}
  ]
}

작성 가이드:
- 항목이 없으면 빈 배열([]) 사용
- status 판단 기준:
  - done: 핵심 과업 완료 강조
  - blocked: 진행 불가 상태 명시
  - risk: 리스크가 핵심
  - on_track: 정상 진행
  - unknown: 판단 불가
"""

    user_prompt = f"""팀: {team}
주차: {current_week}

원문:
---
{_clip(exec_summary, max_len=5000)}
---
"""

    try:
        response = _create_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=600,
        )
        raw_text = response.choices[0].message.content
        return _extract_json_object(raw_text)
    except Exception as exc:
        print(f"   ⚠️ 구조화 LLM 오류: {exc}")
        return _default_insight()


def infer_tag(summary: str, insight: Dict) -> str:
    if summary.startswith("[완료]") or summary.startswith("[리스크]") or summary.startswith(
        "[의사결정요청]"
    ):
        return ""
    if insight.get("action_required"):
        return "[의사결정요청]"
    status = insight.get("status", "")
    if status == "done":
        return "[완료]"
    if status in {"risk", "blocked"} or insight.get("risk"):
        return "[리스크]"
    return ""


def apply_tag(summary: str, insight: Dict) -> str:
    tag = infer_tag(summary, insight)
    if not tag:
        return summary
    return f"{tag} {summary}"


# ========== 도메인별 그룹핑 ==========
def get_team_primary_domain(team: str, week: str) -> str:
    _ = week
    return TEAM_GROUP_MAP.get(team, "직속")


def group_teams_by_domain(teams_data: Dict[str, Dict], week: str) -> Dict[str, List[Dict]]:
    grouped = {domain: [] for domain in DOMAIN_ORDER}
    for team, data in teams_data.items():
        domain = get_team_primary_domain(team, week)
        if domain not in DOMAIN_ORDER:
            domain = "직속"
        grouped[domain].append(
            {
                "team": team,
                "summary": data.get("summary", ""),
                "exec_summary": data.get("exec_summary", ""),
                "insight": data.get("insight", _default_insight()),
            }
        )
    for domain in grouped:
        grouped[domain].sort(key=lambda item: item["team"])
    return grouped


def _join_items(items: List[str], default_text: str = "-") -> str:
    cleaned = [item.strip() for item in items if item and item.strip()]
    if not cleaned:
        return default_text
    return " / ".join(cleaned)


def _format_kpi(items: List[Dict[str, str]]) -> str:
    if not items:
        return "-"
    chunks: List[str] = []
    for item in items[:3]:
        name = item.get("name", "").strip()
        value = item.get("value", "").strip()
        delta = item.get("delta", "").strip()
        text = name
        if value:
            text += f" {value}"
        if delta:
            text += f" ({delta})"
        if text:
            chunks.append(text)
    return " / ".join(chunks) if chunks else "-"


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_tag(summary: str) -> str:
    for tag, (style, label) in TAG_STYLE.items():
        if summary.startswith(tag):
            content = summary[len(tag) :].strip()
            return (
                f'<span style="{style} padding: 2px 6px; border-radius: 3px; '
                f'font-size: 10px; font-weight: bold;">{label}</span> '
                f"{_escape_html(content)}"
            )
    return _escape_html(summary)


# ========== HTML 출력 ==========
def generate_html(grouped_data: Dict[str, List[Dict]], week: str, timestamp: str) -> str:
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Layer3 Executive Dashboard v2 - {week}</title>
    <style>
        body {{ margin: 0; padding: 0; }}
        table {{ border-collapse: collapse; }}
        body, table, td, th, p, span, div {{
            font-family: 'Malgun Gothic', '맑은 고딕', 'Apple SD Gothic Neo', 'Segoe UI', Arial, sans-serif !important;
        }}
    </style>
</head>
<body style="margin: 0; padding: 0; background-color: #f5f5f5;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f5f5f5;">
        <tr>
            <td align="center" style="padding: 20px 10px;">
                <table width="1080" cellpadding="0" cellspacing="0" border="0" style="max-width: 1080px;">
                    <tr>
                        <td style="background-color: #1a202c; padding: 24px 28px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td style="font-size: 22px; font-weight: bold; color: #ffffff;">
                                        Layer3 Executive Dashboard v2
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
                    <tr>
                        <td style="background-color: #ffffff;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td style="background-color: {domain_color}; padding: 12px 20px; font-size: 15px; font-weight: bold; color: #ffffff;">
                                        {domain}
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 0;">
                                        <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                            <tr style="background-color: #f7fafc;">
                                                <td style="padding: 10px 12px; font-size: 11px; font-weight: bold; color: #4a5568; width: 80px; border-bottom: 2px solid #e2e8f0;">팀</td>
                                                <td style="padding: 10px 12px; font-size: 11px; font-weight: bold; color: #4a5568; width: 300px; border-bottom: 2px solid #e2e8f0;">Executive Summary</td>
                                                <td style="padding: 10px 12px; font-size: 11px; font-weight: bold; color: #4a5568; width: 250px; border-bottom: 2px solid #e2e8f0;">리스크</td>
                                                <td style="padding: 10px 12px; font-size: 11px; font-weight: bold; color: #4a5568; width: 250px; border-bottom: 2px solid #e2e8f0;">다음주 계획</td>
                                                <td style="padding: 10px 12px; font-size: 11px; font-weight: bold; color: #4a5568; width: 200px; border-bottom: 2px solid #e2e8f0;">의사결정 요청/KPI</td>
                                            </tr>
"""
        for team_data in teams:
            team = team_data["team"]
            summary = _render_tag(team_data.get("summary", ""))
            insight = team_data.get("insight", _default_insight())
            risk_text = _escape_html(_join_items(insight.get("risk", [])))
            plan_text = _escape_html(_join_items(insight.get("next_week_plan", [])))
            action_text = _escape_html(_join_items(insight.get("action_required", [])))
            kpi_text = _escape_html(_format_kpi(insight.get("kpi", [])))
            action_kpi = action_text
            if kpi_text != "-":
                if action_kpi == "-":
                    action_kpi = f"KPI: {kpi_text}"
                else:
                    action_kpi = f"{action_kpi} / KPI: {kpi_text}"

            html += f"""
                                            <tr>
                                                <td style="padding: 12px 12px; font-size: 13px; color: #2d3748; font-weight: bold; border-bottom: 1px solid #e2e8f0; vertical-align: top;">{_escape_html(team)}</td>
                                                <td style="padding: 12px 12px; font-size: 13px; color: #2d3748; line-height: 1.5; border-bottom: 1px solid #e2e8f0;">{summary}</td>
                                                <td style="padding: 12px 12px; font-size: 12px; color: #2d3748; line-height: 1.5; border-bottom: 1px solid #e2e8f0;">{risk_text}</td>
                                                <td style="padding: 12px 12px; font-size: 12px; color: #2d3748; line-height: 1.5; border-bottom: 1px solid #e2e8f0;">{plan_text}</td>
                                                <td style="padding: 12px 12px; font-size: 12px; color: #2d3748; line-height: 1.5; border-bottom: 1px solid #e2e8f0;">{action_kpi}</td>
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
def generate_markdown(grouped_data: Dict[str, List[Dict]], week: str, timestamp: str) -> str:
    md = f"""# Layer3 Executive Dashboard v2

**대상 주차**: {week}
**생성 시각**: {timestamp}

---

"""
    for domain in DOMAIN_ORDER:
        teams = grouped_data.get(domain, [])
        if not teams:
            continue
        md += f"## {domain}\n\n"
        md += (
            "| 팀 | Executive Summary | 리스크 | 다음주 계획 | 의사결정 요청/KPI |\n"
            "|-----|-------------------|--------|-------------|-------------------|\n"
        )
        for team_data in teams:
            team = team_data["team"]
            summary = team_data["summary"].replace("|", "\\|")
            insight = team_data.get("insight", _default_insight())
            risk_text = _join_items(insight.get("risk", [])).replace("|", "\\|")
            plan_text = _join_items(insight.get("next_week_plan", [])).replace("|", "\\|")
            action_text = _join_items(insight.get("action_required", []))
            kpi_text = _format_kpi(insight.get("kpi", []))
            combined = action_text if action_text != "-" else ""
            if kpi_text != "-":
                if combined:
                    combined = f"{combined} / KPI: {kpi_text}"
                else:
                    combined = f"KPI: {kpi_text}"
            if not combined:
                combined = "-"
            combined = combined.replace("|", "\\|")
            md += f"| {team} | {summary} | {risk_text} | {plan_text} | {combined} |\n"
        md += "\n---\n\n"
    return md


# ========== 메인 함수 ==========
def generate_layer3(
    week: str,
    teams: Optional[List[str]] = None,
    output_format: str = "both",
    db_source: str = "file",
) -> Dict:
    print("=" * 60)
    print("Layer3 Executive Dashboard v2 생성")
    print("=" * 60)

    if teams is None:
        teams = TEAMS

    print(f"📅 대상 주차: {week}")
    print(f"👥 대상 팀: {len(teams)}개")
    print(f"💾 데이터 소스: {db_source}")
    print()

    teams_data: Dict[str, Dict] = {}
    for team in teams:
        print(f"📝 {team} 처리 중...")
        if db_source == "opensearch":
            current_mail = load_team_mail_from_opensearch(team, week)
        else:
            current_mail = load_team_mail(team, week)

        exec_summary = extract_executive_summary(current_mail)
        print(f"   - Executive Summary: {len(exec_summary)}자")

        summary = generate_one_line_summary(team, exec_summary, week)
        insight = generate_structured_insight(team, exec_summary, week)
        summary = apply_tag(summary, insight)

        print(f"   - 요약: {summary[:50]}...")
        print(
            "   - 인사이트: "
            f"status={insight.get('status')} "
            f"risk={len(insight.get('risk', []))} "
            f"plan={len(insight.get('next_week_plan', []))} "
            f"action={len(insight.get('action_required', []))}"
        )

        teams_data[team] = {
            "summary": summary,
            "exec_summary": exec_summary,
            "insight": insight,
        }
        print("   ✅ 완료")

    print()
    grouped_data = group_teams_by_domain(teams_data, week)

    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamp_display = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    result = {"teams_data": teams_data, "grouped_data": grouped_data}

    if output_format in ("md", "both"):
        md_content = generate_markdown(grouped_data, week, timestamp_display)
        md_path = OUTPUT_DIR / f"layer3_v2_{week}_{timestamp}.md"
        with open(md_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(md_content)
        print(f"📄 Markdown 저장: {md_path}")
        result["md_path"] = str(md_path)

    if output_format in ("html", "both"):
        html_content = generate_html(grouped_data, week, timestamp_display)
        html_path = OUTPUT_DIR / f"layer3_v2_{week}_{timestamp}.html"
        with open(html_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(html_content)
        print(f"📄 HTML 저장: {html_path}")
        result["html_path"] = str(html_path)

    # 구조화 결과를 별도 JSON으로도 저장해 사후 검증/재사용 가능하게 함
    json_path = OUTPUT_DIR / f"layer3_v2_{week}_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as file_obj:
        json.dump(result, file_obj, ensure_ascii=False, indent=2)
    print(f"📄 JSON 저장: {json_path}")
    result["json_path"] = str(json_path)

    print()
    print("=" * 60)
    print("✅ Layer3 v2 생성 완료")
    print("=" * 60)
    for domain in DOMAIN_ORDER:
        print(f"   {domain}: {len(grouped_data.get(domain, []))}개 팀")
    return result


if __name__ == "__main__":
    generate_layer3(
        week=CONFIG["week"],
        teams=CONFIG["teams"],
        output_format=CONFIG["output_format"],
        db_source=CONFIG["db_source"],
    )

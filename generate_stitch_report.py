"""
Stitch-style 주간 리포트 생성 (Material Design 톤)

구성:
- Executive Summary : wiki/overview/{week}_전체요약.md 섹션 1 파싱
- Team Highlights   : generate_layer3_v2 의 Executive Summary 추출 + LLM 요약 로직 재사용
                      (해당 주차 메일 데이터가 없으면 overview md 섹션 2 로 자동 폴백)
- Cross-Team Issues : overview md 섹션 3 파싱
- Major Risks       : overview md 섹션 4 파싱
- 출력              : output/stitch_{week}_{ts}.html
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from generate_layer3_v2 import (
    DOMAIN_COLORS,
    DOMAIN_ORDER,
    TEAM_GROUP_MAP,
    _default_insight,
    apply_tag,
    extract_executive_summary,
    generate_one_line_summary,
    generate_structured_insight,
    load_team_mail,
    load_team_mail_from_opensearch,
)

# ========== 설정 ==========
OUTPUT_DIR = Path("output")
OVERVIEW_DIR = Path("wiki/overview")

# stitch.html 의 팀 순서 (EQUIP, PROCESS, PE, QA, TEST, YIELD)
REPORT_TEAMS = ["EQUIP팀", "PROCESS팀", "PE팀", "QA팀", "TEST팀", "YIELD팀"]

TEAM_DISPLAY = {
    "EQUIP팀": "EQUIP",
    "PROCESS팀": "PROCESS",
    "PE팀": "PE",
    "QA팀": "QA",
    "TEST팀": "TEST",
    "YIELD팀": "YIELD",
}

CONFIG = {
    "week": "2026-11",
    "teams": None,
    "db_source": "file",  # "file" | "opensearch"
}

# 3종 주차 tag: (label, background, text color)
# 임원 행동 관점 분류 — 정상(skim) / 리스크(주시) / 의사결정(결재)
TAG_NORMAL:   Tuple[str, str, str] = ("정상",   "#d1fae5", "#065f46")  # green
TAG_RISK:     Tuple[str, str, str] = ("리스크",  "#fee2e2", "#991b1b")  # red
TAG_DECISION: Tuple[str, str, str] = ("의사결정", "#fef3c7", "#92400e")  # amber

# layer3_v2 insight.status → 3-way 매핑
# - done / on_track → 정상
# - risk / unknown  → 리스크 (unknown 은 보수적 기본값)
# - blocked          → 의사결정
# - action_required 가 채워지면 status 무관하게 의사결정으로 승격
STATUS_TO_TAG: Dict[str, Tuple[str, str, str]] = {
    "done":     TAG_NORMAL,
    "on_track": TAG_NORMAL,
    "risk":     TAG_RISK,
    "blocked":  TAG_DECISION,
    "unknown":  TAG_RISK,
}


# ========== 유틸 ==========
def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def week_to_period(week: str) -> str:
    """'2026-11' -> '2026.03.09 — 2026.03.15' (ISO week 기준)."""
    try:
        year_s, wk_s = week.split("-")
        year, wk = int(year_s), int(wk_s)
        jan4 = date(year, 1, 4)
        week1_monday = jan4 - timedelta(days=jan4.isoweekday() - 1)
        start = week1_monday + timedelta(weeks=wk - 1)
        end = start + timedelta(days=6)
        return f"{start.strftime('%Y.%m.%d')} — {end.strftime('%Y.%m.%d')}"
    except Exception:
        return week


# ========== Overview MD 파싱 ==========
# 한 줄짜리 섹션 헤더 감지 — 관대하게:
#   **1. X**   **1) X**   ### 1. X   ## 1. X   1. **X**   **1. X:**
_HEADER_LINE_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?"
    r"\*{0,2}[ \t]*"
    r"(\d+)[.)][ \t]*"
    r"\*{0,2}[ \t]*"
    r"([^\n*#][^\n*]*?)"
    r"[ \t]*\*{0,2}[ \t]*:?[ \t]*$",
    re.MULTILINE,
)

_SEPARATORS = ":：–—\\-·•/→"
_TITLE_DESC_RE = re.compile(
    r"^\s*\*{0,2}\s*"
    r"(?P<title>[^" + _SEPARATORS + r"\n*]+?)"
    r"\s*\*{0,2}\s*"
    r"[" + _SEPARATORS + r"]\s*"
    r"(?P<desc>.+)$"
)

_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•▪·]|\d+[.)])\s+")


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def _extract_sections(text: str) -> Dict[str, str]:
    text = _strip_frontmatter(text)
    matches = list(_HEADER_LINE_RE.finditer(text))
    if not matches:
        return {}
    sections: Dict[str, str] = {}
    for i, m in enumerate(matches):
        num = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[num] = text[body_start:body_end].strip()
    return sections


def _parse_bullet_lines(body: str) -> List[str]:
    out: List[str] = []
    for raw in body.split("\n"):
        m = _BULLET_PREFIX_RE.match(raw)
        if not m:
            continue
        content = raw[m.end():].strip()
        if content:
            out.append(content)
    return out


def _split_title_desc(bullet: str) -> Dict[str, str]:
    m = _TITLE_DESC_RE.match(bullet)
    if m:
        return {"title": m.group("title").strip(), "desc": m.group("desc").strip()}
    return {"title": bullet.strip(), "desc": ""}


def parse_overview(week: str) -> Dict:
    md_path = OVERVIEW_DIR / f"{week}_전체요약.md"
    if not md_path.exists():
        print(f"   ⚠️  Overview md 없음: {md_path}")
        return {
            "exec_bullets": [],
            "team_md_bullets": {},
            "cross_team": [],
            "risks": [],
        }
    text = md_path.read_text(encoding="utf-8")
    sections = _extract_sections(text)

    team_md_bullets: Dict[str, str] = {}
    for bullet in _parse_bullet_lines(sections.get("2", "")):
        parsed = _split_title_desc(bullet)
        if parsed["title"] and parsed["desc"]:
            team_md_bullets[parsed["title"]] = parsed["desc"]

    return {
        "exec_bullets": _parse_bullet_lines(sections.get("1", "")),
        "team_md_bullets": team_md_bullets,
        "cross_team": [_split_title_desc(b) for b in _parse_bullet_lines(sections.get("3", ""))],
        "risks": [_split_title_desc(b) for b in _parse_bullet_lines(sections.get("4", ""))],
    }


# ========== Team Data (layer3_v2 + fallback) ==========
def build_team_data(
    week: str,
    teams: List[str],
    db_source: str,
    overview_fallback: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict]:
    teams_data: Dict[str, Dict] = {}
    for team in teams:
        print(f"📝 {team} 처리 중...")
        if db_source == "opensearch":
            try:
                mail = load_team_mail_from_opensearch(team, week)
            except Exception as exc:
                print(f"   ⚠️  opensearch 로드 실패: {exc}")
                mail = ""
        else:
            mail = load_team_mail(team, week)

        exec_summary = extract_executive_summary(mail)
        print(f"   - Executive Summary: {len(exec_summary)}자")

        if exec_summary.strip():
            summary = generate_one_line_summary(team, exec_summary, week)
            insight = generate_structured_insight(team, exec_summary, week)
            summary = apply_tag(summary, insight)
            source = "layer3_v2"
        else:
            # 메일 없음 → overview md 섹션 2 에서 팀 한 줄 가져오기
            fallback = (overview_fallback or {}).get(team, "")
            summary = fallback or "데이터 없음"
            insight = _default_insight()
            source = "overview_md" if fallback else "empty"

        teams_data[team] = {
            "summary": summary,
            "exec_summary": exec_summary,
            "insight": insight,
            "source": source,
        }
        print(f"   - {team} ({source}): {summary[:60]}")
    return teams_data


# ========== Team Highlights (domain-grouped one-liners) ==========
def _strip_tag_prefix(summary: str) -> str:
    for tag in ("[완료] ", "[리스크] ", "[의사결정요청] "):
        if summary.startswith(tag):
            return summary[len(tag):]
    return summary


def _team_line_text(team_data: Dict) -> str:
    """팀 한 줄 요약 (태그 프리픽스 제거)."""
    return _strip_tag_prefix(team_data.get("summary", "")).strip() or "데이터 없음"


def _team_tag(team_data: Dict) -> Tuple[str, str, str]:
    """(label, bg, fg) 반환. 3-way: 정상 / 리스크 / 의사결정."""
    insight = team_data.get("insight") or _default_insight()
    # 임원 결재·지원 요청이 있으면 status 무관하게 의사결정으로 승격
    if insight.get("action_required"):
        return TAG_DECISION
    status = insight.get("status", "unknown")
    return STATUS_TO_TAG.get(status, TAG_RISK)


def group_by_domain(teams_data: Dict) -> Dict[str, List[str]]:
    """layer3_v2 TEAM_GROUP_MAP 기준으로 팀을 도메인별로 분류."""
    grouped: Dict[str, List[str]] = {d: [] for d in DOMAIN_ORDER}
    for team in teams_data:
        domain = TEAM_GROUP_MAP.get(team, "직속")
        if domain not in grouped:
            grouped[domain] = []
        grouped[domain].append(team)
    return grouped


def render_team_highlights(teams_data: Dict) -> str:
    """도메인별 컬러 파티션 + 팀 한 줄 요약 렌더."""
    grouped = group_by_domain(teams_data)
    partitions: List[str] = []
    for domain in DOMAIN_ORDER:
        teams = grouped.get(domain, [])
        if not teams:
            continue
        color = DOMAIN_COLORS.get(domain, "#6fa8dc")
        rows: List[str] = []
        for i, team in enumerate(teams):
            display = TEAM_DISPLAY.get(team, team.replace("팀", ""))
            summary = _team_line_text(teams_data[team])
            tag_label, tag_bg, tag_fg = _team_tag(teams_data[team])
            divider = (
                "border-bottom:1px solid #e2e8f0;"
                if i < len(teams) - 1
                else ""
            )
            rows.append(f"""<tr>
<td style="padding:14px 20px; {divider}">
<table border="0" cellpadding="0" cellspacing="0" role="presentation" width="100%">
<tbody><tr>
<td width="90" valign="top" style="font-size:14px; font-weight:bold; color:{color}; white-space:nowrap;">{_escape(display)}</td>
<td width="92" valign="top" style="padding-left:8px; white-space:nowrap;"><span style="display:inline-block; background:{tag_bg}; color:{tag_fg}; padding:3px 10px; border-radius:10px; font-size:11px; font-weight:bold;">{_escape(tag_label)}</span></td>
<td valign="top" style="padding-left:12px; font-size:14px; line-height:20px; color:#506076;">{_escape(summary)}</td>
</tr></tbody></table>
</td>
</tr>""")
        partitions.append(f"""<tr>
<td class="pb-4">
<table border="0" cellpadding="0" cellspacing="0" role="presentation" width="100%" style="border:1px solid #e2e8f0; border-radius:8px; overflow:hidden; background-color:#ffffff;">
<tbody>
<tr>
<td style="background-color:{color}; color:#ffffff; padding:12px 20px; font-size:14px; font-weight:bold; letter-spacing:0.5px;">{_escape(domain)}</td>
</tr>
{''.join(rows)}
</tbody></table>
</td>
</tr>""")
    return "\n".join(partitions) if partitions else '<tr><td class="text-sm text-secondary">팀 데이터 없음</td></tr>'


# ========== Section Rendering ==========
def render_exec_summary(bullets: List[str]) -> str:
    if not bullets:
        return '<p class="text-lg text-on-surface">데이터 없음</p>'
    joined = " ".join(_escape(b) for b in bullets)
    return (
        '<p class="text-lg leading-relaxed text-on-surface font-medium border-l-4 border-primary pl-6">'
        f"{joined}"
        "</p>"
    )


def render_cross_team(items: List[Dict]) -> str:
    if not items:
        return '<tr><td class="text-sm text-secondary">크로스팀 이슈 없음</td></tr>'
    rows = []
    for idx, item in enumerate(items, 1):
        pb_class = "pb-4" if idx < len(items) else ""
        rows.append(f"""<tr>
<td class="{pb_class}">
<div class="bg-surface-container-low p-5 rounded-lg">
<div class="text-xs font-bold text-secondary mb-2">ISSUE {idx:02d}: {_escape(item['title'])}</div>
<p class="text-sm text-on-surface leading-relaxed">{_escape(item['desc'])}</p>
</div>
</td>
</tr>""")
    return "\n".join(rows)


def render_risks(items: List[Dict]) -> str:
    if not items:
        return '<p class="text-sm text-secondary">리스크 없음</p>'
    cards = []
    for idx, item in enumerate(items, 1):
        cards.append(f"""<div class="flex items-center p-5 bg-error-container/10 border-l-4 border-error rounded-r-lg">
<div class="bg-error text-on-error w-8 h-8 rounded-full flex items-center justify-center mr-4 shrink-0 font-bold text-xs">{idx}</div>
<div>
<h4 class="font-bold text-on-surface-variant text-sm">{_escape(item['title'])}</h4>
<p class="text-xs text-on-surface-variant opacity-80">{_escape(item['desc'])}</p>
</div>
</div>""")
    return "\n".join(cards)


# ========== Full HTML Rendering ==========
_FONT_STACK = (
    "'Pretendard','Pretendard Variable','-apple-system',"
    "BlinkMacSystemFont,'Segoe UI','Malgun Gothic','맑은 고딕',"
    "'Apple SD Gothic Neo',sans-serif"
)

_TAILWIND_CONFIG = """
tailwind.config = {
    darkMode: "class",
    theme: {
        extend: {
            colors: {
                "secondary": "#506076",
                "surface": "#f7f9fb",
                "background": "#f7f9fb",
                "surface-container": "#e8eff3",
                "surface-container-low": "#f0f4f7",
                "surface-container-lowest": "#ffffff",
                "surface-container-high": "#e1e9ee",
                "surface-container-highest": "#d9e4ea",
                "surface-variant": "#d9e4ea",
                "on-surface": "#2a3439",
                "on-surface-variant": "#566166",
                "on-background": "#2a3439",
                "primary": "#465f88",
                "on-primary": "#f6f7ff",
                "primary-container": "#d6e3ff",
                "error": "#9f403d",
                "on-error": "#fff7f6",
                "error-container": "#fe8983"
            },
            borderRadius: {
                "DEFAULT": "0.125rem",
                "lg": "0.25rem",
                "xl": "0.5rem",
                "full": "0.75rem"
            },
            fontFamily: {
                "headline": ["Pretendard","Pretendard Variable","-apple-system","BlinkMacSystemFont","Segoe UI","Malgun Gothic","맑은 고딕","Apple SD Gothic Neo","sans-serif"],
                "body":     ["Pretendard","Pretendard Variable","-apple-system","BlinkMacSystemFont","Segoe UI","Malgun Gothic","맑은 고딕","Apple SD Gothic Neo","sans-serif"],
                "label":    ["Pretendard","Pretendard Variable","-apple-system","BlinkMacSystemFont","Segoe UI","Malgun Gothic","맑은 고딕","Apple SD Gothic Neo","sans-serif"]
            }
        }
    }
}
"""


def generate_report_html(week: str, teams_data: Dict, overview: Dict) -> str:
    period = week_to_period(week)
    title = f"{week} 전체 팀 종합 요약"

    team_cards_html = render_team_highlights(teams_data)
    exec_html = render_exec_summary(overview["exec_bullets"])
    cross_html = render_cross_team(overview["cross_team"])
    risks_html = render_risks(overview["risks"])

    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>{_escape(title)}</title>
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<script id="tailwind-config">{_TAILWIND_CONFIG}</script>
<style>
.material-symbols-outlined {{
    font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
    vertical-align: middle;
}}
html, body, table, td, div, p, h1, h2, h3, h4, h5, h6, span, li, ul {{
    font-family: {_FONT_STACK} !important;
}}
body {{ margin: 0; padding: 0; background-color: #f7f9fb; font-family: {_FONT_STACK}; }}
table {{ border-collapse: collapse !important; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
</style>
</head>
<body class="bg-surface font-body text-on-surface">
<table border="0" cellpadding="0" cellspacing="0" class="bg-surface" role="presentation" width="100%">
<tbody><tr>
<td align="center" style="padding: 40px 0;">
<table border="0" cellpadding="0" cellspacing="0" role="presentation" style="max-width: 800px; width: 100%;" width="800">

<!-- Report Header -->
<tbody><tr>
<td class="px-8 pb-10">
<table border="0" cellpadding="0" cellspacing="0" role="presentation" width="100%">
<tbody><tr>
<td>
<div class="inline-block px-3 py-1 bg-surface-container-highest text-on-surface-variant text-xs font-bold tracking-widest rounded-full mb-4">WEEKLY REPORT</div>
<h1 class="text-4xl font-extrabold tracking-tighter text-on-surface mb-2">{_escape(title)}</h1>
<p class="text-secondary font-medium tracking-wide">PERIOD: {_escape(period)} | STRATEGIC INSIGHTS GROUP</p>
</td>
</tr>
</tbody></table>
</td>
</tr>

<!-- Executive Summary -->
<tr>
<td class="px-8 pb-12">
<table border="0" cellpadding="0" cellspacing="0" class="bg-surface-container-low rounded-xl" role="presentation" width="100%">
<tbody><tr>
<td class="p-8">
<h2 class="text-sm font-bold uppercase tracking-widest text-primary mb-6 flex items-center">
<span class="material-symbols-outlined mr-2" style="font-size: 18px;">summarize</span> Executive Summary
</h2>
<div class="space-y-4">
{exec_html}
</div>
</td>
</tr>
</tbody></table>
</td>
</tr>

<!-- Team Highlights -->
<tr>
<td class="px-8 pb-12">
<h2 class="text-sm font-bold uppercase tracking-widest text-primary mb-8 flex items-center">
<span class="material-symbols-outlined mr-2" style="font-size: 18px;">groups</span> Team Highlights
</h2>
<table border="0" cellpadding="0" cellspacing="0" role="presentation" width="100%">
<tbody>
{team_cards_html}
</tbody></table>
</td>
</tr>

<!-- Cross-Team Issues -->
<tr>
<td class="px-8 pb-12">
<table border="0" cellpadding="0" cellspacing="0" class="bg-surface-container rounded-xl overflow-hidden" role="presentation" width="100%">
<tbody><tr>
<td class="p-8">
<h2 class="text-sm font-bold uppercase tracking-widest text-primary mb-6 flex items-center">
<span class="material-symbols-outlined mr-2" style="font-size: 18px;">sync_alt</span> Cross-Team Issues
</h2>
<table border="0" cellpadding="0" cellspacing="0" role="presentation" width="100%">
<tbody>
{cross_html}
</tbody></table>
</td>
</tr>
</tbody></table>
</td>
</tr>

<!-- Major Risks -->
<tr>
<td class="px-8 pb-16">
<table border="0" cellpadding="0" cellspacing="0" role="presentation" width="100%">
<tbody><tr>
<td>
<h2 class="text-sm font-bold uppercase tracking-widest text-error mb-6 flex items-center">
<span class="material-symbols-outlined mr-2" style="font-size: 18px; font-variation-settings: 'FILL' 1;">warning</span> Major Risks
</h2>
<div class="space-y-4">
{risks_html}
</div>
</td>
</tr>
</tbody></table>
</td>
</tr>

<!-- Footer -->
<tr>
<td class="px-8 py-10 border-t border-slate-200 text-center">
<div class="text-sm font-semibold uppercase tracking-widest text-slate-500">Weekly Mail Agent · {_escape(week)} Auto-Generated Report</div>
</td>
</tr>

</tbody></table>
</td>
</tr>
</tbody></table>
</body></html>
"""


# ========== Main ==========
def generate_stitch_report(
    week: str = CONFIG["week"],
    teams: Optional[List[str]] = None,
    db_source: str = CONFIG["db_source"],
) -> str:
    print("=" * 60)
    print(f"Stitch Report 생성: {week}")
    print("=" * 60)

    if teams is None:
        teams = REPORT_TEAMS

    overview = parse_overview(week)
    print(
        f"📘 Overview: exec={len(overview['exec_bullets'])} "
        f"cross={len(overview['cross_team'])} "
        f"risks={len(overview['risks'])} "
        f"team_fallback={len(overview['team_md_bullets'])}"
    )

    teams_data = build_team_data(
        week=week,
        teams=teams,
        db_source=db_source,
        overview_fallback=overview["team_md_bullets"],
    )

    html = generate_report_html(week, teams_data, overview)

    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"stitch_{week}_{ts}.html"
    out_path.write_text(html, encoding="utf-8")
    print()
    print("=" * 60)
    print(f"✅ HTML 저장: {out_path}")
    print("=" * 60)
    return str(out_path)


if __name__ == "__main__":
    generate_stitch_report(
        week=CONFIG["week"],
        teams=CONFIG["teams"],
        db_source=CONFIG["db_source"],
    )

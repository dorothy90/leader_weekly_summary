"""
Stitch-style 주간 리포트 생성 (SK 브랜드 / Material 3 톤)

입력: wiki/overview/{week}_전체요약.md (wiki_builder → wiki_export 산출물)
출력: output/stitch_{week}_{ts}.html

섹션:
  1. 이번 주 조직 핵심
  2. 팀별 하이라이트
  3. 크로스팀 이슈 (그룹 단위: 소제목 + 연관팀 pill + 팀별 bullet + 종합)
  4. 주요 리스크 항목 (3열 테이블, 영향도는 '리스크 내용' 하위 보조 텍스트)
  5. 조직 차원 권고사항
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

OUTPUT_DIR = Path("output")
OVERVIEW_DIR = Path("wiki/overview")
WEEK_DEFAULT = "2026-11"

# 팀 ↔ 소속 매핑 (섹션 2 파티션)
TEAM_ORG_MAP = {
    "EQUIP":   "DRAM PTE",
    "PE":      "DRAM PTE",
    "PROCESS": "NAND PTE",
    "QA":      "DRAM SRT",
    "TEST":    "NAND SRT",
    "YIELD":   "직속",
}
ORG_ORDER = ["DRAM PTE", "NAND PTE", "DRAM SRT", "NAND SRT", "직속"]
ORG_FALLBACK = "기타"


def _team_org(team: str) -> str:
    key = (team or "").strip().rstrip("팀").strip()
    return TEAM_ORG_MAP.get(key, ORG_FALLBACK)


# ========== Overview 파싱 ==========
_SECTION_RE = re.compile(
    r"\*\*(\d+)\.\s*([^\*]+?)\*\*\s*(.*?)(?=\n\*\*\d+\.|\Z)",
    re.DOTALL,
)
_TITLE_DESC_RE = re.compile(r"\*\*(.+?)\*\*\s*[:：–—]\s*(.+)")
_PLAIN_TITLE_DESC_RE = re.compile(r"^\*{0,2}\s*([^:：*\n]+?)\s*\*{0,2}\s*[:：]\s*(.+)$")
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•▪·]|\d+[.)])\s+")
_MD_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")

_RISK_HEADER_ALIAS = {
    "항목": "항목",
    "리스크": "항목",
    "리스크명": "항목",
    "상세내용": "상세내용",
    "상세 내용": "상세내용",
    "상세": "상세내용",
    "설명": "상세내용",
    "영향도": "영향도",
    "임팩트": "영향도",
    "대응방안": "대응방안",
    "대응 방안": "대응방안",
    "대응": "대응방안",
    "대응 필요 팀": "대응방안",
    "대응팀": "대응방안",
}

_CROSS_GROUP_HEADER_RE = re.compile(
    r"^\s*#{2,4}\s*"
    r"(?:[①②③④⑤⑥⑦⑧⑨⑩]\s*)?"
    r"\**\s*"
    r"(?P<title>[^(\n#][^(\n]*?)"
    r"\s*\**\s*"
    r"(?:\(\s*(?P<teams>[^)]+?)\s*\))?\s*$"
)
_CROSS_TEAM_SPLIT_RE = re.compile(r"\s*(?:<->|↔|⇄|⟷|,)\s*")
_SUMMARY_KEYS = {"종합", "요약", "Summary", "summary", "정리"}
_STATUS_KEYS = {"상태"}
_STATUS_CONT_RE = re.compile(
    r"(?:지속|계속)\s*\(\s*(?P<first>\d{4}-\d{2})\s*부터\s*(?P<n>\d+)\s*주\s*연속\s*\)"
)


def _parse_status_label(status_raw: str) -> Optional[Dict]:
    s = (status_raw or "").strip()
    if not s:
        return None
    if s.startswith("신규"):
        return {"kind": "신규", "label": "신규"}
    m = _STATUS_CONT_RE.search(s)
    if m:
        return {
            "kind": "지속",
            "first": m.group("first"),
            "n": int(m.group("n")),
            "label": f"{m.group('first')}부터 {m.group('n')}주 연속",
        }
    return {"kind": "지속", "label": s}


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def _extract_sections(text: str) -> Dict[str, str]:
    text = _strip_frontmatter(text)
    sections: Dict[str, str] = {}
    for m in _SECTION_RE.finditer(text):
        sections[m.group(1)] = m.group(3).strip()
    return sections


def _parse_bullet_lines(body: str) -> List[str]:
    out: List[str] = []
    for raw in body.split("\n"):
        line = raw.strip()
        m = _BULLET_PREFIX_RE.match(line)
        if m:
            content = line[m.end():].strip()
            if content:
                out.append(content)
    return out


def _split_title_desc(bullet: str) -> Dict[str, str]:
    m = _TITLE_DESC_RE.match(bullet)
    if m:
        return {"title": m.group(1).strip(), "desc": m.group(2).strip()}
    m = _PLAIN_TITLE_DESC_RE.match(bullet)
    if m:
        return {"title": m.group(1).strip().strip("*").strip(), "desc": m.group(2).strip()}
    return {"title": bullet.strip(), "desc": ""}


def _parse_table_cells(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip().strip("*").strip() for c in s.split("|")]


def _parse_markdown_table(body: str) -> List[Dict[str, str]]:
    lines = [ln for ln in body.split("\n") if ln.strip()]
    rows: List[Dict[str, str]] = []
    i = 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and _MD_TABLE_SEP_RE.match(lines[i + 1].strip()):
            header = _parse_table_cells(lines[i])
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = _parse_table_cells(lines[j])
                if len(cells) == len(header):
                    rows.append(dict(zip(header, cells)))
                j += 1
            break
        i += 1
    return rows


def _normalize_risk_row(row: Dict[str, str]) -> Dict[str, str]:
    out = {"항목": "", "상세내용": "", "영향도": "", "대응방안": ""}
    for k, v in row.items():
        canonical = _RISK_HEADER_ALIAS.get(k.strip())
        if canonical:
            out[canonical] = v.strip()
    return out


def _parse_cross_team_groups(body: str) -> List[Dict]:
    groups: List[Dict] = []
    current: Optional[Dict] = None
    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        header_match = (
            _CROSS_GROUP_HEADER_RE.match(line)
            if line.lstrip().startswith("#")
            else None
        )
        if header_match:
            if current is not None:
                groups.append(current)
            teams_raw = header_match.group("teams") or ""
            teams = [t.strip() for t in _CROSS_TEAM_SPLIT_RE.split(teams_raw) if t.strip()]
            current = {
                "title": header_match.group("title").strip().strip("*").strip(),
                "related_teams": teams,
                "entries": [],
                "summary": "",
                "status": None,
            }
            continue
        bullet_match = _BULLET_PREFIX_RE.match(line)
        if bullet_match and current is not None:
            content = line[bullet_match.end():].strip()
            parsed = _split_title_desc(content)
            title = parsed["title"]
            desc = parsed["desc"]
            if title in _STATUS_KEYS:
                current["status"] = _parse_status_label(desc or title)
            elif title in _SUMMARY_KEYS:
                current["summary"] = desc or title
            elif title:
                current["entries"].append({"team": title, "content": desc or content})
            else:
                current["entries"].append({"team": "", "content": content})
    if current is not None:
        groups.append(current)
    return [g for g in groups if g["entries"] or g["summary"]]


def parse_overview(week: str) -> Dict:
    md_path = OVERVIEW_DIR / f"{week}_전체요약.md"
    if not md_path.exists():
        print(f"⚠️  Overview md 없음: {md_path}")
        return {
            "exec_bullets": [],
            "team_bullets": [],
            "cross_groups": [],
            "risks_table": [],
            "recommendations": [],
        }
    text = md_path.read_text(encoding="utf-8")
    sections = _extract_sections(text)

    section_2_entries: List[Dict[str, str]] = []
    for bullet in _parse_bullet_lines(sections.get("2", "")):
        parsed = _split_title_desc(bullet)
        team = parsed["title"] or "팀"
        content = parsed["desc"] or parsed["title"]
        section_2_entries.append({
            "team": team,
            "content": content,
            "org": _team_org(team),
        })

    md_table = _parse_markdown_table(sections.get("4", ""))
    risks_table = [_normalize_risk_row(r) for r in md_table] if md_table else []

    section_3_body = sections.get("3", "")
    return {
        "exec_bullets": _parse_bullet_lines(sections.get("1", "")),
        "team_bullets": section_2_entries,
        "cross_groups": _parse_cross_team_groups(section_3_body),
        "cross_bullets": [_split_title_desc(b) for b in _parse_bullet_lines(section_3_body)],
        "risks_table": risks_table,
        "recommendations": [
            _split_title_desc(b) for b in _parse_bullet_lines(sections.get("5", ""))
        ],
    }


# ========== 유틸 ==========
def _esc(s: str) -> str:
    return html.escape(s or "")


def week_to_period(week: str) -> str:
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


# ========== 섹션 렌더러 ==========
def render_section_1(bullets: List[str]) -> str:
    if not bullets:
        return '<p class="text-on-surface-variant">데이터 없음</p>'
    items = "".join(
        f"""<li class="flex items-start gap-3">
<span class="material-symbols-outlined text-secondary mt-0.5" data-weight="fill">radio_button_checked</span>
<span>{_esc(b)}</span>
</li>"""
        for b in bullets
    )
    return f"""<div class="bg-surface-container-low p-8 rounded-lg relative overflow-hidden">
<div class="absolute left-0 top-0 boㅌ1ㅌttom-0 w-2 bg-gradient-to-b from-primary to-secondary"></div>
<ul class="space-y-4 text-on-surface text-base">
{items}
</ul>
</div>"""


def render_section_2(entries: List[Dict]) -> str:
    if not entries:
        return '<p class="text-on-surface-variant">팀 데이터 없음</p>'

    grouped: Dict[str, List[Dict]] = {}
    for e in entries:
        grouped.setdefault(e.get("org") or ORG_FALLBACK, []).append(e)

    ordered_orgs = [o for o in ORG_ORDER if o in grouped] + [
        o for o in grouped if o not in ORG_ORDER
    ]

    blocks: List[str] = []
    for org in ordered_orgs:
        team_items = "".join(
            f"""<li class="flex items-start gap-2">
<span class="font-bold text-on-surface whitespace-nowrap min-w-[100px]">{_esc(e['team'])}:</span>
<span>{_esc(e['content'])}</span>
</li>"""
            for e in grouped[org]
        )
        blocks.append(f"""<div>
<div class="text-xs font-bold uppercase tracking-widest text-primary pb-2 mb-3 border-b border-surface-variant">{_esc(org)}</div>
<ul class="space-y-2 text-sm text-on-surface-variant">
{team_items}
</ul>
</div>""")

    return f"""<div class="bg-surface-container-lowest p-8 rounded-xl shadow-[0_8px_30px_rgb(0,0,0,0.04)]">
<div class="flex flex-col gap-5">
{"".join(blocks)}
</div>
</div>"""


def render_section_3(groups: List[Dict], fallback_bullets: Optional[List[Dict]] = None) -> str:
    if not groups and fallback_bullets:
        cards = []
        for b in fallback_bullets:
            title = (b.get("title") or "").strip()
            desc = (b.get("desc") or "").strip()
            if not (title or desc):
                continue
            body = (
                f'<p class="text-on-surface-variant leading-relaxed text-sm">{_esc(desc)}</p>'
                if desc else ""
            )
            cards.append(f"""<div class="bg-surface-container-lowest p-8 rounded-xl shadow-[0_8px_30px_rgb(0,0,0,0.04)] border-l-4 border-secondary">
<div class="flex items-center gap-3 mb-2">
<span class="material-symbols-outlined text-secondary text-2xl">sync_problem</span>
<h4 class="font-headline text-lg font-bold text-on-surface">{_esc(title or desc)}</h4>
</div>
{body}
</div>""")
        if cards:
            return '<div class="flex flex-col gap-6">' + "".join(cards) + "</div>"
    if not groups:
        return '<p class="text-on-surface-variant">크로스팀 이슈 없음</p>'

    bucket_new: List[Dict] = []
    bucket_cont: List[Dict] = []
    bucket_unknown: List[Dict] = []
    for g in groups:
        kind = (g.get("status") or {}).get("kind")
        if kind == "신규":
            bucket_new.append(g)
        elif kind == "지속":
            bucket_cont.append(g)
        else:
            bucket_unknown.append(g)

    def _card_html(g: Dict, accent_classes: str, icon: str) -> str:
        teams_pill = ""
        if g["related_teams"]:
            pills = "".join(
                f'<span class="inline-block text-xs font-semibold text-on-secondary-container bg-secondary-fixed px-2 py-0.5 rounded-full mr-1">{_esc(t)}</span>'
                for t in g["related_teams"]
            )
            teams_pill = f'<div class="mt-1 mb-3">{pills}</div>'

        entries_html = ""
        if g["entries"]:
            lis = "".join(
                f"""<li class="flex items-start gap-2">
<span class="font-bold text-on-surface whitespace-nowrap min-w-[100px]">{_esc(e['team'])}:</span>
<span>{_esc(e['content'])}</span>
</li>"""
                for e in g["entries"]
            )
            entries_html = f'<ul class="space-y-2 text-sm text-on-surface-variant">{lis}</ul>'

        summary_html = ""
        if g["summary"]:
            summary_html = (
                f'<p class="text-on-surface-variant leading-relaxed text-sm mt-4 pt-4 border-t border-surface-variant">'
                f'<span class="font-bold text-primary">종합 — </span>{_esc(g["summary"])}'
                f'</p>'
            )

        chain_subtitle = ""
        status = g.get("status") or {}
        if status.get("kind") == "지속" and status.get("first") and status.get("n"):
            chain_subtitle = (
                f'<p class="text-xs text-amber-700 font-semibold mb-2">'
                f'⌛ {_esc(status["first"])}부터 {status["n"]}주 연속</p>'
            )

        return (
            f'<div class="bg-surface-container-lowest p-8 rounded-xl shadow-[0_8px_30px_rgb(0,0,0,0.04)] {accent_classes}">'
            f'<div class="flex items-center gap-3 mb-1">'
            f'<span class="material-symbols-outlined text-2xl">{icon}</span>'
            f'<h4 class="font-headline text-lg font-bold text-on-surface">{_esc(g["title"])}</h4>'
            f'</div>'
            f'{chain_subtitle}'
            f'{teams_pill}'
            f'{entries_html}'
            f'{summary_html}'
            f'</div>'
        )

    def _section_block(title: str, badge_classes: str, count: int,
                       cards_html: str, divider_classes: str) -> str:
        return (
            f'<div class="flex flex-col gap-4">'
            f'<div class="flex items-center gap-3 pb-2 border-b-2 {divider_classes}">'
            f'<h3 class="font-headline text-base font-bold uppercase tracking-wide">{_esc(title)}</h3>'
            f'<span class="inline-flex items-center text-xs font-bold {badge_classes} px-2.5 py-0.5 rounded-full">{count}건</span>'
            f'</div>'
            f'{cards_html}'
            f'</div>'
        )

    sections_html: List[str] = []
    if bucket_new:
        cards_html = "".join(
            _card_html(g, "border-l-4 border-emerald-500", "fiber_new")
            for g in bucket_new
        )
        sections_html.append(_section_block(
            "신규 이슈",
            "bg-emerald-100 text-emerald-800",
            len(bucket_new),
            cards_html,
            "border-emerald-500",
        ))
    if bucket_cont:
        cards_html = "".join(
            _card_html(g, "border-l-4 border-amber-500", "history")
            for g in bucket_cont
        )
        sections_html.append(_section_block(
            "지속 이슈",
            "bg-amber-100 text-amber-800",
            len(bucket_cont),
            cards_html,
            "border-amber-500",
        ))
    if bucket_unknown:
        cards_html = "".join(
            _card_html(g, "border-l-4 border-secondary", "sync_problem")
            for g in bucket_unknown
        )
        sections_html.append(_section_block(
            "기타",
            "bg-secondary-container text-on-secondary-container",
            len(bucket_unknown),
            cards_html,
            "border-secondary",
        ))

    return '<div class="flex flex-col gap-8">' + "".join(sections_html) + "</div>"


def _risk_cell(r: Dict[str, str]) -> str:
    detail = _esc(r.get("상세내용") or "")
    impact = _esc(r.get("영향도") or "")
    if impact and detail:
        return (
            f'{detail}'
            f'<div class="text-xs text-on-surface-variant mt-1 opacity-80">'
            f'<span class="font-semibold">영향 · </span>{impact}'
            f'</div>'
        )
    if impact:
        return (
            f'<div class="text-xs text-on-surface-variant opacity-80">'
            f'<span class="font-semibold">영향 · </span>{impact}'
            f'</div>'
        )
    return detail or "—"


def render_section_4(rows: List[Dict]) -> str:
    valid = [r for r in rows if any(v.strip() for v in r.values())]
    if not valid:
        return '<p class="text-on-surface-variant">리스크 없음</p>'
    body_rows = "".join(
        f"""<tr class="hover:bg-surface-container-low transition-colors">
<td class="p-4 text-sm font-medium text-on-surface align-top">{_esc(r.get("항목") or "—")}</td>
<td class="p-4 text-sm text-on-surface-variant align-top">{_risk_cell(r)}</td>
<td class="p-4 text-sm text-on-surface-variant align-top">{_esc(r.get("대응방안") or "—")}</td>
</tr>"""
        for r in valid
    )
    return f"""<div class="overflow-x-auto rounded-lg shadow-[0_8px_30px_rgb(0,0,0,0.04)] bg-surface-container-lowest">
<table class="w-full text-left border-collapse">
<thead>
<tr class="bg-primary text-on-primary">
<th class="p-4 font-semibold text-sm w-1/4">관련 팀/프로젝트</th>
<th class="p-4 font-semibold text-sm w-1/2">리스크 내용</th>
<th class="p-4 font-semibold text-sm w-1/4">대응 방안</th>
</tr>
</thead>
<tbody class="divide-y divide-surface-variant">
{body_rows}
</tbody>
</table>
</div>"""


def render_section_5(items: List[Dict]) -> str:
    if not items:
        return '<p class="text-on-surface-variant">권고사항 없음</p>'
    lis = []
    for idx, it in enumerate(items, 1):
        title = (it.get("title") or "").strip()
        desc = (it.get("desc") or "").strip()
        if title and desc:
            body = f'<span class="font-bold text-on-surface">{_esc(title)} — </span>{_esc(desc)}'
        else:
            body = _esc(desc or title)
        lis.append(f"""<li class="flex items-start gap-3">
<span class="text-primary font-bold whitespace-nowrap">{idx}.</span>
<span>{body}</span>
</li>""")
    return f"""<div class="bg-surface-container-lowest p-8 rounded-xl shadow-[0_8px_30px_rgb(0,0,0,0.04)]">
<ul class="space-y-4 text-sm text-on-surface-variant">
{"".join(lis)}
</ul>
</div>"""


# ========== 전체 HTML 템플릿 (사용자 디자인 시안 유지) ==========
_TAILWIND_CONFIG = """
tailwind.config = {
  darkMode: "class",
  theme: {
    extend: {
      "colors": {
              "surface-dim": "#d9dadb",
              "secondary": "#9e4300",
              "surface-container-low": "#f3f4f5",
              "primary-container": "#e11900",
              "on-background": "#191c1d",
              "on-tertiary-container": "#f8f7ff",
              "surface": "#f8f9fa",
              "surface-variant": "#e1e3e4",
              "on-surface": "#191c1d",
              "on-secondary-fixed-variant": "#783100",
              "tertiary-container": "#0066fe",
              "on-tertiary-fixed-variant": "#003fa4",
              "background": "#f8f9fa",
              "error": "#ba1a1a",
              "on-primary": "#ffffff",
              "outline": "#936e68",
              "on-primary-fixed-variant": "#900c00",
              "inverse-on-surface": "#f0f1f2",
              "surface-tint": "#bd1300",
              "inverse-surface": "#2e3132",
              "on-primary-fixed": "#400200",
              "on-secondary-fixed": "#341100",
              "primary-fixed-dim": "#ffb4a6",
              "on-primary-container": "#fff6f4",
              "error-container": "#ffdad6",
              "on-secondary-container": "#602600",
              "on-error-container": "#93000a",
              "surface-container-lowest": "#ffffff",
              "secondary-fixed-dim": "#ffb691",
              "tertiary-fixed-dim": "#b3c5ff",
              "surface-container-highest": "#e1e3e4",
              "on-secondary": "#ffffff",
              "primary-fixed": "#ffdad4",
              "tertiary-fixed": "#dae1ff",
              "on-error": "#ffffff",
              "inverse-primary": "#ffb4a6",
              "secondary-fixed": "#ffdbcb",
              "on-tertiary": "#ffffff",
              "surface-container-high": "#e7e8e9",
              "on-tertiary-fixed": "#001849",
              "primary": "#b31200",
              "outline-variant": "#e8bdb5",
              "surface-bright": "#f8f9fa",
              "tertiary": "#0050ca",
              "surface-container": "#edeeef",
              "on-surface-variant": "#5e3f39",
              "secondary-container": "#fe7b28"
      },
      "borderRadius": {
              "DEFAULT": "0.25rem",
              "lg": "0.5rem",
              "xl": "0.75rem",
              "full": "9999px"
      },
      "fontFamily": {
              "headline": ["Manrope", "sans-serif"],
              "body": ["Inter", "sans-serif"],
              "label": ["Inter", "sans-serif"]
      }
    }
  }
}
"""

_STYLE_BLOCK = """
body { background-color: #f8f9fa; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }
.glass-panel { background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(12px); border: 1px solid rgba(232, 189, 181, 0.15); }
.circle-motif { position: absolute; border-radius: 50%; opacity: 0.1; mix-blend-mode: multiply; pointer-events: none; }
.bg-pattern { background-image: radial-gradient(#d9dadb 1px, transparent 1px); background-size: 20px 20px; }
"""


def render_full_html(week: str, overview: Dict) -> str:
    title = f"{week} 전체 팀 종합 요약"
    period = week_to_period(week)
    s1 = render_section_1(overview["exec_bullets"])
    s2 = render_section_2(overview["team_bullets"])
    s3 = render_section_3(overview["cross_groups"], overview.get("cross_bullets"))
    s4 = render_section_4(overview["risks_table"])
    s5 = render_section_5(overview["recommendations"])

    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>{_esc(title)}</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;700;800&amp;family=Inter:wght@400;500;600&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<script id="tailwind-config">{_TAILWIND_CONFIG}</script>
<style>{_STYLE_BLOCK}</style>
</head>
<body class="font-body text-on-surface antialiased bg-surface selection:bg-primary-fixed selection:text-on-primary-fixed">
<header class="bg-slate-50 dark:bg-slate-950 sticky top-0 z-50 w-full relative overflow-hidden bg-pattern">
<div class="circle-motif bg-primary w-64 h-64 -top-12 -left-12 opacity-5"></div>
<div class="circle-motif bg-secondary w-96 h-96 -top-24 right-0 opacity-5"></div>
<div class="flex justify-between items-center w-full px-8 py-6 max-w-5xl mx-auto relative z-10">
<div class="flex items-center gap-4">
<div class="text-xl font-black text-red-600 dark:text-red-500 tracking-tighter">SK</div>
<h1 class="font-['Manrope'] font-bold tracking-tight text-red-600 dark:text-red-500 text-2xl">{_esc(title)}</h1>
</div>
<div class="hidden md:flex items-center gap-4 text-xs text-slate-600 dark:text-slate-400 uppercase tracking-widest">
<span>Period</span>
<span class="font-semibold text-slate-700 dark:text-slate-300">{_esc(period)}</span>
</div>
<div class="flex gap-4 text-red-600 dark:text-red-500">
<button class="hover:text-red-500 transition-colors"><span class="material-symbols-outlined">share</span></button>
<button class="hover:text-red-500 transition-colors"><span class="material-symbols-outlined">print</span></button>
</div>
</div>
<div class="bg-slate-100 dark:bg-slate-900 h-px w-full opacity-50 absolute bottom-0"></div>
</header>
<main class="max-w-4xl mx-auto px-4 sm:px-8 py-12 flex flex-col gap-16 relative">

<!-- Section 1: Core Highlights -->
<section class="flex flex-col gap-6">
<h3 class="font-headline text-2xl font-bold text-primary tracking-tight">이번 주 조직 핵심 (Core Highlights)</h3>
{s1}
</section>

<!-- Section 2: Team Highlights -->
<section class="flex flex-col gap-6 pt-8">
<h3 class="font-headline text-2xl font-bold text-primary tracking-tight">팀별 하이라이트 (Team Highlights)</h3>
{s2}
</section>

<!-- Section 3: Cross-team Issues -->
<section class="flex flex-col gap-6 pt-8">
<h3 class="font-headline text-2xl font-bold text-primary tracking-tight">크로스팀 이슈 (Cross-team Issues)</h3>
{s3}
</section>

<!-- Section 4: Risk Items -->
<section class="flex flex-col gap-6 pt-8">
<h3 class="font-headline text-2xl font-bold text-primary tracking-tight">주요 리스크 항목 (Risk Items)</h3>
{s4}
</section>

<!-- Section 5: Recommendations -->
<section class="flex flex-col gap-6 pt-8 pb-12">
<h3 class="font-headline text-2xl font-bold text-primary tracking-tight">조직 차원 권고사항 (Recommendations)</h3>
{s5}
</section>

</main>
<footer class="bg-slate-100 dark:bg-slate-900 full-width py-12 mt-auto pt-12 border-t border-slate-200/20">
<div class="flex flex-col items-center gap-6 text-center w-full max-w-5xl mx-auto px-8">
<div class="text-lg font-bold text-slate-900 dark:text-slate-100 font-['Inter'] uppercase tracking-widest leading-relaxed">SK Group</div>
<p class="text-xs text-slate-500 dark:text-slate-400 font-['Inter'] uppercase tracking-widest leading-relaxed mt-4">© {datetime.now().year} SK Group. Weekly Strategic Report. Confidential and Proprietary.</p>
</div>
</footer>
</body></html>
"""


# ========== 엔트리 ==========
def generate_stitch_report(week: str = WEEK_DEFAULT) -> str:
    print("=" * 60)
    print(f"Stitch Report 생성: {week}")
    print("=" * 60)

    overview = parse_overview(week)
    print(
        f"📘 Overview: exec={len(overview['exec_bullets'])} "
        f"teams={len(overview['team_bullets'])} "
        f"cross_groups={len(overview['cross_groups'])} "
        f"risks={len(overview['risks_table'])} "
        f"recos={len(overview['recommendations'])}"
    )

    html_out = render_full_html(week, overview)

    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"stitch_{week}_{ts}.html"
    out_path.write_text(html_out, encoding="utf-8")
    print()
    print("=" * 60)
    print(f"✅ HTML 저장: {out_path}")
    print("=" * 60)
    return str(out_path)


if __name__ == "__main__":
    import sys
    week = sys.argv[1] if len(sys.argv) > 1 else WEEK_DEFAULT
    generate_stitch_report(week)

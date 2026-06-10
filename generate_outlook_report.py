"""Convert overview markdown to Outlook (Word 2016) compatible HTML.

Usage:
    python generate_outlook_report.py wiki/overview/2026-11_전체요약.md
    python generate_outlook_report.py input.md -o output.html
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

try:
    import yaml  # optional; falls back to minimal parser
except ImportError:
    yaml = None

NAVY = "#0b3b76"
NAVY_MUTED = "#cfd8e3"
RED = "#b42318"
RED_BG = "#fdecec"
RED_TEXT = "#8a1818"
YELLOW = "#ca8a04"
YELLOW_BG = "#fff8e1"
YELLOW_TEXT = "#8a5a00"
BORDER = "#d0d7de"
INK = "#1f2328"
MUTED = "#57606a"
PAGE_BG = "#f4f5f7"
CARD_BG = "#ffffff"
PANEL_BG = "#f6f8fa"
FONT = "'Malgun Gothic','맑은 고딕',Arial,Helvetica,sans-serif"

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

SECTION_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?"
    r"\*{0,2}[ \t]*"
    r"(\d+)[.)][ \t]*"
    r"\*{0,2}[ \t]*"
    r"([^\n*#][^\n*]*?)"
    r"[ \t]*\*{0,2}[ \t]*:?[ \t]*$",
    re.MULTILINE,
)
BULLET_RE = re.compile(r"^-\s+(.*)")
BOLD_LABEL_RE = re.compile(r"^\*\*([^*]+)\*\*\s*(?:[:：]|[\u2013\u2014\-])?\s*(.*)$", re.DOTALL)
INLINE_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
MD_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")

RISK_HEADER_ALIAS = {
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


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    idx = text.find("\n---", 3)
    if idx == -1:
        return {}, text
    header = text[3:idx].strip()
    body = text[idx + 4 :].lstrip("\n")
    if yaml is not None:
        meta = yaml.safe_load(header) or {}
    else:
        meta = {}
        for line in header.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def split_sections(body: str) -> list[tuple[int, str, str]]:
    headers = list(SECTION_RE.finditer(body))
    out: list[tuple[int, str, str]] = []
    for i, m in enumerate(headers):
        num = int(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        out.append((num, title, body[start:end].strip()))
    return out


def parse_bullets(content: str) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    for raw in content.splitlines():
        line = raw.rstrip()
        m = BULLET_RE.match(line)
        if m:
            if buf:
                items.append(" ".join(buf).strip())
                buf = []
            buf.append(m.group(1).strip())
        elif line.strip() == "":
            if buf:
                items.append(" ".join(buf).strip())
                buf = []
        elif buf:
            buf.append(line.strip())
    if buf:
        items.append(" ".join(buf).strip())
    return items


def split_labeled(text: str) -> tuple[str, str]:
    m = BOLD_LABEL_RE.match(text)
    if m and m.group(2).strip():
        return m.group(1).strip(), m.group(2).strip()
    return "", text.strip()


def esc_inline(text: str) -> str:
    """HTML-escape text then convert **bold** markers to <b>."""
    parts: list[str] = []
    cursor = 0
    for m in INLINE_BOLD_RE.finditer(text):
        parts.append(html.escape(text[cursor : m.start()]))
        parts.append(f"<b>{html.escape(m.group(1))}</b>")
        cursor = m.end()
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def section_header(num: int, title: str, accent: str) -> str:
    return (
        f'<tr><td style="padding:16px 28px 8px 28px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;">'
        f'<tr>'
        f'<td width="4" bgcolor="{accent}" '
        f'style="width:4px; background-color:{accent}; font-size:1px; line-height:1px;">&nbsp;</td>'
        f'<td width="10" style="width:10px;">&nbsp;</td>'
        f'<td style="font-family:{FONT}; font-size:15px; line-height:20px; '
        f'font-weight:bold; color:{NAVY};">{num}. {esc_inline(title)}</td>'
        f'</tr></table></td></tr>'
    )


def render_bullet_list(bullets: list[str]) -> str:
    rows: list[str] = []
    for i, b in enumerate(bullets):
        if i > 0:
            rows.append(
                '<tr><td colspan="2" style="font-size:1px; line-height:6px;">&nbsp;</td></tr>'
            )
        rows.append(
            f'<tr>'
            f'<td valign="top" width="18" style="width:18px; font-weight:bold; color:{NAVY};">&bull;</td>'
            f'<td valign="top">{esc_inline(b)}</td>'
            f'</tr>'
        )
    return (
        f'<tr><td style="padding:4px 28px 16px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse; font-family:{FONT}; font-size:14px; '
        f'line-height:22px; color:{INK};">'
        f'{"".join(rows)}'
        f'</table></td></tr>'
    )


def render_team_table(bullets: list[str]) -> str:
    rows: list[str] = []
    for b in bullets:
        label, rest = split_labeled(b)
        badge = label.removesuffix("팀") if label.endswith("팀") else (label or "팀")
        rows.append(
            f'<tr>'
            f'<td width="90" valign="top" bgcolor="{NAVY}" '
            f'style="width:90px; padding:10px 10px; background-color:{NAVY}; '
            f'color:#ffffff; font-weight:bold; border:1px solid {NAVY};">{esc_inline(badge)}</td>'
            f'<td valign="top" bgcolor="{CARD_BG}" '
            f'style="padding:10px 12px; background-color:{CARD_BG}; '
            f'border:1px solid {BORDER}; color:{INK};">{esc_inline(rest or b)}</td>'
            f'</tr>'
        )
    return (
        f'<tr><td style="padding:4px 28px 16px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse; font-family:{FONT}; font-size:13px; line-height:19px;">'
        f'{"".join(rows)}'
        f'</table></td></tr>'
    )


def render_team_table_grouped(bullets: list[str]) -> str:
    if not bullets:
        return (
            f'<tr><td style="padding:4px 28px 16px 28px; font-family:{FONT}; '
            f'font-size:13px; color:{MUTED};">팀 데이터 없음</td></tr>'
        )

    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for b in bullets:
        label, rest = split_labeled(b)
        badge = label.removesuffix("팀") if label.endswith("팀") else (label or "팀")
        org = _team_org(label or b)
        grouped.setdefault(org, []).append((badge, rest or b, label))

    ordered_orgs = [o for o in ORG_ORDER if o in grouped] + [
        o for o in grouped if o not in ORG_ORDER
    ]

    blocks: list[str] = []
    for gi, org in enumerate(ordered_orgs):
        header_row = (
            f'<tr><td colspan="2" style="padding:10px 0 6px 0; '
            f'border-bottom:1px solid {BORDER}; color:{NAVY}; font-weight:bold; '
            f'font-size:11px; letter-spacing:1px; text-transform:uppercase;">'
            f'{esc_inline(org)}</td></tr>'
        )
        team_rows = []
        for badge, rest, _label in grouped[org]:
            team_rows.append(
                f'<tr>'
                f'<td width="90" valign="top" bgcolor="{NAVY}" '
                f'style="width:90px; padding:10px 10px; background-color:{NAVY}; '
                f'color:#ffffff; font-weight:bold; border:1px solid {NAVY};">{esc_inline(badge)}</td>'
                f'<td valign="top" bgcolor="{CARD_BG}" '
                f'style="padding:10px 12px; background-color:{CARD_BG}; '
                f'border:1px solid {BORDER}; color:{INK};">{esc_inline(rest)}</td>'
                f'</tr>'
            )
        blocks.append(header_row + "".join(team_rows))
        if gi < len(ordered_orgs) - 1:
            blocks.append(
                '<tr><td colspan="2" style="font-size:1px; line-height:10px;">&nbsp;</td></tr>'
            )

    return (
        f'<tr><td style="padding:4px 28px 16px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse; font-family:{FONT}; font-size:13px; line-height:19px;">'
        f'{"".join(blocks)}'
        f'</table></td></tr>'
    )


def render_boxes(bullets: list[str], accent: str, bg: str, text_color: str) -> str:
    rows: list[str] = []
    for i, b in enumerate(bullets):
        if i > 0:
            rows.append('<tr><td style="font-size:1px; line-height:6px;">&nbsp;</td></tr>')
        label, rest = split_labeled(b)
        if label:
            inner = (
                f'<b style="color:{text_color};">{esc_inline(label)}</b><br />'
                f'{esc_inline(rest)}'
            )
        else:
            inner = esc_inline(b)
        rows.append(
            f'<tr>'
            f'<td bgcolor="{bg}" '
            f'style="padding:10px 12px; background-color:{bg}; border-left:3px solid {accent};">'
            f'{inner}</td></tr>'
        )
    return (
        f'<tr><td style="padding:4px 28px 16px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse; font-family:{FONT}; font-size:13px; '
        f'line-height:20px; color:{INK};">'
        f'{"".join(rows)}'
        f'</table></td></tr>'
    )


CROSS_GROUP_HEADER_RE = re.compile(
    r"^\s*#{2,4}\s*"
    r"(?:[①②③④⑤⑥⑦⑧⑨⑩]\s*)?"
    r"\**\s*"
    r"(?P<title>[^(\n#][^(\n]*?)"
    r"\s*\**\s*"
    r"(?:\(\s*(?P<teams>[^)]+?)\s*\))?\s*$"
)
CROSS_TEAM_SPLIT_RE = re.compile(r"\s*(?:<->|↔|⇄|⟷|,)\s*")
SUMMARY_KEYS = {"종합", "요약", "Summary", "summary", "정리"}
WEEK_LABEL_RE = re.compile(r"^\d{4}-\d{2}$")


def parse_cross_team_groups(body: str) -> list[dict]:
    groups: list[dict] = []
    current: dict | None = None
    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        header_match = (
            CROSS_GROUP_HEADER_RE.match(line)
            if line.lstrip().startswith("#")
            else None
        )
        if header_match:
            if current is not None:
                groups.append(current)
            teams_raw = header_match.group("teams") or ""
            teams = [t.strip() for t in CROSS_TEAM_SPLIT_RE.split(teams_raw) if t.strip()]
            current = {
                "title": header_match.group("title").strip().strip("*").strip(),
                "related_teams": teams,
                "entries": [],
                "timeline_entries": [],
                "summary": "",
            }
            continue
        m = BULLET_RE.match(line.strip())
        if m and current is not None:
            content = m.group(1).strip()
            label, rest = split_labeled(content)
            if label in SUMMARY_KEYS:
                current["summary"] = rest or content
                continue
            stripped = content.lstrip("*").strip()
            matched_summary = False
            for key in SUMMARY_KEYS:
                if stripped.startswith(key):
                    after = stripped[len(key):].lstrip(" *:：—–-").strip()
                    if after:
                        current["summary"] = after
                        matched_summary = True
                        break
            if matched_summary:
                continue
            if label and WEEK_LABEL_RE.match(label):
                current["timeline_entries"].append({"week": label, "content": rest})
            elif label:
                current["entries"].append({"team": label, "content": rest})
            else:
                current["entries"].append({"team": "", "content": content})
            continue
    if current is not None:
        groups.append(current)
    return [g for g in groups if g["entries"] or g["timeline_entries"] or g["summary"]]


def _render_single_cross_card(g: dict, accent: str) -> str:
    pills = ""
    if g["related_teams"]:
        pills_text = " · ".join(esc_inline(t) for t in g["related_teams"])
        pills = (
            f' <span style="color:{MUTED}; font-weight:normal; '
            f'font-size:11px;">({pills_text})</span>'
        )
    title_row = (
        f'<tr><td style="padding:6px 12px; background-color:{YELLOW_BG}; '
        f'border-left:3px solid {accent}; color:{YELLOW_TEXT}; '
        f'font-weight:bold;">{esc_inline(g["title"])}{pills}</td></tr>'
    )
    entry_rows: list[str] = []
    for e in g["entries"]:
        team = esc_inline(e.get("team", ""))
        content = esc_inline(e.get("content", ""))
        team_chip = (
            f'<b style="color:{YELLOW_TEXT};">{team}</b> &middot; '
            if team else ""
        )
        entry_rows.append(
            f'<tr><td style="padding:6px 12px 6px 18px; '
            f'background-color:{CARD_BG}; border-left:3px solid {accent}; '
            f'color:{INK};">{team_chip}{content}</td></tr>'
        )
    timeline_rows: list[str] = []
    timeline_entries = g.get("timeline_entries") or []
    if timeline_entries:
        timeline_rows.append(
            f'<tr><td style="padding:8px 12px 2px 18px; '
            f'background-color:{CARD_BG}; border-left:3px solid {accent}; '
            f'border-top:1px solid {BORDER}; color:{MUTED}; '
            f'font-size:10px; letter-spacing:1px; text-transform:uppercase;">'
            f'주차별 흐름</td></tr>'
        )
        for t in timeline_entries:
            week = esc_inline(t.get("week", ""))
            content = esc_inline(t.get("content", ""))
            timeline_rows.append(
                f'<tr><td style="padding:2px 12px 2px 18px; '
                f'background-color:{CARD_BG}; border-left:3px solid {accent}; '
                f'color:{MUTED}; font-size:11px;">'
                f'<b style="color:{YELLOW_TEXT};">{week}</b> &middot; {content}</td></tr>'
            )
    summary_row = ""
    if g["summary"]:
        summary_row = (
            f'<tr><td style="padding:8px 12px; background-color:{PANEL_BG}; '
            f'border-left:3px solid {NAVY}; color:{INK};">'
            f'<b style="color:{NAVY};">종합 &middot; </b>'
            f'{esc_inline(g["summary"])}</td></tr>'
        )
    return title_row + "".join(entry_rows) + "".join(timeline_rows) + summary_row


def render_cross_team_groups(groups: list[dict]) -> str:
    blocks: list[str] = []
    for ci, g in enumerate(groups):
        blocks.append(_render_single_cross_card(g, YELLOW))
        if ci < len(groups) - 1:
            blocks.append(
                '<tr><td style="font-size:1px; line-height:10px;">&nbsp;</td></tr>'
            )
    return (
        f'<tr><td style="padding:4px 28px 16px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse; font-family:{FONT}; font-size:13px; '
        f'line-height:20px; color:{INK};">'
        f'{"".join(blocks)}'
        f'</table></td></tr>'
    )


def parse_table_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip().strip("*").strip() for c in s.split("|")]


def parse_risk_table(body: str) -> list[dict]:
    lines = [ln for ln in body.split("\n") if ln.strip()]
    rows: list[dict] = []
    i = 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and MD_TABLE_SEP_RE.match(lines[i + 1].strip()):
            header = parse_table_cells(lines[i])
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = parse_table_cells(lines[j])
                if len(cells) == len(header):
                    row = {"항목": "", "상세내용": "", "영향도": "", "대응방안": ""}
                    for k, v in zip(header, cells):
                        canonical = RISK_HEADER_ALIAS.get(k.strip())
                        if canonical:
                            row[canonical] = v.strip()
                    rows.append(row)
                j += 1
            break
        i += 1
    return rows


def render_risks_table(rows: list[dict]) -> str:
    valid = [r for r in rows if any(v.strip() for v in r.values())]
    if not valid:
        return (
            f'<tr><td style="padding:4px 28px 16px 28px; font-family:{FONT}; '
            f'font-size:13px; color:{MUTED};">리스크 없음</td></tr>'
        )
    header_cell_style = (
        f"padding:8px 10px; background-color:{RED_BG}; border:1px solid {BORDER}; "
        f"color:{RED_TEXT}; font-weight:bold; font-size:12px;"
    )
    body_cell_style_primary = (
        f"padding:8px 10px; background-color:{CARD_BG}; border:1px solid {BORDER}; "
        f"font-weight:bold; color:{INK};"
    )
    body_cell_style = (
        f"padding:8px 10px; background-color:{CARD_BG}; border:1px solid {BORDER}; "
        f"color:{INK};"
    )
    header_row = (
        f'<tr bgcolor="{RED_BG}">'
        f'<th align="left" bgcolor="{RED_BG}" width="20%" style="width:20%; {header_cell_style}">항목</th>'
        f'<th align="left" bgcolor="{RED_BG}" width="30%" style="width:30%; {header_cell_style}">상세내용</th>'
        f'<th align="left" bgcolor="{RED_BG}" width="22%" style="width:22%; {header_cell_style}">영향도</th>'
        f'<th align="left" bgcolor="{RED_BG}" width="28%" style="width:28%; {header_cell_style}">대응방안</th>'
        f'</tr>'
    )
    body_rows = "".join(
        f'<tr>'
        f'<td valign="top" bgcolor="{CARD_BG}" style="{body_cell_style_primary}">{esc_inline(r.get("항목") or "—")}</td>'
        f'<td valign="top" bgcolor="{CARD_BG}" style="{body_cell_style}">{esc_inline(r.get("상세내용") or "—")}</td>'
        f'<td valign="top" bgcolor="{CARD_BG}" style="{body_cell_style}">{esc_inline(r.get("영향도") or "—")}</td>'
        f'<td valign="top" bgcolor="{CARD_BG}" style="{body_cell_style}">{esc_inline(r.get("대응방안") or "—")}</td>'
        f'</tr>'
        for r in valid
    )
    return (
        f'<tr><td style="padding:4px 28px 16px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse; font-family:{FONT}; font-size:13px; line-height:19px;">'
        f'{header_row}'
        f'{body_rows}'
        f'</table></td></tr>'
    )


RECO_BG = "#eef3f9"

SECTION_STYLES = {
    1: ("bullets", NAVY),
    2: ("teams_grouped", NAVY),
    3: ("cross_groups", YELLOW, YELLOW_BG, YELLOW_TEXT),
    4: ("risks_table", RED),
    5: ("boxes", NAVY, RECO_BG, NAVY),
}


def render_section(num: int, title: str, content: str) -> str:
    style = SECTION_STYLES.get(num, ("bullets", NAVY))
    kind = style[0]
    accent = style[1]

    if kind == "risks_table":
        rows = parse_risk_table(content)
        if rows:
            return section_header(num, title, accent) + render_risks_table(rows)
        bullets = parse_bullets(content)
        return (
            section_header(num, title, accent)
            + render_boxes(bullets, RED, RED_BG, RED_TEXT)
        )

    if kind == "cross_groups":
        groups = parse_cross_team_groups(content)
        if groups:
            return section_header(num, title, accent) + render_cross_team_groups(groups)
        bullets = parse_bullets(content)
        return (
            section_header(num, title, accent)
            + render_boxes(bullets, style[1], style[2], style[3])
        )

    bullets = parse_bullets(content)
    parts = [section_header(num, title, accent)]
    if kind == "bullets":
        parts.append(render_bullet_list(bullets))
    elif kind == "teams":
        parts.append(render_team_table(bullets))
    elif kind == "teams_grouped":
        parts.append(render_team_table_grouped(bullets))
    elif kind == "boxes":
        parts.append(render_boxes(bullets, style[1], style[2], style[3]))
    return "".join(parts)


HEAD_TEMPLATE = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="ko">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="x-apple-disable-message-reformatting" />
<title>{title}</title>
<!--[if mso]>
<style type="text/css">
  * {{ mso-line-height-rule: exactly; }}
  table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  table, td, div, p, a, span {{ font-family: 'Malgun Gothic', '맑은 고딕', Arial, sans-serif !important; }}
  td {{ border-collapse: collapse; }}
</style>
<![endif]-->
</head>
"""


def render_html(meta: dict, sections: list[tuple[int, str, str]]) -> str:
    title_text = str(meta.get("title", "주간 요약"))
    week = str(meta.get("week", ""))
    preheader = ""
    if sections:
        first_bullets = parse_bullets(sections[0][2])
        if first_bullets:
            preheader = first_bullets[0][:140]

    body_rows = "".join(
        render_section(num, title, content)
        for num, title, content in sections
    )

    head = HEAD_TEMPLATE.format(title=html.escape(title_text))

    header_title = f"{week} SYLD Weekly Report" if week else "SYLD Weekly Report"
    header_row = (
        f'<tr><td bgcolor="{NAVY}" width="680" '
        f'style="width:680px; padding:20px 28px; background-color:{NAVY}; font-family:{FONT};">'
        f'<div style="color:#ffffff; font-size:20px; font-weight:bold; line-height:26px;">'
        f'{html.escape(header_title)}</div>'
        + '</td></tr>'
    )

    footer_row = (
        f'<tr><td bgcolor="{PANEL_BG}" '
        f'style="background-color:{PANEL_BG}; border-top:1px solid {BORDER}; '
        f'padding:14px 28px; font-family:{FONT}; font-size:11px; '
        f'line-height:17px; color:{MUTED};">'
        f'본 메일은 주간 리포트 자동 생성 시스템에서 발송되었습니다.'
        f'</td></tr>'
    )

    preheader_div = (
        f'<div style="display:none; max-height:0; overflow:hidden; '
        f'font-size:1px; line-height:1px; color:{PAGE_BG}; mso-hide:all;">'
        f'{html.escape(preheader)}</div>'
    )

    body = f"""<body bgcolor="{PAGE_BG}" style="margin:0; padding:0; background-color:{PAGE_BG}; font-family:{FONT}; color:{INK};">
{preheader_div}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE_BG}" style="border-collapse:collapse; background-color:{PAGE_BG};">
  <tr>
    <td align="center" style="padding:24px 12px;">
      <table role="presentation" width="680" cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD_BG}" style="width:680px; border-collapse:collapse; background-color:{CARD_BG}; border:1px solid {BORDER};">
        {header_row}
        {body_rows}
        {footer_row}
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""
    return head + body


def convert(input_path: Path, output_path: Path) -> None:
    text = input_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    sections = split_sections(body)
    if not sections:
        print(f"warning: no '**N. Title**' sections found in {input_path}", file=sys.stderr)
    html_out = render_html(meta, sections)
    output_path.write_text(html_out, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Input markdown file (overview type)")
    ap.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output HTML file (default: <input>.html sibling)",
    )
    args = ap.parse_args()
    inp: Path = args.input
    if not inp.exists():
        print(f"error: file not found: {inp}", file=sys.stderr)
        return 2
    out: Path = args.output if args.output else inp.with_suffix(".html")
    convert(inp, out)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

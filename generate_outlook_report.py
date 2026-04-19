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

SECTION_RE = re.compile(r"^\*\*\s*(\d+)\.\s*([^*]+?)\*\*\s*$", re.MULTILINE)
BULLET_RE = re.compile(r"^-\s+(.*)")
BOLD_LABEL_RE = re.compile(r"^\*\*([^*]+)\*\*\s*(?:[:：]|[\u2013\u2014\-])?\s*(.*)$", re.DOTALL)
INLINE_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


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


SECTION_STYLES = {
    1: ("bullets", NAVY),
    2: ("teams", NAVY),
    3: ("boxes", YELLOW, YELLOW_BG, YELLOW_TEXT),
    4: ("boxes", RED, RED_BG, RED_TEXT),
}


def render_section(num: int, title: str, bullets: list[str]) -> str:
    style = SECTION_STYLES.get(num, ("bullets", NAVY))
    kind = style[0]
    accent = style[1]
    parts = [section_header(num, title, accent)]
    if kind == "bullets":
        parts.append(render_bullet_list(bullets))
    elif kind == "teams":
        parts.append(render_team_table(bullets))
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
    updated = str(meta.get("updated_at") or meta.get("created_at") or "")
    short_date = updated.split("T")[0] if updated else ""
    preheader = ""
    if sections:
        first_bullets = parse_bullets(sections[0][2])
        if first_bullets:
            preheader = first_bullets[0][:140]

    body_rows = "".join(
        render_section(num, title, parse_bullets(content))
        for num, title, content in sections
    )

    head = HEAD_TEMPLATE.format(title=html.escape(title_text))

    header_row = (
        f'<tr><td bgcolor="{NAVY}" width="680" '
        f'style="width:680px; padding:20px 28px; background-color:{NAVY}; font-family:{FONT};">'
        f'<div style="color:#ffffff; font-size:20px; font-weight:bold; line-height:26px;">'
        f'{html.escape(title_text)}</div>'
        f'<div style="color:{NAVY_MUTED}; font-size:12px; line-height:18px; padding-top:6px;">'
        f'Week {html.escape(week)} &middot; Overview'
        + (f' &middot; 발행 {html.escape(short_date)}' if short_date else "")
        + '</div></td></tr>'
    )

    footer_row = (
        f'<tr><td bgcolor="{PANEL_BG}" '
        f'style="background-color:{PANEL_BG}; border-top:1px solid {BORDER}; '
        f'padding:14px 28px; font-family:{FONT}; font-size:11px; '
        f'line-height:17px; color:{MUTED};">'
        f'Week {html.escape(week)} &middot; Overview Summary'
        + (f' &nbsp;|&nbsp; Generated {html.escape(short_date)}' if short_date else "")
        + '<br />본 메일은 주간 리포트 자동 생성 시스템에서 발송되었습니다.'
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

"""Convert monthly overview markdown to Outlook (Word 2016) compatible HTML.

Usage:
    python generate_monthly_report.py wiki/monthly/2026-04_월간요약.md
    python generate_monthly_report.py input.md -o output.html

월간 리포트는 6개 섹션:
  1. 그룹별 핵심 1줄 (DRAM PTE / NAND PTE / DRAM SRT / NAND SRT / 우시 PTE)
  2. 수율 주요내용
  3. 품질 주요내용
  4. 증산TF수율분과
  5. 개발제품수율 및 양산성
  6. 크로스팀이슈 (한달치)
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

from generate_outlook_report import (
    BORDER,
    CARD_BG,
    FONT,
    HEAD_TEMPLATE,
    INK,
    MUTED,
    NAVY,
    NAVY_MUTED,
    PAGE_BG,
    PANEL_BG,
    YELLOW,
    YELLOW_BG,
    YELLOW_TEXT,
    esc_inline,
    parse_bullets,
    parse_cross_team_groups,
    parse_frontmatter,
    render_boxes,
    render_cross_team_groups,
    section_header,
    split_labeled,
    split_sections,
)
from team_dict import teams_by_group

SECTION1_LABELS: list[str] = list(teams_by_group.keys())

SECTION2_CANONICAL = ["Spica수율", "HBM수율", "LC_CP수율", "Olympus수율", "CL_PE수율", "우시수율PTE"]
SECTION3_CANONICAL = ["DRAM품질PTE", "NAND품질PTE"]
SECTION4_CANONICAL = ["DRAM수율전략", "DRAM FA PTE", "NAND수율전략", "NAND FA PTE"]
SECTION5_CANONICAL = ["DRAM SRT 개발공정", "Heraion양산수율", "Procyon양산수율", "Robson양산수율"]

SECTION5_DISPLAY = {"DRAM SRT 개발공정": "DRAM SRT 개발공정(@HBM4E만)"}


def _all_team_dict_teams() -> set[str]:
    out: set[str] = set()
    for members in teams_by_group.values():
        out.update(members)
    return out


def _sanity_check_labels() -> None:
    """섹션 2~5 canonical 라벨 합이 team_dict 전체 팀과 정확히 일치하는지 확인."""
    section_union = set(SECTION2_CANONICAL) | set(SECTION3_CANONICAL) | set(SECTION4_CANONICAL) | set(SECTION5_CANONICAL)
    td = _all_team_dict_teams()
    missing = td - section_union
    extra = section_union - td
    if missing:
        print(f"warning: team_dict 팀이 섹션 2~5에 누락됨: {sorted(missing)}", file=sys.stderr)
    if extra:
        print(f"warning: 섹션 2~5에 team_dict에 없는 팀이 포함됨: {sorted(extra)}", file=sys.stderr)


_sanity_check_labels()

DASH = "—"


def _display_for(canonical: str) -> str:
    return SECTION5_DISPLAY.get(canonical, canonical)


def _render_minimal_rows(
    bullets: list[str],
    expected_canonical: list[str] | None,
    label_width_px: int,
    label_font_size: int,
    content_font_size: int,
    row_padding_y: int,
) -> str:
    """채움 배경 없는 미니멀 행 — 라벨(NAVY bold) + 내용 + 1px 하단 divider.

    임원 보고용: 시각 잡음 최소화, 가독성 최우선.
    """
    rows: list[str] = []
    seen: set[str] = set()

    def _row(label: str, content: str, missing: bool = False) -> str:
        label_color = NAVY if not missing else MUTED
        content_color = INK if not missing else MUTED
        content_extra = "" if not missing else " font-style:italic;"
        return (
            f'<tr>'
            f'<td width="{label_width_px}" valign="top" '
            f'style="width:{label_width_px}px; padding:{row_padding_y}px 12px {row_padding_y}px 0; '
            f'border-bottom:1px solid {BORDER}; '
            f'color:{label_color}; font-weight:700; font-size:{label_font_size}px; '
            f'line-height:{label_font_size + 5}px; word-break:keep-all;">{esc_inline(label)}</td>'
            f'<td valign="top" '
            f'style="padding:{row_padding_y}px 0; border-bottom:1px solid {BORDER}; '
            f'color:{content_color}; font-size:{content_font_size}px; '
            f'line-height:{content_font_size + 7}px;{content_extra}">{esc_inline(content)}</td>'
            f'</tr>'
        )

    for b in bullets:
        label, rest = split_labeled(b)
        if label:
            seen.add(label)
            rows.append(_row(_display_for(label), rest if rest else b))
        else:
            rows.append(_row(DASH, b))

    if expected_canonical:
        for canonical in expected_canonical:
            if canonical in seen:
                continue
            rows.append(_row(_display_for(canonical), "데이터 없음", missing=True))

    return (
        f'<tr><td style="padding:0 28px 16px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse; font-family:{FONT};">'
        f'{"".join(rows)}'
        f'</table></td></tr>'
    )


REPORT_WIDTH_PX = 1100
BADGE_WIDTH_PX = 170


def render_cross_team_groups_summary(groups: list[dict]) -> str:
    """월간 전용: 크로스팀 그룹을 '제목(+공동 대응 라벨) · 협업 팀 칩 · 종합'으로 렌더."""
    blocks: list[str] = []
    for ci, g in enumerate(groups):
        teams = g.get("related_teams") or []
        is_multi = len(teams) >= 2

        collab_label = ""
        if is_multi:
            collab_label = (
                f' <span style="font-weight:normal; font-size:11px; '
                f'color:{NAVY}; padding-left:8px;">'
                f'&middot; {len(teams)}팀 공동 대응</span>'
            )
        title_row = (
            f'<tr><td style="padding:6px 12px; background-color:{YELLOW_BG}; '
            f'border-left:3px solid {YELLOW}; color:{YELLOW_TEXT}; '
            f'font-weight:bold;">{esc_inline(g["title"])}{collab_label}</td></tr>'
        )

        chips_row = ""
        if is_multi:
            chips = "".join(
                f'<span style="display:inline-block; padding:2px 8px; '
                f'margin:2px 4px 2px 0; border:1px solid {BORDER}; color:{MUTED}; '
                f'font-size:11px; font-weight:normal; line-height:16px; '
                f'mso-padding-alt:0;">{esc_inline(t)}</span>'
                for t in teams
            )
            chips_row = (
                f'<tr><td style="padding:6px 12px; background-color:{CARD_BG}; '
                f'border-left:3px solid {YELLOW};">{chips}</td></tr>'
            )

        summary_row = ""
        if g.get("summary"):
            summary_row = (
                f'<tr><td style="padding:8px 12px; background-color:{PANEL_BG}; '
                f'border-left:3px solid {NAVY}; color:{INK};">'
                f'<b style="color:{NAVY};">종합 &middot; </b>'
                f'{esc_inline(g["summary"])}</td></tr>'
            )
        blocks.append(title_row + chips_row + summary_row)
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


def render_label_table(bullets: list[str], expected_canonical: list[str] | None = None) -> str:
    """섹션 2~5: NAVY filled 배지 + 흰 본문 (이전 스타일 유지)."""
    rows: list[str] = []
    seen: set[str] = set()

    for b in bullets:
        label, rest = split_labeled(b)
        canonical = label
        display = _display_for(canonical) if canonical else DASH
        if canonical:
            seen.add(canonical)
        badge_html = esc_inline(display) if display != DASH else DASH
        content_html = esc_inline(rest if rest else b)
        rows.append(
            f'<tr>'
            f'<td width="{BADGE_WIDTH_PX}" valign="top" bgcolor="{NAVY}" '
            f'style="width:{BADGE_WIDTH_PX}px; padding:10px 12px; background-color:{NAVY}; '
            f'color:#ffffff; font-weight:bold; border:1px solid {NAVY}; '
            f'word-break:keep-all; overflow-wrap:anywhere;">{badge_html}</td>'
            f'<td valign="top" bgcolor="{CARD_BG}" '
            f'style="padding:10px 12px; background-color:{CARD_BG}; '
            f'border:1px solid {BORDER}; color:{INK};">{content_html}</td>'
            f'</tr>'
        )

    if expected_canonical:
        for canonical in expected_canonical:
            if canonical in seen:
                continue
            display = _display_for(canonical)
            rows.append(
                f'<tr>'
                f'<td width="{BADGE_WIDTH_PX}" valign="top" bgcolor="{PANEL_BG}" '
                f'style="width:{BADGE_WIDTH_PX}px; padding:10px 12px; background-color:{PANEL_BG}; '
                f'color:{MUTED}; font-weight:bold; border:1px solid {BORDER}; '
                f'word-break:keep-all; overflow-wrap:anywhere;">{esc_inline(display)}</td>'
                f'<td valign="top" bgcolor="{PANEL_BG}" '
                f'style="padding:10px 12px; background-color:{PANEL_BG}; '
                f'border:1px solid {BORDER}; color:{MUTED}; font-style:italic;">데이터 없음</td>'
                f'</tr>'
            )

    return (
        f'<tr><td style="padding:4px 28px 16px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse; font-family:{FONT}; font-size:13px; line-height:19px;">'
        f'{"".join(rows)}'
        f'</table></td></tr>'
    )


def render_group_callouts(bullets: list[str], expected_canonical: list[str] | None = None) -> str:
    """섹션 1: 임원 보고용 미니멀 (라벨/내용 같은 행, 채움 배경 X, 1px divider)."""
    return _render_minimal_rows(
        bullets, expected_canonical,
        label_width_px=120, label_font_size=15, content_font_size=15, row_padding_y=14,
    )


MONTHLY_SECTION_STYLES = {
    1: ("group_callouts", NAVY, SECTION1_LABELS),
    2: ("label_table", NAVY, SECTION2_CANONICAL),
    3: ("label_table", NAVY, SECTION3_CANONICAL),
    4: ("label_table", NAVY, SECTION4_CANONICAL),
    5: ("label_table", NAVY, SECTION5_CANONICAL),
    6: ("cross_groups", YELLOW, YELLOW_BG, YELLOW_TEXT),
}


def render_section(num: int, title: str, content: str) -> str:
    style = MONTHLY_SECTION_STYLES.get(num, ("label_table", NAVY, []))
    kind = style[0]
    accent = style[1]

    if kind == "cross_groups":
        groups = parse_cross_team_groups(content)
        if groups:
            return section_header(num, title, accent) + render_cross_team_groups_summary(groups)
        bullets = parse_bullets(content)
        return (
            section_header(num, title, accent)
            + render_boxes(bullets, style[1], style[2], style[3])
        )

    bullets = parse_bullets(content)
    expected = style[2] if len(style) >= 3 else None

    if kind == "group_callouts":
        return section_header(num, title, accent) + render_group_callouts(bullets, expected)

    return section_header(num, title, accent) + render_label_table(bullets, expected)


def render_html_monthly(
    meta: dict,
    sections: list[tuple[int, str, str]],
    *,
    dashboard_html: str | None = None,
) -> str:
    title_text = str(meta.get("title", "월간 요약"))
    month = str(meta.get("month", ""))
    updated = str(meta.get("updated_at") or meta.get("created_at") or "")
    short_date = updated.split("T")[0] if updated else ""
    preheader = ""
    if sections:
        first_bullets = parse_bullets(sections[0][2])
        if first_bullets:
            preheader = first_bullets[0][:140]

    body_rows = "".join(
        render_section(num, title, content)
        for num, title, content in sections
    )
    if dashboard_html:
        body_rows = body_rows + dashboard_html

    head = HEAD_TEMPLATE.format(title=html.escape(title_text))

    header_row = (
        f'<tr><td bgcolor="{NAVY}" width="{REPORT_WIDTH_PX}" '
        f'style="width:{REPORT_WIDTH_PX}px; padding:20px 28px; background-color:{NAVY}; font-family:{FONT};">'
        f'<div style="color:#ffffff; font-size:20px; font-weight:bold; line-height:26px;">'
        f'{html.escape(title_text)}</div>'
        f'<div style="color:{NAVY_MUTED}; font-size:12px; line-height:18px; padding-top:6px;">'
        f'Month {html.escape(month)} &middot; Monthly Overview'
        + (f' &middot; 발행 {html.escape(short_date)}' if short_date else "")
        + '</div></td></tr>'
    )

    footer_row = (
        f'<tr><td bgcolor="{PANEL_BG}" '
        f'style="background-color:{PANEL_BG}; border-top:1px solid {BORDER}; '
        f'padding:14px 28px; font-family:{FONT}; font-size:11px; '
        f'line-height:17px; color:{MUTED};">'
        f'Month {html.escape(month)} &middot; Monthly Overview Summary'
        + (f' &nbsp;|&nbsp; Generated {html.escape(short_date)}' if short_date else "")
        + '<br />본 메일은 월간 리포트 자동 생성 시스템에서 발송되었습니다.'
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
      <table role="presentation" width="{REPORT_WIDTH_PX}" cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD_BG}" style="width:{REPORT_WIDTH_PX}px; border-collapse:collapse; background-color:{CARD_BG}; border:1px solid {BORDER};">
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


CHART_MODES = ("chartjs", "svg", "outlook_html", "outlook_png")


def _suffixed(path: Path, suffix: str) -> Path:
    """``foo.html`` + ``chartjs`` → ``foo_chartjs.html``."""
    return path.with_name(f"{path.stem}_{suffix}{path.suffix}")


def convert(input_path: Path, output_path: Path, *, charts: str = "none") -> None:
    text = input_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    sections = split_sections(body)
    if not sections:
        print(f"warning: no '**N. Title**' sections found in {input_path}", file=sys.stderr)

    dashboard_html: str | None = None
    if charts != "none":
        from monthly_dashboard import render_dashboard
        dashboard_html = render_dashboard(kind=charts)

    html_out = render_html_monthly(meta, sections, dashboard_html=dashboard_html)
    output_path.write_text(html_out, encoding="utf-8")
    print(f"written: {output_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Input markdown file (monthly type)")
    ap.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output HTML file (default: <input>.html sibling)",
    )
    ap.add_argument(
        "--charts",
        choices=("none", *CHART_MODES, "all"),
        default="none",
        help="Append a dashboard cards section. 'all' generates 4 sibling files.",
    )
    args = ap.parse_args()
    inp: Path = args.input
    if not inp.exists():
        print(f"error: file not found: {inp}", file=sys.stderr)
        return 2

    base_out: Path = args.output if args.output else inp.with_suffix(".html")

    if args.charts == "all":
        for mode in CHART_MODES:
            convert(inp, _suffixed(base_out, mode), charts=mode)
    elif args.charts == "none":
        convert(inp, base_out, charts="none")
    else:
        convert(inp, _suffixed(base_out, args.charts), charts=args.charts)
    return 0


if __name__ == "__main__":
    sys.exit(main())

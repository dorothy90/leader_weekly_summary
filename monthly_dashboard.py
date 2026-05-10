"""Dashboard cards section (섹션 7) for the monthly report.

Generates 5 owners × 4 topics = 20 cards with target/actual time series for
March~September. Renders the same data in 4 modes:

- ``chartjs``     : Chart.js CDN, interactive line+bar (browsers only)
- ``svg``         : Inline SVG, line+bar (browsers + modern email)
- ``outlook_html``: HTML <table> + bgcolor cells, grouped bars
                    (Outlook 2016+ Word engine compatible; no JS/SVG)
- ``outlook_png`` : matplotlib-rendered PNG, base64 inline
                    (Outlook 2016+ compatible; matplotlib required)

Data shape mirrors the standalone prototype at ``output/dashboard/dummy_data.js``.
The same LCG (seed=42, multiplier=1103515245, increment=12345, mask=0x7fffffff)
is used; values may diverge from the JS prototype in late steps because JS
arithmetic loses precision past 2**53, but the structure (40 entries, 20 keys)
and the first key are identical.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from generate_outlook_report import (
    BORDER,
    CARD_BG,
    FONT,
    INK,
    MUTED,
    NAVY,
    esc_inline,
    section_header,
)

OWNERS = ["SYLD", "KMHJ", "PJWS", "CHWN", "LJSY"]
TOPICS = [
    {"구분": "문제 해결력 : E1Q 표준", "구분2": "E1Q 표준"},
    {"구분": "고객 만족 : 서비스 품질", "구분2": "서비스 품질"},
    {"구분": "협업 역량 : 부서간 소통", "구분2": "부서간 소통"},
    {"구분": "실행력 : 납기 준수", "구분2": "납기 준수"},
]
MONTHS = ["3월", "4월", "5월", "6월", "7월", "8월", "9월"]

GOAL_COLOR = "#9ca3af"
COLOR_MAP = {
    "green": "#10b981",
    "red": "#ef4444",
    "amber": "#f59e0b",
    "null": "#9ca3af",
}
DEFAULT_BAR_COLOR = "#3b82f6"

CARD_GRID_COLS = 4


class _LCG:
    def __init__(self, seed: int = 42) -> None:
        self._seed = seed

    def rnd(self) -> float:
        self._seed = (self._seed * 1103515245 + 12345) & 0x7FFFFFFF
        return self._seed / 0x7FFFFFFF

    def rint(self, lo: int, hi: int) -> int:
        return int(self.rnd() * (hi - lo + 1)) + lo


def _make_key(owner: str, gubun: str) -> str:
    no_colon = gubun.replace(":", "")
    norm = "-".join(part for part in no_colon.split() if part)
    return f"editableDiv{owner}_{norm}"


def generate_dummy_data() -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Return ``(api1, api2)`` — 20 cards × (목표+실적) = 40 entries, 20 API1 keys."""
    lcg = _LCG(42)
    api2: list[dict[str, Any]] = []
    api1: dict[str, str] = {}
    num = 1

    for oi, owner in enumerate(OWNERS):
        for ti, topic in enumerate(TOPICS):
            card_idx = oi * len(TOPICS) + ti
            goal_base = 55 + ti * 4
            goal = {m: str(goal_base + mi * 3) for mi, m in enumerate(MONTHS)}

            is_missing_sep = card_idx in (7, 13)
            is_missing_last = card_idx == 11

            actual: dict[str, str] = {}
            for mi, m in enumerate(MONTHS):
                if mi == 6 and is_missing_sep:
                    actual[m] = ""
                    continue
                base = int(goal[m])
                delta = lcg.rint(-12, 14)
                actual[m] = str(max(0, base + delta))

            goal_last = float(goal["8월"])
            act_last: float | None
            act_last = None if is_missing_last else float(actual["8월"])

            if act_last is None:
                month_color = "null"
            elif act_last >= goal_last:
                month_color = "green"
            elif act_last >= goal_last - 4:
                month_color = "amber"
            else:
                month_color = "red"

            base_meta = {
                "num": str(num),
                "type": "6",
                "담당자": owner,
                "구분": topic["구분"],
                "구분2": topic["구분2"],
            }
            api2.append({
                **base_meta, "category": "목표", **goal,
                "-": ".", "W16": None,
                "last_month_result": goal_last, "last_week_result": None,
                "month_color": "null", "week_color": "null",
            })
            num += 1
            api2.append({
                **{**base_meta, "num": str(num)}, "category": "실적", **actual,
                "-": ".", "W16": None,
                "last_month_result": act_last, "last_week_result": None,
                "month_color": month_color, "week_color": "null",
            })
            num += 1

            trend_text = {
                "green": "목표 대비 양호",
                "amber": "소폭 미달",
                "red": "목표 미달",
                "null": "8월 데이터 미수집",
            }[month_color]
            last_txt = "—" if act_last is None else f"{act_last:.0f}"
            api1[_make_key(owner, topic["구분"])] = (
                f"<p><b>{owner}</b> · {topic['구분2']} &mdash; "
                f"8월 실적 <b>{last_txt}</b> / 목표 {goal_last:.0f} "
                f"({trend_text}). 9월 목표 {goal['9월']}.</p>"
            )

    return api1, api2


def _to_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bar_color(card: dict) -> str:
    actual = card.get("actual") or {}
    return COLOR_MAP.get(actual.get("month_color"), DEFAULT_BAR_COLOR)


def _group_cards(api2: list[dict]) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for row in api2:
        k = (row["담당자"], row["구분"])
        if k not in by_key:
            by_key[k] = {
                "담당자": row["담당자"],
                "구분": row["구분"],
                "구분2": row["구분2"],
                "goal": None,
                "actual": None,
            }
        if row["category"] == "목표":
            by_key[k]["goal"] = row
        elif row["category"] == "실적":
            by_key[k]["actual"] = row
    return list(by_key.values())


def _wrap_card(card: dict, *, chart_html: str, text_html: str) -> str:
    """Card outer container — table-based for Outlook compat."""
    title = esc_inline(card["구분"])
    owner = esc_inline(card["담당자"])
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{CARD_BG}" '
        f'style="border-collapse:collapse; background-color:{CARD_BG}; border:1px solid {BORDER}; '
        f'table-layout:fixed; width:100%;">'
        f'<tr><td valign="top" style="padding:12px; font-family:{FONT}; word-break:keep-all; overflow-wrap:anywhere;">'
        f'<div style="font-size:13px; font-weight:bold; color:{INK}; line-height:1.35; word-break:keep-all;">{title}</div>'
        f'<div style="font-size:11px; color:{MUTED}; padding-top:2px;">담당자 {owner}</div>'
        f'<div style="padding:10px 0 4px; min-width:0;">{chart_html}</div>'
        f'<div style="border-top:1px solid #e5e7eb; margin-top:6px; padding-top:6px; '
        f'font-size:11px; color:#4b5563; line-height:1.5; word-break:keep-all; overflow-wrap:anywhere;">{text_html}</div>'
        f'</td></tr></table>'
    )


def _card_text(card: dict, api1: dict[str, str]) -> str:
    return api1.get(
        _make_key(card["담당자"], card["구분"]),
        '<p style="color:#9ca3af; margin:0;">데이터 없음</p>',
    )


# ---------- chartjs --------------------------------------------------------


def _render_card_chartjs(card: dict, idx: int, api1: dict[str, str]) -> str:
    chart_html = (
        f'<div style="position:relative; width:100%; max-width:100%; height:160px; '
        f'overflow:hidden; box-sizing:border-box;">'
        f'<canvas id="dash_chart_{idx}" style="max-width:100%; max-height:100%; '
        f'display:block;"></canvas></div>'
    )
    return _wrap_card(card, chart_html=chart_html, text_html=_card_text(card, api1))


def _chartjs_trailer(cards: list[dict]) -> str:
    payload = []
    for idx, card in enumerate(cards):
        goal = card.get("goal") or {}
        actual = card.get("actual") or {}
        payload.append({
            "idx": idx,
            "label": card["구분2"],
            "months": MONTHS,
            "goal": [_to_num(goal.get(m)) for m in MONTHS],
            "actual": [_to_num(actual.get(m)) for m in MONTHS],
            "barColor": _bar_color(card),
        })
    data_json = json.dumps(payload, ensure_ascii=False)
    return (
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>'
        f'<script>window.DASHBOARD_CARDS = {data_json};</script>'
        '<script>(function(){'
        'var cards=window.DASHBOARD_CARDS||[];'
        f'var GOAL="{GOAL_COLOR}";'
        'cards.forEach(function(c){'
        'var ctx=document.getElementById("dash_chart_"+c.idx);if(!ctx)return;'
        'new Chart(ctx,{data:{labels:c.months,datasets:['
        '{type:"bar",label:c.label,data:c.actual,backgroundColor:c.barColor,'
        'borderRadius:2,barPercentage:0.6,categoryPercentage:0.7,order:2},'
        '{type:"line",label:"목표",data:c.goal,borderColor:GOAL,backgroundColor:GOAL,'
        'borderDash:[4,4],borderWidth:1.5,pointRadius:0,tension:0,spanGaps:false,order:1}'
        ']},options:{responsive:true,maintainAspectRatio:false,animation:false,'
        'plugins:{legend:{position:"bottom",labels:{font:{size:10},boxWidth:10,boxHeight:10,padding:6}},'
        'tooltip:{enabled:true}},'
        'scales:{x:{grid:{display:false},ticks:{font:{size:10}}},'
        'y:{grid:{color:"#f3f4f6"},ticks:{font:{size:10}}}}}});'
        '});})();</script>'
    )


# ---------- svg ------------------------------------------------------------


def _render_card_svg(card: dict, api1: dict[str, str]) -> str:
    width, height = 240, 160
    pad_l, pad_r, pad_t, pad_b = 30, 8, 8, 24
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b

    goal_data = [_to_num((card.get("goal") or {}).get(m)) for m in MONTHS]
    actual_data = [_to_num((card.get("actual") or {}).get(m)) for m in MONTHS]
    present = [v for v in goal_data + actual_data if v is not None]

    if not present:
        svg = (
            f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
            f'style="width:100%; height:auto; display:block;"></svg>'
        )
    else:
        dmin, dmax = min(present), max(present)
        span = (dmax - dmin) or 1.0
        ymin = dmin - span * 0.12
        ymax = dmax + span * 0.12
        yrange = ymax - ymin

        def x_at(i: int) -> float:
            return pad_l + inner_w * i / (len(MONTHS) - 1)

        def y_at(v: float) -> float:
            return pad_t + inner_h - inner_h * (v - ymin) / yrange

        bar_color = _bar_color(card)
        slot = inner_w / len(MONTHS)
        bar_w = slot * 0.5

        y_ticks: list[str] = []
        for t in range(3):
            yv = ymin + (ymax - ymin) * t / 2
            yp = y_at(yv)
            y_ticks.append(
                f'<line x1="{pad_l}" y1="{yp:.1f}" x2="{width - pad_r}" y2="{yp:.1f}" '
                f'stroke="#f3f4f6" stroke-width="1"/>'
                f'<text x="{pad_l - 4}" y="{yp + 3:.1f}" text-anchor="end" '
                f'font-size="9" fill="#6b7280">{round(yv)}</text>'
            )

        bars: list[str] = []
        for i, v in enumerate(actual_data):
            if v is None:
                continue
            yp = y_at(v)
            bh = (pad_t + inner_h) - yp
            bars.append(
                f'<rect x="{x_at(i) - bar_w / 2:.1f}" y="{yp:.1f}" '
                f'width="{bar_w:.1f}" height="{bh:.1f}" fill="{bar_color}" rx="1"/>'
            )

        path_parts: list[str] = []
        started = False
        for i, v in enumerate(goal_data):
            if v is None:
                started = False
                continue
            cmd = " L " if started else " M "
            path_parts.append(f"{cmd}{x_at(i):.1f} {y_at(v):.1f}")
            started = True
        path = "".join(path_parts).strip()

        x_labels = "".join(
            f'<text x="{x_at(i):.1f}" y="{height - pad_b + 14}" '
            f'text-anchor="middle" font-size="9" fill="#6b7280">{m}</text>'
            for i, m in enumerate(MONTHS)
        )

        svg = (
            f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
            f'style="width:100%; height:auto; display:block;">'
            + "".join(y_ticks)
            + "".join(bars)
            + f'<path d="{path}" stroke="{GOAL_COLOR}" stroke-width="1.5" fill="none" '
            f'stroke-dasharray="4 4"/>'
            + x_labels
            + "</svg>"
        )

    legend = _svg_legend(card)
    return _wrap_card(card, chart_html=svg + legend, text_html=_card_text(card, api1))


def _svg_legend(card: dict) -> str:
    bar_color = _bar_color(card)
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" '
        f'style="margin-top:4px; font-family:{FONT}; font-size:10px; color:#4b5563;">'
        f'<tr>'
        f'<td><table cellpadding="0" cellspacing="0"><tr>'
        f'<td bgcolor="{bar_color}" width="10" height="10" '
        f'style="background-color:{bar_color}; font-size:1px; line-height:1px;">&nbsp;</td>'
        f'<td style="padding:0 10px 0 4px;">{esc_inline(card["구분2"])}</td>'
        f'<td style="padding:0 4px 0 0;">'
        f'<span style="display:inline-block; width:14px; border-top:1.5px dashed {GOAL_COLOR}; '
        f'vertical-align:middle;">&nbsp;</span></td>'
        f'<td>목표</td>'
        f'</tr></table></td>'
        f'</tr></table>'
    )


# ---------- outlook_html (group bars) -------------------------------------


def _render_card_outlook_html(card: dict, api1: dict[str, str]) -> str:
    chart_h = 100
    bar_w = 9
    gap = 2

    goal_data = [_to_num((card.get("goal") or {}).get(m)) for m in MONTHS]
    actual_data = [_to_num((card.get("actual") or {}).get(m)) for m in MONTHS]
    present = [v for v in goal_data + actual_data if v is not None]

    if present:
        dmin, dmax = min(present), max(present)
        span = (dmax - dmin) or 1.0
        ymin = max(0.0, dmin - span * 0.12)
        ymax = dmax + span * 0.12
        yrange = (ymax - ymin) or 1.0

        def bar_h(v: float | None) -> int:
            if v is None:
                return 0
            return max(1, int(chart_h * (v - ymin) / yrange))
    else:
        def bar_h(v: float | None) -> int:
            return 0

    bar_color = _bar_color(card)
    chart_cells: list[str] = []
    label_cells: list[str] = []

    def _bar_block(color: str, h: int) -> str:
        if h <= 0:
            return f'<td valign="bottom" width="{bar_w}" style="height:{chart_h}px;">&nbsp;</td>'
        return (
            f'<td valign="bottom" style="height:{chart_h}px;">'
            f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{color}" width="{bar_w}" height="{h}" '
            f'style="background-color:{color}; line-height:1px; font-size:1px; min-width:{bar_w}px;">&nbsp;</td>'
            f'</tr></table></td>'
        )

    for i, m in enumerate(MONTHS):
        gh = bar_h(goal_data[i])
        ah = bar_h(actual_data[i])
        chart_cells.append(
            f'<td valign="bottom" align="center" style="padding:0 4px;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
            f'<tr>{_bar_block(GOAL_COLOR, gh)}'
            f'<td width="{gap}">&nbsp;</td>'
            f'{_bar_block(bar_color, ah)}</tr>'
            f'</table></td>'
        )
        label_cells.append(
            f'<td align="center" style="font-size:9px; color:#6b7280; padding:3px 0 0 0;">{m}</td>'
        )

    chart_table = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" '
        f'style="border-collapse:collapse;">'
        f'<tr>{"".join(chart_cells)}</tr>'
        f'<tr>{"".join(label_cells)}</tr>'
        f'</table>'
    )

    legend = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" '
        f'style="margin-top:6px; font-family:{FONT}; font-size:10px; color:#4b5563;">'
        f'<tr>'
        f'<td bgcolor="{GOAL_COLOR}" width="10" height="10" '
        f'style="background-color:{GOAL_COLOR}; font-size:1px; line-height:1px;">&nbsp;</td>'
        f'<td style="padding:0 10px 0 4px;">목표</td>'
        f'<td bgcolor="{bar_color}" width="10" height="10" '
        f'style="background-color:{bar_color}; font-size:1px; line-height:1px;">&nbsp;</td>'
        f'<td style="padding:0 0 0 4px;">{esc_inline(card["구분2"])}</td>'
        f'</tr></table>'
    )

    return _wrap_card(
        card, chart_html=chart_table + legend, text_html=_card_text(card, api1)
    )


# ---------- outlook_png (matplotlib) --------------------------------------


_MPL_FONT_SET = False


def _ensure_matplotlib_korean_font() -> None:
    """Pick the first available Korean font once."""
    global _MPL_FONT_SET
    if _MPL_FONT_SET:
        return
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    candidates = [
        "AppleGothic", "Apple SD Gothic Neo", "Malgun Gothic", "맑은 고딕",
        "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR",
    ]
    chosen = next((c for c in candidates if c in available), None)
    if chosen:
        plt.rcParams["font.family"] = chosen
    plt.rcParams["axes.unicode_minus"] = False
    _MPL_FONT_SET = True


def _render_card_outlook_png(card: dict, api1: dict[str, str]) -> str:
    _ensure_matplotlib_korean_font()
    import matplotlib.pyplot as plt

    goal_data = [_to_num((card.get("goal") or {}).get(m)) for m in MONTHS]
    actual_data = [_to_num((card.get("actual") or {}).get(m)) for m in MONTHS]
    bar_color = _bar_color(card)

    fig, ax = plt.subplots(figsize=(2.6, 1.5), dpi=160)
    fig.patch.set_facecolor("white")

    xs = list(range(len(MONTHS)))

    bars_x = [xi for xi, v in zip(xs, actual_data) if v is not None]
    bars_y = [v for v in actual_data if v is not None]
    if bars_x:
        ax.bar(bars_x, bars_y, width=0.5, color=bar_color, label=card["구분2"], zorder=2)

    segments: list[tuple[list[int], list[float]]] = []
    cur_x: list[int] = []
    cur_y: list[float] = []
    for xi, v in zip(xs, goal_data):
        if v is None:
            if cur_x:
                segments.append((cur_x, cur_y))
                cur_x, cur_y = [], []
        else:
            cur_x.append(xi)
            cur_y.append(v)
    if cur_x:
        segments.append((cur_x, cur_y))

    label_used = False
    for sx, sy in segments:
        ax.plot(
            sx, sy,
            color=GOAL_COLOR, linestyle="--", linewidth=1.3,
            label=(None if label_used else "목표"),
            zorder=3,
        )
        label_used = True

    ax.set_xticks(xs)
    ax.set_xticklabels(MONTHS, fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("bottom", "left"):
        ax.spines[spine].set_color("#e5e7eb")
    ax.grid(axis="y", color="#f3f4f6", linewidth=0.7, zorder=1)
    ax.set_axisbelow(True)
    if bars_x or label_used:
        ax.legend(fontsize=7, loc="lower right", frameon=False)

    fig.tight_layout(pad=0.3)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")

    chart_html = (
        f'<img src="data:image/png;base64,{b64}" '
        f'alt="{esc_inline(card["구분"])}" width="240" '
        f'style="display:block; max-width:100%; height:auto;">'
    )
    return _wrap_card(card, chart_html=chart_html, text_html=_card_text(card, api1))


# ---------- top-level ------------------------------------------------------


_RENDERERS = {
    "chartjs": lambda card, idx, api1: _render_card_chartjs(card, idx, api1),
    "svg": lambda card, idx, api1: _render_card_svg(card, api1),
    "outlook_html": lambda card, idx, api1: _render_card_outlook_html(card, api1),
    "outlook_png": lambda card, idx, api1: _render_card_outlook_png(card, api1),
}


def render_dashboard(*, kind: str) -> str:
    """Return ``<tr>`` rows for the dashboard section (header + grid + trailer)."""
    if kind not in _RENDERERS:
        raise ValueError(f"unknown chart kind: {kind!r}")

    api1, api2 = generate_dummy_data()
    cards = _group_cards(api2)
    render = _RENDERERS[kind]
    card_htmls = [render(card, idx, api1) for idx, card in enumerate(cards)]

    if kind in ("chartjs", "svg"):
        items = "".join(f'<div style="min-width:0;">{c}</div>' for c in card_htmls)
        grid = (
            '<div style="display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); '
            'gap:16px; box-sizing:border-box;">'
            + items
            + "</div>"
        )
    else:
        rows: list[str] = []
        for r in range(0, len(card_htmls), CARD_GRID_COLS):
            cells = card_htmls[r : r + CARD_GRID_COLS]
            while len(cells) < CARD_GRID_COLS:
                cells.append("&nbsp;")
            cell_html = "".join(
                f'<td valign="top" width="25%" style="width:25%; padding:8px; overflow:hidden;">{c}</td>'
                for c in cells
            )
            rows.append(f"<tr>{cell_html}</tr>")
        grid = (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse; table-layout:fixed;">'
            + "".join(rows)
            + "</table>"
        )

    trailer = _chartjs_trailer(cards) if kind == "chartjs" else ""

    section_body = (
        f'<tr><td style="padding:4px 28px 16px 28px;">{grid}{trailer}</td></tr>'
    )

    return section_header(7, "성과지표 카드 (담당자별)", NAVY) + section_body

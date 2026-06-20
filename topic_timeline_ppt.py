"""주제별 타임라인 → PPTX 생성.

generate_topic_timeline() 의 구조화 결과(week_summaries)를 받아 시간순
프레젠테이션을 만든다. deep_mining_ppt 의 디자인 테마/슬라이드 dict 스키마/
렌더러(create_native_pptx)를 그대로 재사용해 deep mining 덱과 동일한 룩을 유지한다.

슬라이드 구성:
  1) 타이틀 — 주제 / 기간 / 주차·문서 수
  2) 개요 — 전체 추이 요약(Executive Summary 스타일)
  3+) 타임라인 — [주차 | 핵심 내용 | 관련 팀] 테이블, 시간순. 주차가 많으면 분할.

렌더러(create_native_pptx)는 외부 패키지 pptdaddy 에 의존한다(deep mining과 동일).
패키지가 없는 환경에서는 build_topic_timeline_pptx() 호출 시 ImportError 가 난다.
slide_def 생성부(build_slide_defs)는 pptdaddy 없이도 동작하도록 분리해 두었다.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# deep_mining_ppt 의 디자인 토대 재사용 (THEME / 레이아웃 상수 / 헤더 헬퍼).
# create_native_pptx 는 무거운 외부 의존(pptdaddy)이라 함수 내부에서 lazy import.
from deep_mining_ppt import (
    THEME,
    MARGIN,
    SLIDE_W,
    SLIDE_H,
    CONTENT_W,
    _make_slide_header,
)

# 타임라인 테이블 1슬라이드당 최대 주차 행 수 (셀이 멀티라인이라 보수적으로)
MAX_WEEKS_PER_SLIDE = 5
# 셀 1개에 담을 불릿 최대 개수 / 길이
MAX_BULLETS_PER_CELL = 4
MAX_CHARS_PER_CELL = 200


def _bullets_to_cell(bullets_md: str) -> str:
    """'- a\\n- b' 형태의 마크다운 불릿 → 테이블 셀용 멀티라인 텍스트."""
    lines = []
    for raw in (bullets_md or "").split("\n"):
        s = raw.strip()
        if not s:
            continue
        s = s.lstrip("-").lstrip("•").strip()
        if s:
            lines.append(f"• {s}")
        if len(lines) >= MAX_BULLETS_PER_CELL:
            break
    text = "\n".join(lines)
    if len(text) > MAX_CHARS_PER_CELL:
        text = text[: MAX_CHARS_PER_CELL - 1].rstrip() + "…"
    return text or "-"


def _title_slide(topic: str, meta: Dict) -> Dict:
    """타이틀 슬라이드 (dark)."""
    span = meta.get("span", "-")
    covered = meta.get("weeks_covered", 0)
    total = meta.get("weeks_total", 0)
    docs = meta.get("document_count", 0)
    sub = f"{span}  |  관련 {covered}/{total}주  |  {docs}건 분석"
    return {
        "background": {"color": THEME["bg_dark"]},
        "elements": [
            {
                "type": "shape",
                "shape_type": "rectangle",
                "position": {"left": 0, "top": 0, "width": SLIDE_W, "height": SLIDE_H},
                "style": {"fill_color": THEME["bg_dark"]},
            },
            {
                "type": "text_box",
                "content": "주제별 타임라인",
                "position": {"left": 1.0, "top": 1.3, "width": 8.0, "height": 0.7},
                "style": {
                    "font_size": THEME["font_subtitle"],
                    "font_color": THEME["text_muted"],
                    "alignment": "center",
                },
            },
            {
                "type": "text_box",
                "content": topic,
                "position": {"left": 1.0, "top": 2.0, "width": 8.0, "height": 1.2},
                "style": {
                    "font_size": THEME["font_title"],
                    "font_bold": True,
                    "font_color": THEME["text_primary"],
                    "alignment": "center",
                },
            },
            {
                "type": "shape",
                "shape_type": "rectangle",
                "position": {"left": 3.5, "top": 3.3, "width": 3.0, "height": 0.06},
                "style": {"fill_color": THEME["accent"]},
            },
            {
                "type": "text_box",
                "content": sub,
                "position": {"left": 1.0, "top": 3.6, "width": 8.0, "height": 0.8},
                "style": {
                    "font_size": THEME["font_subtitle"],
                    "font_color": THEME["text_muted"],
                    "alignment": "center",
                },
            },
        ],
    }


def _overview_slide(overview: str) -> Dict:
    """개요 슬라이드 (전체 추이). 긴 텍스트는 문장 단위 불릿으로 분할."""
    text = overview or "-"
    if len(text) > 200:
        content = [s.strip() + ("." if not s.strip().endswith(".") else "")
                   for s in text.split(". ") if s.strip()]
    else:
        content = text
    return {
        "background": {"color": THEME["bg_light"]},
        "elements": [
            *_make_slide_header("개요 — 전체 추이"),
            {
                "type": "text_box",
                "content": content,
                "position": {"left": MARGIN, "top": 1.6, "width": CONTENT_W, "height": 3.4},
                "style": {
                    "font_size": THEME["font_body"],
                    "font_color": THEME["text_dark"],
                    "line_spacing": 6,
                },
            },
        ],
    }


def _timeline_slides(week_summaries: List[Dict]) -> List[Dict]:
    """주차별 요약 → 시간순 타임라인 테이블 슬라이드(들). 주차 많으면 분할."""
    slides: List[Dict] = []
    chunks = [
        week_summaries[i : i + MAX_WEEKS_PER_SLIDE]
        for i in range(0, len(week_summaries), MAX_WEEKS_PER_SLIDE)
    ]
    multi = len(chunks) > 1
    for idx, chunk in enumerate(chunks, 1):
        title = "타임라인 (시간순)"
        if multi:
            title += f"  ({idx}/{len(chunks)})"
        table_data = [["주차", "핵심 내용", "관련 팀"]]
        for ws in chunk:
            teams = ", ".join(ws.get("teams", [])) or "-"
            table_data.append([ws["week"], _bullets_to_cell(ws.get("bullets", "")), teams])
        slides.append({
            "background": {"color": THEME["bg_light"]},
            "elements": [
                *_make_slide_header(title),
                {
                    "type": "table",
                    "data": table_data,
                    "position": {"left": MARGIN, "top": 1.6, "width": CONTENT_W, "height": 3.6},
                    "style": {
                        "header_color": THEME["header_bg"],
                        "header_font_color": THEME["header_font"],
                        "font_size": THEME["font_small"],
                    },
                },
            ],
        })
    return slides


def build_slide_defs(
    topic: str,
    overview: str,
    week_summaries: List[Dict],
    meta: Dict,
) -> List[Dict]:
    """create_native_pptx() 용 slide definition dict 리스트 생성 (렌더링 없음, 테스트 용이)."""
    slides = [_title_slide(topic, meta), _overview_slide(overview)]
    if week_summaries:
        slides.extend(_timeline_slides(week_summaries))
    else:
        slides.append({
            "background": {"color": THEME["bg_light"]},
            "elements": [
                *_make_slide_header("타임라인"),
                {
                    "type": "text_box",
                    "content": "관련 내용이 있는 주차가 없습니다.",
                    "position": {"left": MARGIN, "top": 2.0, "width": CONTENT_W, "height": 1.0},
                    "style": {"font_size": THEME["font_body"], "font_color": THEME["text_muted"]},
                },
            ],
        })
    return slides


def build_topic_timeline_pptx(result: Dict, output_path: str) -> str:
    """generate_topic_timeline() 결과 dict → PPTX 파일 생성.

    Args:
        result: generate_topic_timeline() 반환 dict
                (topic, overview, week_summaries, weeks_total, weeks_covered,
                 document_count 포함).
        output_path: 출력 PPTX 경로.

    Returns:
        생성된 PPTX 파일 경로.
    """
    week_summaries: List[Dict] = result.get("week_summaries", [])
    span = (
        f"{week_summaries[0]['week']} ~ {week_summaries[-1]['week']}"
        if week_summaries else "-"
    )
    meta = {
        "span": span,
        "weeks_total": result.get("weeks_total", 0),
        "weeks_covered": result.get("weeks_covered", len(week_summaries)),
        "document_count": result.get("document_count", 0),
    }

    slide_defs = build_slide_defs(
        result.get("topic", ""), result.get("overview", ""), week_summaries, meta
    )

    # 무거운 외부 의존(pptdaddy)은 여기서 lazy import
    from pptdaddy.utils.export_native import create_native_pptx

    return create_native_pptx(
        slides_data=slide_defs,
        output_file=output_path,
        presentation_title=f"{result.get('topic', '')} 주제별 타임라인",
    )

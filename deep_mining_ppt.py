"""
Deep Mining PPT 생성 — 카드 기반 레이아웃 + 렌더
6단 아키텍처 중 5~6단: Layout(card packing + fit check) → Render(create_native_pptx)
"""

import logging
from dataclasses import dataclass, field
from typing import List, Any, Dict, Optional
from pathlib import Path

from pptdaddy.utils.export_native import create_native_pptx
from deep_mining_schemas import (
    DeepMiningAnalysis,
    PresentationOutline,
    TeamFindings,
    TopicFindings,
    MetricItem,
    TrendSignal,
)

logger = logging.getLogger(__name__)

# ========== 디자인 테마 상수 ==========

THEME = {
    "bg_dark": "#1a1a2e",
    "bg_light": "#ffffff",
    "bg_section": "#f7f8fa",
    "accent": "#e94560",
    "text_primary": "#ffffff",
    "text_dark": "#2d3748",
    "text_muted": "#718096",
    "header_bg": "#2d3748",
    "header_font": "#ffffff",
    "font_title": 36,
    "font_subtitle": 20,
    "font_body": 16,
    "font_small": 12,
    "font_metric": 28,
}

# 슬라이드 usable area (10x5.625 inches, margin 0.8)
MARGIN = 0.8
SLIDE_W = 10.0
SLIDE_H = 5.625
CONTENT_W = SLIDE_W - 2 * MARGIN  # 8.4
CONTENT_H = SLIDE_H - 2 * MARGIN  # 4.025

# Fit 제한
MAX_BULLETS_PER_SLIDE = 6
MAX_TABLE_ROWS = 10
MAX_CHARS_PER_BULLET = 60


# ========== ContentCard ==========

@dataclass
class ContentCard:
    """레이아웃 packing 단위"""
    card_type: str          # "title" | "summary" | "team" | "topic" | "metric" | "trend" | "recommendation" | "appendix"
    title: str
    content: Any            # 카드 타입별 데이터
    importance: str = "medium"  # "high" | "medium" | "low"
    estimated_space: float = 0.5  # 슬라이드 내 차지 비율 (0.0~1.0)
    must_include: bool = False
    mergeable: bool = True
    evidence_refs: List[str] = field(default_factory=list)


# ========== Step 1: 분석 결과 → atomic cards ==========

def decompose_to_cards(
    analysis: DeepMiningAnalysis,
    outline: Optional[PresentationOutline] = None,
) -> List[ContentCard]:
    """DeepMiningAnalysis를 atomic ContentCard 리스트로 분해"""
    cards: List[ContentCard] = []

    # 타이틀 카드 (필수)
    cards.append(ContentCard(
        card_type="title",
        title="Deep Mining Report",
        content={
            "week_range": analysis.week_range,
            "document_count": analysis.document_count,
            "team_count": len(analysis.findings_by_team),
        },
        importance="high",
        estimated_space=1.0,
        must_include=True,
        mergeable=False,
    ))

    # Executive Summary 카드 (필수)
    cards.append(ContentCard(
        card_type="summary",
        title="Executive Summary",
        content=analysis.executive_summary,
        importance="high",
        estimated_space=1.0,
        must_include=True,
        mergeable=False,
    ))

    # 팀별 카드
    for tf in analysis.findings_by_team:
        cards.append(ContentCard(
            card_type="team",
            title=tf.headline,
            content=tf,
            importance="high" if tf.risks else "medium",
            estimated_space=0.5 if len(tf.key_bullets) <= 3 else 1.0,
            must_include=False,
            mergeable=True,
            evidence_refs=tf.evidence_refs,
        ))

    # 주제별 카드
    for tp in analysis.findings_by_topic:
        cards.append(ContentCard(
            card_type="topic",
            title=tp.headline,
            content=tp,
            importance="medium",
            estimated_space=0.5,
            must_include=False,
            mergeable=True,
            evidence_refs=tp.evidence_refs,
        ))

    # 트렌드 카드
    if analysis.trends:
        cards.append(ContentCard(
            card_type="trend",
            title="Trends",
            content=analysis.trends,
            importance="medium",
            estimated_space=1.0,
            must_include=False,
            mergeable=False,
        ))

    # 핵심 수치 카드
    if analysis.key_metrics:
        cards.append(ContentCard(
            card_type="metric",
            title="Key Metrics",
            content=analysis.key_metrics,
            importance="medium",
            estimated_space=1.0,
            must_include=False,
            mergeable=False,
        ))

    # 권고사항 카드 (필수)
    if analysis.recommendations:
        cards.append(ContentCard(
            card_type="recommendation",
            title="Recommendations",
            content=analysis.recommendations,
            importance="high",
            estimated_space=1.0,
            must_include=True,
            mergeable=False,
        ))

    # Appendix (coverage)
    if outline and outline.appendix_needed:
        cards.append(ContentCard(
            card_type="appendix",
            title="Appendix: Data Coverage",
            content=analysis.coverage,
            importance="low",
            estimated_space=1.0,
            must_include=False,
            mergeable=False,
        ))

    return cards


# ========== Step 2: 카드 packing ==========

IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def pack_cards(cards: List[ContentCard], num_slides: int) -> List[List[ContentCard]]:
    """num_slides 예산 내에서 카드를 슬라이드에 배치

    Returns:
        List[List[ContentCard]] — 각 inner list가 한 슬라이드의 카드들
    """
    # 필수 카드 먼저 분리
    must = [c for c in cards if c.must_include]
    optional = sorted(
        [c for c in cards if not c.must_include],
        key=lambda c: (IMPORTANCE_ORDER.get(c.importance, 1), -c.estimated_space),
    )

    slides: List[List[ContentCard]] = []

    # 필수 카드는 각각 개별 슬라이드 (full-width)
    for card in must:
        slides.append([card])

    # 남은 슬롯에 optional 카드 배치
    remaining_slots = max(0, num_slides - len(slides))

    if remaining_slots == 0 and optional:
        # 슬라이드가 부족하면 마지막 필수 슬라이드에 합칠 수 있는 것들 병합
        for card in optional:
            if card.mergeable and slides:
                # 가장 여유 있는 슬라이드에 병합 시도
                for s in slides:
                    total_space = sum(c.estimated_space for c in s)
                    if total_space + card.estimated_space <= 1.5:
                        s.append(card)
                        break
    else:
        # optional 카드를 슬롯에 배치
        current_slide: List[ContentCard] = []
        current_space = 0.0

        for card in optional:
            if remaining_slots <= 0:
                break

            if card.estimated_space >= 1.0 or not card.mergeable:
                # full-width 카드 → 새 슬라이드
                if current_slide:
                    slides.append(current_slide)
                    current_slide = []
                    current_space = 0.0
                    remaining_slots -= 1

                if remaining_slots > 0:
                    slides.append([card])
                    remaining_slots -= 1
            else:
                # half-width 카드 → 현재 슬라이드에 같은 타입만 병합
                current_type = current_slide[0].card_type if current_slide else None
                can_merge = (
                    current_space + card.estimated_space <= 1.2
                    and (current_type is None or current_type == card.card_type)
                )
                if can_merge:
                    current_slide.append(card)
                    current_space += card.estimated_space
                else:
                    if current_slide:
                        slides.append(current_slide)
                        remaining_slots -= 1
                    current_slide = [card]
                    current_space = card.estimated_space

        if current_slide and remaining_slots > 0:
            slides.append(current_slide)

    return slides


# ========== Step 3: Fit check ==========

def _truncate_bullets(bullets: List[str], max_count: int = MAX_BULLETS_PER_SLIDE) -> List[str]:
    """bullet 수 제한 + 문자 수 제한"""
    result = []
    for b in bullets[:max_count]:
        if len(b) > MAX_CHARS_PER_BULLET:
            result.append(b[:MAX_CHARS_PER_BULLET - 3] + "...")
        else:
            result.append(b)
    if len(bullets) > max_count:
        result.append(f"외 {len(bullets) - max_count}건...")
    return result


def _truncate_table(data: List[List[str]], max_rows: int = MAX_TABLE_ROWS) -> List[List[str]]:
    """테이블 행 수 제한"""
    if len(data) <= max_rows:
        return data
    header = data[:1]
    body = data[1:max_rows]
    footer = [[f"외 {len(data) - max_rows}행...", *["" for _ in range(len(data[0]) - 1)]]]
    return header + body + footer


# ========== Step 4: 카드 → slide definition ==========

def _build_title_slide(card: ContentCard) -> Dict:
    """타이틀 슬라이드"""
    info = card.content
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
                "content": "Deep Mining Report",
                "position": {"left": 1.5, "top": 1.5, "width": 7.0, "height": 1.2},
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
                "position": {"left": 3.5, "top": 2.8, "width": 3.0, "height": 0.06},
                "style": {"fill_color": THEME["accent"]},
            },
            {
                "type": "text_box",
                "content": f"{info['week_range']}  |  {info['document_count']}건 분석  |  {info['team_count']}팀",
                "position": {"left": 1.5, "top": 3.2, "width": 7.0, "height": 0.8},
                "style": {
                    "font_size": THEME["font_subtitle"],
                    "font_color": THEME["text_muted"],
                    "alignment": "center",
                },
            },
        ],
    }


def _build_summary_slide(card: ContentCard) -> Dict:
    """Executive Summary 슬라이드"""
    summary_text = card.content
    # 긴 요약은 bullet로 분할
    if isinstance(summary_text, str) and len(summary_text) > 200:
        sentences = [s.strip() for s in summary_text.split(". ") if s.strip()]
        bullets = _truncate_bullets(sentences)
        content = bullets
    else:
        content = str(summary_text)

    return {
        "background": {"color": THEME["bg_light"]},
        "elements": [
            {
                "type": "text_box",
                "content": "Executive Summary",
                "position": {"left": MARGIN, "top": 0.5, "width": CONTENT_W, "height": 0.8},
                "style": {
                    "font_size": THEME["font_subtitle"] + 4,
                    "font_bold": True,
                    "font_color": THEME["text_dark"],
                },
            },
            {
                "type": "shape",
                "shape_type": "rectangle",
                "position": {"left": MARGIN, "top": 1.3, "width": 1.5, "height": 0.06},
                "style": {"fill_color": THEME["accent"]},
            },
            {
                "type": "text_box",
                "content": content,
                "position": {"left": MARGIN, "top": 1.6, "width": CONTENT_W, "height": 3.2},
                "style": {
                    "font_size": THEME["font_body"],
                    "font_color": THEME["text_dark"],
                    "line_spacing": 6,
                },
            },
        ],
    }


def _build_team_slide(cards: List[ContentCard]) -> Dict:
    """팀별 분석 슬라이드 (1~2개 팀 카드 병합 가능)"""
    elements = [
        {
            "type": "text_box",
            "content": "팀별 핵심 분석",
            "position": {"left": MARGIN, "top": 0.5, "width": CONTENT_W, "height": 0.8},
            "style": {
                "font_size": THEME["font_subtitle"] + 4,
                "font_bold": True,
                "font_color": THEME["text_dark"],
            },
        },
        {
            "type": "shape",
            "shape_type": "rectangle",
            "position": {"left": MARGIN, "top": 1.3, "width": 1.5, "height": 0.06},
            "style": {"fill_color": THEME["accent"]},
        },
    ]

    if len(cards) == 1:
        tf: TeamFindings = cards[0].content
        bullets = _truncate_bullets(tf.key_bullets)

        elements.append({
            "type": "text_box",
            "content": f"{tf.team}: {tf.headline}",
            "position": {"left": MARGIN, "top": 1.5, "width": CONTENT_W, "height": 0.6},
            "style": {
                "font_size": THEME["font_subtitle"],
                "font_bold": True,
                "font_color": THEME["accent"],
            },
        })
        elements.append({
            "type": "text_box",
            "content": bullets,
            "position": {"left": MARGIN, "top": 2.1, "width": CONTENT_W, "height": 2.5},
            "style": {
                "font_size": THEME["font_body"],
                "font_color": THEME["text_dark"],
                "line_spacing": 4,
            },
        })

        # 수치가 있으면 테이블 추가
        if tf.metrics:
            table_data = [["지표", "값", "팀"]]
            for m in tf.metrics[:5]:
                table_data.append([m.name, m.display_value, m.team])
            table_data = _truncate_table(table_data)
            elements.append({
                "type": "table",
                "data": table_data,
                "position": {"left": MARGIN, "top": 3.5, "width": CONTENT_W, "height": 1.5},
                "style": {
                    "header_color": THEME["header_bg"],
                    "header_font_color": THEME["header_font"],
                    "font_size": THEME["font_small"],
                },
            })
    else:
        # 여러 팀 카드를 테이블로 합본
        table_data = [["팀", "핵심 이슈", "주요 포인트"]]
        for card in cards:
            tf: TeamFindings = card.content
            top_bullet = tf.key_bullets[0] if tf.key_bullets else "-"
            if len(top_bullet) > 40:
                top_bullet = top_bullet[:37] + "..."
            table_data.append([tf.team, tf.headline, top_bullet])
        table_data = _truncate_table(table_data)

        elements.append({
            "type": "table",
            "data": table_data,
            "position": {"left": MARGIN, "top": 1.6, "width": CONTENT_W, "height": 3.2},
            "style": {
                "header_color": THEME["header_bg"],
                "header_font_color": THEME["header_font"],
                "font_size": 14,
            },
        })

    return {"background": {"color": THEME["bg_light"]}, "elements": elements}


def _build_topic_slide(cards: List[ContentCard]) -> Dict:
    """주제별 분석 슬라이드"""
    elements = [
        {
            "type": "text_box",
            "content": "주제별 분석",
            "position": {"left": MARGIN, "top": 0.5, "width": CONTENT_W, "height": 0.8},
            "style": {
                "font_size": THEME["font_subtitle"] + 4,
                "font_bold": True,
                "font_color": THEME["text_dark"],
            },
        },
        {
            "type": "shape",
            "shape_type": "rectangle",
            "position": {"left": MARGIN, "top": 1.3, "width": 1.5, "height": 0.06},
            "style": {"fill_color": THEME["accent"]},
        },
    ]

    table_data = [["주제", "핵심 포인트"]]
    for card in cards:
        tp: TopicFindings = card.content
        top_bullet = tp.key_bullets[0] if tp.key_bullets else "-"
        if len(top_bullet) > 50:
            top_bullet = top_bullet[:47] + "..."
        table_data.append([tp.headline, top_bullet])
    table_data = _truncate_table(table_data)

    elements.append({
        "type": "table",
        "data": table_data,
        "position": {"left": MARGIN, "top": 1.6, "width": CONTENT_W, "height": 3.2},
        "style": {
            "header_color": THEME["header_bg"],
            "header_font_color": THEME["header_font"],
            "font_size": 14,
        },
    })

    return {"background": {"color": THEME["bg_light"]}, "elements": elements}


def _build_trend_slide(card: ContentCard) -> Dict:
    """트렌드 슬라이드"""
    trends: List[TrendSignal] = card.content
    direction_icon = {"improving": "[+]", "declining": "[-]", "stable": "[=]"}

    bullets = []
    for t in trends[:MAX_BULLETS_PER_SLIDE]:
        icon = direction_icon.get(t.direction, "")
        text = f"{icon} {t.description}"
        if len(text) > MAX_CHARS_PER_BULLET:
            text = text[:MAX_CHARS_PER_BULLET - 3] + "..."
        bullets.append(text)

    return {
        "background": {"color": THEME["bg_light"]},
        "elements": [
            {
                "type": "text_box",
                "content": "Trends",
                "position": {"left": MARGIN, "top": 0.5, "width": CONTENT_W, "height": 0.8},
                "style": {
                    "font_size": THEME["font_subtitle"] + 4,
                    "font_bold": True,
                    "font_color": THEME["text_dark"],
                },
            },
            {
                "type": "shape",
                "shape_type": "rectangle",
                "position": {"left": MARGIN, "top": 1.3, "width": 1.5, "height": 0.06},
                "style": {"fill_color": THEME["accent"]},
            },
            {
                "type": "text_box",
                "content": bullets,
                "position": {"left": MARGIN, "top": 1.6, "width": CONTENT_W, "height": 3.2},
                "style": {
                    "font_size": THEME["font_body"],
                    "font_color": THEME["text_dark"],
                    "line_spacing": 6,
                },
            },
        ],
    }


def _build_metric_slide(card: ContentCard) -> Dict:
    """핵심 수치 슬라이드"""
    metrics: List[MetricItem] = card.content
    table_data = [["지표", "값", "팀", "주차"]]
    for m in metrics:
        table_data.append([m.name, m.display_value, m.team, m.week])
    table_data = _truncate_table(table_data)

    return {
        "background": {"color": THEME["bg_light"]},
        "elements": [
            {
                "type": "text_box",
                "content": "Key Metrics",
                "position": {"left": MARGIN, "top": 0.5, "width": CONTENT_W, "height": 0.8},
                "style": {
                    "font_size": THEME["font_subtitle"] + 4,
                    "font_bold": True,
                    "font_color": THEME["text_dark"],
                },
            },
            {
                "type": "shape",
                "shape_type": "rectangle",
                "position": {"left": MARGIN, "top": 1.3, "width": 1.5, "height": 0.06},
                "style": {"fill_color": THEME["accent"]},
            },
            {
                "type": "table",
                "data": table_data,
                "position": {"left": MARGIN, "top": 1.6, "width": CONTENT_W, "height": 3.2},
                "style": {
                    "header_color": THEME["header_bg"],
                    "header_font_color": THEME["header_font"],
                    "font_size": 14,
                },
            },
        ],
    }


def _build_recommendation_slide(card: ContentCard) -> Dict:
    """권고사항 슬라이드"""
    recs: List[str] = card.content
    bullets = _truncate_bullets(recs)

    return {
        "background": {"color": THEME["bg_light"]},
        "elements": [
            {
                "type": "text_box",
                "content": "Recommendations",
                "position": {"left": MARGIN, "top": 0.5, "width": CONTENT_W, "height": 0.8},
                "style": {
                    "font_size": THEME["font_subtitle"] + 4,
                    "font_bold": True,
                    "font_color": THEME["text_dark"],
                },
            },
            {
                "type": "shape",
                "shape_type": "rectangle",
                "position": {"left": MARGIN, "top": 1.3, "width": 1.5, "height": 0.06},
                "style": {"fill_color": THEME["accent"]},
            },
            {
                "type": "text_box",
                "content": bullets,
                "position": {"left": MARGIN, "top": 1.6, "width": CONTENT_W, "height": 3.2},
                "style": {
                    "font_size": THEME["font_body"],
                    "font_color": THEME["text_dark"],
                    "line_spacing": 6,
                },
            },
        ],
    }


def _build_appendix_slide(card: ContentCard) -> Dict:
    """Appendix 슬라이드 — coverage metadata"""
    coverage = card.content
    lines = [
        f"총 문서 수: {coverage.total_documents}",
        f"분석 기간: {coverage.week_range}",
        f"배치 수: {coverage.batch_count}",
    ]
    if coverage.documents_by_team:
        for team, count in coverage.documents_by_team.items():
            lines.append(f"  {team}: {count}건")
    if coverage.excluded_count > 0:
        lines.append(f"제외 문서: {coverage.excluded_count}건 ({coverage.excluded_reason or 'N/A'})")

    return {
        "background": {"color": THEME["bg_section"]},
        "elements": [
            {
                "type": "text_box",
                "content": "Appendix: Data Coverage",
                "position": {"left": MARGIN, "top": 0.5, "width": CONTENT_W, "height": 0.8},
                "style": {
                    "font_size": THEME["font_subtitle"],
                    "font_bold": True,
                    "font_color": THEME["text_dark"],
                },
            },
            {
                "type": "text_box",
                "content": lines,
                "position": {"left": MARGIN, "top": 1.4, "width": CONTENT_W, "height": 3.5},
                "style": {
                    "font_size": THEME["font_small"],
                    "font_color": THEME["text_muted"],
                    "line_spacing": 4,
                },
            },
        ],
    }


def cards_to_slide_defs(packed: List[List[ContentCard]]) -> List[Dict]:
    """packed 카드 리스트 → create_native_pptx()용 slide definition dict 리스트"""
    slide_defs = []

    for slide_cards in packed:
        if not slide_cards:
            continue

        primary = slide_cards[0]

        if primary.card_type == "title":
            slide_defs.append(_build_title_slide(primary))
        elif primary.card_type == "summary":
            slide_defs.append(_build_summary_slide(primary))
        elif primary.card_type == "team":
            slide_defs.append(_build_team_slide(slide_cards))
        elif primary.card_type == "topic":
            slide_defs.append(_build_topic_slide(slide_cards))
        elif primary.card_type == "trend":
            slide_defs.append(_build_trend_slide(primary))
        elif primary.card_type == "metric":
            slide_defs.append(_build_metric_slide(primary))
        elif primary.card_type == "recommendation":
            slide_defs.append(_build_recommendation_slide(primary))
        elif primary.card_type == "appendix":
            slide_defs.append(_build_appendix_slide(primary))
        else:
            logger.warning(f"Unknown card type: {primary.card_type}")

    return slide_defs


# ========== 전체 파이프라인 ==========

def build_deep_mining_pptx(
    analysis: DeepMiningAnalysis,
    outline: Optional[PresentationOutline],
    num_slides: int,
    output_path: str,
) -> str:
    """Deep Mining 분석 결과 → PPTX 파일 생성

    Args:
        analysis: Reduce 노드 출력
        outline: LLM이 생성한 프레젠테이션 개요 (optional)
        num_slides: 사용자 지정 슬라이드 수
        output_path: 출력 PPTX 경로

    Returns:
        생성된 PPTX 파일 경로
    """
    # Step 1: 분해
    cards = decompose_to_cards(analysis, outline)
    logger.info(f"Decomposed into {len(cards)} cards")

    # Step 2: Packing
    packed = pack_cards(cards, num_slides)
    logger.info(f"Packed into {len(packed)} slides (requested: {num_slides})")

    # Step 3: Slide defs
    slide_defs = cards_to_slide_defs(packed)

    # Step 4: Render
    result_path = create_native_pptx(
        slides_data=slide_defs,
        output_file=output_path,
        presentation_title="Deep Mining Report",
    )

    return result_path

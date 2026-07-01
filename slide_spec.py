"""분석 결과(analysis) + 개요(outline) → 아티팩트형 슬라이드 스펙 JSON.

콘텐츠↔코드 분리: 이 모듈은 '무엇을 담을지'(스펙)만 만든다. '어떻게 그릴지'(좌표/렌더)는
ppt_render/render_deck.js(pptxgenjs)가 담당한다.

LLM 플래너(사내 디자인 규칙 주입) + 파싱 실패 시 결정적 폴백.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DESIGN_RULES = (ROOT / "pptx_design_rules.md")

VALID_LAYOUTS = {
    "cover", "section", "kpi_callout", "icon_rows", "two_col",
    "timeline", "chart", "table", "takeaways",
}
ICON_NAMES = ["chart-line", "trending-up", "trending-down", "alert", "check",
              "target", "gear", "flask", "clock", "layers", "activity", "info"]

LAYOUT_CATALOG = """
사용 가능한 layout과 슬롯(JSON):
- cover:      {"layout":"cover","eyebrow":str,"title":str,"subtitle":str}
- section:    {"layout":"section","index":"01","title":str}
- kpi_callout:{"layout":"kpi_callout","title":str,"cards":[{"value":"98.5%","label":str,"delta":"+3.2pp","direction":"up|down|flat","cite":"[YYYY-WW/팀]"}]}  // 카드 2~4개
- icon_rows:  {"layout":"icon_rows","title":str,"rows":[{"icon":"<아이콘>","heading":str,"body":str,"cite":str}]}  // 3~5행
- two_col:    {"layout":"two_col","title":str,"left":{"heading":str,"bullets":[str],"cite":str},"right":{...}}
- timeline:   {"layout":"timeline","title":str,"items":[{"week":"2026-03","text":str,"cite":str}]}  // 3~5개
- chart:      {"layout":"chart","title":str,"chartType":"line|bar","series":[{"name":str,"labels":[str],"values":[num]}],"note":str}
- table:      {"layout":"table","title":str,"headers":[str],"rows":[[str]],"note":str}  // 최대 8행
- takeaways:  {"layout":"takeaways","title":str,"bullets":[str]}  // 다크, 3~6개
아이콘: __ICONS__
""".replace("__ICONS__", ", ".join(ICON_NAMES))


def _load_rules() -> str:
    try:
        return DESIGN_RULES.read_text(encoding="utf-8")
    except Exception:
        return "임원 톤, 아티팩트형(카드/아이콘/차트), 제목 밑 강조선 금지, 본문 좌측정렬."


def _llm() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY", ""),
        base_url=os.getenv("OPENROUTER_BASE_URL", "") or None,
    )


def _system_prompt(target_slides: int = 8) -> str:
    return (
        "당신은 반도체 수율·품질 조직의 전문 프레젠테이션 디자이너입니다.\n"
        "주어진 분석 결과(analysis)와 개요(outline)를 편집가능한 아티팩트형 슬라이드 스펙 JSON으로 설계하세요.\n"
        "좌표/폰트/색은 절대 넣지 마세요(렌더러가 처리). layout과 콘텐츠 슬롯만.\n\n"
        "[디자인 규칙]\n" + _load_rules() + "\n\n" + LAYOUT_CATALOG + "\n"
        "[분량·구성 — 반드시 준수]\n"
        f"- 총 {target_slides}장 내외로 충실히 구성(표지·종합 포함). 데이터를 과소 활용하지 말 것.\n"
        "- 주요 파트 앞에 section 구분 슬라이드를 넣어 흐름을 만들 것(예: 01 개요, 02 이슈, 03 종합).\n"
        "- **레이아웃을 반복하지 말고** kpi_callout·chart·icon_rows·two_col·timeline·table을 고루 섞을 것.\n"
        "[매핑 지침]\n"
        "- 1장: cover(다룬 주차 구간·문서수를 subtitle에).\n"
        "- key_metrics는 kpi_callout(카드 2~4개, display_value→value, direction은 trend/부호로).\n"
        "- **수치 value가 있는 key_metrics나 trends가 있으면 chart 슬라이드(line/bar)를 반드시 1장 이상** 만들 것"
        "(labels/values는 주차·수치로 구성, 근거 범위 내에서).\n"
        "- findings_by_team이 2팀 이상이면 two_col(팀 비교)로, 이슈 원인-대응은 icon_rows로.\n"
        "- 주차별 전개가 있으면 timeline. **각 항목의 week는 evidence_refs의 서로 다른 주차**를 쓰고 중복 금지.\n"
        "- findings_by_topic은 별도 icon_rows/two_col로 심층 1장.\n"
        "- recommendations는 마지막 takeaways(다크).\n"
        "- outline.slides가 있으면 그 흐름/제목을 존중하고 preferred_visual을 layout으로 매핑"
        "(metric-strip→kpi_callout, table→table, 2-column→two_col, bullet→icon_rows/two_col).\n"
        "- 각 항목 끝 cite는 evidence_refs/주차·팀에서 [YYYY-WW/팀]으로 축약.\n"
        "- 레이아웃을 반복하지 말고 섞으세요. 슬라이드당 핵심 5~6개 이내.\n\n"
        '출력은 {"meta":{...},"slides":[...]} JSON 하나만. 설명/코드펜스 금지.'
    )


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    # 첫 { 부터 마지막 } 까지
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1:
        return None
    try:
        return json.loads(text[i:j + 1])
    except Exception:
        return None


def _ensure_chart(spec: dict, analysis: dict) -> dict:
    """LLM이 차트를 빠뜨렸는데 수치 시계열이 있으면 차트 슬라이드를 보장 삽입."""
    slides = spec.get("slides", [])
    if any(s.get("layout") == "chart" for s in slides):
        return spec
    pts = [(m.get("week", ""), m.get("value"), m.get("name", ""))
           for m in analysis.get("key_metrics", []) if isinstance(m.get("value"), (int, float))]
    pts = [p for p in pts if p[0]]  # 주차 있는 것만
    if len(pts) < 2:
        return spec
    pts.sort(key=lambda p: p[0])
    chart = {
        "layout": "chart", "title": "수치 추이",
        "chartType": "line" if len({p[0] for p in pts}) == len(pts) else "bar",
        "series": [{"name": "값", "labels": [p[0] for p in pts[:8]], "values": [p[1] for p in pts[:8]]}],
        "note": f"[{analysis.get('week_range', '')}] 근거 범위 내 수치",
    }
    # 첫 kpi_callout 뒤(없으면 cover 뒤)에 삽입
    idx = next((i for i, s in enumerate(slides) if s.get("layout") == "kpi_callout"), 0)
    slides.insert(idx + 1, chart)
    spec["slides"] = slides
    return spec


def _normalize(spec: dict, meta: dict) -> dict:
    """LLM 출력 방어적 정규화 — 유효 layout만, 카운트 클램프, meta 보강."""
    out_meta = {**meta, **(spec.get("meta") or {})}
    slides: List[dict] = []
    for s in spec.get("slides", []):
        if not isinstance(s, dict):
            continue
        layout = s.get("layout")
        if layout not in VALID_LAYOUTS:
            continue
        if layout == "kpi_callout":
            s["cards"] = (s.get("cards") or [])[:4]
        elif layout == "icon_rows":
            for r in (s.get("rows") or []):
                if r.get("icon") not in ICON_NAMES:
                    r["icon"] = "info"
            s["rows"] = (s.get("rows") or [])[:5]
        elif layout == "timeline":
            s["items"] = (s.get("items") or [])[:5]
        elif layout == "table":
            s["rows"] = (s.get("rows") or [])[:8]
        elif layout == "takeaways":
            s["bullets"] = (s.get("bullets") or [])[:6]
        slides.append(s)
    if not slides:
        raise ValueError("정규화 후 유효 슬라이드 없음")
    return {"meta": out_meta, "slides": slides}


# ---------- 결정적 폴백 ----------

def _cite(refs: List[str]) -> str:
    return f"[{refs[0]}]" if refs else ""


def _fallback_spec(analysis: dict, outline: Optional[dict], meta: dict) -> dict:
    a = analysis or {}
    week_range = a.get("week_range", "")
    doc_n = a.get("document_count", 0)
    slides: List[dict] = [{
        "layout": "cover",
        "eyebrow": "주제 종합 보고",
        "title": meta.get("title", "주제 보고서"),
        "subtitle": f"{week_range} · 전 주차 전수 분석 · 문서 {doc_n}건".strip(" ·"),
    }]

    metrics = a.get("key_metrics", [])[:4]
    if metrics:
        slides.append({
            "layout": "kpi_callout", "title": "핵심 지표",
            "cards": [{
                "value": m.get("display_value") or (str(m.get("value")) if m.get("value") is not None else ""),
                "label": m.get("name", ""),
                "cite": _cite([f"{m.get('week','')}/{m.get('team','')}".strip("/")]),
            } for m in metrics],
        })

    numeric = [m for m in a.get("key_metrics", []) if isinstance(m.get("value"), (int, float))]
    if len(numeric) >= 2:
        slides.append({
            "layout": "chart", "title": "핵심 수치 비교", "chartType": "bar",
            "series": [{"name": "값", "labels": [m.get("name", "")[:12] for m in numeric[:6]],
                        "values": [m.get("value") for m in numeric[:6]]}],
            "note": f"[{week_range}] 근거 범위 내 수치",
        })

    trends = a.get("trends", [])
    if trends:
        slides.append({
            "layout": "icon_rows", "title": "추이 신호",
            "rows": [{
                "icon": "trending-up" if t.get("direction") == "improving" else ("trending-down" if t.get("direction") == "declining" else "activity"),
                "heading": (", ".join(t.get("teams", [])) or "전체")[:20],
                "body": t.get("description", ""),
                "cite": _cite(t.get("evidence_refs", [])),
            } for t in trends[:5]],
        })

    teams = a.get("findings_by_team", [])
    for i in range(0, min(len(teams), 4), 2):
        pair = teams[i:i + 2]
        slide = {"layout": "two_col", "title": "팀별 현황"}
        for key, tf in zip(["left", "right"], pair):
            slide[key] = {
                "heading": tf.get("team", ""),
                "bullets": tf.get("key_bullets", [])[:6],
                "cite": _cite(tf.get("evidence_refs", [])),
            }
        slides.append(slide)

    topics = a.get("findings_by_topic", [])
    if topics:
        slides.append({
            "layout": "icon_rows", "title": "주제별 심층",
            "rows": [{
                "icon": "layers", "heading": tp.get("headline", tp.get("topic", ""))[:24],
                "body": " · ".join(tp.get("key_bullets", [])[:2]),
                "cite": _cite(tp.get("evidence_refs", [])),
            } for tp in topics[:5]],
        })

    recs = a.get("recommendations", [])
    slides.append({
        "layout": "takeaways", "title": "종합 및 시사점",
        "bullets": (recs or [a.get("executive_summary", "")])[:6],
    })
    return {"meta": meta, "slides": slides}


def build_slide_spec(
    analysis: dict,
    outline: Optional[dict],
    meta: Optional[dict] = None,
    *,
    use_llm: bool = True,
    target_slides: int = 8,
) -> dict:
    """analysis(+outline) → 슬라이드 스펙 dict. LLM 실패 시 결정적 폴백."""
    meta = meta or {}
    meta.setdefault("title", (outline or {}).get("title") or analysis.get("week_range", "주제 보고서"))

    if use_llm and os.getenv("OPENROUTER_API_KEY"):
        user = (
            f"다음 분석 결과와 개요로 슬라이드 스펙 JSON을 설계하세요. 총 {target_slides}장 내외.\n\n"
            f"[meta]\n{json.dumps(meta, ensure_ascii=False)}\n\n"
            f"[analysis]\n{json.dumps(analysis, ensure_ascii=False)[:12000]}\n\n"
            f"[outline]\n{json.dumps(outline, ensure_ascii=False)[:4000] if outline else '(없음)'}\n"
        )
        try:
            resp = _llm().chat.completions.create(
                model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "system", "content": _system_prompt(target_slides)},
                          {"role": "user", "content": user}],
                temperature=0.2, max_tokens=8000,
            )
            parsed = _extract_json(resp.choices[0].message.content or "")
            if parsed:
                return _ensure_chart(_normalize(parsed, meta), analysis)
            logger.warning("[slide_spec] LLM JSON 파싱 실패 — 폴백")
        except Exception as e:
            logger.warning(f"[slide_spec] LLM 실패({e}) — 폴백")

    return _fallback_spec(analysis, outline, meta)

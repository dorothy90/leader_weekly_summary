"""주제 기반 전(全)주차 Deep-Research 보고서 생성기.

사용자가 자연어 주제를 주면, 누적된 모든 주차의 원본 메일(weekly_mail 인덱스)을
deep-research 패턴(질의 fan-out → hybrid 검색 → 관련성 검증 → 합성 → grounding 검증)
으로 종합해 하나의 풍부한 한국어 보고서(md + Outlook 호환 HTML)로 만든다.

사용법:
    python generate_topic_report.py "HBM 수율 이슈 추이"
    python generate_topic_report.py "Procyon 양산성 점검" --subqueries 6 --limit 10 --send
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

from openai import OpenAI

import embed_vectordb
from wiki_builder import (
    LLM_MODEL,
    MAX_CHARS_PER_CALL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    _slugify_topic,
)
from generate_outlook_report import (
    BORDER,
    CARD_BG,
    FONT,
    HEAD_TEMPLATE,
    INK,
    MUTED,
    NAVY,
    PAGE_BG,
    PANEL_BG,
    esc_inline,
)
from topic_report_prompts import (
    GROUNDING_SYSTEM,
    QUERY_EXPANSION_SYSTEM,
    RELEVANCE_FILTER_SYSTEM,
    REPORT_SYSTEM,
    SLIDE_HTML_SYSTEM,
    SLIDE_PLAN_SYSTEM,
    WEEK_COMPRESS_SYSTEM,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "wiki" / "topic"


# ========== LLM 헬퍼 ==========
def _llm() -> OpenAI:
    return OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def _chat(system: str, user: str, *, max_tokens: int = 4000, temperature: float = 0.2) -> str:
    resp = _llm().chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def _parse_json_list(text: str) -> list:
    """LLM 출력에서 첫 JSON 배열을 추출. 실패 시 [] 반환."""
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        return []


# ========== 1. 질의 확장 (fan-out) ==========
def expand_queries(topic: str, n: int) -> List[str]:
    # glm-4.7 은 reasoning 모델 — 추론이 토큰을 소비하므로 작은 구조화 출력도 넉넉히.
    raw = _chat(QUERY_EXPANSION_SYSTEM, f"보고 주제: {topic}\n\n{n}개 내외의 질의로 확장하세요.",
                max_tokens=2500, temperature=0.3)
    subs = [str(q).strip() for q in _parse_json_list(raw) if str(q).strip()]
    # 원 주제는 항상 포함, 중복 제거(순서 보존)
    queries = [topic] + subs
    seen: set = set()
    uniq = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[: n + 1]


# ========== 2. 전 주차 hybrid 검색 ==========
def list_indexed_weeks() -> List[str]:
    if not DATA_DIR.is_dir():
        return []
    return sorted(p.name for p in DATA_DIR.iterdir() if p.is_dir())


def hybrid_search(os_client, embed_client, query: str, weeks: List[str], limit: int) -> List[Dict]:
    """knn(0.7) + BM25(0.3) hybrid. 주차 리스트를 명시해 recency boost 없이 전 주차 동등 검색.

    (rag_api_opensearch_v3.OpenSearchClient.search 의 쿼리 구조를 경량 복제)
    """
    emb = embed_vectordb.get_embedding(query, embed_client)
    body = {
        "size": limit,
        "query": {
            "bool": {
                "should": [
                    {"knn": {"embedding": {"vector": emb, "k": limit, "boost": 7.0}}},
                    {"match": {"text": {"query": query, "analyzer": "korean", "boost": 0.3}}},
                ],
                "filter": [{"terms": {"week": weeks}}] if weeks else [],
                "minimum_should_match": 1,
            }
        },
        "_source": {"excludes": ["embedding"]},
    }
    resp = os_client.search(index=embed_vectordb.INDEX_NAME, body=body)
    hits = []
    for h in resp["hits"]["hits"]:
        s = h["_source"]
        hits.append(
            {
                "score": h["_score"],
                "text": s.get("text", ""),
                "team": s.get("team", "unknown"),
                "week": s.get("week", "unknown"),
                "mail_id": s.get("mail_id", "unknown"),
                "part_index": s.get("part_index"),
            }
        )
    return hits


def gather(topic: str, queries: List[str], weeks: List[str], k: int) -> List[Dict]:
    """모든 하위 질의를 검색해 dedup·병합 (최고 점수 유지)."""
    os_client = embed_vectordb.get_opensearch_client()
    embed_client = embed_vectordb.get_embedding_client()
    merged: Dict[tuple, Dict] = {}
    for q in queries:
        for h in hybrid_search(os_client, embed_client, q, weeks, k):
            key = (h["team"], h["week"], h["mail_id"], h["part_index"])
            if key not in merged or h["score"] > merged[key]["score"]:
                merged[key] = h
    hits = list(merged.values())
    hits.sort(key=lambda x: (x["week"], x["team"]))
    return hits


# ========== 3. 관련성 필터 (adversarial verify, fail-open) ==========
def filter_relevant(topic: str, hits: List[Dict]) -> List[Dict]:
    if len(hits) <= 6:
        return hits
    lines = [f"[{i}] ({h['week']}/{h['team']}) {h['text'][:300]}" for i, h in enumerate(hits)]
    raw = _chat(
        RELEVANCE_FILTER_SYSTEM,
        f"보고 주제: {topic}\n\n--- 발췌 목록 ---\n" + "\n".join(lines),
        max_tokens=2500,
        temperature=0,
    )
    idx = _parse_json_list(raw)
    keep = [hits[i] for i in idx if isinstance(i, int) and 0 <= i < len(hits)]
    return keep if keep else hits  # 파싱 실패/공집합이면 원본 유지


# ========== 4. 합성 (필요 시 2-stage) ==========
def _blocks_by_week(hits: List[Dict]) -> Dict[str, List[Dict]]:
    grouped: Dict[str, List[Dict]] = {}
    for h in hits:
        grouped.setdefault(h["week"], []).append(h)
    return grouped


def _context_text(hits: List[Dict]) -> str:
    return "\n\n".join(f"[{h['week']}/{h['team']}] {h['text']}" for h in hits)


def synthesize(topic: str, hits: List[Dict]) -> str:
    context = _context_text(hits)
    if len(context) > MAX_CHARS_PER_CALL:
        # 2-stage: 주차별 1차 압축 후 종합
        print(f"  ↳ 입력 {len(context):,}자 > {MAX_CHARS_PER_CALL:,} — 주차별 1차 압축")
        parts = []
        for week, ws in sorted(_blocks_by_week(hits).items()):
            wtext = "\n\n".join(f"({h['team']}) {h['text']}" for h in ws)
            comp = _chat(WEEK_COMPRESS_SYSTEM, f"주제: {topic}\n주차: {week}\n\n{wtext[:MAX_CHARS_PER_CALL]}",
                         max_tokens=3500)
            parts.append(f"[{week}]\n{comp}")
        context = "\n\n".join(parts)
    user = (
        f"보고 주제: {topic}\n\n"
        f"--- 주차별 원본 발췌 (오름차순) ---\n{context}\n--- 끝 ---\n\n"
        "위 발췌만 근거로 보고서를 작성하세요."
    )
    return _chat(REPORT_SYSTEM, user, max_tokens=8000, temperature=0.3)


# ========== 5. grounding 검증 ==========
def check_grounding(topic: str, md: str, hits: List[Dict]) -> bool:
    src = _context_text(hits)
    user = (
        f"질문: {topic}\n\n--- 소스 발췌 ---\n{src[:30000]}\n\n"
        f"--- 보고서 ---\n{md[:8000]}"
    )
    verdict = _chat(GROUNDING_SYSTEM, user, max_tokens=2500, temperature=0).upper()
    approved = "APPROVE" in verdict
    print(f"  {'✅' if approved else '❌'} [grounding] {verdict.splitlines()[-1][:40] if verdict else '(빈 응답)'}")
    return approved


# ========== 6. 렌더 (경량 markdown → Outlook 호환 HTML) ==========
def _md_to_rows(md: str) -> str:
    """## / ### / 문단 / - 불릿 / **bold** / [YYYY-WW/팀] 인용을 표 기반 HTML 로."""
    rows: List[str] = []
    bullets: List[str] = []

    def flush_bullets():
        if not bullets:
            return
        items = "".join(
            f'<li style="margin:0 0 6px 0; line-height:22px;">{esc_inline(b)}</li>'
            for b in bullets
        )
        rows.append(
            f'<tr><td style="padding:2px 28px 10px 28px; font-family:{FONT}; '
            f'font-size:14px; color:{INK};"><ul style="margin:0; padding-left:20px;">{items}</ul></td></tr>'
        )
        bullets.clear()

    for line in md.splitlines():
        s = line.strip()
        if not s:
            flush_bullets()
            continue
        if s.startswith("### "):
            flush_bullets()
            rows.append(
                f'<tr><td style="padding:12px 28px 4px 28px; font-family:{FONT}; '
                f'font-size:15px; font-weight:bold; color:{INK};">{esc_inline(s[4:])}</td></tr>'
            )
        elif s.startswith("## "):
            flush_bullets()
            rows.append(
                f'<tr><td style="padding:18px 28px 6px 28px;">'
                f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                f'style="border-collapse:collapse;"><tr>'
                f'<td width="4" bgcolor="{NAVY}" style="width:4px; background-color:{NAVY}; '
                f'font-size:1px; line-height:1px;">&nbsp;</td>'
                f'<td style="padding-left:10px; font-family:{FONT}; font-size:17px; '
                f'font-weight:bold; color:{NAVY};">{esc_inline(s[3:])}</td>'
                f'</tr></table></td></tr>'
            )
        elif s.startswith("# "):
            continue  # 최상위 제목은 배너로 대체
        elif s.startswith("- ") or s.startswith("* "):
            bullets.append(s[2:])
        else:
            flush_bullets()
            rows.append(
                f'<tr><td style="padding:4px 28px 8px 28px; font-family:{FONT}; '
                f'font-size:14px; line-height:23px; color:{INK};">{esc_inline(s)}</td></tr>'
            )
    flush_bullets()
    return "".join(rows)


def render_html(topic: str, md: str, weeks: List[str]) -> str:
    head = HEAD_TEMPLATE.format(title=html.escape(f"주제 보고서 — {topic}"))
    span = f"{weeks[0]} ~ {weeks[-1]}" if weeks else ""
    header_row = (
        f'<tr><td bgcolor="{NAVY}" width="680" '
        f'style="width:680px; padding:20px 28px; background-color:{NAVY}; font-family:{FONT};">'
        f'<div style="color:#ffffff; font-size:20px; font-weight:bold; line-height:26px;">'
        f'{html.escape(topic)}</div>'
        f'<div style="color:#cfd8e3; font-size:12px; margin-top:6px;">'
        f'주제 기반 종합 보고서 &middot; {html.escape(span)}</div>'
        f'</td></tr>'
    )
    footer_row = (
        f'<tr><td bgcolor="{PANEL_BG}" style="background-color:{PANEL_BG}; '
        f'border-top:1px solid {BORDER}; padding:14px 28px; font-family:{FONT}; '
        f'font-size:11px; line-height:17px; color:{MUTED};">'
        f'본 보고서는 전(全)주차 원본 메일을 deep-research 방식으로 종합해 자동 생성되었습니다. '
        f'인용 [YYYY-WW/팀]은 근거 주차/팀을 가리킵니다.</td></tr>'
    )
    body = f"""<body bgcolor="{PAGE_BG}" style="margin:0; padding:0; background-color:{PAGE_BG}; font-family:{FONT}; color:{INK};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE_BG}" style="border-collapse:collapse; background-color:{PAGE_BG};">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="680" cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD_BG}" style="width:680px; border-collapse:collapse; background-color:{CARD_BG}; border:1px solid {BORDER};">
      {header_row}
      {_md_to_rows(md)}
      {footer_row}
    </table>
  </td></tr>
</table>
</body>
</html>
"""
    return head + body


# ========== 7. PPT (pptdaddy native exporter 재사용) ==========
def _strip_nulls(obj):
    """dict 의 None 값 키를 재귀적으로 제거 (LLM이 낸 null 색상 등이 _parse_color 를 깨뜨리는 것 방지)."""
    if isinstance(obj, dict):
        return {k: _strip_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_nulls(v) for v in obj]
    return obj


def report_to_pptx(topic: str, md: str, out_path: Path) -> Path | None:
    """보고서 md → LLM 슬라이드 설계 → pptdaddy create_native_pptx → 편집 가능한 .pptx."""
    user = f"보고 주제: {topic}\n\n--- 보고서 ---\n{md}\n--- 끝 ---\n\nslides_data JSON 배열을 작성하세요."
    slides = None
    for attempt in (1, 2):
        raw = _chat(SLIDE_PLAN_SYSTEM, user, max_tokens=16000, temperature=0.2)
        parsed = _parse_json_list(raw)
        if parsed:
            slides = parsed
            break
        print(f"  ⚠️ 슬라이드 JSON 파싱 실패(시도 {attempt})")
    if not slides:
        print("❌ 슬라이드 설계 실패 — PPT 미생성")
        return None

    slides = _strip_nulls(slides)  # null 색상값 등 제거 (export_native _parse_color 보호)

    # export_native 는 python-pptx 만 의존 (playwright 무관) → 안전하게 import
    sys.path.insert(0, str(ROOT / "pptdaddy"))
    from utils.export_native import create_native_pptx

    create_native_pptx(slides, output_file=str(out_path), presentation_title=topic)
    print(f"📊 {out_path} ({len(slides)} 슬라이드)")
    return out_path


def _capture_screenshots(slide_files: List[Path], shot_dir: Path) -> List[str]:
    """Playwright(headless)로 1920x1080 슬라이드 HTML 캡처 → png 경로 리스트."""
    from playwright.sync_api import sync_playwright

    shot_dir.mkdir(parents=True, exist_ok=True)
    shots: List[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for i, sf in enumerate(slide_files, 1):
            page.goto(f"file://{sf.resolve()}", wait_until="networkidle")
            page.wait_for_timeout(500)
            out = shot_dir / f"slide_{i}.png"
            page.screenshot(path=str(out), full_page=False)
            shots.append(str(out))
            print(f"  📸 {out.name}")
        browser.close()
    return shots


def report_to_pptx_screenshot(topic: str, md: str, out_path: Path) -> Path | None:
    """보고서 md → LLM HTML 슬라이드 → Playwright 캡처 → pptdaddy create_pptx_from_screenshots."""
    user = f"보고 주제: {topic}\n\n--- 보고서 ---\n{md}\n--- 끝 ---\n\nHTML 슬라이드들을 ===SLIDE=== 로 구분해 출력하세요."
    raw = _chat(SLIDE_HTML_SYSTEM, user, max_tokens=20000, temperature=0.2)
    htmls = [h.strip() for h in raw.split("===SLIDE===") if "<" in h and h.strip()]
    if not htmls:
        print("❌ HTML 슬라이드 생성 실패 — PPT 미생성")
        return None

    slug = _slugify_topic(topic)
    asset_dir = OUT_DIR / "_ppt_assets" / slug
    slide_dir = asset_dir / "slides"
    slide_dir.mkdir(parents=True, exist_ok=True)
    slide_files = []
    for i, html_doc in enumerate(htmls, 1):
        f = slide_dir / f"slide_{i}.html"
        f.write_text(html_doc, encoding="utf-8")
        slide_files.append(f)

    shots = _capture_screenshots(slide_files, asset_dir / "screenshots")
    if not shots:
        print("❌ 스크린샷 캡처 실패 — PPT 미생성")
        return None

    sys.path.insert(0, str(ROOT / "pptdaddy"))
    from utils.export import create_pptx_from_screenshots

    create_pptx_from_screenshots(shots, output_file=str(out_path), presentation_title=topic)
    print(f"📊 {out_path} ({len(shots)} 슬라이드, screenshot)")
    return out_path


# ========== 오케스트레이션 ==========
def build_report(topic: str, n_sub: int, k: int) -> Dict | None:
    weeks = list_indexed_weeks()
    print(f"🔎 전 주차 검색 대상: {len(weeks)}개 ({weeks[0]}~{weeks[-1]})" if weeks else "⚠️ data/ 주차 없음")

    queries = expand_queries(topic, n_sub)
    print(f"🧭 질의 fan-out {len(queries)}개: {queries}")

    hits = gather(topic, queries, weeks, k)
    print(f"📥 검색·병합 청크: {len(hits)}건")
    if not hits:
        print("❌ 관련 내용 없음 — 보고서 미생성")
        return None

    hits = filter_relevant(topic, hits)
    covered = sorted({h["week"] for h in hits})
    teams = sorted({h["team"] for h in hits})
    print(f"✅ 관련성 통과 {len(hits)}건 — 주차 {len(covered)}개, 팀 {len(teams)}개")

    md = synthesize(topic, hits)
    if not md:
        print("❌ 합성 실패")
        return None

    grounded = check_grounding(topic, md, hits)
    if not grounded:
        print("🚫 grounding 검증 실패 — 검토 필요(파일은 생성하되 경고 표기)")

    return {"md": md, "weeks": covered, "teams": teams, "grounded": grounded}


def write_outputs(topic: str, result: Dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify_topic(topic)
    today = date.today().isoformat()
    weeks = result["weeks"]
    fm = (
        f"---\ntitle: 주제 보고서 - {topic}\ntopic: {topic}\n"
        f"generated: {today}\nweeks: {', '.join(weeks)}\n"
        f"teams: {', '.join(result['teams'])}\ngrounded: {result['grounded']}\n---\n\n"
    )
    md_path = OUT_DIR / f"{slug}_보고서.md"
    md_path.write_text(fm + result["md"], encoding="utf-8")

    html_path = md_path.with_suffix(".html")
    html_path.write_text(render_html(topic, result["md"], weeks), encoding="utf-8")
    print(f"📝 {md_path}")
    print(f"🌐 {html_path}")
    return html_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("topic", help="보고 주제 (자연어)")
    ap.add_argument("--subqueries", type=int, default=5, help="질의 확장 개수 (기본 5)")
    ap.add_argument("--limit", type=int, default=8, help="질의당 top-K (기본 8)")
    ap.add_argument("--send", action="store_true", help="생성 후 메일 발송")
    ap.add_argument("--ppt", action="store_true", help="PPTX도 생성")
    ap.add_argument("--ppt-mode", choices=["native", "screenshot"], default="native",
                    help="native=편집가능(python-pptx), screenshot=HTML 캡처 이미지(playwright). 기본 native")
    args = ap.parse_args()

    print("=" * 60)
    print(f"🚀 주제 보고서 — {args.topic}")
    print("=" * 60)

    result = build_report(args.topic, args.subqueries, args.limit)
    if result is None:
        return 1

    html_path = write_outputs(args.topic, result)

    if args.ppt:
        pptx_path = OUT_DIR / f"{_slugify_topic(args.topic)}_보고서.pptx"
        if args.ppt_mode == "screenshot":
            report_to_pptx_screenshot(args.topic, result["md"], pptx_path)
        else:
            report_to_pptx(args.topic, result["md"], pptx_path)

    if args.send:
        import send_report
        week_label = result["weeks"][-1] if result["weeks"] else date.today().isoformat()
        send_report.send_report(html_path, week_label)
        print("📧 발송 완료")

    print("🎉 완료")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n❌ 실패: {type(e).__name__}: {e}")
        sys.exit(1)

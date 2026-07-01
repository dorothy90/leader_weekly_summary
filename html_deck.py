"""LLM이 슬라이드를 HTML로 직접 디자인 → Playwright 캡처 → 이미지 PPTX.

pptxgenjs 하드코딩 대신, LLM에게 슬라이드별 자유 HTML(Tailwind + Chart.js + Pretendard)을
짜게 해 Claude 아티팩트/Gamma 급 퀄리티를 얻는다. 결과는 이미지 슬라이드(편집불가, 고퀄).

입력: DeepMiningAnalysis + PresentationOutline (deep_mining synthesize 출력)
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent


def _theme() -> dict:
    try:
        b = json.loads((ROOT / "themes" / "exec.json").read_text(encoding="utf-8")).get("brand", {})
    except Exception:
        b = {}
    return {
        "primary": "#" + b.get("primary", "0B3B76"),
        "primaryDark": "#" + b.get("primary_dark", "07264D"),
        "accent": "#" + b.get("accent", "1EA0A0"),
        "ink": "#" + b.get("ink", "1A2230"),
        "muted": "#" + b.get("muted", "6B7480"),
    }


def _system_prompt(target_slides: int, t: dict) -> str:
    return f"""당신은 세계적 수준의 프레젠테이션 디자이너입니다(맥킨지·Gamma·Claude 아티팩트 톤).
반도체 수율·품질 임원 보고를 위한 **1920x1080px 슬라이드들을 각각 완결된 HTML 문서**로 디자인하세요.
슬라이드 사이는 정확히 한 줄 `===SLIDE===` 로 구분. 설명/코드펜스/JSON 없이 HTML 들만 출력.

[기술 스택 — 각 슬라이드 <head>에 반드시 포함]
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>*{{font-family:'Pretendard',sans-serif;-webkit-font-smoothing:antialiased;}}</style>
body 는 반드시 `class="w-[1920px] h-[1080px] overflow-hidden m-0 p-0"`.

[브랜드 컬러 — Tailwind 커스텀 클래스로 바로 사용(설정은 시스템이 자동 주입)]
`bg-primary` `text-primary` `bg-primaryDark` `bg-accent` `text-accent` `text-ink` `text-muted` `border-accent` 등을 그대로 쓰면 됩니다.
값: primary {t['primary']} · primaryDark {t['primaryDark']} · accent {t['accent']} · ink {t['ink']} · muted {t['muted']}
⚠️ 다크 배경 슬라이드는 반드시 `bg-primaryDark`(또는 bg-primary)를 body에 주고 글자는 text-white/text-accent. 배경 없이 text-white 쓰면 백지가 됨.

[모든 콘텐츠 슬라이드 필수 골격 — 이 구조를 반드시 따르세요(표지·섹션·종합 제외)]
```
<body class="w-[1920px] h-[1080px] overflow-hidden bg-white flex flex-col p-16">
  <header class="shrink-0">
    <p class="text-accent font-bold uppercase tracking-[0.2em] text-lg">실제 카테고리(예: 핵심 지표)</p>
    <h1 class="text-5xl font-black text-ink mt-1">슬라이드 제목</h1>
  </header>
  <main class="flex-1 min-h-0 mt-10">  <!-- 본문: 이 영역을 꽉 채운다 --></main>
  <footer class="shrink-0 flex justify-between items-center text-muted text-base border-t pt-4">
    <span>보고서명</span><span>페이지</span>
  </footer>
</body>
```
- kicker에 "KICKER" 같은 placeholder 금지 — 실제 카테고리명을 쓸 것.

[내용 밀도 — 매우 중요]
- analysis의 **key_bullets·details·metrics·risks·trends를 빠짐없이** 슬라이드에 녹여라. 한 줄로 과하게 요약 금지.
- 팀 카드/2컬럼은 각 팀의 key_bullets를 **전부**(3~5개) 불릿으로, 관련 수치는 큰 스탯으로 병기.
- 이슈 슬라이드는 원인·영향·대응·잔여리스크를 각각 항목으로. 슬라이드가 비면 내용을 더 끌어와 채워라(단, 지어내기 금지 — 제공된 데이터 범위 내에서).

[캔버스 채우기 — 가장 중요]
- <main>은 `flex-1`로 남은 세로를 전부 차지. 그 안의 그리드/카드도 `h-full`로 세로를 꽉 채운다. **상단에만 몰리고 아래가 비면 실패.**
- 카드는 `h-full flex flex-col justify-between p-8` — 값/제목은 위, 라벨/칩/근거는 아래로 분산해 카드 내부도 꽉 차게.
- **내용이 적은 슬라이드(불릿 1~2개 등)는 빈 느낌 금지**: 불릿을 text-2xl~3xl로 크게, `justify-center`로 세로 중앙 정렬, 각 불릿 앞에 아이콘/체크, 관련 수치를 큰 스탯으로 병기해 공간을 채운다. 2컬럼 패널은 heading을 상단 바(bg-primary/5 rounded)로, 본문을 세로 중앙에.
- 차트 슬라이드: <main>에 `pb-8`을 주어 canvas가 풋터와 겹치지 않게. canvas는 <main> 안에서 `h-full w-full`.

[예시 — KPI 슬라이드 <main>]
```
<main class="flex-1 min-h-0 mt-10 grid grid-cols-4 gap-6">
  <div class="h-full rounded-2xl border border-slate-200 shadow-lg p-8 flex flex-col justify-between">
    <div class="w-14 h-14 rounded-full bg-primary/10 flex items-center justify-center"><i class="fa-solid fa-arrow-trend-up text-primary text-2xl"></i></div>
    <div><p class="text-7xl font-black text-primary">98.3%</p></div>
    <div><span class="inline-flex items-center gap-1 px-3 py-1 rounded-full bg-emerald-50 text-emerald-600 font-bold text-sm">▲ +4.5%p</span>
    <p class="text-lg font-bold text-ink mt-3">HBM3E 최종 수율</p><p class="text-slate-400 text-sm mt-1">[2026-35/HBM수율]</p></div>
  </div>
  <!-- 카드 3개 더 -->
</main>
```
- 한 색(primary)이 지배(60~70%), accent는 강조 포인트에만. 표지·섹션·종합은 **다크 배경**, 본문은 라이트("샌드위치").
- **모든 슬라이드에 시각요소**(카드/아이콘/차트/스탯). 텍스트-only 금지. 레이아웃 반복 금지.
- 큰 스탯 콜아웃(text-7xl~8xl 숫자), 아이콘은 컬러 원 배지(rounded-full) 안에. 카드는 rounded-2xl + shadow-xl + 미묘한 border.
- **제목 밑 강조선(밑줄 바) 금지**(AI 티). 위계는 크기·굵기·색으로. 본문 좌측정렬.
- 상단 작은 kicker(accent, uppercase, tracking-widest) + 큰 제목(text-4xl~5xl bold). 하단 얇은 풋터(페이지/보고서명).
- 폰트: 제목 font-bold~black, 본문 font-normal~medium.

[데이터 시각화 — Chart.js]
- 추이/비교는 canvas + Chart.js. **반드시 `options.animation=false`**(캡처 안정화), `responsive:false`, canvas width/height 명시.
- 축/그리드는 옅게(muted), 데이터 라벨/포인트 강조. 브랜드 컬러 사용, 라인은 area fill(투명도).

[구성 — 총 {target_slides}장 내외]
1) 표지(다크): kicker + 큰 제목 + 부제(주차 구간·문서수). 거대한 고스트 텍스트/그래픽 모티프로 채움.
2) 섹션 구분(다크): 큰 번호 + 제목.
3) 핵심 지표: KPI 스탯 카드 그리드(값·델타 칩·라벨·아이콘).
4) 추이 차트(Chart.js line/area).
5) 이슈/원인: 아이콘 카드 리스트 or 2컬럼.
6) 팀 비교: 카드/표.
7) 주차별 타임라인.
8) 종합·시사점(다크): 번호형 핵심 3~5개.
- 각 항목 근거는 작은 회색 캡션 `[YYYY-WW/팀]`으로. analysis/outline의 실제 팀명·주차·수치만 사용(지어내기 금지).

각 슬라이드 HTML을 `===SLIDE===` 로 구분해 출력하세요."""


def _llm() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENROUTER_API_KEY", ""), base_url=os.getenv("OPENROUTER_BASE_URL", "") or None)


def build_html_deck(analysis: dict, outline: Optional[dict], meta: Optional[dict] = None,
                    *, target_slides: int = 8) -> List[str]:
    """analysis(+outline) → 슬라이드 HTML 리스트."""
    meta = meta or {}
    t = _theme()
    user = (
        f"보고 제목: {meta.get('title','주제 보고서')}\n총 {target_slides}장 내외로 디자인.\n\n"
        f"[analysis]\n{json.dumps(analysis, ensure_ascii=False)[:12000]}\n\n"
        f"[outline]\n{json.dumps(outline, ensure_ascii=False)[:4000] if outline else '(없음)'}\n"
    )
    resp = _llm().chat.completions.create(
        model=os.getenv("DEEP_MINING_HTML_MODEL", "z-ai/glm-5.2"),  # 디자인 HTML 최강(색코딩·차트·타임라인 자동)
        messages=[{"role": "system", "content": _system_prompt(target_slides, t)},
                  {"role": "user", "content": user}],
        temperature=0.4, max_tokens=16000,
    )
    raw = resp.choices[0].message.content or ""
    raw = re.sub(r"```html\n?|```\n?", "", raw)
    htmls = [h.strip() for h in raw.split("===SLIDE===") if "<" in h and h.strip()]
    if not htmls:
        raise RuntimeError("HTML 슬라이드 파싱 실패")
    logger.info(f"[html_deck] {len(htmls)} HTML 슬라이드 생성")
    return htmls


def _inject_config(html: str, t: dict) -> str:
    """브랜드 컬러를 Tailwind 커스텀 컬러로 주입 — LLM이 bg-primary/text-accent 등을
    써도 항상 해석되게 보장(색 누락·백지 슬라이드 방지)."""
    cfg = (
        "<script>tailwind={config:{theme:{extend:{colors:{"
        f"primary:'{t['primary']}',primaryDark:'{t['primaryDark']}',accent:'{t['accent']}',"
        f"ink:'{t['ink']}',muted:'{t['muted']}'"
        "}}}}}</script>"
    )
    if "cdn.tailwindcss.com" in html:
        return re.sub(r"(<script src=\"https://cdn\.tailwindcss\.com\"></script>)", r"\1" + cfg, html, count=1)
    if "</head>" in html:
        return html.replace("</head>", cfg + "</head>", 1)
    return cfg + html


def _capture(htmls: List[str], asset_dir: Path, t: dict) -> List[str]:
    from playwright.sync_api import sync_playwright
    slide_dir = asset_dir / "slides"; shot_dir = asset_dir / "shots"
    slide_dir.mkdir(parents=True, exist_ok=True); shot_dir.mkdir(parents=True, exist_ok=True)
    shots: List[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=2)
        for i, html in enumerate(htmls, 1):
            f = slide_dir / f"slide_{i}.html"
            f.write_text(_inject_config(html, t), encoding="utf-8")
            page.goto(f"file://{f.resolve()}", wait_until="networkidle")
            try:  # Tailwind play-CDN이 클래스 처리(배경색 적용)를 마칠 때까지 대기
                page.wait_for_function("document.body && getComputedStyle(document.body).backgroundColor !== 'rgba(0, 0, 0, 0)'", timeout=4000)
            except Exception:
                pass
            page.evaluate("document.fonts && document.fonts.ready")
            page.wait_for_timeout(1600)  # Chart.js/폰트 렌더 대기
            out = shot_dir / f"slide_{i}.png"
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": 1920, "height": 1080})
            shots.append(str(out))
        browser.close()
    return shots


def html_to_pptx(htmls: List[str], out_path: str, title: str, asset_dir: Path) -> str:
    """HTML 슬라이드 → Playwright 캡처 → 이미지 PPTX."""
    import sys
    shots = _capture(htmls, asset_dir, _theme())
    if not shots:
        raise RuntimeError("스크린샷 캡처 실패")
    sys.path.insert(0, str(ROOT / "pptdaddy"))
    from utils.export import create_pptx_from_screenshots
    create_pptx_from_screenshots(shots, output_file=out_path, presentation_title=title)
    return out_path

"""주제별 타임라인 리포트 생성.

원하는 주제(topic)를 받아 OpenSearch(weekly_mail)에 쌓인 모든 주차 데이터를
'주차별 순회(per-week)' 방식으로 검색한 뒤, 시간순(오래된 주 → 최신 주)으로
정리한 타임라인 리포트(markdown / html)를 생성한다.

검색 전략 (주차별 순회):
  1. get_unique_weeks() 로 전체 주차 확보 → week_from/week_to 로 범위 제한
  2. 각 주차에 week 필터를 걸고 하이브리드 검색 → 주차별 top-k 확보
     (전역 top-k 한 번보다 모든 주차 커버리지가 보장되고 특정 주차 쏠림이 없음)
  3. 주차별 LLM 요약 — 검색 결과가 실제로 해당 주제와 무관하면 '관련 없음'으로 제외
  4. 전체 추이를 합성한 개요 + 주차별 섹션을 시간순으로 묶어 리포트 생성

이 모듈은 rag_api_opensearch_v3 를 모듈 로드 시점에 import 하지 않는다(순환 import 방지).
CLI 실행 시에만 lazy import 한다.
"""

from __future__ import annotations

import argparse
import html as _html
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from opensearch import get_unique_weeks


# ========== Pydantic 스키마 ==========

class TopicTimelineRequest(BaseModel):
    """주제별 타임라인 작업 요청"""
    topic: str = Field(..., description="검색할 주제/키워드")
    week_from: Optional[str] = Field(None, description="시작 주차 (예: 2025-05). 포함")
    week_to: Optional[str] = Field(None, description="끝 주차 (예: 2025-25). 포함")
    team: Optional[str] = Field(None, description="특정 팀만 필터 (None=전체)")
    k_per_week: int = Field(default=5, ge=1, le=20, description="주차별 검색 결과 수")


class TopicTimelineResponse(BaseModel):
    """주제별 타임라인 작업 상태/결과"""
    job_id: str
    status: str  # "accepted" | "processing" | "completed" | "failed"
    topic: Optional[str] = None
    markdown_url: Optional[str] = None  # md 다운로드 경로
    html_url: Optional[str] = None      # html 다운로드 경로
    pptx_url: Optional[str] = None      # pptx 다운로드 경로 (생성 성공 시)
    overview: Optional[str] = None      # 전체 추이 요약(미리보기용)
    weeks_total: Optional[int] = None   # 검색 대상 주차 수
    weeks_covered: Optional[int] = None # 실제 관련 내용이 있던 주차 수
    document_count: Optional[int] = None
    progress: Optional[float] = None    # 0.0 ~ 1.0
    error: Optional[str] = None


# 주차별 요약 결과가 '관련 없음'일 때 LLM이 출력하는 센티넬
_NONE_SENTINEL = "관련내용없음"


# ========== 주차 범위 ==========

def _filter_weeks(
    weeks: List[str],
    week_from: Optional[str],
    week_to: Optional[str],
) -> List[str]:
    """주차 목록을 [week_from, week_to] 범위로 제한하고 오름차순 정렬.

    week 문자열은 "YYYY-WW" 형식이라 사전식 비교가 곧 시간순 비교가 된다.
    """
    result = []
    for w in weeks:
        if week_from and w < week_from:
            continue
        if week_to and w > week_to:
            continue
        result.append(w)
    return sorted(result)  # 오름차순 = 오래된 주 → 최신 주


# ========== LLM 프롬프트 ==========

def _summarize_week(
    llm,
    topic: str,
    week: str,
    hits: List[Dict],
    max_chars_per_hit: int,
) -> Optional[Dict]:
    """한 주차의 검색 결과를 LLM으로 요약.

    검색 결과가 실제로 topic 과 무관하면 None 을 반환(해당 주차 제외).
    반환: {"week", "bullets"(str), "teams"(list), "refs"(list)} 또는 None
    """
    if not hits:
        return None

    # 컨텍스트 구성 (팀/메일ID 라벨 부착)
    ctx_parts = []
    teams: List[str] = []
    refs: List[str] = []
    for h in hits:
        team = h.get("team", "unknown")
        mail_id = h.get("mail_id", "unknown")
        text = (h.get("text") or "").strip()[:max_chars_per_hit]
        if not text:
            continue
        if team not in teams:
            teams.append(team)
        ref = f"{team}/{mail_id}"
        if ref not in refs:
            refs.append(ref)
        ctx_parts.append(f"[팀:{team} | mail_id:{mail_id}]\n{text}")

    if not ctx_parts:
        return None

    context = "\n\n---\n\n".join(ctx_parts)

    prompt = f"""당신은 반도체 조직의 주간 업무 메일을 분석하는 애널리스트입니다.

주제: "{topic}"
주차: {week}

아래는 이 주차의 메일에서 위 주제로 검색된 내용입니다.

{context}

[작업]
- 위 내용 중 주제 "{topic}"와 직접 관련된 사실만 골라, 이 주차에 무슨 일/진척/이슈가 있었는지 한국어 불릿으로 정리하세요.
- 각 불릿은 1문장, 구체적 수치·상태·결정 위주로. 최대 4개.
- 추측/일반론 금지. 검색 내용에 근거한 사실만.
- 만약 위 내용이 주제 "{topic}"와 실질적으로 무관하면, 다른 말 없이 정확히 "{_NONE_SENTINEL}" 한 줄만 출력하세요.

[출력] 불릿(- )만 출력. 머리말/맺음말 없이."""

    resp = llm.invoke(prompt)
    content = (getattr(resp, "content", None) or str(resp)).strip()

    # 센티넬 / 빈 응답 처리
    normalized = content.replace(" ", "").replace("\n", "")
    if not content or _NONE_SENTINEL in normalized:
        return None

    return {
        "week": week,
        "bullets": content,
        "teams": teams,
        "refs": refs,
    }


def _synthesize_overview(llm, topic: str, week_summaries: List[Dict]) -> str:
    """주차별 요약들을 종합해 전체 추이(개요)를 작성."""
    if not week_summaries:
        return f'주제 "{topic}"와 관련된 내용이 검색 범위 내에서 발견되지 않았습니다.'

    timeline_text = "\n\n".join(
        f"[{ws['week']}]\n{ws['bullets']}" for ws in week_summaries
    )

    prompt = f"""당신은 반도체 조직의 주간 업무 흐름을 정리하는 애널리스트입니다.

주제: "{topic}"

아래는 주차별로 정리된 이 주제의 타임라인입니다(오래된 주 → 최신 주).

{timeline_text}

[작업]
위 타임라인 전체를 종합해, 이 주제가 시간에 따라 어떻게 전개됐는지를 4~6문장의 한국어 단락으로 요약하세요.
- 시작 상황 → 주요 전개/변곡점 → 최신 상태 흐름이 드러나게.
- 핵심 수치·결정·이슈는 유지. 과장/추측 금지.
[출력] 단락 텍스트만."""

    resp = llm.invoke(prompt)
    return (getattr(resp, "content", None) or str(resp)).strip()


# ========== 리포트 빌드 ==========

def _build_markdown(
    topic: str,
    overview: str,
    week_summaries: List[Dict],
    *,
    weeks_total: int,
    week_from: Optional[str],
    week_to: Optional[str],
    team: Optional[str],
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    span = f"{week_summaries[0]['week']} ~ {week_summaries[-1]['week']}" if week_summaries else "-"

    lines: List[str] = []
    # frontmatter (wiki_summaries / wiki_export 호환: summary_type=topic)
    lines.append("---")
    lines.append(f"title: {topic} 주제별 타임라인")
    lines.append("summary_type: topic")
    lines.append(f"topic: {topic}")
    lines.append(f"generated_at: {now}")
    lines.append(f"weeks_covered: {len(week_summaries)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# 📌 {topic} — 주제별 타임라인")
    lines.append("")
    # 메타 정보
    meta_bits = [f"검색 주차 {weeks_total}개 중 관련 {len(week_summaries)}개", f"기간 {span}"]
    if team:
        meta_bits.append(f"팀: {team}")
    if week_from or week_to:
        meta_bits.append(f"범위: {week_from or '처음'} ~ {week_to or '최신'}")
    lines.append(f"*{' · '.join(meta_bits)} · 생성 {now}*")
    lines.append("")
    lines.append("## 개요")
    lines.append("")
    lines.append(overview)
    lines.append("")
    lines.append("## 타임라인")
    lines.append("")

    if not week_summaries:
        lines.append("_관련 내용이 있는 주차가 없습니다._")
        lines.append("")
    else:
        for ws in week_summaries:
            teams_str = ", ".join(ws["teams"]) if ws["teams"] else "-"
            lines.append(f"### {ws['week']}")
            lines.append("")
            lines.append(ws["bullets"])
            lines.append("")
            lines.append(f"> 관련 팀: {teams_str}")
            lines.append("")

    return "\n".join(lines)


def _md_to_html(markdown_text: str, title: str) -> str:
    """경량 markdown→HTML 변환 (외부 의존성 없이 동작).

    지원: frontmatter 제거, #/##/### 헤딩, '- ' 불릿, '> ' 인용, **굵게**, 단락.
    """
    # frontmatter 제거
    body = markdown_text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            nl = body.find("\n", end + 1)
            body = body[nl + 1:] if nl != -1 else ""

    def inline(s: str) -> str:
        s = _html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        return s

    html_lines: List[str] = []
    in_list = False
    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if line.startswith("### "):
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>"); in_list = True
            html_lines.append(f"<li>{inline(line[2:])}</li>")
        elif line.startswith("> "):
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f'<blockquote>{inline(line[2:])}</blockquote>')
        elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f'<p class="meta">{inline(line.strip("*"))}</p>')
        else:
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f"<p>{inline(line)}</p>")
    if in_list:
        html_lines.append("</ul>")

    style = """
    body{font-family:-apple-system,'Malgun Gothic',sans-serif;max-width:860px;margin:32px auto;padding:0 20px;color:#1a1a1a;line-height:1.65;}
    h1{font-size:26px;border-bottom:2px solid #2563eb;padding-bottom:8px;}
    h2{font-size:20px;margin-top:32px;color:#2563eb;}
    h3{font-size:16px;margin-top:24px;background:#eff6ff;display:inline-block;padding:4px 10px;border-radius:6px;}
    .meta{color:#888;font-size:13px;}
    blockquote{color:#666;font-size:13px;border-left:3px solid #d1d5db;margin:6px 0;padding:2px 12px;}
    ul{margin:8px 0;}li{margin:4px 0;}
    """
    return (
        f"<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        f"<title>{_html.escape(title)}</title><style>{style}</style></head>"
        f"<body>{''.join(html_lines)}</body></html>"
    )


# ========== 메인 오케스트레이터 ==========

def generate_topic_timeline(
    topic: str,
    os_client,
    *,
    llm=None,
    week_from: Optional[str] = None,
    week_to: Optional[str] = None,
    team: Optional[str] = None,
    k_per_week: int = 5,
    max_chars_per_hit: int = 1200,
    output_dir: Path = Path("exports/topic_timeline"),
    job_id: Optional[str] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> Dict:
    """주제별 타임라인 리포트를 생성하고 md/html 파일로 저장.

    Args:
        topic: 검색할 주제.
        os_client: OpenSearchClient 인스턴스 (search 메서드 보유).
        llm: langchain ChatOpenAI 호환 객체. None이면 lazy import로 생성.
        week_from/week_to: 주차 범위(포함). "YYYY-WW".
        team: 팀 필터.
        k_per_week: 주차별 검색 결과 수.
        output_dir: 결과 파일 저장 디렉토리.
        job_id: 파일명 prefix. None이면 timestamp.
        progress_callback: 0.0~1.0 진행률 콜백.

    Returns:
        dict: markdown, markdown_path, html_path, overview,
              weeks_total, weeks_covered, document_count, topic
    """
    def _progress(p: float):
        if progress_callback:
            try:
                progress_callback(p)
            except Exception:
                pass

    if llm is None:
        # CLI / 단독 실행용 lazy import (순환 import 방지)
        from rag_api_opensearch_v3 import get_llm
        llm = get_llm()

    _progress(0.05)

    # 1. 전체 주차 → 범위 제한 + 시간순 정렬
    all_weeks = get_unique_weeks()
    weeks = _filter_weeks(all_weeks, week_from, week_to)
    weeks_total = len(weeks)
    if weeks_total == 0:
        raise ValueError("검색 대상 주차가 없습니다 (범위/데이터 확인)")

    # 2 + 3. 주차별 순회 검색 → 주차별 요약
    week_summaries: List[Dict] = []
    document_count = 0
    for i, w in enumerate(weeks):
        hits = os_client.search(
            query=topic,
            week=w,
            team=team,
            limit=k_per_week,
        )
        document_count += len(hits)
        summary = _summarize_week(llm, topic, w, hits, max_chars_per_hit)
        if summary:
            week_summaries.append(summary)
        # 검색/요약 구간을 0.05~0.85 로 매핑
        _progress(0.05 + 0.80 * (i + 1) / weeks_total)

    # week_summaries 는 weeks 순서(오름차순)를 유지 → 시간순
    # 4. 전체 추이 개요 합성
    overview = _synthesize_overview(llm, topic, week_summaries)
    _progress(0.92)

    # 리포트 빌드
    markdown = _build_markdown(
        topic, overview, week_summaries,
        weeks_total=weeks_total, week_from=week_from, week_to=week_to, team=team,
    )

    # 저장
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = job_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = output_dir / f"{stem}.md"
    html_path = output_dir / f"{stem}.html"
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(
        _md_to_html(markdown, f"{topic} 주제별 타임라인"), encoding="utf-8"
    )
    _progress(1.0)

    return {
        "topic": topic,
        "markdown": markdown,
        "markdown_path": str(md_path),
        "html_path": str(html_path),
        "overview": overview,
        "weeks_total": weeks_total,
        "weeks_covered": len(week_summaries),
        "document_count": document_count,
        # 구조화 데이터 (PPT 등 다른 출력에서 재사용)
        "week_summaries": week_summaries,
        "week_from": week_from,
        "week_to": week_to,
        "team": team,
    }


# ========== CLI ==========

def main() -> int:
    ap = argparse.ArgumentParser(description="주제별 타임라인 리포트 생성")
    ap.add_argument("topic", help="검색할 주제/키워드")
    ap.add_argument("--week-from", default=None, help="시작 주차 (예: 2025-05)")
    ap.add_argument("--week-to", default=None, help="끝 주차 (예: 2025-25)")
    ap.add_argument("--team", default=None, help="팀 필터")
    ap.add_argument("--k", type=int, default=5, help="주차별 검색 결과 수")
    ap.add_argument("--out", default="exports/topic_timeline", help="출력 디렉토리")
    args = ap.parse_args()

    # lazy import (순환 import 방지 + 무거운 의존성 회피)
    from rag_api_opensearch_v3 import OpenSearchClient

    os_client = OpenSearchClient()

    def _cli_progress(p: float):
        sys.stdout.write(f"\r진행률 {p*100:5.1f}%")
        sys.stdout.flush()

    result = generate_topic_timeline(
        args.topic,
        os_client,
        week_from=args.week_from,
        week_to=args.week_to,
        team=args.team,
        k_per_week=args.k,
        output_dir=Path(args.out),
        progress_callback=_cli_progress,
    )
    print()
    print(f"✅ 완료 — 관련 주차 {result['weeks_covered']}/{result['weeks_total']}, "
          f"검색 문서 {result['document_count']}건")
    print(f"   markdown: {result['markdown_path']}")
    print(f"   html    : {result['html_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

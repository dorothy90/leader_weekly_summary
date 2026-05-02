"""[Throwaway] v1/v2 monthly md 비교 평가.

Usage:
    python scripts/eval_monthly_v2.py wiki/monthly/2026-04_월간요약_v1_baseline.md wiki/monthly/2026-04_월간요약_v2.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


SECTION_RE = re.compile(r"^\*\*(\d+)\.\s*([^\*]+?)\*\*\s*$", re.MULTILINE)
CROSS_HEADER_RE = re.compile(r"^###\s+(?P<title>[^(\n]+?)\s*(?:\((?P<teams>[^)]+)\))?\s*$", re.MULTILINE)
TIMELINE_BULLET_RE = re.compile(r"^-\s*(?P<week>\d{4}-\d{2})\s*[:：]\s*(?P<body>.+)$", re.MULTILINE)


def load_md_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        idx = text.find("\n---", 3)
        if idx != -1:
            text = text[idx + 4 :].lstrip("\n")
    return text


def split_sections(body: str) -> dict[int, tuple[str, str]]:
    headers = list(SECTION_RE.finditer(body))
    out: dict[int, tuple[str, str]] = {}
    for i, m in enumerate(headers):
        num = int(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        out[num] = (title, body[start:end].strip())
    return out


def cross_block_count(section6: str) -> int:
    return len(list(CROSS_HEADER_RE.finditer(section6)))


def timeline_stats_per_block(section6: str) -> list[dict]:
    """블록별 timeline bullet 수 + 등장 주차 리스트."""
    blocks: list[dict] = []
    cursor_starts = [m.start() for m in CROSS_HEADER_RE.finditer(section6)]
    cursor_starts.append(len(section6))
    for i in range(len(cursor_starts) - 1):
        body = section6[cursor_starts[i]:cursor_starts[i + 1]]
        weeks = [m.group("week") for m in TIMELINE_BULLET_RE.finditer(body)]
        blocks.append({"weeks": weeks, "count": len(weeks)})
    return blocks


def diff_sections_1_5(v1_body: str, v2_body: str) -> dict[int, str]:
    s1 = split_sections(v1_body)
    s2 = split_sections(v2_body)
    out: dict[int, str] = {}
    for n in (1, 2, 3, 4, 5):
        a = s1.get(n, ("", ""))[1]
        b = s2.get(n, ("", ""))[1]
        if a == b:
            out[n] = "동일"
        elif a and b:
            # 길이 비교
            la, lb = len(a), len(b)
            out[n] = f"다름 (v1 {la}자 / v2 {lb}자, Δ {lb-la:+d})"
        elif not a:
            out[n] = "v1 누락"
        else:
            out[n] = "v2 누락"
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    v1 = Path(sys.argv[1])
    v2 = Path(sys.argv[2])
    v1b = load_md_body(v1)
    v2b = load_md_body(v2)

    print(f"[입력] v1: {v1.name} ({len(v1b):,}자)  v2: {v2.name} ({len(v2b):,}자)")
    print()

    print("=== 정량 1: 섹션 1~5 동일성 ===")
    diffs = diff_sections_1_5(v1b, v2b)
    for n, msg in diffs.items():
        print(f"  섹션 {n}: {msg}")
    print()

    s1 = split_sections(v1b).get(6, ("", ""))[1]
    s2 = split_sections(v2b).get(6, ("", ""))[1]
    n1 = cross_block_count(s1)
    n2 = cross_block_count(s2)
    print(f"=== 정량 2: 섹션 6 cross-team 블록 수 ===")
    print(f"  v1: {n1}건   v2: {n2}건")
    print()

    tl1 = timeline_stats_per_block(s1)
    tl2 = timeline_stats_per_block(s2)
    cov1 = sum(1 for b in tl1 if b["count"] > 0)
    cov2 = sum(1 for b in tl2 if b["count"] > 0)
    total1 = sum(b["count"] for b in tl1)
    total2 = sum(b["count"] for b in tl2)
    avg1 = total1 / max(len(tl1), 1)
    avg2 = total2 / max(len(tl2), 1)
    print("=== 정량 3: 주차별 timeline bullet 커버리지 ===")
    print(f"  v1 - timeline 적용 블록: {cov1}/{n1} ({cov1*100//max(n1,1)}%)  · 총 bullet {total1}개  · 블록당 평균 {avg1:.1f}")
    print(f"  v2 - timeline 적용 블록: {cov2}/{n2} ({cov2*100//max(n2,1)}%)  · 총 bullet {total2}개  · 블록당 평균 {avg2:.1f}")
    print()

    print("=== 정량 4: 블록별 등장 주차 ===")
    print(f"  v1:")
    for i, b in enumerate(tl1):
        print(f"    [{i+1}] {b['count']}주: {', '.join(b['weeks']) if b['weeks'] else '(없음)'}")
    print(f"  v2:")
    for i, b in enumerate(tl2):
        print(f"    [{i+1}] {b['count']}주: {', '.join(b['weeks']) if b['weeks'] else '(없음)'}")
    print()

    # 섹션 6 헤더 (제목 + 팀) 보존
    print("=== 정량 5: 섹션 6 블록 헤더 보존 ===")
    titles_v1 = [(m.group("title").strip(), (m.group("teams") or "").strip()) for m in CROSS_HEADER_RE.finditer(s1)]
    titles_v2 = [(m.group("title").strip(), (m.group("teams") or "").strip()) for m in CROSS_HEADER_RE.finditer(s2)]
    print(f"  v1 블록 제목들:")
    for t, teams in titles_v1:
        print(f"    - {t}  ({teams})")
    print(f"  v2 블록 제목들:")
    for t, teams in titles_v2:
        print(f"    - {t}  ({teams})")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

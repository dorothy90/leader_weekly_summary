"""[Throwaway] 월간 파이프라인 검증용 더미 team-week 데이터 생성/삭제.

이 스크립트는 일회성입니다 — 검증 종료 후 실 운영 데이터로 교체된 시점에 삭제 가능.

Usage:
    python generate_dummy_team_weeks.py --month 2026-04             # 생성+인덱싱
    python generate_dummy_team_weeks.py --month 2026-04 --no-index  # md 파일만
    python generate_dummy_team_weeks.py --month 2026-04 --purge     # 더미 정리

격리 정책:
- OpenSearch doc_id 는 'dummy_team_week_{week}_{팀}' prefix 강제 → RAG/topic timeline에서 식별 가능.
- --purge 는 prefix 매칭으로 OS 문서 + 파일 일괄 삭제.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

from team_dict import teams_by_group
from wiki_builder import (
    WIKI_INDEX,
    get_client,
    get_embedding_client,
    month_to_weeks,
    save_wiki_doc,
)

DUMMY_DOC_ID_PREFIX = "dummy_team_week_"
TEAM_WEEK_DIR = Path("wiki/team-week")

DOMAIN_SEEDS: dict[str, dict[str, list[str]]] = {
    "수율": {
        "topics": ["Edge Particle 개선", "Bridge defect Pareto", "D0 추세", "Wafer Edge 균일도"],
        "metrics": ["수율", "Defect Density"],
        "issues": ["Particle 영향 확대", "ECC fail 증가", "Etch 잔류물 검출"],
    },
    "품질": {
        "topics": ["Gate oxide integrity", "TDDB 수명 분석", "Cross-section 분석"],
        "metrics": ["클레임 건수", "TDDB 시간"],
        "issues": ["고객 클레임 신규 1건", "Reliability margin 저하"],
    },
    "FA": {
        "topics": ["Failure mode 신규 등록", "8D Report 진행", "근본원인 분석"],
        "metrics": ["8D 완료 건수"],
        "issues": ["FA 분석 백로그 증가"],
    },
    "양산수율": {
        "topics": ["양산 ramp 진척", "학습곡선 가속", "EPM PCSA 안정화"],
        "metrics": ["양산 수율", "Ramp 진척률"],
        "issues": ["초기 수율 변동성"],
    },
    "개발공정": {
        "topics": ["HBM4E Qual Step 진행", "DOE 매트릭스 실행", "양산 이관 일정 조율"],
        "metrics": ["Qual 진행률"],
        "issues": ["일정 압박 발생"],
    },
    "수율전략": {
        "topics": ["라인 확장 계획", "신규 라인 셋업 일정", "월간 목표 달성 점검"],
        "metrics": ["증산 목표 달성률"],
        "issues": ["라인 확장 일정 지연 가능성"],
    },
}


def _domain_for(team: str) -> str:
    if "FA" in team:
        return "FA"
    if "품질" in team:
        return "품질"
    if "수율전략" in team:
        return "수율전략"
    if "개발공정" in team:
        return "개발공정"
    if "양산수율" in team:
        return "양산수율"
    if "수율" in team:
        return "수율"
    return "수율"


def _seed_int(team: str, week: str) -> int:
    h = hashlib.sha256(f"{team}__{week}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _yield_pct(seed: int, base: float = 92.0, swing: float = 1.5) -> float:
    delta = ((seed % 1000) / 1000.0) * (2 * swing) - swing
    return round(base + delta, 1)


def _utilization_pct(seed: int) -> float:
    return _yield_pct(seed >> 4, base=88.0, swing=2.0)


def _pick(items: list[str], seed: int, n: int = 1) -> list[str]:
    if not items:
        return []
    n = min(n, len(items))
    return [items[(seed + i) % len(items)] for i in range(n)]


def _rotate(items: list[str], seed: int, n: int) -> list[str]:
    if not items:
        return []
    return [items[(seed + i) % len(items)] for i in range(n)]


def _group_for(team: str) -> str:
    for group, members in teams_by_group.items():
        if team in members:
            return group
    return ""


def _generate_team_week_md(team: str, week: str) -> str:
    seed = _seed_int(team, week)
    domain = _domain_for(team)
    seeds = DOMAIN_SEEDS[domain]
    group = _group_for(team)

    yield_val = _yield_pct(seed)
    util_val = _utilization_pct(seed)
    topics = _rotate(seeds["topics"], seed, 3)
    metric_label = seeds["metrics"][0]
    issue = seeds["issues"][seed % len(seeds["issues"])]

    sections = []
    sections.append(f"**1. 핵심 요약 (3줄 이내)**")
    sections.append(
        f"- {team}({group}) {week} 주차 {metric_label} {yield_val}%, 가동률 {util_val}%, 전주 대비 변동 ±0.{(seed % 9) + 1}%p"
    )
    sections.append(f"- 주요 토픽: {topics[0]} 진행, {topics[1]} 분석 중")
    sections.append(f"- 이슈: {issue} (관련 항목 {(seed % 3) + 1}건)")
    sections.append("")

    sections.append(f"**2. 주요 업무**")
    for t in topics:
        sections.append(f"- {t}: {week} 진행 상태 점검 및 차주 계획 수립")
    sections.append(f"- {metric_label} 모니터링 — 목표 {round(yield_val + 1.0, 1)}% 대비 -{round(1.0 - (seed % 3) * 0.1, 1)}%p")
    sections.append("")

    sections.append(f"**3. 이슈 & 리스크**")
    sections.append("| 항목 | 상세내용 | 영향도 | 대응방안 |")
    sections.append("|------|---------|--------|---------|")
    sections.append(
        f"| {issue} | {topics[0]} 영역에서 발생, {metric_label} {yield_val}% 기록 | 차월 양산 일정에 영향 가능 | {topics[1]} 데이터 기반 DOE 진행 |"
    )
    sections.append("")

    sections.append(f"**4. 핵심 키워드**")
    keywords = topics + [metric_label, issue.split()[0]]
    sections.append(f"- {', '.join(keywords)}")

    return "\n".join(sections)


def _doc_id(team: str, week: str) -> str:
    return f"{DUMMY_DOC_ID_PREFIX}{week}_{team}"


def _file_path(team: str, week: str) -> Path:
    return TEAM_WEEK_DIR / f"{week}_{team}_dummy.md"


def _write_md(team: str, week: str, body: str) -> Path:
    TEAM_WEEK_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc).isoformat()
    title = f"{team} {week} 주차 요약 (DUMMY)"
    frontmatter = (
        "---\n"
        f"title: \"{title}\"\n"
        f"summary_type: team-week\n"
        f"team: {team}\n"
        f"week: {week}\n"
        f"is_dummy: true\n"
        f"created_at: {now}\n"
        f"updated_at: {now}\n"
        "---\n\n"
    )
    path = _file_path(team, week)
    path.write_text(frontmatter + body, encoding="utf-8")
    return path


def _index_to_os(team: str, week: str, body: str) -> None:
    os_client = get_client()
    embed_client = get_embedding_client()
    save_wiki_doc(
        os_client,
        embed_client,
        text=body,
        title=f"{team} {week} 주차 요약 (DUMMY)",
        summary_type="team-week",
        team=team,
        week=week,
        doc_id=_doc_id(team, week),
    )


def cmd_generate(month: str, no_index: bool) -> int:
    weeks = month_to_weeks(month)
    if not weeks:
        print(f"❌ {month} 의 ISO 주차를 계산할 수 없습니다", file=sys.stderr)
        return 2

    teams = sorted({t for members in teams_by_group.values() for t in members})
    total = len(teams) * len(weeks)
    print(f"🌱 더미 시드: {len(teams)}팀 × {len(weeks)}주 = {total}개")
    print(f"   주차: {', '.join(weeks)}")

    written = 0
    indexed = 0
    for team in teams:
        for week in weeks:
            body = _generate_team_week_md(team, week)
            path = _write_md(team, week, body)
            written += 1
            if not no_index:
                try:
                    _index_to_os(team, week, body)
                    indexed += 1
                except Exception as exc:
                    print(f"   ⚠️ 인덱싱 실패: {team} {week} - {type(exc).__name__}: {exc}")
    print(f"✅ 파일 작성 {written}/{total}, OpenSearch 인덱싱 {indexed}/{total}")
    print(f"   격리 정책: doc_id prefix '{DUMMY_DOC_ID_PREFIX}' / 파일 suffix '_dummy.md'")
    return 0


def cmd_purge(month: str | None) -> int:
    try:
        os_client = get_client()
        if not os_client.indices.exists(index=WIKI_INDEX):
            print(f"⚠️ 인덱스 '{WIKI_INDEX}' 없음 — OS 정리 생략")
        else:
            teams = sorted({t for members in teams_by_group.values() for t in members})
            if month:
                weeks = month_to_weeks(month)
            else:
                weeks = []
            doc_ids: list[str] = []
            if weeks:
                for week in weeks:
                    for team in teams:
                        doc_ids.append(_doc_id(team, week))
            else:
                # month 미지정 시: ids 매칭 불가 → 전수 스캔 후 prefix 매칭으로 ID 추출
                scan_body = {
                    "size": 1000,
                    "_source": False,
                    "query": {"match_all": {}},
                }
                resp = os_client.search(index=WIKI_INDEX, body=scan_body)
                doc_ids = [
                    h["_id"]
                    for h in resp["hits"]["hits"]
                    if h["_id"].startswith(DUMMY_DOC_ID_PREFIX)
                ]
            deleted = 0
            for doc_id in doc_ids:
                try:
                    os_client.delete(index=WIKI_INDEX, id=doc_id, refresh=False)
                    deleted += 1
                except Exception:
                    pass
            os_client.indices.refresh(index=WIKI_INDEX)
            print(f"🗑️ OpenSearch: {deleted}/{len(doc_ids)}건 삭제 (prefix='{DUMMY_DOC_ID_PREFIX}')")
    except Exception as exc:
        print(f"   ⚠️ OpenSearch 정리 생략 (연결 실패 또는 오류): {type(exc).__name__}: {exc}")

    removed = 0
    if TEAM_WEEK_DIR.exists():
        if month:
            weeks = month_to_weeks(month)
            patterns = [f"{w}_*_dummy.md" for w in weeks]
        else:
            patterns = ["*_dummy.md"]
        for pat in patterns:
            for p in TEAM_WEEK_DIR.glob(pat):
                p.unlink()
                removed += 1
    print(f"🗑️ 파일: {removed}개 삭제")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--month",
        type=str,
        default=datetime.now().strftime("%Y-%m"),
        help="대상 월 (YYYY-MM, 기본: 현재 월)",
    )
    ap.add_argument("--no-index", action="store_true", help="OpenSearch 인덱싱 생략 (md 파일만)")
    ap.add_argument(
        "--purge",
        action="store_true",
        help=f"더미 정리 (OS doc_id prefix '{DUMMY_DOC_ID_PREFIX}' + '*_dummy.md' 파일)",
    )
    args = ap.parse_args()

    if args.purge:
        return cmd_purge(args.month)
    return cmd_generate(args.month, args.no_index)


if __name__ == "__main__":
    sys.exit(main())

"""
Wiki Export: OpenSearch wiki_summaries → 마크다운 파일 export
- wiki_summaries 인덱스의 내용을 마크다운 파일로 변환
- 팀/주차/타입별 필터링 지원
- index.md 자동 생성 (전체 목차)
"""

import os
import argparse
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from opensearchpy import OpenSearch
from dotenv import load_dotenv

load_dotenv()

# ========== 설정 ==========
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "rlaeorka1!K")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true"

WIKI_INDEX = "wiki_summaries"
OUTPUT_DIR = Path("wiki")

# summary_type → 디렉토리 매핑
TYPE_DIR_MAP = {
    "team-week": "team-week",
    "topic": "topic",
    "overview": "overview",
    "query_synthesis": "synthesis",
}


# ========== OpenSearch ==========
def get_client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
        use_ssl=OPENSEARCH_USE_SSL,
        verify_certs=False,
        ssl_show_warn=False,
    )


def fetch_wiki_docs(
    client: OpenSearch,
    team: Optional[str] = None,
    week: Optional[str] = None,
    summary_type: Optional[str] = None,
    limit: int = 1000,
) -> List[Dict]:
    """wiki_summaries 인덱스에서 문서 조회"""
    filters = []
    if team:
        filters.append({"term": {"team": team}})
    if week:
        filters.append({"term": {"week": week}})
    if summary_type:
        filters.append({"term": {"summary_type": summary_type}})

    if filters:
        query = {"bool": {"filter": filters}}
    else:
        query = {"match_all": {}}

    body = {
        "size": limit,
        "query": query,
        "sort": [
            {"week": {"order": "asc", "missing": "_last"}},
            {"team": {"order": "asc", "missing": "_last"}},
        ],
    }

    resp = client.search(index=WIKI_INDEX, body=body)
    docs = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        docs.append({
            "id": hit["_id"],
            "text": src.get("text", ""),
            "title": src.get("title", ""),
            "summary_type": src.get("summary_type", ""),
            "team": src.get("team"),
            "week": src.get("week"),
            "topic": src.get("topic"),
            "created_at": src.get("created_at", ""),
            "updated_at": src.get("updated_at", ""),
            "source_doc_ids": src.get("source_doc_ids", []),
        })
    return docs


# ========== 마크다운 변환 ==========
def doc_to_markdown(doc: Dict) -> str:
    """wiki 문서를 마크다운 문자열로 변환 (frontmatter 포함)"""
    lines = ["---"]

    lines.append(f"title: \"{doc['title']}\"")
    lines.append(f"summary_type: {doc['summary_type']}")
    if doc.get("team"):
        lines.append(f"team: {doc['team']}")
    if doc.get("week"):
        lines.append(f"week: {doc['week']}")
    if doc.get("topic"):
        lines.append(f"topic: {doc['topic']}")
    if doc.get("created_at"):
        lines.append(f"created_at: {doc['created_at']}")
    if doc.get("updated_at"):
        lines.append(f"updated_at: {doc['updated_at']}")
    if doc.get("source_doc_ids"):
        lines.append(f"source_docs: {len(doc['source_doc_ids'])}")

    lines.append("---")
    lines.append("")
    lines.append(f"# {doc['title']}")
    lines.append("")
    lines.append(doc["text"])
    lines.append("")

    return "\n".join(lines)


def get_filename(doc: Dict) -> str:
    """문서 타입에 따라 파일명 결정"""
    st = doc["summary_type"]

    if st == "team-week":
        team = doc.get("team", "unknown")
        week = doc.get("week", "unknown")
        return f"{week}_{team}.md"

    elif st == "overview":
        week = doc.get("week", "unknown")
        return f"{week}_전체요약.md"

    elif st == "topic":
        topic = doc.get("topic", "unknown")
        topic_safe = topic.replace("/", "_").replace(" ", "_")
        return f"{topic_safe}.md"

    elif st == "query_synthesis":
        # ID 기반 또는 타이틀 기반
        doc_id = doc.get("id", "unknown")
        # 짧은 ID로 변환
        short_id = doc_id[-8:] if len(doc_id) > 8 else doc_id
        return f"synthesis_{short_id}.md"

    else:
        return f"{doc.get('id', 'unknown')}.md"


def get_subdir(doc: Dict) -> str:
    """문서 타입에 따라 서브디렉토리 결정"""
    return TYPE_DIR_MAP.get(doc["summary_type"], "other")


# ========== Export ==========
def export_docs(docs: List[Dict], output_dir: Path):
    """문서들을 마크다운 파일로 export"""
    exported = 0

    for doc in docs:
        subdir = get_subdir(doc)
        filename = get_filename(doc)
        dir_path = output_dir / subdir
        dir_path.mkdir(parents=True, exist_ok=True)

        filepath = dir_path / filename
        content = doc_to_markdown(doc)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        exported += 1

    return exported


def generate_index(docs: List[Dict], output_dir: Path):
    """index.md 생성 — 전체 목차"""
    lines = [
        "# Wiki Index",
        "",
        f"> 자동 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 총 {len(docs)}개 문서",
        "",
    ]

    # 타입별 그룹핑
    by_type: Dict[str, List[Dict]] = {}
    for doc in docs:
        st = doc["summary_type"]
        by_type.setdefault(st, []).append(doc)

    type_labels = {
        "team-week": "팀별 주간 요약",
        "overview": "주차별 전체 요약",
        "topic": "주제별 타임라인",
        "query_synthesis": "Q&A 축적 지식",
    }

    for st, label in type_labels.items():
        group = by_type.get(st, [])
        if not group:
            continue

        subdir = TYPE_DIR_MAP.get(st, "other")
        lines.append(f"## {label} ({len(group)}개)")
        lines.append("")

        if st == "team-week":
            # 주차별로 그룹핑
            by_week: Dict[str, List[Dict]] = {}
            for doc in group:
                w = doc.get("week", "unknown")
                by_week.setdefault(w, []).append(doc)

            for week in sorted(by_week.keys(), reverse=True):
                lines.append(f"### {week}")
                for doc in sorted(by_week[week], key=lambda d: d.get("team", "")):
                    filename = get_filename(doc)
                    team = doc.get("team", "")
                    lines.append(f"- [{team}]({subdir}/{filename})")
                lines.append("")

        elif st == "overview":
            for doc in sorted(group, key=lambda d: d.get("week", ""), reverse=True):
                filename = get_filename(doc)
                week = doc.get("week", "")
                lines.append(f"- [{week} 전체 요약]({subdir}/{filename})")
            lines.append("")

        elif st == "topic":
            for doc in sorted(group, key=lambda d: d.get("topic", "")):
                filename = get_filename(doc)
                topic = doc.get("topic", "")
                lines.append(f"- [{topic}]({subdir}/{filename})")
            lines.append("")

        elif st == "query_synthesis":
            for doc in group:
                filename = get_filename(doc)
                title = doc.get("title", "")[:60]
                lines.append(f"- [{title}]({subdir}/{filename})")
            lines.append("")

    index_path = output_dir / "index.md"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"📖 index.md 생성 완료")


def run_export(
    team: Optional[str] = None,
    week: Optional[str] = None,
    summary_type: Optional[str] = None,
    output_dir: Optional[str] = None,
):
    """Export 실행"""
    client = get_client()

    # 인덱스 존재 확인
    if not client.indices.exists(index=WIKI_INDEX):
        print(f"❌ 인덱스 '{WIKI_INDEX}'가 존재하지 않습니다.")
        print("   먼저 wiki_builder.py를 실행하여 wiki를 생성하세요.")
        return

    out = Path(output_dir) if output_dir else OUTPUT_DIR

    # 필터 정보 출력
    filters = []
    if team:
        filters.append(f"team={team}")
    if week:
        filters.append(f"week={week}")
    if summary_type:
        filters.append(f"type={summary_type}")
    filter_str = ", ".join(filters) if filters else "전체"
    print(f"📤 Wiki Export 시작 (필터: {filter_str})")

    # 문서 조회
    docs = fetch_wiki_docs(client, team=team, week=week, summary_type=summary_type)
    if not docs:
        print("  ⚠️ 조건에 맞는 문서가 없습니다.")
        return

    print(f"  📄 {len(docs)}개 문서 조회 완료")

    # Export
    out.mkdir(parents=True, exist_ok=True)
    exported = export_docs(docs, out)
    print(f"  ✅ {exported}개 파일 생성 → {out}/")

    # index.md 생성 (전체 export 시에만)
    if not team and not week and not summary_type:
        # 전체 문서로 index 생성
        generate_index(docs, out)
    else:
        # 필터 export 시에도 해당 문서 기준 index 생성
        generate_index(docs, out)


# ========== CLI ==========
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wiki Export - OpenSearch → 마크다운 변환")
    parser.add_argument("--team", type=str, help="팀 필터 (예: YIELD팀)")
    parser.add_argument("--week", type=str, help="주차 필터 (예: 2025-48)")
    parser.add_argument("--type", type=str, dest="summary_type",
                        choices=["team-week", "overview", "topic", "query_synthesis"],
                        help="요약 타입 필터")
    parser.add_argument("--output", type=str, help="출력 디렉토리 (기본: wiki/)")
    parser.add_argument("--stats", action="store_true", help="통계만 출력")

    args = parser.parse_args()

    if args.stats:
        client = get_client()
        if not client.indices.exists(index=WIKI_INDEX):
            print(f"❌ 인덱스 '{WIKI_INDEX}'가 존재하지 않습니다.")
        else:
            count = client.count(index=WIKI_INDEX)["count"]
            print(f"📊 Wiki 문서 수: {count}")

            # 타입별 카운트
            body = {
                "size": 0,
                "aggs": {
                    "types": {"terms": {"field": "summary_type", "size": 10}},
                    "weeks": {"terms": {"field": "week", "size": 100, "order": {"_key": "desc"}}},
                },
            }
            resp = client.search(index=WIKI_INDEX, body=body)

            print("\n타입별:")
            for b in resp["aggregations"]["types"]["buckets"]:
                label = {"team-week": "팀별 주간", "overview": "전체 요약",
                         "topic": "주제별", "query_synthesis": "Q&A"}.get(b["key"], b["key"])
                print(f"  {label}: {b['doc_count']}개")

            print("\n주차별:")
            for b in resp["aggregations"]["weeks"]["buckets"][:10]:
                print(f"  {b['key']}: {b['doc_count']}개")
    else:
        run_export(
            team=args.team,
            week=args.week,
            summary_type=args.summary_type,
            output_dir=args.output,
        )

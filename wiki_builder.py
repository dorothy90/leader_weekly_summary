"""
Wiki Builder: OpenSearch wiki_summaries 인덱스 생성 및 백필
- 기존 weekly_mail 인덱스의 raw chunk를 읽어 LLM으로 요약 생성
- 요약 타입: team-week, topic-timeline, weekly-overview
- OpenSearch wiki_summaries 인덱스에 저장
"""

import os
import json
import argparse
import time
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from opensearchpy import OpenSearch, helpers
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ========== 설정 ==========
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "rlaeorka1!K")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "")
EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
EMBEDDING_DIMENSION = 4096
LLM_MODEL = 'z-ai/glm-4.7'
# LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss-120b")

SOURCE_INDEX = os.getenv("OPENSEARCH_INDEX", "weekly_mail")
WIKI_INDEX = "wiki_summaries"

DATA_DIR = Path("data")

TEAMS = [
    "CS팀", "DT팀", "EQUIP팀", "FA팀", "PE팀",
    "PI팀", "PROCESS팀", "QA팀", "TEST팀", "YIELD팀",
]


# ========== OpenSearch 클라이언트 ==========
def get_client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
        use_ssl=OPENSEARCH_USE_SSL,
        verify_certs=False,
        ssl_show_warn=False,
    )


def get_embedding_client() -> OpenAI:
    return OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def get_embedding(client: OpenAI, text: str) -> List[float]:
    text = text[:8000] if len(text) > 8000 else text
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return response.data[0].embedding


# ========== 인덱스 생성 ==========
def create_wiki_index(client: OpenSearch):
    """wiki_summaries 인덱스 생성 (kNN + Nori)"""
    if client.indices.exists(index=WIKI_INDEX):
        print(f"✅ 인덱스 '{WIKI_INDEX}' 이미 존재")
        return

    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "analysis": {
                "tokenizer": {
                    "nori_tokenizer": {
                        "type": "nori_tokenizer",
                        "decompound_mode": "mixed",
                    }
                },
                "analyzer": {
                    "korean": {
                        "type": "custom",
                        "tokenizer": "nori_tokenizer",
                        "filter": ["nori_readingform", "lowercase"],
                    }
                },
            },
        },
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": EMBEDDING_DIMENSION,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "faiss",
                        "parameters": {"ef_construction": 128, "m": 16},
                    },
                },
                "text": {
                    "type": "text",
                    "analyzer": "korean",
                    "search_analyzer": "korean",
                },
                "title": {
                    "type": "text",
                    "analyzer": "korean",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                # 메타데이터
                "summary_type": {"type": "keyword"},   # team-week / topic / overview / query_synthesis
                "team": {"type": "keyword"},
                "week": {"type": "keyword"},
                "topic": {"type": "keyword"},           # topic-timeline용
                "source_doc_ids": {"type": "keyword"},  # 원본 문서 ID 참조
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
            }
        },
    }

    client.indices.create(index=WIKI_INDEX, body=index_body)
    print(f"✅ 인덱스 '{WIKI_INDEX}' 생성 완료")


def delete_wiki_index(client: OpenSearch):
    if client.indices.exists(index=WIKI_INDEX):
        client.indices.delete(index=WIKI_INDEX)
        print(f"🗑️ 인덱스 '{WIKI_INDEX}' 삭제 완료")


# ========== 소스 데이터 조회 ==========
def get_available_weeks(client: OpenSearch) -> List[str]:
    """weekly_mail 인덱스에서 사용 가능한 주차 목록 조회"""
    body = {
        "size": 0,
        "aggs": {
            "weeks": {
                "terms": {"field": "week", "size": 100, "order": {"_key": "asc"}}
            }
        },
    }
    resp = client.search(index=SOURCE_INDEX, body=body)
    return [b["key"] for b in resp["aggregations"]["weeks"]["buckets"]]


def get_teams_for_week(client: OpenSearch, week: str) -> List[str]:
    """특정 주차에 데이터가 있는 팀 목록"""
    body = {
        "size": 0,
        "query": {"term": {"week": week}},
        "aggs": {
            "teams": {"terms": {"field": "team", "size": 20}}
        },
    }
    resp = client.search(index=SOURCE_INDEX, body=body)
    return [b["key"] for b in resp["aggregations"]["teams"]["buckets"]]


def fetch_chunks_for_team_week(
    client: OpenSearch, team: str, week: str, limit: int = 100
) -> List[Dict]:
    """특정 팀-주차의 raw chunk 조회"""
    body = {
        "size": limit,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"team": team}},
                    {"term": {"week": week}},
                ]
            }
        },
        "sort": [{"part_index": {"order": "asc"}}],
    }
    resp = client.search(index=SOURCE_INDEX, body=body)
    results = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        results.append({
            "id": hit["_id"],
            "text": src.get("text", ""),
            "team": src.get("team", ""),
            "week": src.get("week", ""),
            "mail_id": src.get("mail_id", ""),
            "part_index": src.get("part_index", 0),
        })
    return results


def fetch_all_chunks_for_week(client: OpenSearch, week: str, limit: int = 500) -> List[Dict]:
    """특정 주차의 전체 chunk 조회 (모든 팀)"""
    body = {
        "size": limit,
        "query": {"term": {"week": week}},
        "sort": [{"team": {"order": "asc"}}, {"part_index": {"order": "asc"}}],
    }
    resp = client.search(index=SOURCE_INDEX, body=body)
    results = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        results.append({
            "id": hit["_id"],
            "text": src.get("text", ""),
            "team": src.get("team", ""),
            "week": src.get("week", ""),
        })
    return results


# ========== LLM 요약 생성 ==========
def generate_team_week_summary(team: str, week: str, chunks: List[Dict]) -> str:
    """팀-주차 wiki 요약 생성"""
    if not chunks:
        return ""

    combined_text = "\n\n---\n\n".join([c["text"] for c in chunks])
    # 토큰 제한을 위해 자름
    if len(combined_text) > 30000:
        combined_text = combined_text[:30000] + "\n\n... (이하 생략)"

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

    system_prompt = """당신은 반도체 주간 업무 보고서 요약 전문가입니다.

주차 형식: YYYY-WW (예: 2026-02 = 2026년 제2주차, 월이 아님)

작성 원칙:
1. 원본 메일 내용만 기반으로 요약 (추측 금지)
2. 핵심 이슈, 진행 상황, 수치 중심
3. 기술 용어는 그대로 유지
4. 간결하고 구조화된 형태

출력 형식 — 아래 예시의 구조를 한 글자도 바꾸지 말고 그대로 복제하세요.
섹션 헤더는 반드시 `**숫자. 제목**` 형식이며, `#`, `##`, `###` 같은 markdown heading을 사용해서는 안 됩니다.
불릿은 `- ` 로만 시작합니다.

예시:
**1. 핵심 요약 (3줄 이내)**
- 이번 주 가장 중요한 내용.
- 두 번째 핵심 내용.
- 세 번째 핵심 내용.

**2. 주요 업무**
- **프로젝트/업무명**: 진행 상황, 수치, 결과.
- **프로젝트/업무명**: 진행 상황, 수치, 결과.

**3. 이슈 & 리스크**
- **이슈명**: 문제점, 지연 사항, 주의 필요 항목.
- **이슈명**: 문제점, 지연 사항, 주의 필요 항목.

**4. 핵심 키워드**
- 키워드1, 키워드2, 키워드3, ... (5~10개, 쉼표 구분)"""

    user_prompt = f"""[{team}] {week} 주차 보고서를 요약해주세요.

--- 원본 내용 ---
{combined_text}
--- 끝 ---

위 내용을 바탕으로 구조화된 요약을 작성하세요."""

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=1500,
    )
    return response.choices[0].message.content


def generate_weekly_overview(week: str, team_summaries: Dict[str, str]) -> str:
    """주차별 전체 팀 크로스 요약 생성"""
    if not team_summaries:
        return ""

    summaries_text = ""
    for team, summary in team_summaries.items():
        summaries_text += f"\n=== {team} ===\n{summary}\n"

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

    system_prompt = """당신은 반도체 조직의 주간 업무 현황을 종합하는 전문가입니다.

작성 원칙:
1. 각 팀 요약을 바탕으로 조직 전체 관점에서 종합
2. 팀 간 연관 이슈가 있으면 크로스 레퍼런스
3. 조직 차원의 핵심 이슈와 리스크 도출

출력 형식 — 아래 예시의 구조를 한 글자도 바꾸지 말고 그대로 복제하세요.
섹션 헤더는 반드시 `**숫자. 제목**` 형식이며, `#`, `##`, `###` 같은 markdown heading을 사용해서는 안 됩니다.
불릿은 `- ` 로만 시작하고, 제목과 설명 사이 구분자는 `: ` 를 사용합니다.
팀명/이슈명/리스크명은 반드시 `**...**` 로 볼드 처리합니다.

예시:
**1. 이번 주 조직 핵심 (3줄)**
- 첫 번째 핵심 요점.
- 두 번째 핵심 요점.
- 세 번째 핵심 요점.

**2. 팀별 하이라이트**
- **EQUIP팀**: 한 줄 핵심.
- **PE팀**: 한 줄 핵심.
- **PROCESS팀**: 한 줄 핵심.
- **QA팀**: 한 줄 핵심.
- **TEST팀**: 한 줄 핵심.
- **YIELD팀**: 한 줄 핵심.

**3. 크로스팀 이슈**
- **이슈 제목**: 설명.
- **이슈 제목**: 설명.

**4. 주요 리스크**
- **리스크 제목**: 설명.
- **리스크 제목**: 설명."""

    user_prompt = f"""{week} 주차 전체 팀 현황을 종합해주세요.

{summaries_text}

위 각 팀 요약을 바탕으로 조직 전체 관점의 종합 요약을 작성하세요."""

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=1500,
    )
    return response.choices[0].message.content


# ========== Wiki 문서 저장 ==========
def save_wiki_doc(
    os_client: OpenSearch,
    embed_client: OpenAI,
    text: str,
    title: str,
    summary_type: str,
    team: Optional[str] = None,
    week: Optional[str] = None,
    topic: Optional[str] = None,
    source_doc_ids: Optional[List[str]] = None,
    doc_id: Optional[str] = None,
):
    """wiki 문서를 OpenSearch에 저장"""
    if not text.strip():
        return

    embedding = get_embedding(embed_client, text)
    now = datetime.now(tz=__import__('datetime').timezone.utc).isoformat()

    doc = {
        "embedding": embedding,
        "text": text,
        "title": title,
        "summary_type": summary_type,
        "team": team,
        "week": week,
        "topic": topic,
        "source_doc_ids": source_doc_ids or [],
        "created_at": now,
        "updated_at": now,
    }

    # doc_id 지정 시 upsert (같은 팀-주차 요약은 덮어쓰기)
    if doc_id:
        # 기존 문서가 있으면 created_at 유지
        try:
            existing = os_client.get(index=WIKI_INDEX, id=doc_id)
            doc["created_at"] = existing["_source"].get("created_at", now)
        except Exception:
            pass
        os_client.index(index=WIKI_INDEX, id=doc_id, body=doc)
    else:
        os_client.index(index=WIKI_INDEX, body=doc)

    print(f"  💾 저장: {title}")


# ========== 백필 ==========
def backfill_team_week(
    os_client: OpenSearch,
    embed_client: OpenAI,
    team: str,
    week: str,
):
    """특정 팀-주차의 wiki 요약 생성 및 저장"""
    print(f"\n📝 [{team}] {week} 요약 생성 중...")

    chunks = fetch_chunks_for_team_week(os_client, team, week)
    if not chunks:
        print(f"  ⚠️ 데이터 없음: {team} {week}")
        return None

    summary = generate_team_week_summary(team, week, chunks)
    if not summary:
        print(f"  ⚠️ 요약 생성 실패: {team} {week}")
        return None

    doc_id = f"team-week_{week}_{team}"
    title = f"{week} {team} 주간 요약"
    source_ids = [c["id"] for c in chunks]

    save_wiki_doc(
        os_client, embed_client,
        text=summary,
        title=title,
        summary_type="team-week",
        team=team,
        week=week,
        source_doc_ids=source_ids,
        doc_id=doc_id,
    )

    return summary


def backfill_weekly_overview(
    os_client: OpenSearch,
    embed_client: OpenAI,
    week: str,
    team_summaries: Dict[str, str],
):
    """주차별 전체 팀 크로스 요약 생성 및 저장"""
    print(f"\n📋 {week} 전체 요약 생성 중...")

    overview = generate_weekly_overview(week, team_summaries)
    if not overview:
        print(f"  ⚠️ 전체 요약 생성 실패: {week}")
        return

    doc_id = f"overview_{week}"
    title = f"{week} 전체 팀 종합 요약"

    save_wiki_doc(
        os_client, embed_client,
        text=overview,
        title=title,
        summary_type="overview",
        week=week,
        doc_id=doc_id,
    )


def backfill_all(
    weeks: Optional[List[str]] = None,
    teams: Optional[List[str]] = None,
    skip_overview: bool = False,
    recreate: bool = False,
):
    """전체 백필 실행"""
    os_client = get_client()
    embed_client = get_embedding_client()

    if recreate:
        delete_wiki_index(os_client)
    create_wiki_index(os_client)

    # 대상 주차
    available_weeks = get_available_weeks(os_client)
    target_weeks = weeks if weeks else available_weeks
    target_teams = teams if teams else TEAMS

    print(f"🚀 백필 시작: {len(target_weeks)}개 주차 x {len(target_teams)}개 팀")
    print(f"   주차: {', '.join(target_weeks)}")
    print(f"   팀: {', '.join(target_teams)}")

    total = 0
    for week in target_weeks:
        week_summaries = {}

        for team in target_teams:
            try:
                summary = backfill_team_week(os_client, embed_client, team, week)
                if summary:
                    week_summaries[team] = summary
                    total += 1
                # API rate limit 방지
                time.sleep(1)
            except Exception as e:
                print(f"  ❌ 실패: {team} {week} - {e}")

        # 주차별 전체 요약
        if not skip_overview and week_summaries:
            try:
                backfill_weekly_overview(os_client, embed_client, week, week_summaries)
                total += 1
                time.sleep(1)
            except Exception as e:
                print(f"  ❌ 전체 요약 실패: {week} - {e}")

    print(f"\n✅ 백필 완료: {total}개 wiki 문서 생성")


# ========== Knowledge Accumulation ==========
def verify_answer_grounding(question: str, answer: str, source_chunks: str) -> bool:
    """LLM으로 답변이 소스 문서에 근거하는지 검증"""
    if not source_chunks or not source_chunks.strip():
        return False

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "당신은 답변의 사실 근거를 검증하는 전문가입니다. 답변이 소스 문서에 정확히 근거하는지 판단하세요."},
                {"role": "user", "content": f"""아래 답변이 소스 문서에 근거하는지 판단하세요.

소스 문서:
{source_chunks[:15000]}

질문: {question}
답변: {answer[:5000]}

판단 기준:
- 답변의 핵심 내용이 소스 문서에서 확인 가능하면 APPROVE
- 소스 문서에 없는 내용을 만들어냈거나, 핵심 사실이 틀리면 REJECT

APPROVE 또는 REJECT 한 단어만 출력하세요."""},
            ],
            temperature=0,
            max_tokens=10,
        )
        result = resp.choices[0].message.content.strip().upper()
        approved = "APPROVE" in result
        print(f"  {'✅' if approved else '❌'} [Wiki 검증] {result}")
        return approved
    except Exception as e:
        print(f"  ⚠️ [Wiki 검증] LLM 호출 실패, 저장 건너뜀: {e}")
        return False


def accumulate_query_result(
    os_client: OpenSearch,
    embed_client: OpenAI,
    question: str,
    answer: str,
    source_teams: Optional[List[str]] = None,
    source_weeks: Optional[List[str]] = None,
    source_chunks: Optional[str] = None,
):
    """쿼리 결과를 wiki에 축적 (knowledge evaporation 방지)"""
    # 저장 전 LLM 검증
    if not verify_answer_grounding(question, answer, source_chunks or ""):
        print("  🚫 [Knowledge Accumulation] 근거 검증 실패, 저장하지 않음")
        return
    text = f"질문: {question}\n\n답변:\n{answer}"
    title = f"Q&A: {question[:80]}"

    # 중복 체크: 유사 질문이 이미 있는지 확인
    query_embedding = get_embedding(embed_client, question)
    search_body = {
        "size": 1,
        "query": {
            "bool": {
                "must": [
                    {"term": {"summary_type": "query_synthesis"}},
                    {
                        "knn": {
                            "embedding": {
                                "vector": query_embedding,
                                "k": 1,
                            }
                        }
                    },
                ],
            }
        },
    }

    try:
        resp = os_client.search(index=WIKI_INDEX, body=search_body)
        if resp["hits"]["hits"]:
            top_score = resp["hits"]["hits"][0]["_score"]
            # cosine similarity > 0.92 이면 기존 문서 업데이트
            if top_score > 9.2:  # OpenSearch kNN score는 10 * cosine
                existing_id = resp["hits"]["hits"][0]["_id"]
                print(f"  🔄 기존 문서 업데이트: {existing_id} (score: {top_score:.2f})")
                save_wiki_doc(
                    os_client, embed_client,
                    text=text,
                    title=title,
                    summary_type="query_synthesis",
                    team=source_teams[0] if source_teams else None,
                    week=source_weeks[0] if source_weeks else None,
                    doc_id=existing_id,
                )
                return
    except Exception:
        pass

    # 새 문서 저장
    save_wiki_doc(
        os_client, embed_client,
        text=text,
        title=title,
        summary_type="query_synthesis",
        team=source_teams[0] if source_teams else None,
        week=source_weeks[0] if source_weeks else None,
    )


# ========== CLI ==========
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wiki Builder - OpenSearch wiki 요약 생성")
    parser.add_argument("--week", type=str, nargs="*", help="대상 주차 (예: 2025-48 2025-49)")
    parser.add_argument("--team", type=str, nargs="*", help="대상 팀 (예: YIELD팀 DT팀)")
    parser.add_argument("--skip-overview", action="store_true", help="전체 요약 생성 건너뛰기")
    parser.add_argument("--recreate", action="store_true", help="인덱스 재생성 (기존 데이터 삭제)")
    parser.add_argument("--list-weeks", action="store_true", help="사용 가능한 주차 목록 출력")

    args = parser.parse_args()

    if args.list_weeks:
        client = get_client()
        weeks = get_available_weeks(client)
        print(f"사용 가능한 주차 ({len(weeks)}개):")
        for w in weeks:
            teams = get_teams_for_week(client, w)
            print(f"  {w}: {', '.join(teams)}")
    else:
        backfill_all(
            weeks=args.week,
            teams=args.team,
            skip_overview=args.skip_overview,
            recreate=args.recreate,
        )

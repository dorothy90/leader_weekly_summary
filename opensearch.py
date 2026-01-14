"""
OpenSearch 조회 유틸리티
- 자주 사용하는 쿼리 예제 모음
- embed_vectordb.py의 인덱스 구조 기반
"""

import os
from typing import List, Dict, Any, Optional
from opensearchpy import OpenSearch
from openai import OpenAI

# ========== 설정 ==========
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "admin")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true"

INDEX_NAME = "weekly_mail"
EMBEDDING_MODEL = "text-embedding-3-small"


def get_client() -> OpenSearch:
    """OpenSearch 클라이언트"""
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
        use_ssl=OPENSEARCH_USE_SSL,
        verify_certs=False,
        ssl_show_warn=False,
    )


def get_embedding_client() -> OpenAI:
    """임베딩 클라이언트"""
    api_key = os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=api_key)


def get_embedding(text: str) -> List[float]:
    """텍스트 임베딩"""
    client = get_embedding_client()
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text[:8000])
    return response.data[0].embedding


# ========== 1. 기본 조회 ==========


def get_all_documents(limit: int = 100) -> List[Dict]:
    """전체 문서 조회"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": limit,
            "query": {"match_all": {}},
            "sort": [{"team": "asc"}, {"week": "desc"}],
        },
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


def get_document_by_id(doc_id: str) -> Optional[Dict]:
    """ID로 문서 조회"""
    client = get_client()
    try:
        response = client.get(index=INDEX_NAME, id=doc_id)
        return response["_source"]
    except:
        return None


def get_documents_by_mail_id(mail_id: str) -> List[Dict]:
    """특정 메일의 모든 파트 조회"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 100,
            "query": {"term": {"mail_id": mail_id}},
            "sort": [{"part_index": "asc"}],
        },
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


# ========== 2. 필터 조회 ==========


def get_by_team(team: str, limit: int = 100) -> List[Dict]:
    """팀별 문서 조회"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": limit,
            "query": {"term": {"team": team}},
            "sort": [{"week": "desc"}],
        },
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


def get_by_week(week: str, limit: int = 100) -> List[Dict]:
    """주차별 문서 조회"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": limit,
            "query": {"term": {"week": week}},
            "sort": [{"team": "asc"}],
        },
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


def get_by_team_and_week(team: str, week: str) -> List[Dict]:
    """팀 + 주차로 문서 조회"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 100,
            "query": {
                "bool": {
                    "must": [
                        {"term": {"team": team}},
                        {"term": {"week": week}},
                    ]
                }
            },
            "sort": [{"mail_id": "asc"}, {"part_index": "asc"}],
        },
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


# ========== 3. 검색 ==========


def search_keyword(
    query: str, team: str = None, week: str = None, limit: int = 10
) -> List[Dict]:
    """키워드 검색 (한국어 형태소 분석)"""
    client = get_client()

    filters = []
    if team:
        filters.append({"term": {"team": team}})
    if week:
        filters.append({"term": {"week": week}})

    body = {
        "size": limit,
        "query": {
            "bool": {
                "must": [{"match": {"text": {"query": query, "analyzer": "korean"}}}],
                "filter": filters,
            }
        },
        "_source": ["text", "team", "week", "mail_id", "part_index"],
    }

    response = client.search(index=INDEX_NAME, body=body)
    return [
        {**hit["_source"], "score": hit["_score"]} for hit in response["hits"]["hits"]
    ]


def search_vector(
    query: str, team: str = None, week: str = None, k: int = 5
) -> List[Dict]:
    """벡터 유사도 검색"""
    client = get_client()
    query_embedding = get_embedding(query)

    filters = []
    if team:
        filters.append({"term": {"team": team}})
    if week:
        filters.append({"term": {"week": week}})

    body = {
        "size": k,
        "query": {
            "bool": {
                "must": [{"knn": {"embedding": {"vector": query_embedding, "k": k}}}],
                "filter": filters,
            }
        },
        "_source": ["text", "team", "week", "mail_id", "part_index"],
    }

    response = client.search(index=INDEX_NAME, body=body)
    return [
        {**hit["_source"], "score": hit["_score"]} for hit in response["hits"]["hits"]
    ]


def search_phrase(phrase: str, team: str = None) -> List[Dict]:
    """정확한 구문 검색"""
    client = get_client()

    filters = []
    if team:
        filters.append({"term": {"team": team}})

    body = {
        "size": 20,
        "query": {
            "bool": {
                "must": [{"match_phrase": {"text": phrase}}],
                "filter": filters,
            }
        },
    }

    response = client.search(index=INDEX_NAME, body=body)
    return [hit["_source"] for hit in response["hits"]["hits"]]


def search_wildcard(pattern: str) -> List[Dict]:
    """와일드카드 검색 (예: *수율*, 개선*)"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 20,
            "query": {"wildcard": {"text": {"value": pattern}}},
        },
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


# ========== 4. 집계 (Aggregations) ==========


def count_by_team() -> Dict[str, int]:
    """팀별 문서 수"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 0,
            "aggs": {"teams": {"terms": {"field": "team", "size": 50}}},
        },
    )
    return {
        bucket["key"]: bucket["doc_count"]
        for bucket in response["aggregations"]["teams"]["buckets"]
    }


def count_by_week() -> Dict[str, int]:
    """주차별 문서 수"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 0,
            "aggs": {
                "weeks": {
                    "terms": {"field": "week", "size": 100, "order": {"_key": "desc"}}
                }
            },
        },
    )
    return {
        bucket["key"]: bucket["doc_count"]
        for bucket in response["aggregations"]["weeks"]["buckets"]
    }


def count_by_team_and_week() -> Dict[str, Dict[str, int]]:
    """팀별 + 주차별 문서 수 (크로스탭)"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 0,
            "aggs": {
                "teams": {
                    "terms": {"field": "team", "size": 50},
                    "aggs": {"weeks": {"terms": {"field": "week", "size": 100}}},
                }
            },
        },
    )

    result = {}
    for team_bucket in response["aggregations"]["teams"]["buckets"]:
        team = team_bucket["key"]
        result[team] = {
            week_bucket["key"]: week_bucket["doc_count"]
            for week_bucket in team_bucket["weeks"]["buckets"]
        }
    return result


def get_unique_teams() -> List[str]:
    """고유 팀 목록"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 0,
            "aggs": {"teams": {"terms": {"field": "team", "size": 100}}},
        },
    )
    return [bucket["key"] for bucket in response["aggregations"]["teams"]["buckets"]]


def get_unique_weeks() -> List[str]:
    """고유 주차 목록"""
    client = get_client()
    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 0,
            "aggs": {
                "weeks": {
                    "terms": {"field": "week", "size": 200, "order": {"_key": "desc"}}
                }
            },
        },
    )
    return [bucket["key"] for bucket in response["aggregations"]["weeks"]["buckets"]]


# ========== 5. 통계 ==========


def get_index_stats() -> Dict:
    """인덱스 통계"""
    client = get_client()
    stats = client.indices.stats(index=INDEX_NAME)
    count = client.count(index=INDEX_NAME)

    return {
        "total_documents": count["count"],
        "index_size": stats["indices"][INDEX_NAME]["total"]["store"]["size_in_bytes"],
        "index_size_mb": round(
            stats["indices"][INDEX_NAME]["total"]["store"]["size_in_bytes"]
            / 1024
            / 1024,
            2,
        ),
    }


def get_total_count() -> int:
    """전체 문서 수"""
    client = get_client()
    return client.count(index=INDEX_NAME)["count"]


def get_count_with_filter(team: str = None, week: str = None) -> int:
    """필터 조건 문서 수"""
    client = get_client()

    filters = []
    if team:
        filters.append({"term": {"team": team}})
    if week:
        filters.append({"term": {"week": week}})

    body = (
        {"query": {"bool": {"filter": filters}}}
        if filters
        else {"query": {"match_all": {}}}
    )
    return client.count(index=INDEX_NAME, body=body)["count"]


# ========== 6. 페이지네이션 ==========


def get_paginated(
    page: int = 1, page_size: int = 20, team: str = None, week: str = None
) -> Dict:
    """페이지네이션 조회"""
    client = get_client()

    filters = []
    if team:
        filters.append({"term": {"team": team}})
    if week:
        filters.append({"term": {"week": week}})

    from_idx = (page - 1) * page_size

    body = {
        "from": from_idx,
        "size": page_size,
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"week": "desc"}, {"team": "asc"}],
    }

    response = client.search(index=INDEX_NAME, body=body)
    total = response["hits"]["total"]["value"]

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
        "documents": [hit["_source"] for hit in response["hits"]["hits"]],
    }


# ========== 7. 삭제 ==========


def delete_by_week(week: str) -> int:
    """주차별 문서 삭제"""
    client = get_client()
    response = client.delete_by_query(
        index=INDEX_NAME,
        body={"query": {"term": {"week": week}}},
    )
    return response["deleted"]


def delete_by_team_and_week(team: str, week: str) -> int:
    """팀 + 주차 문서 삭제"""
    client = get_client()
    response = client.delete_by_query(
        index=INDEX_NAME,
        body={
            "query": {
                "bool": {
                    "must": [
                        {"term": {"team": team}},
                        {"term": {"week": week}},
                    ]
                }
            }
        },
    )
    return response["deleted"]


# ========== 8. 유틸리티 ==========


def check_connection() -> bool:
    """연결 확인"""
    try:
        client = get_client()
        info = client.info()
        print(
            f"✅ 연결 성공: {info['version']['distribution']} {info['version']['number']}"
        )
        return True
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        return False


def print_summary():
    """인덱스 요약 출력"""
    print("=" * 50)
    print("📊 OpenSearch 인덱스 요약")
    print("=" * 50)

    stats = get_index_stats()
    print(f"전체 문서 수: {stats['total_documents']:,}개")
    print(f"인덱스 크기: {stats['index_size_mb']} MB")

    print("\n📁 팀별 문서 수:")
    for team, count in count_by_team().items():
        print(f"  - {team}: {count}개")

    print("\n📅 주차별 문서 수:")
    for week, count in count_by_week().items():
        print(f"  - {week}: {count}개")


# ========== 9. 메일 유형별 집계 (Tool Calling용) ==========


def count_mails_by_team(mail_type: str = None, week: str = None) -> Dict[str, int]:
    """팀별 고유 메일 수 (청크가 아닌 실제 메일 단위)

    Args:
        mail_type: 메일 유형 필터 ("weekly_report" / "other" / None=전체)
        week: 주차 필터 (예: "2025-48")

    Returns:
        팀별 메일 수 딕셔너리
    """
    client = get_client()

    filters = []
    if mail_type:
        filters.append({"term": {"mail_type": mail_type}})
    if week:
        filters.append({"term": {"week": week}})

    body = {
        "size": 0,
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "aggs": {
            "teams": {
                "terms": {"field": "team", "size": 50},
                "aggs": {
                    # mail_id로 고유 메일 수 집계 (청크 중복 제거)
                    "unique_mails": {"cardinality": {"field": "mail_id"}}
                },
            }
        },
    }

    response = client.search(index=INDEX_NAME, body=body)

    return {
        bucket["key"]: bucket["unique_mails"]["value"]
        for bucket in response["aggregations"]["teams"]["buckets"]
    }


def count_weekly_reports_by_team(week: str = None) -> Dict[str, int]:
    """주간보고 메일 팀별 count

    Args:
        week: 주차 필터 (예: "2025-48"). None이면 전체 기간

    Returns:
        팀별 주간보고 메일 수
    """
    return count_mails_by_team(mail_type="weekly_report", week=week)


def count_daily_reports_by_team(week: str = None) -> Dict[str, int]:
    """일일보고 메일 팀별 count

    Args:
        week: 주차 필터 (예: "2025-48"). None이면 전체 기간

    Returns:
        팀별 일일보고 메일 수
    """
    return count_mails_by_team(mail_type="daily_report", week=week)


def count_other_mails_by_team(week: str = None, team: str = None) -> Dict[str, int]:
    """주간보고 외 메일 팀별 count

    Args:
        week: 주차 필터
        team: 특정 팀 필터 (None이면 전체 팀)

    Returns:
        팀별 기타 메일 수 (team 지정시 해당 팀만)
    """
    result = count_mails_by_team(mail_type="other", week=week)

    if team:
        return {team: result.get(team, 0)}
    return result


def get_missing_teams(week: str) -> List[str]:
    """주간보고 미제출 팀 목록

    Args:
        week: 주차 (필수)

    Returns:
        미제출 팀 리스트
    """
    # 전체 팀 목록
    all_teams = set(get_unique_teams())

    # 해당 주차에 주간보고 제출한 팀
    submitted = set(count_weekly_reports_by_team(week=week).keys())

    # 미제출 팀
    missing = all_teams - submitted
    return sorted(list(missing))


def get_mail_type_summary(week: str = None) -> Dict[str, Any]:
    """메일 유형별 요약 통계

    Args:
        week: 주차 필터 (None이면 전체)

    Returns:
        주간보고/기타 메일 팀별 현황 및 총계
    """
    weekly = count_weekly_reports_by_team(week=week)
    other = count_other_mails_by_team(week=week)

    return {
        "week": week or "all",
        "weekly_report": {
            "by_team": weekly,
            "total": sum(weekly.values()),
        },
        "other": {
            "by_team": other,
            "total": sum(other.values()),
        },
    }


def count_by_mail_type_and_team() -> Dict[str, Dict[str, int]]:
    """메일 유형별 + 팀별 문서(메일) 수"""
    client = get_client()

    response = client.search(
        index=INDEX_NAME,
        body={
            "size": 0,
            "aggs": {
                "mail_types": {
                    "terms": {"field": "mail_type", "size": 10},
                    "aggs": {
                        "teams": {
                            "terms": {"field": "team", "size": 50},
                            "aggs": {
                                "unique_mails": {"cardinality": {"field": "mail_id"}}
                            },
                        }
                    },
                }
            },
        },
    )

    result = {}
    for type_bucket in response["aggregations"]["mail_types"]["buckets"]:
        mail_type = type_bucket["key"]
        result[mail_type] = {
            team_bucket["key"]: team_bucket["unique_mails"]["value"]
            for team_bucket in type_bucket["teams"]["buckets"]
        }
    return result


# ========== 실행 예제 ==========

if __name__ == "__main__":
    # 연결 확인
    if not check_connection():
        exit(1)

    # 인덱스 요약
    print_summary()

    # 예제: 팀별 조회
    print("\n" + "=" * 50)
    print("🔍 FA팀 문서 조회 (최근 5개)")
    docs = get_by_team("FA팀", limit=5)
    for doc in docs:
        print(f"  [{doc['week']}] {doc['mail_id']} - {doc['text'][:50]}...")

    # 예제: 키워드 검색
    print("\n" + "=" * 50)
    print("🔍 키워드 검색: '수율'")
    results = search_keyword("수율", limit=3)
    for r in results:
        print(f"  [{r['team']}] Score: {r['score']:.2f} - {r['text'][:50]}...")

    # 예제: 벡터 검색
    # print("\n" + "=" * 50)
    # print("🔍 벡터 검색: '공정 개선 방안'")
    # results = search_vector("공정 개선 방안", k=3)
    # for r in results:
    #     print(f"  [{r['team']}] Score: {r['score']:.2f} - {r['text'][:50]}...")

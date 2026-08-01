"""
4단계: OpenSearch Vector DB 임베딩
- combined.txt → 5000자 오버랩 청킹 → Vector DB (type: original_part)
- 메타데이터 포함 저장
- chunks.json은 Layer1/Layer2 생성용으로만 사용 (임베딩 안 함)
"""

import os
import re
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional

from opensearchpy import OpenSearch, helpers
from openai import OpenAI

from dotenv import load_dotenv

from app.content.mail import (
    mail_content_id,
    mail_content_locator,
    safe_mail_log_context,
)

load_dotenv()

# ========== 설정 ==========
# OpenSearch 설정
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true"

# 임베딩 설정
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"  # OpenAI 임베딩 모델
EMBEDDING_DIMENSION = 4096  # text-embedding-3-small 차원

# 인덱스 설정
INDEX_NAME = "weekly_mail"
DATA_DIR = Path("data")

# 청킹 설정
CHUNK_SIZE = 1500  # 1500자 (context 효율화)
CHUNK_OVERLAP = 300  # 300자 오버랩 (20%)


# ========== OpenSearch 클라이언트 ==========
def get_opensearch_client() -> OpenSearch:
    """OpenSearch 클라이언트 생성"""
    client = OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
        use_ssl=OPENSEARCH_USE_SSL,
        verify_certs=False,
        ssl_show_warn=False,
    )
    return client


# ========== 임베딩 클라이언트 ==========
def get_embedding_client() -> OpenAI:
    """OpenAI 임베딩 클라이언트 (OpenRouter 또는 OpenAI 직접 사용)"""
    # OpenAI API 직접 사용 (임베딩은 OpenRouter에서 지원 안 될 수 있음)
    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL")
    return OpenAI(api_key=api_key, base_url=base_url)


def get_embedding(text: str, client: OpenAI) -> List[float]:
    """텍스트를 임베딩 벡터로 변환"""
    # 텍스트가 너무 길면 자르기 (토큰 제한)
    max_chars = 8000  # 대략적인 제한
    if len(text) > max_chars:
        text = text[:max_chars]

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def get_embeddings_batch(texts: List[str], client: OpenAI) -> List[List[float]]:
    """배치 임베딩"""
    # 텍스트 길이 제한
    max_chars = 8000
    truncated_texts = [t[:max_chars] if len(t) > max_chars else t for t in texts]

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=truncated_texts,
    )
    return [item.embedding for item in response.data]


# ========== 텍스트 전처리 ==========
def clean_table_garbage(text: str) -> str:
    """표 관련 garbage 패턴 정제 (Vision 출력 후처리)

    - 연속된 | 패턴 제거 (|||||| 등)
    - 빈 markdown 표 행 제거
    - 반복 패턴 압축
    """
    if not text:
        return text

    # 1. 연속된 | 문자 축소 (|||| → |)
    text = re.sub(r"\|{2,}", "|", text)

    # 2. 빈 테이블 셀만 있는 패턴 제거 (| | | | 등)
    text = re.sub(r"\|(\s*\|)+", "|", text)

    # 3. 빈 markdown 표 행 제거 (| 만 있거나 |---|---| 형태)
    text = re.sub(r"^\s*\|[\s\|\-:]*\|\s*$", "", text, flags=re.MULTILINE)

    # 4. 단독 | 또는 |만 있는 줄 제거
    text = re.sub(r"^\s*\|?\s*$", "", text, flags=re.MULTILINE)

    # 5. 연속된 빈 줄 압축
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 6. 동일 패턴 5회 이상 반복 제거 (예: "| | |\n" 반복)
    text = re.sub(r"(.{5,50}?)\1{4,}", r"\1", text)

    return text.strip()


# ========== 텍스트 청킹 ==========
def split_text_with_overlap(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[str]:
    """텍스트를 오버랩 청킹으로 분리

    Args:
        text: 원본 텍스트
        chunk_size: 청크 크기 (기본 5000자)
        overlap: 오버랩 크기 (기본 1000자)

    Returns:
        청크 리스트
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        # 청크 끝 위치 계산
        end = start + chunk_size

        if end >= text_len:
            # 마지막 청크
            chunks.append(text[start:])
            break

        # 문장 경계에서 자르기 시도 (마침표, 줄바꿈)
        # 청크 끝에서 역방향으로 경계 찾기
        best_break = end
        for sep in ["\n\n", "\n", ". ", "。", "? ", "! "]:
            # 청크 마지막 20%에서 경계 찾기
            search_start = end - int(chunk_size * 0.2)
            pos = text.rfind(sep, search_start, end)
            if pos > start:
                best_break = pos + len(sep)
                break

        chunks.append(text[start:best_break])

        # 다음 시작 위치 (오버랩 적용)
        start = best_break - overlap
        if start < 0:
            start = 0

    return chunks


# ========== 인덱스 관리 ==========
def create_index(client: OpenSearch, index_name: str = INDEX_NAME):
    """k-NN 벡터 인덱스 생성 (한국어 Nori 분석기 포함)"""

    # 인덱스가 이미 있으면 스킵
    if client.indices.exists(index=index_name):
        print(f"✅ 인덱스 '{index_name}' 이미 존재")
        return

    # 인덱스 매핑 정의 (한국어 Nori 분석기 포함)
    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            # 한국어 형태소 분석기 설정
            "analysis": {
                "tokenizer": {
                    "nori_tokenizer": {
                        "type": "nori_tokenizer",
                        "decompound_mode": "mixed",  # 복합어 분해
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
                # 벡터 필드 (의미 기반 검색)
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
                # 텍스트 필드 (한국어 형태소 분석 적용)
                "text": {
                    "type": "text",
                    "analyzer": "korean",
                    "search_analyzer": "korean",
                },
                # 메타데이터 필드
                "type": {"type": "keyword"},  # original_part (5000자 청킹)
                "team": {"type": "keyword"},
                "week": {"type": "keyword"},
                "mail_id": {"type": "keyword"},
                "user_id": {"type": "keyword"},
                "content_id": {"type": "keyword"},
                "document_locator": {"type": "keyword"},
                "part_index": {"type": "integer"},  # 청크 파트 인덱스
                "total_parts": {"type": "integer"},  # 총 파트 수
                # 추가 필드 (통계/분류용)
                "subject": {
                    "type": "text",
                    "analyzer": "korean",
                    "fields": {"keyword": {"type": "keyword"}},  # 정확한 매칭용
                },
                "mail_type": {"type": "keyword"},  # weekly_report / other
            }
        },
    }

    client.indices.create(index=index_name, body=index_body)
    print(f"✅ 인덱스 '{index_name}' 생성 완료 (한국어 Nori 분석기 적용)")


def delete_index(client: OpenSearch, index_name: str = INDEX_NAME):
    """인덱스 삭제"""
    if client.indices.exists(index=index_name):
        client.indices.delete(index=index_name)
        print(f"🗑️ 인덱스 '{index_name}' 삭제 완료")


# ========== 문서 저장 ==========
def index_original_mail(
    client: OpenSearch,
    embedding_client: OpenAI,
    mail_dir: Path,
    index_name: str = INDEX_NAME,
) -> Optional[Dict]:
    """원본 메일 (combined.txt) 5000자 오버랩 청킹 후 임베딩 및 저장"""

    # combined.txt 읽기
    combined_path = mail_dir / "combined.txt"
    if not combined_path.exists():
        return None

    combined_text = combined_path.read_text(encoding="utf-8")
    if not combined_text.strip():
        return None

    # 표 garbage 전처리 (Vision 출력 정제)
    combined_text = clean_table_garbage(combined_text)
    if not combined_text.strip():
        return None

    # meta.json 읽기
    meta_path = mail_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    user_id = require_document_user_id(meta)
    team = meta.get("team", "unknown")
    week = meta.get("week", "unknown")
    subject = meta.get("subject", "")
    mail_type = meta.get("mail_type", "other")  # weekly_report / other
    mail_id = str(meta.get("mail_id") or mail_dir.name)
    content_id = mail_content_id(user_id, mail_id)
    locator = mail_content_locator(content_id)

    # 5000자 오버랩 청킹
    chunks = split_text_with_overlap(combined_text)

    if not chunks:
        return None

    # 배치 임베딩
    embeddings = get_embeddings_batch(chunks, embedding_client)

    # 벌크 저장을 위한 문서 준비
    actions = []
    for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
        doc_id = owner_scoped_chunk_id(user_id, mail_id, idx)

        doc = {
            "_index": index_name,
            "_id": doc_id,
            "_source": {
                "text": chunk_text,
                "embedding": embedding,
                "type": "original_part",
                "team": team,
                "week": week,
                "mail_id": mail_id,
                "user_id": user_id,
                "content_id": content_id,
                "document_locator": locator,
                "part_index": idx,
                "total_parts": len(chunks),
                "subject": subject,
                "mail_type": mail_type,
            },
        }
        actions.append(doc)

    # 벌크 저장
    if actions:
        helpers.bulk(client, actions)

    return {
        "doc_id": hashlib.sha256(
            f"legacy-mail-v2\x1f{user_id}\x1f{mail_id}".encode()
        ).hexdigest(),
        "user_id": user_id,
        "team": team,
        "week": week,
        "text_length": len(combined_text),
        "parts_count": len(chunks),
    }


def require_document_user_id(meta: Dict[str, Any]) -> str:
    """Require source metadata ownership; never infer it from folders or teams."""
    user_id = str(meta.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("meta.json must contain user_id before indexing")
    return user_id


def owner_scoped_chunk_id(user_id: str, mail_id: str, part_index: int) -> str:
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("user_id is required for document IDs")
    payload = f"legacy-chunk-v2\x1f{owner}\x1f{mail_id}\x1f{part_index}"
    return hashlib.sha256(payload.encode()).hexdigest()


# [DEPRECATED] LLM 청크 임베딩은 더 이상 사용하지 않음
# chunks.json은 Layer1/Layer2 생성용으로만 사용
# def index_chunks(
#     client: OpenSearch,
#     embedding_client: OpenAI,
#     mail_dir: Path,
#     index_name: str = INDEX_NAME,
# ) -> Optional[Dict]:
#     """청크 (chunks.json) 임베딩 및 저장 - DEPRECATED"""
#     pass


# ========== 메일 폴더 처리 ==========
def process_mail_folder(
    client: OpenSearch,
    embedding_client: OpenAI,
    mail_dir: Path,
    index_name: str = INDEX_NAME,
) -> Dict:
    """단일 메일 폴더의 원본 5000자 오버랩 청킹 임베딩"""

    print(f"\n📂 처리 중: {safe_mail_log_context(mail_dir)}")

    result = {
        "original": None,
    }

    # 원본 5000자 오버랩 청킹 임베딩
    original_result = index_original_mail(
        client, embedding_client, mail_dir, index_name
    )
    if original_result:
        parts_info = f"{original_result['parts_count']}개 파트"
        print(f"   ✅ 저장: {original_result['doc_id']} ({parts_info})")
        result["original"] = original_result
    else:
        print(f"   ⚠️ 원본 없음 (combined.txt)")

    return result


def process_all(recreate_index: bool = False, week=None):
    """메일 폴더 처리 (week 지정 시 해당 주차만)"""
    print("=" * 50)
    print("OpenSearch Vector DB 임베딩 (5000자 오버랩 청킹)")
    print("=" * 50)

    # 클라이언트 초기화
    os_client = get_opensearch_client()
    embedding_client = get_embedding_client()

    # 연결 확인
    try:
        info = os_client.info()
        print(
            f"✅ OpenSearch 연결 성공: {info['version']['distribution']} {info['version']['number']}"
        )
    except Exception as e:
        print(f"❌ OpenSearch 연결 실패: {type(e).__name__}")
        return

    # 인덱스 생성/재생성
    if recreate_index:
        delete_index(os_client)
    create_index(os_client)

    # 데이터 폴더 확인
    if not DATA_DIR.exists():
        print("❌ configured data directory is missing")
        return

    # owner-scoped and legacy mail folders are both discovered; metadata remains
    # the sole ownership source and ownerless legacy folders fail closed.
    mail_folders = list(DATA_DIR.glob("**/mail_*"))
    if week:
        mail_folders = [
            folder
            for folder in mail_folders
            if (folder / "meta.json").exists()
            and json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("week")
            == week
        ]
    print(f"\n📁 발견된 메일 폴더: {len(mail_folders)}개")

    stats = {
        "processed": 0,
        "mails": 0,
        "parts": 0,
        "failed": 0,
    }
    owner_weeks: Dict[str, set[str]] = {}

    for mail_dir in mail_folders:
        if not mail_dir.is_dir():
            continue

        try:
            result = process_mail_folder(os_client, embedding_client, mail_dir)
            stats["processed"] += 1

            if result["original"]:
                stats["mails"] += 1
                stats["parts"] += result["original"]["parts_count"]
                owner = result["original"]["user_id"]
                owner_weeks.setdefault(owner, set()).add(result["original"]["week"])

        except Exception as e:
            print(
                f"   ❌ 처리 실패: {safe_mail_log_context(mail_dir)} "
                f"error={type(e).__name__}"
            )
            stats["failed"] += 1

    # 인덱스 새로고침
    os_client.indices.refresh(index=INDEX_NAME)

    # 결과 요약
    print("\n" + "=" * 50)
    print("✅ 임베딩 완료")
    print("=" * 50)
    print(f"   처리된 폴더: {stats['processed']}개")
    print(f"   임베딩된 메일: {stats['mails']}개")
    print(f"   총 파트 수: {stats['parts']}개 (5000자 청킹)")
    print(f"   실패: {stats['failed']}개")

    # Wiki 요약 자동 생성 (임베딩된 주차 대상)
    if stats["mails"] > 0:
        try:
            processed_weeks = {
                processed_week
                for weeks_for_owner in owner_weeks.values()
                for processed_week in weeks_for_owner
            }

            if processed_weeks:
                print(f"\n📖 Wiki 요약 자동 생성 시작: {len(processed_weeks)}개 주차")
                from wiki_builder import backfill_all
                for owner, weeks_for_owner in sorted(owner_weeks.items()):
                    backfill_all(user_id=owner, weeks=sorted(weeks_for_owner))
        except Exception as e:
            print(f"⚠️ Wiki 요약 생성 실패 (무시): {type(e).__name__}")


# ========== 검색 함수 ==========
def search_vector(
    query: str,
    doc_type: Optional[str] = None,  # "original_part" (5000자 청킹), None (전체)
    team: Optional[str] = None,
    week: Optional[str] = None,
    domain: Optional[str] = None,
    tech: Optional[str] = None,
    k: int = 5,
) -> List[Dict]:
    """벡터 유사도 검색 (의미 기반)"""

    os_client = get_opensearch_client()
    embedding_client = get_embedding_client()

    # 쿼리 임베딩
    query_embedding = get_embedding(query, embedding_client)

    # 필터 구성
    filters = []
    if doc_type:
        filters.append({"term": {"type": doc_type}})
    if team:
        filters.append({"term": {"team": team}})
    if week:
        filters.append({"term": {"week": week}})
    if domain:
        filters.append({"term": {"domain": domain}})
    if tech:
        filters.append({"term": {"tech": tech}})

    # 검색 쿼리
    search_body = {
        "size": k,
        "query": {
            "bool": {
                "must": [{"knn": {"embedding": {"vector": query_embedding, "k": k}}}],
                "filter": filters if filters else [],
            }
        },
    }

    response = os_client.search(index=INDEX_NAME, body=search_body)

    results = []
    for hit in response["hits"]["hits"]:
        results.append(
            {
                "score": hit["_score"],
                "text": hit["_source"]["text"][:200] + "...",
                "full_text": hit["_source"]["text"],
                "type": hit["_source"]["type"],
                "team": hit["_source"]["team"],
                "week": hit["_source"]["week"],
                "mail_id": hit["_source"].get("mail_id", ""),
                "document_locator": hit["_source"].get("document_locator", ""),
                "part_index": hit["_source"].get("part_index"),
                "total_parts": hit["_source"].get("total_parts"),
            }
        )

    return results


def search_keyword(
    query: str,
    doc_type: Optional[str] = None,
    team: Optional[str] = None,
    week: Optional[str] = None,
    domain: Optional[str] = None,
    tech: Optional[str] = None,
    k: int = 5,
) -> List[Dict]:
    """키워드 검색 (한국어 형태소 분석 기반 BM25)"""

    os_client = get_opensearch_client()

    # 필터 구성
    filters = []
    if doc_type:
        filters.append({"term": {"type": doc_type}})
    if team:
        filters.append({"term": {"team": team}})
    if week:
        filters.append({"term": {"week": week}})
    if domain:
        filters.append({"term": {"domain": domain}})
    if tech:
        filters.append({"term": {"tech": tech}})

    # BM25 키워드 검색 (Nori 형태소 분석 자동 적용)
    search_body = {
        "size": k,
        "query": {
            "bool": {
                "must": [{"match": {"text": {"query": query, "analyzer": "korean"}}}],
                "filter": filters if filters else [],
            }
        },
    }

    response = os_client.search(index=INDEX_NAME, body=search_body)

    results = []
    for hit in response["hits"]["hits"]:
        results.append(
            {
                "score": hit["_score"],
                "text": hit["_source"]["text"][:200] + "...",
                "full_text": hit["_source"]["text"],
                "type": hit["_source"]["type"],
                "team": hit["_source"]["team"],
                "week": hit["_source"]["week"],
                "mail_id": hit["_source"].get("mail_id", ""),
                "document_locator": hit["_source"].get("document_locator", ""),
                "part_index": hit["_source"].get("part_index"),
                "total_parts": hit["_source"].get("total_parts"),
            }
        )

    return results


def search_hybrid(
    query: str,
    doc_type: Optional[str] = None,
    team: Optional[str] = None,
    week: Optional[str] = None,
    domain: Optional[str] = None,
    tech: Optional[str] = None,
    k: int = 5,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> List[Dict]:
    """하이브리드 검색 (벡터 + 키워드)

    최종 점수 = (벡터 유사도 × vector_weight) + (키워드 BM25 × keyword_weight)
    """

    # 1. 벡터 검색
    vector_results = search_vector(query, doc_type, team, week, domain, tech, k=k * 2)

    # 2. 키워드 검색
    keyword_results = search_keyword(query, doc_type, team, week, domain, tech, k=k * 2)

    # 3. 점수 정규화 및 병합
    score_map = {}  # doc_key -> {vector_score, keyword_score, doc}

    # 벡터 점수 정규화
    if vector_results:
        max_vector = max(r["score"] for r in vector_results)
        for r in vector_results:
            key = f"{r['team']}_{r['week']}_{r['type']}_{r['text'][:50]}"
            normalized_score = r["score"] / max_vector if max_vector > 0 else 0
            score_map[key] = {
                "vector_score": normalized_score,
                "keyword_score": 0,
                "doc": r,
            }

    # 키워드 점수 정규화 및 병합
    if keyword_results:
        max_keyword = max(r["score"] for r in keyword_results)
        for r in keyword_results:
            key = f"{r['team']}_{r['week']}_{r['type']}_{r['text'][:50]}"
            normalized_score = r["score"] / max_keyword if max_keyword > 0 else 0
            if key in score_map:
                score_map[key]["keyword_score"] = normalized_score
            else:
                score_map[key] = {
                    "vector_score": 0,
                    "keyword_score": normalized_score,
                    "doc": r,
                }

    # 4. 최종 점수 계산 및 정렬
    final_results = []
    for key, data in score_map.items():
        final_score = (
            data["vector_score"] * vector_weight
            + data["keyword_score"] * keyword_weight
        )
        doc = data["doc"]
        doc["score"] = final_score
        doc["vector_score"] = data["vector_score"]
        doc["keyword_score"] = data["keyword_score"]
        final_results.append(doc)

    # 점수 내림차순 정렬
    final_results.sort(key=lambda x: x["score"], reverse=True)

    return final_results[:k]


# 기존 함수 별칭 (호환성)
search_similar = search_vector


def run_search(
    query: str,
    mode: str = "hybrid",
    doc_type: Optional[str] = None,
    team: Optional[str] = None,
    week: Optional[str] = None,
):
    """검색 실행

    Args:
        query: 검색어
        mode: 검색 모드 ("vector", "keyword", "hybrid")
        doc_type: 문서 타입 필터 ("original_part" 또는 None)
        team: 팀 필터
        week: 주차 필터
    """
    print(f"\n🔍 검색: '{query}' (모드: {mode})")

    if mode == "vector":
        results = search_vector(query, doc_type=doc_type, team=team, week=week)
    elif mode == "keyword":
        results = search_keyword(query, doc_type=doc_type, team=team, week=week)
    else:  # hybrid
        results = search_hybrid(query, doc_type=doc_type, team=team, week=week)

    for i, r in enumerate(results, 1):
        score_info = f"Score: {r['score']:.4f}"
        if mode == "hybrid":
            score_info += f" (벡터: {r.get('vector_score', 0):.2f}, 키워드: {r.get('keyword_score', 0):.2f})"
        print(f"\n[{i}] {score_info}")
        part_info = ""
        if r.get("total_parts") and r.get("total_parts") > 1:
            part_info = f" | Part: {r['part_index']+1}/{r['total_parts']}"
        print(
            f"    Type: {r['type']} | Team: {r['team']} | Week: {r['week']}{part_info}"
        )
        print(f"    {r['text']}")

    return results


if __name__ == "__main__":
    # ========== 실행 설정 ==========
    # 임베딩 설정
    RECREATE_INDEX = True  # True: 인덱스 재생성

    # 검색 설정 (SEARCH_QUERY가 None이 아니면 검색 실행)
    SEARCH_QUERY = None  # 검색어 (예: "수율 개선")
    SEARCH_MODE = "hybrid"  # "vector", "keyword", "hybrid"
    SEARCH_TYPE = None  # "original_part" 또는 None
    SEARCH_TEAM = None  # 팀 필터 (예: "FA팀")
    SEARCH_WEEK = None  # 주차 필터 (예: "2025-48")

    # 실행
    if SEARCH_QUERY:
        run_search(
            query=SEARCH_QUERY,
            mode=SEARCH_MODE,
            doc_type=SEARCH_TYPE,
            team=SEARCH_TEAM,
            week=SEARCH_WEEK,
        )
    else:
        process_all(recreate_index=RECREATE_INDEX)

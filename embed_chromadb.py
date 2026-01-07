"""
ChromaDB 테스트용 임베딩 스크립트
- combined.txt → 5000자 오버랩 청킹 → original_part 타입으로 저장
- chunks.json은 Layer1/Layer2 생성용으로만 사용 (임베딩 안 함)
- 로컬 임베딩 모델 사용 (sentence-transformers)
"""

import json
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

import chromadb
from chromadb.utils import embedding_functions

# ========== 설정 ==========
DATA_DIR = Path("data")
CHROMA_DIR = Path("chroma_db")
COLLECTION_NAME = "weekly_mail"

# 청킹 설정
CHUNK_SIZE = 5000  # 5000자
CHUNK_OVERLAP = 1000  # 1000자 오버랩 (20%)

# 팀 목록
TEAMS = [
    "CS팀",
    "DT팀",
    "EQUIP팀",
    "FA팀",
    "PE팀",
    "PI팀",
    "PROCESS팀",
    "QA팀",
    "TEST팀",
    "YIELD팀",
]


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


class ChromaDBClient:
    """ChromaDB 클라이언트 (테스트용 - 로컬 임베딩 사용)"""

    def __init__(self, persist_dir: str = str(CHROMA_DIR)):
        """ChromaDB 클라이언트 초기화"""
        self.client = chromadb.PersistentClient(path=persist_dir)
        # 로컬 임베딩 모델 사용 (sentence-transformers)
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self._get_or_create_collection()

    def _get_or_create_collection(self):
        """컬렉션 가져오기 또는 생성"""
        return self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self.embedding_fn,
            metadata={"description": "Weekly mail embeddings"},
        )

    def add_document(
        self,
        doc_id: str,
        text: str,
        metadata: Dict,
    ):
        """문서 추가 (ChromaDB가 자동으로 임베딩)"""
        self.collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )

    def search_by_filter(
        self,
        team: Optional[str] = None,
        week: Optional[str] = None,
        doc_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """필터 기반 조회"""
        conditions = []
        if team:
            conditions.append({"team": team})
        if week:
            conditions.append({"week": week})
        if doc_type:
            conditions.append({"type": doc_type})

        # 여러 조건이 있으면 $and 사용
        if len(conditions) > 1:
            where = {"$and": conditions}
        elif len(conditions) == 1:
            where = conditions[0]
        else:
            where = None

        results = self.collection.get(
            where=where,
            limit=limit,
            include=["documents", "metadatas"],
        )

        docs = []
        for i, doc_id in enumerate(results["ids"]):
            docs.append(
                {
                    "id": doc_id,
                    "text": results["documents"][i],
                    "metadata": results["metadatas"][i],
                }
            )

        return docs

    def search_similar(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        doc_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict]:
        """유사도 검색"""
        conditions = []
        if team:
            conditions.append({"team": team})
        if week:
            conditions.append({"week": week})
        if doc_type:
            conditions.append({"type": doc_type})

        # 여러 조건이 있으면 $and 사용
        if len(conditions) > 1:
            where = {"$and": conditions}
        elif len(conditions) == 1:
            where = conditions[0]
        else:
            where = None

        results = self.collection.query(
            query_texts=[query],  # ChromaDB가 자동으로 임베딩
            where=where,
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )

        docs = []
        for i, doc_id in enumerate(results["ids"][0]):
            docs.append(
                {
                    "id": doc_id,
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                }
            )

        return docs

    def get_stats(self) -> Dict:
        """통계 조회"""
        return {
            "total_documents": self.collection.count(),
        }

    def clear_collection(self):
        """컬렉션 초기화"""
        self.client.delete_collection(COLLECTION_NAME)
        self.collection = self._get_or_create_collection()


def embed_week(week: str, client: ChromaDBClient):
    """특정 주차 데이터 임베딩 (5000자 오버랩 청킹)"""
    week_dir = DATA_DIR / week

    if not week_dir.exists():
        print(f"❌ 주차 폴더 없음: {week_dir}")
        return

    print(f"\n📅 {week} 주차 데이터 임베딩 (5000자 오버랩 청킹)")
    print("-" * 40)

    total_mails = 0
    total_parts = 0

    for team_dir in week_dir.iterdir():
        if not team_dir.is_dir():
            continue

        team = team_dir.name
        if team not in TEAMS:
            continue

        for mail_dir in team_dir.iterdir():
            if not mail_dir.is_dir():
                continue

            mail_id = mail_dir.name

            # combined.txt 5000자 오버랩 청킹 임베딩
            combined_file = mail_dir / "combined.txt"
            if combined_file.exists():
                with open(combined_file, "r", encoding="utf-8") as f:
                    text = f.read().strip()

                if text:
                    # 5000자 오버랩 청킹
                    chunks = split_text_with_overlap(text)

                    for idx, chunk_text in enumerate(chunks):
                        doc_id = f"{team}_{week}_{mail_id}_part_{idx}"
                        metadata = {
                            "team": team,
                            "week": week,
                            "mail_id": mail_id,
                            "type": "original_part",
                            "part_index": idx,
                            "total_parts": len(chunks),
                        }

                        try:
                            client.add_document(doc_id, chunk_text, metadata)
                            total_parts += 1
                        except Exception as e:
                            print(f"   ❌ {team}/{mail_id}/part_{idx}: {e}")

                    total_mails += 1
                    print(f"   ✅ {team}/{mail_id} ({len(chunks)}개 파트)")

    print(f"\n📊 {week} 임베딩 완료: {total_mails}개 메일, {total_parts}개 파트")


def main(week: str, clear: bool = False):
    """ChromaDB 임베딩 실행

    Args:
        week: 대상 주차 (예: "2025-48")
        clear: 컬렉션 초기화 후 시작 여부
    """
    print("=" * 50)
    print("ChromaDB 임베딩 시작 (5000자 오버랩 청킹)")
    print("=" * 50)

    # ChromaDB 클라이언트 초기화
    CHROMA_DIR.mkdir(exist_ok=True)
    client = ChromaDBClient()

    if clear:
        print("🗑️ 컬렉션 초기화...")
        client.clear_collection()

    # 주차 데이터 임베딩
    embed_week(week, client)

    # 통계 출력
    stats = client.get_stats()
    print(f"\n📈 전체 문서 수: {stats['total_documents']}")

    print("\n" + "=" * 50)
    print("✅ 임베딩 완료")
    print("=" * 50)


if __name__ == "__main__":
    # ========== 실행 설정 ==========
    WEEK = "2025-48"  # 대상 주차
    CLEAR = False  # True: 컬렉션 초기화 후 시작

    main(week=WEEK, clear=CLEAR)

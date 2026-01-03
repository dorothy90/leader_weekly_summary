"""
ChromaDB 테스트용 임베딩 스크립트
- combined.txt → original_mail 타입으로 저장
- chunks.json → chunk 타입으로 저장
- OpenAI text-embedding-3-small 사용
"""

import json
import argparse
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

import chromadb
from chromadb.utils import embedding_functions

# ========== 설정 ==========
DATA_DIR = Path("data")
CHROMA_DIR = Path("chroma_db")
COLLECTION_NAME = "weekly_mail"

# 팀 목록
TEAMS = [
    "CS팀", "DT팀", "EQUIP팀", "FA팀", "PE팀",
    "PI팀", "PROCESS팀", "QA팀", "TEST팀", "YIELD팀",
]


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
            metadata={"description": "Weekly mail embeddings"}
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
            docs.append({
                "id": doc_id,
                "text": results["documents"][i],
                "metadata": results["metadatas"][i],
            })

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
            docs.append({
                "id": doc_id,
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })

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
    """특정 주차 데이터 임베딩"""
    week_dir = DATA_DIR / week

    if not week_dir.exists():
        print(f"❌ 주차 폴더 없음: {week_dir}")
        return

    print(f"\n📅 {week} 주차 데이터 임베딩")
    print("-" * 40)

    total_original = 0
    total_chunks = 0

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

            # 1. combined.txt 임베딩 (original_mail)
            combined_file = mail_dir / "combined.txt"
            if combined_file.exists():
                with open(combined_file, "r", encoding="utf-8") as f:
                    text = f.read().strip()

                if text:
                    doc_id = f"{team}_{week}_{mail_id}_original"
                    metadata = {
                        "team": team,
                        "week": week,
                        "mail_id": mail_id,
                        "type": "original_mail",
                    }

                    try:
                        client.add_document(doc_id, text, metadata)
                        total_original += 1
                        print(f"   ✅ {team}/{mail_id}/combined.txt")
                    except Exception as e:
                        print(f"   ❌ {team}/{mail_id}/combined.txt: {e}")

            # 2. chunks.json 임베딩 (chunk)
            chunks_file = mail_dir / "chunks.json"
            if chunks_file.exists():
                with open(chunks_file, "r", encoding="utf-8") as f:
                    chunks = json.load(f)

                for idx, chunk in enumerate(chunks):
                    text = chunk.get("text", "").strip()
                    if not text:
                        continue

                    doc_id = f"{team}_{week}_{mail_id}_chunk_{idx}"
                    metadata = {
                        "team": team,
                        "week": week,
                        "mail_id": mail_id,
                        "type": "chunk",
                        "domain": chunk.get("domain", ""),
                        "tech": chunk.get("tech", ""),
                        "product": chunk.get("product", ""),
                    }

                    try:
                        client.add_document(doc_id, text, metadata)
                        total_chunks += 1
                    except Exception as e:
                        print(f"   ❌ {team}/{mail_id}/chunk_{idx}: {e}")

                print(f"   ✅ {team}/{mail_id}/chunks.json ({len(chunks)}개)")

    print(f"\n📊 {week} 임베딩 완료: original_mail {total_original}개, chunk {total_chunks}개")


def main():
    parser = argparse.ArgumentParser(description="ChromaDB 임베딩")
    parser.add_argument("--week", type=str, required=True, help="대상 주차 (예: 2025-48)")
    parser.add_argument("--clear", action="store_true", help="컬렉션 초기화 후 시작")

    args = parser.parse_args()

    print("=" * 50)
    print("ChromaDB 임베딩 시작")
    print("=" * 50)

    # ChromaDB 클라이언트 초기화
    CHROMA_DIR.mkdir(exist_ok=True)
    client = ChromaDBClient()

    if args.clear:
        print("🗑️ 컬렉션 초기화...")
        client.clear_collection()

    # 주차 데이터 임베딩
    embed_week(args.week, client)

    # 통계 출력
    stats = client.get_stats()
    print(f"\n📈 전체 문서 수: {stats['total_documents']}")

    print("\n" + "=" * 50)
    print("✅ 임베딩 완료")
    print("=" * 50)


if __name__ == "__main__":
    main()


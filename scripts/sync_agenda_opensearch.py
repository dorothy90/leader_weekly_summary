"""Retry pending SQLite-to-OpenSearch agenda synchronization."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agenda_opensearch import sync_pending
from embed_vectordb import get_opensearch_client
from knowledge_store import DEFAULT_DB_PATH, SQLiteKnowledgeStore


def main() -> int:
    db_path = Path(os.getenv("KNOWLEDGE_DB_PATH", str(DEFAULT_DB_PATH)))
    result = sync_pending(get_opensearch_client(), SQLiteKnowledgeStore(db_path))
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["failed_mails"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

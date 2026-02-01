"""
core.py - 인프라 계층
- 설정/상수
- Pydantic 스키마
- 유틸리티 함수
- OpenSearch/LLM 클라이언트
- MongoDB 함수
"""

import os
import re
import json
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, UTC
from urllib.parse import quote

from pydantic import BaseModel, Field
from opensearchpy import OpenSearch
from openai import OpenAI
from langchain_openai import ChatOpenAI
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import tiktoken

load_dotenv()

# ========== 설정 ==========
# OpenSearch 설정
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "rlaeorka1!K")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "true").lower() == "true"
INDEX_NAME = os.getenv("OPENSEARCH_INDEX", "weekly_mail")

# 임베딩 설정
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "")
EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"

# LLM 설정
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss-120b")

# MongoDB 설정
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "weekly_mail_agent")
HISTORY_TTL_SECONDS = int(os.getenv("HISTORY_TTL_SECONDS", "1800"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))
MAX_ANSWER_LENGTH = int(os.getenv("MAX_ANSWER_LENGTH", "1000"))

# LLM 히스토리 설정
MAX_LLM_HISTORY_TURNS = int(os.getenv("MAX_LLM_HISTORY_TURNS", "3"))

# 토큰/검색 설정
MAX_TOOL_RESULT_TOKENS = int(os.getenv("MAX_TOOL_RESULT_TOKENS", "150000"))
SEARCH_RESULT_LIMIT = int(os.getenv("SEARCH_RESULT_LIMIT", "50"))
MAX_TEXT_PER_DOC = int(os.getenv("MAX_TEXT_PER_DOC", "2000"))
ENCODING_NAME = "cl100k_base"

# LangGraph 설정
GRAPH_RECURSION_LIMIT = int(os.getenv("GRAPH_RECURSION_LIMIT", "15"))

# 서버 설정
HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "8002"))
API_BASE_URL = os.getenv("API_BASE_URL", f"http://localhost:{PORT}")

# 디렉토리
MAIL_DIR = Path("mail")

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


# ========== Pydantic 스키마 ==========
class Reference(BaseModel):
    """참조 출처"""

    team: str
    week: str
    mail_id: str
    url: str
    score: float
    part_index: Optional[int] = None
    total_parts: Optional[int] = None


class ChatV2Request(BaseModel):
    """채팅 요청"""

    user_id: str
    message: str
    conversation_id: Optional[str] = Field(default=None, description="대화 세션 ID")
    team: Optional[str] = Field(default=None, description="팀 필터")
    week: Optional[str] = Field(default=None, description="주차 필터 (예: 2025-48)")


class ToolCallInfo(BaseModel):
    """Tool 호출 정보"""

    name: str
    arguments: Dict[str, Any]


class ToolResultInfo(BaseModel):
    """Tool 실행 결과"""

    name: str
    result: Any


class ChatV2Response(BaseModel):
    """채팅 응답"""

    answer: str
    tool_calls: List[ToolCallInfo]
    tool_results: List[ToolResultInfo]
    references: List[Reference] = Field(default=[], description="참조 출처 목록")


# ========== 유틸리티 ==========
def count_tokens(text: str) -> int:
    """텍스트의 토큰 수 계산"""
    try:
        encoding = tiktoken.get_encoding(ENCODING_NAME)
        return len(encoding.encode(text))
    except Exception:
        return len(text) // 4


def parse_used_references(answer: str, contexts: List[Dict]) -> tuple:
    """LLM 답변에서 실제 참고한 문서만 추출"""
    pattern = r"\[문서\s*(\d+)\]"
    matches = re.findall(pattern, answer)

    if not matches:
        return answer, []

    used_indices = set(int(m) - 1 for m in matches if int(m) - 1 < len(contexts))
    used_contexts = [ctx for i, ctx in enumerate(contexts) if i in used_indices]

    clean_answer = re.sub(
        r"\n*참고:\s*(\[문서\s*\d+\],?\s*)+\.?$", "", answer, flags=re.MULTILINE
    ).strip()

    return clean_answer, used_contexts


def extract_references(contexts: List[Dict]) -> List[Reference]:
    """검색 결과에서 참조 출처 추출"""
    refs = []
    seen = set()

    for ctx in contexts:
        team = ctx.get("team", "unknown")
        week = ctx.get("week", "unknown")
        mail_id = ctx.get("mail_id", "unknown")

        key = f"{team}_{week}_{mail_id}"
        if key in seen:
            continue
        seen.add(key)

        filename = f"{week}_{team}_{mail_id}.html"
        url = f"{API_BASE_URL}/mail/{quote(filename, safe='')}"
        refs.append(
            Reference(
                team=team,
                week=week,
                mail_id=mail_id,
                url=url,
                score=ctx.get("score", 0),
                part_index=ctx.get("part_index"),
                total_parts=ctx.get("total_parts"),
            )
        )

    return refs


def convert_table_to_bullet(text: str) -> str:
    """마크다운 표를 개조식으로 변환"""
    lines = text.split("\n")
    result = []
    table_lines = []
    in_table = False

    for line in lines:
        if line.strip().startswith("|") and "|" in line[1:]:
            in_table = True
            table_lines.append(line)
        else:
            if in_table and table_lines:
                result.append(_parse_table_to_bullet(table_lines))
                table_lines = []
                in_table = False
            result.append(line)

    if table_lines:
        result.append(_parse_table_to_bullet(table_lines))

    return "\n".join(result)


def _parse_table_to_bullet(table_lines: list) -> str:
    """표를 개조식으로 변환"""
    rows = []
    headers = []

    for line in table_lines:
        if re.match(r"^\|[\s\-:\|]+\|?$", line.strip()):
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if not headers:
            headers = cells
        else:
            rows.append(cells)

    bullet_lines = []
    for row in rows:
        if len(headers) == len(row) and len(row) >= 2:
            item = f"• {row[0]}"
            details = [f"{headers[j]}: {row[j]}" for j in range(1, len(row)) if row[j]]
            if details:
                item += f" ({', '.join(details)})"
            bullet_lines.append(item)
        elif row:
            bullet_lines.append(f"• {' | '.join(row)}")

    return "\n".join(bullet_lines) if bullet_lines else ""


def clean_html_breaks(text: str) -> str:
    """HTML <br> 태그를 줄바꿈으로 변환"""
    return re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)


# ========== OpenSearch 클라이언트 ==========
class OpenSearchClient:
    """OpenSearch 클라이언트"""

    def __init__(self):
        self.client = OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
            use_ssl=OPENSEARCH_USE_SSL,
            verify_certs=False,
            ssl_show_warn=False,
        )
        self.embedding_client = OpenAI(
            api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL
        )
        self.index_name = INDEX_NAME

    def _get_embedding(self, text: str) -> List[float]:
        """텍스트를 임베딩 벡터로 변환"""
        text = text[:8000] if len(text) > 8000 else text
        response = self.embedding_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
        )
        return response.data[0].embedding

    def search(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        mail_type: Optional[str] = None,
        limit: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict]:
        """하이브리드 검색

        Args:
            mail_type: 메일 유형 필터 ("weekly_report" / "daily_report" / None=전체)
        """
        # 가중치 정규화
        total = vector_weight + keyword_weight
        if total > 0:
            vector_weight, keyword_weight = (
                vector_weight / total,
                keyword_weight / total,
            )
        else:
            vector_weight, keyword_weight = 0.7, 0.3

        query_embedding = self._get_embedding(query)

        filters = []
        if team:
            filters.append({"term": {"team": team}})
        if week:
            filters.append({"term": {"week": week}})
        if mail_type:
            filters.append({"term": {"mail_type": mail_type}})

        knn_boost = vector_weight * 10
        bm25_boost = keyword_weight

        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "should": [
                        {
                            "knn": {
                                "embedding": {
                                    "vector": query_embedding,
                                    "k": limit,
                                    "boost": knn_boost,
                                }
                            }
                        },
                        {
                            "match": {
                                "text": {
                                    "query": query,
                                    "analyzer": "korean",
                                    "boost": bm25_boost,
                                }
                            }
                        },
                    ],
                    "filter": filters if filters else [],
                    "minimum_should_match": 1,
                }
            },
        }

        response = self.client.search(index=self.index_name, body=search_body)

        results = []
        for hit in response["hits"]["hits"]:
            source = hit["_source"]
            results.append(
                {
                    "score": hit["_score"],
                    "text": source.get("text", ""),
                    "team": source.get("team", "unknown"),
                    "week": source.get("week", "unknown"),
                    "mail_id": source.get("mail_id", "unknown"),
                    "html_path": source.get("html_path", ""),
                    "part_index": source.get("part_index"),
                    "total_parts": source.get("total_parts"),
                }
            )
        return results

    def get_stats(self) -> Dict:
        try:
            count = self.client.count(index=self.index_name)
            return {"total_documents": count["count"], "index": self.index_name}
        except Exception as e:
            return {"error": str(e)}

    def health_check(self) -> Dict:
        try:
            info = self.client.info()
            return {
                "status": "ok",
                "distribution": info["version"]["distribution"],
                "version": info["version"]["number"],
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# ========== LLM 클라이언트 ==========
def get_llm():
    """LLM 클라이언트"""
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        temperature=0,
        default_headers={
            "HTTP-Referer": "https://weekly-mail-agent.local",
            "X-Title": "Weekly Mail RAG Chatbot",
        },
    )


# ========== MongoDB ==========
mongo_client: Optional[AsyncIOMotorClient] = None
mongo_db = None


async def init_mongodb():
    """MongoDB 초기화"""
    global mongo_client, mongo_db
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    mongo_db = mongo_client[MONGO_DB]

    await mongo_db.conversation_logs.create_index(
        [("conversation_id", 1), ("timestamp", -1)]
    )
    await mongo_db.conversation_logs.create_index("user_id")
    await mongo_db.conversation_history.create_index("conversation_id", unique=True)
    await mongo_db.conversation_history.create_index(
        "updated_at", expireAfterSeconds=HISTORY_TTL_SECONDS
    )

    print(f"✅ MongoDB 초기화: {MONGO_URI}/{MONGO_DB}")


async def close_mongodb():
    """MongoDB 종료"""
    global mongo_client
    if mongo_client:
        mongo_client.close()
        print("🔌 MongoDB 연결 종료")


async def save_full_log(
    conversation_id: str,
    user_id: str,
    message: str,
    team: Optional[str],
    week: Optional[str],
    tool_calls: List[Dict],
    tool_results: List[Dict],
    answer: str,
    references: List[Dict],
) -> None:
    """전체 대화 기록 저장"""
    if mongo_db is None:
        return

    existing_count = await mongo_db.conversation_logs.count_documents(
        {"conversation_id": conversation_id}
    )

    log_doc = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "timestamp": datetime.now(UTC),
        "turn": existing_count + 1,
        "request": {"message": message, "team": team, "week": week},
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "response": {"answer": answer, "references": references},
    }

    await mongo_db.conversation_logs.insert_one(log_doc)


async def save_history(
    conversation_id: str, user_message: str, assistant_answer: str
) -> None:
    """히스토리 저장"""
    if mongo_db is None:
        return

    messages_to_add = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_answer},
    ]

    await mongo_db.conversation_history.update_one(
        {"conversation_id": conversation_id},
        {
            "$push": {
                "messages": {
                    "$each": messages_to_add,
                    "$slice": -(MAX_HISTORY_TURNS * 2),
                }
            },
            "$set": {"updated_at": datetime.now(UTC)},
            "$setOnInsert": {"created_at": datetime.now(UTC)},
        },
        upsert=True,
    )


async def get_history(conversation_id: str) -> List[Dict]:
    """대화 히스토리 조회"""
    if mongo_db is None:
        return []

    doc = await mongo_db.conversation_history.find_one(
        {"conversation_id": conversation_id}
    )
    if not doc:
        return []

    return doc.get("messages", [])


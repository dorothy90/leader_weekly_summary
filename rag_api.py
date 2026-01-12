"""
RAG Chatbot API Server
- 사내 메신저 API 연동을 위한 REST API 서버
- FastAPI 기반
- ChromaDB 유사도 검색 + LLM 답변 생성
"""

import os
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import chromadb
from chromadb.utils import embedding_functions
from langchain_openai import ChatOpenAI

from dotenv import load_dotenv

load_dotenv()

# ========== 설정 ==========
CHROMA_DIR = Path("chroma_db")
COLLECTION_NAME = "weekly_mail"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "your_api_key")
MODEL = "gpt-oss-120b"

# 메일 HTML 파일 저장 폴더
MAIL_DIR = Path("mail")

# API 서버 설정
HOST = "0.0.0.0"
PORT = 8001

# 메일 서버 URL (body.html 정적 파일 서빙용)
MAIL_SERVER_URL = os.getenv("MAIL_SERVER_URL", f"http://localhost:{PORT}/mail")

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
class ChatRequest(BaseModel):
    """채팅 요청"""

    user_id: str
    message: str


class Reference(BaseModel):
    """참조 출처"""

    team: str
    week: str
    mail_id: str
    url: str


class ChatResponse(BaseModel):
    """채팅 응답"""

    answer: str
    references: List[Reference]


# ========== ChromaDB 클라이언트 ==========
class ChromaDBClient:
    """ChromaDB 클라이언트"""

    def __init__(self, persist_dir: str = str(CHROMA_DIR)):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self._get_or_create_collection()

    def _get_or_create_collection(self):
        return self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self.embedding_fn,
            metadata={"description": "Weekly mail embeddings"},
        )

    def search_similar(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """유사도 검색"""
        conditions = []
        if team:
            conditions.append({"team": team})
        if week:
            conditions.append({"week": week})

        if len(conditions) > 1:
            where = {"$and": conditions}
        elif len(conditions) == 1:
            where = conditions[0]
        else:
            where = None

        results = self.collection.query(
            query_texts=[query],
            where=where,
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )

        docs = []
        if results["ids"] and results["ids"][0]:
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
        return {"total_documents": self.collection.count()}


# ========== LLM 클라이언트 ==========
def get_llm():
    """LLM 클라이언트"""
    return ChatOpenAI(
        model=MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.3,
        default_headers={
            "HTTP-Referer": "https://weekly-mail-agent.local",
            "X-Title": "Weekly Mail RAG Chatbot",
        },
    )


# ========== RAG 로직 ==========
def generate_answer(query: str, contexts: List[Dict]) -> str:
    """검색된 context 기반으로 LLM 답변 생성"""
    if not contexts:
        return "관련 정보를 찾을 수 없습니다."

    # Context 구성
    context_text = ""
    for i, ctx in enumerate(contexts, 1):
        meta = ctx["metadata"]
        context_text += f"\n[문서 {i}]\n"
        context_text += f"팀: {meta.get('team', 'unknown')}\n"
        context_text += f"주차: {meta.get('week', 'unknown')}\n"
        context_text += f"내용:\n{ctx['text'][:2000]}\n"
        context_text += "-" * 40

    # 프롬프트
    system_prompt = """당신은 반도체 주간 업무 보고서를 기반으로 질문에 답변하는 AI 어시스턴트입니다.

## 답변 원칙
1. 제공된 문서(context)만 근거로 답변하세요.
2. 문서에 없는 내용은 추측하지 마세요.
3. 관련 정보가 없으면 "관련 정보를 찾을 수 없습니다"라고 답하세요.
4. 답변은 간결하고 명확하게 작성하세요.
5. 수치, 이슈, 액션 등 핵심 정보를 포함하세요.
"""

    user_prompt = f"""질문: {query}

참고 문서:
{context_text}

위 문서를 바탕으로 질문에 답변해주세요."""

    llm = get_llm()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    response = llm.invoke(messages)
    return response.content


def extract_references(contexts: List[Dict]) -> List[Reference]:
    """검색 결과에서 참조 출처 추출"""
    refs = []
    seen = set()

    for ctx in contexts:
        meta = ctx["metadata"]
        team = meta.get("team", "unknown")
        week = meta.get("week", "unknown")
        mail_id = meta.get("mail_id", "unknown")

        # 중복 제거
        key = f"{team}_{week}_{mail_id}"
        if key in seen:
            continue
        seen.add(key)

        # URL 형식: /mail/{week}_{team}_{mail_id}.html
        url = f"{MAIL_SERVER_URL}/{week}_{team}_{mail_id}.html"
        refs.append(
            Reference(
                team=team,
                week=week,
                mail_id=mail_id,
                url=url,
            )
        )

    return refs


def format_answer_with_references(answer: str, references: List[Reference]) -> str:
    """답변에 출처 정보를 포함하여 반환"""
    if not references:
        return answer

    # 출처 섹션 구성: URL은 대괄호 없이 표시 (클릭 가능하도록)
    ref_lines = ["\n\n━━━━━━━━━━━━━━━━━━━━", "📎 참고 출처:"]
    for ref in references:
        ref_lines.append(f"  • {ref.team} | {ref.week}")
        ref_lines.append(f"    {ref.url}")

    return answer + "\n".join(ref_lines)


# ========== FastAPI 앱 ==========
app = FastAPI(
    title="Weekly Mail RAG Chatbot API",
    description="주간 메일 히스토리 기반 Q&A API",
    version="1.0.0",
)

# 전역 클라이언트
db_client: Optional[ChromaDBClient] = None


@app.on_event("startup")
async def startup():
    """서버 시작 시 ChromaDB 클라이언트 초기화 및 정적 파일 서빙 설정"""
    global db_client
    CHROMA_DIR.mkdir(exist_ok=True)
    MAIL_DIR.mkdir(exist_ok=True)
    db_client = ChromaDBClient()
    print(f"✅ ChromaDB 초기화 완료: {db_client.get_stats()}")
    print(f"📁 메일 HTML 서빙 경로: {MAIL_DIR.absolute()}")


# 정적 파일 서빙: /mail 경로로 mail 폴더 제공
MAIL_DIR.mkdir(exist_ok=True)  # 폴더 미리 생성
app.mount("/mail", StaticFiles(directory=str(MAIL_DIR)), name="mail")


@app.get("/health")
async def health():
    """헬스체크"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "db_stats": db_client.get_stats() if db_client else None,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """채팅 API - 질문 수신 및 답변 반환"""
    if not db_client:
        raise HTTPException(status_code=503, detail="DB 초기화 중")

    query = request.message.strip()
    if not query:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    # 1. 유사도 검색
    contexts = db_client.search_similar(query, limit=5)

    # 2. LLM 답변 생성
    answer = generate_answer(query, contexts)

    # 3. 참조 출처 추출
    references = extract_references(contexts)

    # 4. 답변에 출처 포함
    answer_with_refs = format_answer_with_references(answer, references)

    return ChatResponse(
        answer=answer_with_refs,
        references=references,
    )


# ========== 실행 ==========
if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("RAG Chatbot API Server")
    print("=" * 50)
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Docs: http://localhost:{PORT}/docs")
    print(f"  Mail: http://localhost:{PORT}/mail/")
    print("=" * 50)

    uvicorn.run(app, host=HOST, port=PORT)

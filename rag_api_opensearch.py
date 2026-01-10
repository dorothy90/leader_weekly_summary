"""
RAG Chatbot API Server (OpenSearch 버전)
- 사내 메신저 API 연동을 위한 REST API 서버
- FastAPI 기반
- OpenSearch 하이브리드 검색 (벡터 + 키워드 가중치 조절) + LLM 답변 생성
"""

import os
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from opensearchpy import OpenSearch
from openai import OpenAI
from langchain_openai import ChatOpenAI

from dotenv import load_dotenv

load_dotenv()

# ========== 설정 ==========
# OpenSearch 설정
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "admin")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true"
INDEX_NAME = os.getenv("OPENSEARCH_INDEX", "weekly_mail")

# 임베딩 설정
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536

# LLM 설정
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss-120b")

# 서버 설정
HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "8002"))
# API 베이스 URL (외부 접근용, 환경변수로 재정의 가능)
API_BASE_URL = os.getenv("API_BASE_URL", f"http://localhost:{PORT}")

# 데이터 디렉토리
DATA_DIR = Path("data")

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
    """채팅 요청

    가중치 설정 예시:
    - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
    - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
    - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값, 추천)
    """

    user_id: str
    message: str
    team: Optional[str] = Field(default=None, description="팀 필터")
    week: Optional[str] = Field(default=None, description="주차 필터 (예: 2025-48)")
    vector_weight: float = Field(
        default=0.7, ge=0.0, le=1.0, description="벡터 검색 가중치"
    )
    keyword_weight: float = Field(
        default=0.3, ge=0.0, le=1.0, description="키워드 검색 가중치"
    )


class Reference(BaseModel):
    """참조 출처"""

    team: str
    week: str
    mail_id: str
    url: str
    score: float
    part_index: Optional[int] = None
    total_parts: Optional[int] = None


class ChatResponse(BaseModel):
    """채팅 응답"""

    answer: str
    references: List[Reference]
    vector_weight: float
    keyword_weight: float


class SearchRequest(BaseModel):
    """검색 요청 (채팅 없이 검색만)"""

    query: str
    team: Optional[str] = None
    week: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=20)
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    keyword_weight: float = Field(default=0.3, ge=0.0, le=1.0)


class SearchResult(BaseModel):
    """검색 결과"""

    score: float
    text: str
    team: str
    week: str
    mail_id: str
    html_path: str
    part_index: Optional[int] = None
    total_parts: Optional[int] = None
    vector_score: Optional[float] = None
    keyword_score: Optional[float] = None


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
        self.embedding_client = self._get_embedding_client()
        self.index_name = INDEX_NAME

    def _get_embedding_client(self) -> OpenAI:
        """OpenAI 임베딩 클라이언트"""
        api_key = OPENAI_API_KEY or OPENROUTER_API_KEY
        return OpenAI(api_key=api_key)

    def _get_embedding(self, text: str) -> List[float]:
        """텍스트를 임베딩 벡터로 변환"""
        max_chars = 8000
        if len(text) > max_chars:
            text = text[:max_chars]

        response = self.embedding_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
        )
        return response.data[0].embedding

    def _search_vector(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """벡터 유사도 검색 (의미 기반) - 내부 사용"""
        query_embedding = self._get_embedding(query)

        # 필터 구성
        filters = []
        if team:
            filters.append({"term": {"team": team}})
        if week:
            filters.append({"term": {"week": week}})

        # k-NN 검색 쿼리
        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [
                        {"knn": {"embedding": {"vector": query_embedding, "k": limit}}}
                    ],
                    "filter": filters if filters else [],
                }
            },
        }

        response = self.client.search(index=self.index_name, body=search_body)
        return self._parse_results(response)

    def _search_keyword(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """키워드 검색 (한국어 Nori 형태소 분석 기반 BM25) - 내부 사용"""
        # 필터 구성
        filters = []
        if team:
            filters.append({"term": {"team": team}})
        if week:
            filters.append({"term": {"week": week}})

        # BM25 키워드 검색
        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [
                        {"match": {"text": {"query": query, "analyzer": "korean"}}}
                    ],
                    "filter": filters if filters else [],
                }
            },
        }

        response = self.client.search(index=self.index_name, body=search_body)
        return self._parse_results(response)

    def search(
        self,
        query: str,
        team: Optional[str] = None,
        week: Optional[str] = None,
        limit: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict]:
        """하이브리드 검색 (벡터 + 키워드)

        최종 점수 = (벡터 유사도 × vector_weight) + (키워드 BM25 × keyword_weight)

        가중치 설정:
        - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
        - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
        - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값)
        """
        # 가중치 정규화 (합이 1이 아닐 경우)
        total_weight = vector_weight + keyword_weight
        if total_weight > 0:
            vector_weight = vector_weight / total_weight
            keyword_weight = keyword_weight / total_weight
        else:
            # 둘 다 0인 경우 기본값
            vector_weight = 0.7
            keyword_weight = 0.3

        score_map = {}

        # 1. 벡터 검색 (가중치가 0보다 클 때만)
        if vector_weight > 0:
            vector_results = self._search_vector(query, team, week, limit=limit * 2)
            if vector_results:
                max_vector = max(r["score"] for r in vector_results)
                for r in vector_results:
                    key = f"{r['team']}_{r['week']}_{r['mail_id']}_{r.get('part_index', 0)}"
                    normalized_score = r["score"] / max_vector if max_vector > 0 else 0
                    score_map[key] = {
                        "vector_score": normalized_score,
                        "keyword_score": 0,
                        "doc": r,
                    }

        # 2. 키워드 검색 (가중치가 0보다 클 때만)
        if keyword_weight > 0:
            keyword_results = self._search_keyword(query, team, week, limit=limit * 2)
            if keyword_results:
                max_keyword = max(r["score"] for r in keyword_results)
                for r in keyword_results:
                    key = f"{r['team']}_{r['week']}_{r['mail_id']}_{r.get('part_index', 0)}"
                    normalized_score = (
                        r["score"] / max_keyword if max_keyword > 0 else 0
                    )
                    if key in score_map:
                        score_map[key]["keyword_score"] = normalized_score
                    else:
                        score_map[key] = {
                            "vector_score": 0,
                            "keyword_score": normalized_score,
                            "doc": r,
                        }

        # 3. 최종 점수 계산 및 정렬
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

        return final_results[:limit]

    def _parse_results(self, response: Dict) -> List[Dict]:
        """검색 결과 파싱"""
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
        """인덱스 통계"""
        try:
            count = self.client.count(index=self.index_name)
            return {
                "total_documents": count["count"],
                "index": self.index_name,
            }
        except Exception as e:
            return {"error": str(e)}

    def health_check(self) -> Dict:
        """OpenSearch 헬스체크"""
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
        base_url="https://openrouter.ai/api/v1",
        temperature=0.3,
        default_headers={
            "HTTP-Referer": "https://weekly-mail-agent.local",
            "X-Title": "Weekly Mail RAG Chatbot (OpenSearch)",
        },
    )


# ========== RAG 로직 ==========
def generate_answer(
    query: str,
    contexts: List[Dict],
    vector_weight: float,
    keyword_weight: float,
) -> str:
    """검색된 context 기반으로 LLM 답변 생성"""
    if not contexts:
        return "관련 정보를 찾을 수 없습니다."

    # Context 구성
    context_text = ""
    for i, ctx in enumerate(contexts, 1):
        context_text += f"\n[문서 {i}]\n"
        context_text += f"팀: {ctx.get('team', 'unknown')}\n"
        context_text += f"주차: {ctx.get('week', 'unknown')}\n"
        if ctx.get("part_index") is not None:
            context_text += (
                f"파트: {ctx['part_index'] + 1}/{ctx.get('total_parts', 1)}\n"
            )
        context_text += f"내용:\n{ctx['text'][:3000]}\n"
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

    weight_info = f"벡터:{vector_weight:.0%}, 키워드:{keyword_weight:.0%}"
    user_prompt = f"""질문: {query}

참고 문서 (검색 가중치 - {weight_info}):
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
        team = ctx.get("team", "unknown")
        week = ctx.get("week", "unknown")
        mail_id = ctx.get("mail_id", "unknown")
        part_index = ctx.get("part_index")

        # 중복 제거 (같은 메일의 다른 파트는 허용)
        key = f"{team}_{week}_{mail_id}_{part_index}"
        if key in seen:
            continue
        seen.add(key)

        # /mail 경로로 static files 서빙
        url = f"{API_BASE_URL}/mail/{week}/{team}/{mail_id}/body.html"
        refs.append(
            Reference(
                team=team,
                week=week,
                mail_id=mail_id,
                url=url,
                score=ctx.get("score", 0),
                part_index=part_index,
                total_parts=ctx.get("total_parts"),
            )
        )

    return refs


# ========== FastAPI 앱 ==========
app = FastAPI(
    title="Weekly Mail RAG Chatbot API (OpenSearch)",
    description="""주간 메일 히스토리 기반 Q&A API - OpenSearch 하이브리드 검색 지원

## 검색 가중치 설정
- `vector_weight=1.0, keyword_weight=0.0` → 순수 벡터 검색 (의미 기반)
- `vector_weight=0.0, keyword_weight=1.0` → 순수 키워드 검색 (형태소 기반)
- `vector_weight=0.7, keyword_weight=0.3` → 하이브리드 (기본값, 추천)
""",
    version="2.1.0",
)

# Static files 서빙 (원본 메일 HTML 조회용)
# /mail/{week}/{team}/{mail_id}/body.html 경로로 접근 가능
if DATA_DIR.exists():
    app.mount("/mail", StaticFiles(directory=str(DATA_DIR)), name="mail")

# 전역 클라이언트
os_client: Optional[OpenSearchClient] = None


@app.on_event("startup")
async def startup():
    """서버 시작 시 OpenSearch 클라이언트 초기화"""
    global os_client
    os_client = OpenSearchClient()
    health = os_client.health_check()
    stats = os_client.get_stats()
    print(f"✅ OpenSearch 초기화 완료: {health}")
    print(f"📊 인덱스 통계: {stats}")


@app.get("/health")
async def health():
    """헬스체크"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "opensearch": os_client.health_check() if os_client else None,
        "db_stats": os_client.get_stats() if os_client else None,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """채팅 API - 질문 수신 및 답변 반환

    가중치로 검색 방식을 조절합니다:
    - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
    - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
    - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값)
    """
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    query = request.message.strip()
    if not query:
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다")

    # 1. 하이브리드 검색 (가중치로 모드 제어)
    contexts = os_client.search(
        query,
        team=request.team,
        week=request.week,
        limit=5,
        vector_weight=request.vector_weight,
        keyword_weight=request.keyword_weight,
    )

    # 2. LLM 답변 생성
    answer = generate_answer(
        query,
        contexts,
        request.vector_weight,
        request.keyword_weight,
    )

    # 3. 참조 출처 추출
    references = extract_references(contexts)

    return ChatResponse(
        answer=answer,
        references=references,
        vector_weight=request.vector_weight,
        keyword_weight=request.keyword_weight,
    )


@app.post("/search", response_model=List[SearchResult])
async def search(request: SearchRequest):
    """검색 API (채팅 없이 검색만)

    가중치로 검색 방식을 조절합니다:
    - vector_weight=1.0, keyword_weight=0.0 → 순수 벡터 검색
    - vector_weight=0.0, keyword_weight=1.0 → 순수 키워드 검색
    - vector_weight=0.7, keyword_weight=0.3 → 하이브리드 (기본값)
    """
    if not os_client:
        raise HTTPException(status_code=503, detail="OpenSearch 초기화 중")

    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="검색어가 비어있습니다")

    # 하이브리드 검색
    results = os_client.search(
        query,
        team=request.team,
        week=request.week,
        limit=request.limit,
        vector_weight=request.vector_weight,
        keyword_weight=request.keyword_weight,
    )

    return [
        SearchResult(
            score=r["score"],
            text=r["text"][:500] + "..." if len(r["text"]) > 500 else r["text"],
            team=r["team"],
            week=r["week"],
            mail_id=r["mail_id"],
            html_path=r["html_path"],
            part_index=r.get("part_index"),
            total_parts=r.get("total_parts"),
            vector_score=r.get("vector_score"),
            keyword_score=r.get("keyword_score"),
        )
        for r in results
    ]


@app.get("/teams")
async def get_teams():
    """팀 목록 조회"""
    return {"teams": TEAMS}


# ========== 실행 ==========
if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("RAG Chatbot API Server (OpenSearch)")
    print("=" * 50)
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Docs: http://localhost:{PORT}/docs")
    print(f"  Mail: http://localhost:{PORT}/mail/")
    print(f"  OpenSearch: {OPENSEARCH_HOST}:{OPENSEARCH_PORT}")
    print(f"  Index: {INDEX_NAME}")
    print("=" * 50)

    uvicorn.run(app, host=HOST, port=PORT)
